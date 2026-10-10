"""Optional OTLP export. Collector credentials stay in the API process."""

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, SpanLimits, SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.sdk.util.instrumentation import InstrumentationScope
from opentelemetry.trace import (
    INVALID_SPAN,
    NonRecordingSpan,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)

from .settings import settings
from .trace_messages import ResponseMessages, message_attributes, messages, request_messages

logger = logging.getLogger(__name__)
_provider = None
_processor = None


class TraceProcessor(SpanProcessor):
    """Export off the request path with an unbounded queue and no content truncation."""

    def __init__(self, endpoint, headers):
        self.endpoint = endpoint
        self.client = httpx.Client(headers=headers, timeout=None)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="litereview-traces")

    def on_end(self, span):
        self.executor.submit(self.export, span)

    def export(self, span):
        try:
            response = self.client.post(
                self.endpoint,
                content=encode_spans((span,)).SerializeToString(),
                headers={"Content-Type": "application/x-protobuf"},
            )
            response.raise_for_status()
        except Exception:
            logger.warning("Trace export failed. Check the collector and its credentials.")

    def force_flush(self, timeout_millis=None):
        self.executor.submit(lambda: None).result(
            timeout=timeout_millis / 1000 if timeout_millis is not None else None
        )
        return True

    def shutdown(self):
        self.executor.shutdown(wait=True)
        self.client.close()


def start():
    global _provider, _processor
    config = settings()
    if not config.litereview_trace_endpoint:
        return
    try:
        url = urlsplit(config.litereview_trace_endpoint)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError("Invalid collector URL")
        provider = TracerProvider(
            resource=Resource({"service.name": "litereview"}),
            sampler=ALWAYS_ON,
            span_limits=SpanLimits(
                max_span_attributes=SpanLimits.UNSET,
                max_events=SpanLimits.UNSET,
                max_links=SpanLimits.UNSET,
                max_event_attributes=SpanLimits.UNSET,
                max_link_attributes=SpanLimits.UNSET,
                max_attribute_length=SpanLimits.UNSET,
                max_span_attribute_length=SpanLimits.UNSET,
            ),
            shutdown_on_exit=False,
        )
        processor = TraceProcessor(
            config.litereview_trace_endpoint,
            {"Authorization": "Bearer " + config.litereview_trace_api_key}
            if config.litereview_trace_api_key else {},
        )
        provider.add_span_processor(processor)
        _processor = processor
        _provider = provider
    except Exception:
        # Configuration failures must not prevent reviews or disclose credentials.
        logger.warning("Trace export is disabled. Check LITEREVIEW_TRACE_ENDPOINT and collector settings.")


def shutdown():
    global _provider, _processor
    provider, _provider = _provider, None
    _processor = None
    if provider:
        provider.shutdown()


def job_context(job):
    # Stable across API restarts and publication retries. Never derive IDs from tokens.
    identity = hashlib.sha256(("litereview.review.v1:" + job.id).encode()).digest()
    return SpanContext(
        trace_id=int.from_bytes(identity[:16], "big") or 1,
        span_id=int.from_bytes(identity[16:24], "big") or 1,
        is_remote=False, trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )


def job_attributes(job):
    cfg = job.config
    return {
        "agent.name": cfg.get("name", "LiteReview"),
        "gen_ai.agent.name": cfg.get("name", "LiteReview"),
        "session.id": job.id,
        "litereview.run_id": job.run_id,
        "litereview.job_id": job.id,
        "litereview.harness": cfg.get("harness", "builtin"),
    }


