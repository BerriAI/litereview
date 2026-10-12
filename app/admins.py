"""Admin roles are separate from the review grants editable by workspace members."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from .db import Session, SSOIdentity, WorkspaceAdmin
from .security import require_admin
from .settings import settings
from .sso import approved_identity

router = APIRouter()


class PersonRole(BaseModel):
    role: Literal["admin", "member"]


@router.put("/api/people/{identity_sub}/role")
def set_role(identity_sub: str, data: PersonRole, user=Depends(require_admin)):
    if not settings().sso_required:
        raise HTTPException(400, "People roles require Google sign-in.")
    with Session() as db:
        identity = db.get(SSOIdentity, identity_sub, with_for_update=True)
        if not identity:
            raise HTTPException(404, "This person no longer exists. Reload People.")
        if data.role == "admin" and not approved_identity(identity):
            raise HTTPException(409, "This person must sign in with an approved Google account first.")
        configured = identity.email.lower() in settings().spend_admin_emails
        if data.role == "member":
            if configured:
                raise HTTPException(409, "This admin is managed by the deployment's ADMIN_EMAILS setting.")
            if identity.sub == user["sub"]:
                raise HTTPException(409, "Ask another admin to remove your admin role.")
        grant = db.get(WorkspaceAdmin, identity.sub)
        if data.role == "admin" and not configured and not grant:
            db.add(WorkspaceAdmin(identity_sub=identity.sub, granted_by=user["sub"]))
        elif data.role == "member" and grant:
            db.delete(grant)
        try:
            db.commit()
        except IntegrityError:
            # Concurrent promotions and retries grant the role only once.
            db.rollback()
            if data.role != "admin" or not db.get(WorkspaceAdmin, identity.sub):
                raise
        return {"identity_sub": identity.sub, "is_admin": data.role == "admin",
                "admin_source": "configuration" if configured else "people" if data.role == "admin" else None}
