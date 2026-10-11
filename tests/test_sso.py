import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_reviews import mock_github, payload, setup, webhook

from app import github, sso
from app.db import Job, Run, Session, SSOIdentity, SSOSession, init_db
from app.main import app
from app.security import digest, signed
from app.settings import settings


def enable(monkeypatch):
    for key, value in {
        "google_sso_required": True,
        "google_client_id": "test-client",
        "google_client_secret": "test-secret",
        "google_allowed_domains": "berri.ai",
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
        db.add(SSOIdentity(**{**data, "expires_at": 0, "session_id": ""}))
        if client:
            db.add(
                SSOSession(
                    token_hash=digest(data["session_id"]),
                    identity_sub=data["sub"],
                    audience=data["audience"],
                    expires_at=data["expires_at"],
                )
            )
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
        await sso.verify_google(
            jwt.encode({"sub": "forged"}, "test-hmac-secret-" * 3, algorithm="HS256"), "nonce"
        )


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


def test_legacy_sessions_cannot_bypass_sso_and_members_get_workspace_access(owner, monkeypatch):
    enable(monkeypatch)
    assert owner.get("/api/resources/agents").status_code == 401
    assert owner.post("/api/login", json={"token": settings().admin_token}).status_code == 403
    assert owner.get("/api/auth/github", follow_redirects=False).status_code == 401
    owner.cookies.clear()
    identity(owner)
    assert "admin" not in owner.get("/api/session").json()["user"]
    for path in ["/api/resources/agents", "/api/runs", "/api/setup", "/api/harnesses"]:
        assert owner.get(path).status_code == 200
    assert owner.get("/api/artifacts/any").status_code == 404
    assert owner.post("/api/logout").status_code == 200
    assert owner.get("/api/resources/agents").status_code == 401
    assert sso.eligible_caller({"id": 42, "login": "trusted"})


@pytest.mark.parametrize(
    "changed", ["domain", "audience", "expires_at", "revoked", "identity_sub", "new-client"]
)
def test_identity_policy_and_session_changes_take_effect_immediately(client, monkeypatch, changed):
    enable(monkeypatch)
    identity(client, email="admin@berri.ai")
    assert client.get("/api/resources/agents").status_code == 200
    with Session() as db:
        if changed == "new-client":
            # Reauthorizing the identity with a changed Google client must not
            # revive browser sessions issued for the previous client.
            monkeypatch.setattr(settings(), "google_client_id", "new-client")
            db.get(SSOIdentity, "google-sub").audience = "new-client"
        elif changed in ("domain", "audience"):
            setattr(db.get(SSOIdentity, "google-sub"), changed, "changed")
        else:
            browser = db.get(SSOSession, digest("test-session"))
            if changed == "revoked":
                db.delete(browser)
            else:
                setattr(browser, changed, 0 if changed == "expires_at" else "changed")
        db.commit()
    assert client.get("/api/resources/agents").status_code == 401


def test_incomplete_sso_config_fails_closed(client, monkeypatch):
    monkeypatch.setattr(settings(), "google_sso_required", True)
    assert client.post("/api/login", json={"token": settings().admin_token}).status_code == 403
    assert client.get("/api/auth/google").status_code == 503
    assert not sso.eligible_caller({"id": 42, "login": "trusted"})


def test_google_browsers_keep_independent_sessions_and_return_to_run(client, signing, monkeypatch):
    run_id = "a" * 32
    destination = "/runs/" + run_id
    with Session.begin() as db:
        db.add(
            Run(
                id=run_id,
                delivery="session-check",
                repo="org/repo",
                pr=1,
                actor="trusted",
                head="a" * 40,
                base="b" * 40,
                snapshot={},
            )
        )
    real_client = httpx.AsyncClient

    def login(browser):
        start = browser.get("/api/auth/google", params={"next": destination}, follow_redirects=False)
        query = parse_qs(urlparse(start.headers["location"]).query)
        monkeypatch.setattr(
            sso.httpx,
            "AsyncClient",
            lambda **kw: real_client(
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json={"id_token": signing(nonce=query["nonce"][0])})
                )
            ),
        )
        result = browser.get(
            "/api/auth/google/callback",
            params={"state": query["state"][0], "code": "ok"},
            follow_redirects=False,
        )
        assert result.status_code == 303
        assert result.headers["location"] == destination
        assert "HttpOnly" in result.headers["set-cookie"]
        assert f"Max-Age={settings().sso_session_seconds}" in result.headers["set-cookie"]
        return browser.cookies.get("lr_session")

    with TestClient(app) as second:
        first_cookie = login(client)
        first_expiry = client.get("/api/session").json()["user"]["expires_at"]
        second_cookie = login(second)
        assert first_cookie != second_cookie
        for browser in (client, second):
            assert browser.get("/api" + destination).json()["id"] == run_id
        assert client.get("/api/session").json()["user"]["expires_at"] == first_expiry
        assert second.post("/api/logout").status_code == 200
        second.cookies.set("lr_session", second_cookie)
        assert second.get("/api" + destination).status_code == 401
        assert client.get("/api" + destination).status_code == 200
        # Repeated login and application startup leave the first browser valid.
        second.cookies.clear()
        login(second)
        init_db()
        for browser in (client, second):
            assert browser.get("/api" + destination).status_code == 200
        with Session.begin() as db:
            sessions = db.scalars(select(SSOSession).order_by(SSOSession.expires_at)).all()
            assert len(sessions) == 2
            sessions[0].expires_at = 0
        assert client.get("/api" + destination).status_code == 401
        assert second.get("/api" + destination).status_code == 200


