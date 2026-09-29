import asyncio
import base64
import io
import json
import secrets
import time
import zipfile
from contextlib import asynccontextmanager, suppress
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode

import httpx
from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import ValidationError
from sqlalchemy import func, select, update

from . import github, providers, reviews
from .db import Artifact, Job, Resource, Run, Session, init_db, uid
from .schemas import SCHEMAS, ReviewResult
from .security import decrypt, digest, encrypt, require_admin, signed, unsign, verify_signature
from .settings import settings


@asynccontextmanager
async def lifespan(app):
    if len(settings().secret_key) < 32 or len(settings().admin_token) < 20:
        raise RuntimeError(
            "Set SECRET_KEY (32+ characters) and ADMIN_TOKEN (20+ characters). See .env.example."
        )
    init_db()
    # Optional first-deploy model bootstrap; subsequent edits belong to the UI.
    with Session() as db:
        s = settings()
        for kind, key, url in [
            ("gateway", s.gateway_api_key, s.gateway_url),
            ("openai", s.openai_api_key, ""),
            ("anthropic", s.anthropic_api_key, ""),
        ]:
            rid = "initial-" + kind
            if key and (kind != "gateway" or url) and not db.get(Resource, rid):
                db.add(
                    Resource(
                        id=rid,
                        kind="connections",
                        data={
                            "name": "Default " + kind,
                            "type": kind,
                            "base_url": url.rstrip("/"),
                            "tools": [],
                        },
                        secret=encrypt(key),
                    )
                )
        db.commit()
    task = asyncio.create_task(reviews.dispatcher()) if settings().dispatcher_enabled else None
    yield
    if task:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(title="LiteReview", lifespan=lifespan, docs_url=None, redoc_url=None)


@app.middleware("http")
async def guard(request, call_next):
    if int(request.headers.get("content-length", "0")) > 12_000_000:
        return JSONResponse({"detail": "Request exceeds 12 MB."}, status_code=413)
    if request.method not in ("GET", "HEAD", "OPTIONS") and not request.url.path.startswith(
        ("/api/webhook", "/api/internal/")
    ):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != settings().public_url.rstrip("/"):
            return JSONResponse({"detail": "Request origin does not match PUBLIC_URL."}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    with Session() as db:
        db.scalar(select(func.count()).select_from(Resource))
    return {"status": "ok"}


@app.get("/api/session")
def session(request: Request):
    try:
        user = require_admin(request)
    except HTTPException:
        user = None
    return {"user": user, "github_login": bool(github.config().get("client_id"))}


login_attempts = {}


@app.post("/api/login")
async def login(request: Request):
    host = request.client.host
    recent = [t for t in login_attempts.get(host, []) if t > time.time() - 900]
    if len(recent) >= 20:
        raise HTTPException(429, "Too many sign-in attempts. Try again in 15 minutes.")
    login_attempts[host] = recent + [time.time()]
    body = await request.json()
    if not secrets.compare_digest(str(body.get("token", "")), settings().admin_token):
        raise HTTPException(401, "The workspace access key is incorrect.")
    login_attempts.pop(host, None)
    response = JSONResponse({"ok": True})
    response.set_cookie(
        "lr_session",
        signed({"login": "owner"}),
        httponly=True,
        secure=settings().public_url.startswith("https"),
        samesite="lax",
        max_age=86400,
    )
    return response


@app.post("/api/logout")
def logout():
    response = JSONResponse({"ok": True})
    response.delete_cookie("lr_session")
    return response


@app.get("/api/auth/github")
def oauth_start():
    conf = github.config()
    if not conf.get("client_id"):
        raise HTTPException(400, "Connect a GitHub App first.")
    state = signed({"nonce": secrets.token_urlsafe(24), "purpose": "oauth"})
    response = RedirectResponse(
        "https://github.com/login/oauth/authorize?"
        + urlencode(
            {
                "client_id": conf["client_id"],
                "redirect_uri": settings().public_url + "/api/auth/github/callback",
                "state": state,
            }
        )
    )
    response.set_cookie(
        "lr_oauth",
        state,
        httponly=True,
        secure=settings().public_url.startswith("https"),
        samesite="lax",
        max_age=600,
    )
    return response


@app.get("/api/auth/github/callback")
async def oauth_callback(request: Request, code: str, state: str):
    if (
        not secrets.compare_digest(state, request.cookies.get("lr_oauth", ""))
        or unsign(state, 600).get("purpose") != "oauth"
    ):
        raise HTTPException(403, "Invalid sign-in state.")
    conf = github.config()
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://github.com/login/oauth/access_token",
            headers={"Accept": "application/json"},
            json={"client_id": conf["client_id"], "client_secret": conf["client_secret"], "code": code},
        )
    auth = response.json().get("access_token")
    if not auth:
        raise HTTPException(400, "GitHub sign-in failed.")
    user = (await github.request("GET", "/user", auth)).json()
    if user["login"].lower() not in settings().admins:
        raise HTTPException(403, "Your account is not in ADMIN_USERS for this workspace.")
    result = RedirectResponse("/")
    result.set_cookie(
        "lr_session",
        signed({"login": user["login"], "avatar": user.get("avatar_url")}),
        httponly=True,
        secure=settings().public_url.startswith("https"),
        samesite="lax",
        max_age=86400,
    )
    result.delete_cookie("lr_oauth")
    return result


