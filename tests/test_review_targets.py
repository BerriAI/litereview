"""Direct agents and groups share names, but authorize calls independently."""

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from test_reviews import create, mock_github, payload, setup, webhook

from app import reviews
from app.db import Job, Resource, Run, Session, engine, init_db, review_target_names


def update(owner, kind, row, **changes):
    response = owner.put(f"/api/resources/{kind}/{row['id']}", json={**row, **changes})
    assert response.status_code == 200, response.text
    return response.json()


def test_direct_agent_needs_no_group(owner, monkeypatch):
    _, _, agent, repo, group = setup(owner)
    owner.delete("/api/resources/groups/" + group["id"]).raise_for_status()
    update(owner, "agents", agent, repos=[repo["name"]])
    mock_github(monkeypatch)
    response = webhook(owner, payload(body="@litereview REVIEWER reviewer"))
    assert response.status_code == 200
    with Session() as db:
        run = db.get(Run, response.json()["run_id"])
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 1
        assert jobs[0].config["agent_id"] == agent["id"]
        assert jobs[0].config["target_kind"] == "agent"
        assert jobs[0].config["instance"] == 1
        assert run.head == "a" * 40
        comment = reviews.render_comment(run, jobs)
        assert "### reviewer · 1" in comment
        assert "reviewer / reviewer" not in comment


def test_mixed_targets_expand_independently_and_deduplicate(owner, monkeypatch):
    _, _, agent, repo, _ = setup(owner)
    update(owner, "agents", agent, repos=[repo["name"]])
    mock_github(monkeypatch)
    response = webhook(owner, payload(body="@litereview reviewer GENERAL reviewer general"))
    with Session() as db:
        jobs = db.scalars(select(Job)).all()
        assert len(jobs) == 3
        assert sorted(j.config["target_kind"] for j in jobs) == ["agent", "group", "group"]
        assert len({j.id for j in jobs}) == 3
        assert all(j.config["agent_id"] == agent["id"] for j in jobs)
        run_id = response.json()["run_id"]
        assert len(db.get(Run, run_id).snapshot["groups"]) == 2
    # Same delivery cannot duplicate jobs or re-resolve edited settings.
    assert webhook(owner, payload()).json()["run_id"] == run_id
    with Session() as db:
        assert len(db.scalars(select(Job)).all()) == 3


@pytest.mark.parametrize("denial", ["no_repos", "other_repo", "caller", "unknown"])
def test_invalid_direct_selection_rejects_entire_mixed_command(owner, monkeypatch, denial):
    _, _, agent, repo, _ = setup(owner)
    changes = {"repos": [repo["name"]]}
    if denial == "no_repos":
        changes["repos"] = []
    elif denial == "other_repo":
        other = create(owner, "repos", name="owner/other", installation_id=42)
        changes["repos"] = [other["name"]]
    elif denial == "caller":
        changes.update(caller_policy="allowlist", callers=["someone-else"])
    update(owner, "agents", agent, **changes)
    mock_github(monkeypatch)
    name = "missing" if denial == "unknown" else agent["name"]
    response = webhook(owner, payload(body=f"@litereview general {name}"))
    with Session() as db:
        assert db.get(Run, response.json()["run_id"]).status == "rejected"
        assert not db.scalars(select(Job)).all()
    # Group access is independent: membership still works when direct access is disabled.
    response = webhook(owner, payload(body="@litereview general"), "group-only")
    with Session() as db:
        assert db.get(Run, response.json()["run_id"]).status == "queued"
        assert len(db.scalars(select(Job)).all()) == 2


def test_direct_allowlist_does_not_grant_group_access(owner, monkeypatch):
    _, _, agent, repo, _ = setup(owner)
    agent = update(
        owner,
        "agents",
        agent,
        repos=[repo["name"].upper()],
        caller_policy="allowlist",
        callers=[" TRUSTED ", "trusted"],
    )
    assert agent["callers"] == ["trusted"]
    mock_github(monkeypatch, "read")
    direct = webhook(owner, payload(actor="Trusted", body="@litereview reviewer"))
    mixed = webhook(owner, payload(actor="Trusted", body="@litereview reviewer general"), "mixed")
    with Session() as db:
        assert db.get(Run, direct.json()["run_id"]).status == "queued"
        assert db.get(Run, mixed.json()["run_id"]).status == "rejected"
        assert len(db.scalars(select(Job)).all()) == 1
    mock_github(monkeypatch, "none")
    assert webhook(owner, payload(body="@litereview reviewer"), "no-repo-access").json()["ignored"]