@pytest.mark.parametrize("expired", [False, True])
def test_single_session_upgrade_preserves_valid_cookie_without_resurrecting_logout(
    client, monkeypatch, expired
):
    enable(monkeypatch)
    data = identity()
    expires_at = time.time() + (-60 if expired else 600)
    with Session.begin() as db:
        row = db.get(SSOIdentity, data["sub"])
        row.session_id, row.expires_at = data["session_id"], expires_at
    client.cookies.set(
        "lr_session",
        signed(
            {
                "auth": "google",
                "sub": data["sub"],
                "session_id": data["session_id"],
            }
        ),
    )
    init_db()
    init_db()
    if expired:
        assert client.get("/api/resources/agents").status_code == 401
    else:
        assert client.get("/api/session").json()["user"]["expires_at"] == expires_at
        cookie = client.cookies.get("lr_session")
        client.post("/api/logout").raise_for_status()
        client.cookies.set("lr_session", cookie)
    init_db()
    assert client.get("/api/resources/agents").status_code == 401
    with Session() as db:
        assert not db.scalars(select(SSOSession)).all()
        assert db.get(SSOIdentity, data["sub"]).session_id == ""
    assert sso.eligible_caller({"id": 42, "login": "trusted"})


@pytest.mark.parametrize("session_ended", ["expired", "signed_out"])
def test_signed_webhook_uses_linked_github_after_browser_session_ends(owner, monkeypatch, session_ended):
    setup(owner)
    mock_github(monkeypatch)
    enable(monkeypatch)
    data = payload()
    data["comment"]["user"]["id"] = 42
    rejected = webhook(owner, data, "no-sso").json()["run_id"]
    owner.cookies.clear()
    identity(owner)
    browser_cookie = owner.cookies.get("lr_session")
    data["comment"]["id"] = 12346
    accepted = webhook(owner, data, "with-sso").json()["run_id"]
    data["comment"]["id"] = 12347
    data["comment"]["user"]["id"] = 43
    wrong_id = webhook(owner, data, "wrong-id").json()["run_id"]
    with Session() as db:
        assert db.get(Run, rejected).status == db.get(Run, wrong_id).status == "rejected"
        assert db.get(Run, accepted).status == "queued"
        assert len(db.scalars(select(Job)).all()) == 2
    if session_ended == "expired":
        with Session() as db:
            db.get(SSOSession, digest("test-session")).expires_at = time.time() - 365 * 86400
            db.commit()
    else:
        assert owner.post("/api/logout").status_code == 200
        owner.cookies.clear()
        owner.cookies.set("lr_session", browser_cookie)
    assert owner.get("/api/resources/agents").status_code == 401
    assert sso.eligible_caller({"id": 42, "login": "trusted"})
    data["comment"]["id"] = 12348
    data["comment"]["user"]["id"] = 42
    later = webhook(owner, data, "after-session").json()["run_id"]
    with Session() as db:
        assert db.get(Run, later).status == "queued"
        assert len(db.scalars(select(Job)).all()) == 4
    assert owner.post("/api/webhook/github", json=data).status_code == 401


