"""MCP OAuth delegates login to existing Google SSO, never Google tokens to clients."""

import html
import json
import secrets
import time
from urllib.parse import urlencode, urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from sqlalchemy import delete, select, update

from .db import MCPAuthorization, MCPClient, MCPGrant, MCPToken, Session, SSOIdentity, uid
from .security import decrypt, digest, encrypt, signed, unsign
from .settings import settings
from .sso import approved_identity, configured, current_user

router = APIRouter()
SCOPE = "litereview"
ACCESS_SECONDS = 3600
GRANT_SECONDS = 30 * 86400
REFRESH_RETRY_SECONDS = 60


def endpoint():
    return settings().public_url + "/api/mcp"


def unavailable_reason():
    if not configured():
        return "Configure Google Workspace sign-in to connect an AI assistant."
    parsed = urlparse(settings().public_url)
    if (parsed.query or parsed.fragment or parsed.path not in ("", "/") or parsed.username or parsed.password
            or not (parsed.scheme == "https" or parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))):
        return "Use an HTTPS PUBLIC_URL (or localhost for development) to enable the remote MCP."
    return ""


def grant_identity(db, grant):
    identity = db.get(SSOIdentity, grant.identity_sub) if grant else None
    if (not configured() or not grant or grant.revoked or grant.expires_at <= time.time()
            or grant.audience != settings().google_client_id or grant.resource != endpoint()
            or SCOPE not in grant.scopes or not approved_identity(identity)):
        return None
    return identity


def access_token(raw):
    with Session() as db:
        token = db.get(MCPToken, digest(raw))
        if not token or token.kind != "access" or token.used or token.expires_at <= time.time():
            return None
        grant = db.get(MCPGrant, token.grant_id)
        identity = grant_identity(db, grant)
        if not identity:
            return None
        return AccessToken(token=raw, client_id=grant.client_id, scopes=grant.scopes,
                           expires_at=int(token.expires_at), resource=grant.resource, subject=identity.sub)


def bearer_user(raw):
    token = access_token(raw)
    if not token:
        raise HTTPException(401, "MCP authorization expired or was revoked. Reconnect and sign in with Google.")
    with Session() as db:
        identity = db.get(SSOIdentity, token.subject)
        return {"auth": "google", "sub": identity.sub, "email": identity.email,
                "login": identity.github_login or identity.email, "github_login": identity.github_login,
                "github_id": identity.github_id, "expires_at": token.expires_at}


def issue_tokens(db, grant):
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for kind, raw, expires in [("access", access, min(time.time() + ACCESS_SECONDS, grant.expires_at)),
                               ("refresh", refresh, grant.expires_at)]:
        db.add(MCPToken(token_hash=digest(raw), grant_id=grant.id, kind=kind, expires_at=expires))
    return OAuthToken(access_token=access, refresh_token=refresh, token_type="Bearer",
                      expires_in=int(min(ACCESS_SECONDS, grant.expires_at - time.time())), scope=" ".join(grant.scopes))


def refresh_result(db, token_hash, client_id):
    """Retry a lost/concurrent refresh response without rotating or revoking again."""
    cached = db.get(MCPAuthorization, token_hash)
    if (not cached or cached.kind != "refresh_retry" or cached.client_id != client_id
            or cached.expires_at <= time.time()):
        return None
    data = json.loads(decrypt(cached.data["response"]))
    data["expires_in"] = max(1, data["expires_in"] - int(time.time() - cached.data["issued_at"]))
    return OAuthToken(**data)


