"""Launch Codex with the shared result and tool contract."""

import asyncio
import json
import os
import signal
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryFile

from .bridge import Bridge
from .catalog import HARNESS_LABELS, HARNESS_VERSIONS

ROOT = Path("/opt/litereview")
STATE = Path("/workspace/litereview-run")


def command(cfg, url, instructions, prompt, env, resume_id=None):
    if cfg.get("harness", "codex") != "codex":
        raise ValueError("LiteReview uses Codex. Start a new review.")
    executable = ROOT / "harness/node_modules/.bin"
    terminal = "terminal" in cfg["capabilities"]
    instructions_path = STATE / "agent-instructions.md"
    instructions_path.write_text(instructions)
    config = {
        "model_instructions_file": str(instructions_path),
        "model": cfg["model"],
        "model_provider": "litereview",
        "approval_policy": "never",
        "features.shell_tool": terminal,
        "features.multi_agent": True,
        "web_search": "disabled",
        "analytics.enabled": False,
        "model_providers.litereview": {
            "name": "LiteReview",
            "base_url": url + "/v1",
            "wire_api": "responses",
            "env_key": "LITEREVIEW_HARNESS_KEY",
        },
        "mcp_servers.litereview": {
            "url": url + "/mcp",
            "required": True,
            # Codex requires finite MCP deadlines; use Modal's full sandbox lifetime.
            "startup_timeout_sec": 24 * 60 * 60,
            "tool_timeout_sec": 24 * 60 * 60,
        },
    }
    if cfg.get("reasoning_effort"):
        config["model_reasoning_effort"] = cfg["reasoning_effort"]
    env["LITEREVIEW_HARNESS_KEY"] = "sandbox-scoped"
    # Config lives outside the PR and the checkout is never trusted for executable hooks/plugins.
    args = [
        str(executable / "codex"),
        "exec",
        "--json",
        "--skip-git-repo-check",
        "--sandbox",
        "danger-full-access",
        "-C",
        str(STATE),
    ]

    def toml(value):
        if isinstance(value, dict):
            return "{" + ", ".join(f"{key} = {toml(item)}" for key, item in value.items()) + "}"
        return json.dumps(value)

    for key, value in config.items():
        args += ["-c", key + "=" + toml(value)]
    return args + (["resume", resume_id, "-"] if resume_id else ["-"])


async def stream_lines(stream):
    """Read JSON events without StreamReader's fixed maximum line length."""
    pending = bytearray()
    searched = 0
    while chunk := await stream.read(64 * 1024):
        pending.extend(chunk)
        start = 0
        while (end := pending.find(b"\n", searched)) != -1:
            yield bytes(pending[start:end + 1])
            start = searched = end + 1
        searched = len(pending) - start
        del pending[:start]
    if pending:
        yield bytes(pending)


async def run_external(tools, relay, data, system, prompt):
    cfg = data["config"]
    harness = "codex"
    STATE.mkdir(parents=True, exist_ok=True)
    instructions = system
    # Keep PR data outside Codex's bounded turn input as well as OS argv limits.
    context_path = STATE / "review-context.json"
    context_path.write_text(prompt, encoding="utf-8")
    prompt = (
        f"Review the pull request described in {context_path}. "
        "Read this local JSON file for the PR metadata and sandbox environment. "
        "The complete diff is also available through lr_read_diff; use offset and length "
        "to inspect large changes in sections. Follow your configured review instructions "
        "and submit the result through lr_finish_review."
    )
    bridge = Bridge(tools, relay, cfg)
    await relay.event(f"Starting {HARNESS_LABELS[harness]} {HARNESS_VERSIONS[harness]}")
    async with bridge.serve() as url:
        env = tools.child_env()
        env["PYTHONPATH"] = str(ROOT)
        thread_id = None
        while not bridge.finished.is_set():
            args = command(cfg, url, instructions, prompt, env, resume_id=thread_id)
            # PR diffs can exceed exec's per-argument limit. A file-backed stdin
            # preserves the full prompt without pipe backpressure during startup.
            with TemporaryFile() as prompt_input:
                prompt_input.write(prompt.encode("utf-8"))
                prompt_input.seek(0)
                process = await asyncio.create_subprocess_exec(
                    *args,
                    cwd=STATE,
                    env=env,
                    stdin=prompt_input,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )

            async def drain():
                nonlocal thread_id
                with open(STATE / "harness.log", "ab") as log:
                    async for line in stream_lines(process.stdout):
                        log.write(line)
                        log.flush()
                        try:
                            event = json.loads(line)
                            if event.get("type") == "thread.started":
                                thread_id = event.get("thread_id")
                            if event.get("lr_event"):
                                await relay.event(str(event["lr_event"]))
                            item = event.get("item", {})
                            if (
                                item.get("type") == "command_execution"
                                and event.get("type") == "item.completed"
                            ):
                                await relay.event("Using shell")
                            if event.get("type") == "tool_use":
                                await relay.event(
                                    "Using " + str(event.get("part", {}).get("tool", "harness tool"))
                                )
                        except (ValueError, TypeError):
                            pass
                return await process.wait()

            output = asyncio.create_task(drain())
            finished = asyncio.create_task(bridge.finished.wait())
            try:
                await asyncio.wait({output, finished}, return_when=asyncio.FIRST_COMPLETED)
                if not bridge.finished.is_set():
                    await output
                    if (
                        thread_id
                        and process.returncode == 0
                        and cfg.get("minimum_review_seconds")
                        and not bridge.model_error
                    ):
                        prompt = (
                            "Continue this review in the same thread. Your structured review has not been "
                            "accepted. Check lr_review_clock, investigate remaining edge cases and verify "
                            "evidence until the minimum time elapses, then call lr_finish_review. "
                            "Do not sleep or busy-wait. Retain your instructions, skills and tool scope."
                        )
                        await relay.event("Continuing review in the same harness session")
                        continue
                    print(
                        "Harness diagnostic:\n" + (STATE / "harness.log").read_text(errors="replace"),
                        flush=True,
                    )
                    raise RuntimeError(
                        bridge.model_error
                        or f"{HARNESS_LABELS[harness]} exited without submitting a review (exit {process.returncode}). Check the harness/model combination and run logs."
                    )
            finally:
                if process.returncode is None:
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGTERM)
                    try:
                        await asyncio.wait_for(asyncio.shield(output), 5)
                    except TimeoutError:
                        with suppress(ProcessLookupError):
                            os.killpg(process.pid, signal.SIGKILL)
                        await output
                finished.cancel()
                with suppress(asyncio.CancelledError):
                    await finished
        await relay.event("Review clock at finish: " + json.dumps(tools.clock.status()))
        await relay("finish", bridge.result)
