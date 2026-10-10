"""Runs inside a fresh Modal sandbox. No provider or GitHub credentials are present."""

import asyncio
import base64
import io
import json
import os
import tarfile
from pathlib import PurePosixPath

import httpx

from .catalog import review_capabilities
from .instructions import agent_instructions
from .tools import REPO, SKILLS, Toolset, definitions


class Relay:
    def __init__(self):
        api = os.environ.pop("LR_API_URL")
        job = os.environ.pop("LR_JOB_ID")
        token = os.environ.pop("LR_JOB_TOKEN")
        self.client = httpx.AsyncClient(
            base_url=f"{api}/api/internal/jobs/{job}/",
            headers={"Authorization": "Bearer " + token},
            timeout=120,
        )

    async def __call__(self, endpoint, data=None):
        for attempt in range(3):
            try:
                response = await (
                    self.client.get(endpoint) if data is None else self.client.post(endpoint, json=data)
                )
                if response.is_success:
                    return response.json()
                if response.status_code < 500 or attempt == 2:
                    detail = response.json().get("detail", "Request failed")
                    raise RuntimeError(str(detail))
            except (httpx.TimeoutException, httpx.NetworkError):
                if attempt == 2:
                    raise RuntimeError("The review service could not be reached.") from None
            await asyncio.sleep(2**attempt)

    async def event(self, message):
        try:
            await self("event", {"message": message})
        except Exception:
            pass


def extract_archive(raw, root):
    root.mkdir(parents=True, exist_ok=True)
    total = 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        for index, member in enumerate(tar):
            if index > 30000:
                raise ValueError("Repository archive contains too many files.")
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
                total += member.size
                if total > 250_000_000:
                    raise ValueError("Expanded repository exceeds 250 MB.")
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(tar.extractfile(member).read())
                path.chmod(0o755 if member.mode & 0o111 else 0o644)
            # Symlinks and device entries are never materialized.


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


def prune_images(messages):
    image_messages = [
        m
        for m in messages
        if isinstance(m.get("content"), list) and any(c.get("type") == "image_url" for c in m["content"])
    ]
    for old in image_messages[:-4]:
        old["content"] = [c for c in old["content"] if c.get("type") != "image_url"]
        old["content"].append(
            {
                "type": "text",
                "text": "Earlier screenshots omitted from the active context; use a screenshot tool to inspect the current state again.",
            }
        )


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
    data["config"]["capabilities"] = review_capabilities(data["config"].get("capabilities", []))
    response = await relay.client.get("archive")
    response.raise_for_status()
    extract_archive(response.content, REPO)
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
        messages = [
            {
                "role": "system",
                "content": system,
            },
            {
                "role": "user",
                "content": json.dumps(
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
                )[:70000],
            },
        ]
        if cfg.get("harness", "builtin") != "builtin":
            from .harness import run_external

            await run_external(tools, relay, data, system, messages[-1]["content"])
            return
        if "computer" in cfg["capabilities"]:
            shot = await tools.screen()
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Initial desktop screenshot; inspect this before interacting.",
                        },
                        {"type": "image_url", "image_url": {"url": shot["image"]}},
                    ],
                }
            )
        rounds = 0
        while data.get("max_rounds") is None or rounds < data["max_rounds"]:
            rounds += 1
            prune_images(messages)
            reply = await relay("model", {"messages": messages, "tools": definitions(cfg)})
            messages.append(reply)
            calls = reply.get("tool_calls", [])
            if not calls:
                messages.append(
                    {
                        "role": "user",
                        "content": "Continue inspecting with your tools, or submit your structured result with finish_review.",
                    }
                )
                continue
            visuals = []
            for call in calls:
                name = call["function"]["name"]
                await relay.event("Using " + name)
                try:
                    args = json.loads(call["function"]["arguments"])
                    if name == "finish_review":
                        tools.clock.require_elapsed()
                        if len(calls) != 1:
                            raise ValueError(
                                "Submit finish_review in a separate turn after inspecting all tool results."
                            )
                        args["status"] = "completed"
                        await relay.event("Review clock at finish: " + json.dumps(tools.clock.status()))
                        await relay("finish", args)
                        return
                    result = await tools.execute(name, args)
                    images = result.pop("images", [])
                    if result.get("image"):
                        images.append(result.pop("image"))
                    for picture in images:
                        visuals.extend(
                            [
                                {
                                    "type": "text",
                                    "text": f"Visual output of {name}; evidence artifact: {result.get('artifact_id', 'unsaved')}. Inspect the visible result.",
                                },
                                {"type": "image_url", "image_url": {"url": picture}},
                            ]
                        )
                    text = json.dumps(result, ensure_ascii=False)
                except Exception as exc:
                    text = json.dumps({"error": str(exc)[:2000]})
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": text[:65000]})
            if visuals:
                # Image inputs use standard user image parts supported by both adapters.
                messages.append({"role": "user", "content": visuals})
        await relay(
            "finish",
            {
                "status": "timed_out",
                "summary": "The agent reached its model round limit.",
                "findings": [],
                "limitations": ["The review did not finish within the configured limit."],
            },
        )
    finally:
        await tools.close()


async def main():
    relay = Relay()
    try:
        data = await relay("bootstrap")
        async with asyncio.timeout(data.get("timeout")):
            await run(relay, data)
    except Exception as exc:
        while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
            exc = exc.exceptions[0]
        status = "timed_out" if isinstance(exc, TimeoutError) else "failed"
        # Do not print headers, credentials, or provider request bodies.
        message = "Execution time limit reached." if status == "timed_out" else str(exc)[:2000]
        try:
            await relay(
                "finish",
                {
                    "status": status,
                    "summary": "The review could not complete.",
                    "findings": [],
                    "limitations": [message],
                },
            )
        except Exception:
            print("Unable to submit result; the service will mark this job incomplete.", flush=True)
    finally:
        await relay.client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
