import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import select
from test_automatic_reviews import deliver, event, github_pr
from test_review_publication import publication as publication
from test_reviews import create, mock_github, payload, setup, webhook

from app import github, review_sessions, reviews
from app.db import Job, ReviewFinding, ReviewSession, Run, Session, init_db


def complete(run_id, status="completed", limitations=None):
    with Session.begin() as db:
        for job in db.scalars(select(Job).where(Job.run_id == run_id)):
            job.status = status
            job.result = {"summary": "Checked", "findings": [], "limitations": limitations or []}


def live_pr(monkeypatch, head="a" * 40, *, ancestry="ahead"):
    mock_github(monkeypatch)
    calls = []

    async def request(repo, installation, method, suffix, **kwargs):
        calls.append((suffix, kwargs))
        if suffix.startswith("pulls/"):
            return httpx.Response(200, json={"head": {"sha": head}, "base": {"sha": "b" * 40},
                                            "state": "open", "title": "PR", "body": ""})
        if kwargs.get("headers", {}).get("Accept") == "application/vnd.github.diff":
            return httpx.Response(200, text="full diff" if suffix.startswith("compare/" + "b" * 40) else "incremental diff")
        start = suffix.removeprefix("compare/").split("...")[0]
        return httpx.Response(200, json={"status": ancestry, "merge_base_commit": {"sha": start}})

    monkeypatch.setattr(github, "repo_request", request)
    return calls


def test_distinct_simultaneous_comments_launch_one_batch(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: asyncio.run(reviews.accept(payload(comment_id=n), str(n))), [11, 12]))
    with Session() as db:
        assert len(db.scalars(select(Job)).all()) == 2  # Intentional group instances.
        assert len(db.scalars(select(ReviewSession)).all()) == 1
        notice = next(db.get(Run, rid) for rid in results if db.get(Run, rid).snapshot["request_only"])
        assert notice.snapshot["notices"][0]["run_id"] in results
        assert notice.status == "not_started"
    assert owner.get("/api/spend").json()["total"]["runs"] == 1
    init_db()
    assert webhook(owner, payload(comment_id=11), "redelivered").json()["run_id"] == results[0]


async def test_latest_pending_commit_replaces_old_pending_and_recovers_after_restart(owner, monkeypatch):
    setup(owner)
    live_pr(monkeypatch)
    first = await reviews.accept(payload(), "first")
    live_pr(monkeypatch, "c" * 40)
    pending = await reviews.accept(payload(comment_id=2), "second")
    live_pr(monkeypatch, "d" * 40)
    latest = await reviews.accept(payload(comment_id=3), "third")
    repeated = await reviews.accept(payload(comment_id=4), "fourth")
    with Session() as db:
        assert all(j.status == "queued" for j in db.scalars(select(Job).where(Job.run_id == first)))
        assert all(j.status == "superseded" for j in db.scalars(select(Job).where(Job.run_id == pending)))
        assert all(j.status == "blocked" for j in db.scalars(select(Job).where(Job.run_id == latest)))
        assert db.get(Run, repeated).snapshot["notices"][0]["run_id"] == latest
    complete(first, "failed")
    init_db()
    await review_sessions.prepare()
    with Session() as db:
        session = db.scalar(select(ReviewSession))
        assert session.active_run_id == latest and session.pending_run_id is None
        assert session.baseline_run_id is None
        assert all(j.status == "queued" and j.config["session_prepared"] for j in db.scalars(select(Job).where(Job.run_id == latest)))


async def test_slow_older_request_cannot_queue_behind_newer_head(owner, monkeypatch):
    setup(owner)
    live_pr(monkeypatch)
    original_request = github.repo_request
    waiting, release = asyncio.Event(), asyncio.Event()

    async def slow_request(*args, **kwargs):
        if args[3].startswith("compare/"):
            waiting.set()
            await release.wait()
        return await original_request(*args, **kwargs)

    monkeypatch.setattr(github, "repo_request", slow_request)
    earlier = asyncio.create_task(reviews.accept(payload(), "slow"))
    await asyncio.wait_for(waiting.wait(), 2)
    live_pr(monkeypatch, "c" * 40)
    latest = await reviews.accept(payload(comment_id=2), "latest")
    release.set()
    old = await earlier
    with Session() as db:
        assert db.get(Run, old).snapshot["notices"][0]["run_id"] == latest
        assert {j.run_id for j in db.scalars(select(Job))} == {latest}


