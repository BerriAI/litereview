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
from app.trace_messages import message_attributes, messages


@pytest.fixture
def captured(client, monkeypatch):
    exporter = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource({'service.name': 'litereview'}), shutdown_on_exit=False)
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    monkeypatch.setattr(tracing, '_provider', provider)
    monkeypatch.setattr(tracing, '_processor', processor)
    return exporter


def job():
    return SimpleNamespace(id='job-1', run_id='run-1', config={
        'name': 'Reviewer', 'model': 'model', 'harness': 'codex', 'target_kind': 'agent',
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


def test_one_review_thread_for_19_calls_and_parent(captured):
    review = job()
    for _ in range(19):
        span = tracing.ModelTrace(job(), {'messages': [{'content': 'private input'}]})
        span.result({'choices': [{'message': {'content': 'private output'}}],
                     'usage': {'prompt_tokens': 10, 'completion_tokens': 4}})
        span.end()
    review.started_at, review.finished_at = 100.5, 200.5
    review.status, review.model_calls, review.result = 'completed', 19, {'summary': 'private result'}
    tracing.finish_job(review, SimpleNamespace(id='run-1', status='completed', repo='org/repo', pr=1, head='abc', created_at=90))
    *spans, root = captured.get_finished_spans()
    assert len(spans) == 19
    assert len({span.context.span_id for span in spans}) == 19
    assert root.parent is None
    assert root.attributes['openinference.span.kind'] == 'AGENT'
    assert root.start_time == 100_500_000_000
    assert root.end_time == 200_500_000_000
    assert 'private' not in str(dict(root.attributes))
    for span in spans:
        assert span.context.trace_id == root.context.trace_id
        assert span.parent.span_id == root.context.span_id
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
    assert attrs['llm.output_messages.0.message.content'] == 'answer'
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
    assert spans[0].attributes['llm.output_messages.0.message.content'] == 'y' * 20_000
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


def test_job_context_survives_restart_and_separates_agents(captured, monkeypatch):
    first = tracing.ModelTrace(job(), {})
    first.end()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(captured))
    monkeypatch.setattr(tracing, '_provider', provider)
    second = tracing.ModelTrace(job(), {})
    second.end()
    other = job()
    other.id = 'job-2'
    third = tracing.ModelTrace(other, {})
    third.end()
    a, b, c = captured.get_finished_spans()
    assert a.context.trace_id == b.context.trace_id
    assert a.parent.span_id == b.parent.span_id
    assert c.context.trace_id != b.context.trace_id


@pytest.mark.parametrize('status,limitations', [('completed', []), ('completed', ['Required check failed']),
                                               ('failed', []), ('timed_out', [])])
def test_review_root_contains_final_result_and_actual_status(captured, monkeypatch, status, limitations):
    monkeypatch.setattr(settings(), 'litereview_trace_content', True)
    review = job()
    review.started_at, review.finished_at, review.model_calls = 100, 220, 3
    review.status = status
    review.config['instructions'] = 'Review this code'
    review.result = {'summary': 'Verified the regression', 'findings': [{'title': 'The bug', 'severity': 'high', 'explanation': 'Verified failure'}],
                     'limitations': limitations}
    tracing.finish_job(review, SimpleNamespace(id='run-1', status='completed', repo='org/repo', pr=1, head='abc', created_at=90))
    root, = captured.get_finished_spans()
    assert root.status.status_code == (StatusCode.OK if status == 'completed' and not limitations else StatusCode.ERROR)
    assert 'Review this code' in root.attributes['input.value']
    assert 'Verified the regression' in root.attributes['llm.output_messages.0.message.content']
    assert '### High · The bug' in root.attributes['llm.output_messages.0.message.content']
    assert json.loads(root.attributes['litereview.result']) == review.result


def test_responses_tool_only_stream_and_tool_result_input(captured, monkeypatch):
    monkeypatch.setattr(settings(), 'litereview_trace_content', True)
    call = {'type': 'function_call', 'id': 'item1', 'call_id': 'call1', 'name': 'shell', 'arguments': ''}
    trace = tracing.ModelTrace(job(), {'input': 'Run tests'})
    events = [
        {'type': 'response.output_item.added', 'output_index': 0, 'item': call},
        {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'delta': '{"cmd":'},
        {'type': 'response.function_call_arguments.delta', 'output_index': 0, 'delta': '"pytest"}'},
        {'type': 'response.output_item.done', 'output_index': 0,
         'item': {**call, 'arguments': '{"cmd":"pytest"}'}},
        {'type': 'response.completed', 'response': {'id': 'resp1', 'output': [{**call, 'arguments': '{"cmd":"pytest"}'}]}},
    ]
    for event in events:
        wire = ('data: ' + json.dumps(event) + '\n\n').encode()
        for byte in wire:
            trace.feed(bytes([byte]), sse=True)
    trace.end()
    attrs = captured.get_finished_spans()[0].attributes
    assert json.loads(attrs['output.value'])['id'] == 'resp1'
    assert attrs['llm.output_messages.0.message.tool_calls.0.tool_call.function.name'] == 'shell'
    assert attrs['llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments'] == '{"cmd":"pytest"}'
    assert len(json.loads(attrs['output.value'])['messages']) == 1
    followup = tracing.ModelTrace(job(), {'input': [
        {**call, 'arguments': '{"cmd":"pytest"}'},
        {'type': 'function_call_output', 'call_id': 'call1', 'output': '2 tests passed'},
    ]})
    followup.result({'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Done'}]}]})
    followup.end()
    attrs = captured.get_finished_spans()[1].attributes
    assert attrs['llm.input_messages.1.message.role'] == 'tool'
    assert attrs['llm.input_messages.1.message.name'] == 'shell'
    assert attrs['llm.input_messages.1.message.content'] == '2 tests passed'
    assert attrs['llm.output_messages.0.message.content'] == 'Done'


