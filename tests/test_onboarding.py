import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from test_sso import enable, identity

from app import github, sso
from app.db import Job, LoginState, Resource, Run, Session
from app.settings import settings
from runner.catalog import HARNESS_VERSIONS

REPO = {"name": "BerriAI/litereview", "installation_id": 42}


def discovery(monkeypatch, repos=None):
    result = {
        "state": "available",
        "owner": "BerriAI",
        "owner_type": "Organization",
        "repositories": repos if repos is not None else [REPO.copy()],
        "installations": [],
    }
    monkeypatch.setattr(github, "connection_status", AsyncMock(return_value=result))
    monkeypatch.setattr(github, "installed_repositories", AsyncMock(return_value=result["repositories"]))
    return result


def test_sync_imports_once_and_preserves_environment_secrets_and_revoked_grant(owner, monkeypatch):
    result = discovery(monkeypatch)
    assert owner.post("/api/github/sync").json()["added"] == 1
    repo = owner.get("/api/resources/repos").json()[0]
    owner.put(
        "/api/resources/repos/" + repo["id"],
        json={**repo, "setup_command": "npm ci", "test_secrets": {"APP_KEY": "keep"}},
    ).raise_for_status()
    grant = next(g for g in owner.get("/api/resources/access").json() if g["subject"] == "repo")
    owner.delete("/api/resources/access/" + grant["id"]).raise_for_status()
    result["repositories"][0]["installation_id"] = 43
    assert owner.post("/api/github/sync").json()["added"] == 0
    repos = owner.get("/api/resources/repos").json()
    assert len(repos) == 1
    assert repos[0]["setup_command"] == "npm ci" and repos[0]["secret_names"] == ["APP_KEY"]
    assert repos[0]["installation_id"] == 43
    assert not any(g["subject"] == "repo" for g in owner.get("/api/resources/access").json())


def test_deleted_repo_is_not_silently_restored_but_explicit_restore_works(owner, monkeypatch):
    discovery(monkeypatch)
    owner.post("/api/github/sync").raise_for_status()
    repo = owner.get("/api/resources/repos").json()[0]
    owner.delete("/api/resources/repos/" + repo["id"]).raise_for_status()
    response = owner.post("/api/github/sync").json()
    assert response["added"] == 0 and response["excluded"] == ["berriai/litereview"]
    assert owner.get("/api/resources/repos").json() == []
    owner.post("/api/github/connect", json={"name": "BerriAI/litereview"}).raise_for_status()
    assert owner.post("/api/github/sync").json()["excluded"] == []


def test_failed_sync_and_installation_removal_preserve_settings(owner, monkeypatch):
    status = discovery(monkeypatch)
    owner.post("/api/github/sync").raise_for_status()
    before = owner.get("/api/resources/repos").json()
    status["repositories"] = []
    assert owner.post("/api/github/sync").json()["repositories"] == []
    assert owner.get("/api/resources/repos").json() == before
    monkeypatch.setattr(github, "connection_status", AsyncMock(side_effect=httpx.ConnectError("offline")))
    assert owner.post("/api/github/sync").status_code == 502
    assert owner.get("/api/resources/repos").json() == before


def test_sync_cannot_commit_after_reset(owner, monkeypatch):
    async def interrupted():
        with Session() as db:
            db.add(Resource(id="github", kind="system", data={"generation": "new", "disabled": True}))
            db.commit()
        return {"state": "available", "repositories": [REPO], "installations": []}

    monkeypatch.setattr(github, "connection_status", interrupted)
    assert owner.post("/api/github/sync").status_code == 409
    assert owner.get("/api/resources/repos").json() == []


def test_members_cannot_sync_or_read_admin_readiness(client, monkeypatch):
    assert client.get("/api/start").status_code == 401
    assert client.post("/api/github/sync").status_code == 401
    enable(monkeypatch)
    identity(client)
    assert client.post("/api/github/sync").status_code == 403
    assert client.get("/api/workspace/readiness").status_code == 403


