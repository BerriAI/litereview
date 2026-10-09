from unittest.mock import AsyncMock

import pytest
from test_sso import enable, identity

from app import github, providers
from app.db import Artifact, Job, Run, Session, SSOIdentity
from app.settings import settings


@pytest.mark.parametrize("linked", [False, True])
def test_google_members_manage_shared_workspace_without_admin_allowlist(client, monkeypatch, linked):
    enable(monkeypatch)
    monkeypatch.setattr(settings(), "admin_users", "someone-else")
    identity(client, **({} if linked else {"github_id": None, "github_login": ""}))

    def create(kind, **data):
        response = client.post("/api/resources/" + kind, json=data)
        response.raise_for_status()
        return response.json()

    connection = create("connections", name="Shared model", type="openai", api_key="private-model-key")
    skill = create("skills", name="Review", instructions="Review changed code.")
    agent = create(
        "agents",
        name="member-reviewer",
        harness="codex",
        connection_id=connection["id"],
        model="test-model",
        instructions="Review changed code.",
        skill_ids=[skill["id"]],
    )
    group = create("groups", name="team", members=[{"agent_id": agent["id"], "count": 1}])
    repo = create("repos", name="org/repo", installation_id=42, test_secrets={"TEST_KEY": "private-test-key"})
    grant = create("access", subject="user", name="teammate", scope="selected", target_ids=[agent["id"]])
    updated = client.put(
        "/api/resources/agents/" + agent["id"], json={**agent, "instructions": "Updated review."}
    )
    assert updated.json()["instructions"] == "Updated review."
    for kind in ["agents", "groups", "skills", "connections", "repos", "access"]:
        response = client.get("/api/resources/" + kind)
        assert response.status_code == 200
        assert "private-model-key" not in response.text and "private-test-key" not in response.text

    monkeypatch.setattr(providers, "probe", AsyncMock(return_value={"ok": True}))
    assert client.post("/api/agents/test", json=agent).json()["ok"]
    assert client.post(f"/api/agents/{agent['id']}/test").json()["ok"]
    assert client.get("/api/harnesses").status_code == 200
    assert client.get("/api/setup").status_code == 200
    monkeypatch.setattr(
        github,
        "installed_repositories",
        AsyncMock(return_value=[{"name": "org/repo", "installation_id": 42}]),
    )
    assert client.get("/api/github/repositories").json()[0]["name"] == "org/repo"

    with Session() as db:
        db.add(
            Run(
                id="member-run",
                delivery="member-test",
                repo="org/repo",
                pr=1,
                actor="trusted",
                head="a" * 40,
                base="b" * 40,
                snapshot={},
            )
        )
        db.add(Job(id="member-job", run_id="member-run", config={}, status="completed"))
        db.add(Artifact(id="member-artifact", job_id="member-job", name="Screenshot", content=b"test-image"))
        db.commit()
    assert client.get("/api/runs").json()[0]["id"] == "member-run"
    assert client.get("/api/runs/member-run").json()["jobs"][0]["id"] == "member-job"
    assert client.get("/api/artifacts/member-artifact").content == b"test-image"
    for kind, item in [
        ("access", grant),
        ("repos", repo),
        ("groups", group),
        ("agents", agent),
        ("skills", skill),
        ("connections", connection),
    ]:
        assert client.delete(f"/api/resources/{kind}/{item['id']}").status_code == 200


@pytest.mark.parametrize("state", ["anonymous", "expired", "outside", "wrong-audience", "unlinked"])
def test_workspace_endpoints_still_require_eligible_google_session(client, monkeypatch, state):
    enable(monkeypatch)
    if state != "anonymous":
        identity(client, github_id=None, github_login="")
        with Session() as db:
            person = db.get(SSOIdentity, "google-sub")
            if state == "expired":
                person.expires_at = 0
            elif state == "outside":
                person.domain, person.email = "outside.test", "person@outside.test"
            elif state == "wrong-audience":
                person.audience = "another-client"
            db.commit()
    if state == "unlinked":
        assert client.get("/api/resources/agents").status_code == 200
        # Workspace configuration is available before GitHub linking.
        assert (
            client.post(
                "/api/resources/skills", json={"name": "Review", "instructions": "Review."}
            ).status_code
            == 200
        )
        return
    for method, path in [
        ("GET", "/api/resources/agents"),
        ("POST", "/api/resources/skills"),
        ("PUT", "/api/resources/agents/example"),
        ("DELETE", "/api/resources/repos/example"),
        ("GET", "/api/runs"),
        ("GET", "/api/runs/example"),
        ("GET", "/api/artifacts/example"),
        ("GET", "/api/setup"),
        ("GET", "/api/harnesses"),
        ("GET", "/api/workspace/readiness"),
        ("POST", "/api/github/sync"),
        ("POST", "/api/github/reset"),
        ("POST", "/api/github/manifest"),
        ("POST", "/api/connections/example/test"),
        ("POST", "/api/connections/example/oauth/start"),
        ("POST", "/api/connections/example/oauth/disconnect"),
        ("GET", "/api/mcp/oauth/callback"),
        ("POST", "/api/agents/check"),
        ("GET", "/api/agents/check/example"),
    ]:
        assert client.request(method, path, json={}).status_code == 401, (method, path)