def public_resource(row):
    data = {**row.data}
    if row.kind == "repos":
        data["secret_names"] = list(json.loads(decrypt(row.secret))) if row.secret else []
    return {"id": row.id, **data, "has_secret": bool(row.secret), "updated_at": row.updated_at}


def safe_path(name):
    p = PurePosixPath(name)
    if p.is_absolute() or ".." in p.parts or "\\" in name or not p.parts or len(name) > 250:
        raise HTTPException(400, "Invalid skill file path.")
    return str(p)


def validate_resource(kind, data, db, resource_id):
    def exists(id, expected):
        r = db.get(Resource, id)
        if not r or r.kind != expected:
            raise HTTPException(400, f"A selected {expected} item no longer exists.")
        return r

    if kind == "agents":
        conn = exists(data["connection_id"], "connections")
        if conn.data["type"] == "mcp":
            raise HTTPException(400, "Choose a model connection.")
        for sid in data["skill_ids"]:
            exists(sid, "skills")
        for mcp in data["mcps"]:
            c = exists(mcp.get("connection_id"), "connections")
            if (
                c.data["type"] != "mcp"
                or not isinstance(mcp.get("tools"), list)
                or not set(mcp["tools"]).issubset({t["name"] for t in c.data["tools"]})
            ):
                raise HTTPException(400, "Select valid tools from a tested MCP connection.")
    if kind == "groups":
        if not data["callers"] and data["caller_policy"] == "allowlist":
            raise HTTPException(400, "Add at least one allowed GitHub username.")
        for member in data["members"]:
            exists(member["agent_id"], "agents")
        if len({m["agent_id"] for m in data["members"]}) != len(data["members"]):
            raise HTTPException(400, "Use the instance count instead of adding an agent twice.")
        configured = {
            r.data["name"].lower() for r in db.scalars(select(Resource).where(Resource.kind == "repos"))
        }
        if not {r.lower() for r in data["repos"]}.issubset(configured):
            raise HTTPException(400, "Connect each repository before adding it to a group.")
    if kind in ("groups", "repos"):
        for other in db.scalars(select(Resource).where(Resource.kind == kind, Resource.id != resource_id)):
            if other.data["name"].lower() == data["name"].lower():
                raise HTTPException(409, "This name is already in use.")
    if kind == "skills":
        if len(data["files"]) > 50:
            raise HTTPException(400, "A skill can contain at most 50 supporting files.")
        total = 0
        for name, value in data["files"].items():
            safe_path(name)
            try:
                total += len(base64.b64decode(value, validate=True))
            except ValueError:
                raise HTTPException(400, "Supporting files must contain base64 data.") from None
        if total > 2_000_000:
            raise HTTPException(400, "Skill files exceed 2 MB.")


@app.get("/api/resources/{kind}", dependencies=[Depends(require_admin)])
def list_resources(kind: str):
    if kind not in SCHEMAS:
        raise HTTPException(404)
    with Session() as db:
        return [
            public_resource(r)
            for r in db.scalars(
                select(Resource).where(Resource.kind == kind).order_by(Resource.updated_at.desc())
            )
        ]


