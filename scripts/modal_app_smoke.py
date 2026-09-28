"""Opt-in: build a trusted LiteReview PR in Modal and verify real UI/network tools.

Requires authenticated gh, local Modal credentials, and a gateway in .env.
Runs PR code, consumes Modal/model credits, and stores private local evidence.
Usage: uv run python -m scripts.modal_app_smoke --repo BerriAI/litereview --pr 1
"""

import argparse
import asyncio
import base64
import hashlib
import io
import json
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import modal

from app import github, providers
from app.runtime import client, sandbox_image
from app.settings import settings

REMOTE = r"""
import asyncio, base64, hashlib, json, time
from pathlib import Path
import httpx
from runner.agent import extract_archive
from runner.tools import REPO, Toolset

def progress(value):
    print(value, flush=True)

async def relay(endpoint, body):
    return {"id": "smoke-local-artifact"}

def save(result, name):
    raw = base64.b64decode(result["image"].split(",", 1)[1])
    assert raw.startswith(b"\x89PNG\r\n\x1a\n"), name
    Path("/tmp/" + name + ".png").write_bytes(raw)

async def main():
    manifest = json.loads(Path("/tmp/pr-manifest.json").read_text())
    extract_archive(Path("/tmp/pr.tar.gz").read_bytes(), REPO)
    actual = {str(p.relative_to(REPO)): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in REPO.rglob("*") if p.is_file()}
    assert actual == manifest["files"], "Extracted source differs from PR archive"
    assert not (REPO / ".git").exists()
    progress("PASS: all %d source files match the pinned PR archive" % len(actual))

    network = {}
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as http:
        for url in ["https://api.github.com", "https://registry.npmjs.org/react/latest",
                    "https://pypi.org/pypi/httpx/json", "https://example.com"]:
            response = await http.get(url)
            response.raise_for_status()
            network[url] = response.status_code
    progress("PASS: outbound HTTPS to GitHub, npm, PyPI, and example.com")

    tools = Toolset({
        "config": {"capabilities": ["terminal", "browser", "computer"], "mcp_definitions": []},
        "repository": {
            "setup_command": "uv sync --frozen --no-dev && npm ci --prefix frontend && npm run build --prefix frontend",
            "start_command": ".venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log",
            "readiness_url": "http://localhost:8000/health",
        },
        "test_secrets": {
            "SECRET_KEY": "smoke-only-disposable-secret-000000000000",
            "ADMIN_TOKEN": "smoke-only-disposable-access-0000000000",
            "DISPATCHER_ENABLED": "false",
            "PUBLIC_URL": "http://localhost:8000",
        },
    }, relay)
    started = time.monotonic()
    try:
        progress("Installing Python/Node dependencies, building the frontend, and starting the PR app")
        await tools.init()
        startup_seconds = round(time.monotonic() - started, 1)
        progress("PASS: full application built and ready in %ss" % startup_seconds)
        errors = []
        tools.page.on("pageerror", lambda error: errors.append(str(error)))
        result = await tools.execute("terminal", {"command": "curl --fail --silent http://localhost:8000/health"})
        assert result["exit_code"] == 0 and json.loads(result["output"])["status"] == "ok", result

        result = await tools.execute("browser", {"action": "navigate", "url": "https://docs.python.org/3/"})
        assert "Python" in result["page"], result["page"]
        save(result, "app-smoke-browser-internet")
        progress("PASS: Chromium loads and screenshots an external HTTPS page")

        await tools.execute("browser", {"action": "navigate", "url": "http://localhost:8000"})
        await tools.execute("browser", {"action": "fill", "selector": "input[type=password]",
                                        "text": "smoke-only-disposable-access-0000000000"})
        result = await tools.execute("browser", {"action": "click", "selector": "button:has-text('Sign in')"})
        await tools.page.get_by_role("heading", name="PR agents", exact=True).wait_for()
        result = await tools.execute("browser", {"action": "screenshot"})
        save(result, "app-smoke-agents")
        await tools.execute("browser", {"action": "click", "selector": "button:has-text('Create agent')"})
        await tools.page.get_by_role("dialog").wait_for()
        result = await tools.execute("browser", {"action": "screenshot"})
        save(result, "app-smoke-agent-editor")
        await tools.execute("browser", {"action": "click", "selector": "button:has-text('Cancel')"})
        result = await tools.execute("browser", {"action": "click", "selector": "button:has-text('Agent groups')"})
        assert "Agent groups" in result["page"] and "Create group" in result["page"], result["page"]
        progress("PASS: browser signs in, opens the agent editor, and switches to groups")

        # Use desktop keyboard input to navigate the shared browser back to the app.
        await tools.execute("computer", {"action": "key", "keys": ["ctrl", "l"]})
        await tools.execute("computer", {"action": "type", "text": "http://localhost:8000"})
        await tools.execute("computer", {"action": "key", "keys": ["enter"]})
        await tools.page.get_by_role("heading", name="PR agents", exact=True).wait_for()
        result = await tools.execute("computer", {"action": "screenshot"})
        save(result, "app-smoke-desktop")
        assert not errors, errors
        progress("PASS: desktop keyboard navigates the app; full-screen PNG captured; no JavaScript errors")
        result = {
            "head_sha": manifest["sha"], "source_files_verified": len(actual),
            "network": network, "startup_seconds": startup_seconds,
            "dependencies_installed": True, "frontend_built": True, "health": "ok",
            "browser_internet": True, "browser_login_and_clicks": True,
            "desktop_keyboard_and_screenshot": True, "javascript_errors": errors,
            "visual_calls": tools.visual_calls,
        }
        Path("/tmp/app-smoke-result.json").write_text(json.dumps(result, indent=2))
    finally:
        await tools.close()

asyncio.run(main())
"""


