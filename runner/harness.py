"""Launch one real harness, with a shared result/tool contract."""

import asyncio
import json
import os
import signal
from contextlib import suppress
from pathlib import Path

from .bridge import Bridge
from .catalog import HARNESS_LABELS, HARNESS_VERSIONS

ROOT = Path("/opt/litereview")
STATE = Path("/workspace/litereview-run")


def opencode_config(cfg, url, instructions):
    v2 = cfg["harness"] == "opencode-v2"
    anthropic = cfg["connection"]["type"] == "anthropic"
    package = "@ai-sdk/anthropic" if anthropic else "@ai-sdk/openai-compatible"
    model = {
        "name": cfg["model"],
        "limit": {"context": 128000, "output": 8192},
        "modalities": {"input": ["text", "image"], "output": ["text"]},
    }
    permission = {
        "*": "deny",
        "read": "allow",
        "glob": "allow",
        "grep": "allow",
        "litereview_*": "allow",
        "task": "allow",
        "edit": "allow",
        "write": "allow",
        "apply_patch": "allow",
        "external_directory": "allow",
    }
    permission["shell" if v2 else "bash"] = "allow" if "terminal" in cfg["capabilities"] else "deny"
    provider = {
        "name": "LiteReview",
        "npm": package,
        "options": {"baseURL": url + "/v1", "apiKey": "sandbox-scoped"},
        "models": {cfg["model"]: model},
    }
    # V2 explicitly supports V1 configuration normalization. Pinning keeps that contract testable.
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": "litereview/" + cfg["model"],
        "small_model": "litereview/" + cfg["model"],
        "provider": {"litereview": provider},
        "enabled_providers": ["litereview"],
        "default_agent": "review",
        "agent": {"review": {"mode": "primary", "prompt": instructions, "permission": permission}},
        "permission": permission,
        "mcp": (
            {
                "servers": {
                    "litereview": {"type": "remote", "url": url + "/mcp", "oauth": False, "codemode": False}
                }
            }
            if v2
            else {"litereview": {"type": "remote", "url": url + "/mcp", "oauth": False}}
        ),
        "share": "disabled",
        "autoupdate": False,
        "plugin": [],
    }


def command(cfg, url, instructions, prompt, env, resume_id=None):
    harness = cfg["harness"]
    executable = ROOT / "harness/node_modules/.bin"
    terminal = "terminal" in cfg["capabilities"]
    config = {"config": cfg, "url": url, "instructions": instructions, "prompt": prompt, "state": str(STATE)}
    path = STATE / "input.json"
    path.write_text(json.dumps(config))
    if harness in ("pi", "claude-code"):
        return ["node", str(ROOT / "runner" / (harness + ".mjs")), str(path)]
    if harness == "deepagents":
        return ["python", "-m", "runner.deepagent", str(path)]
    if harness.startswith("opencode-"):
        conf = opencode_config(cfg, url, instructions)
        config_path = STATE / "opencode.json"
        config_path.write_text(json.dumps(conf))
        env.update(
            {
                "OPENCODE_CONFIG": str(config_path),
                "OPENCODE_CONFIG_CONTENT": json.dumps(conf),
                "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
                "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
                "OPENCODE_DISABLE_CLAUDE_CODE": "true",
                "OPENCODE_DISABLE_AUTOUPDATE": "true",
            }
        )
        return [
            str(executable / "opencode"),
            "run",
            *(["--continue"] if resume_id else []),
            "--format",
            "json",
            "--agent",
            "review",
            "--model",
            "litereview/" + cfg["model"],
            prompt,
        ]
    if harness == "codex":
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
                "startup_timeout_sec": 40,
                "tool_timeout_sec": 180,
            },
        }
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
        return args + (["resume", resume_id, prompt] if resume_id else [prompt])
    raise ValueError("Unknown harness.")


async def run_external(tools, relay, data, system, prompt):
    cfg = data["config"]
    harness = cfg["harness"]
    STATE.mkdir(parents=True, exist_ok=True)
    instructions = system
    bridge = Bridge(tools, relay, cfg)
    await relay.event(f"Starting {HARNESS_LABELS[harness]} {HARNESS_VERSIONS[harness]}")
    async with bridge.serve() as url:
        env = tools.child_env()
        env["PYTHONPATH"] = str(ROOT)
        thread_id = None
        while not bridge.finished.is_set():
            args = command(cfg, url, instructions, prompt, env, resume_id=thread_id)
            process = await asyncio.create_subprocess_exec(
                *args,
                cwd=STATE,
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                limit=16_000_000,
            )

            diagnostic = bytearray()

            async def drain():
                nonlocal thread_id
                with open(STATE / "harness.log", "wb") as log:
                    total = 0
                    async for line in process.stdout:
                        diagnostic.extend(line[-8000:])
                        del diagnostic[:-8000]
                        if total < 2_000_000:
                            log.write(line)
                            log.flush()
                            total += len(line)
                        try:
                            event = json.loads(line)
                            if event.get("type") == "thread.started":
                                thread_id = event.get("thread_id")
                            if event.get("lr_event"):
                                await relay.event(str(event["lr_event"])[:200])
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
                        (harness == "codex" and thread_id or harness.startswith("opencode-"))
                        and process.returncode == 0
                        and cfg.get("minimum_review_seconds")
                        and not bridge.model_error
                    ):
                        thread_id = thread_id or "continue"
                        prompt = (
                            "Continue this review in the same thread. Your structured review has not been "
                            "accepted. Check lr_review_clock, investigate remaining edge cases and verify "
                            "evidence until the minimum time elapses, then call lr_finish_review. "
                            "Do not sleep or busy-wait. Retain your instructions, skills and tool scope."
                        )
                        await relay.event("Continuing review in the same harness session")
                        continue
                    print(
                        "Harness diagnostic:\n" + diagnostic.decode(errors="replace"),
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
