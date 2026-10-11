import asyncio
import copy
import hashlib
import hmac
import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from test_reviews import create, setup

from app import automatic_reviews, github
from app.db import AutomaticReviewClaim, Job, Resource, Run, Session


def configure(owner, *, authors=None, events=None, target_group=False):
    _, _, agent, repo, group = setup(owner)
    rule = {"target_id": (group if target_group else agent)["id"],
            "authors": authors or [], "events": events or ["opened", "ready_for_review", "synchronize"]}
    response = owner.put("/api/resources/repos/" + repo["id"], json={**repo, "review_rules": [rule]})
    assert response.status_code == 200, response.text
    return agent, response.json(), group


def event(action="opened", author="someone", head="a" * 40, **pr):
    return {"action": action, "repository": {"full_name": "owner/repo"}, "installation": {"id": 42},
            "sender": {"login": "unrelated-pusher"},
            "pull_request": {"number": 1, "state": "open", "draft": False, "user": {"login": author},
                             "head": {"sha": head}, "base": {"sha": "b" * 40}, "title": "Test", "body": "", **pr}}


def github_pr(monkeypatch, data):
    async def request(repo, installation, method, suffix, **kwargs):
        return (httpx.Response(200, json=data["pull_request"]) if suffix.startswith("pulls/")
                else httpx.Response(200, text="diff --git a/x b/x\n+changed"))
    monkeypatch.setattr(github, "repo_request", AsyncMock(side_effect=request))
    monkeypatch.setattr(github, "permission", AsyncMock(side_effect=AssertionError("Not a manual caller")))


def deliver(owner, data, delivery="event-1", event_type="pull_request"):
    body = json.dumps(data).encode()
    return owner.post("/api/webhook/github", content=body, headers={
        "x-hub-signature-256": "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest(),
        "x-github-event": event_type, "x-github-delivery": delivery})


def test_every_author_rule_and_new_commit_are_snapshotted_and_idempotent(owner, monkeypatch):
    from app.settings import settings
    agent, repo, _ = configure(owner)
    data = event(author="outside-contributor")
    github_pr(monkeypatch, data)
    monkeypatch.setattr(settings(), "google_sso_required", True)
    first = deliver(owner, data).json()["run_id"]
    assert first
    assert deliver(owner, data).json()["run_id"] == first
    assert deliver(owner, event("ready_for_review", author="outside-contributor"), "event-2").json()["run_id"] == first
    with Session() as db:
        job = db.scalar(select(Job))
        assert job.config["agent_id"] == agent["id"]
        assert db.get(Run, first).actor == "outside-contributor"
        assert db.get(Run, first).snapshot["trigger"]["event"] == "opened"
        assert len(db.scalars(select(Job)).all()) == 1
        job.status = "failed"
        db.commit()
    assert deliver(owner, data, "event-3").json()["run_id"] == first  # no hidden retries
    second_data = event("synchronize", head="c" * 40)
    github_pr(monkeypatch, second_data)
    second = deliver(owner, second_data, "event-4").json()["run_id"]
    assert second and second != first
    with Session() as db:
        assert len(db.scalars(select(AutomaticReviewClaim)).all()) == 2


@pytest.mark.parametrize("author,matched", [("Alice", True), ("bob", True), ("dependabot[bot]", True),
                                           ("unrelated-pusher", False), ("charlie", False)])
def test_filters_pr_author_including_bots_not_sender(owner, monkeypatch, author, matched):
    configure(owner, authors=["@alice", "BOB", "dependabot[bot]"])
    data = event(author=author)
    github_pr(monkeypatch, data)
    assert bool(deliver(owner, data).json()["run_id"]) == matched


@pytest.mark.parametrize("change", [{"draft": True}, {"state": "closed"}])
def test_drafts_and_closed_prs_never_start(owner, monkeypatch, change):
    configure(owner)
    data = event(**change)
    github_pr(monkeypatch, data)
    assert deliver(owner, data).json()["ignored"]
    github.repo_request.assert_not_called()