@pytest.mark.parametrize(
    "changes",
    [
        {"domain": "outside.test", "email": "member@outside.test"},
        {"audience": "another-client"},
        {"email": "member@outside.test"},
        {"github_id": None, "github_login": ""},
        {"github_id": "43"},
        {"github_login": "another-login"},
    ],
)
def test_persistent_github_access_still_checks_workspace_identity_and_link(owner, monkeypatch, changes):
    setup(owner)
    mock_github(monkeypatch)
    enable(monkeypatch)
    identity(expires_at=0, **changes)
    data = payload()
    data["comment"]["user"]["id"] = 42
    rejected = webhook(owner, data, "invalid-link").json()["run_id"]
    with Session() as db:
        assert db.get(Run, rejected).status == "rejected"
        assert not db.scalars(select(Job)).all()


def test_github_link_requires_both_proven_identities(client, monkeypatch):
    from app.db import Resource

    enable(monkeypatch)
    identity(client, github_id=None, github_login="")
    with Session() as db:
        db.add(
            Resource(
                id="pending-person",
                kind="access",
                data={"subject": "user", "name": "trusted", "scope": "selected", "target_ids": []},
            )
        )
        db.commit()
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
    with Session() as db:
        assert db.get(Resource, "pending-person").data["identity_sub"] == "google-sub"


def test_sso_members_can_review_without_manual_people_entry(owner, monkeypatch):
    from app.db import Resource

    setup(owner)
    with Session() as db:
        for grant in db.scalars(select(Resource).where(Resource.kind == "access")):
            if grant.data["subject"] == "user":
                db.delete(grant)
        db.commit()
    mock_github(monkeypatch)
    enable(monkeypatch)
    identity()
    data = payload()
    data["comment"]["user"]["id"] = 42
    run = webhook(owner, data, "sso-default").json()["run_id"]
    with Session() as db:
        assert db.get(Run, run).status == "queued"


@pytest.mark.parametrize("subject", ["user", "repo"])
@pytest.mark.parametrize("expired", [False, True])
def test_sso_default_never_overrides_explicit_restrictions(owner, monkeypatch, subject, expired):
    from app.db import Resource

    setup(owner)
    with Session() as db:
        for grant in db.scalars(select(Resource).where(Resource.kind == "access")):
            if grant.data["subject"] == subject:
                grant.data = {**grant.data, "scope": "selected", "target_ids": []}
        db.commit()
    mock_github(monkeypatch)
    enable(monkeypatch)
    identity(expires_at=0 if expired else time.time() + 1000)
    data = payload()
    data["comment"]["user"]["id"] = 42
    run = webhook(owner, data, "restricted").json()["run_id"]
    with Session() as db:
        assert db.get(Run, run).status == "rejected"
        assert not db.scalars(select(Job)).all()


