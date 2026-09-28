import hashlib
import hmac
import io
import json
import tarfile
import time
import zipfile
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app import github, reviews
from app.db import Job, Resource, Run, Session
from app.security import digest
from runner.agent import extract_archive
from runner.tools import Toolset


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
    return connection, skill, agent, repo, group


def payload(actor="trusted", body="@litereview general"):
    return {
        "action": "created",
        "repository": {"full_name": "owner/repo"},
        "issue": {"number": 1, "pull_request": {}},
        "installation": {"id": 42},
        "comment": {"user": {"login": actor, "type": "User"}, "body": body},
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
        "hello @litereview general",
        "```\n@litereview general\n```",
        "@other general",
        "@litereview",
        "@litereview general; echo unsafe",
    ],
)
def test_only_explicit_commands(body):
    assert reviews.command(body) == []


def test_unauthorized_webhooks_and_callers(owner, monkeypatch):
    setup(owner)
    assert owner.post("/api/webhook/github", json=payload()).status_code == 401
    mock_github(monkeypatch, "read")
    assert webhook(owner, payload()).status_code == 403
    mock_github(monkeypatch)
    assert webhook(owner, payload(body="@litereview general unknown")).status_code == 403
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
    assert upload({"SKILL.md": "hello", "huge": "x" * 2_000_001}).status_code == 400
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
    assert "**No findings.**" not in text
    assert "**Incomplete review**" in text
    job.status = "completed"
    job.result = {"findings": [], "summary": "Checked", "limitations": []}
    assert "**No findings.**" in reviews.render_comment(run, [job])


def test_duplicate_comment_publication_recovers_existing_comment(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    run_id = webhook(owner, payload()).json()["run_id"]
    calls = []

    async def request(repo, installation, method, suffix, **kwargs):
        calls.append(method)
        if method == "GET":
            return httpx.Response(
                200, json=[{"id": 123, "body": f"<!-- litereview:{run_id} -->", "user": {"type": "Bot"}}]
            )
        return httpx.Response(200, json={"id": 123})

    monkeypatch.setattr(github, "repo_request", request)
    import asyncio

    asyncio.run(reviews.publish(run_id))
    assert "POST" not in calls
    assert calls == ["GET", "PATCH"]