@pytest.mark.parametrize(
    "first_kind,second_kind",
    [("agents", "agents"), ("groups", "groups"), ("agents", "groups"), ("groups", "agents")],
)
def test_shared_case_insensitive_names_on_create_and_update(owner, first_kind, second_kind):
    _, _, agent, _, group = setup(owner)
    templates = {"agents": agent, "groups": group}
    first = create(owner, first_kind, **{**templates[first_kind], "name": "Shared-Name"})
    assert first["name"] == "shared-name"
    duplicate = owner.post(
        "/api/resources/" + second_kind, json={**templates[second_kind], "name": "SHARED-NAME"}
    )
    assert duplicate.status_code == 409
    assert "unique names" in duplicate.text
    second = create(owner, second_kind, **{**templates[second_kind], "name": "different"})
    duplicate = owner.put(
        f"/api/resources/{second_kind}/{second['id']}", json={**second, "name": "SHARED-name"}
    )
    assert duplicate.status_code == 409
    # Saving the same resource and renaming to an available name both work.
    update(owner, first_kind, first, name="SHARED-NAME")
    update(owner, first_kind, first, name="available")
    update(owner, second_kind, second, name="shared-name")


@pytest.mark.parametrize("kind", ["agents", "groups"])
def test_database_prevents_duplicate_names_even_without_api(owner, kind):
    setup(owner)
    # Simulate upgrading an existing table; startup may be repeated safely.
    review_target_names.drop(engine)
    init_db()
    init_db()
    with Session() as db:
        db.add(Resource(kind=kind, data={"name": "REVIEWER"}))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        row = Resource(kind=kind, data={"name": "available"})
        db.add(row)
        db.commit()
        row.data = {"name": "GENERAL"}
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        # Unrelated resource types may share names.
        db.add(Resource(kind="skills", data={"name": "reviewer"}))
        db.commit()


def test_agent_access_validation_and_repository_deletion(owner):
    _, _, agent, repo, group = setup(owner)
    response = owner.put(
        "/api/resources/agents/" + agent["id"], json={**agent, "repos": ["unconnected/repo"]}
    )
    assert response.status_code == 400
    response = owner.put(
        "/api/resources/agents/" + agent["id"],
        json={**agent, "caller_policy": "allowlist", "callers": [" ", ""]},
    )
    assert response.status_code == 400
    for name in ["two words", "123", "-bad", "x" * 41]:
        response = owner.put("/api/resources/agents/" + agent["id"], json={**agent, "name": name})
        assert response.status_code == 422
    owner.delete("/api/resources/groups/" + group["id"]).raise_for_status()
    agent = update(owner, "agents", agent, repos=[repo["name"].upper()])
    response = owner.delete("/api/resources/repos/" + repo["id"])
    assert response.status_code == 409
    assert "agent" in response.text
    update(owner, "agents", agent, repos=[])
    assert owner.delete("/api/resources/repos/" + repo["id"]).status_code == 200


def test_editor_probes_current_unsaved_model_without_saving_agent(owner, monkeypatch):
    from unittest.mock import AsyncMock

    from app import providers

    connection = create(owner, "connections", name="Model provider", type="openai")
    probe = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(providers, "probe", probe)
    response = owner.post(
        "/api/agents/test",
        json={
            "connection_id": connection["id"],
            "model": "new-model",
            "capabilities": ["computer"],
        },
    )
    assert response.status_code == 200
    assert probe.call_args.args[1:] == ("new-model", True)
    assert owner.get("/api/resources/agents").json() == []
    mcp = create(owner, "connections", name="Tools", type="mcp")
    assert (
        owner.post("/api/agents/test", json={"connection_id": mcp["id"], "model": "model"}).status_code == 400
    )
    assert (
        owner.post("/api/agents/test", json={"connection_id": connection["id"], "model": ""}).status_code
        == 422
    )


def test_model_probe_requires_authentication(client):
    assert client.post("/api/agents/test", json={"connection_id": "any", "model": "any"}).status_code == 401
