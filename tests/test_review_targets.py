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
        assert "### reviewer\n" in comment
        assert "· 1" not in comment
        assert "reviewer / reviewer" not in comment


def test_mixed_targets_expand_independently_and_deduplicate(owner, monkeypatch):
    _, _, agent, repo, _ = setup(owner)
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


@pytest.mark.parametrize("subject", ["user", "repo"])
def test_each_access_rule_must_allow_every_selected_target(owner, monkeypatch, subject):
    _, _, agent, repo, group = setup(owner)
    grants = owner.get("/api/resources/access").json()
    grant = next(
        g
        for g in grants
        if g["subject"] == subject and g["name"] == ("trusted" if subject == "user" else repo["name"])
    )
    update(owner, "access", grant, scope="selected", target_ids=[group["id"]])
    mock_github(monkeypatch, "read")
    rejected = webhook(owner, payload(body="@litereview general reviewer"))
    with Session() as db:
        assert db.get(Run, rejected.json()["run_id"]).status == "rejected"
        assert not db.scalars(select(Job)).all()
    accepted = webhook(owner, payload(body="@litereview general"), "group-only")
    with Session() as db:
        assert db.get(Run, accepted.json()["run_id"]).status == "queued"
        assert len(db.scalars(select(Job)).all()) == 2


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


def test_agent_command_name_validation(owner):
    _, _, agent, _, _ = setup(owner)
    for name in ["two words", "123", "-bad", "x" * 41]:
        response = owner.put("/api/resources/agents/" + agent["id"], json={**agent, "name": name})
        assert response.status_code == 422


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