def ready_workspace(owner, monkeypatch):
    discovery(monkeypatch)
    owner.post("/api/github/sync").raise_for_status()
    conn = owner.post(
        "/api/resources/connections",
        json={
            "name": "Private connection",
            "type": "gateway",
            "base_url": "https://secret.example/v1",
            "api_key": "model-secret",
        },
    ).json()
    agent = owner.post(
        "/api/resources/agents",
        json={
            "name": "reviewer",
            "harness": "codex",
            "connection_id": conn["id"],
            "model": "example",
            "instructions": "Secret instructions",
        },
    ).json()
    owner.post(
        "/api/resources/access",
        json={"subject": "user", "name": "trusted", "scope": "selected", "target_ids": [agent["id"]]},
    ).raise_for_status()
    with Session() as db:
        run = Run(
            id="check-run",
            delivery="check",
            repo="Harness check",
            pr=0,
            actor="workspace",
            head="",
            base="",
            snapshot={"harness_check": True},
        )
        db.add(run)
        db.add(
            Job(
                id="check-job",
                run_id=run.id,
                status="completed",
                config={**agent, "harness_version": HARNESS_VERSIONS["codex"]},
                result={"limitations": []},
            )
        )
        db.commit()
    for key, val in {
        "modal_token_id": "id",
        "modal_token_secret": "secret",
        "public_url": "https://testserver",
        "dispatcher_enabled": True,
    }.items():
        monkeypatch.setattr(settings(), key, val)
    monkeypatch.setattr(github, "config", lambda: {"client_id": "app-client"})
    monkeypatch.setattr(github, "permission", AsyncMock(return_value="read"))
    return agent, conn


def test_member_response_is_minimal_and_applies_both_grants_and_github_access(owner, monkeypatch):
    agent, conn = ready_workspace(owner, monkeypatch)
    enable(monkeypatch)
    identity(owner)
    response = owner.get("/api/start").json()
    assert response["state"] == "ready"
    assert response["repositories"] == [{"name": "BerriAI/litereview", "reviewers": ["reviewer"]}]
    github.permission.assert_awaited_once_with("BerriAI/litereview", 42, "trusted", expected_id="42")
    assert all(
        secret not in json.dumps(response)
        for secret in ["model-secret", "Secret instructions", "secret.example", conn["id"], agent["id"]]
    )
    with Session() as db:
        grant = next(
            g
            for g in db.scalars(select(Resource).where(Resource.kind == "access"))
            if g.data["subject"] == "repo"
        )
        grant.data = {**grant.data, "scope": "selected", "target_ids": []}
        db.commit()
    assert owner.get("/api/start").json()["repositories"] == []


@pytest.mark.parametrize("cause", ["permission", "installation", "stale-check", "failed-check"])
def test_readiness_does_not_claim_unavailable_or_unchecked_reviewer(owner, monkeypatch, cause):
    agent, conn = ready_workspace(owner, monkeypatch)
    if cause == "permission":
        github.permission.return_value = "none"
    elif cause == "installation":
        github.installed_repositories.return_value = []
    else:
        with Session() as db:
            if cause == "stale-check":
                db.get(Resource, conn["id"]).updated_at = time.time() + 1
            else:
                db.get(Job, "check-job").status = "failed"
            db.commit()
    enable(monkeypatch)
    identity(owner)
    response = owner.get("/api/start").json()
    assert response["state"] != "ready" and response["repositories"] == []


async def test_recycled_github_name_does_not_expose_repository_access(monkeypatch):
    monkeypatch.setattr(
        github,
        "repo_request",
        AsyncMock(return_value=httpx.Response(200, json={"permission": "admin", "user": {"id": 43}})),
    )
    assert await github.permission("org/private", 42, "renamed", expected_id="42") == "none"


@pytest.mark.parametrize(
    "destination,expected",
    [
        ("/repositories", "/repositories"),
        ("/settings?from=app", "/settings"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("/runs/" + "a" * 32, "/runs/" + "a" * 32),
    ],
)
def test_google_return_destination_is_bound_and_local(client, monkeypatch, destination, expected):
    enable(monkeypatch)
    response = client.get("/api/auth/google", params={"next": destination}, follow_redirects=False)
    assert response.status_code == 303
    with Session() as db:
        state = db.scalars(select(LoginState)).one()
        assert state.data["next"] == expected
    assert sso.return_path(destination) == expected