@app.post("/api/resources/{kind}", dependencies=[Depends(require_admin)])
@app.put("/api/resources/{kind}/{resource_id}", dependencies=[Depends(require_admin)])
async def save_resource(kind: str, request: Request, resource_id: str | None = None):
    if kind not in SCHEMAS:
        raise HTTPException(404)
    from pydantic import ValidationError

    try:
        data = SCHEMAS[kind].model_validate(await request.json()).model_dump()
    except ValidationError as exc:
        raise HTTPException(
            422, "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        ) from None
    with Session() as db:
        row = db.get(Resource, resource_id) if resource_id else None
        if resource_id and (not row or row.kind != kind):
            raise HTTPException(404)
        rid = resource_id or uid()
        validate_resource(kind, data, db, rid)
        secret = row.secret if row else ""
        if kind == "connections":
            key = data.pop("api_key")
            data["tools"] = row.data.get("tools", []) if row else []
            if key:
                secret = encrypt(key)
            if row and (row.data["base_url"] != data["base_url"] or row.data["type"] != data["type"]):
                data["tools"] = []
        if kind == "repos":
            keys = data.pop("test_secrets")
            if keys:
                secret = encrypt(json.dumps(keys))
        if not row:
            row = Resource(id=rid, kind=kind, data=data)
            db.add(row)
        row.data, row.secret, row.updated_at = data, secret, time.time()
        db.commit()
        return public_resource(row)


@app.delete("/api/resources/{kind}/{resource_id}", dependencies=[Depends(require_admin)])
def delete_resource(kind: str, resource_id: str):
    with Session() as db:
        row = db.get(Resource, resource_id)
        if not row or row.kind != kind:
            raise HTTPException(404)
        for other in db.scalars(select(Resource).where(Resource.id != resource_id)):
            if other.kind == "agents" and (
                resource_id == other.data["connection_id"]
                or resource_id in other.data["skill_ids"]
                or any(m["connection_id"] == resource_id for m in other.data["mcps"])
            ):
                raise HTTPException(409, f'Remove this item from agent "{other.data["name"]}" first.')
            if other.kind == "groups" and (
                any(m["agent_id"] == resource_id for m in other.data["members"])
                or (kind == "repos" and row.data["name"] in other.data["repos"])
            ):
                raise HTTPException(409, f'Remove this item from group "{other.data["name"]}" first.')
        active = db.scalar(
            select(func.count()).select_from(Job).where(Job.status.in_(["queued", "starting", "running"]))
        )
        if active and kind in ("connections", "repos"):
            raise HTTPException(
                409, "Wait for active reviews to finish before deleting connections or repositories."
            )
        db.delete(row)
        db.commit()
    return {"ok": True}


@app.post("/api/skills/upload", dependencies=[Depends(require_admin)])
async def upload_skill(file: UploadFile = File(...)):
    content = await file.read(2_000_001)
    if len(content) > 2_000_000:
        raise HTTPException(400, "Skill ZIP exceeds 2 MB.")
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
        entries = [f for f in archive.infolist() if not f.is_dir()]
        if len(entries) > 51 or sum(f.file_size for f in entries) > 2_000_000:
            raise HTTPException(400, "Expanded skill exceeds the file or size limit.")
        files = {}
        for entry in entries:
            safe_path(entry.filename)
            if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                raise HTTPException(400, "Symlinks are not allowed in skills.")
            files[entry.filename] = archive.read(entry)
        roots = [p for p in files if PurePosixPath(p).name == "SKILL.md"]
        if len(roots) != 1:
            raise HTTPException(400, "Include exactly one SKILL.md.")
        root = PurePosixPath(roots[0]).parent
        normalized = {
            str(PurePosixPath(p).relative_to(root)): v
            for p, v in files.items()
            if PurePosixPath(p).is_relative_to(root)
        }
        instructions = normalized.pop("SKILL.md").decode("utf-8")
        return {
            "name": (root.name or file.filename.removesuffix(".zip"))[:100],
            "description": "",
            "instructions": instructions,
            "files": {p: base64.b64encode(v).decode() for p, v in normalized.items()},
        }
    except (zipfile.BadZipFile, UnicodeError, ValueError, RuntimeError):
        raise HTTPException(400, "Upload a valid ZIP with a UTF-8 SKILL.md.") from None


@app.post("/api/connections/{resource_id}/test", dependencies=[Depends(require_admin)])
async def test_connection(resource_id: str):
    conn = providers.connection(resource_id)
    if conn["type"] == "mcp":
        tools = await providers.mcp_tools(conn)
        with Session() as db:
            row = db.get(Resource, resource_id)
            row.data = {**row.data, "tools": tools}
            db.commit()
        return {"ok": True, "tools": tools}
    return {"ok": True, "models": await providers.models(conn)}


@app.post("/api/agents/{resource_id}/test", dependencies=[Depends(require_admin)])
async def test_agent(resource_id: str):
    with Session() as db:
        row = db.get(Resource, resource_id)
        if not row or row.kind != "agents":
            raise HTTPException(404)
        data = row.data
    return await providers.probe(
        providers.connection(data["connection_id"]),
        data["model"],
        bool({"browser", "computer"} & set(data["capabilities"])),
    )


@app.get("/api/setup", dependencies=[Depends(require_admin)])
def setup_status():
    conf = github.config()
    return {
        "public_url": settings().public_url,
        "modal": bool(settings().modal_token_id and settings().modal_token_secret),
        "github": bool(conf.get("app_id") and conf.get("private_key")),
        "github_app_url": conf.get("html_url", ""),
        "development_github_token": bool(settings().github_token),
        "webhook_url": settings().public_url + "/api/webhook/github",
        "admin_users": sorted(settings().admins),
    }


@app.get("/api/github/repositories", dependencies=[Depends(require_admin)])
async def repos():
    return await github.installed_repositories()


@app.post("/api/github/manifest", dependencies=[Depends(require_admin)])
async def manifest(request: Request):
    body = await request.json()
    state = signed({"purpose": "manifest", "nonce": secrets.token_urlsafe(20)})
    base = settings().public_url
    if not base.startswith("https://"):
        raise HTTPException(
            400, "Set PUBLIC_URL to your deployed HTTPS address before creating the GitHub App."
        )
    manifest = {
        "name": body.get("name") or "LiteReview-" + secrets.token_hex(3),
        "url": base,
        "hook_attributes": {"url": base + "/api/webhook/github", "active": True},
        "redirect_url": base + "/api/github/manifest/callback",
        "callback_urls": [base + "/api/auth/github/callback"],
        "setup_url": base + "/connections",
        "public": False,
        "default_permissions": {
            "contents": "read",
            "metadata": "read",
            "pull_requests": "write",
            "issues": "write",
        },
        "default_events": ["issue_comment"],
    }
    owner_type = body.get("owner_type", "personal")
    url = "https://github.com/settings/apps/new"
    if owner_type == "organization":
        import re

        owner = str(body.get("owner", ""))
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner):
            raise HTTPException(400, "Enter the GitHub organization name.")
        url = f"https://github.com/organizations/{owner}/settings/apps/new"
    elif owner_type != "personal":
        raise HTTPException(400, "Choose a personal account or organization.")
    response = JSONResponse(
        {"url": url + "?" + urlencode({"state": state}), "manifest": json.dumps(manifest)}
    )
    response.set_cookie("lr_manifest", state, httponly=True, secure=True, samesite="lax", max_age=900)
    return response


