import asyncio
import base64
import hashlib
import re
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from test_sso import enable, identity

from app.db import (
    Artifact,
    Base,
    MCPAuthorization,
    MCPGrant,
    MCPToken,
    Resource,
    Session,
    WorkspaceAdmin,
    engine,
)
from app.main import app
from app.schemas import Agent
from app.security import digest
from app.settings import settings

URL = "http://localhost:8769"
REDIRECT = "http://127.0.0.1:9042/callback"


@pytest.fixture
def mcp_client(monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(settings(), "public_url", URL)
    Base.metadata.drop_all(engine)
    with TestClient(app, base_url=URL) as client:
        identity(client)
        yield client


def register(client, **changes):
    response = client.post("/register", json={"client_name": "Local acceptance client", "redirect_uris": [REDIRECT],
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"],
        "token_endpoint_auth_method": "none", **changes})
    response.raise_for_status()
    return response.json()


def authorize(client, registered=None, **params):
    registered = registered or register(client)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    response = client.get("/authorize", params={"response_type": "code", "client_id": registered["client_id"],
        "redirect_uri": REDIRECT, "scope": "litereview", "resource": URL + "/api/mcp", "state": "client-state",
        "code_challenge": challenge, "code_challenge_method": "S256", **params}, follow_redirects=False)
    return registered, verifier, response


def approve(client, **params):
    registered, verifier, response = authorize(client, **params)
    assert response.status_code == 302
    path = response.headers["location"]
    consent = client.get(path)
    assert "Connect Local acceptance client to LiteReview" in consent.text
    csrf = re.search(r'name="csrf" value="([^"]+)"', consent.text)[1]
    response = client.post(path, data={"csrf": csrf, "decision": "allow"}, follow_redirects=False)
    assert response.status_code == 303
    query = parse_qs(urlparse(response.headers["location"]).query)
    assert query["state"] == ["client-state"]
    form = {"grant_type": "authorization_code", "code": query["code"][0], "client_id": registered["client_id"],
            "redirect_uri": REDIRECT, "code_verifier": verifier, "resource": URL + "/api/mcp"}
    return form


def token(client):
    form = approve(client)
    response = client.post("/token", data=form)
    assert response.status_code == 200, response.text
    return response.json(), form


def rpc(client, access, method, params=None, **headers):
    response = client.post("/api/mcp", headers={"Authorization": "Bearer " + access,
        "Accept": "application/json, text/event-stream", **headers},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}})
    assert response.status_code == 200, response.text
    return response.json()["result"]


def call(client, access, name, arguments=None):
    result = rpc(client, access, "tools/call", {"name": "litereview_" + name, "arguments": arguments or {}})
    assert not result.get("isError"), result
    return result["structuredContent"]


