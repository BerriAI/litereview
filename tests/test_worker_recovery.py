"""A temporary API outage must not discard safe setup work or a finished review."""

import io
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from runner import agent


def relay_for(monkeypatch, handler):
    client = httpx.AsyncClient
    monkeypatch.setattr(agent.httpx, "AsyncClient",
                        lambda **kwargs: client(transport=httpx.MockTransport(handler), **kwargs))
    monkeypatch.setenv("LR_API_URL", "https://review.test")
    monkeypatch.setenv("LR_JOB_ID", "job")
    monkeypatch.setenv("LR_JOB_TOKEN", "job-token")
    monkeypatch.setattr(agent.asyncio, "sleep", AsyncMock())
    return agent.Relay()


@pytest.mark.parametrize("endpoint", ["bootstrap", "start-review", "finish"])
async def test_safe_callbacks_survive_restart_and_html_gateway_errors(monkeypatch, endpoint):
    calls = []

    def respond(request):
        calls.append(request)
        if len(calls) <= 4:
            return httpx.Response(502, text="<html>Bad Gateway</html>")
        return httpx.Response(200, json={"ok": True})

    relay = relay_for(monkeypatch, respond)
    try:
        assert await relay(endpoint, None if endpoint == "bootstrap" else {"status": "completed"}) == {"ok": True}
        assert len(calls) == 5
        assert len({r.content for r in calls}) == 1
    finally:
        await relay.client.aclose()


@pytest.mark.parametrize("status", [401, 409, 422])
async def test_auth_and_validation_failures_do_not_retry(monkeypatch, status):
    relay = relay_for(monkeypatch, lambda request: httpx.Response(status, json={"detail": "Correct the request"}))
    try:
        with pytest.raises(RuntimeError, match="Correct the request"):
            await relay("finish", {})
        agent.asyncio.sleep.assert_not_called()
    finally:
        await relay.client.aclose()


async def test_outage_retry_stops_and_handles_non_json_errors(monkeypatch):
    relay = relay_for(monkeypatch, lambda request: httpx.Response(503, text="<html>Unavailable</html>"))
    monkeypatch.setattr(agent, "RECOVERY_SECONDS", 0)
    try:
        with pytest.raises(RuntimeError, match="HTTP 503"):
            await relay("finish", {})
        agent.asyncio.sleep.assert_not_called()
    finally:
        await relay.client.aclose()


async def test_archive_restarts_after_gateway_error_and_truncated_stream(tmp_path, monkeypatch):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as archive:
        member = tarfile.TarInfo("repo/file.txt")
        member.size = 4
        archive.addfile(member, io.BytesIO(b"done"))
    calls = 0

    class BrokenStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b"partial transfer that is not a tar archive"
            raise httpx.RemoteProtocolError("peer closed connection")

    def respond(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(502, text="Bad Gateway")
        if calls == 2:
            return httpx.Response(200, stream=BrokenStream())
        return httpx.Response(200, content=raw.getvalue())

    monkeypatch.setattr(agent, "REPO", tmp_path / "repo")
    monkeypatch.setattr(agent.asyncio, "sleep", AsyncMock())
    async with httpx.AsyncClient(base_url="https://review.test/", transport=httpx.MockTransport(respond)) as client:
        relay = SimpleNamespace(client=client, event=AsyncMock())
        await agent.prepare_repository(relay)
    assert calls == 3
    assert (agent.REPO / "file.txt").read_bytes() == b"done"
    assert relay.event.await_args_list[-1].args == ("Extracting repository",)
