import asyncio
import json
import os
import sys
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_harnesses import seed_job
from test_reviews import create, mock_github, payload, setup, webhook

from app import reviews
from app.db import Job, Resource, ReviewTiming, Run, Session
from runner import harness
from runner.bridge import Bridge
from runner.timing import ReviewClock
from runner.tools import Toolset


@pytest.mark.parametrize("body", ["@litereview security frontend security", "light review security frontend"])
def test_comments_only_select_names(body):
    assert reviews.command_requests(body) == [{"name": "security"}, {"name": "frontend"}]


@pytest.mark.parametrize(
    "tail",
    ["t=10 security", "security t=5", "security t=5m", "security t=0", "security t=-1", "security t=1.5"],
)
def test_comment_time_options_are_rejected_with_ui_guidance(tail):
    with pytest.raises(ValueError, match="PR agents"):
        reviews.command_requests("@litereview " + tail)


def test_saved_times_apply_independently_to_direct_agents_and_group_instances(owner, monkeypatch):
    _, _, agent, _, group = setup(owner)
    owner.put(
        "/api/resources/agents/" + agent["id"], json={**agent, "minimum_minutes": 10}
    ).raise_for_status()
    second = create(owner, "agents", **{**agent, "name": "frontend", "minimum_minutes": 5})
    owner.put(
        "/api/resources/groups/" + group["id"],
        json={**group, "members": group["members"] + [{"agent_id": second["id"], "count": 1}]},
    ).raise_for_status()
    mock_github(monkeypatch)
    response = webhook(owner, payload(body="@litereview reviewer general reviewer"))
    with Session() as db:
        jobs = db.scalars(select(Job)).all()
        assert sorted(j.config["minimum_review_seconds"] for j in jobs) == [300, 600, 600, 600]
        assert all(j.config["skills"] and "mcp_definitions" in j.config for j in jobs)
        assert all(j.config["timeout_seconds"] is None and j.config["max_rounds"] is None for j in jobs)
        for job in jobs:
            job.status = "completed"
        assert "Requested minimum: 10 minutes." in reviews.render_comment(
            db.get(Run, response.json()["run_id"]), jobs
        )
    owner.put("/api/resources/agents/" + agent["id"], json={**agent, "minimum_minutes": 3}).raise_for_status()
    assert (
        next(a for a in owner.get("/api/resources/agents").json() if a["id"] == agent["id"])[
            "minimum_minutes"
        ]
        == 3
    )
    with Session() as db:
        assert sorted(j.config["minimum_review_seconds"] for j in db.scalars(select(Job)).all()) == [
            300,
            600,
            600,
            600,
        ]
    fresh = webhook(owner, payload(body="@litereview reviewer", comment_id=12346), "new-time")
    with Session() as db:
        assert (
            db.scalar(select(Job).where(Job.run_id == fresh.json()["run_id"])).config[
                "minimum_review_seconds"
            ]
            == 180
        )
    bad = webhook(owner, payload(body="@litereview general t=1", comment_id=12347), "invalid-time")
    with Session() as db:
        assert db.get(Run, bad.json()["run_id"]).status == "rejected"
        assert len(db.scalars(select(Job)).all()) == 5


@pytest.mark.parametrize("minutes", [-1, 1.5, "5", True])
def test_saved_time_rejects_invalid_values(owner, minutes):
    _, _, agent, _, _ = setup(owner)
    response = owner.put("/api/resources/agents/" + agent["id"], json={**agent, "minimum_minutes": minutes})
    assert response.status_code == 422


def test_time_default_disabled_and_no_workspace_ceiling(owner, monkeypatch):
    _, _, agent, _, _ = setup(owner)
    assert agent["minimum_minutes"] == 0
    monkeypatch.setenv("MAX_REVIEW_MINUTES", "2")
    assert "max_review_minutes" not in owner.get("/api/setup").json()
    assert (
        owner.put("/api/resources/agents/" + agent["id"], json={**agent, "minimum_minutes": 1500}).status_code
        == 200
    )
    mock_github(monkeypatch)
    # Existing agents created before this field was introduced remain untimed.
    with Session() as db:
        row = db.get(Resource, agent["id"])
        row.data = {k: v for k, v in row.data.items() if k != "minimum_minutes"}
        db.commit()
    webhook(owner, payload())
    with Session() as db:
        assert all(not j.config.get("minimum_review_seconds") for j in db.scalars(select(Job)).all())


async def test_finish_tool_keeps_agent_working_until_monotonic_minimum():
    cfg = {"capabilities": [], "mcp_definitions": [], "minimum_review_seconds": 60}
    tools = Toolset({"config": cfg}, AsyncMock())
    tools.clock.start()
    bridge = Bridge(tools, AsyncMock(), cfg)
    result = {"summary": "done", "findings": [], "limitations": []}
    rejected = await bridge.call("lr_finish_review", result)
    assert rejected["isError"] and "Continue useful investigation" in rejected["content"][0]["text"]
    assert not bridge.finished.is_set()
    assert (await tools.execute("review_clock", {}))["remaining_seconds"] == 60
    tools.clock.started -= 60
    assert not (await bridge.call("lr_finish_review", result))["isError"]
    assert bridge.finished.is_set()


