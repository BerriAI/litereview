import asyncio
import threading
import time
from functools import lru_cache
from pathlib import Path

import modal

from runner.catalog import HARNESS_VERSIONS, NPM_PACKAGES, validate_connection

from . import providers
from .db import Job, Session
from .limits import SANDBOX_MAX_SECONDS, job_timeout
from .settings import settings


def sandbox_image(harness="builtin"):
    if harness not in HARNESS_VERSIONS:
        raise ValueError("Unknown harness.")
    image = (
        modal.Image.from_registry("node:22-bookworm-slim", add_python="3.12")
        .apt_install("git", "ripgrep", "curl", "xvfb", "openbox", "scrot", "xdotool", "xauth")
        .pip_install(
            "uv==0.10.9", "httpx==0.28.1", "playwright==1.63.0", "pyautogui==0.9.54", "pillow>=11,<13"
        )
        .run_commands("playwright install --with-deps chromium")
    )
    if harness != "builtin":
        image = image.pip_install("mcp>=1.26,<2", "uvicorn==0.34.3", "starlette>=0.49,<1")
    if harness in NPM_PACKAGES:
        package = NPM_PACKAGES[harness] + "@" + HARNESS_VERSIONS[harness]
        image = image.run_commands(f"npm install --prefix /opt/litereview/harness --omit=dev {package}")
    elif harness == "deepagents":
        image = image.pip_install(
            "deepagents==0.7.19", "langchain-openai==1.6.6", "langchain-anthropic>=1,<2"
        )
    return image.add_local_dir(
        str(Path(__file__).resolve().parent.parent / "runner"), "/opt/litereview/runner"
    )


def client():
    s = settings()
    if not s.modal_token_id or not s.modal_token_secret:
        raise RuntimeError("Set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in the deployment environment.")
    return modal.Client.from_credentials(s.modal_token_id, s.modal_token_secret)


_app_lock = threading.Lock()


@lru_cache(maxsize=1)
def _app(name, token_id, token_secret):
    c = modal.Client.from_credentials(token_id, token_secret)
    return modal.App.lookup(name, create_if_missing=True, client=c)


def create_sandbox(job_id, token, harness="builtin", timeout=None):
    s = settings()
    c = client()
    with _app_lock:
        app = _app(s.modal_app_name, s.modal_token_id, s.modal_token_secret)
    return modal.Sandbox.create(
        "python",
        "-m",
        "runner.agent",
        app=app,
        image=sandbox_image(harness),
        client=c,
        name="lr-" + job_id,
        timeout=min(timeout + 60, SANDBOX_MAX_SECONDS) if timeout else SANDBOX_MAX_SECONDS,
        cpu=2,
        memory=4096,
        workdir="/opt/litereview",
        env={
            "LR_API_URL": s.public_url.rstrip("/"),
            "LR_JOB_ID": job_id,
            "LR_JOB_TOKEN": token,
            "PYTHONUNBUFFERED": "1",
        },
    )


async def terminate(sandbox_id):
    try:
        await asyncio.to_thread(lambda: modal.Sandbox.from_id(sandbox_id, client=client()).terminate())
    except Exception:
        pass  # Modal's lifetime remains the cleanup backstop if its API is unavailable.


async def launch(job_id, token):
    sandbox = None
    try:
        with Session() as db:
            job = db.get(Job, job_id)
            cfg = job.config
        harness = cfg.get("harness", "builtin")
        if cfg.get("harness_version", HARNESS_VERSIONS[harness]) != HARNESS_VERSIONS[harness]:
            raise RuntimeError(
                "The harness version changed after this review was queued. Start a new review."
            )
        validate_connection(harness, cfg["connection"]["type"])
        if harness == "builtin":
            await providers.probe(
                providers.snapshot_connection(cfg["connection"]),
                cfg["model"],
                bool({"browser", "computer"} & set(cfg["capabilities"])),
            )
        if not settings().public_url.startswith("https://"):
            raise RuntimeError(
                "Modal requires a publicly reachable HTTPS PUBLIC_URL. Deploy to Render or use a development tunnel."
            )
        sandbox = await asyncio.to_thread(create_sandbox, job_id, token, harness, job_timeout(cfg))
        with Session() as db:
            job = db.get(Job, job_id)
            job.sandbox_id = sandbox.object_id
            db.commit()
        await watch(job_id, sandbox)
    except asyncio.CancelledError:
        # API shutdown is not a request to stop the review. The next dispatcher
        # watches the saved sandbox ID while the worker keeps its active-job token.
        sandbox = None
        raise
    except Exception as exc:
        from fastapi import HTTPException

        message = (
            exc.detail
            if isinstance(exc, HTTPException)
            else str(exc)
            if isinstance(exc, RuntimeError)
            else f"Could not start the Modal sandbox ({type(exc).__name__}). Check deployment credentials and Modal logs."
        )
        with Session() as db:
            job = db.get(Job, job_id)
            if job and job.status not in ("completed", "failed", "timed_out"):
                job.status = "failed"
                job.finished_at = time.time()
                job.result = {
                    "status": "failed",
                    "summary": "The review could not complete.",
                    "findings": [],
                    "limitations": [message],
                }
                db.commit()
    finally:
        if sandbox:
            await terminate(sandbox.object_id)


async def watch(job_id, sandbox):
    """Observe actual sandbox exit, including after the API restarts."""
    try:
        await sandbox.wait.aio()
        with Session() as db:
            job = db.get(Job, job_id)
            if job.status not in ("completed", "failed", "timed_out"):
                job.status = "failed"
                job.finished_at = time.time()
                job.result = {
                    "status": "failed",
                    "summary": "The sandbox exited before submitting its review.",
                    "findings": [],
                    "limitations": ["Check PUBLIC_URL connectivity and the Modal sandbox logs."],
                }
                db.commit()
    except modal.exception.SandboxTimeoutError:
        with Session() as db:
            job = db.get(Job, job_id)
            if job and job.status not in ("completed", "failed", "timed_out"):
                job.status = "timed_out"
                job.finished_at = time.time()
                job.result = {
                    "status": "timed_out",
                    "summary": "The sandbox reached its execution time limit.",
                    "findings": [],
                    "limitations": [
                        "Execution time limit reached."
                        if job_timeout(job.config)
                        else "Modal's 24-hour sandbox lifetime was reached. No agent time limit was configured."
                    ],
                }
                db.commit()


async def watch_existing(job_id, sandbox_id):
    sandbox = await asyncio.to_thread(lambda: modal.Sandbox.from_id(sandbox_id, client=client()))
    await watch(job_id, sandbox)
    await terminate(sandbox_id)