async def fetch_source(repo, pr):
    # Credentials stay on the host; only the source archive enters the sandbox.
    settings().github_token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    try:
        pull = (await github.repo_request(repo, 0, "GET", f"pulls/{pr}")).json()
        sha = pull["head"]["sha"]
        raw = await github.archive(repo, 0, sha)
    finally:
        settings().github_token = ""
    files = {}
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            if member.isfile():
                path = str(PurePosixPath(*PurePosixPath(member.name).parts[1:]))
                files[path] = hashlib.sha256(archive.extractfile(member).read()).hexdigest()
    return raw, {"sha": sha, "files": files}


async def verify_vision(folder, model):
    s = settings()
    message = await providers.complete(
        {"type": "gateway", "base_url": s.gateway_url, "api_key": s.gateway_api_key},
        model,
        [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Read this screenshot. Return only JSON with app_name, page_heading, and one primary_button. Transcribe the visible labels.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": "data:image/png;base64,"
                            + base64.b64encode((folder / "app-smoke-desktop.png").read_bytes()).decode()
                        },
                    },
                ],
            }
        ],
        [],
    )
    content = message.get("content", "")
    assert "LiteReview" in content and "PR agents" in content and "Create agent" in content, content
    return {"model": model, "visible_labels": content}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--model", default="claude-haiku-4-5-20251001")
    args = parser.parse_args()
    folder = Path(".test-artifacts")
    folder.mkdir(exist_ok=True)
    raw, manifest = asyncio.run(fetch_source(args.repo, args.pr))
    print("Testing pinned PR commit:", manifest["sha"], flush=True)
    c = client()
    app = modal.App.lookup(settings().modal_app_name, create_if_missing=True, client=c)
    with modal.enable_output():
        sandbox = modal.Sandbox.create(
            "sleep",
            "600",
            app=app,
            image=sandbox_image(),
            client=c,
            timeout=600,
            cpu=2,
            memory=4096,
            workdir="/opt/litereview",
        )
        try:
            print("Sandbox:", sandbox.object_id, flush=True)
            sandbox.filesystem.write_bytes(raw, "/tmp/pr.tar.gz")
            sandbox.filesystem.write_bytes(json.dumps(manifest).encode(), "/tmp/pr-manifest.json")
            process = sandbox.exec("python", "-u", "-c", REMOTE, timeout=480, bufsize=1)
            for line in process.stdout:
                print(line, end="", flush=True)
            process.wait()
            if process.returncode:
                print(process.stderr.read()[-6000:])
                raise RuntimeError("Modal application smoke test failed")
            for name in ["browser-internet", "agents", "agent-editor", "desktop"]:
                filename = "app-smoke-" + name + ".png"
                (folder / filename).write_bytes(sandbox.filesystem.read_bytes("/tmp/" + filename))
            result = json.loads(sandbox.filesystem.read_bytes("/tmp/app-smoke-result.json"))
            result.update(
                {
                    "repo": args.repo,
                    "pr": args.pr,
                    "sandbox_id": sandbox.object_id,
                    "tested_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        finally:
            sandbox.terminate()
            print("Sandbox terminated", flush=True)
    result["vision"] = asyncio.run(verify_vision(folder, args.model))
    (folder / "app-smoke-result.json").write_text(json.dumps(result, indent=2))
    print("PASS: real gateway model read the app name, page heading, and button from the desktop screenshot")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