def test_server_rejects_early_success_even_if_worker_bypasses_tool(client):
    seed_job()
    with Session() as db:
        job = db.get(Job, "test-job")
        job.config = {**job.config, "minimum_review_seconds": 60, "timeout_seconds": 1200, "max_rounds": 120}
        db.commit()
    root = "/api/internal/jobs/test-job/"
    headers = {"authorization": "Bearer job-token"}
    result = {"status": "completed", "summary": "done", "findings": [], "limitations": []}
    assert client.post(root + "finish", headers=headers, json=result).status_code == 409
    first = client.post(root + "start-review", headers=headers, json={}).json()
    assert client.post(root + "start-review", headers=headers, json={}).json() == first
    assert client.post(root + "finish", headers=headers, json=result).status_code == 409
    with Session() as db:
        db.get(ReviewTiming, "test-job").started_at -= 60
        db.commit()
    assert client.post(root + "finish", headers=headers, json=result).status_code == 200


def test_failures_do_not_wait_out_minimum_and_job_limits_are_independent(client):
    seed_job()
    with Session() as db:
        job = db.get(Job, "test-job")
        job.config = {**job.config, "minimum_review_seconds": 3600, "timeout_seconds": 4200}
        job.started_at = time.time() - 1000
        db.commit()
    headers = {"authorization": "Bearer job-token"}
    assert (
        client.post("/api/internal/jobs/test-job/start-review", headers=headers, json={}).status_code == 200
    )
    result = {"status": "failed", "summary": "Provider failed", "findings": [], "limitations": ["No credits"]}
    assert client.post("/api/internal/jobs/test-job/finish", headers=headers, json=result).status_code == 200


async def test_codex_early_exit_resumes_same_thread_and_keeps_tools(tmp_path, monkeypatch):
    cfg = {
        "harness": "codex",
        "model": "openai/gpt-6-sol",
        "capabilities": [],
        "mcp_definitions": [],
        "minimum_review_seconds": 60,
        "skills": [],
        "instructions": "Review carefully",
    }
    relay = AsyncMock()
    tools = Toolset({"config": cfg}, relay)
    tools.clock = ReviewClock(60)
    tools.clock.start()
    monkeypatch.setattr(harness, "STATE", tmp_path)
    monkeypatch.setattr(harness, "ROOT", tmp_path)
    monkeypatch.setattr(tools, "child_env", lambda: dict(os.environ))
    attempts = []
    build_command = harness.command

    # The full PR exceeds both exec's argument limit and Codex's 1 MiB input
    # limit. It stays readable on disk while the CLI receives a short prompt.
    executable = tmp_path / "harness/node_modules/.bin/codex"
    executable.parent.mkdir(parents=True)
    executable.write_text(f"#!{sys.executable}\n" + r'''import json,sys,urllib.request
from pathlib import Path
data=json.loads(Path("test-input.json").read_text())
prompt=sys.stdin.buffer.read().decode("utf-8")
assert prompt == data["prompt"]
assert json.loads(Path("review-context.json").read_text())["diff"].endswith("世界\n")
assert Path("agent-instructions.md").read_text() == data["instructions"]
assert sys.argv[-1] == "-"
resuming="resume" in sys.argv
if resuming:
    assert sys.argv[-3:] == ["resume", "same-thread", "-"]
Path("received-resume.txt" if resuming else "received-start.txt").write_text(prompt)
print(json.dumps({"type":"thread.started","thread_id":"same-thread"}),flush=True)
body={"name":"lr_finish_review","arguments":{"summary":"Verified","findings":[],"limitations":[]}}
r=urllib.request.urlopen(urllib.request.Request(data["url"]+"/call",data=json.dumps(body).encode(),headers={"content-type":"application/json"}))
print(r.read().decode(),flush=True)
''')
    executable.chmod(0o755)

    def command(cfg, url, instructions, prompt, env, resume_id=None):
        attempts.append(resume_id)
        if resume_id:
            tools.clock.started -= 60
        (tmp_path / "test-input.json").write_text(json.dumps({
            "url": url, "prompt": prompt, "instructions": instructions,
        }))
        return build_command(cfg, url, instructions, prompt, env, resume_id)

    monkeypatch.setattr(harness, "command", command)
    prompt = json.dumps({"diff": "+ Full diff: café 世界\n" * 100_000}, ensure_ascii=False)
    instructions = "Review carefully. Préserver les instructions.\n" * 10_000
    await asyncio.wait_for(harness.run_external(tools, relay, {"config": cfg}, instructions, prompt), 15)
    assert attempts == [None, "same-thread"]
    assert (tmp_path / "review-context.json").read_text() == prompt
    assert str(tmp_path / "review-context.json") in (tmp_path / "received-start.txt").read_text()
    assert len((tmp_path / "received-start.txt").read_bytes()) < 1024
    assert (tmp_path / "received-resume.txt").read_text().startswith("Continue this review in the same thread.")
    assert relay.call_args.args == (
        "finish",
        {"summary": "Verified", "findings": [], "limitations": [], "status": "completed"},
    )