def test_different_agents_continue_and_partial_request_reports_existing_review(owner, monkeypatch):
    _, _, agent, _, _ = setup(owner)
    create(owner, "agents", **{**agent, "name": "other"})
    mock_github(monkeypatch)
    first = webhook(owner, payload(body="@litereview reviewer")).json()["run_id"]
    second = webhook(owner, payload(body="@litereview reviewer other", comment_id=2)).json()["run_id"]
    with Session() as db:
        assert db.get(Run, second).snapshot["notices"][0]["run_id"] == first
        assert [j.config["name"] for j in db.scalars(select(Job).where(Job.run_id == second))] == ["other"]


@pytest.mark.parametrize("automatic_first", [True, False])
def test_manual_and_automatic_triggers_share_active_session(owner, monkeypatch, automatic_first):
    _, _, agent, repo, _ = setup(owner)
    owner.put("/api/resources/repos/" + repo["id"], json={**repo, "review_rules": [{"target_id": agent["id"]}]}).raise_for_status()
    data = event()
    github_pr(monkeypatch, data)
    # Automatic requests use repository rules, manual requests still check callers.
    from unittest.mock import AsyncMock
    monkeypatch.setattr(github, "permission", AsyncMock(return_value="write"))
    actions = [lambda: deliver(owner, data), lambda: webhook(owner, payload(body="@litereview reviewer"))]
    if not automatic_first:
        actions.reverse()
    for action in actions:
        assert action().status_code == 200
    with Session() as db:
        assert len(db.scalars(select(Job)).all()) == 1


async def test_incremental_baseline_and_private_comparison(owner, monkeypatch):
    from test_review_publication import GitHubFixture
    from test_spend import charge

    from app import main
    _, _, agent, _, _ = setup(owner)
    owner.put("/api/resources/agents/" + agent["id"], json={**agent, "review_mode": "compare"}).raise_for_status()
    live_pr(monkeypatch)
    first = await reviews.accept(payload(body="@litereview reviewer"), "first")
    await review_sessions.prepare()
    with Session() as db:
        assert db.scalar(select(Job)).config["review_scope"] == "full"
        assert len(db.scalars(select(Job)).all()) == 1
    complete(first)
    calls = live_pr(monkeypatch, "c" * 40)
    second = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "second")
    await review_sessions.prepare()
    await review_sessions.prepare()  # Restart/retry must not create another shadow.
    with Session() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == second)).all()
        assert len(jobs) == 2
        primary = next(j for j in jobs if not j.config.get("comparison_parent_id"))
        shadow = next(j for j in jobs if j.config.get("comparison_parent_id"))
        assert primary.config["review_diff"] == "incremental diff"
        assert primary.config["review_from"] == "a" * 40
        assert shadow.config["review_diff"] == "full diff"
        assert reviews.ordered_jobs(db.get(Run, second), jobs) == [primary]
        assert db.scalar(select(ReviewSession)).baseline_run_id == first
    detail = owner.get("/api/runs/" + second).json()
    assert len(detail["jobs"]) == 1 and len(detail["jobs"][0]["comparisons"]) == 1
    assert detail["jobs"][0]["session_history"][0]["id"] == first
    assert any(s == f"compare/{'a' * 40}...{'c' * 40}" for s, _ in calls)
    complete(second)
    with Session.begin() as db:
        for job_id, summary in [(primary.id, "Public incremental result"), (shadow.id, "Private full result")]:
            job = db.get(Job, job_id)
            job.result = {**job.result, "summary": summary}
    fake = GitHubFixture(second)
    fake.head = "c" * 40
    monkeypatch.setattr(github, "repo_request", fake.request)
    await reviews.publish(second)
    assert len(fake.comments) == 1
    assert "Public incremental result" in fake.comments[0]["body"]
    assert "Private full result" not in fake.comments[0]["body"]
    charge(primary, ".2")
    charge(shadow, ".3")
    detail = owner.get("/api/runs/" + second).json()
    assert detail["spend"]["cost_usd"] == .5
    assert detail["jobs"][0]["primary_spend"]["cost_usd"] == .2
    assert detail["jobs"][0]["comparisons"][0]["spend"]["cost_usd"] == .3
    assert owner.get("/api/spend").json()["agents"][0]["cost_usd"] == .5
    monkeypatch.setattr(main, "is_admin", lambda _: False)
    member_detail = owner.get("/api/runs/" + second).json()
    assert "spend" not in member_detail
    assert "primary_spend" not in member_detail["jobs"][0]
    assert "spend" not in member_detail["jobs"][0]["comparisons"][0]


