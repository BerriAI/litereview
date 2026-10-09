import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException, Request
from sqlalchemy import select
from test_reviews import mock_github, payload, setup, webhook

from app import github, reviews
from app.db import Job, Resource, Run, Session
from app.main import reset_github
from app.security import encrypt, signed
from app.settings import settings

APP = {
    "app_id": "123",
    "private_key": "fixture-key",
    "webhook_secret": "test-webhook-secret",
    "client_id": "fixture-client",
    "client_secret": "fixture-secret",
    "html_url": "https://github.com/apps/fixture-app",
}


def save_app():
    with Session() as db:
        row = Resource(id="github", kind="system", data={}, secret=encrypt(json.dumps(APP)))
        db.add(row)
        db.commit()
        return row.secret


def reset_during_request():
    # Model an administrator resetting setup during an outstanding GitHub request.
    return reset_github(Request({"type": "http", "path": "/api/github/reset", "headers": []}))


def manifest_state(owner):
    state = signed({"purpose": "manifest", "generation": github.setup_state().get("generation", "")})
    owner.cookies.set("lr_manifest", state, domain="testserver.local", path="/")
    return state


def test_reset_requires_admin(client):
    for path in ("/api/github/reset", "/api/github/reset/undo"):
        assert client.post(path).status_code == 401
    client.cookies.set("lr_session", signed({"login": "outsider"}))
    for path in ("/api/github/reset", "/api/github/reset/undo"):
        assert client.post(path).status_code == 403


def test_reset_preserves_workspace_and_undo_restores_connection(owner, monkeypatch):
    secret = save_app()
    setup(owner)
    mock_github(monkeypatch)
    with Session() as db:
        before = {r.id: (r.kind, r.data, r.secret, r.updated_at) for r in db.scalars(select(Resource))}
    old_state = manifest_state(owner)
    owner.cookies.set("lr_oauth", "old-oauth", domain="testserver.local", path="/")
    assert owner.get("/api/session").json()["github_login"] is True
    assert owner.post("/api/github/reset").json() == {"ok": True, "github_reset": True}
    assert "lr_manifest" not in owner.cookies and "lr_oauth" not in owner.cookies
    assert github.config() == {}
    assert owner.get("/api/setup").json()["github_reset"] is True
    assert owner.get("/api/github/status").json()["state"] == "unconfigured"
    session = owner.get("/api/session").json()
    assert session["user"]["admin"] is True and session["github_login"] is False
    assert owner.get("/api/auth/github").status_code == 400
    assert webhook(owner, payload()).status_code == 401
    with Session() as db:
        assert db.get(Resource, "github").secret == secret
        after = {r.id: (r.kind, r.data, r.secret, r.updated_at) for r in db.scalars(select(Resource))}
    assert {k: v for k, v in before.items() if k != "github"} == {
        k: v for k, v in after.items() if k != "github"
    }
    generation = github.setup_state()["generation"]
    owner.post("/api/github/reset").raise_for_status()
    assert github.setup_state()["generation"] == generation
    owner.post("/api/github/reset/undo").raise_for_status()
    assert github.config() == APP
    assert owner.get("/api/setup").json()["github_reset"] is False
    assert owner.get("/api/session").json()["github_login"] is True
    restored_generation = github.setup_state()["generation"]
    assert restored_generation != generation
    owner.post("/api/github/reset/undo").raise_for_status()
    assert github.setup_state()["generation"] == restored_generation
    # Even a stale tab retaining its old cookie cannot overwrite restored setup.
    owner.cookies.set("lr_manifest", old_state, domain="testserver.local", path="/")
    response = owner.get(
        "/api/github/manifest/callback", params={"code": "old", "state": old_state}, follow_redirects=False
    )
    assert response.headers["location"] == "/connections?github_error=expired"
    assert github.config() == APP
    assert webhook(owner, payload()).status_code == 200


async def test_reset_disables_environment_credentials_and_development_token(owner, monkeypatch):
    for key, value in APP.items():
        if key != "html_url":
            monkeypatch.setattr(settings(), "github_" + key, value)
    monkeypatch.setattr(settings(), "github_token", "development-token")
    owner.post("/api/github/reset").raise_for_status()
    assert github.config() == {}
    assert owner.get("/api/github/status").json()["state"] == "unconfigured"
    for call in (github.token(0), github.installed_repositories()):
        with pytest.raises(HTTPException) as exc:
            await call
        assert exc.value.status_code == 409
    owner.post("/api/github/reset/undo").raise_for_status()
    assert github.config()["app_id"] == APP["app_id"]
    assert await github.token(0) == "development-token"


