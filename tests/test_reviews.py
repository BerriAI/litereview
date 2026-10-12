import asyncio
import hashlib
import hmac
import io
import json
import tarfile
import time
import zipfile
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from sqlalchemy import select

from app import github, providers, reviews
from app.db import Job, Resource, Run, Session
from app.security import digest
from runner.agent import extract_archive
from runner.tools import Toolset


@pytest.mark.parametrize("cap, expected", [(0, 20), (3, 3)])
async def test_dispatcher_across_ten_reviews(owner, monkeypatch, cap, expected):
    from app import runtime
    from app.settings import settings

    setup(owner)
    mock_github(monkeypatch)
    for i in range(10):
        await reviews.accept(payload(comment_id=12345 + i), f"concurrent-{i}")
    monkeypatch.setattr(settings(), "concurrency", cap)
    started = set()
    ready = asyncio.Event()
    release = asyncio.Event()

    async def launch(job_id, token):
        started.add(job_id)
        if len(started) == expected:
            ready.set()
        await release.wait()

    monkeypatch.setattr(runtime, "launch", launch)
    monkeypatch.setattr(reviews, "publish", AsyncMock())
    dispatcher = asyncio.create_task(reviews.dispatcher())
    try:
        await asyncio.wait_for(ready.wait(), 5)
        with Session() as db:
            jobs = db.scalars(select(Job)).all()
            assert sum(j.status == "starting" for j in jobs) == expected
            assert sum(j.status == "queued" for j in jobs) == 20 - expected
            assert len({j.run_id for j in jobs}) == 10
    finally:
        dispatcher.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await dispatcher


def test_concurrency_defaults_to_on_demand(monkeypatch):
    from pydantic import ValidationError

    from app.settings import Settings

    monkeypatch.delenv("CONCURRENCY", raising=False)
    assert Settings(_env_file=None).concurrency == 0
    with pytest.raises(ValidationError):
        Settings(concurrency=-1, _env_file=None)


def create(owner, kind, **data):
    r = owner.post("/api/resources/" + kind, json=data)
    assert r.status_code == 200, r.text
    return r.json()


def setup(owner):
    connection = create(
        owner,
        "connections",
        name="Gateway",
        type="gateway",
        base_url="https://gateway.example/v1",
        api_key="not-a-real-key",
    )
    skill = create(owner, "skills", name="Review guide", instructions="Look for actionable issues.")
    agent = create(
        owner,
        "agents",
        name="Reviewer",
        instructions="Review",
        model="test-model",
        connection_id=connection["id"],
        skill_ids=[skill["id"]],
    )
    repo = create(owner, "repos", name="owner/repo", installation_id=42)
    group = create(
        owner, "groups", name="general", members=[{"agent_id": agent["id"], "count": 2}], repos=[repo["name"]]
    )
    create(owner, "access", name="trusted", subject="user", scope="all")
    return connection, skill, agent, repo, group


def payload(actor="trusted", body="@litereview general", comment_id=12345):
    return {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 1, "pull_request": {}},
        "installation": {"id": 42},
        "comment": {"id": comment_id, "user": {"login": actor, "type": "User"}, "body": body},
    }


def mock_github(monkeypatch, permission="write"):
    monkeypatch.setattr(github, "permission", AsyncMock(return_value=permission))

    async def request(repo, installation, method, suffix, **kwargs):
        if suffix.startswith("compare/"):
            return httpx.Response(200, text="diff --git a/app.py b/app.py\n+new code")
        return httpx.Response(
            200,
            json={
                "head": {"sha": "a" * 40},
                "base": {"sha": "b" * 40},
                "title": "Test PR",
                "body": "",
                "state": "open",
                "changed_files": 1,
            },
        )

    monkeypatch.setattr(github, "repo_request", request)


def webhook(owner, data, delivery="delivery-1"):
    raw = json.dumps(data).encode()
    signature = "sha256=" + hmac.new(b"test-webhook-secret", raw, hashlib.sha256).hexdigest()
    return owner.post(
        "/api/webhook/github",
        content=raw,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": "issue_comment",
            "x-github-delivery": delivery,
        },
    )