class Provider:
    async def verify_token(self, token):
        return access_token(token)

    async def get_client(self, client_id):
        with Session() as db:
            row = db.get(MCPClient, client_id)
            return OAuthClientInformationFull(**row.data, client_secret=decrypt(row.secret) or None) if row else None

    async def register_client(self, client_info):
        if not configured():
            raise RegistrationError("invalid_client_metadata", "Configure Google Workspace sign-in in LiteReview first.")
        for uri in client_info.redirect_uris or []:
            parsed = urlparse(str(uri))
            if (parsed.fragment or parsed.username or parsed.password or not parsed.hostname
                    or not (parsed.scheme == "https" or parsed.scheme == "http"
                            and parsed.hostname in ("localhost", "127.0.0.1", "::1"))):
                raise RegistrationError("invalid_redirect_uri", "Use an HTTPS callback or a loopback HTTP callback.")
        with Session.begin() as db:
            # Bound unauthenticated registration bursts without limiting established clients.
            recent = db.scalars(select(MCPClient.id).where(MCPClient.created_at > time.time() - 60).limit(60)).all()
            if len(recent) >= 60:
                raise RegistrationError("invalid_client_metadata", "Too many registrations. Try again in a minute.")
            db.add(MCPClient(id=client_info.client_id,
                             data=client_info.model_dump(mode="json", exclude={"client_secret"}),
                             secret=encrypt(client_info.client_secret or "")))

    async def authorize(self, client, params):
        if not configured():
            raise AuthorizeError("access_denied", "Google Workspace sign-in is not configured.")
        if params.resource not in (None, endpoint()):
            raise AuthorizeError("invalid_request", "The resource must be the LiteReview MCP URL.")
        if set(params.scopes or [SCOPE]) != {SCOPE}:
            raise AuthorizeError("invalid_scope", "Request the litereview scope.")
        ticket = secrets.token_urlsafe(32)
        with Session.begin() as db:
            db.execute(delete(MCPAuthorization).where(MCPAuthorization.expires_at <= time.time()))
            db.add(MCPAuthorization(key=digest(ticket), kind="pending", client_id=client.client_id,
                                    data=params.model_dump(mode="json"), expires_at=time.time() + 600))
        return settings().public_url + "/api/mcp/authorize/" + ticket

    async def load_authorization_code(self, client, authorization_code):
        with Session() as db:
            row = db.get(MCPAuthorization, digest(authorization_code))
            if not row or row.kind != "code" or row.client_id != client.client_id or row.expires_at <= time.time():
                return None
            return AuthorizationCode(code=authorization_code, client_id=row.client_id,
                                     expires_at=row.expires_at, **row.data)

    async def exchange_authorization_code(self, client, authorization_code):
        with Session.begin() as db:
            data = db.execute(delete(MCPAuthorization).where(
                MCPAuthorization.key == digest(authorization_code.code), MCPAuthorization.kind == "code",
                MCPAuthorization.client_id == client.client_id, MCPAuthorization.expires_at > time.time()
            ).returning(MCPAuthorization.data)).scalar_one_or_none()
            if not data or not approved_identity(db.get(SSOIdentity, data["subject"])):
                raise TokenError("invalid_grant", "Authorization expired or was already used. Reconnect.")
            grant = MCPGrant(id=uid(), identity_sub=data["subject"], client_id=client.client_id,
                             scopes=data["scopes"], resource=data["resource"], audience=settings().google_client_id,
                             expires_at=time.time() + GRANT_SECONDS)
            db.add(grant)
            return issue_tokens(db, grant)

    async def load_refresh_token(self, client, refresh_token):
        with Session.begin() as db:
            row = db.get(MCPToken, digest(refresh_token))
            grant = db.get(MCPGrant, row.grant_id) if row else None
            if not row or row.kind != "refresh" or not grant or grant.client_id != client.client_id:
                return None
            if row.expires_at <= time.time() or not grant_identity(db, grant):
                return None
            if row.used and not refresh_result(db, row.token_hash, client.client_id):
                grant.revoked = True  # Replay outside the bounded retry window revokes the grant.
                return None
            return RefreshToken(token=refresh_token, client_id=client.client_id, scopes=grant.scopes,
                                expires_at=int(row.expires_at), resource=grant.resource, subject=grant.identity_sub)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        failed = False
        with Session.begin() as db:
            row = db.get(MCPToken, digest(refresh_token.token))
            grant = db.get(MCPGrant, row.grant_id, with_for_update=True) if row else None
            if (not row or row.kind != "refresh" or not grant_identity(db, grant) or grant.client_id != client.client_id
                    or set(scopes) != {SCOPE}):
                raise TokenError("invalid_grant", "Reconnect to LiteReview.")
            consumed = db.execute(update(MCPToken).where(MCPToken.token_hash == row.token_hash,
                                  MCPToken.used.is_(False), MCPToken.expires_at > time.time())
                                  .values(used=True).returning(MCPToken.token_hash)).scalar_one_or_none()
            if not consumed:
                result = refresh_result(db, row.token_hash, client.client_id)
                if result is None:
                    grant.revoked, failed = True, True
            else:
                # Existing access tokens remain valid until their original expiry.
                # In-flight requests may still use one while the client refreshes.
                result = issue_tokens(db, grant)
                db.execute(delete(MCPAuthorization).where(MCPAuthorization.kind == "refresh_retry",
                                                          MCPAuthorization.expires_at <= time.time()))
                db.add(MCPAuthorization(key=row.token_hash, kind="refresh_retry", client_id=client.client_id,
                    expires_at=time.time() + REFRESH_RETRY_SECONDS,
                    data={"issued_at": time.time(), "response": encrypt(result.model_dump_json())}))
        if failed:
            raise TokenError("invalid_grant", "Refresh token was already used. Reconnect to LiteReview.")
        return result

    async def load_access_token(self, token):
        return access_token(token)

    async def revoke_token(self, token):
        with Session.begin() as db:
            row = db.get(MCPToken, digest(token.token))
            if row:
                db.execute(update(MCPGrant).where(MCPGrant.id == row.grant_id).values(revoked=True))


provider = Provider()