@pytest.mark.parametrize("harness_check", [False, True])
@pytest.mark.parametrize("status", ["queued", "starting", "running"])
def test_reset_waits_for_pr_reviews_but_allows_harness_checks(owner, status, harness_check):
    save_app()
    with Session() as db:
        db.add(
            Run(
                id="run",
                delivery="test",
                repo="owner/repo",
                pr=1,
                actor="trusted",
                head="a",
                base="b",
                snapshot={"harness_check": True} if harness_check else {},
            )
        )
        db.add(Job(id="job", run_id="run", config={}, status=status))
        db.commit()
    response = owner.post("/api/github/reset")
    assert response.status_code == (200 if harness_check else 409)
    assert bool(github.setup_state().get("disabled")) == harness_check
    with Session() as db:
        assert db.get(Job, "job").status == status


def test_reset_preserves_completed_review_history(owner):
    save_app()
    with Session() as db:
        db.add(
            Run(
                id="run",
                delivery="test",
                repo="owner/repo",
                pr=1,
                actor="trusted",
                head="a",
                base="b",
                snapshot={"diff": "saved"},
                status="completed",
            )
        )
        db.add(Job(id="job", run_id="run", config={}, status="completed", result={"summary": "saved"}))
        db.commit()
    owner.post("/api/github/reset").raise_for_status()
    with Session() as db:
        assert db.get(Run, "run").snapshot == {"diff": "saved"}
        assert db.get(Job, "job").result == {"summary": "saved"}


@pytest.mark.parametrize("during_conversion", [False, True])
def test_reset_invalidates_pending_manifest_even_if_browser_cookie_survives(
    owner, monkeypatch, during_conversion
):
    save_app()
    state = manifest_state(owner)

    async def convert(*args, **kwargs):
        reset_during_request()
        return httpx.Response(201, json={"id": "new", "pem": "new-key", **APP})

    conversion = AsyncMock(side_effect=convert)
    monkeypatch.setattr(httpx.AsyncClient, "post", conversion)
    if not during_conversion:
        reset_during_request()
    response = owner.get(
        "/api/github/manifest/callback", params={"code": "old", "state": state}, follow_redirects=False
    )
    assert response.headers["location"] == "/connections?github_error=expired"
    assert github.config() == {}
    assert conversion.await_count == int(during_conversion)


@pytest.mark.parametrize("method", ["manual", "manifest"])
def test_new_setup_replaces_reset_backup_and_overrides_old_environment(owner, monkeypatch, method):
    save_app()
    monkeypatch.setattr(settings(), "github_app_id", "environment-app")
    owner.post("/api/github/reset").raise_for_status()
    if method == "manual":
        owner.post("/api/github/config", json={**APP, "app_id": "new"}).raise_for_status()
    else:
        state = manifest_state(owner)
        monkeypatch.setattr(
            httpx.AsyncClient,
            "post",
            AsyncMock(return_value=httpx.Response(201, json={"id": "new", "pem": APP["private_key"], **APP})),
        )
        response = owner.get(
            "/api/github/manifest/callback", params={"code": "new", "state": state}, follow_redirects=False
        )
        assert response.headers["location"] == APP["html_url"] + "/installations/new"
    assert github.config()["app_id"] == "new"
    assert owner.get("/api/setup").json()["github_reset"] is False
    owner.post("/api/github/reset/undo").raise_for_status()
    assert github.config()["app_id"] == "new"


def test_incomplete_new_credentials_keep_undo_available(owner):
    secret = save_app()
    owner.post("/api/github/reset").raise_for_status()
    assert owner.post("/api/github/config", json={"app_id": "new"}).status_code == 400
    assert github.setup_state()["disabled"] is True
    with Session() as db:
        assert db.get(Resource, "github").secret == secret


def test_inflight_repository_discovery_cannot_connect_after_reset(owner, monkeypatch):
    save_app()

    async def discover():
        reset_during_request()
        return [{"name": "owner/repo", "installation_id": 42}]

    monkeypatch.setattr(github, "installed_repositories", discover)
    assert owner.post("/api/github/connect", json={"name": "owner/repo"}).status_code == 409
    assert owner.get("/api/resources/repos").json() == []


async def test_inflight_review_cannot_queue_after_reset(owner, monkeypatch):
    save_app()
    setup(owner)
    mock_github(monkeypatch)
    request = github.repo_request

    async def reset_while_loading_diff(repo, installation, method, suffix, **kwargs):
        if suffix.startswith("compare/"):
            reset_during_request()
        return await request(repo, installation, method, suffix, **kwargs)

    monkeypatch.setattr(github, "repo_request", reset_while_loading_diff)
    assert await reviews.accept(payload(), "inflight") is None
    assert await reviews.accept(payload(), "after-reset") is None
    with Session() as db:
        assert db.scalars(select(Job)).all() == []
        assert db.scalars(select(Run)).all() == []
