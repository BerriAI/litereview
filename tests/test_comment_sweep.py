from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
from sqlalchemy import select
from test_reviews import create, mock_github, payload, setup, webhook

from app import comment_sweep, github
from app.db import Job, Run, Session


def make_comment(comment_id, since, body="@litereview general", user_type="User", issue_number=1):
    return {
        "id": comment_id,
        "created_at": datetime.fromtimestamp(since, timezone.utc).isoformat().replace("+00:00", "Z"),
        "issue_url": f"https://api.github.com/repos/owner/repo/issues/{issue_number}",
        "user": {"login": "trusted", "type": user_type},
        "body": body,
    }


def stub_sweep_github(monkeypatch, comments_by_repo, issues=None, fail_repos=()):
    calls = []
    monkeypatch.setattr(github, "permission", AsyncMock(return_value="write"))
    issues = issues or {}

    async def request(repo, installation, method, suffix, **kwargs):
        calls.append((repo, suffix, kwargs.get("params")))
        if suffix == "issues/comments":
            if repo in fail_repos:
                raise github.GitHubError(httpx.Response(403), method, suffix)
            return httpx.Response(200, json=comments_by_repo.get(repo, []))
        if suffix.startswith("issues/"):
            number = int(suffix.rsplit("/", 1)[-1])
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
        "direction": "asc",
        "per_page": 100,
        "page": 1,
    }


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
