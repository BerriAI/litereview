import asyncio
import base64
import os
import signal
import subprocess
from pathlib import Path

from .finding_assessment import FindingAssessment
from .preview import PreviewDocument
from .timing import ReviewClock

REPO = Path("/workspace/repo")
SKILLS = Path("/workspace/skills")


def function(name, description, properties, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


S = {"type": "string"}
N = {"type": "integer"}


def definitions(cfg):
    tools = [
        function(
            "read_file",
            "Read a repository or attached skill file. Paths are relative to repo; use skills/<skill-id>/... for skills. Images are returned visually.",
            {"path": S, "start_line": N, "end_line": N},
            ["path"],
        ),
        function("list_files", "List repository files, optionally within a directory.", {"path": S}),
        function(
            "search", "Search repository text for a literal string.", {"query": S, "path": S}, ["query"]
        ),
        function("read_diff", "Read the immutable PR diff. Optionally select an offset and length.",
                 {"offset": N, "length": N}),
        function(
            "finish_review",
            "Submit the configured task in the agent's requested voice. Summary is the primary response; findings holds concrete issues when relevant. Record verification in checks, distinguishing required work from optional checks. Limitations is only for blockers to completing the configured task. Optional checks not run do not make it incomplete. Use [] for empty arrays.",
            {
                "summary": S,
                "findings": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "title": S,
                            "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                            "explanation": S,
                            "file": S,
                            "line": N,
                            "evidence": {"type": "array", "items": S},
                        },
                        "required": ["title", "severity", "explanation"],
                    },
                },
                "limitations": {"type": "array", "items": S},
                "checks": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": S,
                            "rule_id": S,
                            "status": {"type": "string", "enum": ["passed", "failed", "not_run"]},
                            "required": {"type": "boolean"},
                            "details": S,
                        },
                        "required": ["name", "status", "required", "details"],
                    },
                },
            },
            ["summary", "findings", "limitations"],
        ),
    ]
    if cfg.get("show_score") and cfg.get("fusion_stage") != "branch" and cfg.get("task") != "reply" and cfg.get("output_type") != "preview":
        schema = tools[-1]["function"]["parameters"]
        schema["properties"]["finding_assessments"] = {
            "type": "array", "items": FindingAssessment.model_json_schema(),
            "description": "Recheck each open finding in finding_history at the current commit. Return its exact id, open/fixed/uncertain status and evidence. Do not repeat it in findings. Use [] when there is no open history.",
        }
        schema["required"].append("finding_assessments")
    if cfg.get("output_type") == "preview":
        schema = tools[-1]["function"]["parameters"]
        preview = PreviewDocument.model_json_schema()
        schema["$defs"] = preview.pop("$defs", {})
        schema["properties"]["preview"] = preview
        if cfg.get("fusion_stage") != "branch":
            schema["required"].append("preview")
    if cfg.get("task") == "reply":
        schema = tools[-1]["function"]["parameters"]
        schema["properties"]["rebuttal"] = {
            "type": "object",
            "properties": {"verdict": {"type": "string", "enum": ["withdraw", "uphold", "uncertain"]},
                           "reason": {"type": "string", "minLength": 1},
                           "evidence": {"type": "array", "items": {"type": "string", "minLength": 1}}},
            "required": ["verdict", "reason", "evidence"],
            "allOf": [{"if": {"properties": {"verdict": {"const": "withdraw"}}},
                       "then": {"properties": {"evidence": {"minItems": 1}}}}],
        }
        schema["required"].append("rebuttal")
    caps = cfg["capabilities"]
    if {"browser", "computer"} & set(caps):
        tools.append(function("recording", "Record the shared browser/desktop at 1280x900. Start before the walkthrough; stop to save an MP4 artifact. Record only the demonstration, never credentials. Stop before finish_review and attach the returned artifact_id to the preview.",
                              {"action": {"type": "string", "enum": ["start", "stop"]}, "name": S}, ["action"]))
    if cfg.get("minimum_review_seconds"):
        tools.append(function("review_clock", "Check the review start, current time, and time remaining.", {}))
    if "terminal" in caps:
        tools.append(
            function(
                "terminal",
                "Run a shell command in the disposable repository. Use tests and diagnostic commands. Never push code or contact unrelated targets.",
                {"command": S, "timeout_seconds": N},
                ["command"],
            )
        )
    if "browser" in caps:
        tools.append(
            function(
                "browser",
                'Operate Chromium. Every action returns a screenshot and an accessibility tree. For tree roles use role selectors, e.g. role=textbox[name="Username"] or role=button[name="Log In"]. CSS #username and input[name="password"] also work. Select exactly one element. Use fill with text to enter a whole field value; press is for keyboard shortcuts. A bare role name like textbox is not a CSS selector.',
                {
                    "action": {
                        "type": "string",
                        "enum": ["navigate", "inspect", "click", "fill", "press", "scroll", "screenshot"],
                    },
                    "url": S,
                    "selector": S,
                    "text": S,
                    "key": S,
                    "x": N,
                    "y": N,
                },
                ["action"],
            )
        )
    if "computer" in caps:
        tools.append(
            function(
                "computer",
                'See and operate the Linux desktop at 1280x900. Every action returns a fresh screenshot. Use screenshot first. Keyboard shortcuts are arrays such as ["ctrl", "l"].',
                {
                    "action": {
                        "type": "string",
                        "enum": [
                            "screenshot",
                            "click",
                            "double_click",
                            "move",
                            "drag",
                            "type",
                            "key",
                            "scroll",
                        ],
                    },
                    "x": N,
                    "y": N,
                    "text": S,
                    "keys": {"type": "array", "items": S},
                    "amount": N,
                },
                ["action"],
            )
        )
    for conn in cfg.get("mcp_definitions", []):
        for tool in conn["tools"]:
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": mcp_name(conn["id"], tool["name"]),
                        "description": tool["description"],
                        "parameters": tool["inputSchema"],
                    },
                }
            )
    return tools


