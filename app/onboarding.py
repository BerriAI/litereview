"""Role-aware readiness and idempotent installation synchronization."""

import asyncio

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from runner.catalog import HARNESS_VERSIONS, review_capabilities

from . import github, reviews
from .access import caller_grant, caller_permits, permits
from .db import Job, Resource, Run, Session
from .schemas import Repository
from .security import require_admin, require_user
from .settings import settings

router = APIRouter()
sync_lock = asyncio.Lock()


def excluded_repositories(db):
    row = db.get(Resource, "github-exclusions")
    return set(row.data.get("names", [])) if row else set()


def exclude_repository(db, name):
    row = db.get(Resource, "github-exclusions") or Resource(id="github-exclusions", kind="system", data={})
    row.data = {"names": sorted(excluded_repositories(db) | {name.lower()})}
    db.add(row)


def include_repository(db, name):
    row = db.get(Resource, "github-exclusions")
    if row:
        row.data = {"names": sorted(excluded_repositories(db) - {name.lower()})}


async def synchronize():
    async with sync_lock:
        generation = github.setup_state().get("generation", "")
        status = await github.connection_status()
        if status["state"] == "unconfigured":
            return {**status, "added": 0, "excluded": []}
        with Session() as db:
            saved = db.get(Resource, "github", with_for_update=True)
            state = saved.data if saved else {}
            if state.get("disabled") or state.get("generation", "") != generation:
                raise HTTPException(409, "GitHub setup changed. Check repository access again.")
            resources = db.scalars(select(Resource)).all()
            existing = {r.data["name"].lower(): r for r in resources if r.kind == "repos"}
            grants = {r.data["name"].lower() for r in resources if r.kind == "access"}
            excluded = excluded_repositories(db)
            added = 0
            for repo in status["repositories"]:
                name = repo["name"].lower()
                if name in excluded:
                    continue
                if name in existing:
                    row = existing[name]
                    if row.data["installation_id"] != repo["installation_id"]:
                        row.data = {**row.data, "installation_id": repo["installation_id"]}
                    # Do not recreate a deleted grant or replace an environment/secret.
                    continue
                row = Resource(kind="repos", data=Repository(**repo).model_dump(exclude={"test_secrets"}))
                db.add(row)
                existing[name] = row
                if name not in grants:
                    db.add(
                        Resource(
                            kind="access",
                            data={
                                "subject": "repo",
                                "name": name,
                                "scope": "all",
                                "target_ids": [],
                            },
                        )
                    )
                    grants.add(name)
                added += 1
            try:
                db.commit()
            except IntegrityError:
                db.rollback()
                raise HTTPException(409, "Repository selection changed. Try syncing again.") from None
        return {**status, "added": added, "excluded": sorted(excluded)}


@router.post("/api/github/sync", dependencies=[Depends(require_admin)])
async def sync_repositories():
    try:
        return await asyncio.wait_for(synchronize(), timeout=25)
    except (TimeoutError, httpx.RequestError):
        raise HTTPException(502, "GitHub is unavailable. Your saved setup is unchanged. Try again.") from None
    except (ValueError, jwt.InvalidKeyError):
        raise HTTPException(
            400, "The App credentials are invalid. Update them in Settings → GitHub App."
        ) from None


