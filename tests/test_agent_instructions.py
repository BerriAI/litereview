import asyncio
import json
import shlex
import sys
from unittest.mock import AsyncMock

import pytest
from test_harnesses import seed_job

from app import reviews
from app.db import Job, Run, Session
from runner import harness
from runner import tools as runner_tools
from runner.instructions import agent_instructions
from runner.timing import ReviewClock
from runner.tools import Toolset


def test_codex_uses_saved_instructions_as_base_on_start_and_resume(tmp_path, monkeypatch):
    monkeypatch.setattr(harness, "STATE", tmp_path)
    cfg = {"harness": "codex", "capabilities": ["terminal"], "model": "model"}
    instructions = "Write a sarcastic review in French.\nPreserve this exact task."
    for resume_id in (None, "existing-thread"):
        args = harness.command(cfg, "http://127.0.0.1:8000", instructions, "immutable PR data", {}, resume_id)
        settings = dict(arg.split("=", 1) for i, arg in enumerate(args) if i and args[i - 1] == "-c")
        path = json.loads(settings["model_instructions_file"])
        assert (tmp_path / "agent-instructions.md").read_text() == instructions
        assert path == str(tmp_path / "agent-instructions.md")
        assert args[-1] == "-"
        assert "immutable PR data" not in args
        assert instructions not in args
        if resume_id:
            assert args[-3:] == ["resume", resume_id, "-"]


def test_instruction_contract_preserves_task_and_skills_without_duplicate_persona():
    task = "Discuss maintainability in limericks. Do not execute tests."
    skills = "Skill: concise\nKeep each observation focused."
    result = agent_instructions({"instructions": task}, ReviewClock(0), skills)
    assert result.startswith("# Agent instructions\n" + task)
    assert result.count(task) == result.count(skills) == 1
    assert "voice" in result and "required=false" in result
    assert "You are a PR reviewer" not in result


@pytest.mark.parametrize(
    "required,status,expected",
    [(False, "not_run", "completed"), (True, "not_run", "incomplete"), (True, "failed", "completed")],
)
async def test_check_outcomes_control_completion_and_keep_agent_response(client, required, status, expected):
    seed_job()
    with Session() as db:
        run = db.get(Run, "test-run")
        run.snapshot = {"harness_check": True, "check_word": ""}
        job = db.get(Job, "test-job")
        job.config = {**job.config, "capabilities": [], "name": "critic", "target_kind": "agent"}
        db.commit()
    summary = "This equality check is doing an impressive impersonation of a compatibility check."
    result = {
        "status": "completed", "summary": summary, "findings": [], "limitations": [],
        "checks": [{"name": "Rust tests", "status": status, "required": required, "details": "Observed test outcome."}],
    }
    response = client.post(
        "/api/internal/jobs/test-job/finish", headers={"authorization": "Bearer job-token"}, json=result
    )
    assert response.status_code == 200
    await reviews.publish("test-run")
    with Session() as db:
        job, run = db.get(Job, "test-job"), db.get(Run, "test-run")
        assert run.status == expected
        assert bool(job.result["limitations"]) == (required and status == "not_run")
        body = reviews.render_comment(run, [job])
        assert summary in body.split("<details>", 1)[0]
        assert ("Review incomplete." in body) == (expected == "incomplete")
        assert "Rust tests" in body and "Observed test outcome." in body
        if not required and status == "not_run":
            assert "Completed · Some checks not run." in body


async def test_dependency_commands_have_no_implicit_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(runner_tools, "REPO", tmp_path)
    limits = []
    wait_for = asyncio.wait_for

    async def observed_wait(awaitable, timeout):
        limits.append(timeout)
        return await wait_for(awaitable, timeout)

    monkeypatch.setattr(runner_tools.asyncio, "wait_for", observed_wait)
    toolset = Toolset({"config": {"capabilities": ["terminal"], "mcp_definitions": []}}, AsyncMock())
    command = shlex.quote(sys.executable) + " -c 'print(42)'"
    result = await toolset.execute("terminal", {"command": command})
    assert result["exit_code"] == 0 and result["output"].strip() == "42"
    assert limits == [None]
    await toolset.execute("terminal", {"command": command, "timeout_seconds": 900})
    assert limits[-1] == 900