def mcp_name(cid, name):
    import hashlib

    return "mcp_" + hashlib.sha256((cid + ":" + name).encode()).hexdigest()[:32]


class Toolset:
    @property
    def recording_active(self):
        return self.recorder.process is not None

    def __init__(self, data, relay):
        from .recording import Recording
        self.recorder = Recording()
        self.data = data
        self.relay = relay
        self.caps = data["config"]["capabilities"]
        self.allowed = {t["function"]["name"] for t in definitions(data["config"])}
        self.pw = self.context = self.page = self.browser = None
        self.processes = []
        self.artifacts = 0
        self.visual_calls = 0
        self.clock = ReviewClock(data["config"].get("minimum_review_seconds", 0))

    async def init(self):
        if {"computer", "browser"} & set(self.caps):
            os.environ["DISPLAY"] = ":99"
            Path.home().joinpath(".Xauthority").touch(exist_ok=True)
            self.processes.append(
                subprocess.Popen(
                    ["Xvfb", ":99", "-screen", "0", "1280x900x24", "-ac"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            await asyncio.sleep(0.8)
            self.processes.append(
                subprocess.Popen(["openbox"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            )
            from playwright.async_api import async_playwright

            self.pw = await async_playwright().start()
            self.browser = await self.pw.chromium.launch(
                headless=False, timeout=0,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--window-size=1280,900"]
            )
            self.context = await self.browser.new_context(no_viewport=True)
            self.context.set_default_timeout(0)
            self.context.set_default_navigation_timeout(0)
            self.page = await self.context.new_page()
        repo = self.data["repository"]
        if self.caps:
            os.environ.update(self.data.get("test_secrets", {}))
            if repo.get("setup_command"):
                import time

                started = time.monotonic()
                await self.relay.event("Running repository setup; reconciling dependencies with this revision")
                result = await self.shell(repo["setup_command"])
                if result["exit_code"] != 0:
                    raise RuntimeError("Repository setup command failed: " + result["output"])
                await self.relay.event(f"Repository setup completed in {time.monotonic() - started:.2f}s")
            if repo.get("start_command"):
                log = open("/tmp/litereview-app.log", "w")
                p = subprocess.Popen(
                    repo["start_command"],
                    shell=True,
                    cwd=REPO,
                    env=self.child_env(),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                self.processes.append(p)
                log.close()
            if repo.get("readiness_url"):
                import httpx

                async with httpx.AsyncClient(timeout=None) as client:
                    while True:
                        if repo.get("start_command") and p.poll() is not None:
                            raise RuntimeError("The app exited before becoming ready at the configured URL.")
                        try:
                            r = await client.get(repo["readiness_url"])
                            if r.status_code < 500:
                                break
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(1)

    def child_env(self):
        return {k: v for k, v in os.environ.items() if not k.startswith(("LR_", "MODAL_", "GITHUB_"))}

    def path(self, value="."):
        if value.startswith("skills/"):
            path = SKILLS / value.removeprefix("skills/")
        else:
            path = REPO / value
        resolved = path.resolve()
        if not (resolved.is_relative_to(REPO) or resolved.is_relative_to(SKILLS)):
            raise ValueError("Path is outside the repository and attached skills.")
        return resolved

    async def shell(self, command, timeout=None):
        process = await asyncio.create_subprocess_exec(
            "/bin/bash",
            "-lc",
            command,
            cwd=REPO,
            env=self.child_env(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        output = bytearray()

        async def drain():
            while chunk := await process.stdout.read(8192):
                output.extend(chunk)
            await process.wait()

        timed_out = False
        try:
            await asyncio.wait_for(drain(), max(1, timeout) if timeout is not None else None)
        except (TimeoutError, asyncio.CancelledError) as exc:
            timed_out = isinstance(exc, TimeoutError)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()
            if not timed_out:
                raise
        return {
            "exit_code": process.returncode,
            "output": output.decode(errors="replace"),
            "timed_out": timed_out,
        }

    async def picture(self, raw, label):
        self.visual_calls += 1
        encoded = base64.b64encode(raw).decode()
        artifact = None
        try:
            result = await self.relay("artifact", {"data": encoded, "name": label})
            artifact = result["id"]
            self.artifacts += 1
        except Exception:
            pass
        return {"artifact_id": artifact, "image": "data:image/png;base64," + encoded}

    async def screen(self):
        target = "/tmp/litereview-screen.png"
        subprocess.run(
            ["scrot", "--overwrite", target], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        return await self.picture(Path(target).read_bytes(), "Desktop screenshot")

    async def execute(self, name, args):
        if name not in self.allowed:
            raise ValueError("This tool is not enabled for this agent.")
        if name == "review_clock":
            return self.clock.status()
        if name == "recording":
            return await (self.recorder.start(args.get("name", "Walkthrough"))
                          if args["action"] == "start" else self.recorder.stop(self.relay))
        if name == "read_file":
            path = self.path(args["path"])
            if path.suffix.lower() == ".png":
                return await self.picture(path.read_bytes(), path.name)
            lines = path.read_text(errors="replace").splitlines()
            start = max(1, args.get("start_line", 1))
            end = args.get("end_line", len(lines))
            return {
                "content": "\n".join(
                    f"{i + 1}: {line}" for i, line in enumerate(lines[start - 1 : end], start - 1)
                ),
                "total_lines": len(lines),
            }
        if name == "list_files":
            path = self.path(args.get("path", "."))
            items = []
            for root, dirs, files in os.walk(path):
                dirs[:] = [
                    d
                    for d in dirs
                    if d not in (".git", "node_modules", ".venv") and not (Path(root) / d).is_symlink()
                ]
                items.extend(
                    str((Path(root) / f).relative_to(path))
                    for f in files
                    if not (Path(root) / f).is_symlink()
                )
            return {"files": items, "truncated": False}
        if name == "search":
            path = self.path(args.get("path", "."))
            p = await asyncio.create_subprocess_exec(
                "rg",
                "-n",
                "-F",
                "--",
                args["query"],
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                out, _ = await p.communicate()
            except asyncio.CancelledError:
                p.kill()
                await p.wait()
                raise
            return {"matches": out.decode(errors="replace")}
        if name == "read_diff":
            diff = self.data["pr"]["diff"]
            offset = max(0, args.get("offset", 0))
            end = offset + max(1, args["length"]) if args.get("length") is not None else len(diff)
            return {
                "diff": diff[offset:end],
                "next_offset": end if end < len(diff) else None,
            }
        if name == "terminal":
            return await self.shell(args["command"], args.get("timeout_seconds"))
        if name == "browser":
            return await self.browse(args)
        if name == "computer":
            return await self.computer(args)
        for conn in self.data["config"].get("mcp_definitions", []):
            for tool in conn["tools"]:
                if mcp_name(conn["id"], tool["name"]) == name:
                    result = await self.relay(
                        "mcp", {"connection_id": conn["id"], "name": tool["name"], "arguments": args}
                    )
                    text, images = [], []
                    for content in result.get("content", []):
                        if content["type"] == "text":
                            text.append(content["text"])
                        if content["type"] == "image":
                            images.append("data:" + content["mimeType"] + ";base64," + content["data"])
                    return {
                        "content": "\n".join(text),
                        "images": images,
                        "isError": result.get("isError", False),
                    }
        raise ValueError("Unknown tool.")

    async def browse(self, a):
        page = self.context.pages[-1] if self.context.pages else await self.context.new_page()
        action = a["action"]
        if action == "navigate":
            from urllib.parse import urlparse

            if urlparse(a["url"]).scheme not in ("http", "https"):
                raise ValueError("Browser URLs must use HTTP or HTTPS.")
            await page.goto(a["url"], wait_until="domcontentloaded", timeout=0)
        elif action == "click":
            await page.locator(a["selector"]).click(timeout=0)
        elif action == "fill":
            await page.locator(a["selector"]).fill(a["text"], timeout=0)
        elif action == "press":
            await page.keyboard.press(a["key"])
        elif action == "scroll":
            await page.mouse.wheel(a.get("x", 0), a.get("y", 500))
        elif action not in ("inspect", "screenshot"):
            raise ValueError("Unsupported browser action.")
        await page.wait_for_timeout(350)
        self.page = page
        result = await self.picture(
            await page.screenshot(type="png"), "Browser: " + (await page.title())
        )
        result.update(
            {"url": page.url, "page": await page.locator("body").aria_snapshot(timeout=0)}
        )
        return result

    async def computer(self, a):
        import pyautogui as gui

        gui.FAILSAFE = False
        gui.PAUSE = 0.1
        action = a["action"]
        x, y = min(1279, max(0, a.get("x", 0))), min(899, max(0, a.get("y", 0)))
        if action == "click":
            gui.click(x, y)
        elif action == "double_click":
            gui.doubleClick(x, y, interval=0.1)
        elif action == "move":
            gui.moveTo(x, y)
        elif action == "drag":
            gui.dragTo(x, y, duration=0.5)
        elif action == "type":
            # xdotool handles UTF-8 text better than keyboard-layout-dependent key events.
            subprocess.run(
                ["xdotool", "type", "--clearmodifiers", "--", a.get("text", "")], check=True
            )
        elif action == "key":
            keys = a.get("keys", [])
            if not keys or any(k not in gui.KEYBOARD_KEYS for k in keys):
                raise ValueError("Use valid key names such as ctrl, shift, enter, tab, esc.")
            gui.hotkey(*keys)
        elif action == "scroll":
            gui.scroll(a.get("amount", -3))
        elif action != "screenshot":
            raise ValueError("Unsupported computer action.")
        await asyncio.sleep(0.4)
        return await self.screen()

    async def close(self):
        await self.recorder.close()
        if self.browser:
            await self.browser.close()
        if self.pw:
            await self.pw.stop()
        for process in reversed(self.processes):
            with __import__("contextlib").suppress(ProcessLookupError):
                process.terminate()
