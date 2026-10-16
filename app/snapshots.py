"""Bounded, best-effort repository warming, independent of review launches."""

import asyncio
import hashlib
import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import quote

import modal
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

from . import github
from .db import Job, RepositorySnapshot, Resource, Session, uid
from .settings import settings

TTL = 7 * 24 * 3600
REFRESH = 24 * 3600
BUILD_TIMEOUT = 45 * 60
RETRY = 3600
log = logging.getLogger("litereview")


@lru_cache(maxsize=1)
def runtime_fingerprint():
    root = Path(__file__).resolve().parent.parent
    digest = hashlib.sha256()
    paths = [root / "app/runtime.py", root / "app/snapshots.py", root / "uv.lock"]
    paths += sorted((root / "runner").glob("*.py"))
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    digest.update(modal.__version__.encode())
    return digest.hexdigest()


def fingerprint(repository):
    return hashlib.sha256(json.dumps({
        "runtime": runtime_fingerprint(), "name": repository["name"],
        "installation": repository.get("installation_id", 0),
        "setup": repository.get("setup_command", ""),
    }, sort_keys=True).encode()).hexdigest()


def compatible(row, repository, now=None):
    return bool(repository.get("setup_command") and row and row.image_id
                and row.fingerprint == fingerprint(repository)
                and row.expires_at > (time.time() if now is None else now) + 60)


def image_for(repository):
    with Session() as db:
        row = db.get(RepositorySnapshot, repository.get("id", ""))
        return row.image_id if compatible(row, repository) else None


def invalidate(repository_id, image_id):
    with Session.begin() as db:
        db.execute(update(RepositorySnapshot).where(
            RepositorySnapshot.repository_id == repository_id, RepositorySnapshot.image_id == image_id,
        ).values(image_id="", retry_after=0))


def status(repository_id, repository):
    with Session() as db:
        row = db.get(RepositorySnapshot, repository_id)
        ready = compatible(row, repository)
        building = bool(row and row.lease_until > time.time())
        return {
            "state": "disabled" if not repository.get("setup_command") else
                     "ready" if ready else "building" if building else "cold",
            "refreshing": building, "source_sha": row.source_sha if ready else None,
            "built_at": row.built_at if ready else None,
            "expires_at": row.expires_at if ready else None,
            "last_error": row.error if row else "",
        }


def claim(repository_id, repository):
    now, requested, token = time.time(), fingerprint(repository), uid()
    try:
        with Session.begin() as db:
            if not db.get(RepositorySnapshot, repository_id):
                db.add(RepositorySnapshot(repository_id=repository_id))
    except IntegrityError:
        pass  # Another dispatcher initialized the same row during a rollout.
    with Session.begin() as db:
        changed = db.execute(update(RepositorySnapshot).where(
            RepositorySnapshot.repository_id == repository_id,
            RepositorySnapshot.lease_until < now,
            or_(RepositorySnapshot.retry_after <= now,
                RepositorySnapshot.requested_fingerprint != requested),
        ).values(lease_until=now + BUILD_TIMEOUT + 300, build_token=token,
                 requested_fingerprint=requested)).rowcount
    return token if changed else None


async def build(repository):
    """Only trusted default-branch code enters reusable images. No secrets enter."""
    from .runtime import _app, client, sandbox_image

    name, installation = repository["name"], repository.get("installation_id", 0)
    metadata = (await github.repo_request(name, installation, "GET", "")).json()
    branch = quote(metadata["default_branch"], safe="")
    commit = (await github.repo_request(name, installation, "GET", f"commits/{branch}")).json()["sha"]
    # Download on the service side: the sandbox never receives a GitHub token or
    # an authenticated download URL, including during dependency installation.
    with NamedTemporaryFile(suffix=".tar.gz") as archive:
        async with github.archive(name, installation, commit) as response:
            async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                archive.write(chunk)
        archive.flush()
        s = settings()
        app = await asyncio.to_thread(_app, s.modal_app_name, s.modal_token_id, s.modal_token_secret)
        modal_client = await asyncio.to_thread(client)
        sandbox = await modal.Sandbox.create.aio(
            "sleep", str(BUILD_TIMEOUT), app=app, image=sandbox_image(), client=modal_client,
            timeout=BUILD_TIMEOUT, cpu=2, memory=4096, workdir="/opt/litereview",
        )
        try:
            await sandbox.filesystem.copy_from_local.aio(archive.name, "/tmp/lr-source.tar.gz")
            await sandbox.filesystem.write_text.aio(json.dumps({
                "archive": "/tmp/lr-source.tar.gz", "command": repository["setup_command"],
            }), "/tmp/lr-warm.json")
            process = await sandbox.exec.aio(
                "python", "-m", "runner.warm", "/tmp/lr-warm.json",
                stdout=modal.stream_type.StreamType.DEVNULL, stderr=modal.stream_type.StreamType.DEVNULL,
            )
            if await process.wait.aio() != 0:
                raise RuntimeError("Setup failed without test secrets; reviews will use cold sandboxes.")
            image = await sandbox.snapshot_filesystem.aio(timeout=120, ttl=TTL)
            return image.object_id, commit
        finally:
            await sandbox.terminate.aio()


async def refresh(repository_id, repository):
    token = claim(repository_id, repository)
    if not token:
        return
    values = {"lease_until": 0, "retry_after": time.time() + RETRY}
    try:
        async with asyncio.timeout(BUILD_TIMEOUT):
            image_id, commit = await build(repository)
        now = time.time()
        values.update(image_id=image_id, source_sha=commit, fingerprint=fingerprint(repository),
                      built_at=now, expires_at=now + TTL, retry_after=now + REFRESH, error="")
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        # No setup output, archive URL, or credentials in service logs/status.
        values["error"] = (f"Snapshot refresh failed ({type(exc).__name__}); reviews use a compatible "
                           "snapshot if available, otherwise cold setup.")
        log.warning("Repository snapshot refresh failed for %s (%s)", repository_id, type(exc).__name__)
    finally:
        with Session.begin() as db:
            db.execute(update(RepositorySnapshot).where(
                RepositorySnapshot.repository_id == repository_id, RepositorySnapshot.build_token == token,
            ).values(**values))


async def maintain():
    # One build at a time per service; database leases also protect each repo
    # during rolling deployments. Review dispatch never waits for maintenance.
    while True:
        try:
            with Session() as db:
                repositories = [(r.id, r.data) for r in db.scalars(
                    select(Resource).where(Resource.kind == "repos"))]
            for repository_id, repository in repositories:
                if repository.get("setup_command"):
                    await refresh(repository_id, repository)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Repository snapshot maintenance failed")
        await asyncio.sleep(60)


def event(job_id, message):
    with Session.begin() as db:
        job = db.get(Job, job_id)
        job.logs = job.logs + [{"time": time.time(), "message": message}]
