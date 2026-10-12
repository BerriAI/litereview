import httpx
import pytest
from sqlalchemy import select
from test_automatic_reviews import deliver
from test_review_publication import FINDING, finish
from test_review_publication import publication as publication

from app import github, rebuttals, review_sessions, reviews
from app.db import Job, Resource, ReviewFinding, Run, Session
from app.security import digest


def reply_payload(fake, root, body="The existing guard prevents that input.", **user):
    fake.sequence += 1
    comment = {"id": fake.sequence, "in_reply_to_id": root, "body": body,
               "user": {"login": "trusted", "id": 42, "type": "User", **user}}
    fake.threads.append(comment)
    return {"action": "created", "repository": {"full_name": "owner/repo"},
            "installation": {"id": 42}, "pull_request": {"number": 1}, "comment": comment}


async def original_finding(fake):
    finish(fake.run_id, findings=[FINDING])
    await reviews.publish(fake.run_id)
    return fake.threads[0]["id"]


def finish_reply(run_id, verdict, *, status="completed", limitations=None):
    with Session.begin() as db:
        job = db.scalar(select(Job).where(Job.run_id == run_id))
        job.status = status
        job.result = {"summary": "Checked the argument", "findings": [], "limitations": limitations or [],
                      "rebuttal": {"verdict": verdict, "reason": "Verified the guard in app.py.",
                                   "evidence": ["app.py:2 rejects that input before the cache lookup."]}}
        return job.id


@pytest.mark.parametrize("verdict", ["withdraw", "uphold", "uncertain"])
async def test_reply_decision_controls_only_its_finding_thread(publication, owner, monkeypatch, verdict):
    from unittest.mock import AsyncMock
    fake = publication
    root = await original_finding(fake)
    resolver = AsyncMock()
    monkeypatch.setattr(github, "resolve_review_thread", resolver)
    data = reply_payload(fake, root, body="f off" if verdict == "uphold" else "The guard rejects this.")
    response = deliver(owner, data, "reply", "pull_request_review_comment")
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    assert deliver(owner, data, "redelivery", "pull_request_review_comment").json()["run_id"] == run_id
    with Session() as db:
        job = db.scalar(select(Job).where(Job.run_id == run_id))
        assert job.config["task"] == "reply" and not job.config.get("fusion")
        assert db.get(Run, run_id).snapshot["reply"]["comments"][-1]["body"] == data["comment"]["body"]
    finish_reply(run_id, verdict)
    await rebuttals.publish_pending()
    await rebuttals.publish_pending()
    assert len([c for c in fake.threads if f"litereview:{run_id}:reply" in c["body"]]) == 1
    assert resolver.await_count == (1 if verdict == "withdraw" else 0)
    if verdict == "withdraw":
        assert resolver.call_args.args[-1] == root
    with Session() as db:
        finding = db.scalar(select(ReviewFinding).where(ReviewFinding.comment_id == root))
        assert finding.status == ("withdrawn" if verdict == "withdraw" else "open")
        assert db.get(Run, run_id).published == 2
        assert db.scalar(select(ReviewFinding).where(ReviewFinding.comment_id != root)).status == "open"
    await reviews.publish(fake.run_id)
    if verdict == "withdraw":
        assert any("Withdrawn after verification" in c["body"] for c in fake.comments)
        detail = owner.get("/api/runs/" + fake.run_id).json()
        assert any(s["status"] == "withdrawn" for j in detail["jobs"] for s in j["finding_states"].values())


@pytest.mark.parametrize("reason", ["new_head", "failed", "incomplete"])
async def test_inconclusive_work_never_resolves(publication, owner, monkeypatch, reason):
    from unittest.mock import AsyncMock
    fake = publication
    root = await original_finding(fake)
    resolver = AsyncMock()
    monkeypatch.setattr(github, "resolve_review_thread", resolver)
    data = reply_payload(fake, root)
    run_id = await rebuttals.accept(data, "reply")
    finish_reply(run_id, "withdraw", status="failed" if reason == "failed" else "completed",
                 limitations=["Model endpoint HTTP 503"] if reason == "incomplete" else [])
    if reason == "new_head":
        fake.head = "c" * 40
    await rebuttals.publish_pending()
    resolver.assert_not_called()
    detail = owner.get("/api/runs/" + run_id).json()
    assert detail["jobs"][0]["result"]["rebuttal"]["verdict"] == "uncertain"
    with Session() as db:
        assert db.scalar(select(ReviewFinding).where(ReviewFinding.comment_id == root)).status == "open"


