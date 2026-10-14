import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from test_harnesses import seed_job

from app import runtime
from app.db import Job, Session
from runner import agent, harness
from runner.bridge import Bridge
from runner.diagnostics import PREFIX, ReviewError, failure_record, parse


def sandbox_with_logs(lines, code=1, before_read=None):
    async def tail(*args, **kwargs):
        if before_read:
            before_read()
        for line in lines:
            yield SimpleNamespace(message=line)

    return SimpleNamespace(returncode=code, wait=SimpleNamespace(aio=AsyncMock()),
                           logs=SimpleNamespace(tail=SimpleNamespace(aio=tail)))


async def test_model_failure_survives_failed_callback_and_reaches_run_logs(client, monkeypatch, capsys):
    seed_job()
    relay = AsyncMock(side_effect=[{}, agent.response_error(httpx.Response(503), "result submission")])
    monkeypatch.setattr(agent, "Relay", lambda: relay)
    monkeypatch.setattr(agent, "run", AsyncMock(side_effect=ReviewError(
        "The model request failed (HTTP 502).", stage="model request", http_status=502, exit_code=1)))
    assert await agent.main() == 1
    lines = capsys.readouterr().out.splitlines()
    records = [parse(line) for line in lines]
    assert [r["http_status"] for r in records] == [502, 503]
    assert records[0]["exit_code"] == 1
    assert records[-1]["kind"] == "submission_failed"

    await runtime.watch("test-job", sandbox_with_logs(lines))
    with Session() as db:
        job = db.get(Job, "test-job")
        assert job.status == "failed"
        assert job.result["summary"] == "The review stopped during model request."
        assert "HTTP 502" in job.result["limitations"][0]
        assert "HTTP 503" in job.result["limitations"][1]
        assert any("failure report also failed" in x for x in job.result["limitations"])
        assert any("HTTP 502" in e["message"] for e in job.logs)
        assert "PUBLIC_URL" not in json.dumps(job.result)


async def test_failed_result_submission_is_distinct_from_model_failure(client, monkeypatch, capsys):
    seed_job()
    relay = AsyncMock(return_value={})
    monkeypatch.setattr(agent, "Relay", lambda: relay)
    monkeypatch.setattr(agent, "run", AsyncMock(side_effect=agent.response_error(
        httpx.Response(502), "result submission")))
    await agent.main()
    result = await runtime.sandbox_failure(sandbox_with_logs(capsys.readouterr().out.splitlines()))
    assert result["summary"] == "The worker could not submit its review result."
    assert result["limitations"][0] == "The review service returned HTTP 502 during result submission."


@pytest.mark.parametrize("code", [0, 137, -9])
async def test_exit_code_is_reported_without_guessing_a_cause(code):
    result = await runtime.sandbox_failure(sandbox_with_logs([], code))
    assert result["status"] == "failed"
    assert f"Sandbox exit code: {code}." in result["limitations"]
    assert "underlying error" in result["limitations"][0]
    assert not any(word in json.dumps(result) for word in ["PUBLIC_URL", "memory", "success"])


async def test_legacy_submission_failure_is_recognized_without_inventing_original_error():
    result = await runtime.sandbox_failure(sandbox_with_logs([
        "Harness diagnostic:", "arbitrary PR content",
        "Unable to submit result; the service will mark this job incomplete."], 0))
    assert result["summary"] == "The worker could not submit its result."
    assert "underlying error" in result["limitations"][0]
    assert "arbitrary PR content" not in json.dumps(result)


async def test_unavailable_modal_logs_do_not_hide_exit_status():
    sandbox = sandbox_with_logs([], 137, before_read=lambda: (_ for _ in ()).throw(TimeoutError()))
    result = await runtime.sandbox_failure(sandbox)
    assert "Sandbox exit code: 137." in result["limitations"]
    assert "Sandbox diagnostics could not be read (TimeoutError)." in result["limitations"]


@pytest.mark.parametrize("status", ["completed", "cancelling", "superseded"])
async def test_late_completion_or_cancellation_is_not_overwritten(client, status):
    seed_job()

    def finish():
        with Session.begin() as db:
            job = db.get(Job, "test-job")
            job.status = status
            job.result = {"summary": "Saved result"}

    await runtime.watch("test-job", sandbox_with_logs([], before_read=finish))
    with Session() as db:
        job = db.get(Job, "test-job")
        assert job.status == status
        assert job.result == {"summary": "Saved result"}


