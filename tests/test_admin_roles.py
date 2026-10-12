import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_spend import add_job, add_run, charge
from test_spend_access import assert_no_spend, assert_spend
from test_sso import enable, identity

from app.db import Resource, Session, SSOIdentity, WorkspaceAdmin, init_db
from app.main import app
from app.settings import settings


@pytest.fixture
def admin(client, monkeypatch):
    enable(monkeypatch)
    monkeypatch.setattr(settings(), "admin_emails", "ADMIN@BERRI.AI")
    identity(client, sub="admin-sub", email="admin@berri.ai", github_id=None, github_login="")
    return client


@pytest.fixture
def member(admin):
    client = TestClient(app)
    identity(client, sub="member-sub", session_id="member-session", github_id=None, github_login="")
    yield client
    client.close()


def role(client, sub="member-sub", value="admin"):
    return client.put(f"/api/people/{sub}/role", json={"role": value})


def test_admin_can_promote_unlinked_member_and_member_can_delegate(admin, member):
    assert member.get("/api/spend").status_code == 403
    assert role(admin).json()["is_admin"] is True
    # Retry does not create another grant; restart preserves the role.
    assert role(admin).status_code == 200
    init_db()
    assert member.get("/api/session").json()["is_admin"] is True
    assert member.get("/api/spend").status_code == 200
    with Session() as db:
        assert db.scalar(select(func.count()).select_from(WorkspaceAdmin)) == 1
        assert db.get(WorkspaceAdmin, "member-sub").granted_by == "admin-sub"
    rows = {r["identity_sub"]: r for r in admin.get("/api/resources/access").json()}
    assert rows["member-sub"]["is_admin"] is True
    assert rows["member-sub"]["admin_source"] == "people"
    assert rows["admin-sub"]["admin_source"] == "configuration"
    identity(sub="colleague", email="colleague@berri.ai")
    assert role(member, "colleague").status_code == 200


def test_revocation_takes_effect_for_existing_sessions_and_all_spend_surfaces(admin, member):
    add_run("fusion")
    parent = add_job("parent", "fusion", fusion={"branches": [{}, {}]})
    left = add_job("left", "fusion", fusion_parent_id=parent.id, fusion_index=0)
    right = add_job("right", "fusion", fusion_parent_id=parent.id, fusion_index=1)
    for job, cost in [(parent, ".2"), (left, ".3"), (right, ".5")]:
        charge(job, cost)
    assert role(admin).status_code == 200
    assert_spend(member)
    assert role(admin, value="member").json()["is_admin"] is False
    assert role(admin, value="member").status_code == 200
    assert_no_spend(member)
    assert role(member, "admin-sub").status_code == 403


def test_member_cannot_self_promote_through_roles_or_review_grants(admin, member):
    assert role(member).status_code == 403
    response = member.post("/api/resources/access", json={
        "subject": "user", "name": "trusted", "scope": "all", "target_ids": [],
        "is_admin": True, "role": "admin", "admin_source": "configuration",
    })
    assert response.status_code == 200
    assert member.get("/api/session").json()["is_admin"] is False
    for kind in ["workspace_admins", "admins"]:
        assert member.post(f"/api/resources/{kind}", json={"identity_sub": "member-sub"}).status_code == 404


def test_review_grant_edits_and_github_relinks_do_not_change_admin_role(admin, member):
    assert role(admin).status_code == 200
    with Session.begin() as db:
        person = db.get(SSOIdentity, "member-sub")
        person.github_login, person.github_id = "new-name", "99"
        person.email = "renamed@berri.ai"
    grant = member.post("/api/resources/access", json={
        "subject": "user", "name": "new-name", "scope": "selected", "target_ids": [],
    }).json()
    assert member.get("/api/session").json()["is_admin"] is True
    assert member.delete("/api/resources/access/" + grant["id"]).status_code == 200
    assert member.get("/api/session").json()["is_admin"] is True
    with Session() as db:
        assert db.scalar(select(Resource).where(Resource.kind == "access")) is None


def test_configured_admin_and_self_removal_are_protected(admin, member):
    assert role(admin, "admin-sub", "member").status_code == 409
    assert role(admin).status_code == 200
    assert role(member, value="member").status_code == 409
    assert member.get("/api/session").json()["is_admin"] is True


@pytest.mark.parametrize("changes", [
    {"domain": "outside.test", "email": "person@outside.test"},
    {"audience": "other-client"},
])
def test_ineligible_identity_cannot_be_promoted_or_use_a_saved_role(admin, member, changes):
    assert role(admin).status_code == 200
    with Session.begin() as db:
        person = db.get(SSOIdentity, "member-sub")
        for key, value in changes.items():
            setattr(person, key, value)
    assert role(admin).status_code == 409
    assert member.get("/api/spend").status_code == 401
    assert role(admin, value="member").status_code == 200
    with Session() as db:
        assert db.get(WorkspaceAdmin, "member-sub") is None


def test_role_endpoint_rejects_missing_people_invalid_roles_and_anonymous_users(admin):
    assert role(admin, "missing").status_code == 404
    assert role(admin, "admin-sub", "owner").status_code == 422
    admin.cookies.clear()
    assert role(admin).status_code == 401


def test_legacy_owner_cannot_create_google_roles(owner):
    assert role(owner).status_code == 400
