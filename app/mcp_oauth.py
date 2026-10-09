"""Durable MCP OAuth: discovery, PKCE, encrypted credentials and token refresh."""

import asyncio
import base64
import hashlib
import json
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    extract_resource_metadata_from_www_auth,
    extract_scope_from_www_auth,
    get_client_metadata_scopes,
    validate_metadata_issuer,
)
from mcp.shared.auth import OAuthMetadata, OAuthToken, ProtectedResourceMetadata
from mcp.shared.auth_utils import check_resource_allowed, resource_url_from_server_url
from sqlalchemy import delete, select

from .db import LoginState, MCPOAuth, Resource, Session, uid
from .security import decrypt, digest, encrypt, require_user
from .settings import settings

router = APIRouter()
# Serializes refreshes within an instance; database row locks cover rolling deployments.
refresh_lock = threading.Lock()


def callback_url():
    return settings().public_url + "/api/mcp/oauth/callback"


def secure_url(value):
    parsed = urlsplit(str(value))
    if (
        parsed.username
        or parsed.password
        or parsed.fragment
        or not parsed.hostname
        or not (
            parsed.scheme == "https"
            or parsed.scheme == "http"
            and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        )
    ):
        raise HTTPException(400, "OAuth endpoints must use HTTPS (or localhost for development).")
    return str(value)


def unpack(row):
    return json.loads(decrypt(row.secret)) if row and row.secret else {}


def current_connection(db, cid):
    row = db.get(Resource, cid, with_for_update=True)
    if not row or row.kind != "connections" or row.data["type"] != "mcp":
        raise HTTPException(404, "MCP connection not found.")
    if row.data.get("auth_type") != "oauth":
        raise HTTPException(409, "Choose OAuth for this MCP connection first.")
    return row


def status(cid):
    with Session() as db:
        data = unpack(db.get(MCPOAuth, cid))
    tokens = data.get("tokens", {})
    connected = bool(tokens.get("access_token"))
    if data.get("expires_at", 0) and data["expires_at"] <= time.time() and not tokens.get("refresh_token"):
        connected = False
    return {
        "connected": connected,
        "has_client_secret": bool(data.get("manual_secret")),
        "redirect_uri": callback_url(),
    }


def update_connection(db, cid, before, after, client_secret):
    """Invalidate authorization when its endpoint or client identity changes."""
    row = db.get(MCPOAuth, cid, with_for_update=True)
    keys = ("base_url", "type", "auth_type", "oauth_client_id", "oauth_scope")
    changed = any(
        (before or {}).get(k, "bearer" if k == "auth_type" else "") != after.get(k, "") for k in keys
    )
    if after["type"] != "mcp" or after.get("auth_type") != "oauth":
        if row:
            db.delete(row)
        return changed
    if row is None:
        row = MCPOAuth(connection_id=cid, revision=uid())
        db.add(row)
    data = unpack(row)
    if changed or client_secret:
        keep_secret = (
            data.get("manual_secret", "")
            if (before or {}).get("oauth_client_id") == after.get("oauth_client_id")
            and (before or {}).get("base_url") == after.get("base_url")
            else ""
        )
        data = {"manual_secret": client_secret or keep_secret}
        row.revision = uid()
    row.secret = encrypt(json.dumps(data))
    return changed or bool(client_secret)


def get_metadata(client, urls, schema):
    for url in dict.fromkeys(urls):
        response = client.get(secure_url(url))
        if response.status_code >= 500 or response.status_code == 429:
            raise HTTPException(502, "The OAuth server is temporarily unavailable. Try connecting again.")
        if response.status_code == 200:
            try:
                return schema.model_validate(response.json())
            except ValueError:
                pass
    return None


