import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException

from app import github
from app.db import Resource, Session
from app.security import encrypt, signed


def configured(monkeypatch):
    monkeypatch.setattr(github, "config", lambda: {"app_id": "123", "private_key": "test"})


def test_github_setup_requires_admin(client):
    assert client.get("/api/github/status").status_code == 401
    assert client.post("/api/github/connect", json={"name": "BerriAI/litereview"}).status_code == 401


def test_unconfigured_setup_does_not_call_github(owner, monkeypatch):
    monkeypatch.setattr(github, "config", lambda: {})
    request = AsyncMock()
    monkeypatch.setattr(github, "request", request)
    assert owner.get("/api/github/status").json()["state"] == "unconfigured"
    request.assert_not_called()


def test_saved_app_without_installation_is_incomplete(owner, monkeypatch):
    configured(monkeypatch)
    request = AsyncMock(
        side_effect=[
            httpx.Response(
                200,
                json={
                    "name": "Review",
                    "html_url": "https://github.com/apps/review",
                    "owner": {"login": "different-owner"},
                },
            ),
            httpx.Response(200, json=[]),
        ]
    )
    monkeypatch.setattr(github, "request", request)
    result = owner.get("/api/github/status").json()
    assert result["state"] == "needs_installation"
    assert result["owner"] == "different-owner"
    assert result["repositories"] == []
    assert "private_key" not in json.dumps(result)


@pytest.mark.parametrize(
    "failure", [httpx.ConnectError("unreachable"), TimeoutError(), HTTPException(502, "GitHub returned 401")]
)
def test_failed_check_is_not_reported_as_empty_installation(owner, monkeypatch, failure):
    monkeypatch.setattr(github, "connection_status", AsyncMock(side_effect=failure))
    response = owner.get("/api/github/status")
    assert response.status_code == 502
    assert "state" not in response.json()
    assert response.json()["detail"]


async def test_installation_and_repository_pagination_and_suspension(owner, monkeypatch):
    configured(monkeypatch)
    calls = []

    async def request(method, path, token=None):
        calls.append(path)
        if path == "/app/installations?per_page=100&page=1":
            return httpx.Response(200, json=[{"id": i, "suspended_at": "today"} for i in range(100)])
        if path == "/app/installations?per_page=100&page=2":
            return httpx.Response(200, json=[{"id": 200, "suspended_at": None}])
        if path == "/app/installations/200/access_tokens":
            return httpx.Response(200, json={"token": "fixture"})
        if path == "/installation/repositories?per_page=100&page=1":
            return httpx.Response(
                200, json={"repositories": [{"full_name": f"org/repo{i}"} for i in range(100)]}
            )
        assert path == "/installation/repositories?per_page=100&page=2"
        return httpx.Response(200, json={"repositories": [{"full_name": "org/final"}]})

    monkeypatch.setattr(github, "request", request)
    repos = await github.installed_repositories()
    assert len(repos) == 101
    assert repos[-1] == {"name": "org/final", "installation_id": 200}
    assert len(calls) == 5


def test_connect_derives_installation_and_retries_preserve_settings_and_revocation(owner, monkeypatch):
    discover = AsyncMock(return_value=[{"name": "BerriAI/litereview", "installation_id": 42}])
    monkeypatch.setattr(github, "installed_repositories", discover)
    response = owner.post("/api/github/connect", json={"name": "berriai/LiteReview", "installation_id": 999})
    assert response.status_code == 200
    repo = response.json()
    assert repo["name"] == "BerriAI/litereview" and repo["installation_id"] == 42
    grants = owner.get("/api/resources/access").json()
    grant = next(g for g in grants if g["name"] == "berriai/litereview")
    assert grant["scope"] == "all"
    owner.put(
        "/api/resources/repos/" + repo["id"],
        json={**repo, "setup_command": "npm ci", "test_secrets": {"APP_TEST_KEY": "keep-me"}},
    ).raise_for_status()
    owner.delete("/api/resources/access/" + grant["id"]).raise_for_status()
    discover.return_value[0]["installation_id"] = 43
    retry = owner.post("/api/github/connect", json={"name": repo["name"]}).json()
    assert retry["id"] == repo["id"] and retry["installation_id"] == 43
    assert retry["setup_command"] == "npm ci" and retry["secret_names"] == ["APP_TEST_KEY"]
    assert len(owner.get("/api/resources/repos").json()) == 1
    assert not any(g["name"] == grant["name"] for g in owner.get("/api/resources/access").json())


def test_cannot_connect_repository_no_longer_available(owner, monkeypatch):
    monkeypatch.setattr(github, "installed_repositories", AsyncMock(return_value=[]))
    assert owner.post("/api/github/connect", json={"name": "org/missing"}).status_code == 400
    assert owner.get("/api/resources/repos").json() == []


def test_expired_manifest_returns_to_recoverable_setup_without_overwriting_app(owner, monkeypatch):
    with Session() as db:
        db.add(Resource(id="github", kind="system", data={}, secret=encrypt(json.dumps({"app_id": "keep"}))))
        db.commit()
    conversion = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", conversion)
    response = owner.get("/api/github/manifest/callback?code=example&state=expired", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "/repositories?github_error=expired"
    assert github.config()["app_id"] == "keep"
    conversion.assert_not_called()


@pytest.mark.parametrize("failure", [httpx.ConnectError("offline"), None])
def test_failed_manifest_exchange_preserves_saved_app(owner, monkeypatch, failure):
    with Session() as db:
        db.add(Resource(id="github", kind="system", data={}, secret=encrypt(json.dumps({"app_id": "keep"}))))
        db.commit()
    state = signed({"purpose": "manifest"})
    owner.cookies.set("lr_manifest", state, domain="testserver.local", path="/")
    conversion = AsyncMock(side_effect=failure, return_value=httpx.Response(400))
    monkeypatch.setattr(httpx.AsyncClient, "post", conversion)
    response = owner.get(
        "/api/github/manifest/callback", params={"code": "code", "state": state}, follow_redirects=False
    )
    assert response.headers["location"] == "/repositories?github_error=conversion"
    assert "lr_manifest" not in owner.cookies
    assert github.config()["app_id"] == "keep"
    conversion.assert_awaited_once()
