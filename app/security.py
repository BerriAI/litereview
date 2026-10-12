import base64
import hashlib
import hmac

from cryptography.fernet import Fernet
from fastapi import Depends, HTTPException, Request
from itsdangerous import BadSignature, URLSafeTimedSerializer

from .settings import settings


def cipher():
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(settings().secret_key.encode()).digest()))


def encrypt(value):
    return cipher().encrypt(value.encode()).decode() if value else ""


def decrypt(value):
    return cipher().decrypt(value.encode()).decode() if value else ""


def serializer():
    return URLSafeTimedSerializer(settings().secret_key, salt="litereview-session")


def signed(value):
    return serializer().dumps(value)


def unsign(value, age=86400):
    try:
        return serializer().loads(value, max_age=age)
    except BadSignature:
        raise HTTPException(401, "Please sign in again.")


def require_user(request: Request):
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        from .mcp_auth import bearer_user
        return bearer_user(authorization[7:])
    if settings().sso_required:
        from .sso import current_user

        return current_user(request)
    token = request.cookies.get("lr_session", "")
    if not token:
        raise HTTPException(401, "Sign in to LiteReview.")
    user = unsign(token)
    if user.get("login") != "owner" and user.get("login", "").lower() not in settings().admins:
        raise HTTPException(403, "This account is not approved for this workspace.")
    return user


def is_admin(user):
    """Recheck configuration and saved roles for each authenticated request."""
    if not user:
        return False
    if settings().sso_required:
        if user.get("auth") != "google":
            return False
        if user.get("email", "").lower() in settings().spend_admin_emails:
            return True
        from .db import Session, WorkspaceAdmin

        with Session() as db:
            return db.get(WorkspaceAdmin, user.get("sub", "")) is not None
    return user.get("login") == "owner" or user.get("login", "").lower() in settings().admins


def require_admin(user=Depends(require_user)):
    if not is_admin(user):
        raise HTTPException(403, "This action is available to workspace admins only.")
    return user


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_signature(body, signature, secret):
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(secret and signature and hmac.compare_digest(expected, signature))