@pytest.mark.parametrize("long_reply", [False, True])
async def test_reply_publication_recovers_lost_post_without_duplicate(publication, monkeypatch, long_reply):
    from unittest.mock import AsyncMock
    fake = publication
    root = await original_finding(fake)
    monkeypatch.setattr(github, "resolve_review_thread", AsyncMock())
    run_id = await rebuttals.accept(reply_payload(fake, root), "reply")
    job_id = finish_reply(run_id, "withdraw")
    if long_reply:
        with Session.begin() as db:
            job = db.get(Job, job_id)
            job.result = {**job.result, "rebuttal": {**job.result["rebuttal"],
                          "reason": "Evidence from code.\n" * 4000 + "End of verification."}}
    lost = False

    async def request(*args, **kwargs):
        nonlocal lost
        result = await fake.request(*args, **kwargs)
        body = kwargs.get("json", {}).get("body", "")
        if (args[2] == "POST" and args[3].endswith("/replies") and not lost
                and (not long_reply or ":part:2 -->" in body)):
            lost = True
            raise httpx.ReadTimeout("Lost response")
        return result

    monkeypatch.setattr(github, "repo_request", request)
    await rebuttals.publish_pending()
    with Session() as db:
        assert db.get(Run, run_id).published != 2
    await rebuttals.publish_pending()
    comments = [c for c in fake.threads if f"litereview:{run_id}:reply" in c["body"]]
    assert len(comments) == (2 if long_reply else 1)
    assert all(len(c["body"]) <= reviews.GITHUB_COMMENT_CHARACTERS for c in comments)
    if long_reply:
        assert "End of verification." in comments[-1]["body"]
    with Session() as db:
        assert db.get(Run, run_id).published == 2


async def test_changed_pr_removes_old_reply_continuation_on_retry(publication, monkeypatch):
    from unittest.mock import AsyncMock

    fake = publication
    root = await original_finding(fake)
    monkeypatch.setattr(github, "resolve_review_thread", AsyncMock(side_effect=httpx.ReadTimeout("retry")))
    run_id = await rebuttals.accept(reply_payload(fake, root), "reply")
    job_id = finish_reply(run_id, "withdraw")
    with Session.begin() as db:
        job = db.get(Job, job_id)
        job.result = {**job.result, "rebuttal": {**job.result["rebuttal"], "reason": "Evidence.\n" * 8000}}
    await rebuttals.publish_pending()
    assert len([c for c in fake.threads if f"litereview:{run_id}:reply" in c["body"]]) == 2
    fake.head = "d" * 40
    await rebuttals.publish_pending()
    comments = [c for c in fake.threads if f"litereview:{run_id}:reply" in c["body"]]
    assert len(comments) == 1 and "Unable to conclude" in comments[0]["body"]


@pytest.mark.parametrize("removed", [False, True])
async def test_group_identity_survives_rename_but_not_removed_membership(publication, removed):
    fake = publication
    root = await original_finding(fake)
    with Session.begin() as db:
        group = db.scalar(select(Resource).where(Resource.kind == "groups"))
        group.data = {**group.data, "name": "renamed",
                      "members": [{"agent_id": "different-agent", "count": 1}] if removed else group.data["members"]}
    assert bool(await rebuttals.accept(reply_payload(fake, root), "reply")) is not removed


@pytest.mark.parametrize("reason", ["bot", "unlisted", "foreign_thread", "repository", "edited", "sso"])
async def test_only_authorized_human_replies_to_our_threads_start_jobs(publication, monkeypatch, reason):
    from app.settings import settings
    fake = publication
    root = await original_finding(fake)
    data = reply_payload(fake, root)
    if reason == "bot":
        data["comment"]["user"]["type"] = "Bot"
    elif reason == "unlisted":
        data["comment"]["user"]["login"] = "unlisted"
    elif reason == "foreign_thread":
        data["comment"]["in_reply_to_id"] = 999
    elif reason == "repository":
        data["repository"]["full_name"] = "another/repo"
    elif reason == "edited":
        data["action"] = "edited"
    else:
        monkeypatch.setattr(settings(), "google_sso_required", True)
    assert await rebuttals.accept(data, "reply") is None
    with Session() as db:
        assert len(db.scalars(select(Job)).all()) == 2


