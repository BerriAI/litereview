import os
import sys
import time
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from test_harnesses import seed_job
from test_reviews import mock_github, payload, setup, webhook

from app import reviews
from app.db import Job, ReviewTiming, Run, Session
from runner import harness
from runner.bridge import Bridge
from runner.timing import ReviewClock
from runner.tools import Toolset


@pytest.mark.parametrize(
    "body", ["@litereview security t=10 frontend t=5", "light review security t=10m frontend t=5"]
)
def test_independent_durations(body):
    assert reviews.command_requests(body) == [
        {"name": "security", "minutes": 10},
        {"name": "frontend", "minutes": 5},
    ]
    assert reviews.command("@litereview security security frontend") == ["security", "frontend"]


@pytest.mark.parametrize(
    "tail",
    [
        "t=10 security",
        "security t=",
        "security t=-1",
        "security t=0",
        "security t=121",
        "security t=NaN",
        "security t=1.5",
        "security t=5 t=6",
        "security t=5 security t=6",
    ],
)
def test_invalid_durations_reject_entire_command(tail):
    with pytest.raises(ValueError):
        reviews.command_requests("@litereview " + tail)


def test_timed_group_snapshots_preserve_tools_skills_and_dedup(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    response = webhook(owner, payload(body="@litereview reviewer t=10 general t=5 reviewer t=10"))
    with Session() as db:
        jobs = db.scalars(select(Job)).all()
        assert sorted(j.config["minimum_review_seconds"] for j in jobs) == [300, 300, 600]
        assert len({j.config["agent_id"] for j in jobs}) == 1
        assert all(j.config["skills"] and "mcp_definitions" in j.config for j in jobs)
        assert all(j.config["timeout_seconds"] > j.config["minimum_review_seconds"] for j in jobs)
        assert "Requested minimum: 10 minutes." in reviews.render_comment(
            db.get(Run, response.json()["run_id"]), jobs
        )
    bad = webhook(owner, payload(body="@litereview general t=0"), "invalid-time")
    with Session() as db:
        assert db.get(Run, bad.json()["run_id"]).status == "rejected"
        assert len(db.scalars(select(Job)).all()) == 3


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
    monkeypatch.setattr(tools, "child_env", lambda: dict(os.environ))
    attempts = []

    def command(cfg, url, instructions, prompt, env, resume_id=None):
        attempts.append(resume_id)
        if resume_id:
            tools.clock.started -= 60
        script = """import json,sys,urllib.request
print(json.dumps({"type":"thread.started","thread_id":"same-thread"}),flush=True)
body={"name":"lr_finish_review","arguments":{"summary":"Verified","findings":[],"limitations":[]}}
r=urllib.request.urlopen(urllib.request.Request(sys.argv[1]+"/call",data=json.dumps(body).encode(),headers={"content-type":"application/json"}))
print(r.read().decode(),flush=True)
"""
        return [sys.executable, "-c", script, url]

    monkeypatch.setattr(harness, "command", command)
    await harness.run_external(tools, relay, {"config": cfg}, "system", "review")
    assert attempts == [None, "same-thread"]
    assert relay.call_args.args == (
        "finish",
        {"summary": "Verified", "findings": [], "limitations": [], "status": "completed"},
    )
