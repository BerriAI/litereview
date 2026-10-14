"""Runs inside a fresh Modal sandbox. No provider or GitHub credentials are present."""

import asyncio
import base64
import gzip
import io
import json
import os
import shutil
import sys
import tarfile
import time
from pathlib import Path, PurePosixPath
from tempfile import TemporaryFile

import httpx

from .catalog import review_capabilities
from .diagnostics import ReviewError, emit, failure_record
from .instructions import agent_instructions
from .tools import REPO, SKILLS, Toolset

# Allow safe setup/completion requests to survive a rolling API restart.
RECOVERY_SECONDS = 300


def response_error(response, stage="review"):
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        detail = None
    message = f"The review service returned HTTP {response.status_code} during {stage}."
    return ReviewError(detail if isinstance(detail, str) and detail else message,
                       stage=stage, safe_message=message, http_status=response.status_code)


async def recovery_pause(attempt, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return False
    await asyncio.sleep(min(2 ** min(attempt, 4), remaining))
    return True


class Relay:
    def __init__(self):
        api = os.environ.pop("LR_API_URL")
        job = os.environ.pop("LR_JOB_ID")
        token = os.environ.pop("LR_JOB_TOKEN")
        self.client = httpx.AsyncClient(
            base_url=f"{api}/api/internal/jobs/{job}/",
            headers={"Authorization": "Bearer " + token},
            timeout=None,
        )

    async def __call__(self, endpoint, data=None):
        stage = {"finish": "result submission", "bootstrap": "setup", "start-review": "setup"}.get(
            endpoint, "review")
        recoverable = endpoint in {"bootstrap", "start-review", "finish"}
        deadline = time.monotonic() + RECOVERY_SECONDS
        attempt = 0
        while True:
            try:
                response = await (
                    self.client.get(endpoint) if data is None else self.client.post(
                        endpoint,
                        content=gzip.compress(json.dumps(data).encode()),
                        headers={"content-type": "application/json", "content-encoding": "gzip"},
                    )
                )
                if response.is_success:
                    return response.json()
                error = response_error(response, stage)
                if response.status_code < 500:
                    raise error
            except httpx.TransportError as exc:
                error = ReviewError(
                    f"The review service could not be reached during {stage} ({type(exc).__name__}).",
                    stage=stage)
            if (not recoverable and attempt >= 2) or not await recovery_pause(attempt, deadline):
                raise error from None
            attempt += 1

    async def event(self, message):
        try:
            await self("event", {"message": message})
        except Exception:
            pass

    async def upload_video(self, path, name):
        from urllib.parse import quote

        async def chunks():
            with path.open("rb") as source:
                while chunk := source.read(256 * 1024):
                    yield chunk
        response = await self.client.post("artifact/video", content=chunks(),
                                          headers={"content-type": "video/mp4", "x-artifact-name": quote(name)})
        if not response.is_success:
            raise RuntimeError("Video upload failed. Retry recording stop to preserve the existing video.")
        return response.json()


def extract_archive(raw, root):
    root.mkdir(parents=True, exist_ok=True)
    source = io.BytesIO(raw) if isinstance(raw, bytes) else raw
    # Stream tar metadata and file contents; neither grows an in-memory archive.
    with tarfile.open(fileobj=source, mode="r|gz") as tar:
        for member in tar:
            tar.members.clear()
            parts = PurePosixPath(member.name).parts
            if len(parts) < 2:
                continue
            relative = PurePosixPath(*parts[1:])
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative):
                raise ValueError("Unsafe repository archive path.")
            path = root / relative
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as contents, path.open("wb") as target:
                    shutil.copyfileobj(contents, target, length=64 * 1024)
                path.chmod(0o755 if member.mode & 0o111 else 0o644)
            # Symlinks and device entries are never materialized.