@app.get("/api/github/manifest/callback", dependencies=[Depends(require_admin)])
async def manifest_callback(request: Request, code: str, state: str):
    if (
        not secrets.compare_digest(state, request.cookies.get("lr_manifest", ""))
        or unsign(state, 900).get("purpose") != "manifest"
    ):
        raise HTTPException(403)
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            github.API + f"/app-manifests/{code}/conversions",
            headers={"Accept": "application/vnd.github+json"},
        )
    if not response.is_success:
        raise HTTPException(400, "GitHub App creation failed. Start again from Connections.")
    data = response.json()
    conf = {
        "app_id": str(data["id"]),
        "private_key": data["pem"],
        "webhook_secret": data["webhook_secret"],
        "client_id": data["client_id"],
        "client_secret": data["client_secret"],
        "html_url": data["html_url"],
    }
    with Session() as db:
        row = db.get(Resource, "github") or Resource(id="github", kind="system", data={})
        row.secret = encrypt(json.dumps(conf))
        db.add(row)
        db.commit()
    response = RedirectResponse(data["html_url"] + "/installations/new")
    response.delete_cookie("lr_manifest")
    return response


@app.post("/api/github/config", dependencies=[Depends(require_admin)])
async def github_config(request: Request):
    data = await request.json()
    allowed = {"app_id", "private_key", "webhook_secret", "client_id", "client_secret", "html_url"}
    conf = github.config()
    conf.update({k: v for k, v in data.items() if k in allowed and v})
    with Session() as db:
        row = db.get(Resource, "github") or Resource(id="github", kind="system", data={})
        row.secret = encrypt(json.dumps(conf))
        db.add(row)
        db.commit()
    return {"ok": True}