async def test_followups_serialize_and_keep_decisions_before_next_task(publication, monkeypatch):
    from unittest.mock import AsyncMock
    fake = publication
    root = await original_finding(fake)
    monkeypatch.setattr(github, "resolve_review_thread", AsyncMock())
    first = await rebuttals.accept(reply_payload(fake, root), "one")
    second = await rebuttals.accept(reply_payload(fake, root, "Also see the regression test."), "two")
    with Session() as db:
        assert db.scalar(select(Job).where(Job.run_id == second)).status == "blocked"
    finish_reply(first, "withdraw")
    await review_sessions.prepare()
    with Session() as db:
        assert db.scalar(select(Job).where(Job.run_id == second)).status == "blocked"
    await rebuttals.publish_pending()
    await review_sessions.prepare()
    with Session() as db:
        next_job = db.scalar(select(Job).where(Job.run_id == second))
        assert next_job.status == "queued"
        assert any(f["status"] == "withdrawn" for f in next_job.config["finding_history"])
    finish_reply(second, "uncertain", status="failed")
    await rebuttals.publish_pending()
    with Session() as db:
        finding = db.scalar(select(ReviewFinding).where(ReviewFinding.comment_id == root))
        assert finding.status == "withdrawn" and finding.decision["run_id"] == first


async def test_finish_requires_structured_supported_verdict(publication, owner):
    fake = publication
    root = await original_finding(fake)
    run_id = await rebuttals.accept(reply_payload(fake, root), "reply")
    with Session.begin() as db:
        job = db.scalar(select(Job).where(Job.run_id == run_id))
        job.status, job.token_hash = "running", digest("reply-token")
        job_id = job.id
    endpoint = f"/api/internal/jobs/{job_id}/finish"
    headers = {"Authorization": "Bearer reply-token"}
    body = {"status": "completed", "summary": "Checked", "findings": [], "limitations": []}
    assert owner.post(endpoint, headers=headers, json=body).status_code == 422
    body["rebuttal"] = {"verdict": "withdraw", "reason": "Agreed", "evidence": []}
    assert owner.post(endpoint, headers=headers, json=body).status_code == 422
    body["rebuttal"]["evidence"] = ["app.py guard makes the alleged input unreachable."]
    assert owner.post(endpoint, headers=headers, json=body).status_code == 200


async def test_legacy_published_thread_is_adopted(publication):
    from app.db import ReviewSession
    fake = publication
    root = await original_finding(fake)
    with Session.begin() as db:
        for row in db.scalars(select(ReviewFinding)):
            db.delete(row)
        for row in db.scalars(select(ReviewSession)):
            db.delete(row)
    assert await rebuttals.accept(reply_payload(fake, root), "legacy")


async def test_removed_group_access_cannot_trigger_followup(publication):
    fake = publication
    root = await original_finding(fake)
    with Session.begin() as db:
        for grant in db.scalars(select(Resource).where(Resource.kind == "access")):
            grant.data = {**grant.data, "scope": "selected", "target_ids": []}
    assert await rebuttals.accept(reply_payload(fake, root), "denied") is None


@pytest.mark.parametrize("changed", [False, True])
async def test_resolve_uses_correct_thread_and_rechecks_head(monkeypatch, changed):
    from unittest.mock import AsyncMock

    from fastapi import HTTPException

    monkeypatch.setattr(github, "token", AsyncMock(return_value="test-only-token"))
    monkeypatch.setattr(github, "repo_request", AsyncMock(return_value=httpx.Response(200, json={
        "head": {"sha": ("b" if changed else "a") * 40}, "state": "open"})))
    mutations = []

    async def request(method, path, auth, **kwargs):
        assert path == "/graphql" and auth == "test-only-token"
        data = kwargs["json"]
        if data["query"].startswith("mutation"):
            mutations.append(data["variables"]["id"])
            return httpx.Response(200, json={"data": {"resolveReviewThread": {"thread": {"isResolved": True}}}})
        cursor = data["variables"]["cursor"]
        return httpx.Response(200, json={"data": {"repository": {"pullRequest": {"reviewThreads": {
            "nodes": [{"id": "right-thread" if cursor else "unrelated-thread", "isResolved": False,
                       "comments": {"nodes": [{"databaseId": 99 if cursor else 98}]}}],
            "pageInfo": {"hasNextPage": not bool(cursor), "endCursor": "next"},
        }}}}})

    monkeypatch.setattr(github, "request", request)
    if changed:
        with pytest.raises(HTTPException, match="PR changed"):
            await github.resolve_review_thread("owner/repo", 42, 1, 99, expected_head="a" * 40)
        assert not mutations
    else:
        await github.resolve_review_thread("owner/repo", 42, 1, 99, expected_head="a" * 40)
        assert mutations == ["right-thread"]