def test_chat_parallel_tool_deltas_and_builtin_double_observation(captured, monkeypatch):
    monkeypatch.setattr(settings(), 'litereview_trace_content', True)
    trace = tracing.ModelTrace(job(), {})
    for calls in [
        [{'index': 0, 'id': 'c1', 'function': {'name': 'shell', 'arguments': '{"cmd":'}},
         {'index': 1, 'id': 'c2', 'function': {'name': 'read', 'arguments': '{"path":'}}],
        [{'index': 1, 'function': {'arguments': '"README"}'}},
         {'index': 0, 'function': {'arguments': '"pwd"}'}}],
    ]:
        trace.result({'choices': [{'index': 0, 'delta': {'tool_calls': calls}}]})
    trace.end()
    attrs = captured.get_finished_spans()[0].attributes
    assert attrs['llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments'] == '{"cmd":"pwd"}'
    assert attrs['llm.output_messages.0.message.tool_calls.1.tool_call.function.arguments'] == '{"path":"README"}'
    trace = tracing.ModelTrace(job(), {})
    result = {'role': 'assistant', 'content': None, 'tool_calls': [
        {'id': 'c3', 'function': {'name': 'finish_review', 'arguments': '{"summary":"Done"}'}}]}
    trace.result({'id': 'chat1', 'choices': [{'message': result}]})
    trace.result(result)
    trace.end()
    output = json.loads(captured.get_finished_spans()[1].attributes['output.value'])
    assert output['id'] == 'chat1'
    assert len(output['messages'][0]['tool_calls']) == 1