def test_google_handoff_and_protected_metadata(mcp_client):
    c = mcp_client
    response = c.post("/api/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert response.status_code == 401  # A dashboard cookie never substitutes for MCP consent.
    assert "resource_metadata=" in response.headers["www-authenticate"]
    metadata = c.get("/.well-known/oauth-protected-resource/api/mcp").json()
    assert metadata["resource"] == URL + "/api/mcp"
    auth = c.get("/.well-known/oauth-authorization-server").json()
    assert auth["code_challenge_methods_supported"] == ["S256"]
    c.cookies.clear()
    _, _, response = authorize(c)
    login = c.get(response.headers["location"], follow_redirects=False)
    assert login.headers["location"].startswith("/api/auth/google?")
    google = c.get(login.headers["location"], follow_redirects=False)
    assert google.headers["location"].startswith("https://accounts.google.com/")


def test_pkce_redirect_resource_code_replay_and_refresh_rotation(mcp_client):
    c = mcp_client
    form = approve(c)
    for changes in [{"code_verifier": "wrong"}, {"redirect_uri": "https://evil.example/cb"},
                    {"resource": "https://other.example/mcp"}]:
        assert c.post("/token", data={**form, **changes}).status_code == 400
    issued = c.post("/token", data=form).json()
    assert c.post("/token", data=form).status_code == 400
    refresh = {"grant_type": "refresh_token", "client_id": form["client_id"], "refresh_token": issued["refresh_token"]}
    response = c.post("/token", data=refresh)
    assert response.status_code == 200
    rotated = response.json()
    assert rotated["refresh_token"] != issued["refresh_token"]
    assert c.get("/api/session", headers={"Authorization": "Bearer " + issued["access_token"]}).json()["user"]
    assert c.get("/api/session", headers={"Authorization": "Bearer " + rotated["access_token"]}).json()["user"]
    retried = c.post("/token", data=refresh)
    assert retried.status_code == 200
    assert retried.json()["access_token"] == rotated["access_token"]
    assert retried.json()["refresh_token"] == rotated["refresh_token"]
    with Session.begin() as db:
        cache = db.get(MCPAuthorization, digest(issued["refresh_token"]))
        assert all(value not in str(cache.data) for key, value in rotated.items() if key.endswith("token"))
        cache.expires_at = time.time() - 1
    assert c.post("/token", data=refresh).status_code == 400
    assert c.get("/api/session", headers={"Authorization": "Bearer " + rotated["access_token"]}).json()["user"] is None
    with Session() as db:
        assert db.scalar(select(MCPGrant)).revoked
        assert all(t.token_hash not in issued.values() for t in db.scalars(select(MCPToken)))


def test_parallel_refreshes_and_restart_retry_keep_one_token_pair(mcp_client):
    from app.mcp_auth import Provider

    issued, form = token(mcp_client)
    ready = Barrier(4)

    def refresh():
        async def run():
            provider = Provider()  # Separate workers share only the database.
            client = await provider.get_client(form["client_id"])
            loaded = await provider.load_refresh_token(client, issued["refresh_token"])
            assert loaded
            ready.wait(timeout=10)
            return await provider.exchange_refresh_token(client, loaded, ["litereview"])
        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: refresh(), range(4)))
    assert len({r.access_token for r in results}) == 1
    assert len({r.refresh_token for r in results}) == 1
    with Session.begin() as db:
        assert not db.scalar(select(MCPGrant)).revoked
        assert len(db.scalars(select(MCPToken)).all()) == 4
        cache = db.get(MCPAuthorization, digest(issued["refresh_token"]))
        cache.data = {**cache.data, "issued_at": cache.data["issued_at"] - 20}
    # A lost response can be recovered by a fresh Provider after a restart.
    async def retry():
        provider = Provider()
        client = await provider.get_client(form["client_id"])
        loaded = await provider.load_refresh_token(client, issued["refresh_token"])
        return await provider.exchange_refresh_token(client, loaded, ["litereview"])
    retried = asyncio.run(retry())
    assert retried.access_token == results[0].access_token
    assert retried.refresh_token == results[0].refresh_token
    assert retried.expires_in <= results[0].expires_in - 19
    form = {"grant_type": "refresh_token", "client_id": form["client_id"], "refresh_token": retried.refresh_token}
    assert mcp_client.post("/token", data=form).status_code == 200


@pytest.mark.parametrize("change", ["revoked", "expired", "workspace", "resource", "audience"])
def test_refresh_retry_rechecks_authorization(mcp_client, monkeypatch, change):
    issued, form = token(mcp_client)
    refresh = {"grant_type": "refresh_token", "client_id": form["client_id"], "refresh_token": issued["refresh_token"]}
    rotated = mcp_client.post("/token", data=refresh).json()
    with Session.begin() as db:
        grant = db.scalar(select(MCPGrant))
        if change == "revoked":
            grant.revoked = True
        elif change == "expired":
            grant.expires_at = time.time() - 1
        elif change == "resource":
            grant.resource = "https://other.example/mcp"
        elif change == "audience":
            grant.audience = "other-google-client"
    if change == "workspace":
        monkeypatch.setattr(settings(), "google_allowed_domains", "different.example")
    assert mcp_client.post("/token", data=refresh).status_code == 400
    for access in [issued["access_token"], rotated["access_token"]]:
        assert mcp_client.post("/api/mcp", json={}, headers={"Authorization": "Bearer " + access}).status_code == 401


