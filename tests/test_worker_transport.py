import gzip
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from starlette.requests import Request
from test_harnesses import seed_job

from app.db import Job, Session
from runner import agent
from runner.bridge import Bridge

SOURCE = "Review `curl -X POST <proxy>/v1/systemone`\nUnicode: café 🧪"
HEADERS = {
    "authorization": "Bearer job-token",
    "content-type": "application/json",
    "content-encoding": "gzip",
}


@pytest.mark.asyncio
async def test_sandbox_compresses_callbacks_and_native_model_payloads(monkeypatch):
    sent = []

    async def upstream(request):
        sent.append(request)
        assert request.headers["authorization"] == "Bearer job-token"
        assert request.headers["content-encoding"] == "gzip"
        assert int(request.headers["content-length"]) == len(request.content)
        return httpx.Response(200, json={"ok": True})

    actual_client = httpx.AsyncClient
    monkeypatch.setattr(
        agent.httpx, "AsyncClient",
        lambda **kwargs: actual_client(transport=httpx.MockTransport(upstream), **kwargs),
    )
    monkeypatch.setenv("LR_API_URL", "https://review.test")
    monkeypatch.setenv("LR_JOB_ID", "test-job")
    monkeypatch.setenv("LR_JOB_TOKEN", "job-token")
    relay = agent.Relay()
    try:
        await relay("event", {"message": SOURCE})
        assert json.loads(gzip.decompress(sent[-1].content)) == {"message": SOURCE}
        raw = json.dumps({"input": SOURCE, "stream": True}, ensure_ascii=False).encode()

        async def receive():
            return {"type": "http.request", "body": raw}

        request = Request({
            "type": "http", "method": "POST", "path": "/v1/responses",
            "path_params": {"path": "responses"},
            "headers": [(b"anthropic-version", b"2023-06-01")],
        }, receive)
        bridge = Bridge(AsyncMock(), relay, {"capabilities": [], "mcp_definitions": []})
        response = await bridge.model_http(request)
        assert response.status_code == 200
        assert gzip.decompress(sent[-1].content) == raw
        assert sent[-1].headers["anthropic-version"] == "2023-06-01"
        assert str(sent[-1].url).endswith("/api/internal/jobs/test-job/llm/responses")
        assert b"".join([part async for part in response.body_iterator]) == b'{"ok":true}'
    finally:
        await relay.client.aclose()


def test_compressed_review_results_and_events_preserve_source(client):
    seed_job()
    path = "/api/internal/jobs/test-job/"
    event = client.post(path + "event", headers=HEADERS, content=gzip.compress(
        json.dumps({"message": SOURCE}).encode()))
    assert event.status_code == 200
    result = {"status": "completed", "summary": SOURCE, "findings": [], "limitations": []}
    finished = client.post(path + "finish", headers=HEADERS, content=gzip.compress(json.dumps(result).encode()))
    assert finished.status_code == 200
    with Session() as db:
        job = db.get(Job, "test-job")
        assert job.logs[-1]["message"] == SOURCE
        assert job.status == "completed"
        assert job.result["summary"] == SOURCE
    # Old workers and retries can still finish idempotently after the deployment.
    assert client.post(path + "finish", headers={"authorization": "Bearer job-token"}, json=result).status_code == 200


@pytest.mark.parametrize("endpoint", ["event", "finish", "model", "mcp", "artifact", "llm/responses"])
def test_job_authentication_precedes_decompression(client, endpoint):
    seed_job()
    path = "/api/internal/jobs/test-job/" + endpoint
    invalid = {**HEADERS, "authorization": "Bearer wrong-job-token"}
    assert client.post(path, headers=invalid, content=b"not gzip").status_code == 401
    assert client.post(path, headers=HEADERS, content=b"not gzip").status_code == 400


@pytest.mark.parametrize("body", [gzip.compress(b"not json"), gzip.compress(b"{}")[:-4]])
def test_corrupt_compressed_payloads_are_client_errors(client, body):
    seed_job()
    response = client.post("/api/internal/jobs/test-job/event", headers=HEADERS, content=body)
    assert response.status_code == 400


def test_unsupported_encoding_is_rejected(client):
    seed_job()
    response = client.post("/api/internal/jobs/test-job/event", content=b"{}",
                           headers={**HEADERS, "content-encoding": "br"})
    assert response.status_code == 415
