import asyncio
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy import select
from test_harnesses import seed_job
from test_reviews import mock_github, payload, setup, webhook

from app import providers, review_sessions, reviews, runtime
from app.db import Job, ReviewSession, Session
from app.limits import SANDBOX_MAX_SECONDS, job_timeout, model_rounds
from app.settings import Settings, settings
from runner import agent, harness


def test_legacy_environment_never_imposes_limits(monkeypatch):
    monkeypatch.setenv("JOB_TIMEOUT", "600")
    monkeypatch.setenv("MAX_ROUNDS", "60")
    Settings(_env_file=None)  # Old deployments can retain these unused values.
    assert job_timeout({}) is None
    assert model_rounds({}) is None


def test_agent_limits_snapshot_and_clear_independently(owner, monkeypatch):
    _, _, reviewer, _, _ = setup(owner)
    assert reviewer["maximum_minutes"] is None
    assert reviewer["max_model_calls"] is None
    reviewer.update(minimum_minutes=5, maximum_minutes=30, max_model_calls=250)
    path = "/api/resources/agents/" + reviewer["id"]
    assert owner.put(path, json=reviewer).status_code == 200
    mock_github(monkeypatch)
    first = webhook(owner, payload(body="@litereview reviewer general")).json()["run_id"]
    with Session() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == first)).all()
        assert len(jobs) == 3
        assert all(j.config["timeout_seconds"] == 1800 and j.config["max_rounds"] == 250 for j in jobs)
    reviewer.update(maximum_minutes=None, max_model_calls=None)
    assert owner.put(path, json=reviewer).status_code == 200
    second = webhook(
        owner, payload(body="@litereview reviewer general", comment_id=12346), "no-limits"
    ).json()["run_id"]
    with Session() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == second)).all()
        assert all(j.config["minimum_review_seconds"] == 300 for j in jobs)
        assert all(job_timeout(j.config) is None and model_rounds(j.config) is None for j in jobs)
        assert all(
            job_timeout(j.config) == 1800 and model_rounds(j.config) == 250
            for j in db.scalars(select(Job).where(Job.run_id == first))
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"maximum_minutes": 0},
        {"maximum_minutes": -1},
        {"maximum_minutes": 1.5},
        {"maximum_minutes": "10"},
        {"maximum_minutes": True},
        {"max_model_calls": 0},
        {"max_model_calls": -1},
        {"max_model_calls": 1.5},
        {"max_model_calls": "10"},
        {"max_model_calls": True},
        {"minimum_minutes": 10, "maximum_minutes": 10},
    ],
)
def test_invalid_limits_cannot_be_saved(owner, changes):
    _, _, reviewer, _, _ = setup(owner)
    response = owner.put("/api/resources/agents/" + reviewer["id"], json={**reviewer, **changes})
    assert response.status_code == 422


@pytest.mark.parametrize("changes", [{"maximum_minutes": 1441}, {"max_model_calls": 10000}])
def test_each_limit_is_optional_independently(owner, changes):
    _, _, reviewer, _, _ = setup(owner)
    response = owner.put("/api/resources/agents/" + reviewer["id"], json={**reviewer, **changes})
    assert response.status_code == 200
    for key, value in changes.items():
        assert response.json()[key] == value


def test_old_active_job_can_keep_working_beyond_former_limits(client, monkeypatch):
    seed_job()
    with Session() as db:
        job = db.get(Job, "test-job")
        job.started_at = time.time() - 10 * 3600
        job.model_calls = 1000
        db.commit()
    monkeypatch.setattr(providers, "snapshot_connection", lambda cfg: cfg)
    complete = AsyncMock(return_value={"role": "assistant", "content": "Continue reviewing"})
    monkeypatch.setattr(providers, "complete", complete)
    path = "/api/internal/jobs/test-job/"
    headers = {"authorization": "Bearer job-token"}
    # A growing built-in conversation must not introduce another hidden round cap.
    body = {"messages": [{"role": "user", "content": "Check"}] * 450}
    assert client.post(path + "model", headers=headers, json=body).status_code == 200
    assert client.post(path + "start-review", headers=headers, json={}).status_code == 200
    with Session() as db:
        assert db.get(Job, "test-job").model_calls == 1001
        db.get(Job, "test-job").status = "completed"
        db.commit()
    assert client.post(path + "model", headers=headers, json=body).status_code == 401
    complete.assert_awaited_once()


@pytest.mark.parametrize("timeout,expected", [(None, SANDBOX_MAX_SECONDS), (1200, 1260), (86400, 86400)])
def test_sandbox_uses_selected_timeout_or_full_platform_lifetime(monkeypatch, timeout, expected):
    monkeypatch.setattr(runtime, "client", lambda: "client")
    monkeypatch.setattr(runtime, "_app", lambda *_: "app")
    monkeypatch.setattr(runtime, "sandbox_image", lambda _: "image")
    create = Mock()
    monkeypatch.setattr(runtime.modal.Sandbox, "create", create)
    runtime.create_sandbox("job", "token", timeout=timeout)
    assert create.call_args.kwargs["timeout"] == expected