def test_people_comes_from_sso_and_preserves_existing_restrictions(owner, monkeypatch):
    from app.db import Resource

    setup(owner)
    with Session() as db:
        db.add(
            Resource(
                id="not-yet-linked",
                kind="access",
                data={"subject": "user", "name": "limited-person", "scope": "selected", "target_ids": []},
            )
        )
        db.commit()
    enable(monkeypatch)
    identity(owner, email="admin@berri.ai")
    identity(sub="new", email="new@berri.ai", github_id="43", github_login="new-person")
    identity(sub="unlinked", email="unlinked@berri.ai", github_id=None, github_login="")
    identity(
        sub="expired", email="expired@berri.ai", github_id="44", github_login="expired-person", expires_at=0
    )
    rows = [r for r in owner.get("/api/resources/access").json() if r["subject"] == "user"]
    assert len(rows) == 5
    fresh = next(r for r in rows if r["name"] == "new-person")
    assert fresh["inherited"] and fresh["scope"] == "all" and fresh["status"] == "ready"
    assert next(r for r in rows if r["email"] == "unlinked@berri.ai")["status"] == "github_required"
    assert next(r for r in rows if r["email"] == "expired@berri.ai")["status"] == "ready"
    assert next(r for r in rows if r["name"] == "limited-person")["status"] == "not_signed_in"
    override = owner.post(
        "/api/resources/access", json={**fresh, "scope": "selected", "target_ids": []}
    ).json()
    rows = owner.get("/api/resources/access").json()
    assert not next(r for r in rows if r["name"] == "new-person")["inherited"]
    owner.delete("/api/resources/access/" + override["id"]).raise_for_status()
    assert next(r for r in owner.get("/api/resources/access").json() if r["name"] == "new-person")[
        "inherited"
    ]


@pytest.mark.parametrize("new_id", ["42", "99"])
def test_person_restrictions_follow_google_identity_after_github_changes(owner, monkeypatch, new_id):
    from app.access import bind_existing_people
    from app.db import Resource

    setup(owner)
    enable(monkeypatch)
    identity(owner, email="admin@berri.ai")
    person = next(
        r
        for r in owner.get("/api/resources/access").json()
        if r["subject"] == "user" and r["name"] == "trusted"
    )
    # Existing overrides migrate once and remain attached across relinks.
    with Session() as db:
        row = db.get(Resource, person["id"])
        row.data = {**row.data, "scope": "selected", "target_ids": []}
        db.commit()
    bind_existing_people()
    with Session() as db:
        row = db.get(SSOIdentity, "google-sub")
        row.github_id, row.github_login = new_id, "renamed-person"
        db.commit()
    person = next(r for r in owner.get("/api/resources/access").json() if r["subject"] == "user")
    assert person["name"] == "renamed-person" and person["scope"] == "selected" and not person["target_ids"]
    mock_github(monkeypatch)
    data = payload(actor="renamed-person")
    data["comment"]["user"]["id"] = int(new_id)
    rid = webhook(owner, data, "renamed").json()["run_id"]
    with Session() as db:
        assert db.get(Run, rid).status == "rejected"
    owner.put("/api/resources/access/" + person["id"], json={**person, "scope": "all"}).raise_for_status()
    data["comment"]["id"] = 12346
    rid = webhook(owner, data, "allowed").json()["run_id"]
    with Session() as db:
        assert db.get(Run, rid).status == "queued"


def test_new_override_binds_person_and_cannot_duplicate_after_relink(owner, monkeypatch):
    setup(owner)
    enable(monkeypatch)
    identity(owner, email="admin@berri.ai")
    identity(sub="new-person", email="new@berri.ai", github_id="50", github_login="new-login")
    person = next(r for r in owner.get("/api/resources/access").json() if r["name"] == "new-login")
    saved = owner.post("/api/resources/access", json={**person, "scope": "selected"}).json()
    assert saved["identity_sub"] == "new-person"
    with Session() as db:
        db.get(SSOIdentity, "new-person").github_login = "changed-login"
        db.commit()
    person = next(r for r in owner.get("/api/resources/access").json() if r["name"] == "changed-login")
    assert person["id"] == saved["id"] and not person["inherited"]
    assert owner.post("/api/resources/access", json=person).status_code == 409
    # A different approved Google person linking the recycled name does not take
    # ownership of the old person's override.
    identity(sub="other-person", email="other@berri.ai", github_id="51", github_login="new-login")
    other = next(r for r in owner.get("/api/resources/access").json() if r["name"] == "new-login")
    assert other["inherited"] and other["scope"] == "all"
