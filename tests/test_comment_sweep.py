from datetime import datetime, timezone
from unittest.mock import AsyncMock

import httpx
from sqlalchemy import select
from test_reviews import create, mock_github, payload, setup, webhook

from app import comment_sweep, github
from app.db import Job, Run, Session


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