def test_anthropic_tool_arguments_and_results(captured, monkeypatch):
    monkeypatch.setattr(settings(), 'litereview_trace_content', True)
    trace = tracing.ModelTrace(job(), {'messages': [
        {'role': 'assistant', 'content': [{'type': 'tool_use', 'id': 'a1', 'name': 'Bash', 'input': {'command': 'pwd'}}]},
        {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'a1', 'content': [
            {'type': 'text', 'text': '/workspace/repo'}, {'type': 'image', 'source': {'data': 'private-image'}},
        ]}]},
    ]})
    trace.result({'type': 'message_start', 'message': {'id': 'msg1', 'usage': {'input_tokens': 10}}})
    trace.result({'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'tool_use', 'id': 'a2', 'name': 'Bash', 'input': {}}})
    for delta in ['{"command":', '"pytest"}']:
        trace.result({'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'input_json_delta', 'partial_json': delta}})
    trace.result({'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}, 'usage': {'output_tokens': 12}})
    trace.end()
    attrs = captured.get_finished_spans()[0].attributes
    assert attrs['llm.input_messages.1.message.role'] == 'tool'
    assert attrs['llm.input_messages.1.message.content'] == '/workspace/repo'
    assert attrs['llm.output_messages.0.message.tool_calls.0.tool_call.function.arguments'] == '{"command":"pytest"}'
    assert attrs['gen_ai.usage.input_tokens'] == 10
    assert attrs['gen_ai.usage.output_tokens'] == 12
    assert 'private-image' not in str(dict(attrs))


def test_custom_tools_and_encrypted_reasoning():
    conversation = messages([
        {'type': 'custom_tool_call', 'call_id': 'c1', 'name': 'apply_patch', 'input': '*** patch'},
        {'type': 'custom_tool_call_output', 'call_id': 'c1', 'output': 'Done'},
        {'type': 'reasoning', 'summary': [{'type': 'summary_text', 'text': 'Checking tests'}], 'encrypted_content': 'private-ciphertext'},
    ])
    attrs = message_attributes('input', conversation)
    assert attrs['llm.input_messages.0.message.tool_calls.0.tool_call.function.arguments'] == '*** patch'
    assert attrs['llm.input_messages.1.message.content'] == 'Done'
    assert 'private-ciphertext' not in str(attrs)


@pytest.mark.asyncio
async def test_private_check_publication_exports_root_once(client, captured, monkeypatch):
    from test_harnesses import seed_job

    from app import reviews
    from app.db import Job, Run, Session

    seed_job()
    with Session() as db:
        review = db.get(Job, 'test-job')
        review.status, review.finished_at = 'failed', 220
        review.config = {**review.config, 'name': 'Check', 'target_kind': 'agent'}
        review.result = {'summary': 'Sandbox failed', 'limitations': ['Missing dependency']}
        run = db.get(Run, review.run_id)
        run.snapshot = {'harness_check': True}
        run_id = run.id
        db.commit()
    github = AsyncMock()
    monkeypatch.setattr(reviews.github, 'repo_request', github)
    await reviews.publish(run_id)
    await reviews.publish(run_id)
    assert len(captured.get_finished_spans()) == 1
    github.assert_not_called()


@pytest.mark.parametrize('response', [
    {'choices': [{'message': {'role': 'assistant', 'content': 'Done', 'tool_calls': None}}]},
    {'role': 'assistant', 'content': [{'type': 'text', 'text': 'Done'}]},
    {'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Done'}]}]},
])
def test_nonstreaming_content_with_nullable_fields(captured, monkeypatch, response):
    monkeypatch.setattr(settings(), 'litereview_trace_content', True)
    trace = tracing.ModelTrace(job(), {'messages': [{'role': 'user', 'content': 'Review', 'tool_calls': None}]})
    trace.feed(json.dumps(response).encode())
    trace.end()
    attrs = captured.get_finished_spans()[0].attributes
    assert attrs['llm.input_messages.0.message.content'] == 'Review'
    assert attrs['llm.output_messages.0.message.content'] == 'Done'


def test_final_trace_review_is_markdown_and_preserves_code():
    output = tracing.review_output({
        'summary': '## Review\n\nVerified a defect.',
        'findings': [{'title': 'Order matters', 'severity': 'high', 'explanation': 'Use set equality.',
                      'file': 'maths.py', 'line': 2, 'evidence': ['The regression fails.']}],
        'checks': [{'name': 'pytest', 'status': 'failed', 'details': '```text\n> assert same_cases(a, b)\n```'}],
        'limitations': ['Integration check could not run.'],
    })
    assert '> assert same_cases(a, b)' in output
    assert 'Location: `maths.py:2`' in output
    assert 'The regression fails.' in output
    assert 'Integration check could not run.' in output
    assert '<details>' not in output and '<!--' not in output and '&gt;' not in output