def prepare_authorization(cid, actor):
    with Session() as db:
        conn = current_connection(db, cid).data.copy()
        record = db.get(MCPOAuth, cid, with_for_update=True)
        if not record:
            record = MCPOAuth(connection_id=cid, revision=uid())
            db.add(record)
            db.commit()
        data, revision = unpack(record), record.revision
    endpoint = secure_url(conn["base_url"])
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        response = client.post(
            endpoint,
            headers={"Accept": "application/json, text/event-stream"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "LiteReview", "version": "1.0"},
                },
            },
        )
        prm = get_metadata(
            client,
            build_protected_resource_metadata_discovery_urls(
                extract_resource_metadata_from_www_auth(response), endpoint
            ),
            ProtectedResourceMetadata,
        )
        resource = resource_url_from_server_url(endpoint)
        if prm:
            if not check_resource_allowed(requested_resource=resource, configured_resource=str(prm.resource)):
                raise HTTPException(400, "OAuth metadata identifies a different MCP server.")
            resource = str(prm.resource)
        parts = urlsplit(endpoint)
        issuer = (
            str(prm.authorization_servers[0])
            if prm and prm.authorization_servers
            else f"{parts.scheme}://{parts.netloc}"
        )
        metadata = get_metadata(
            client, build_oauth_authorization_server_metadata_discovery_urls(issuer, endpoint), OAuthMetadata
        )
        if not metadata:
            raise HTTPException(
                400,
                "This server did not provide OAuth discovery metadata. Check its MCP URL, or choose Bearer token / No authentication.",
            )
        validate_metadata_issuer(metadata, issuer)
        authorization_endpoint = secure_url(metadata.authorization_endpoint)
        secure_url(metadata.token_endpoint)
        if (
            metadata.code_challenge_methods_supported is not None
            and "S256" not in metadata.code_challenge_methods_supported
        ):
            raise HTTPException(400, "This OAuth server does not support the required PKCE S256 method.")
        scope = conn.get("oauth_scope") or get_client_metadata_scopes(
            extract_scope_from_www_auth(response), prm, metadata
        )
        registration = (
            data.get("client", {})
            if data.get("issuer") == issuer and data.get("redirect_uri") == callback_url()
            else {}
        )
        if conn.get("oauth_client_id"):
            methods = metadata.token_endpoint_auth_methods_supported or ["client_secret_basic"]
            secret = data.get("manual_secret", "")
            method = (
                ("client_secret_post" if "client_secret_post" in methods else "client_secret_basic")
                if secret
                else "none"
            )
            registration = {
                "client_id": conn["oauth_client_id"],
                "client_secret": secret,
                "token_endpoint_auth_method": method,
            }
        if not registration:
            if not metadata.registration_endpoint:
                raise HTTPException(
                    400,
                    "This server requires a registered OAuth app. Open Advanced OAuth settings, enter its client ID, and register the displayed redirect URI.",
                )
            registered = client.post(
                secure_url(metadata.registration_endpoint),
                json={
                    "client_name": "LiteReview",
                    "redirect_uris": [callback_url()],
                    "grant_types": ["authorization_code", "refresh_token"],
                    "response_types": ["code"],
                    "token_endpoint_auth_method": "none",
                    **({"scope": scope} if scope else {}),
                },
            )
            if not registered.is_success:
                raise HTTPException(
                    400,
                    "OAuth app registration failed. Retry, or enter a client ID in Advanced OAuth settings.",
                )
            registration = registered.json()
            if not isinstance(registration.get("client_id"), str) or not registration["client_id"]:
                raise HTTPException(400, "The OAuth server did not return a client ID.")
        method = registration.get("token_endpoint_auth_method", "none")
        if method not in {"none", "client_secret_basic", "client_secret_post"}:
            raise HTTPException(400, "This OAuth app requires an unsupported token authentication method.")
        if method != "none" and not registration.get("client_secret"):
            raise HTTPException(
                400, "This OAuth app requires a client secret. Enter it in Advanced OAuth settings."
            )
        data.update(
            client=registration,
            issuer=issuer,
            token_endpoint=str(metadata.token_endpoint),
            resource=resource,
            scope=scope,
            redirect_uri=callback_url(),
            endpoint=endpoint,
        )
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with Session() as db:
        current_connection(db, cid)
        record = db.get(MCPOAuth, cid, with_for_update=True)
        if not record or record.revision != revision:
            raise HTTPException(409, "The connection changed. Retry OAuth from its current settings.")
        # Discovery must not overwrite tokens refreshed while the provider was contacted.
        current = unpack(record)
        for key in ("tokens", "expires_at"):
            if key in current:
                data[key] = current[key]
            else:
                data.pop(key, None)
        record.secret = encrypt(json.dumps(data))
        db.execute(delete(LoginState).where(LoginState.expires_at < time.time()))
        db.add(
            LoginState(
                state_hash=digest(state),
                browser_hash=digest(browser),
                expires_at=time.time() + 600,
                data={
                    "purpose": "mcp",
                    "connection_id": cid,
                    "revision": revision,
                    "actor": actor,
                    "secret": encrypt(json.dumps({**data, "verifier": verifier})),
                },
            )
        )
        db.commit()
    params = {
        "response_type": "code",
        "client_id": registration["client_id"],
        "redirect_uri": callback_url(),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": resource,
    }
    if scope:
        params["scope"] = scope
    return authorization_endpoint + ("&" if "?" in authorization_endpoint else "?") + urlencode(
        params
    ), browser