def workspace_readiness():
    with Session() as db:
        resources = {r.id: r for r in db.scalars(select(Resource)).all()}
        checks = db.execute(
            select(Job, Run.created_at)
            .join(Run, Job.run_id == Run.id)
            .where(Run.snapshot["harness_check"].as_boolean().is_(True))
            .order_by(Run.created_at.desc())
            .limit(200)
        ).all()
        has_review = (
            db.scalar(
                select(Run.id)
                .where(Run.status == "completed", Run.snapshot["harness_check"].as_boolean().is_not(True))
                .limit(1)
            )
            is not None
        )
    agents = []
    for row in resources.values():
        if row.kind != "agents":
            continue
        conn = resources.get(row.data["connection_id"])
        check = next(
            (
                job
                for job, created in checks
                if conn
                and created >= conn.updated_at
                and job.config.get("harness_version") == HARNESS_VERSIONS[row.data.get("harness", "builtin")]
                and all(
                    job.config.get(k, "builtin" if k == "harness" else None)
                    == (
                        review_capabilities(row.data.get("capabilities", []))
                        if k == "capabilities"
                        else row.data.get(k, "builtin" if k == "harness" else None)
                    )
                    for k in ("harness", "connection_id", "model", "capabilities")
                )
            ),
            None,
        )
        ready = bool(check and check.status == "completed" and not check.result.get("limitations"))
        agents.append(
            {
                "id": row.id,
                "name": row.data["name"],
                "checked": ready,
                "check_id": check.run_id if check else None,
                "check_status": check.status if check else None,
            }
        )
    s = settings()
    runtime = bool(
        s.modal_token_id
        and s.modal_token_secret
        and s.dispatcher_enabled
        and s.public_url.startswith("https://")
    )
    return {"agents": agents, "runtime": runtime, "has_review": has_review}, resources


@router.get("/api/workspace/readiness", dependencies=[Depends(require_admin)])
def readiness():
    return workspace_readiness()[0]


async def member_start(user):
    owner = next(iter(sorted(settings().google_admins)), "your workspace owner")
    result = {
        "owner": owner,
        "state": "github",
        "repositories": [],
        "max_minutes": settings().max_review_minutes,
    }
    if not github.config().get("client_id"):
        return {**result, "state": "workspace"}
    if not user.get("github_login"):
        return result
    readiness, resources = workspace_readiness()
    grants = {
        (r.data["subject"], r.data["name"].lower()): r for r in resources.values() if r.kind == "access"
    }
    caller = caller_grant(resources.values(), user["github_login"], user.get("sub"))
    if not caller and not settings().sso_required:
        return {**result, "state": "approval"}
    checked = {a["id"] for a in readiness["agents"] if a["checked"]}
    candidates = []
    for repo in resources.values():
        if repo.kind != "repos":
            continue
        grant = grants.get(("repo", repo.data["name"].lower()))
        targets = []
        for target in resources.values():
            if target.kind not in ("agents", "groups") or not (
                caller_permits(caller, target.id) and permits(grant, target.id)
            ):
                continue
            group = (
                target.data
                if target.kind == "groups"
                else {
                    "name": target.data["name"],
                    "members": [{"agent_id": target.id, "count": 1}],
                }
            )
            try:
                jobs = reviews.expand([group], resources)
            except (HTTPException, KeyError):
                continue
            if jobs and all(job["agent_id"] in checked for job in jobs):
                targets.append(target.data["name"])
        if targets:
            candidates.append((repo, sorted(targets)))
    if not candidates or not readiness["runtime"]:
        return {**result, "state": "reviewer"}
    try:
        available = {(r["name"].lower(), r["installation_id"]) for r in await github.installed_repositories()}
        for repo, targets in candidates:
            if (repo.data["name"].lower(), repo.data["installation_id"]) not in available:
                continue
            permission = await github.permission(
                repo.data["name"],
                repo.data["installation_id"],
                user["github_login"],
                expected_id=user.get("github_id"),
            )
            if permission in ("read", "triage", "write", "maintain", "admin"):
                result["repositories"].append({"name": repo.data["name"], "reviewers": targets})
    except (HTTPException, httpx.RequestError, ValueError):
        return {**result, "state": "unavailable", "repositories": []}
    return {**result, "state": "ready" if result["repositories"] else "reviewer"}


@router.get("/api/start")
async def start(request: Request):
    user = require_user(request)
    try:
        return await asyncio.wait_for(member_start(user), timeout=25)
    except TimeoutError:
        return {
            "owner": next(iter(sorted(settings().google_admins)), "your workspace owner"),
            "state": "unavailable",
            "repositories": [],
            "max_minutes": settings().max_review_minutes,
        }
