import pytest
from sqlalchemy import select
from test_reviews import mock_github, payload, setup, webhook
from test_sso import enable, identity

from app.db import Job, ReviewFeedback, Session, WorkspaceAdmin


@pytest.fixture
def review(owner, monkeypatch):
    _, _, agent, _, _ = setup(owner)
    mock_github(monkeypatch)
    rid = webhook(owner, payload(body="@litereview reviewer")).json()["run_id"]
    with Session.begin() as db:
        job = db.scalar(select(Job).where(Job.run_id == rid))
        job.status = "completed"
        job.result = {"summary": "Reviewed", "findings": [], "limitations": []}
        return rid, job.id, agent["id"]


def test_rating_is_editable_and_admin_can_filter(owner, review):
    rid, jid, aid = review
    response = owner.put(f"/api/jobs/{jid}/feedback", json={"rating": "down", "note": " Missed a bug "})
    assert response.status_code == 200
    assert response.json()["note"] == "Missed a bug"
    assert owner.get(f"/api/runs/{rid}?compact=true").json()["jobs"][0]["feedback"]["rating"] == "down"
    report = owner.get("/api/feedback", params={"rating": "down", "agent_id": aid}).json()
    assert report["total"] == 1
    assert report["items"][0]["job_id"] == jid
    assert report["items"][0]["reporter"] == "owner"
    assert report["items"][0]["repo"] == "owner/repo"
    assert owner.get("/api/feedback?rating=up").json()["total"] == 0
    owner.put(f"/api/jobs/{jid}/feedback", json={"rating": "up", "note": "Actually correct"}).raise_for_status()
    assert owner.get("/api/feedback").json()["total"] == 1
    assert owner.get("/api/feedback?rating=down").json()["total"] == 0
    with Session() as db:
        assert len(db.scalars(select(ReviewFeedback)).all()) == 1


def test_members_only_see_their_own_rating_and_admin_role_is_rechecked(owner, review, monkeypatch):
    rid, jid, _ = review
    enable(monkeypatch)
    identity(owner)
    owner.put(f"/api/jobs/{jid}/feedback", json={"rating": "down", "note": "My feedback"}).raise_for_status()
    assert owner.get("/api/feedback").status_code == 403
    identity(owner, sub="other", session_id="other-session", github_id="43", email="other@berri.ai")
    assert owner.get(f"/api/runs/{rid}").json()["jobs"][0]["feedback"] is None
    owner.put(f"/api/jobs/{jid}/feedback", json={"rating": "up"}).raise_for_status()
    with Session.begin() as db:
        db.add(WorkspaceAdmin(identity_sub="other", granted_by="test"))
    assert owner.get("/api/feedback").json()["total"] == 2
    with Session.begin() as db:
        db.delete(db.get(WorkspaceAdmin, "other"))
    assert owner.get("/api/feedback").status_code == 403


@pytest.mark.parametrize("reason", ["running", "branch", "comparison", "skipped"])
def test_only_finished_public_reviewer_results_can_be_rated(owner, review, reason):
    rid, jid, _ = review
    with Session.begin() as db:
        job = db.get(Job, jid)
        if reason == "running":
            job.status = "running"
        elif reason == "skipped":
            job.result = {**job.result, "skipped": True}
        else:
            job.config = {**job.config, "fusion_parent_id" if reason == "branch" else "comparison_parent_id": "parent"}
    assert owner.put(f"/api/jobs/{jid}/feedback", json={"rating": "down"}).status_code == 409


def test_feedback_validates_inputs_and_requires_sign_in(owner, review):
    _, jid, _ = review
    for body in [{"rating": "maybe"}, {"rating": "up", "note": "x" * 4001},
                 {"rating": "up", "reporter": "someone else"}]:
        assert owner.put(f"/api/jobs/{jid}/feedback", json=body).status_code == 422
    assert owner.put("/api/jobs/missing/feedback", json={"rating": "up"}).status_code == 404
    assert owner.get("/api/feedback?limit=0").status_code == 422
    assert owner.get("/api/feedback?offset=-1").status_code == 422
    owner.cookies.clear()
    assert owner.put(f"/api/jobs/{jid}/feedback", json={"rating": "down"}).status_code == 401
    assert owner.get("/api/feedback").status_code == 401
