"""Optional, bounded OTLP export. Collector credentials stay in the API process."""

import json
import logging
from urllib.parse import urlsplit

from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanLimits, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import INVALID_SPAN, SpanKind, StatusCode

from .settings import settings

logger = logging.getLogger(__name__)
_provider = None
CONTENT_LIMIT = 8192
BUFFER_LIMIT = 65536


def start():
    global _provider
    config = settings()
    if not config.litereview_trace_endpoint:
        return
    try:
        url = urlsplit(config.litereview_trace_endpoint)
        if url.scheme not in ("http", "https") or not url.hostname or url.username or url.query or url.fragment:
            raise ValueError("Invalid collector URL")
        exporter = OTLPSpanExporter(
            endpoint=config.litereview_trace_endpoint,
            headers={"Authorization": "Bearer " + config.litereview_trace_api_key}
            if config.litereview_trace_api_key else {},
            timeout=3,
        )
        provider = TracerProvider(
            resource=Resource({"service.name": "litereview"}),
            sampler=ALWAYS_ON,
            span_limits=SpanLimits(max_attributes=32, max_attribute_length=CONTENT_LIMIT),
            shutdown_on_exit=False,
        )
        provider.add_span_processor(BatchSpanProcessor(
            exporter, max_queue_size=256, max_export_batch_size=16,
            schedule_delay_millis=1000, export_timeout_millis=3000,
        ))
        _provider = provider
    except Exception:
        # Configuration failures must not prevent reviews or disclose credentials.
        logger.warning("Trace export is disabled. Check LITEREVIEW_TRACE_ENDPOINT and collector settings.")


def shutdown():
    global _provider
    provider, _provider = _provider, None
    if provider:
        provider.shutdown()


def text_content(value, limit=CONTENT_LIMIT):
    """Only explicit text fields; never images, headers, tools, or arbitrary objects."""
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, list):
        parts, remaining = [], limit
        for item in value:
            part = text_content(item, remaining)
            parts.append(part)
            remaining -= len(part) + 1
            if remaining <= 0:
                break
        return "\n".join(parts)[:limit]
    if isinstance(value, dict):
        for key in ("text", "content"):
            if key in value:
                return text_content(value[key], limit)
    return ""


class ModelTrace:
    """One trace per model call, grouped by review job through session.id."""

    def __init__(self, job, body, enabled=True):
        self.span = INVALID_SPAN
        self.capture = False
        self.buffer = bytearray()
        self.output = ""
        self.overflow = False
        self.ended = False
        self.failed = False
        if _provider is None or not enabled:
            return
        cfg = job.config
        self.capture = settings().litereview_trace_content
        self.span = _provider.get_tracer("litereview.model").start_span(
            "chat " + cfg["model"], kind=SpanKind.CLIENT,
            attributes={
                "openinference.span.kind": "LLM",
                "gen_ai.operation.name": "chat",
                "gen_ai.request.model": cfg["model"],
                "gen_ai.provider.name": cfg["connection"]["type"],
                "gen_ai.agent.name": cfg.get("name", "LiteReview"),
                "session.id": job.id,
                "litereview.run_id": job.run_id,
                "litereview.job_id": job.id,
                "litereview.harness": cfg.get("harness", "builtin"),
            },
        )
        if self.capture:
            self.span.set_attribute("input.value", text_content([
                body.get("system", ""), body.get("instructions", ""),
                body.get("messages", body.get("input", "")),
            ]))

    def result(self, data):
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
        if not self.capture or len(self.output) >= CONTENT_LIMIT:
            return
        delta = data.get("delta", {})
        content = ""
        if data.get("type") == "response.output_text.delta" and isinstance(delta, str):
            content = delta
        elif isinstance(delta, dict):
            content = text_content(delta)
        choices = data.get("choices") or []
        if choices and isinstance(choices, list) and isinstance(choices[0], dict):
            content = text_content(choices[0].get("delta") or choices[0].get("message", {}))
        # Final Responses events repeat the deltas; do not append the full response again.
        if not content and not self.output:
            content = text_content(response.get("content") or response.get("output", []))
        self.output += content[:CONTENT_LIMIT - len(self.output)]

    def feed(self, chunk, sse=False):
        if not self.span.is_recording():
            return
        # Bound each incomplete event/body even if an upstream ignores its protocol.
        for part in chunk.splitlines(keepends=True) if sse else (chunk,):
            if len(self.buffer) + len(part) > BUFFER_LIMIT:
                self.buffer.clear()
                self.overflow = True
            if not self.overflow:
                self.buffer.extend(part)
            if sse and part.endswith(b"\n"):
                if not self.overflow and self.buffer.startswith(b"data:"):
                    self._parse(self.buffer[5:])
                self.buffer.clear()
                self.overflow = False

    def _parse(self, raw):
        try:
            self.result(json.loads(raw))
        except (ValueError, TypeError, KeyError, RecursionError):
            pass  # Telemetry parsing must never change the model response.

    def end(self, error=False, status=None):
        if self.ended:
            return
        self.ended = True
        if self.buffer and not self.overflow:
            self._parse(self.buffer)
        self.buffer.clear()
        if self.capture:
            self.span.set_attribute("output.value", self.output)
        if status is not None:
            self.span.set_attribute("http.response.status_code", status)
        self.span.set_status(StatusCode.ERROR if error or self.failed else StatusCode.OK)
        self.span.end()