async def prepare_repository(relay):
    await relay.event("Downloading repository at the reviewed commit")
    with TemporaryFile() as archive:
        deadline = time.monotonic() + RECOVERY_SECONDS
        attempt = 0
        while True:
            # A broken transfer must restart from byte zero, never append another tarball.
            archive.seek(0)
            archive.truncate()
            try:
                async with relay.client.stream("GET", "archive") as response:
                    if not response.is_success:
                        await response.aread()
                        error = response_error(response, "repository download")
                        if response.status_code < 500:
                            raise error
                    else:
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            archive.write(chunk)
                        break
            except httpx.TransportError:
                error = ReviewError("Repository download was interrupted.", stage="repository download")
            if not await recovery_pause(attempt, deadline):
                raise error from None
            attempt += 1
            await relay.event("Repository download interrupted; retrying after the service recovers")
        await relay.event("Extracting repository")
        archive.seek(0)
        extract_archive(archive, REPO)


def stage_skills(skills):
    SKILLS.mkdir(parents=True, exist_ok=True)
    for skill in skills:
        root = SKILLS / skill["id"]
        root.mkdir()
        (root / "SKILL.md").write_text(skill["instructions"])
        for name, value in skill.get("files", {}).items():
            p = (root / name).resolve()
            if not p.is_relative_to(root):
                raise ValueError("Invalid skill path.")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(base64.b64decode(value))


def skill_context(skills):
    sections = []
    for skill in skills:
        root = f"skills/{skill['id']}"
        files = "\n".join(f"- {root}/{name}" for name in skill.get("files", {}))
        sections.append(
            f"Skill: {skill['name']}\nPath: {root}/SKILL.md\n"
            f"Use these exact read_file paths for supporting files (relative links in this skill resolve under {root}, not the repository):\n"
            f"{files or '(none)'}\n{skill['instructions']}"
        )
    return "\n\n".join(sections)


async def run(relay, data):
    if data.get("branch_reports"):
        report_path = Path("/workspace/litereview-run/branch-reports.json")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(data["branch_reports"], ensure_ascii=False), encoding="utf-8")
    data["config"]["capabilities"] = review_capabilities(data["config"].get("capabilities", []))
    await prepare_repository(relay)
    stage_skills(data["config"]["skills"])
    tools = Toolset(data, relay)
    try:
        await relay.event("Preparing repository and enabled tools")
        await tools.init()
        cfg = data["config"]
        if cfg.get("minimum_review_seconds"):
            await relay("start-review", {})
        tools.clock.start()
        await relay.event("Review clock started: " + json.dumps(tools.clock.status()))
        skills = skill_context(cfg["skills"])
        system = agent_instructions(cfg, tools.clock, skills)
        from .harness import run_external

        prompt = json.dumps(
            {
                "review": data["pr"],
                "environment": {
                    "repo": str(REPO),
                    "checkout": "GitHub source archive of the supplied immutable head SHA; .git history is not included. Use the supplied SHA and source files to identify the checkout; do not fabricate a local commit.",
                    "readiness_url": data["repository"].get("readiness_url"),
                    "capabilities": cfg["capabilities"],
                },
            },
            ensure_ascii=False,
        )
        await run_external(tools, relay, data, system, prompt)
    finally:
        await tools.close()


async def main():
    relay = Relay()
    stage = "setup"
    try:
        data = await relay("bootstrap")
        stage = "review"
        async with asyncio.timeout(data.get("timeout")):
            await run(relay, data)
    except Exception as exc:
        while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
        status = "timed_out" if isinstance(exc, TimeoutError) else "failed"
        record = failure_record(exc, stage)
        emit(record)
        limitations = [record["message"]]
        if record.get("exit_code") is not None:
            limitations.append(f"Review harness exit code: {record['exit_code']}.")
        try:
            await relay(
                "finish",
                {
                    "status": status,
                    "summary": "The review could not complete.",
                    "findings": [],
                    "limitations": limitations,
                },
            )
        except Exception as submission_error:
            emit(failure_record(submission_error, "result submission", kind="submission_failed"))
        return 1
    finally:
        await relay.client.aclose()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
