from decimal import Decimal

import pytest
from test_spend import add_job, add_run, charge
from test_sso import enable, identity

from app import spend
from app.db import ModelUsage, Session, SSOIdentity
from app.security import signed
from app.settings import settings


@pytest.fixture
def metered_run(client):
    add_run("fusion")
    parent = add_job("parent", "fusion", fusion={"branches": [{}, {}]})
    left = add_job("left", "fusion", fusion_parent_id=parent.id, fusion_index=0)
    right = add_job("right", "fusion", fusion_parent_id=parent.id, fusion_index=1)
    for job, cost in [(parent, ".2"), (left, ".3"), (right, ".5")]:
        charge(job, cost)


def assert_no_spend(client):
    assert client.get("/api/session").json()["is_admin"] is False
    assert client.get("/api/spend").status_code == 403
    runs = client.get("/api/runs")
    detail = client.get("/api/runs/fusion")
    assert runs.status_code == detail.status_code == 200
    assert "spend" not in runs.json()[0]
    data = detail.json()
    assert "spend" not in data
    assert "spend" not in data["jobs"][0]
    assert len(data["jobs"][0]["branches"]) == 2
    assert all("spend" not in branch for branch in data["jobs"][0]["branches"])


def assert_spend(client):
    assert client.get("/api/session").json()["is_admin"] is True
    assert client.get("/api/spend").json()["total"]["cost_usd"] == 1
    assert client.get("/api/runs").json()[0]["spend"]["cost_usd"] == 1
    detail = client.get("/api/runs/fusion").json()
    assert detail["spend"]["cost_usd"] == 1
    assert detail["jobs"][0]["spend"]["cost_usd"] == 1


@pytest.mark.parametrize("admin_emails", ["", "other@berri.ai", "@berri.ai"])
def test_google_members_cannot_read_spend(client, monkeypatch, metered_run, admin_emails):
    enable(monkeypatch)
    monkeypatch.setattr(settings(), "admin_emails", admin_emails)
    monkeypatch.setattr(settings(), "admin_users", "trusted")
    identity(client)

    def forbidden(*args, **kwargs):
        pytest.fail("Member requests must not query spend")

    for name in ["dashboard", "run_costs", "job_costs"]:
        monkeypatch.setattr(spend, name, forbidden)
    assert_no_spend(client)
    # Review access and stored accounting are unaffected.
    assert client.get("/api/resources/agents").status_code == 200
    with Session() as db:
        assert sum(row.cost_usd for row in db.query(ModelUsage)) == Decimal("1")


def test_verified_email_admin_does_not_need_github_link(client, monkeypatch, metered_run):
    enable(monkeypatch)
    monkeypatch.setattr(settings(), "admin_emails", " other@berri.ai, MEMBER@BERRI.AI, ")
    identity(client, github_login="", github_id="")
    assert_spend(client)


@pytest.mark.parametrize("revocation", ["allowlist", "identity"])
def test_spend_permission_is_rechecked_for_existing_session(client, monkeypatch, metered_run, revocation):
    enable(monkeypatch)
    monkeypatch.setattr(settings(), "admin_emails", "member@berri.ai")
    identity(client)
    assert_spend(client)
    if revocation == "allowlist":
        monkeypatch.setattr(settings(), "admin_emails", "")
    else:
        with Session.begin() as db:
            db.get(SSOIdentity, "google-sub").email = "other@berri.ai"
    assert_no_spend(client)


def test_cookie_role_email_and_owner_name_cannot_override_google_identity(client, monkeypatch, metered_run):
    enable(monkeypatch)
    monkeypatch.setattr(settings(), "admin_emails", "admin@berri.ai")
    identity(client, github_login="owner")
    client.cookies.set("lr_session", signed({
        "auth": "google", "sub": "google-sub", "session_id": "test-session",
        "email": "admin@berri.ai", "login": "owner", "is_admin": True,
    }))
    assert_no_spend(client)


@pytest.mark.parametrize("login", ["owner", "legacy-admin"])
def test_non_sso_administrators_keep_spend_access(client, monkeypatch, metered_run, login):
    monkeypatch.setattr(settings(), "admin_users", "LEGACY-ADMIN")
    client.cookies.set("lr_session", signed({"login": login}))
    assert_spend(client)


def test_unauthenticated_requests_never_receive_costs(client, metered_run):
    assert client.get("/api/session").json()["is_admin"] is False
    for path in ["/api/spend", "/api/runs", "/api/runs/fusion"]:
        assert client.get(path).status_code == 401