@app.post("/api/webhook/github")
async def webhook(request: Request):
    body = await request.body()
    if not verify_signature(
        body, request.headers.get("x-hub-signature-256"), github.config().get("webhook_secret", "")
    ):
        raise HTTPException(401, "Invalid webhook signature.")
    if request.headers.get("x-github-event") != "issue_comment":
        return {"ignored": True}
    delivery = request.headers.get("x-github-delivery")
    if not delivery or len(delivery) > 150:
        raise HTTPException(400, "Missing GitHub delivery ID.")
    run_id = await reviews.accept(json.loads(body), delivery)
    return {"run_id": run_id, "ignored": not bool(run_id)}


def run_public(run):
    return {
        k: getattr(run, k)
        for k in ("id", "repo", "pr", "actor", "head", "base", "status", "comment_id", "created_at", "error")
    }


@app.get("/api/runs", dependencies=[Depends(require_admin)])
def runs():
    with Session() as db:
        return [run_public(r) for r in db.scalars(select(Run).order_by(Run.created_at.desc()).limit(100))]


@app.get("/api/runs/{run_id}", dependencies=[Depends(require_admin)])
def run_detail(run_id: str):
    with Session() as db:
        run = db.get(Run, run_id)
        if not run:
            raise HTTPException(404)
        jobs = db.scalars(select(Job).where(Job.run_id == run_id)).all()
        result = run_public(run)
        result["jobs"] = []
        for job in jobs:
            data = {
                k: getattr(job, k)
                for k in (
                    "id",
                    "status",
                    "config",
                    "sandbox_id",
                    "started_at",
                    "finished_at",
                    "model_calls",
                    "result",
                    "logs",
                )
            }
            data["artifacts"] = [
                {"id": a.id, "name": a.name}
                for a in db.scalars(select(Artifact).where(Artifact.job_id == job.id))
            ]
            result["jobs"].append(data)
        return result


@app.get("/api/artifacts/{artifact_id}", dependencies=[Depends(require_admin)])
def artifact(artifact_id: str):
    with Session() as db:
        a = db.get(Artifact, artifact_id)
        if not a:
            raise HTTPException(404)
        return Response(
            a.content, media_type=a.mime, headers={"Content-Disposition": 'inline; filename="screenshot.png"'}
        )


def worker(request: Request, job_id: str):
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    with Session() as db:
        job = db.get(Job, job_id)
        if (
            not job
            or not secrets.compare_digest(digest(token), job.token_hash)
            or job.status not in ("starting", "running")
            or time.time() - (job.started_at or 0) > settings().job_timeout + 300
        ):
            raise HTTPException(401, "Job token is invalid or expired.")
        return job


@app.get("/api/internal/jobs/{job_id}/bootstrap")
def bootstrap(job_id: str, request: Request):
    job = worker(request, job_id)
    with Session() as db:
        run = db.get(Run, job.run_id)
        repo = run.snapshot["repository"]
        saved = db.get(Resource, repo["id"])
        test_secrets = (
            json.loads(decrypt(saved.secret)) if saved and saved.secret and job.config["capabilities"] else {}
        )
        db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(["starting", "running"]))
            .values(status="running")
        )
        db.commit()
        return {
            "job_id": job.id,
            "config": job.config,
            "pr": {
                "repo": run.repo,
                "number": run.pr,
                "head": run.head,
                "base": run.base,
                "title": run.snapshot["title"],
                "description": run.snapshot["description"],
                "diff": run.snapshot["diff"],
            },
            "repository": repo,
            "test_secrets": test_secrets,
            "max_rounds": settings().max_rounds,
            "timeout": settings().job_timeout,
        }


