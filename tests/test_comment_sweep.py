import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from test_reviews import create, mock_github, payload, setup, webhook

from app import comment_sweep, github, reviews
from app.db import CommentSweepCursor, Job, Resource, Run, Session
from app.settings import settings


def make_comment(
    comment_id,
    since,
    body="@litereview general",
    user_type="User",
    issue_number=1,
    updated_at=None,
):
    created_at = datetime.fromtimestamp(since, timezone.utc)
    return {
        "id": comment_id,
        "created_at": created_at.isoformat().replace("+00:00", "Z"),
        "updated_at": (updated_at or created_at).isoformat().replace("+00:00", "Z"),
        "issue_url": f"https://api.github.com/repos/owner/repo/issues/{issue_number}",
        "user": {"login": "trusted", "type": user_type},
        "body": body,
    }


def stub_sweep_github(
    monkeypatch,
    comments_by_repo,
    issues=None,
    fail_repos=(),
    pages_by_repo=None,
    fail_issue_fetches=(),
):
    calls = []
    monkeypatch.setattr(github, "permission", AsyncMock(return_value="write"))
    issues = issues or {}
    pages_by_repo = pages_by_repo or {}

    async def request(repo, installation, method, suffix, **kwargs):
        calls.append((repo, suffix, kwargs.get("params")))
        if suffix == "issues/comments":
            if repo in fail_repos:
                raise github.GitHubError(httpx.Response(403), method, suffix)
            page = kwargs.get("params", {}).get("page", 1)
            comments = pages_by_repo.get((repo, page), comments_by_repo.get(repo, []))
            return httpx.Response(200, json=comments)
        if suffix.startswith("issues/"):
            number = int(suffix.rsplit("/", 1)[-1])
            if (repo, number) in fail_issue_fetches:
                raise RuntimeError("issue fetch failed")
            return httpx.Response(
                200,
                json=issues.get((repo, number), {"number": number, "pull_request": {}}),
            )
        if suffix.startswith("compare/"):
            return httpx.Response(200, text="diff --git a/app.py b/app.py\n+new code")
        return httpx.Response(
            200,
            json={
                "head": {"sha": "a" * 40},
                "base": {"sha": "b" * 40},
                "title": "Test PR",
                "body": "",
                "state": "open",
                "changed_files": 1,
            },
        )

    monkeypatch.setattr(github, "repo_request", request)
    return calls


