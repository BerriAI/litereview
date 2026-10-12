import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_reviews import create, mock_github, payload, setup, webhook

from app.access import initialize_access
from app.db import Job, Resource, Run, Session
from app.settings import settings


def grant(owner, subject, name, scope="all", target_ids=None):
    rows = owner.get("/api/resources/access").json()
    existing = next((r for r in rows if r["subject"] == subject and r["name"] == name.lower()), None)
    data = dict(subject=subject, name=name, scope=scope, target_ids=target_ids or [])
    response = (
        owner.put("/api/resources/access/" + existing["id"], json=data)
        if existing
        else owner.post("/api/resources/access", json=data)
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_all_grants_include_future_targets_and_still_require_github_access(owner, monkeypatch):
    _, _, agent, _, _ = setup(owner)
    later = create(owner, "agents", **{**agent, "name": "later-reviewer"})
    mock_github(monkeypatch, "read")
    response = webhook(owner, payload(body="@litereview later-reviewer"))
    with Session() as db:
        assert db.get(Run, response.json()["run_id"]).status == "queued"
        assert db.scalar(select(Job)).config["agent_id"] == later["id"]
    mock_github(monkeypatch, "none")
    assert webhook(
        owner, payload(body="@litereview later-reviewer", comment_id=12346), "denied-github"
    ).json()["ignored"]
    mock_github(monkeypatch, "admin")
    denied = webhook(
        owner, payload(actor="unlisted", body="@litereview later-reviewer", comment_id=12347), "unlisted"
    )
    with Session() as db:
        assert db.get(Run, denied.json()["run_id"]).status == "rejected"
        assert len(db.scalars(select(Job)).all()) == 1


@pytest.mark.parametrize("subject", ["user", "repo"])
def test_revoked_access_stays_revoked_after_restart(owner, monkeypatch, subject):
    _, _, _, repo, _ = setup(owner)
    item = grant(owner, subject, "trusted" if subject == "user" else repo["name"])
    assert owner.delete("/api/resources/access/" + item["id"]).status_code == 200
    initialize_access()
    mock_github(monkeypatch)
    response = webhook(owner, payload())
    with Session() as db:
        assert db.get(Run, response.json()["run_id"]).status == "rejected"
        assert not db.scalars(select(Job)).all()


def test_selected_grants_survive_rename_and_clean_up_deletions(owner):
    _, _, agent, repo, group = setup(owner)
    person = grant(owner, "user", "trusted", "selected", [agent["id"]])
    repository = grant(owner, "repo", repo["name"], "selected", [agent["id"]])
    renamed = owner.put("/api/resources/agents/" + agent["id"], json={**agent, "name": "renamed"})
    assert renamed.status_code == 200
    assert (
        owner.put("/api/resources/repos/" + repo["id"], json={**repo, "name": "owner/renamed"}).status_code
        == 200
    )
    rows = owner.get("/api/resources/access").json()
    assert next(r for r in rows if r["id"] == repository["id"])["name"] == "owner/renamed"
    assert next(r for r in rows if r["id"] == person["id"])["target_ids"] == [agent["id"]]
    owner.delete("/api/resources/groups/" + group["id"]).raise_for_status()
    owner.delete("/api/resources/agents/" + agent["id"]).raise_for_status()
    rows = owner.get("/api/resources/access").json()
    assert all(
        r["target_ids"] == [] and r["scope"] == "selected"
        for r in rows
        if r["id"] in (person["id"], repository["id"])
    )
    owner.delete("/api/resources/repos/" + repo["id"]).raise_for_status()
    assert not any(r["id"] == repository["id"] for r in owner.get("/api/resources/access").json())


def test_access_validation_and_database_uniqueness(owner):
    setup(owner)
    row = grant(owner, "user", "@Teammate")
    assert row["name"] == "teammate" and row["scope"] == "all"
    assert owner.post("/api/resources/access", json={"name": "TEAMMATE"}).status_code == 409
    for bad in [
        {"subject": "repo", "name": "owner/unconnected"},
        {"name": "new-user", "scope": "selected", "target_ids": ["missing"]},
    ]:
        assert owner.post("/api/resources/access", json=bad).status_code == 400
    assert owner.post("/api/resources/access", json={"name": "two users"}).status_code == 422
    with Session() as db:
        db.add(
            Resource(
                kind="access", data={"name": "TEAMMATE", "subject": "user", "scope": "all", "target_ids": []}
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_legacy_explicit_grants_migrate_once_without_dynamic_collaborators(owner, monkeypatch):
    _, _, agent, repo, group = setup(owner)
    monkeypatch.setattr(settings(), "admin_users", "workspace-owner")
    with Session() as db:
        for existing in db.scalars(select(Resource).where(Resource.kind == "access")):
            db.delete(existing)
        db.delete(db.get(Resource, "access-v1"))
        db.get(Resource, agent["id"]).data = {
            **agent,
            "repos": [repo["name"]],
            "caller_policy": "allowlist",
            "callers": ["Legacy-User"],
        }
        db.get(Resource, group["id"]).data = {
            **group,
            "repos": [repo["name"]],
            "caller_policy": "collaborators",
            "callers": [],
        }
        db.commit()
    initialize_access()
    initialize_access()
    rows = owner.get("/api/resources/access").json()
    assert len(rows) == 3
    assert next(r for r in rows if r["name"] == "workspace-owner")["scope"] == "all"
    assert next(r for r in rows if r["name"] == "legacy-user")["target_ids"] == [agent["id"]]
    assert set(next(r for r in rows if r["subject"] == "repo")["target_ids"]) == {agent["id"], group["id"]}
    assert all(
        "caller_policy" not in r and "repos" not in r for r in owner.get("/api/resources/agents").json()
    )


@pytest.mark.parametrize("sso", [False, True])
@pytest.mark.parametrize("body", ["@litereview reviewer review this pls", "@litereview"])
def test_open_agent_accepts_external_caller_without_workspace_or_repo_membership(owner, monkeypatch, sso, body):
    from test_sso import enable

    from app import github

    _, _, agent, _, _ = setup(owner)
    assert agent["trigger_access"] == "workspace"
    owner.put("/api/resources/agents/" + agent["id"], json={**agent, "trigger_access": "anyone"}).raise_for_status()
    # Anyone is explicit opt-in, even for a caller restricted in People.
    grant(owner, "user", "outsider", "selected", [])
    if sso:
        enable(monkeypatch)
    mock_github(monkeypatch, "none")
    run_id = webhook(owner, payload(actor="outsider", body=body)).json()["run_id"]
    github.permission.assert_not_awaited()
    with Session() as db:
        assert db.get(Run, run_id).status == "queued"
        assert db.scalar(select(Job)).config["agent_id"] == agent["id"]
    assert owner.get("/api/resources/agents").status_code == (401 if sso else 200)


@pytest.mark.parametrize("restriction", ["repo", "installation", "group", "mixed", "bot", "revoked"])
def test_open_agent_does_not_bypass_other_boundaries(owner, monkeypatch, restriction):
    _, _, agent, repo, _ = setup(owner)
    owner.put("/api/resources/agents/" + agent["id"], json={**agent, "trigger_access": "anyone"}).raise_for_status()
    mock_github(monkeypatch, "none")
    data = payload(actor="outsider", body="@litereview reviewer")
    if restriction == "repo":
        grant(owner, "repo", repo["name"], "selected", [])
    elif restriction == "installation":
        data["installation"]["id"] = 999
    elif restriction == "group":
        data["comment"]["body"] = "@litereview general"
    elif restriction == "mixed":
        data["comment"]["body"] = "@litereview reviewer general"
    elif restriction == "bot":
        data["comment"]["user"]["type"] = "Bot"
    elif restriction == "revoked":
        owner.put("/api/resources/agents/" + agent["id"], json=agent).raise_for_status()
    assert webhook(owner, data).status_code == 200
    with Session() as db:
        assert not db.scalar(select(Job))