def test_auth_and_secret_redaction(client):
    assert client.get("/api/resources/agents").status_code == 401
    assert client.post("/api/login", json={"token": "incorrect"}).status_code == 401
    r = client.post("/api/login", json={"token": "test-owner-token-" * 3})
    assert "HttpOnly" in r.headers["set-cookie"]
    conn = create(client, "connections", name="Secret", api_key="this-must-be-encrypted")
    assert "this-must-be-encrypted" not in json.dumps(conn)
    assert "api_key" not in client.get("/api/resources/connections").text
    with Session() as db:
        saved = db.get(Resource, conn["id"])
        assert saved.secret and "this-must-be-encrypted" not in saved.secret
    assert (
        client.post(
            "/api/resources/skills", json={}, headers={"origin": "https://attacker.example"}
        ).status_code
        == 403
    )


def test_parallel_expansion_deduplication_and_snapshots(owner, monkeypatch):
    _, skill, agent, repo, _ = setup(owner)
    create(
        owner,
        "groups",
        name="security",
        members=[{"agent_id": agent["id"], "count": 5}],
        repos=[repo["name"]],
    )
    mock_github(monkeypatch)
    response = webhook(owner, payload(body="@litereview GENERAL general security"))
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert webhook(owner, payload(body="@litereview general security")).json()["run_id"] == run_id
    with Session() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 7
        assert len({j.id for j in jobs}) == 7
        assert {j.config["group"] for j in jobs} == {"general", "security"}
        assert db.get(Run, run_id).head == "a" * 40
    r = owner.put(
        "/api/resources/agents/" + agent["id"], json={**agent, "instructions": "Changed instructions"}
    )
    assert r.status_code == 200
    with Session() as db:
        assert all(j.config["instructions"] == "Review" for j in db.scalars(select(Job)))
        assert all(
            j.config["skills"][0]["instructions"] == "Look for actionable issues."
            for j in db.scalars(select(Job))
        )
    assert owner.delete("/api/resources/agents/" + agent["id"]).status_code == 409
    assert owner.delete("/api/resources/skills/" + skill["id"]).status_code == 409


@pytest.mark.parametrize(
    "body",
    [
        "```\n@litereview general\n```",
        "@other general",
    ],
)
def test_only_explicit_commands(body):
    assert reviews.command(body) == []


def test_unauthorized_webhooks_and_callers(owner, monkeypatch):
    setup(owner)
    assert owner.post("/api/webhook/github", json=payload()).status_code == 401
    mock_github(monkeypatch, "write")
    assert webhook(owner, payload(actor="unlisted")).status_code == 200
    mock_github(monkeypatch)
    assert webhook(owner, payload(body="@litereview unknown", comment_id=12346), "delivery-2").status_code == 200
    assert webhook(owner, {**payload(), "action": "edited"}).json()["ignored"]
    assert webhook(owner, {**payload(), "issue": {"number": 1}}).json()["ignored"]
    with Session() as db:
        assert not db.scalars(select(Job)).all()