async def test_fusion_comparison_preserves_branch_minimum_and_stays_private(owner, monkeypatch):
    connection, _, agent, _, _ = setup(owner)
    owner.put("/api/resources/agents/" + agent["id"], json={**agent, "review_mode": "compare", "minimum_minutes": 5,
        "fusion": {"branches": [{"connection_id": connection["id"], "model": "branch-model"}]}}).raise_for_status()
    live_pr(monkeypatch)
    first = await reviews.accept(payload(body="@litereview reviewer"), "one")
    complete(first)
    live_pr(monkeypatch, "c" * 40)
    second = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "two")
    await review_sessions.prepare()
    with Session() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == second)).all()
        assert len(jobs) == 4 and len(reviews.ordered_jobs(db.get(Run, second), jobs)) == 1
        shadow_branch = next(j for j in jobs if j.config.get("comparison_parent_id") and j.config.get("fusion_parent_id"))
        assert shadow_branch.config["minimum_review_seconds"] == 300
        assert shadow_branch.config["review_diff"] == "full diff"


@pytest.mark.parametrize("reason", ["failed", "incomplete", "rewritten", "configuration"])
async def test_unsafe_incremental_baselines_fall_back_to_full(owner, monkeypatch, reason):
    _, _, agent, _, _ = setup(owner)
    owner.put("/api/resources/agents/" + agent["id"], json={**agent, "review_mode": "incremental"}).raise_for_status()
    live_pr(monkeypatch)
    first = await reviews.accept(payload(body="@litereview reviewer"), "first")
    complete(first, "failed" if reason == "failed" else "completed", ["blocked"] if reason == "incomplete" else [])
    if reason == "configuration":
        owner.put("/api/resources/agents/" + agent["id"], json={**agent, "review_mode": "incremental", "instructions": "Different scope"}).raise_for_status()
    live_pr(monkeypatch, "c" * 40, ancestry="diverged" if reason == "rewritten" else "ahead")
    second = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "second")
    await review_sessions.prepare()
    with Session() as db:
        job = db.scalar(select(Job).where(Job.run_id == second))
        assert job.config["review_scope"] == "full"
        assert job.config["review_diff"] == "full diff"


async def test_completed_findings_and_decisions_reach_next_review(owner, monkeypatch):
    setup(owner)
    live_pr(monkeypatch)
    first = await reviews.accept(payload(body="@litereview reviewer"), "first")
    complete(first)
    with Session.begin() as db:
        job = db.scalar(select(Job).where(Job.run_id == first))
        job.result = {**job.result, "findings": [{"title": "Old issue", "explanation": "Evidence", "severity": "low"}]}
    await review_sessions.prepare()
    with Session.begin() as db:
        finding = db.scalar(select(ReviewFinding))
        finding.status, finding.decision = "withdrawn", {"reason": "Guard already prevents it"}
    live_pr(monkeypatch, "c" * 40)
    second = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "second")
    await review_sessions.prepare()
    with Session() as db:
        job = db.scalar(select(Job).where(Job.run_id == second))
        assert job.config["finding_history"][0]["status"] == "withdrawn"
        assert job.config["finding_history"][0]["decision"]["reason"] == "Guard already prevents it"


async def test_fusion_waiting_parent_prevents_duplicate_batch(owner, monkeypatch):
    connection, _, agent, _, _ = setup(owner)
    owner.put("/api/resources/agents/" + agent["id"], json={**agent,
        "fusion": {"branches": [{"connection_id": connection["id"], "model": "branch-model"}]}}).raise_for_status()
    live_pr(monkeypatch)
    first = await reviews.accept(payload(body="@litereview reviewer"), "one")
    await review_sessions.prepare()
    repeated = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "two")
    with Session() as db:
        assert sorted(j.status for j in db.scalars(select(Job))) == ["queued", "waiting"]
        assert db.get(Run, repeated).snapshot["notices"][0]["run_id"] == first


async def test_acknowledgment_does_not_wait_for_sandbox(publication):
    from app.publication import acknowledge
    fake = publication
    await acknowledge(fake.run_id)
    assert len(fake.reactions) == 1
    assert fake.comments == []
    await acknowledge(fake.run_id)
    assert len(fake.reactions) == 1
