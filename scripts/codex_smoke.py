"""Exercise the pinned Codex CLI against a local model fixture; no provider credentials or inference."""

import argparse
import asyncio
import errno
import gzip
import hashlib
import json
import os
import subprocess
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

import httpx

from runner import harness
from runner.catalog import HARNESS_VERSIONS
from runner.instructions import agent_instructions
from runner.tools import Toolset


async def check(native, workdir, model, *, reply=False):
    version = subprocess.check_output([native, "--version"], text=True).strip()
    assert version == "codex-cli " + HARNESS_VERSIONS["codex"], version
    harness.ROOT = workdir / "install"
    harness.STATE = workdir / "state"
    executable = harness.ROOT / "harness/node_modules/.bin/codex"
    executable.parent.mkdir(parents=True, exist_ok=True)
    executable.symlink_to(native)
    harness.STATE.mkdir(parents=True, exist_ok=True)
    cfg = {
        "harness": "codex",
        "model": model,
        "capabilities": ["terminal"],
        "mcp_definitions": [],
        "minimum_review_seconds": 0 if reply else 60,
    }
    context = {"diff": "+ Full diff: café 世界\n" * 100_000}
    if reply:
        cfg.update(task="reply", instructions="Review for actionable bugs.")
        context = {"review": {**context, "reply": {
            "finding": {"title": "Cache may return a stale entry", "severity": "medium"},
            "comments": [{"author": "reviewer", "body": "The invalidation guard already prevents this."}],
        }}}
    prompt = json.dumps(context, ensure_ascii=False)
    instructions = (
        "Review the entire provided PR, preserve the configured voice and finish through lr_finish_review."
    )
    old_args = harness.command(cfg, "http://127.0.0.1:1", instructions, prompt, {})
    old_args[-1] = prompt
    try:
        subprocess.run(old_args, check=True, capture_output=True)
    except OSError as exc:
        assert exc.errno in (errno.E2BIG, errno.ENAMETOOLONG), exc
        old_errno = exc.errno
    else:
        raise AssertionError("Old launcher unexpectedly accepted the oversized argv")
    requests = []
    relay = AsyncMock()
    toolset = Toolset({"config": cfg}, relay)
    (workdir / "codex-home").mkdir()
    toolset.child_env = lambda: {"PATH": os.environ["PATH"], "CODEX_HOME": str(workdir / "codex-home")}
    toolset.clock.start()
    if reply:
        instructions = agent_instructions(cfg, toolset.clock, "")
    verdict = {"verdict": "withdraw", "reason": "The guard prevents the alleged path.",
               "evidence": ["cache.py:12 checks invalidation before returning the entry."]}
    submitted = {"summary": "Structured reply fixture passed", "findings": [], "limitations": [], "rebuttal": verdict}

    async def handle_request(request):
        assert request.headers["content-encoding"] == "gzip"
        body = json.loads(gzip.decompress(request.content))
        requests.append(body)
        index = len(requests)
        assert index <= (2 if reply else 3), f"Unexpected model call {index}"
        texts = [
            part.get("text", "")
            for item in body["input"]
            if item.get("role") == "user"
            for part in item.get("content", [])
            if isinstance(part, dict)
        ]
        assert any(str(harness.STATE / "review-context.json") in text for text in texts)
        assert all(len(text) < 1048576 for text in texts)
        assert (harness.STATE / "review-context.json").read_text() == prompt
        assert any(
            part.get("text", "").strip() == instructions.strip()
            for item in body["input"]
            if item.get("role") == "developer"
            for part in item.get("content", [])
        )
        assert body["model"] == cfg["model"]
        if index == 1:
            digest = hashlib.sha256(prompt.encode()).hexdigest()
            code = f'import hashlib; from pathlib import Path; p=Path("review-context.json").read_bytes(); assert hashlib.sha256(p).hexdigest()=="{digest}"; print("full-context-verified")'
            import shlex

            output = {
                "type": "custom_tool_call",
                "id": "fc_read",
                "call_id": "call_read",
                "namespace": "functions",
                "name": "exec",
                "input": "text(await tools.exec_command("
                + json.dumps({"cmd": "python -c " + shlex.quote(code), "max_output_tokens": 100})
                + "));",
            }
        elif index == 2 and reply:
            assert "full-context-verified" in json.dumps(body["input"])
            assert "An insult" in instructions and "rebuttal field" in instructions
            unsupported = {**submitted, "rebuttal": {**verdict, "evidence": []}}
            output = {
                "type": "custom_tool_call", "id": "fc_reply", "call_id": "call_reply",
                "namespace": "functions", "name": "exec",
                "input": 'const tool = ALL_TOOLS.find(t => t.name.endsWith("lr_finish_review")); '
                         'let refused = false; try { const bad = await tools[tool.name]('
                         + json.dumps(unsupported)
                         + '); refused = bad.isError === true; } catch { refused = true; } '
                         'if (!refused) throw new Error("Unsupported withdrawal was accepted"); '
                         'text(await tools[tool.name](' + json.dumps(submitted) + '));',
            }
        elif index == 2:
            assert "full-context-verified" in json.dumps(body["input"])
            output = {
                "type": "message",
                "id": "msg_first",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "Continue investigating."}],
            }
        else:
            assert any(text.startswith("Continue this review in the same thread.") for text in texts)
            assert body["prompt_cache_key"] == requests[0]["prompt_cache_key"]
            toolset.clock.started -= 60
            output = {
                "type": "custom_tool_call",
                "id": "fc_finish",
                "call_id": "call_finish",
                "namespace": "functions",
                "name": "exec",
                "input": 'const tool = ALL_TOOLS.find(t => t.name.endsWith("lr_finish_review")); text(await tools[tool.name]({summary: "Large-prompt CLI fixture passed", findings: [], limitations: []}));',
            }
        response = {
            "id": f"resp_{index}",
            "object": "response",
            "status": "completed",
            "model": cfg["model"],
            "output": [output],
            "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110},
        }
        events = [
            ("response.created", {"response": {**response, "status": "in_progress", "output": []}}),
            ("response.output_item.added", {"output_index": 0, "item": output}),
            ("response.output_item.done", {"output_index": 0, "item": output}),
            ("response.completed", {"response": response}),
        ]
        stream = "".join(
            "event: " + kind + "\ndata: " + json.dumps({"type": kind, **data}) + "\n\n"
            for kind, data in events
        )
        return httpx.Response(200, content=stream, headers={"content-type": "text/event-stream"})

    async def respond(request):
        try:
            return await handle_request(request)
        except (AssertionError, KeyError) as exc:
            traceback.print_exc()
            print("FIXTURE ASSERTION:", str(exc), flush=True)
            return httpx.Response(400, json={"error": {"message": "Fixture assertion: " + str(exc)}})

    async with httpx.AsyncClient(
        base_url="http://fixture/", transport=httpx.MockTransport(respond)
    ) as client:
        relay.client = client
        await asyncio.wait_for(
            harness.run_external(toolset, relay, {"config": cfg}, instructions, prompt), 60
        )
    assert len(requests) == (2 if reply else 3)
    assert relay.call_args.args[0] == "finish"
    assert relay.call_args.args[1]["status"] == "completed"
    if reply:
        assert relay.call_args.args[1]["rebuttal"] == verdict
    events = [
        json.loads(line)
        for line in (harness.STATE / "harness.log").read_text().splitlines()
        if line.startswith("{")
    ]
    thread_ids = [event["thread_id"] for event in events if event.get("type") == "thread.started"]
    assert len(thread_ids) == (1 if reply else 2) and len(set(thread_ids)) == 1, thread_ids
    assert all(r["prompt_cache_key"] == requests[0]["prompt_cache_key"] for r in requests)
    result = {
        "version": version,
        "prompt_bytes": len(prompt.encode()),
        "old_launch_errno": old_errno,
        "model_fixture_calls": len(requests),
        "same_thread": True,
        "same_prompt_cache_key": True,
        "full_prompt_and_instructions_preserved": True,
        "structured_review_completed": True,
        **({"structured_rebuttal_completed": True, "unsupported_withdrawal_rejected": True} if reply else {}),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codex", required=True, type=Path, help="Path to the pinned Codex executable")
    parser.add_argument("--model", default="openai/gpt-6-sol", help="Model ID sent only to the local fixture")
    parser.add_argument("--reply", action="store_true", help="Check the reply task and withdrawal evidence through the real CLI")
    args = parser.parse_args()
    with TemporaryDirectory(prefix="litereview-codex-check-") as directory:
        asyncio.run(check(args.codex.resolve(), Path(directory), args.model, reply=args.reply))