async def test_runner_uses_codex_and_preserves_full_review_context(monkeypatch):
    relay = AsyncMock()
    monkeypatch.setattr(agent, "prepare_repository", AsyncMock())
    monkeypatch.setattr(agent, "stage_skills", lambda *_: None)
    tools = SimpleNamespace(init=AsyncMock(), close=AsyncMock(), clock=Mock())
    tools.clock.instructions.return_value = ""
    tools.clock.status.return_value = {}
    monkeypatch.setattr(agent, "Toolset", lambda *_: tools)
    run_codex = AsyncMock()
    monkeypatch.setattr(harness, "run_external", run_codex)
    data = {
        "config": {"skills": [], "capabilities": [], "instructions": "Review in French"},
        "pr": {"diff": "large diff\n" * 200_000},
        "repository": {},
    }
    await agent.run(relay, data)
    run_codex.assert_awaited_once()
    args = run_codex.call_args.args
    assert args[:3] == (tools, relay, data)
    assert "Review in French" in args[3]
    assert json.loads(args[4])["review"] == data["pr"]
    tools.close.assert_awaited_once()



@pytest.mark.parametrize("timeout", [None, 0.01])
async def test_worker_only_times_out_when_selected(monkeypatch, timeout):
    relay = AsyncMock(return_value={"timeout": timeout})
    monkeypatch.setattr(agent, "Relay", lambda: relay)
    finished = False

    async def run(*_):
        nonlocal finished
        await asyncio.sleep(0.02)
        finished = True

    monkeypatch.setattr(agent, "run", run)
    await agent.main()
    assert finished == (timeout is None)
    if timeout is not None:
        assert relay.call_args.args[1]["status"] == "timed_out"


async def test_dispatcher_watches_old_unlimited_job_after_restart(client, monkeypatch):
    seed_job()
    with Session() as db:
        job = db.get(Job, "test-job")
        job.started_at = time.time() - 12 * 3600
        job.sandbox_id = "existing-sandbox"
        db.commit()
    observed = asyncio.Event()

    async def watch(job_id, sandbox_id):
        assert (job_id, sandbox_id) == ("test-job", "existing-sandbox")
        with Session() as db:
            assert db.get(Job, job_id).status == "running"
        observed.set()

    monkeypatch.setattr(runtime, "watch_existing", watch)
    terminate = AsyncMock()
    monkeypatch.setattr(runtime, "terminate", terminate)
    monkeypatch.setattr(reviews, "publish", AsyncMock())
    task = asyncio.create_task(reviews.dispatcher())
    try:
        await asyncio.wait_for(observed.wait(), 2)
        terminate.assert_not_awaited()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_reattached_sandbox_exit_marks_lost_callback_incomplete(client, monkeypatch):
    seed_job()
    sandbox = SimpleNamespace(wait=SimpleNamespace(aio=AsyncMock(return_value=0)))
    monkeypatch.setattr(runtime, "client", lambda: "client")
    monkeypatch.setattr(runtime.modal.Sandbox, "from_id", lambda *a, **kw: sandbox)
    terminate = AsyncMock()
    monkeypatch.setattr(runtime, "terminate", terminate)
    await runtime.watch_existing("test-job", "existing-sandbox")
    with Session() as db:
        assert db.get(Job, "test-job").status == "failed"
    terminate.assert_awaited_once_with("existing-sandbox")


async def test_api_shutdown_does_not_kill_running_review(client, monkeypatch):
    seed_job()
    with Session() as db:
        job = db.get(Job, "test-job")
        job.config = {**job.config, "harness": "codex", "capabilities": []}
        db.commit()
    waiting = asyncio.Event()

    async def wait():
        waiting.set()
        await asyncio.Event().wait()

    sandbox = SimpleNamespace(object_id="persisted-sandbox", wait=SimpleNamespace(aio=wait))
    monkeypatch.setattr(settings(), "public_url", "https://review.example")
    monkeypatch.setattr(runtime, "create_sandbox", lambda *_: sandbox)
    terminate = AsyncMock()
    monkeypatch.setattr(runtime, "terminate", terminate)
    task = asyncio.create_task(runtime.launch("test-job", "job-token"))
    await asyncio.wait_for(waiting.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    terminate.assert_not_awaited()
    with Session() as db:
        job = db.get(Job, "test-job")
        assert job.status == "running" and job.sandbox_id == "persisted-sandbox"


async def test_replacement_waits_for_confirmed_modal_stop_and_rejects_old_worker(owner, monkeypatch):
    from test_review_sessions import live_pr

    from app.security import digest

    setup(owner)
    live_pr(monkeypatch)
    first = await reviews.accept(payload(body="@litereview reviewer"), "first")
    with Session.begin() as db:
        old = db.scalar(select(Job).where(Job.run_id == first))
        old.status, old.sandbox_id, old.token_hash = "running", "old-sandbox", digest("old-token")
    live_pr(monkeypatch, "c" * 40)
    latest = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "replacement")
    stopping, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()

    def terminate(*, wait):
        assert wait is True
        loop.call_soon_threadsafe(stopping.set)
        assert release.wait(5)

    monkeypatch.setattr(runtime, "client", lambda: "client")
    monkeypatch.setattr(runtime.modal.Sandbox, "from_id", lambda *a, **kw: SimpleNamespace(terminate=terminate))
    task = asyncio.create_task(runtime.cancel_review(old.id))
    try:
        await asyncio.wait_for(stopping.wait(), 2)
        await review_sessions.prepare()
        with Session() as db:
            assert db.scalar(select(Job).where(Job.run_id == latest)).status == "blocked"
            assert db.get(Job, old.id).status == "cancelling"
        headers = {"Authorization": "Bearer old-token"}
        assert owner.get(f"/api/internal/jobs/{old.id}/bootstrap", headers=headers).status_code == 401
        assert owner.post(f"/api/internal/jobs/{old.id}/finish", headers=headers,
                          json={"status": "completed", "summary": "Stale", "findings": []}).status_code == 401
    finally:
        release.set()
        await task
    await review_sessions.prepare()
    with Session() as db:
        assert db.get(Job, old.id).status == "superseded"
        assert db.get(Job, old.id).result["findings"] == []
        assert db.scalar(select(Job).where(Job.run_id == latest)).status == "queued"
        assert db.scalar(select(ReviewSession)).baseline_run_id is None


