import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from app import harnesses, providers, tracing
from app.settings import Settings, settings


@pytest.fixture
def captured(client, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({'service.name': 'litereview'}), shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(tracing, '_provider', provider)
    return exporter


def job():
    return SimpleNamespace(id='job-1', run_id='run-1', config={
        'name': 'Reviewer', 'model': 'model', 'harness': 'codex',
        'connection': {'type': 'gateway', 'api_key': 'must-not-export'},
    })


def test_disabled_by_default(monkeypatch):
    cfg = Settings(_env_file=None)
    assert not cfg.litereview_trace_endpoint
    assert not cfg.litereview_trace_content
    monkeypatch.setattr(tracing, '_provider', None)
    exporter = AsyncMock()
    monkeypatch.setattr(tracing, 'TraceProcessor', exporter)
    tracing.start()
    span = tracing.ModelTrace(job(), {'messages': []})
    span.feed(b'{"choices": []}')
    span.end()
    exporter.assert_not_called()


def test_metadata_only_and_session_grouping(captured):
    for _ in range(2):
        span = tracing.ModelTrace(job(), {'messages': [{'content': 'private input'}]})
        span.result({'choices': [{'message': {'content': 'private output'}}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 4}})
        span.end()
    spans = captured.get_finished_spans()
    assert len(spans) == 2
    assert spans[0].context.trace_id != spans[1].context.trace_id
    for span in spans:
        assert span.attributes['session.id'] == 'job-1'
        assert span.attributes['gen_ai.agent.name'] == 'Reviewer'
        # Lens uses OpenInference model metadata when span.kind selects that format.
        assert span.attributes['llm.model_name'] == 'model'
        assert span.attributes['agent.name'] == 'Reviewer'
        assert span.attributes['gen_ai.usage.input_tokens'] == 10
        assert span.attributes['gen_ai.usage.output_tokens'] == 4
        assert span.status.status_code == StatusCode.OK
        assert 'private' not in str(dict(span.attributes))
        assert 'must-not-export' not in str(dict(span.attributes))


@pytest.mark.parametrize('protocol,event', [
    ('chat', {'choices': [{'delta': {'content': 'answer'}}]}),
    ('responses', {'type': 'response.output_text.delta', 'delta': 'answer'}),
    ('anthropic', {'type': 'content_block_delta', 'delta': {'type': 'text_delta', 'text': 'answer'}}),
])
def test_fragmented_stream_content_is_opt_in(captured, monkeypatch, protocol, event):
    monkeypatch.setattr(settings(), 'litereview_trace_content', True)
    span = tracing.ModelTrace(job(), {'messages': [{'role': 'user', 'content': [
        {'type': 'text', 'text': 'review'}, {'type': 'image_url', 'image_url': {'url': 'private-image'}},
    ]}], 'extra_headers': {'Authorization': 'private-key'}})
    wire = ('data: ' + json.dumps(event) + '\r\n\r\ndata: [DONE]\n\n').encode()
    for char in wire:
        span.feed(bytes([char]), sse=True)
    span.end()
    attrs = dict(captured.get_finished_spans()[0].attributes)
    assert attrs['output.value'] == 'answer'
    assert 'review' in attrs['input.value']
    assert 'private-image' not in str(attrs)
    assert 'private-key' not in str(attrs)


def test_full_trace_content_and_bad_events_do_not_break_stream(captured, monkeypatch):
    monkeypatch.setattr(settings(), 'litereview_trace_content', True)
    span = tracing.ModelTrace(job(), {'input': 'x' * 100_000})
    span.feed(b'data: ' + b'x' * 100_000, sse=True)
    assert len(span.buffer) == 100_006
    span.feed(b'\n', sse=True)
    span.feed(b'data: {"choices": [null]}\ndata: {"usage": "oops"}\n', sse=True)
    for _ in range(20):
        span.feed(b'data: {"delta":{"text":"' + b'y' * 1000 + b'"}}\n', sse=True)
    span.end(error=True)
    span.end()
    spans = captured.get_finished_spans()
    assert len(spans) == 1
    assert 'x' * 100_000 in spans[0].attributes['input.value']
    assert spans[0].attributes['output.value'] == 'y' * 20_000
    assert spans[0].status.status_code == StatusCode.ERROR


def test_slow_collector_does_not_drop_a_full_queue(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    exported = []
    processor = tracing.TraceProcessor('http://collector.test/v1/traces', {})

    def export(span):
        entered.set()
        assert release.wait(10)
        exported.append(span)

    monkeypatch.setattr(processor, 'export', export)
    try:
        for index in range(300):
            processor.on_end(index)
        assert entered.wait(5)
        release.set()
        processor.force_flush()
        assert exported == list(range(300))
    finally:
        release.set()
        processor.shutdown()


@pytest.mark.parametrize('native', [False, True])
def test_actual_model_routes_export_without_changing_response(client, captured, monkeypatch, native):
    from test_harnesses import seed_job

    seed_job()
    monkeypatch.setattr(providers, 'snapshot_connection', lambda _: {
        'type': 'gateway', 'base_url': 'https://provider.test', 'api_key': 'private-provider-key',
    })
    if native:
        real_client = httpx.AsyncClient
        async def upstream(request):
            assert request.headers['authorization'] == 'Bearer private-provider-key'
            assert 'collector-key' not in str(request.headers)
            return httpx.Response(200, headers={'content-type': 'text/event-stream'},
                                  content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n')
        monkeypatch.setattr(harnesses.httpx, 'AsyncClient',
                            lambda **kw: real_client(transport=httpx.MockTransport(upstream)))
        suffix = 'llm/responses'
    else:
        monkeypatch.setattr(providers, 'complete', AsyncMock(return_value={'content': 'ok'}))
        suffix = 'model'
    response = client.post('/api/internal/jobs/test-job/' + suffix,
                           headers={'Authorization': 'Bearer job-token'},
                           json={'messages': [{'role': 'user', 'content': 'hi'}]})
    assert response.status_code == 200
    assert 'ok' in response.text
    spans = captured.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].status.status_code == StatusCode.OK
    assert 'job-token' not in str(dict(spans[0].attributes))
    assert 'private-provider-key' not in str(dict(spans[0].attributes))


def test_background_export_uses_configured_otlp_endpoint_and_key(monkeypatch):
    received, arrived = [], threading.Event()
    class Collector(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, self.headers.get('Authorization'),
                             self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(200)
            self.end_headers()
            arrived.set()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Collector)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(settings(), 'litereview_trace_endpoint',
                        f'http://127.0.0.1:{server.server_port}/v1/traces')
    monkeypatch.setattr(settings(), 'litereview_trace_api_key', 'collector-key')
    try:
        tracing.start()
        trace = tracing.ModelTrace(job(), {})
        trace.end()
        assert arrived.wait(5)
        endpoint, auth, payload = received[0]
        assert endpoint == '/v1/traces'
        assert auth == 'Bearer collector-key'
        message = ExportTraceServiceRequest.FromString(payload)
        assert message.resource_spans[0].scope_spans[0].spans[0].name == 'chat model'
        assert b'collector-key' not in payload
    finally:
        tracing.shutdown()
        server.shutdown()
        server.server_close()


def test_invalid_collector_configuration_does_not_prevent_reviews(monkeypatch, caplog):
    monkeypatch.setattr(settings(), 'litereview_trace_endpoint', 'https://secret@collector.test/v1/traces')
    tracing.start()
    assert tracing._provider is None
    assert 'disabled' in caplog.text
    assert 'secret' not in caplog.text


def test_export_failure_cannot_fail_model_request(client, monkeypatch):
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter
    from test_harnesses import seed_job

    failed = threading.Event()
    class BrokenCollector(SpanExporter):
        def export(self, spans):
            failed.set()
            raise OSError('collector unavailable')
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(BatchSpanProcessor(BrokenCollector(), schedule_delay_millis=10))
    monkeypatch.setattr(tracing, '_provider', provider)
    seed_job()
    monkeypatch.setattr(providers, 'snapshot_connection', lambda _: {'type': 'gateway'})
    monkeypatch.setattr(providers, 'complete', AsyncMock(return_value={'content': 'ok'}))
    for _ in range(2):
        response = client.post('/api/internal/jobs/test-job/model',
                               headers={'Authorization': 'Bearer job-token'},
                               json={'messages': []})
        assert response.status_code == 200
        assert response.json() == {'content': 'ok'}
        assert failed.wait(3)
