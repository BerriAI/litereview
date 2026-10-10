import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest

from app import harnesses, reviews, runtime
from app.db import Job, Run, Session
from app.security import digest
from runner.bridge import Bridge
from runner.catalog import HARNESS_VERSIONS, NPM_PACKAGES
from runner.tools import Toolset


def test_saved_and_legacy_reviewers_include_terminal(owner):
    from test_reviews import setup

    from app.db import Resource
    from app.schemas import AgentModel

    assert AgentModel(connection_id="provider", model="model").capabilities == ["terminal"]
    assert AgentModel(connection_id="provider", model="model", capabilities=["browser"]).capabilities == [
        "terminal",
        "browser",
    ]
    _, _, agent, _, group = setup(owner)
    with Session() as db:
        legacy = db.get(Resource, agent["id"])
        legacy.data = {**legacy.data, "capabilities": []}
        db.commit()
        resources = {r.id: r for r in db.query(Resource).all()}
        expanded = reviews.expand([group], resources)
    assert all(job["capabilities"] == ["terminal"] for job in expanded)


def test_codex_tools_allow_verification_and_delegation(tmp_path, monkeypatch):
    from app.schemas import AgentModel
    from runner import harness

    monkeypatch.setattr(harness, "STATE", tmp_path)
    cfg = AgentModel(harness="codex", connection_id="provider", model="model").model_dump()
    args = harness.command(cfg, "http://127.0.0.1:8000", "Review", "PR", {})
    settings = [args[i + 1] for i, arg in enumerate(args[:-1]) if arg == "-c"]
    assert "features.shell_tool=true" in settings
    assert "features.multi_agent=true" in settings
    assert not any(s.startswith("features.apply_patch_freeform=") for s in settings)


@pytest.mark.parametrize("selected", HARNESS_VERSIONS)
def test_image_installs_only_selected_harness(monkeypatch, selected):
    installed = []

    class Image:
        def from_registry(self, *args, **kwargs):
            return self

        def apt_install(self, *args):
            return self

        def pip_install(self, *args):
            installed.extend(args)
            return self

        def run_commands(self, *args):
            installed.extend(args)
            return self

        def add_local_dir(self, *args):
            return self

    monkeypatch.setattr(runtime.modal, "Image", Image())
    runtime.sandbox_image(selected)
    dependencies = "\n".join(installed)
    for harness, package in NPM_PACKAGES.items():
        assert (package + "@" in dependencies) == (harness == selected)
    assert ("deepagents==" in dependencies) == (selected == "deepagents")


def test_unknown_harness_never_starts_an_image():
    with pytest.raises(ValueError, match="Unknown harness"):
        runtime.sandbox_image("untrusted-package")


def test_harness_defaults_and_incompatible_connections(owner):
    connection = owner.post("/api/resources/connections", json={"name": "OpenAI", "type": "openai"}).json()
    agent = {
        "name": "check",
        "connection_id": connection["id"],
        "model": "test-model",
        "instructions": "Review",
    }
    response = owner.post("/api/resources/agents", json=agent)
    assert response.status_code == 200
    assert response.json()["harness"] == "builtin"
    agent.update(name="claude", harness="claude-code")
    assert owner.post("/api/resources/agents", json=agent).status_code == 400
    agent.update(harness="untrusted-package")
    assert owner.post("/api/resources/agents", json=agent).status_code == 422
    assert {h["id"] for h in owner.get("/api/harnesses").json()} == set(HARNESS_VERSIONS)


@pytest.mark.asyncio
async def test_shared_tools_enforce_capabilities_without_inventing_visual_tasks():
    relay = AsyncMock()
    cfg = {"capabilities": [], "mcp_definitions": []}
    tools = Toolset({"config": cfg}, relay)
    bridge = Bridge(tools, relay, cfg)
    assert "lr_terminal" not in bridge.definitions
    assert "lr_browser" not in bridge.definitions
    assert (await bridge.call("lr_browser", {"action": "screenshot"}))["isError"]
    assert (await bridge.call("lr_read_file", {}))["isError"]
    visual = {"capabilities": ["computer"], "mcp_definitions": []}
    bridge = Bridge(tools, relay, visual)
    final = {"summary": "done", "findings": [], "limitations": []}
    assert not (await bridge.call("lr_finish_review", final))["isError"]
    assert bridge.finished.is_set()
    assert (await bridge.call("lr_read_diff", {}))["isError"]


def seed_job():
    with Session() as db:
        run = Run(
            id="test-run",
            delivery="test",
            repo="private/repo",
            pr=1,
            actor="tester",
            head="a" * 40,
            base="b" * 40,
            snapshot={},
        )
        job = Job(
            id="test-job",
            run_id=run.id,
            status="running",
            started_at=time.time(),
            token_hash=digest("job-token"),
            config={"model": "chosen-model", "connection": {"id": "provider", "type": "gateway"}},
        )
        db.add_all([run, job])
        db.commit()