@app.get("/api/internal/jobs/{job_id}/archive")
async def job_archive(job_id: str, request: Request):
    job = worker(request, job_id)
    with Session() as db:
        run = db.get(Run, job.run_id)
    return Response(await github.archive(run.repo, run.installation, run.head), media_type="application/gzip")


@app.post("/api/internal/jobs/{job_id}/model")
async def model_relay(job_id: str, request: Request):
    job = worker(request, job_id)
    with Session() as db:
        changed = db.execute(
            update(Job)
            .where(Job.id == job.id, Job.model_calls < settings().max_rounds)
            .values(model_calls=Job.model_calls + 1)
        ).rowcount
        db.commit()
    if not changed:
        raise HTTPException(429, "Model round limit reached.")
    body = await request.json()
    if len(body.get("messages", [])) > 400:
        raise HTTPException(400, "Conversation limit reached.")
    return await providers.complete(
        providers.snapshot_connection(job.config["connection"]),
        job.config["model"],
        body["messages"],
        body.get("tools", []),
    )


@app.post("/api/internal/jobs/{job_id}/mcp")
async def mcp_relay(job_id: str, request: Request):
    job = worker(request, job_id)
    body = await request.json()
    allowed = next((m for m in job.config["mcp_definitions"] if m["id"] == body.get("connection_id")), None)
    if not allowed or body.get("name") not in {t["name"] for t in allowed["tools"]}:
        raise HTTPException(403, "This MCP tool is not enabled for this agent.")
    return await providers.mcp_call(
        providers.snapshot_connection(allowed), body["name"], body.get("arguments", {})
    )


@app.post("/api/internal/jobs/{job_id}/event")
async def job_event(job_id: str, request: Request):
    worker(request, job_id)
    body = await request.json()
    with Session() as db:
        job = db.get(Job, job_id)
        event = {"time": time.time(), "message": str(body.get("message", ""))[:2000]}
        job.logs = (job.logs + [event])[-200:]
        db.commit()
    return {"ok": True}


@app.post("/api/internal/jobs/{job_id}/artifact")
async def job_artifact(job_id: str, request: Request):
    worker(request, job_id)
    body = await request.json()
    try:
        raw = base64.b64decode(body["data"], validate=True)
    except (ValueError, KeyError):
        raise HTTPException(400, "Invalid screenshot.") from None
    if len(raw) > 2_000_000 or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(400, "Screenshots must be PNG files under 2 MB.")
    with Session() as db:
        if db.scalar(select(func.count()).select_from(Artifact).where(Artifact.job_id == job_id)) >= 20:
            raise HTTPException(413, "Screenshot storage limit reached.")
        artifact = Artifact(
            id=uid(), job_id=job_id, name=str(body.get("name", "Screenshot"))[:150], content=raw
        )
        db.add(artifact)
        db.commit()
        return {"id": artifact.id}


@app.post("/api/internal/jobs/{job_id}/finish")
async def finish(job_id: str, request: Request):
    # Idempotent completion is allowed with the same token after completion.
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    with Session() as db:
        job = db.get(Job, job_id)
        if not job or not secrets.compare_digest(job.token_hash, digest(token)):
            raise HTTPException(401)
        if job.status in reviews.TERMINAL:
            return {"ok": True}
    worker(request, job_id)
    try:
        result = ReviewResult.model_validate(await request.json()).model_dump()
    except ValidationError as exc:
        fields = ", ".join(".".join(map(str, error["loc"])) for error in exc.errors()[:10])
        raise HTTPException(
            422,
            f"Invalid review result fields: {fields}. Follow the finish_review schema: findings and limitations must be arrays, and each finding needs title, severity, and explanation. Correct the result and submit again.",
        ) from None
    except ValueError:
        raise HTTPException(400, "Submit the review result as a JSON object.") from None
    with Session() as db:
        db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status.in_(["starting", "running"]))
            .values(status=result["status"], result=result, finished_at=time.time())
        )
        db.commit()
    return {"ok": True}


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404)
    root = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    requested = (root / path).resolve()
    if requested.is_relative_to(root) and requested.is_file():
        return FileResponse(requested)
    if (root / "index.html").exists():
        return FileResponse(root / "index.html")
    return JSONResponse(
        {"detail": "Build the frontend: cd frontend && npm ci && npm run build"}, status_code=503
    )
