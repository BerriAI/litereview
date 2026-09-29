"""Workspace-wide caller and repository grants for PR review commands."""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .db import Resource, Session
from .settings import settings


def permits(grant, target_id):
    return bool(grant and (grant.data["scope"] == "all" or target_id in grant.data["target_ids"]))


def initialize_access():
    """Migrate explicit grants once; never recreate access an admin revoked."""
    with Session() as db:
        if db.get(Resource, "access-v1"):
            return
        resources = db.scalars(select(Resource)).all()
        targets = [row for row in resources if row.kind in ("agents", "groups")]
        users, repos = {}, {}
        for target in targets:
            for repo in target.data.get("repos", []):
                repos.setdefault(repo.lower(), set()).add(target.id)
            if target.data.get("caller_policy") == "allowlist":
                for user in target.data.get("callers", []):
                    if user.strip():
                        users.setdefault(user.strip().lower(), set()).add(target.id)
            target.data = {
                k: v for k, v in target.data.items() if k not in ("repos", "caller_policy", "callers")
            }
        for user in settings().admins:
            users.setdefault(user, set())
        for repo in (row for row in resources if row.kind == "repos"):
            repos.setdefault(repo.data["name"].lower(), set())
        existing = {(r.data["subject"], r.data["name"].lower()) for r in resources if r.kind == "access"}
        for subject, grants in [("user", users), ("repo", repos)]:
            for name, ids in grants.items():
                if (subject, name) not in existing:
                    all_access = subject == "user" and name in settings().admins
                    db.add(
                        Resource(
                            kind="access",
                            data={
                                "subject": subject,
                                "name": name,
                                "scope": "all" if all_access else "selected",
                                "target_ids": [] if all_access else sorted(ids),
                            },
                        )
                    )
        db.add(Resource(id="access-v1", kind="system", data={}))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            if not db.get(Resource, "access-v1"):
                raise