@pytest.mark.parametrize("has_id", [False, True])
async def test_cancellation_recovers_after_restart_and_retries_modal_errors(client, monkeypatch, has_id):
    seed_job()
    with Session.begin() as db:
        job = db.get(Job, "test-job")
        job.status = "cancelling"
        job.sandbox_id = "old-sandbox" if has_id else ""
    stop = Mock(side_effect=RuntimeError("Modal unavailable"))
    lookup = Mock(return_value=SimpleNamespace(terminate=stop))
    monkeypatch.setattr(runtime, "client", lambda: "client")
    monkeypatch.setattr(runtime.modal.Sandbox, "from_id" if has_id else "from_name", lookup)
    with pytest.raises(RuntimeError, match="Modal unavailable"):
        await runtime.cancel_review("test-job")
    with Session() as db:
        assert db.get(Job, "test-job").status == "cancelling"
    stop.side_effect = None
    await runtime.cancel_review("test-job")
    stop.assert_called_with(wait=True)
    if not has_id:
        lookup.assert_called_with(settings().modal_app_name, "lr-test-job", client="client")
    with Session() as db:
        assert db.get(Job, "test-job").status == "superseded"


async def test_cancel_during_sandbox_creation_keeps_job_until_sandbox_is_stopped(client, monkeypatch):
    seed_job()
    with Session.begin() as db:
        job = db.get(Job, "test-job")
        job.config = {**job.config, "harness": "codex", "capabilities": []}
    creating, watching, stopped = asyncio.Event(), asyncio.Event(), asyncio.Event()
    release = threading.Event()
    loop = asyncio.get_running_loop()

    async def wait():
        watching.set()
        await stopped.wait()

    def terminate(*, wait):
        assert wait is True
        loop.call_soon_threadsafe(stopped.set)

    sandbox = SimpleNamespace(object_id="created-sandbox", terminate=terminate, wait=SimpleNamespace(aio=wait))

    def create(*args):
        loop.call_soon_threadsafe(creating.set)
        assert release.wait(5)
        return sandbox

    monkeypatch.setattr(settings(), "public_url", "https://review.example")
    monkeypatch.setattr(runtime, "client", lambda: "client")
    monkeypatch.setattr(runtime, "create_sandbox", create)
    monkeypatch.setattr(runtime.modal.Sandbox, "from_id", lambda *a, **kw: sandbox)
    monkeypatch.setattr(runtime, "terminate", AsyncMock())
    task = asyncio.create_task(runtime.launch("test-job", "job-token"))
    try:
        await asyncio.wait_for(creating.wait(), 2)
        with Session.begin() as db:
            db.get(Job, "test-job").status = "cancelling"
        release.set()
        await asyncio.wait_for(watching.wait(), 2)
        with Session() as db:
            job = db.get(Job, "test-job")
            assert job.status == "cancelling" and job.sandbox_id == "created-sandbox"
        await runtime.cancel_review("test-job")
        await task
    finally:
        release.set()
        if not task.done():
            task.cancel()
    with Session() as db:
        assert db.get(Job, "test-job").status == "superseded"


async def test_cancel_before_launch_does_not_create_a_sandbox(client, monkeypatch):
    seed_job()
    with Session.begin() as db:
        db.get(Job, "test-job").status = "cancelling"
    create = Mock()
    monkeypatch.setattr(runtime, "create_sandbox", create)
    await runtime.launch("test-job", "job-token")
    create.assert_not_called()
    with Session() as db:
        assert db.get(Job, "test-job").status == "superseded"
