import base64
import hashlib
import hmac

from cryptography.fernet import Fernet
from fastapi import HTTPException, Request
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


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def verify_signature(body, signature, secret):
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(secret and signature and hmac.compare_digest(expected, signature))