def token_request(client, data, params):
    registration = data["client"]
    params = {**params, "client_id": registration["client_id"], "resource": data["resource"]}
    method = registration.get("token_endpoint_auth_method", "none")
    auth = None
    if method == "client_secret_basic":
        from urllib.parse import quote

        auth = httpx.BasicAuth(
            quote(registration["client_id"], safe=""), quote(registration.get("client_secret", ""), safe="")
        )
    elif method == "client_secret_post":
        params["client_secret"] = registration.get("client_secret", "")
    response = client.post(secure_url(data["token_endpoint"]), data=params, auth=auth)
    if response.status_code >= 500 or response.status_code == 429:
        raise HTTPException(502, "The OAuth server is temporarily unavailable. Try again.")
    if not response.is_success:
        raise HTTPException(409, "OAuth authorization expired or was declined. Reconnect this MCP server.")
    try:
        token = OAuthToken.model_validate(response.json()).model_dump(exclude_none=True)
    except ValueError:
        raise HTTPException(400, "The OAuth server returned an invalid token response.") from None
    if token.get("token_type", "Bearer").lower() != "bearer":
        raise HTTPException(400, "This MCP connection requires a Bearer OAuth token.")
    if "refresh_token" not in token and params["grant_type"] == "refresh_token":
        token["refresh_token"] = data.get("tokens", {}).get("refresh_token")
    data.update(
        tokens=token,
        expires_at=time.time() + token["expires_in"] - 15 if token.get("expires_in") is not None else 0,
    )
    return data


def exchange_code(pending, code):
    with Session() as db:
        current_connection(db, pending["connection_id"])
        row = db.get(MCPOAuth, pending["connection_id"])
        if not row or row.revision != pending["revision"]:
            raise HTTPException(409, "The connection changed. Retry OAuth from its current settings.")
    data = json.loads(decrypt(pending["secret"]))
    with httpx.Client(timeout=30, follow_redirects=False) as client:
        data = token_request(
            client,
            data,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": data["redirect_uri"],
                "code_verifier": data.pop("verifier"),
            },
        )
    with Session() as db:
        conn = current_connection(db, pending["connection_id"])
        row = db.get(MCPOAuth, pending["connection_id"], with_for_update=True)
        if not row or row.revision != pending["revision"]:
            raise HTTPException(
                409, "The connection changed during sign-in. Reconnect it from its current settings."
            )
        row.secret = encrypt(json.dumps(data))
        # Only one of any concurrently opened sign-in attempts can finish.
        row.revision = uid()
        conn.data = {**conn.data, "credential_revision": uid(), "last_test": None}
        db.commit()


