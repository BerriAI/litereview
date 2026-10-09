import httpx
import pytest
from sqlalchemy import select
from test_reviews import mock_github, payload, setup, webhook

from app import github, reviews
from app.db import Job, Run, Session


def finish(run_id, status="completed"):
    with Session() as db:
        for job in db.scalars(select(Job).where(Job.run_id == run_id)).all():
            job.status = status
            job.result = {"summary": "Checked the changed paths.", "findings": [], "limitations": []}
        db.commit()


@pytest.fixture
def publication(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    run_id = webhook(owner, payload()).json()["run_id"]
    calls = []

    async def request(repo, installation, method, suffix, **kwargs):
        assert (repo, installation) == ("owner/repo", 42)
        calls.append((method, suffix, kwargs.get("json")))
        if suffix.startswith("pulls/"):
            return httpx.Response(200, json={"head": {"sha": "a" * 40}})
        if method == "GET":
            return httpx.Response(200, json=[])
        return httpx.Response(201, json={"id": 67890})

    monkeypatch.setattr(github, "repo_request", request)
    return run_id, calls, request


async def test_acknowledgment_then_one_final_comment_after_every_agent(publication):
    run_id, calls, _ = publication
    await reviews.publish(run_id)
    assert calls == [("POST", "issues/comments/12345/reactions", {"content": "eyes"})]
    with Session() as db:
        run = db.get(Run, run_id)
        assert run.snapshot["trigger_comment_id"] == 12345
        assert run.published == 1 and run.comment_id is None and run.status == "running"
        db.scalar(select(Job).where(Job.run_id == run_id)).status = "completed"
        db.commit()
    await reviews.publish(run_id)
    assert len(calls) == 1
    finish(run_id)
    await reviews.publish(run_id)
    posted = [body for method, path, body in calls if path == "issues/1/comments" and method == "POST"]
    assert len(posted) == 1
    assert "No issues found." in posted[0]["body"]
    assert "Review in progress" not in posted[0]["body"]
    count = len(calls)
    await reviews.publish(run_id)
    assert len(calls) == count
    with Session() as db:
        run = db.get(Run, run_id)
        assert run.published == 2 and run.comment_id == 67890 and run.status == "completed"


async def test_lost_reaction_response_retries_without_comment(publication, monkeypatch):
    run_id, calls, request = publication
    first = True

    async def lost_response(*args, **kwargs):
        nonlocal first
        response = await request(*args, **kwargs)
        if first:
            first = False
            raise httpx.ReadTimeout("Response lost after GitHub accepted the reaction")
        # GitHub returns 200 when the same App has already reacted with eyes.
        return httpx.Response(200, json=response.json())

    monkeypatch.setattr(github, "repo_request", lost_response)
    with pytest.raises(httpx.ReadTimeout):
        await reviews.publish(run_id)
    with Session() as db:
        assert db.get(Run, run_id).published == 0
    await reviews.publish(run_id)
    assert [path for _, path, _ in calls] == ["issues/comments/12345/reactions"] * 2


@pytest.mark.parametrize("reaction_fails", [False, True])
async def test_finished_review_publishes_even_if_acknowledgment_was_missed(
    publication, monkeypatch, reaction_fails
):
    run_id, calls, request = publication
    finish(run_id)

    async def react(*args, **kwargs):
        if reaction_fails and args[3].endswith("/reactions"):
            raise httpx.ReadTimeout("Request comment unavailable")
        return await request(*args, **kwargs)

    monkeypatch.setattr(github, "repo_request", react)
    await reviews.publish(run_id)
    assert sum(path == "issues/1/comments" and method == "POST" for method, path, _ in calls) == 1


async def test_existing_progress_comment_is_updated_only_when_finished(publication):
    run_id, calls, _ = publication
    with Session() as db:
        run = db.get(Run, run_id)
        run.snapshot = {k: v for k, v in run.snapshot.items() if k != "trigger_comment_id"}
        run.published = 1
        run.comment_id = 24680
        db.commit()
    await reviews.publish(run_id)
    assert not calls
    finish(run_id, "failed")
    await reviews.publish(run_id)
    assert calls[-1][:2] == ("PATCH", "issues/comments/24680")
    assert "Review incomplete." in calls[-1][2]["body"]
    assert not any(method == "POST" for method, _, _ in calls)


async def test_rejected_command_explains_failure_without_eyes(publication):
    _, calls, _ = publication
    run_id = reviews.reject_command("rejected", "owner/repo", 1, "trusted", 42, "Unknown reviewer.")
    await reviews.publish(run_id)
    assert not any(path.endswith("/reactions") for _, path, _ in calls)
    assert "Review not started" in calls[-1][2]["body"]


def test_clean_review_keeps_notes_collapsed_and_metadata_in_footer():
    run = Run(id="clean", repo="owner/repo", head="a" * 40, status="completed")
    job = Job(
        config={"name": "yujong", "target_kind": "agent", "group": "yujong", "instance": 1},
        status="completed",
        result={"summary": "Long review narrative.", "findings": [], "limitations": []},
    )
    body = reviews.render_comment(run, [job])
    assert body.startswith("<!-- litereview:clean -->\n\n### yujong\n\n**No issues found.**")
    assert body.index("<details>") < body.index("Long review narrative.") < body.index("</details>")
    assert "Review complete" not in body and "· 1" not in body and "1 agents" not in body
    assert body.endswith(f"Reviewed [`{'a' * 12}`](https://github.com/owner/repo/commit/{'a' * 40})")


def test_findings_and_incomplete_coverage_stay_visible():
    run = Run(id="partial", repo="owner/repo", head="a" * 40, status="incomplete")
    job = Job(
        config={"name": "security", "target_kind": "agent", "group": "security", "instance": 1},
        status="failed",
        result={
            "summary": "Could not finish browser checks.",
            "findings": [
                {
                    "title": "Token exposed",
                    "severity": "high",
                    "explanation": "Remove @all <script>.",
                    "file": "app/auth.py",
                    "line": 42,
                }
            ],
            "limitations": ["Browser unavailable."],
        },
    )
    body = reviews.render_comment(run, [job], newer=True)
    assert "New commits were pushed" in body
    assert "**High · Token exposed** · [app/auth.py:42]" in body
    assert "#L42" in body and "Browser unavailable." in body
    assert "Review incomplete." in body and "No issues found" not in body
    visible, notes = body.split("<details>", 1)
    assert "Browser unavailable." in visible and "Token exposed" in visible
    assert "Could not finish browser checks." not in visible
    assert "Could not finish browser checks." in notes
    assert "@all" not in body and "<script>" not in body
