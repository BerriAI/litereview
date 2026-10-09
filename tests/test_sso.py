import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select
from test_reviews import mock_github, payload, setup, webhook

from app import github, sso
from app.db import Job, Run, Session, SSOIdentity
from app.security import signed
from app.settings import settings


def enable(monkeypatch):
    for key, value in {
        "google_sso_required": True,
        "google_client_id": "test-client",
        "google_client_secret": "test-secret",
        "google_allowed_domains": "berri.ai",
        "google_admin_emails": "admin@berri.ai",
    }.items():
        monkeypatch.setattr(settings(), key, value)


def identity(client=None, **overrides):
    data = dict(
        sub="google-sub",
        email="member@berri.ai",
        domain="berri.ai",
        audience="test-client",
        expires_at=time.time() + 1000,
        session_id="test-session",
        github_id="42",
        github_login="trusted",
    )
    data.update(overrides)
    with Session() as db:
        db.add(SSOIdentity(**data))
        db.commit()
    if client:
        client.cookies.set(
            "lr_session", signed({"auth": "google", "sub": data["sub"], "session_id": data["session_id"]})
        )
    return data


@pytest.fixture
def signing(monkeypatch):
    enable(monkeypatch)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    monkeypatch.setattr(
        sso.keys_client, "get_signing_key_from_jwt", lambda _: SimpleNamespace(key=key.public_key())
    )

    def token(**changes):
        claims = dict(
            iss="https://accounts.google.com",
            aud="test-client",
            sub="google-sub",
            iat=time.time(),
            exp=time.time() + 3600,
            email="member@berri.ai",
            email_verified=True,
            hd="berri.ai",
            nonce="nonce",
        )
        claims.update(changes)
        return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-key"})

    return token


@pytest.mark.parametrize(
    "changes",
    [
        {"aud": "other-client"},
        {"iss": "https://attacker.test"},
        {"exp": time.time() - 100},
        {"nonce": "other"},
        {"email_verified": False},
        {"email_verified": "true"},
        {"hd": "outside.test"},
        {"hd": "berri.ai.evil.test"},
        {"email": "member@outside.test"},
        {"azp": "other-client"},
        {"aud": ["test-client", "other-client"]},
        {"sub": ""},
    ],
)
async def test_google_verification_rejects_wrong_claims(signing, changes):
    with pytest.raises((ValueError, jwt.PyJWTError)):
        await sso.verify_google(signing(**changes), "nonce")


async def test_google_verification_checks_signature_and_accepts_workspace(signing):
    assert (await sso.verify_google(signing(), "nonce"))["email"] == "member@berri.ai"
    parts = signing().split(".")
    parts[2] = "A" * len(parts[2])
    with pytest.raises(jwt.PyJWTError):
        await sso.verify_google(".".join(parts), "nonce")
    with pytest.raises(ValueError):
        await sso.verify_google(jwt.encode({"sub": "forged"}, "test-hmac-secret-" * 3, algorithm="HS256"), "nonce")


def test_oauth_browser_binding_pkce_and_single_use(client, signing, monkeypatch):
    response = client.get("/api/auth/google", follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["hd"] == ["berri.ai"] and query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == ["http://testserver/api/auth/google/callback"]
    browser_cookie = client.cookies.get("lr_sso_state")
    client.cookies.clear()
    url = "/api/auth/google/callback?state=" + query["state"][0] + "&code=test-code"
    assert client.get(url).status_code == 403
    client.cookies.set("lr_sso_state", browser_cookie)
    real_client = httpx.AsyncClient

    async def exchange(request):
        body = parse_qs(request.content.decode())
        assert body["code_verifier"] and body["client_secret"] == ["test-secret"]
        return httpx.Response(200, json={"id_token": signing(nonce=query["nonce"][0])})

    monkeypatch.setattr(
        sso.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(exchange))
    )
    response = client.get(url, follow_redirects=False)
    assert response.status_code == 303
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.get("/api/session").json()["user"]["email"] == "member@berri.ai"
    assert client.get(url, follow_redirects=False).status_code == 403
    assert "test-secret" not in response.text