def test_refresh_rejects_wrong_client_resource_scope_and_preserves_access_expiry(mcp_client):
    issued, form = token(mcp_client)
    refresh = {"grant_type": "refresh_token", "client_id": form["client_id"], "refresh_token": issued["refresh_token"]}
    other = register(mcp_client)
    for changes in [{"client_id": other["client_id"]}, {"resource": "https://other.example/mcp"}, {"scope": "admin"}]:
        assert mcp_client.post("/token", data={**refresh, **changes}).status_code == 400
    with Session() as db:
        original_expiry = db.get(MCPToken, digest(issued["access_token"])).expires_at
    rotated = mcp_client.post("/token", data=refresh).json()
    with Session.begin() as db:
        old = db.get(MCPToken, digest(issued["access_token"]))
        assert old.expires_at == original_expiry
        old.expires_at = time.time() - 1
    assert mcp_client.post("/api/mcp", json={}, headers={"Authorization": "Bearer " + issued["access_token"]}).status_code == 401
    assert call(mcp_client, rotated["access_token"], "get_workspace")["user"]


def test_consent_requires_browser_identity_and_csrf(mcp_client):
    c = mcp_client
    _, _, response = authorize(c)
    path = response.headers["location"]
    response = c.get(path)
    csrf = re.search(r'name="csrf" value="([^"]+)"', response.text)[1]
    c.cookies.delete("lr_mcp_consent")
    assert c.post(path, data={"csrf": csrf, "decision": "allow"}).status_code == 403
    response = c.get(path)
    csrf = re.search(r'name="csrf" value="([^"]+)"', response.text)[1]
    denied = c.post(path, data={"csrf": csrf, "decision": "deny"}, follow_redirects=False)
    assert "error=access_denied" in denied.headers["location"]
    assert c.post(path, data={"csrf": csrf, "decision": "allow"}).status_code in (400, 403)


@pytest.mark.parametrize("redirect", ["http://evil.example/cb", "https://host.example/cb#fragment", "https://u:p@host.example/cb"])
def test_registration_rejects_unsafe_redirects(mcp_client, redirect):
    response = mcp_client.post("/register", json={"redirect_uris": [redirect], "grant_types": ["authorization_code", "refresh_token"]})
    assert response.status_code == 400