def finish_job(job, run):
    """Export the review parent from durable state, including infrastructure failures.

    Children can arrive before the parent. Repeated publication attempts export the
    same span identity, so Lens can upsert it instead of creating another run.
    """
    if _provider is None or _processor is None or not job.finished_at:
        return
    try:
        complete = job.status == "completed" and not job.result.get("limitations")
        attrs = job_attributes(job) | {
            "openinference.span.kind": "AGENT",
            "litereview.repo": run.repo,
            "litereview.pr": run.pr,
            "litereview.head": run.head,
            "litereview.status": "completed" if complete else "incomplete",
            "litereview.model_calls": job.model_calls,
        }
        if settings().litereview_trace_content:
            from .reviews import render_comment

            attrs.update(message_attributes("input", messages(job.config.get("instructions", ""))))
            attrs["litereview.result"] = json.dumps(job.result, ensure_ascii=False)
            attrs.update(message_attributes("output", messages(render_comment(run, [job]), "assistant")))
        _processor.on_end(ReadableSpan(
            name=job.config.get("name", "LiteReview"),
            context=job_context(job),
            resource=_provider.resource,
            attributes=attrs,
            status=Status(StatusCode.OK if complete else StatusCode.ERROR),
            start_time=int((job.started_at or run.created_at) * 1_000_000_000),
            end_time=int(job.finished_at * 1_000_000_000),
            instrumentation_scope=InstrumentationScope("litereview.review"),
        ))
    except Exception:
        logger.warning("Review trace export failed; review publication will continue.")


class ModelTrace:
    """One model span under the review's stable parent, with optional conversation content."""

    def __init__(self, job, body, enabled=True):
        self.span = INVALID_SPAN
        self.capture = False
        self.buffer = bytearray()
        self.response = ResponseMessages()
        self.ended = False
        self.failed = False
        if _provider is None or not enabled:
            return
        cfg = job.config
        self.capture = settings().litereview_trace_content
        self.span = _provider.get_tracer("litereview.model").start_span(
            "chat " + cfg["model"], kind=SpanKind.CLIENT,
            context=set_span_in_context(NonRecordingSpan(job_context(job))),
            attributes=job_attributes(job) | {
                "openinference.span.kind": "LLM",
                "llm.model_name": cfg["model"],
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": cfg["model"],
                "gen_ai.provider.name": cfg["connection"]["type"],
            },
        )
        if self.capture:
            try:
                self.span.set_attributes(message_attributes("input", request_messages(body)))
            except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
                pass

    def result(self, data):
        try:
            self._result(data)
        except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
            pass  # A malformed telemetry event must never change the model response.

    def _result(self, data):
        if not isinstance(data, dict):
            return
        if data.get("error") or data.get("type") in ("error", "response.failed"):
            self.failed = True
        response = data.get("response") or data.get("message") or data
        if not isinstance(response, dict):
            return
        usage = response.get("usage") or data.get("usage") or {}
        if isinstance(usage, dict):
            for dest, names in (("input", ("input_tokens", "prompt_tokens")),
                                ("output", ("output_tokens", "completion_tokens"))):
                for name in names:
                    if isinstance(usage.get(name), int):
                        self.span.set_attribute("gen_ai.usage." + dest + "_tokens", usage[name])
                        break
        if not self.capture:
            return
        self.response.add(data)

    def feed(self, chunk, sse=False):
        if not self.span.is_recording():
            return
        for part in chunk.splitlines(keepends=True) if sse else (chunk,):
            self.buffer.extend(part)
            if sse and part.endswith(b"\n"):
                if self.buffer.startswith(b"data:"):
                    self._parse(self.buffer[5:])
                self.buffer.clear()

    def _parse(self, raw):
        try:
            self.result(json.loads(raw))
        except (ValueError, TypeError, KeyError, RecursionError):
            pass  # Telemetry parsing must never change the model response.

    def end(self, error=False, status=None):
        if self.ended:
            return
        self.ended = True
        if self.buffer:
            self._parse(self.buffer[5:] if self.buffer.startswith(b"data:") else self.buffer)
        self.buffer.clear()
        if self.capture:
            try:
                self.span.set_attributes(message_attributes("output", self.response.conversation(), self.response.response_id))
            except (ValueError, TypeError, KeyError, AttributeError, IndexError, RecursionError):
                pass
        if status is not None:
            self.span.set_attribute("http.response.status_code", status)
        self.span.set_status(StatusCode.ERROR if error or self.failed else StatusCode.OK)
        self.span.end()
