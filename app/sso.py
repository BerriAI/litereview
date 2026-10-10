"""Google Workspace authentication and verified GitHub account linking."""

import asyncio
import base64
import hashlib
import re
import secrets
import time
from urllib.parse import urlencode

import httpx
import jwt
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from . import github
from .access import bind_linked_person
from .db import LoginState, Session, SSOIdentity, SSOSession
from .security import digest, signed, unsign
from .settings import settings

router = APIRouter()
keys_client = jwt.PyJWKClient("https://www.googleapis.com/oauth2/v3/certs", lifespan=3600)
login_attempts = []


def return_path(value):
    # Only known in-app destinations; never pass an arbitrary OAuth redirect through.
    path = (value or "").split("?", 1)[0]
    if re.fullmatch(r"/runs/[a-f0-9]{32}", path):
        return path
    return (
        path
        if path
        in {
            "/",
            "/start",
            "/account",
            "/repositories",
            "/agents",
            "/groups",
            "/skills",
            "/mcp",
            "/models",
            "/settings",
            "/people",
            "/runs",
            "/connections",
            "/access",
        }
        else "/"
    )


def configured():
    s = settings()
    return bool(s.google_client_id and s.google_client_secret and s.google_domains)


def approved_identity(identity):
    """A verified workspace identity remains approved after its browser session ends."""
    s = settings()
    return bool(
        identity
        and identity.audience == s.google_client_id
        and identity.domain in s.google_domains
        and identity.email.rpartition("@")[2] == identity.domain
    )


def current_user(request):
    token = request.cookies.get("lr_session", "")
    if not token:
        raise HTTPException(401, "Sign in with your workspace Google account.")
    data = unsign(token, settings().sso_session_seconds)
    if data.get("auth") != "google":
        raise HTTPException(401, "Sign in with your workspace Google account.")
    with Session() as db:
        identity = db.get(SSOIdentity, data.get("sub", ""))
        browser = db.get(SSOSession, digest(data.get("session_id", "")))
    if (
        not configured()
        or not approved_identity(identity)
        or not browser
        or browser.identity_sub != identity.sub
        or browser.audience != settings().google_client_id
        or browser.expires_at <= time.time()
    ):
        raise HTTPException(401, "Your Google authorization expired. Sign in again.")
    return {
        "auth": "google",
        "sub": identity.sub,
        "email": identity.email,
        "login": identity.github_login or identity.email,
        "github_login": identity.github_login,
        "github_id": identity.github_id,
        "expires_at": browser.expires_at,
    }


def sign_out(request):
    """Revoke this browser's cookie without affecting other signed-in browsers."""
    try:
        data = unsign(request.cookies.get("lr_session", ""), settings().sso_session_seconds)
    except HTTPException:
        return
    if data.get("auth") == "google":
        with Session.begin() as db:
            db.execute(
                delete(SSOSession).where(
                    SSOSession.token_hash == digest(data.get("session_id", "")),
                    SSOSession.identity_sub == data.get("sub", ""),
                )
            )


def caller_identity(user):
    if not configured() or not user.get("id"):
        return None
    with Session() as db:
        identity = db.scalar(select(SSOIdentity).where(SSOIdentity.github_id == str(user["id"])))
    # Both stable ID and login must match: a renamed/recycled GitHub login must relink.
    return (
        identity
        if approved_identity(identity) and identity.github_login.lower() == user.get("login", "").lower()
        else None
    )


def eligible_caller(user):
    return caller_identity(user) is not None


def start_state(purpose, extra=None):
    now = time.monotonic()
    login_attempts[:] = [stamp for stamp in login_attempts if stamp > now - 60]
    if len(login_attempts) >= 60:
        raise HTTPException(429, "Too many sign-in attempts. Try again in a minute.")
    login_attempts.append(now)
    state, browser = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    data = {"purpose": purpose, **(extra or {})}
    with Session() as db:
        db.execute(delete(LoginState).where(LoginState.expires_at < time.time()))
        db.add(
            LoginState(
                state_hash=digest(state),
                browser_hash=digest(browser),
                expires_at=time.time() + 600,
                data=data,
            )
        )
        db.commit()
    return state, browser


def consume_state(request, state, purpose):
    browser = request.cookies.get("lr_sso_state", "")
    if not state or len(state) > 200 or not browser:
        raise HTTPException(403, "Invalid sign-in state.")
    with Session() as db:
        data = db.execute(
            delete(LoginState)
            .where(
                LoginState.state_hash == digest(state),
                LoginState.browser_hash == digest(browser),
                LoginState.expires_at > time.time(),
            )
            .returning(LoginState.data)
        ).scalar_one_or_none()
        db.commit()
    if not data or data.get("purpose") != purpose:
        raise HTTPException(403, "Invalid or expired sign-in state.")
    return data


def redirect(url, browser):
    response = RedirectResponse(url, status_code=303)
    response.set_cookie(
        "lr_sso_state",
        browser,
        max_age=600,
        httponly=True,
        secure=settings().public_url.startswith("https://"),
        samesite="lax",
        path="/api/auth",
    )
    return response


@router.get("/api/auth/google")
def google_start(next_path: str = Query("/", alias="next")):
    if not configured():
        raise HTTPException(503, "Google sign-in is not configured. Contact whoever manages this deployment.")
    nonce, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    state, browser = start_state(
        "google", {"nonce": nonce, "verifier": verifier, "next": return_path(next_path)}
    )
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    s = settings()
    return redirect(
        "https://accounts.google.com/o/oauth2/v2/auth?"
        + urlencode(
            {
                "client_id": s.google_client_id,
                "redirect_uri": s.public_url + "/api/auth/google/callback",
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
                "hd": next(iter(s.google_domains)) if len(s.google_domains) == 1 else "*",
            }
        ),
        browser,
    )