def test_worker_permissions_limits_and_idempotent_finish(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    webhook(owner, payload())
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "running"
        job.started_at = time.time()
        job.token_hash = digest("job-token")
        job_id = job.id
        db.commit()
    base = "/api/internal/jobs/" + job_id
    headers = {"Authorization": "Bearer job-token"}
    assert owner.get(base + "/bootstrap").status_code == 401
    bootstrap = owner.get(base + "/bootstrap", headers=headers)
    assert bootstrap.status_code == 200
    assert "not-a-real-key" not in bootstrap.text
    assert (
        owner.post(
            base + "/mcp", headers=headers, json={"connection_id": "anything", "name": "write"}
        ).status_code
        == 403
    )
    result = {"status": "completed", "summary": "Checked repository.", "findings": [], "limitations": []}
    assert owner.post(base + "/finish", headers=headers, json=result).status_code == 200
    assert (
        owner.post(base + "/finish", headers=headers, json={**result, "summary": "Duplicate"}).status_code
        == 200
    )
    assert owner.post(base + "/model", headers=headers, json={}).status_code == 401
    with Session() as db:
        assert db.get(Job, job_id).result["summary"] == "Checked repository."


def test_invalid_review_result_can_be_corrected(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    webhook(owner, payload())
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "running"
        job.started_at = time.time()
        job.token_hash = digest("result-token")
        job_id = job.id
        db.commit()
    url = "/api/internal/jobs/" + job_id + "/finish"
    headers = {"Authorization": "Bearer result-token"}
    result = {"status": "completed", "summary": "Reviewed", "findings": [], "limitations": []}
    invalid = owner.post(url, headers=headers, json={**result, "limitations": "untrusted-invalid-value"})
    assert invalid.status_code == 422
    assert "limitations" in invalid.json()["detail"]
    assert "untrusted-invalid-value" not in invalid.text
    with Session() as db:
        assert db.get(Job, job_id).status == "running"
    assert owner.post(url, headers=headers, json=result).status_code == 200
    with Session() as db:
        assert db.get(Job, job_id).status == "completed"


async def test_attached_supporting_file_paths_are_readable(tmp_path, monkeypatch):
    import base64

    from runner import agent, tools

    monkeypatch.setattr(agent, "SKILLS", tmp_path / "skills")
    monkeypatch.setattr(tools, "SKILLS", tmp_path / "skills")
    monkeypatch.setattr(tools, "REPO", tmp_path / "repo")
    skill = {
        "id": "guide",
        "name": "Guide",
        "instructions": "Read reference/checklist.txt.",
        "files": {"reference/checklist.txt": base64.b64encode(b"Review evidence checklist").decode()},
    }
    agent.stage_skills([skill])
    path = "skills/guide/reference/checklist.txt"
    assert path in agent.skill_context([skill])
    toolset = Toolset({"config": {"capabilities": [], "mcp_definitions": []}}, AsyncMock())
    result = await toolset.execute("read_file", {"path": path})
    assert "Review evidence checklist" in result["content"]


def test_skill_archive_traversal_and_size(owner):
    def upload(names):
        b = io.BytesIO()
        with zipfile.ZipFile(b, "w") as z:
            for n, v in names.items():
                z.writestr(n, v)
        return owner.post(
            "/api/skills/upload", files={"file": ("skill.zip", b.getvalue(), "application/zip")}
        )

    assert upload({"SKILL.md": "hello", "../outside": "oops"}).status_code == 400
    assert upload({"SKILL.md": "hello", "huge": "x" * 2_000_001}).status_code == 200
    valid = upload({"my-skill/SKILL.md": "# Skill", "my-skill/examples/test.txt": "test"})
    assert valid.status_code == 200
    assert valid.json()["instructions"] == "# Skill"
    assert set(valid.json()["files"]) == {"examples/test.txt"}


def test_repository_archive_traversal(tmp_path):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        entry = tarfile.TarInfo("archive/../../escape")
        entry.size = 4
        tar.addfile(entry, io.BytesIO(b"evil"))
    with pytest.raises(ValueError, match="Unsafe"):
        extract_archive(stream.getvalue(), tmp_path / "repo")
    assert not (tmp_path / "escape").exists()


async def test_capabilities_enforced_and_file_paths_scoped():
    tools = Toolset({"config": {"capabilities": [], "mcp_definitions": []}}, AsyncMock())
    with pytest.raises(ValueError, match="not enabled"):
        await tools.execute("terminal", {"command": "echo forbidden"})
    with pytest.raises(ValueError, match="outside"):
        tools.path("../../etc/passwd")


def test_no_findings_never_used_for_incomplete_reviews():
    run = Run(id="x", repo="owner/repo", head="a" * 40, pr=1)
    cfg = {"name": "Agent", "group": "general", "instance": 1}
    job = Job(
        config=cfg,
        status="failed",
        result={"findings": [], "summary": "Failed", "limitations": ["Model failed"]},
    )
    text = reviews.render_comment(run, [job])
    assert "**No issues found.**" not in text
    assert "**Review incomplete.**" in text
    job.status = "completed"
    job.result = {"findings": [], "summary": "Checked", "limitations": []}
    assert "Checked" in reviews.render_comment(run, [job])
    job.result["summary"] = ""
    assert "**No issues found.**" in reviews.render_comment(run, [job])


def test_duplicate_comment_publication_recovers_existing_comment(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    run_id = webhook(owner, payload()).json()["run_id"]
    with Session() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == run_id)).all()
        for job in jobs:
            job.status = "completed"
        db.commit()
    calls = []

    async def request(repo, installation, method, suffix, **kwargs):
        calls.append(method)
        if suffix.startswith("pulls/"):
            return httpx.Response(200, json={"head": {"sha": "a" * 40}})
        if method == "GET":
            return httpx.Response(200, json=[
                {"id": 5879827650 + i, "body": f"<!-- litereview:{run_id}:job:{j.id} -->", "user": {"type": "Bot"}}
                for i, j in enumerate(jobs)
            ])
        return httpx.Response(200, json={"id": 5879827650})

    monkeypatch.setattr(github, "repo_request", request)
    asyncio.run(reviews.publish(run_id))
    assert "POST" not in calls
    assert calls.count("PATCH") == 4


def test_model_connection_snapshot_is_immutable(owner, monkeypatch):
    conn, _, _, _, _ = setup(owner)
    mock_github(monkeypatch)
    webhook(owner, payload())
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "running"
        job.started_at = time.time()
        job.token_hash = digest("snapshot-token")
        job_id = job.id
        db.commit()
    assert (
        owner.put(
            "/api/resources/connections/" + conn["id"],
            json={**conn, "type": "openai", "base_url": "https://changed.example/v1"},
        ).status_code
        == 200
    )
    complete = AsyncMock(return_value={"role": "assistant", "content": "ok"})
    monkeypatch.setattr(providers, "complete", complete)
    result = owner.post(
        "/api/internal/jobs/" + job_id + "/model",
        headers={"Authorization": "Bearer snapshot-token"},
        json={"messages": [{"role": "user", "content": "review"}], "tools": []},
    )
    assert result.status_code == 200
    used = complete.call_args.args[0]
    assert used["type"] == "gateway"
    assert used["base_url"] == "https://gateway.example/v1"
    assert used["api_key"] == "not-a-real-key"


def test_job_model_budget_and_screenshot_auth(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    run_id = webhook(owner, payload()).json()["run_id"]
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "running"
        job.started_at = time.time()
        job.model_calls = 60
        job.config = {**job.config, "max_rounds": 60}
        job.token_hash = digest("limited-token")
        job_id = job.id
        db.commit()
    headers = {"Authorization": "Bearer limited-token"}
    assert owner.post("/api/internal/jobs/" + job_id + "/model", headers=headers, json={}).status_code == 429
    assert (
        owner.post(
            "/api/internal/jobs/" + job_id + "/artifact", headers=headers, json={"data": "not-valid-base64"}
        ).status_code
        == 400
    )
    owner.post("/api/logout")
    assert owner.get("/api/runs/" + run_id).status_code == 401
    assert owner.get("/api/artifacts/anything").status_code == 401


def test_expired_job_cannot_use_relay(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    webhook(owner, payload())
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "running"
        job.started_at = time.time() - 1000
        job.config = {**job.config, "timeout_seconds": 600}
        job.token_hash = digest("expired-token")
        job_id = job.id
        db.commit()
    assert (
        owner.get(
            "/api/internal/jobs/" + job_id + "/bootstrap", headers={"Authorization": "Bearer expired-token"}
        ).status_code
        == 401
    )


def test_rejected_command_is_visible_without_starting_agents(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    result = webhook(owner, payload(body="@litereview unknown"))
    assert result.status_code == 200
    with Session() as db:
        run = db.get(Run, result.json()["run_id"])
        assert run.status == "rejected"
        assert not db.scalars(select(Job)).all()
        text = reviews.render_comment(run, [])
        assert "Review not started" in text
        assert "unavailable" in text


def test_large_reviews_keep_every_agent_section():
    run = Run(id="large", repo="owner/repo", head="a" * 40, pr=1)
    jobs = []
    for i in range(30):
        jobs.append(
            Job(
                config={"name": f"Agent {i}", "group": "general", "instance": i + 1},
                status="completed",
                result={
                    "summary": "s" * 5000,
                    "findings": [{"title": "t" * 200, "severity": "high", "explanation": "e" * 5000}] * 30,
                    "limitations": [],
                },
            )
        )
    text = reviews.render_comment(run, jobs)
    assert text.count("e" * 5000) == 30 * 30
    assert all(len(part) <= reviews.GITHUB_COMMENT_CHARACTERS for part in reviews.comment_parts(run, text))
    for i in range(30):
        assert f"### Agent {i} · general\n" in text
    assert text.count("<details>") == text.count("</details>")


def test_private_app_manifest_targets_repository_owner(owner, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings(), "public_url", "https://review.example")
    response = owner.post("/api/github/manifest", json={"owner_type": "organization", "owner": "BerriAI"})
    assert response.status_code == 200
    data = response.json()
    target = urlparse(data["url"])
    assert target.scheme == "https" and target.netloc == "github.com"
    assert target.path == "/organizations/BerriAI/settings/apps/new"
    assert parse_qs(target.query)["state"] == [response.cookies["lr_manifest"]]
    manifest = json.loads(data["manifest"])
    assert manifest["redirect_url"] == "https://review.example/api/github/manifest/callback"
    assert manifest["public"] is False
    assert manifest["default_permissions"]["contents"] == "read"
    assert manifest["default_permissions"]["issues"] == "write"
    assert manifest["hook_attributes"]["url"] == "https://review.example/api/webhook/github"
    assert (
        owner.post(
            "/api/github/manifest", json={"owner_type": "organization", "owner": "../invalid"}
        ).status_code
        == 400
    )


def test_manifest_callback_requires_same_browser_and_clears_state(owner, monkeypatch):
    from app.settings import settings

    monkeypatch.setattr(settings(), "public_url", "https://review.example")
    owner.base_url = "https://testserver"
    response = owner.post("/api/github/manifest", json={"owner_type": "personal"})
    state = parse_qs(urlparse(response.json()["url"]).query)["state"][0]
    exchange = AsyncMock(
        return_value=httpx.Response(
            201,
            json={
                "id": 123,
                "pem": "private-key",
                "webhook_secret": "webhook-secret",
                "client_id": "client-id",
                "client_secret": "client-secret",
                "html_url": "https://github.com/apps/test-litereview",
            },
        )
    )
    monkeypatch.setattr(httpx.AsyncClient, "post", exchange)
    owner.cookies.delete("lr_manifest")
    params = {"code": "temporary-code", "state": state}
    assert (
        owner.get("/api/github/manifest/callback", params=params, follow_redirects=False).headers["location"]
        == "/repositories?github_error=expired"
    )
    exchange.assert_not_awaited()
    owner.cookies.set("lr_manifest", state, domain="testserver.local", path="/")
    response = owner.get("/api/github/manifest/callback", params=params, follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://github.com/apps/test-litereview/installations/new"
    assert "lr_manifest" not in owner.cookies
    assert github.config()["app_id"] == "123"
    assert (
        owner.get("/api/github/manifest/callback", params=params, follow_redirects=False).headers["location"]
        == "/repositories?github_error=expired"
    )
    exchange.assert_awaited_once()


def test_render_url_is_detected(monkeypatch):
    from app.settings import Settings

    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://litereview.onrender.com")
    assert Settings(public_url="", _env_file=None).public_url == "https://litereview.onrender.com"


async def test_finished_sandbox_is_always_cleaned_up(owner, monkeypatch):
    from types import SimpleNamespace

    from app import runtime
    from app.settings import settings

    setup(owner)
    mock_github(monkeypatch)
    webhook(owner, payload())
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "starting"
        job.started_at = time.time()
        job_id = job.id
        db.commit()

    async def worker_finishes():
        with Session() as db:
            job = db.get(Job, job_id)
            job.status = "completed"
            job.result = {"summary": "Completed", "findings": [], "limitations": []}
            db.commit()

    sandbox = SimpleNamespace(object_id="sb-test", wait=SimpleNamespace(aio=worker_finishes))
    monkeypatch.setattr(settings(), "public_url", "https://review.example")
    monkeypatch.setattr(providers, "probe", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(runtime, "create_sandbox", lambda *_: sandbox)
    terminate = AsyncMock()
    monkeypatch.setattr(runtime, "terminate", terminate)
    await runtime.launch(job_id, "test-token")
    terminate.assert_awaited_once_with("sb-test")
    with Session() as db:
        assert db.get(Job, job_id).status == "completed"


async def test_modal_hard_timeout_is_incomplete_not_start_failure(owner, monkeypatch):
    from types import SimpleNamespace

    import modal

    from app import runtime
    from app.settings import settings

    setup(owner)
    mock_github(monkeypatch)
    webhook(owner, payload())
    with Session() as db:
        job = db.scalar(select(Job))
        job.status = "starting"
        job.started_at = time.time()
        job_id = job.id
        db.commit()
    sandbox = SimpleNamespace(
        object_id="sb-timeout",
        wait=SimpleNamespace(aio=AsyncMock(side_effect=modal.exception.SandboxTimeoutError())),
    )
    monkeypatch.setattr(settings(), "public_url", "https://review.example")
    monkeypatch.setattr(providers, "probe", AsyncMock(return_value={"ok": True}))
    monkeypatch.setattr(runtime, "create_sandbox", lambda *_: sandbox)
    terminate = AsyncMock()
    monkeypatch.setattr(runtime, "terminate", terminate)
    await runtime.launch(job_id, "test-token")
    terminate.assert_awaited_once_with("sb-timeout")
    with Session() as db:
        result = db.get(Job, job_id)
        assert result.status == "timed_out"
        assert result.result["limitations"] == [
            "Modal's 24-hour sandbox lifetime was reached. No agent time limit was configured."
        ]