def access_token(cid, endpoint, rejected_token=None):
    with refresh_lock, Session() as db:
        conn = current_connection(db, cid)
        if conn.data["base_url"] != endpoint:
            raise HTTPException(
                409, "The MCP endpoint changed after this review started. Start a new review."
            )
        row = db.scalar(select(MCPOAuth).where(MCPOAuth.connection_id == cid).with_for_update())
        data = unpack(row)
        tokens = data.get("tokens", {})
        if not tokens.get("access_token") or data.get("endpoint") != endpoint:
            raise HTTPException(409, "Connect this MCP server with OAuth before using it.")
        expired = data.get("expires_at", 0) and data["expires_at"] <= time.time()
        rejected = rejected_token is not None and secrets.compare_digest(
            rejected_token, tokens["access_token"]
        )
        if expired or rejected:
            if not tokens.get("refresh_token"):
                raise HTTPException(409, "OAuth authorization expired. Reconnect this MCP server.")
            try:
                with httpx.Client(timeout=30, follow_redirects=False) as client:
                    data = token_request(
                        client,
                        data,
                        {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]},
                    )
            except HTTPException as exc:
                if exc.status_code != 502:
                    data.pop("tokens", None)
                    row.secret = encrypt(json.dumps(data))
                    db.commit()
                raise
            row.secret = encrypt(json.dumps(data))
            db.commit()
        return data["tokens"]["access_token"]


class MCPAuth(httpx.Auth):
    def __init__(self, cid, endpoint):
        self.cid, self.endpoint = cid, endpoint

    async def async_auth_flow(self, request):
        # Credentials only ever accompany the configured MCP endpoint.
        if request.url != httpx.URL(self.endpoint):
            raise HTTPException(
                409, "The MCP server redirected to a different endpoint. Update its URL and reconnect."
            )
        token = await asyncio.to_thread(access_token, self.cid, self.endpoint)
        request.headers["Authorization"] = "Bearer " + token
        response = yield request
        if response.status_code == 401:
            await response.aread()
            token = await asyncio.to_thread(access_token, self.cid, self.endpoint, token)
            request.headers["Authorization"] = "Bearer " + token
            yield request


@router.post("/api/connections/{cid}/oauth/start")
async def start(cid: str, request: Request, user=Depends(require_user)):
    try:
        url, browser = await asyncio.to_thread(prepare_authorization, cid, user.get("sub") or user["login"])
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "OAuth discovery failed. Check the server URL and retry.") from None
    from fastapi.responses import JSONResponse

    response = JSONResponse({"authorization_url": url})
    response.set_cookie(
        "lr_mcp_state",
        browser,
        max_age=600,
        httponly=True,
        secure=settings().public_url.startswith("https://"),
        samesite="lax",
        path="/api/mcp/oauth",
    )
    return response


@router.get("/api/mcp/oauth/callback")
async def callback(
    request: Request, state: str = "", code: str = "", error: str = "", user=Depends(require_user)
):
    browser = request.cookies.get("lr_mcp_state", "")
    if not state or len(state) > 200 or not browser:
        raise HTTPException(403, "Invalid OAuth state. Reconnect from LiteReview.")
    with Session() as db:
        pending = db.execute(
            delete(LoginState)
            .where(
                LoginState.state_hash == digest(state),
                LoginState.browser_hash == digest(browser),
                LoginState.expires_at > time.time(),
            )
            .returning(LoginState.data)
        ).scalar_one_or_none()
        db.commit()
    if (
        not pending
        or pending.get("purpose") != "mcp"
        or pending.get("actor") != (user.get("sub") or user["login"])
    ):
        raise HTTPException(403, "Invalid or expired OAuth state. Reconnect from LiteReview.")
    result = "denied" if error or not code else "connected"
    if result == "connected":
        try:
            await asyncio.to_thread(exchange_code, pending, code)
        except Exception:
            result = "failed"
    response = RedirectResponse(
        "/mcp?" + urlencode({"oauth": result, "connection": pending["connection_id"]}), status_code=303
    )
    response.delete_cookie("lr_mcp_state", path="/api/mcp/oauth")
    return response


@router.post("/api/connections/{cid}/oauth/disconnect", dependencies=[Depends(require_user)])
def disconnect(cid: str):
    with Session() as db:
        conn = current_connection(db, cid)
        row = db.get(MCPOAuth, cid, with_for_update=True)
        if row:
            data = unpack(row)
            data.pop("tokens", None)
            row.secret, row.revision = encrypt(json.dumps(data)), uid()
        conn.data = {**conn.data, "credential_revision": uid(), "last_test": None}
        db.commit()
    return {"ok": True}