def test_real_mcp_discovery_context_crud_rules_and_partial_updates(mcp_client):
    c = mcp_client
    issued, _ = token(c)
    access = issued["access_token"]
    workspace = call(c, access, "get_workspace")
    assert workspace["user"]["email"] == "member@berri.ai" and not workspace["is_admin"]
    person = call(c, access, "list_resources", {"kind": "access"})["items"][0]
    assert person["inherited"]
    assert call(c, access, "get_resource", {"kind": "access", "resource_id": person["id"]})["identity_sub"] == "google-sub"
    initialized = rpc(c, access, "initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                       "clientInfo": {"name": "acceptance", "version": "1"}})
    assert "litereview_get_guide" in initialized["instructions"]
    tools = rpc(c, access, "tools/list")["tools"]
    assert len(tools) >= 30
    assert all(t["name"].startswith("litereview_") and "annotations" in t for t in tools)
    create = next(t for t in tools if t["name"] == "litereview_create_agent")
    assert "review_mode" in str(create["inputSchema"]) and "trigger_access" in str(create["inputSchema"])
    assert rpc(c, access, "resources/read", {"uri": "litereview://guide"})["contents"][0]["text"]
    assert rpc(c, access, "prompts/get", {"name": "configure_litereview", "arguments": {"goal": "Configure OSS"}})["messages"]
    assert "rules" in call(c, access, "get_schema", {"kind": "agents"})["properties"]
    connection = call(c, access, "create_connection", {"data": {"name": "Test", "api_key": "private-test-key", "type": "openai"}})
    assert "private-test-key" not in str(connection)
    agent = call(c, access, "create_agent", {"data": {"name": "oss-review", "model": "fixture-model",
        "connection_id": connection["id"], "instructions": "Base review", "trigger_access": "anyone"}})
    added = call(c, access, "add_rule", {"agent_id": agent["id"], "rule": {"name": "E2E", "instructions": "Require real proof"}})
    rule_id = added["rules"][0]["id"]
    updated = call(c, access, "update_agent", {"resource_id": agent["id"], "changes": {"review_mode": "incremental"}})
    assert updated["instructions"] == "Base review" and updated["rules"] == added["rules"]
    null = rpc(c, access, "tools/call", {"name": "litereview_update_agent", "arguments": {
        "resource_id": agent["id"], "changes": {"instructions": None}}})
    assert null["isError"]
    call(c, access, "update_agent", {"resource_id": agent["id"], "changes": {"maximum_minutes": None}})
    assert c.get("/api/resources/agents/" + agent["id"]).json()["review_mode"] == "incremental"
    conflict = rpc(c, access, "tools/call", {"name": "litereview_update_agent", "arguments": {
        "resource_id": agent["id"], "changes": {"instructions": "stale"}, "expected_updated_at": agent["updated_at"]}})
    assert conflict["isError"] and "409" in str(conflict)
    call(c, access, "update_rule", {"agent_id": agent["id"], "rule_id": rule_id, "changes": {"enabled": False}})
    assert not c.get("/api/resources/agents/" + agent["id"]).json()["rules"][0]["enabled"]
    call(c, access, "delete_rule", {"agent_id": agent["id"], "rule_id": rule_id})
    assert not c.get("/api/resources/agents/" + agent["id"]).json()["rules"]
    call(c, access, "update_connection", {"resource_id": connection["id"], "changes": {"name": "Renamed"}})
    with Session() as db:
        assert db.get(Resource, connection["id"]).secret
    call(c, access, "delete_resource", {"kind": "agents", "resource_id": agent["id"]})
    assert call(c, access, "list_resources", {"kind": "agents"})["total"] == 0


def test_mcp_admin_permissions_rechecked_and_user_can_revoke(mcp_client, monkeypatch):
    c = mcp_client
    issued, _ = token(c)
    access = issued["access_token"]
    assert rpc(c, access, "tools/call", {"name": "litereview_get_spend"})["isError"]
    with Session.begin() as db:
        db.add(WorkspaceAdmin(identity_sub="google-sub", granted_by="test"))
    assert not rpc(c, access, "tools/call", {"name": "litereview_get_spend"}).get("isError")
    with Session.begin() as db:
        db.delete(db.get(WorkspaceAdmin, "google-sub"))
    assert rpc(c, access, "tools/call", {"name": "litereview_get_spend"})["isError"]
    grant = c.get("/api/mcp/connections").json()["connections"][0]
    c.delete("/api/mcp/connections/" + grant["id"]).raise_for_status()
    denied = c.post("/api/mcp", json={}, headers={"Authorization": "Bearer " + access})
    assert denied.status_code == 401


def test_mcp_origin_host_resource_and_workspace_rechecked(mcp_client, monkeypatch):
    c = mcp_client
    issued, _ = token(c)
    access = issued["access_token"]
    for header in [{"Origin": "https://evil.example"}, {"Host": "evil.example"}]:
        denied = c.post("/api/mcp", json={}, headers={"Authorization": "Bearer " + access, **header})
        assert denied.status_code in (403, 421)
    with Session.begin() as db:
        grant = db.scalar(select(MCPGrant))
        grant.resource = "https://other.example/api/mcp"
    assert c.post("/api/mcp", json={}, headers={"Authorization": "Bearer " + access}).status_code == 401
    with Session.begin() as db:
        db.scalar(select(MCPGrant)).resource = URL + "/api/mcp"
    monkeypatch.setattr(settings(), "google_allowed_domains", "different.example")
    assert c.post("/api/mcp", json={}, headers={"Authorization": "Bearer " + access}).status_code == 401


def test_mcp_paginated_discovery_and_request_identity(mcp_client, monkeypatch):
    from test_reviews import mock_github, setup

    c = mcp_client
    _, _, agent, _, _ = setup(c)
    mock_github(monkeypatch)
    issued, _ = token(c)
    access = issued["access_token"]
    for name in ["alpha", "beta"]:
        call(c, access, "create_agent", {"data": {k: v for k, v in {**agent, "name": name}.items()
                                                 if k in Agent.model_fields}})
    first = call(c, access, "list_resources", {"kind": "agents", "limit": 1})
    second = call(c, access, "list_resources", {"kind": "agents", "limit": 1, "offset": first["next_offset"]})
    assert first["total"] == 3 and first["has_more"] and first["items"][0]["id"] != second["items"][0]["id"]
    args = {"repo": "owner/repo", "pr": 1, "reviewers": ["reviewer"], "request_id": "request-one"}
    run = call(c, access, "request_review", args)
    assert run["actor"] == "trusted" and len(run["jobs"]) == 1
    assert call(c, access, "request_review", args)["id"] == run["id"]
    assert call(c, access, "list_runs", {"repo": "owner/repo", "limit": 1})["total"] == 1


def test_token_binding_expiry_and_other_person_cannot_disconnect(mcp_client):
    c = mcp_client
    form = approve(c)
    other_client = register(c)
    assert c.post("/token", data={**form, "client_id": other_client["client_id"]}).status_code == 400
    issued = c.post("/token", data=form).json()
    access = issued["access_token"]
    grant = c.get("/api/mcp/connections").json()["connections"][0]
    identity(c, sub="second", email="second@berri.ai", session_id="second-session", github_id="43", github_login="second")
    c.delete("/api/mcp/connections/" + grant["id"]).raise_for_status()
    assert call(c, access, "get_workspace")["user"]["email"] == "member@berri.ai"
    with Session.begin() as db:
        db.get(MCPGrant, grant["id"]).expires_at = time.time() - 1
    assert c.post("/api/mcp", json={}, headers={"Authorization": "Bearer " + access}).status_code == 401


def test_invalid_token_body_returns_oauth_error(mcp_client):
    c = mcp_client
    for body, expected in [(b"\xff", 400), (b"a" * 65537, 413),
                           (("resource=" + URL + "/api/mcp&resource=https://other.example").encode(), 400)]:
        response = c.post("/token", content=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        assert response.status_code == expected
        assert response.json()["error"] in ("invalid_request", "invalid_target")


def test_screenshot_tool_returns_image_content(mcp_client):
    c = mcp_client
    issued, _ = token(c)
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aWZkAAAAASUVORK5CYII=")
    with Session.begin() as db:
        db.add(Artifact(id="screenshot", job_id="fixture", name="test", mime="image/png", content=png))
    result = rpc(c, issued["access_token"], "tools/call", {"name": "litereview_get_artifact", "arguments": {"artifact_id": "screenshot"}})
    assert not result.get("isError")
    assert result["content"][0]["type"] == "image"
    assert base64.b64decode(result["content"][0]["data"]) == png


def test_mcp_configures_independent_fusion_roles_and_decision_conditions(mcp_client):
    c = mcp_client
    issued, _ = token(c)
    access = issued['access_token']
    connection = call(c, access, 'create_connection', {'data': {'name': 'Review models', 'type': 'gateway'}})
    decision = call(c, access, 'create_connection', {'data': {'name': 'Decision models', 'type': 'gateway'}})
    skill = call(c, access, 'create_skill', {'data': {'name': 'drift', 'instructions': 'Look for existing helpers.'}})
    agent = call(c, access, 'create_agent', {'data': {
        'name': 'fusion-review', 'connection_id': connection['id'], 'model': 'final-model',
        'instructions': 'Verify and combine reports.', 'fusion': {'branches': [
            {'connection_id': connection['id'], 'model': 'correctness'},
            {'connection_id': connection['id'], 'model': 'drift', 'name': 'Drift and reuse',
             'instructions': 'Only inspect drift.', 'paths': ['src/**', 'lib/'], 'skill_ids': [skill['id']], 'rules': [], 'mcps': [],
             'condition': {'connection_id': decision['id'], 'model': 'jev-test', 'instructions': 'Does this PR change code?'}},
        ]}}})
    assert agent['instructions'] == 'Verify and combine reports.'
    branch = agent['fusion']['branches'][1]
    assert branch['instructions'] == 'Only inspect drift.' and branch['condition']['threshold'] == 0.5
    updated = call(c, access, 'update_agent', {'resource_id': agent['id'], 'expected_updated_at': agent['updated_at'],
                                              'changes': {'instructions': 'Updated final instructions.'}})
    assert updated['fusion'] == agent['fusion']
    assert call(c, access, 'get_resource', {'kind': 'agents', 'resource_id': agent['id']})['fusion'] == updated['fusion']
    schema = call(c, access, 'get_schema', {'kind': 'agents'})
    assert 'condition' in schema['$defs']['FusionBranch']['properties']
    assert 'paths' in schema['$defs']['FusionBranch']['properties']
    assert branch['paths'] == ['src/**', 'lib/']
    assert 'threshold' in schema['$defs']['BranchCondition']['properties']
    assert c.delete('/api/resources/connections/' + decision['id']).status_code == 409
    assert c.delete('/api/resources/skills/' + skill['id']).status_code == 409
