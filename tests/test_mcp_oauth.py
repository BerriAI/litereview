import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

import httpx
import pytest
from fastapi import HTTPException
from mcp_fixture import mcp_server
from test_sso import enable, identity

from app import mcp_oauth, providers
from app.db import MCPOAuth, Resource, Session
from app.security import encrypt


@pytest.fixture
def server():
    with mcp_server() as state:
        yield state


def connection(owner, server, **extra):
    response = owner.post(
        "/api/resources/connections",
        json={
            "name": "Documents",
            "type": "mcp",
            "base_url": server["base"] + "/mcp",
            "auth_type": "oauth",
            **extra,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def start(owner, conn):
    response = owner.post(f"/api/connections/{conn['id']}/oauth/start")
    assert response.status_code == 200, response.text
    authorization = httpx.get(response.json()["authorization_url"], follow_redirects=False)
    url = urlsplit(authorization.headers["location"])
    return url.path + "?" + url.query


def authorize(owner, conn):
    callback = start(owner, conn)
    response = owner.get(callback, follow_redirects=False)
    assert response.status_code == 303 and "oauth=connected" in response.headers["location"], response.text
    return callback


def test_complete_oauth_pkce_catalog_encryption_and_replay(owner, server):
    conn = connection(owner, server)
    callback = authorize(owner, conn)
    assert owner.get(callback, follow_redirects=False).status_code == 403
    result = owner.post(f"/api/connections/{conn['id']}/test")
    assert result.status_code == 200, result.text
    assert [t["name"] for t in result.json()["tools"]] == ["search_documents", "fetch_document"]
    rows = owner.get("/api/resources/connections").json()
    assert rows[0]["oauth"]["connected"] and rows[0]["last_test"]["tool_count"] == 2
    with Session() as db:
        encrypted = db.get(MCPOAuth, conn["id"]).secret
        token = mcp_oauth.unpack(db.get(MCPOAuth, conn["id"]))["tokens"]["access_token"]
        assert token not in encrypted and token not in json.dumps(rows)
        assert "refresh_token" not in json.dumps(db.get(Resource, conn["id"]).data)
    assert owner.delete("/api/resources/connections/" + conn["id"]).status_code == 200
    with Session() as db:
        assert db.get(MCPOAuth, conn["id"]) is None


def test_connection_test_before_oauth_explains_next_action(owner, server):
    conn = connection(owner, server)
    response = owner.post(f"/api/connections/{conn['id']}/test")
    assert response.status_code == 409 and "Connect this MCP" in response.json()["detail"]
    assert not owner.get("/api/resources/connections").json()[0]["last_test"]["ok"]


def test_cancellation_and_failed_reconnect_keep_working_authorization(owner, server):
    conn = connection(owner, server)
    authorize(owner, conn)
    callback = start(owner, conn)
    denied = owner.get(callback + "&error=access_denied", follow_redirects=False)
    assert "oauth=denied" in denied.headers["location"]
    assert mcp_oauth.status(conn["id"])["connected"]
    callback = start(owner, conn)
    server["token_status"] = 400
    failed = owner.get(callback, follow_redirects=False)
    assert "oauth=failed" in failed.headers["location"]
    assert mcp_oauth.status(conn["id"])["connected"]


@pytest.mark.parametrize("change", ["endpoint", "client", "disconnect", "delete"])
def test_settings_changes_invalidate_in_flight_authorization_before_exchange(owner, server, change):
    conn = connection(owner, server)
    callback = start(owner, conn)
    if change in ("endpoint", "client"):
        data = {
            **conn,
            **(
                {"base_url": server["base"] + "/different"}
                if change == "endpoint"
                else {"oauth_client_id": "other"}
            ),
        }
        owner.put("/api/resources/connections/" + conn["id"], json=data).raise_for_status()
    elif change == "disconnect":
        owner.post(f"/api/connections/{conn['id']}/oauth/disconnect").raise_for_status()
    else:
        owner.delete("/api/resources/connections/" + conn["id"]).raise_for_status()
    result = owner.get(callback, follow_redirects=False)
    assert "oauth=failed" in result.headers["location"]
    assert not any(path == "/token" for path, *_ in server["calls"])


def test_browser_and_actor_binding_expiry_and_admin_only(owner, server, monkeypatch):
    conn = connection(owner, server)
    callback = start(owner, conn)
    saved = owner.cookies.get("lr_mcp_state")
    owner.cookies.delete("lr_mcp_state")
    assert owner.get(callback, follow_redirects=False).status_code == 403
    owner.cookies.set("lr_mcp_state", saved)
    enable(monkeypatch)
    identity(owner, email="admin@berri.ai")
    assert owner.get(callback, follow_redirects=False).status_code == 403
    with Session() as db:
        from app.db import SSOIdentity

        db.get(SSOIdentity, "google-sub").email = "member@berri.ai"
        db.commit()
    assert owner.post(f"/api/connections/{conn['id']}/oauth/start").status_code == 403
    assert owner.post(f"/api/connections/{conn['id']}/oauth/disconnect").status_code == 403


def test_expired_state_rejected(owner, server):
    from sqlalchemy import select

    from app.db import LoginState

    conn = connection(owner, server)
    callback = start(owner, conn)
    with Session() as db:
        db.scalar(select(LoginState)).expires_at = time.time() - 1
        db.commit()
    assert owner.get(callback, follow_redirects=False).status_code == 403


def expire(cid):
    with Session() as db:
        row = db.get(MCPOAuth, cid)
        data = mcp_oauth.unpack(row)
        data["expires_at"] = time.time() - 1
        row.secret = encrypt(json.dumps(data))
        db.commit()


def test_refresh_rotation_is_serialized_and_transient_errors_keep_token(owner, server):
    conn = connection(owner, server)
    authorize(owner, conn)
    expire(conn["id"])
    server["token_status"] = 503
    with pytest.raises(HTTPException) as error:
        mcp_oauth.access_token(conn["id"], conn["base_url"])
    assert error.value.status_code == 502
    assert mcp_oauth.status(conn["id"])["connected"]
    server["token_status"] = 200
    with ThreadPoolExecutor(max_workers=4) as pool:
        tokens = list(pool.map(lambda _: mcp_oauth.access_token(conn["id"], conn["base_url"]), range(4)))
    assert len(set(tokens)) == 1 and server["refreshes"] == 1
    expire(conn["id"])
    server["token_status"] = 400
    with pytest.raises(HTTPException):
        mcp_oauth.access_token(conn["id"], conn["base_url"])
    assert not mcp_oauth.status(conn["id"])["connected"]


async def test_runner_uses_connection_specific_tokens_and_refreshes_on_401(owner, server):
    conn = connection(owner, server)
    authorize(owner, conn)
    other = connection(owner, server, name="Second account")
    authorize(owner, other)
    first_token = mcp_oauth.access_token(conn["id"], conn["base_url"])
    second_token = mcp_oauth.access_token(other["id"], other["base_url"])
    assert first_token != second_token
    server["tokens"].remove(first_token)
    result = await providers.mcp_call(providers.snapshot_connection(conn), "search_documents", {})
    assert result["content"][0]["text"] == "Fixture tool executed" and server["refreshes"] == 1
    assert mcp_oauth.access_token(other["id"], other["base_url"]) == second_token
    auth = mcp_oauth.MCPAuth(conn["id"], conn["base_url"])
    with pytest.raises(HTTPException):
        await anext(auth.async_auth_flow(httpx.Request("POST", server["base"] + "/other")))


def test_manual_registration_and_resource_mismatch(owner, server):
    server["registration"] = False
    conn = connection(owner, server)
    response = owner.post(f"/api/connections/{conn['id']}/oauth/start")
    assert response.status_code == 400 and "client ID" in response.json()["detail"]
    conn = owner.put(
        "/api/resources/connections/" + conn["id"],
        json={**conn, "oauth_client_id": "registered-client", "oauth_client_secret": "private-client-secret"},
    ).json()
    assert "private-client-secret" not in json.dumps(conn)
    authorize(owner, conn)
    token_body = next(body for path, body, _ in server["calls"] if path == "/token")
    assert token_body["client_secret"] == "private-client-secret"
    server["resource"] = "https://other.example/mcp"
    response = owner.post(f"/api/connections/{conn['id']}/oauth/start")
    assert response.status_code == 400


async def test_late_connection_test_cannot_restore_disconnected_success(owner, server, monkeypatch):
    conn = connection(owner, server)
    authorize(owner, conn)

    async def delayed(_):
        mcp_oauth.disconnect(conn["id"])
        return []

    monkeypatch.setattr(providers, "mcp_tools", delayed)
    result = owner.post(f"/api/connections/{conn['id']}/test")
    assert result.status_code == 409
    assert owner.get("/api/resources/connections").json()[0]["last_test"] is None