async def test_sweep_accepts_command_without_webhook(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    calls = stub_sweep_github(monkeypatch, {"owner/repo": [make_comment(2001, since + 1)]})

    assert await comment_sweep.sweep_once(since) == 1

    with Session() as db:
        runs = db.scalars(select(Run)).all()
        jobs = db.scalars(select(Job)).all()
        assert len(runs) == 1
        assert runs[0].delivery == "comment:2001"
        assert runs[0].status == "queued"
        assert len(jobs) == 2
        assert all(job.status == "queued" for job in jobs)
    assert calls[0][1] == "issues/comments"
    assert calls[0][2] == {
        "since": datetime.fromtimestamp(since, timezone.utc).isoformat(),
        "sort": "created",
        "direction": "desc",
        "per_page": 100,
        "page": 1,
    }


async def test_sweep_stops_at_first_comment_before_since(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    comments = [
        make_comment(2010, since + 100),
        make_comment(2011, since + 99),
        *[
            make_comment(2011 + offset, since + 99 - offset, body="not a command")
            for offset in range(1, 98)
        ],
        make_comment(2109, since - 1),
    ]
    calls = stub_sweep_github(
        monkeypatch,
        {},
        pages_by_repo={
            ("owner/repo", 1): comments,
            ("owner/repo", 2): [make_comment(2110, since - 2)],
        },
    )

    run_count = await comment_sweep.sweep_once(since)

    assert [
        params["page"] for _, suffix, params in calls if suffix == "issues/comments"
    ] == [1]
    assert [suffix for _, suffix, _ in calls if suffix == "issues/1"] == ["issues/1", "issues/1"]
    assert run_count == 2
    with Session() as db:
        runs = db.scalars(select(Run)).all()
        assert {run.delivery for run in runs} == {"comment:2010", "comment:2011"}


async def test_sweep_skips_edited_comments_without_fetching_issue(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    comment = make_comment(
        2013,
        since + 1,
        updated_at=datetime.fromtimestamp(since + 2, timezone.utc),
    )
    calls = stub_sweep_github(monkeypatch, {"owner/repo": [comment]})

    assert await comment_sweep.sweep_once(since) == 0
    assert [suffix for _, suffix, _ in calls] == ["issues/comments"]
    with Session() as db:
        assert db.scalars(select(Run)).all() == []
        assert db.scalars(select(Job)).all() == []


async def test_sweep_continues_after_comment_issue_fetch_error(owner, monkeypatch, caplog):
    setup(owner)
    since = 1_700_000_000
    comments = [
        make_comment(2014, since + 1, issue_number=1),
        make_comment(2015, since + 2, issue_number=2),
    ]
    calls = stub_sweep_github(
        monkeypatch,
        {"owner/repo": comments},
        fail_issue_fetches={("owner/repo", 1)},
    )

    assert await comment_sweep.sweep_once(since) == 1
    assert any(
        record.message == "Comment sweep failed for repository owner/repo and comment 2014"
        for record in caplog.records
    )
    assert [suffix for _, suffix, _ in calls if suffix.startswith("issues/")] == [
        "issues/comments",
        "issues/1",
        "issues/2",
    ]
    with Session() as db:
        runs = db.scalars(select(Run)).all()
        assert len(runs) == 1
        assert runs[0].delivery == "comment:2015"


async def test_webhook_first_is_deduplicated_by_sweep(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    result = webhook(owner, payload(comment_id=2002), "webhook-guid")
    assert result.status_code == 200
    run_id = result.json()["run_id"]

    calls = stub_sweep_github(monkeypatch, {"owner/repo": [make_comment(2002, 1_700_000_000)]})
    assert await comment_sweep.sweep_once(1_700_000_000) == 0

    with Session() as db:
        runs = db.scalars(select(Run)).all()
        assert len(runs) == 1
        assert runs[0].id == run_id
    assert [suffix for _, suffix, _ in calls] == ["issues/comments"]


async def test_sweep_first_is_deduplicated_by_webhook(owner, monkeypatch):
    setup(owner)
    stub_sweep_github(monkeypatch, {"owner/repo": [make_comment(2003, 1_700_000_000)]})
    assert await comment_sweep.sweep_once(1_700_000_000) == 1
    with Session() as db:
        run_id = db.scalar(select(Run.id))

    result = webhook(owner, payload(comment_id=2003), "webhook-guid")
    assert result.status_code == 200
    assert result.json()["run_id"] == run_id
    with Session() as db:
        assert len(db.scalars(select(Run)).all()) == 1


async def test_sweep_skips_bots_noncommands_and_old_comments(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    comments = [
        make_comment(2004, since + 1, user_type="Bot"),
        make_comment(2005, since + 1, body="Please review this"),
        make_comment(2006, since - 1),
    ]
    calls = stub_sweep_github(monkeypatch, {"owner/repo": comments})

    assert await comment_sweep.sweep_once(since) == 0
    assert [suffix for _, suffix, _ in calls] == ["issues/comments"]
    with Session() as db:
        assert db.scalars(select(Run)).all() == []
        assert db.scalars(select(Job)).all() == []


async def test_sweep_ignores_plain_issues(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    calls = stub_sweep_github(
        monkeypatch,
        {"owner/repo": [make_comment(2007, since)]},
        issues={("owner/repo", 1): {"number": 1}},
    )

    assert await comment_sweep.sweep_once(since) == 0
    assert [suffix for _, suffix, _ in calls] == ["issues/comments", "issues/1"]
    with Session() as db:
        assert db.scalars(select(Run)).all() == []
        assert db.scalars(select(Job)).all() == []


async def test_sweep_counts_rejected_commands(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    comments = [make_comment(2009, since, body="@litereview t=5")]
    stub_sweep_github(monkeypatch, {"owner/repo": comments})

    assert await comment_sweep.sweep_once(since) == 1
    with Session() as db:
        runs = db.scalars(select(Run)).all()
        assert len(runs) == 1
        assert runs[0].delivery == "comment:2009"
        assert runs[0].status == "rejected"
        assert db.scalars(select(Job)).all() == []


async def test_sweep_continues_after_repository_github_error(owner, monkeypatch):
    setup(owner)
    create(owner, "repos", name="owner/other", installation_id=43)
    since = 1_700_000_000
    calls = stub_sweep_github(
        monkeypatch,
        {"owner/other": [make_comment(2008, since)]},
        fail_repos={"owner/repo"},
    )

    assert await comment_sweep.sweep_once(since) == 1
    with Session() as db:
        runs = db.scalars(select(Run)).all()
        assert len(runs) == 1
        assert runs[0].repo == "owner/other"
        assert runs[0].delivery == "comment:2008"
    assert [repo for repo, suffix, _ in calls if suffix == "issues/comments"] == [
        "owner/repo",
        "owner/other",
    ]


async def test_first_sweep_recovers_comments_from_before_startup(owner, monkeypatch):
    setup(owner)
    now = 1_700_000_000
    monkeypatch.setattr(comment_sweep, "time", SimpleNamespace(time=lambda: now))
    stub_sweep_github(monkeypatch, {"owner/repo": [make_comment(3001, now - 60)]})

    assert await comment_sweep.sweep_once() == 1
    with Session() as db:
        assert db.scalar(select(CommentSweepCursor)).scanned_through == now
        assert db.scalar(select(Run)).delivery == "comment:3001"


@pytest.mark.parametrize("failure", ["repository", "comment", "first_request"])
async def test_checkpoint_recovers_after_outage_longer_than_lookback(owner, monkeypatch, failure):
    setup(owner)
    now = 1_700_000_000
    monkeypatch.setattr(comment_sweep, "time", SimpleNamespace(time=lambda: now))
    comments = {"owner/repo": []}
    failed_repos = set()
    failed_issues = set()
    calls = stub_sweep_github(
        monkeypatch, comments, fail_repos=failed_repos, fail_issue_fetches=failed_issues
    )
    if failure == "first_request":
        failed_repos.add("owner/repo")
    assert await comment_sweep.sweep_once() == 0
    with Session() as db:
        checkpoint = db.scalar(select(CommentSweepCursor)).scanned_through

    now += 60
    comments["owner/repo"] = [make_comment(3002, now)]
    if failure == "comment":
        failed_issues.add(("owner/repo", 1))
    else:
        failed_repos.add("owner/repo")
    assert await comment_sweep.sweep_once() == 0
    with Session() as db:
        assert db.scalar(select(CommentSweepCursor)).scanned_through == checkpoint

    # A new process has only the database checkpoint, not the old process time.
    now += 7200
    failed_repos.clear()
    failed_issues.clear()
    calls.clear()
    assert await comment_sweep.sweep_once() == 1
    since = datetime.fromisoformat(calls[0][2]["since"]).timestamp()
    assert since <= checkpoint
    with Session() as db:
        assert db.scalar(select(Run)).delivery == "comment:3002"
        assert db.scalar(select(CommentSweepCursor)).scanned_through == now
    assert await comment_sweep.sweep_once() == 0


@pytest.mark.parametrize("comment_id", [3003, 6_107_062_032])
async def test_upgrade_recognizes_old_runs_without_restarting_jobs(owner, monkeypatch, comment_id):
    setup(owner)
    mock_github(monkeypatch)
    run_id = webhook(owner, payload(comment_id=comment_id), "legacy-guid").json()["run_id"]
    with Session.begin() as db:
        run = db.get(Run, run_id)
        run.delivery = "legacy-guid"
        run.status = "completed"
    calls = stub_sweep_github(monkeypatch, {"owner/repo": [make_comment(comment_id, 1_700_000_000)]})

    assert await comment_sweep.sweep_once(1_700_000_000) == 0
    assert [suffix for _, suffix, _ in calls] == ["issues/comments"]
    assert webhook(owner, payload(comment_id=comment_id), "legacy-guid").json()["run_id"] == run_id
    assert webhook(owner, payload(comment_id=comment_id), "another-guid").json()["run_id"] == run_id
    with Session() as db:
        assert len(db.scalars(select(Run)).all()) == 1
        assert len(db.scalars(select(Job)).all()) == 2
        assert db.get(Run, run_id).status == "completed"
        assert db.get(Run, run_id).delivery == "legacy-guid"


def test_upgrade_recognizes_redelivered_rejection_without_snapshot_id(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    data = payload(body="@litereview unknown", comment_id=3004)
    run_id = webhook(owner, data, "legacy-rejected-guid").json()["run_id"]
    with Session.begin() as db:
        run = db.get(Run, run_id)
        run.delivery = "legacy-rejected-guid"
        assert "trigger_comment_id" not in run.snapshot
    assert webhook(owner, data, "legacy-rejected-guid").json()["run_id"] == run_id
    with Session() as db:
        assert len(db.scalars(select(Run)).all()) == 1
        assert db.scalars(select(Job)).all() == []


async def test_simultaneous_webhook_and_sweep_enqueue_once(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    stub_sweep_github(monkeypatch, {"owner/repo": [make_comment(3005, since)]})
    entered = 0
    both_entered = asyncio.Event()

    async def permission(*args):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await both_entered.wait()
        return "write"

    monkeypatch.setattr(github, "permission", permission)
    webhook_run, _ = await asyncio.wait_for(asyncio.gather(
        reviews.accept(payload(comment_id=3005), "webhook-guid"),
        comment_sweep.sweep_once(since),
    ), timeout=5)
    with Session() as db:
        runs = db.scalars(select(Run)).all()
        assert len(runs) == 1
        assert runs[0].id == webhook_run
        assert len(db.scalars(select(Job)).all()) == 2


@pytest.mark.parametrize("denial", ["github_permission", "user_grant", "repo_grant", "sso"])
async def test_sweep_enforces_caller_and_repository_access(owner, monkeypatch, denial):
    setup(owner)
    since = 1_700_000_000
    stub_sweep_github(monkeypatch, {"owner/repo": [make_comment(3006, since)]})
    if denial == "github_permission":
        monkeypatch.setattr(github, "permission", AsyncMock(return_value="none"))
    elif denial == "sso":
        monkeypatch.setattr(settings(), "google_sso_required", True)
    else:
        subject = "user" if denial == "user_grant" else "repo"
        with Session.begin() as db:
            grant = next(r for r in db.scalars(select(Resource).where(Resource.kind == "access"))
                         if r.data["subject"] == subject)
            grant.data = {**grant.data, "scope": "selected", "target_ids": []}

    await comment_sweep.sweep_once(since)
    await comment_sweep.sweep_once(since)
    with Session() as db:
        assert db.scalars(select(Job)).all() == []
        runs = db.scalars(select(Run)).all()
        if denial == "github_permission":
            assert runs == []
        else:
            assert len(runs) == 1
            assert runs[0].status == "rejected"


async def test_sweep_scans_beyond_ten_pages(owner, monkeypatch):
    setup(owner)
    since = 1_700_000_000
    pages = {
        ("owner/repo", page): [
            make_comment(page * 100 + offset, since + 2000 - page * 100 - offset, body="not a command")
            for offset in range(100)
        ]
        for page in range(1, 11)
    }
    pages[("owner/repo", 11)] = [make_comment(3007, since + 1)]
    calls = stub_sweep_github(monkeypatch, {}, pages_by_repo=pages)

    assert await comment_sweep.sweep_once(since) == 1
    assert [params["page"] for _, suffix, params in calls if suffix == "issues/comments"] == list(range(1, 12))
    with Session() as db:
        assert db.scalar(select(Run)).delivery == "comment:3007"


async def test_stalled_repository_does_not_block_other_repositories(owner, monkeypatch):
    setup(owner)
    create(owner, "repos", name="owner/other", installation_id=43)
    since = 1_700_000_000
    stub_sweep_github(monkeypatch, {"owner/other": [make_comment(3008, since)]})
    request = github.repo_request
    timeout = asyncio.timeout
    monkeypatch.setattr(comment_sweep.asyncio, "timeout", lambda seconds: timeout(0.05))

    async def stalled(repo, *args, **kwargs):
        if repo == "owner/repo":
            await asyncio.Event().wait()
        return await request(repo, *args, **kwargs)

    monkeypatch.setattr(github, "repo_request", stalled)
    assert await comment_sweep.sweep_once(since) == 1
    with Session() as db:
        assert db.scalar(select(Run)).repo == "owner/other"