@pytest.mark.parametrize("reason", ["installation", "event", "permission", "disabled", "obsolete", "closed", "removed"])
def test_authorization_and_live_state_prevent_unwanted_reviews(owner, monkeypatch, reason):
    _, repo, _ = configure(owner)
    data = event()
    live = copy.deepcopy(data)
    if reason == "installation":
        data["installation"]["id"] = 99
    elif reason == "event":
        data["action"] = "edited"
    elif reason == "obsolete":
        live["pull_request"]["head"]["sha"] = "d" * 40
    elif reason == "closed":
        live["pull_request"]["state"] = "closed"
    else:
        with Session() as db:
            if reason == "permission":
                grant = next(r for r in db.scalars(select(Resource).where(Resource.kind == "access")) if r.data["subject"] == "repo")
                grant.data = {**grant.data, "scope": "selected", "target_ids": []}
            elif reason == "disabled":
                row = db.get(Resource, repo["id"])
                row.data = {**row.data, "review_rules": [{**r, "enabled": False} for r in row.data["review_rules"]]}
            else:
                db.delete(db.get(Resource, repo["id"]))
            db.commit()
    github_pr(monkeypatch, live)
    assert deliver(owner, data).json()["ignored"]
    with Session() as db:
        assert not db.scalars(select(Job)).all()


def test_overlapping_agents_groups_and_later_events_keep_max_instance_count(owner, monkeypatch):
    agent, repo, group = configure(owner, target_group=True)
    rules = repo["review_rules"] + [{"target_id": agent["id"]}, {"target_id": group["id"], "events": ["reopened"]}]
    assert owner.put("/api/resources/repos/" + repo["id"], json={**repo, "review_rules": rules}).status_code == 200
    data = event()
    github_pr(monkeypatch, data)
    first = deliver(owner, data).json()["run_id"]
    assert deliver(owner, event("reopened"), "reopened").json()["run_id"] == first
    with Session() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 2
        assert {j.config["instance"] for j in jobs} == {1, 2}
        assert all(j.config["group"] == group["name"] for j in jobs)


def test_label_hook_matches_added_label_and_current_labels(owner, monkeypatch):
    agent, repo, _ = configure(owner)
    response = owner.put("/api/resources/repos/" + repo["id"], json={**repo, "review_rules": [
        {"target_id": agent["id"], "events": ["labeled"], "label": "Needs Review"}]})
    assert response.status_code == 200
    data = event("labeled", labels=[{"name": "needs review"}])
    data["label"] = {"name": "other"}
    github_pr(monkeypatch, data)
    assert deliver(owner, data).json()["ignored"]
    data["label"]["name"] = "NEEDS REVIEW"
    assert deliver(owner, data).json()["run_id"]


def test_rule_validation_and_reviewer_deletion(owner):
    agent, repo, _ = configure(owner)
    for rule in [{"target_id": agent["id"], "events": []}, {"target_id": agent["id"], "events": ["labeled"]},
                 {"target_id": agent["id"], "authors": ["not a username"]}, {"target_id": "missing"}]:
        assert owner.put("/api/resources/repos/" + repo["id"], json={**repo, "review_rules": [rule]}).status_code in (400, 422)
    assert owner.delete("/api/resources/agents/" + agent["id"]).status_code == 409


def test_concurrent_deliveries_claim_review_once(owner, monkeypatch):
    configure(owner)
    data = event()
    github_pr(monkeypatch, data)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda n: asyncio.run(automatic_reviews.accept(data, str(n))), [1, 2]))
    assert results[0] and results[0] == results[1]
    with Session() as db:
        assert len(db.scalars(select(Job)).all()) == 1
        assert len(db.scalars(select(Run)).all()) == 1


def test_usage_ranks_pr_runs_without_counting_instances_fusion_or_checks(owner, monkeypatch):
    agent, repo, group = configure(owner, target_group=True)
    unused = create(owner, "agents", **{**agent, "name": "aaa-unused"})
    data = event()
    github_pr(monkeypatch, data)
    run_id = deliver(owner, data).json()["run_id"]
    with Session() as db:
        db.add(Job(run_id=run_id, config={"agent_id": unused["id"], "fusion_parent_id": "parent"}))
        check = Run(delivery="check", repo="Harness check", pr=0, actor="workspace", head="", base="",
                    snapshot={"harness_check": True})
        db.add(check)
        db.flush()
        db.add(Job(run_id=check.id, config={"agent_id": unused["id"]}))
        db.commit()
    rows = owner.get("/api/resources/agents").json()
    assert [(r["id"], r["usage_count"]) for r in rows] == [(agent["id"], 1), (unused["id"], 0)]
    owner.put("/api/resources/agents/" + unused["id"], json={**unused, "instructions": "Edited most recently"})
    assert owner.get("/api/resources/agents").json()[0]["id"] == agent["id"]