def test_legacy_sessions_cannot_bypass_sso_and_members_cannot_admin(owner, monkeypatch):
    enable(monkeypatch)
    assert owner.get("/api/resources/agents").status_code == 401
    assert owner.post("/api/login", json={"token": settings().admin_token}).status_code == 403
    assert owner.get("/api/auth/github", follow_redirects=False).status_code == 401
    owner.cookies.clear()
    identity(owner)
    assert owner.get("/api/session").json()["user"]["admin"] is False
    for path in ["/api/resources/agents", "/api/runs", "/api/artifacts/any", "/api/setup", "/api/harnesses"]:
        assert owner.get(path).status_code == 403
    assert owner.post("/api/logout").status_code == 200
    assert not sso.eligible_caller({"id": 42, "login": "trusted"})


@pytest.mark.parametrize("changed", ["domain", "audience", "expires_at", "session_id"])
def test_identity_policy_and_session_changes_take_effect_immediately(client, monkeypatch, changed):
    enable(monkeypatch)
    identity(client, email="admin@berri.ai")
    assert client.get("/api/resources/agents").status_code == 200
    with Session() as db:
        setattr(db.get(SSOIdentity, "google-sub"), changed, 0 if changed == "expires_at" else "changed")
        db.commit()
    assert client.get("/api/resources/agents").status_code == 401


def test_incomplete_sso_config_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings(), "google_sso_required", True)
    assert client.post("/api/login", json={"token": settings().admin_token}).status_code == 403
    assert client.get("/api/auth/google").status_code == 503
    assert not sso.eligible_caller({"id": 42, "login": "trusted"})


def test_signed_webhook_requires_current_google_and_linked_github_id(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    enable(monkeypatch)
    data = payload()
    data["comment"]["user"]["id"] = 42
    rejected = webhook(owner, data, "no-sso").json()["run_id"]
    identity()
    accepted = webhook(owner, data, "with-sso").json()["run_id"]
    data["comment"]["user"]["id"] = 43
    wrong_id = webhook(owner, data, "wrong-id").json()["run_id"]
    with Session() as db:
        assert db.get(Run, rejected).status == db.get(Run, wrong_id).status == "rejected"
        assert db.get(Run, accepted).status == "queued"
        assert len(db.scalars(select(Job)).all()) == 2
        db.get(SSOIdentity, "google-sub").expires_at = 0
        db.commit()
    assert not sso.eligible_caller({"id": 42, "login": "trusted"})
    assert owner.post("/api/webhook/github", json=data).status_code == 401


def test_github_link_requires_both_proven_identities(client, monkeypatch):
    enable(monkeypatch)
    identity(client, github_id=None, github_login="")
    monkeypatch.setattr(
        github, "config", lambda: {"client_id": "github-client", "client_secret": "github-secret"}
    )
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        sso.httpx,
        "AsyncClient",
        lambda **kw: real_client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, json={"access_token": "temporary-token"})
            )
        ),
    )
    monkeypatch.setattr(
        github, "request", AsyncMock(return_value=httpx.Response(200, json={"id": 42, "login": "trusted"}))
    )
    response = client.get("/api/auth/github", follow_redirects=False)
    state = parse_qs(urlparse(response.headers["location"]).query)["state"][0]
    callback = "/api/auth/github/callback?state=" + state + "&code=github-code"
    assert client.get(callback, follow_redirects=False).status_code == 303
    assert sso.eligible_caller({"id": 42, "login": "trusted"})
    assert not sso.eligible_caller({"id": 42, "login": "renamed"})
    assert client.get(callback, follow_redirects=False).status_code == 403
    assert client.delete("/api/auth/github/link").status_code == 200
    assert not sso.eligible_caller({"id": 42, "login": "trusted"})