@router.get("/api/mcp/authorize/{ticket}")
async def consent(ticket: str, request: Request):
    with Session() as db:
        pending = db.get(MCPAuthorization, digest(ticket))
        client = db.get(MCPClient, pending.client_id) if pending else None
        if not pending or pending.kind != "pending" or pending.expires_at <= time.time() or not client:
            raise HTTPException(400, "This connection request expired. Connect again from your MCP client.")
    try:
        user = current_user(request)
    except HTTPException:
        return RedirectResponse("/api/auth/google?" + urlencode({"next": "/api/mcp/authorize/" + ticket}), 303)
    browser = secrets.token_urlsafe(32)
    csrf = signed({"purpose": "mcp-consent", "ticket": digest(ticket), "sub": user["sub"], "browser": digest(browser)})
    name = html.escape(client.data.get("client_name") or "MCP client")
    redirect = html.escape(pending.data["redirect_uri"])
    response = HTMLResponse(f"""<!doctype html><html lang="en"><head><meta name="viewport" content="width=device-width,initial-scale=1">
    <title>Connect to LiteReview</title><style>
    body{{font:15px/1.6 system-ui;color:#272b37;margin:40px auto;padding:24px;max-width:560px}}
    header{{display:flex;align-items:center;gap:10px;margin-bottom:32px}}header img{{width:32px;height:32px;object-fit:contain}}
    h1{{font-size:25px;line-height:1.3;font-weight:500;overflow-wrap:anywhere}}small{{overflow-wrap:anywhere;color:#626978}}
    button{{font:inherit;padding:9px 18px;margin-right:12px;border:1px solid #d8dbe2;border-radius:6px;background:white;cursor:pointer}}
    button[value=allow]{{background:#5b3fd1;border-color:#5b3fd1;color:white}}button:focus-visible{{outline:2px solid #5b3fd1;outline-offset:3px}}
    </style></head><body><header><img src="/brand/litereview.png" alt="">LiteReview</header>
    <main><h1>Connect {name} to LiteReview</h1><p>Signed in as {html.escape(user['email'])}.</p>
    <p>This client can read reviews and manage agents, rules, skills, connections, repository settings and People with your workspace permissions. It can start paid model checks and reviews. Admin actions and spend require your admin role.</p>
    <p>Only connect a client you trust. You can revoke access in Settings → Connect an AI assistant.</p>
    <p><small>Client-provided callback: {redirect}</small></p><form method="post"><input type="hidden" name="csrf" value="{html.escape(csrf)}">
    <button name="decision" value="allow">Connect</button><button name="decision" value="deny">Cancel</button></form></main></body></html>""")
    response.set_cookie("lr_mcp_consent", browser, httponly=True, secure=settings().public_url.startswith("https"),
                        samesite="lax", max_age=600, path="/api/mcp/authorize")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/api/mcp/authorize/{ticket}")
async def confirm_consent(ticket: str, request: Request):
    user = current_user(request)
    form = await request.form()
    csrf = unsign(form.get("csrf", ""), 600)
    if csrf != {"purpose": "mcp-consent", "ticket": digest(ticket), "sub": user["sub"],
                "browser": digest(request.cookies.get("lr_mcp_consent", ""))}:
        raise HTTPException(403, "Connection approval expired. Connect again from your MCP client.")
    with Session.begin() as db:
        pending = db.execute(delete(MCPAuthorization).where(
            MCPAuthorization.key == digest(ticket), MCPAuthorization.kind == "pending",
            MCPAuthorization.expires_at > time.time()).returning(MCPAuthorization.client_id, MCPAuthorization.data)).first()
        if not pending:
            raise HTTPException(400, "This connection request expired or was already used.")
        params = AuthorizationParams(**pending.data)
        if form.get("decision") == "allow":
            code = secrets.token_urlsafe(32)
            db.add(MCPAuthorization(key=digest(code), kind="code", client_id=pending.client_id,
                expires_at=time.time() + 120, data={"scopes": params.scopes or [SCOPE], "code_challenge": params.code_challenge,
                "redirect_uri": str(params.redirect_uri), "redirect_uri_provided_explicitly": params.redirect_uri_provided_explicitly,
                "resource": endpoint(), "subject": user["sub"]}))
            url = construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)
        else:
            url = construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)
    response = RedirectResponse(url, 303)
    response.delete_cookie("lr_mcp_consent", path="/api/mcp/authorize")
    response.headers["Cache-Control"] = "no-store"
    return response


@router.get("/api/mcp/connections")
def connections(request: Request):
    from .security import require_user
    user = require_user(request)
    with Session() as db:
        rows = db.execute(select(MCPGrant, MCPClient).join(MCPClient, MCPGrant.client_id == MCPClient.id)
                          .where(MCPGrant.identity_sub == user.get("sub"), MCPGrant.revoked.is_(False),
                                 MCPGrant.expires_at > time.time()).order_by(MCPGrant.created_at.desc()))
        return {"url": endpoint(), "enabled": not unavailable_reason(), "reason": unavailable_reason(), "connections": [
            {"id": grant.id, "name": client.data.get("client_name") or "MCP client", "created_at": grant.created_at,
             "expires_at": grant.expires_at} for grant, client in rows]}


@router.delete("/api/mcp/connections/{grant_id}")
def disconnect(grant_id: str, request: Request):
    from .security import require_user
    user = require_user(request)
    with Session.begin() as db:
        db.execute(update(MCPGrant).where(MCPGrant.id == grant_id, MCPGrant.identity_sub == user.get("sub"))
                   .values(revoked=True))
    return {"ok": True}