def test_native_relay_is_scoped_streaming_and_counts_calls(client, monkeypatch):
    seed_job()
    sent = []
    monkeypatch.setattr(
        harnesses.providers,
        "snapshot_connection",
        lambda _: {"type": "gateway", "base_url": "https://provider.test", "api_key": "private-provider-key"},
    )
    actual_client = httpx.AsyncClient

    async def upstream(request):
        sent.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b'data: {"ok":true}\n\ndata: [DONE]\n\n',
        )

    monkeypatch.setattr(
        harnesses.httpx,
        "AsyncClient",
        lambda **kwargs: actual_client(transport=httpx.MockTransport(upstream)),
    )
    path = "/api/internal/jobs/test-job/llm/"
    assert client.post(path + "responses", json={}).status_code == 401
    headers = {"authorization": "Bearer job-token"}
    assert client.post(path + "admin/keys", headers=headers, json={}).status_code == 404
    response = client.post(
        path + "responses", headers=headers, json={"model": "other-model", "stream": True, "store": True}
    )
    assert response.status_code == 200
    assert response.text.endswith("data: [DONE]\n\n")
    body = json.loads(sent[-1].content)
    assert body["model"] == "chosen-model"
    assert body["store"] is False
    assert sent[-1].headers["authorization"] == "Bearer private-provider-key"
    assert "private-provider-key" not in response.text
    client.post(path + "messages", headers=headers, json={"model": "other"})
    assert str(sent[-1].url) == "https://provider.test/v1/messages"
    with Session() as db:
        assert db.get(Job, "test-job").model_calls == 2
        db.get(Job, "test-job").model_calls = 60
        db.commit()
    assert client.post(path + "responses", headers=headers, json={}).status_code == 200
    with Session() as db:
        job = db.get(Job, "test-job")
        job.config = {**job.config, "max_rounds": 61}
        db.commit()
    assert client.post(path + "responses", headers=headers, json={}).status_code == 429


@pytest.mark.asyncio
async def test_capability_checks_never_publish_to_github(client, monkeypatch):
    from app.agent_checks import enqueue
    from app.schemas import AgentModel

    data = AgentModel(connection_id="provider", model="test", harness="pi")
    created = enqueue(data, {"id": "provider", "type": "gateway", "api_key": "secret"})
    github = AsyncMock(side_effect=AssertionError("Must never call GitHub"))
    monkeypatch.setattr(reviews.github, "repo_request", github)
    await reviews.publish(created["id"])
    with Session() as db:
        job = db.query(Job).filter(Job.run_id == created["id"]).one()
        assert "secret" not in json.dumps(job.config)
        assert job.config["harness_version"] == HARNESS_VERSIONS["pi"]
        job.status = "completed"
        job.result = {"limitations": []}
        db.commit()
    await reviews.publish(created["id"])
    github.assert_not_called()


def test_cancel_check_is_scoped_and_stops_sandbox(owner, monkeypatch):
    from app.agent_checks import enqueue
    from app.schemas import AgentModel

    created = enqueue(
        AgentModel(connection_id="provider", model="test"), {"id": "provider", "type": "gateway"}
    )
    with Session() as db:
        job = db.query(Job).filter(Job.run_id == created["id"]).one()
        jid = job.id
        job.status = "running"
        job.sandbox_id = "test-sandbox"
        db.commit()
    stop = AsyncMock()
    monkeypatch.setattr(runtime, "terminate", stop)
    assert owner.post("/api/agents/check/" + created["id"] + "/cancel").status_code == 200
    stop.assert_awaited_once_with("test-sandbox")
    with Session() as db:
        assert db.get(Job, jid).status == "failed"
        assert db.get(Job, jid).result["limitations"] == ["Test stopped by the user."]
        db.get(Run, created["id"]).snapshot = {}
        db.commit()
    assert owner.post("/api/agents/check/" + created["id"] + "/cancel").status_code == 404


@pytest.mark.parametrize(
    "protocol,kind,expected",
    [
        ("responses", "openai", "https://api.openai.com/v1/responses"),
        ("messages", "anthropic", "https://api.anthropic.com/v1/messages"),
    ],
)
def test_native_relay_direct_provider_headers(client, monkeypatch, protocol, kind, expected):
    seed_job()
    monkeypatch.setattr(
        harnesses.providers, "snapshot_connection", lambda _: {"type": kind, "api_key": "private-key"}
    )
    sent = []
    actual = httpx.AsyncClient

    async def upstream(request):
        sent.append(request)
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(
        harnesses.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(upstream))
    )
    response = client.post(
        "/api/internal/jobs/test-job/llm/" + protocol,
        headers={"authorization": "Bearer job-token"},
        json={"model": "override", "api_key": "attacker", "extra_headers": {"x-api-key": "attacker"}},
    )
    assert response.status_code == 200
    assert str(sent[0].url) == expected
    assert json.loads(sent[0].content)["model"] == "chosen-model"
    assert "api_key" not in json.loads(sent[0].content)
    assert sent[0].headers.get("x-api-key" if kind == "anthropic" else "authorization") == (
        "private-key" if kind == "anthropic" else "Bearer private-key"
    )


@pytest.mark.asyncio
async def test_bridge_keeps_screenshot_as_image_and_tool_errors_recoverable():
    import base64

    from runner.bridge import content_blocks

    payload = content_blocks(
        {"image": "data:image/png;base64," + base64.b64encode(b"png").decode(), "artifact_id": "shot"}
    )
    assert payload["content"][1]["type"] == "image"
    assert payload["content"][1]["mimeType"] == "image/png"
    relay = AsyncMock()
    tools = AsyncMock()
    tools.execute.side_effect = RuntimeError("MCP unavailable")
    bridge = Bridge(tools, relay, {"capabilities": [], "mcp_definitions": []})
    assert (await bridge.call("lr_read_file", {"path": "maths.py"}))["isError"]
    assert not bridge.finished.is_set()
    result = {"summary": "Could not inspect repository", "findings": [], "limitations": ["MCP unavailable"]}
    assert not (await bridge.call("lr_finish_review", result))["isError"]
    assert bridge.result["limitations"]
