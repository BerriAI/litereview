import asyncio
import time
from pathlib import Path

import modal

from . import providers
from .db import Job, Session
from .settings import settings


def sandbox_image():
    return (
        modal.Image.from_registry("node:22-bookworm-slim", add_python="3.12")
        .apt_install("git", "ripgrep", "curl", "xvfb", "openbox", "scrot", "xdotool", "xauth")
        .pip_install(
            "uv==0.10.9", "httpx==0.28.1", "playwright==1.63.0", "pyautogui==0.9.54", "pillow>=11,<13"
        )
        .run_commands("playwright install --with-deps chromium")
        .add_local_dir(str(Path(__file__).resolve().parent.parent / "runner"), "/opt/litereview/runner")
    )


def client():
    s = settings()
    if not s.modal_token_id or not s.modal_token_secret:
        raise RuntimeError("Set MODAL_TOKEN_ID and MODAL_TOKEN_SECRET in the deployment environment.")
    return modal.Client.from_credentials(s.modal_token_id, s.modal_token_secret)


def create_sandbox(job_id, token):
    s = settings()
    c = client()
    app = modal.App.lookup(s.modal_app_name, create_if_missing=True, client=c)
    return modal.Sandbox.create(
        "python",
        "-m",
        "runner.agent",
        app=app,
        image=sandbox_image(),
        client=c,
        name="lr-" + job_id,
        timeout=s.job_timeout + 60,
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
        pass  # The hard sandbox timeout remains the cleanup backstop.


async def launch(job_id, token):
    sandbox = None
    try:
        with Session() as db:
            job = db.get(Job, job_id)
            cfg = job.config
        await providers.probe(
            providers.snapshot_connection(cfg["connection"]),
            cfg["model"],
            bool({"browser", "computer"} & set(cfg["capabilities"])),
        )
        if not settings().public_url.startswith("https://"):
            raise RuntimeError(
                "Modal requires a publicly reachable HTTPS PUBLIC_URL. Deploy to Render or use a development tunnel."
            )
        sandbox = await asyncio.to_thread(create_sandbox, job_id, token)
        with Session() as db:
            job = db.get(Job, job_id)
            job.sandbox_id = sandbox.object_id
            db.commit()
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