async def test_saved_result_does_not_fetch_modal_logs(client):
    seed_job()
    with Session.begin() as db:
        db.get(Job, "test-job").status = "completed"
    sandbox = sandbox_with_logs([], before_read=lambda: pytest.fail("Should not read logs"))
    await runtime.watch("test-job", sandbox)


def test_callback_details_and_exception_strings_do_not_leak_into_diagnostics():
    error = agent.response_error(httpx.Response(401, json={"detail": "Bearer private-secret"}), "setup")
    assert str(error) == "Bearer private-secret"  # Validation feedback remains available to the harness.
    assert "private-secret" not in json.dumps(failure_record(error))
    try:
        raise ValueError("api_key=private-secret and the whole request body")
    except ValueError as exc:
        record = failure_record(exc)
    assert record["error_type"] == "ValueError"
    assert "test_failure_diagnostics.py:" in record["message"]
    assert "private-secret" not in json.dumps(record)


async def test_harness_exit_keeps_code_without_dumping_transcript(tmp_path, monkeypatch, capsys):
    class Bridge:
        finished = asyncio.Event()
        model_error = None
        submission_error = None

        @asynccontextmanager
        async def serve(self):
            yield "http://unused.test"

    monkeypatch.setattr(harness, "Bridge", lambda *args: Bridge())
    monkeypatch.setattr(harness, "STATE", tmp_path)
    monkeypatch.setattr(harness, "command", lambda *args, **kwargs: [
        sys.executable, "-c", "print('private transcript '*10000); raise SystemExit(17)"])
    with pytest.raises(ReviewError) as caught:
        await harness.run_external(SimpleNamespace(child_env=lambda: dict(os.environ)), AsyncMock(),
                                   {"config": {}}, "instructions", "PR")
    assert caught.value.exit_code == 17
    assert "exit 17" in str(caught.value)
    assert capsys.readouterr().out == ""
    assert "private transcript" in (tmp_path / "harness.log").read_text()


def test_failed_callback_adds_cause_to_activity_once(client):
    seed_job()
    result = {"status": "failed", "summary": "The review could not complete.", "findings": [],
              "limitations": ["The model request failed (HTTP 503)."]}
    for _ in range(2):
        assert client.post("/api/internal/jobs/test-job/finish", json=result,
                           headers={"authorization": "Bearer job-token"}).status_code == 200
    with Session() as db:
        messages = [e["message"] for e in db.get(Job, "test-job").logs]
        assert messages.count(result["limitations"][0]) == 1


async def test_bridge_keeps_http_status_and_interrupted_stream_without_guessing_the_cause():
    relay = AsyncMock()
    bridge = Bridge(AsyncMock(), relay, {"capabilities": [], "mcp_definitions": []})
    request = SimpleNamespace(path_params={"path": "responses"}, headers={}, body=AsyncMock(return_value=b"{}"))
    relay.client.build_request = lambda *args, **kwargs: None
    relay.client.send.return_value = httpx.Response(429, content=b"limit")
    response = await bridge.model_http(request)
    assert response.status_code == 429
    record = failure_record(bridge.model_error)
    assert record["http_status"] == 429
    assert "credits" not in record["message"]

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial model response"
            raise httpx.RemoteProtocolError("private upstream URL and request")

    relay.client.send.return_value = httpx.Response(200, stream=BrokenStream())
    response = await bridge.model_http(request)
    assert bridge.model_error is None
    with pytest.raises(httpx.RemoteProtocolError):
        async for _ in response.body_iterator:
            pass
    record = failure_record(bridge.model_error)
    assert record["stage"] == "model response"
    assert "RemoteProtocolError" in record["message"]
    assert "private upstream" not in record["message"]


def test_non_diagnostic_stdout_is_ignored():
    for line in ["arbitrary transcript", PREFIX + "bad json", PREFIX + "[]", PREFIX + '{"kind":[]}',
                 PREFIX + '{"kind":"failure"}']:
        assert parse(line) is None