async def verify_google(token, nonce):
    if not isinstance(token, str) or len(token) > 16000:
        raise ValueError("Invalid identity token")
    header = jwt.get_unverified_header(token)
    if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str):
        raise ValueError("Invalid signing algorithm")
    key = await asyncio.to_thread(keys_client.get_signing_key_from_jwt, token)
    s = settings()
    claims = jwt.decode(
        token,
        key.key,
        algorithms=["RS256"],
        audience=s.google_client_id,
        issuer=["https://accounts.google.com", "accounts.google.com"],
        options={"require": ["exp", "iat", "iss", "aud", "sub", "nonce", "email", "email_verified", "hd"]},
    )
    if not isinstance(claims["nonce"], str) or not secrets.compare_digest(claims["nonce"], nonce):
        raise ValueError("Invalid nonce")
    if claims.get("azp", s.google_client_id) != s.google_client_id:
        raise ValueError("Invalid authorized party")
    if isinstance(claims["aud"], list) and len(claims["aud"]) > 1 and "azp" not in claims:
        raise ValueError("Missing authorized party")
    email, domain, sub = claims["email"], claims["hd"], claims["sub"]
    if (
        claims["email_verified"] is not True
        or not isinstance(email, str)
        or not isinstance(domain, str)
        or not isinstance(sub, str)
        or not sub
        or domain.lower() not in s.google_domains
        or email.lower().rpartition("@")[2] != domain.lower()
    ):
        raise ValueError("Not a workspace identity")
    return {"sub": sub, "email": email.lower(), "domain": domain.lower()}


@router.get("/api/auth/google/callback")
async def google_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    if not configured():
        raise HTTPException(503, "Google sign-in is not configured.")
    data = consume_state(request, state, "google")
    if error or not code or len(code) > 4096:
        raise HTTPException(400, "Google sign-in was not completed.")
    s = settings()
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": s.google_client_id,
                    "client_secret": s.google_client_secret,
                    "redirect_uri": s.public_url + "/api/auth/google/callback",
                    "code_verifier": data["verifier"],
                },
            )
            response.raise_for_status()
        claims = await verify_google(response.json().get("id_token"), data["nonce"])
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError, TypeError):
        raise HTTPException(
            403, "Google sign-in failed or this account is outside the allowed workspace."
        ) from None
    session_id = secrets.token_urlsafe(32)
    expires_at = time.time() + s.sso_session_seconds
    with Session() as db:
        identity = db.get(SSOIdentity, claims["sub"]) or SSOIdentity(sub=claims["sub"])
        identity.email, identity.domain = claims["email"], claims["domain"]
        identity.audience = s.google_client_id
        db.add(identity)
        db.execute(delete(SSOSession).where(SSOSession.expires_at <= time.time()))
        db.add(
            SSOSession(
                token_hash=digest(session_id),
                identity_sub=identity.sub,
                audience=s.google_client_id,
                expires_at=expires_at,
            )
        )
        db.commit()
    response = RedirectResponse(return_path(data.get("next")), status_code=303)
    response.delete_cookie("lr_sso_state", path="/api/auth")
    response.set_cookie(
        "lr_session",
        signed({"auth": "google", "sub": identity.sub, "session_id": session_id}),
        max_age=s.sso_session_seconds,
        httponly=True,
        secure=s.public_url.startswith("https://"),
        samesite="lax",
    )
    return response


def github_start(request):
    user = current_user(request)
    conf = github.config()
    if not conf.get("client_id"):
        raise HTTPException(409, "Connect the GitHub App in Repositories before linking your account.")
    state, browser = start_state(
        "github-link", {"sub": user["sub"], "next": return_path(request.query_params.get("next", "/start"))}
    )
    return redirect(
        "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": conf["client_id"],
                "state": state,
                "redirect_uri": settings().public_url + "/api/auth/github/callback",
            }
        ),
        browser,
    )


async def github_callback(request, code, state):
    user = current_user(request)
    data = consume_state(request, state, "github-link")
    if data["sub"] != user["sub"]:
        raise HTTPException(403, "Sign-in identity changed. Link GitHub again.")
    conf = github.config()
    try:
        async with httpx.AsyncClient(timeout=None) as client:
            response = await client.post(
                "https://github.com/login/oauth/access_token",
                headers={"Accept": "application/json"},
                json={
                    "client_id": conf["client_id"],
                    "client_secret": conf["client_secret"],
                    "code": code,
                    "redirect_uri": settings().public_url + "/api/auth/github/callback",
                },
            )
            response.raise_for_status()
        token = response.json().get("access_token")
        if not token:
            raise ValueError("No token")
        account = (await github.request("GET", "/user", token)).json()
        if not account.get("id") or not account.get("login"):
            raise ValueError("No account")
    except (httpx.HTTPError, ValueError, KeyError):
        raise HTTPException(400, "GitHub linking failed. Try again.") from None
    with Session() as db:
        identity = db.get(SSOIdentity, user["sub"])
        identity.github_id, identity.github_login = str(account["id"]), account["login"]
        try:
            bind_linked_person(db, identity)
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(
                409, "This GitHub account is already linked to another Google identity."
            ) from None
    response = RedirectResponse(return_path(data.get("next", "/start")), status_code=303)
    response.delete_cookie("lr_sso_state", path="/api/auth")
    return response


@router.delete("/api/auth/github/link")
def unlink_github(request: Request):
    user = current_user(request)
    with Session() as db:
        identity = db.get(SSOIdentity, user["sub"])
        bind_linked_person(db, identity)
        identity.github_id, identity.github_login = None, ""
        db.commit()
    return {"ok": True}
