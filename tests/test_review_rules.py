import pytest
from test_reviews import setup

from app.review_rules import enforce_checks
from runner.instructions import agent_instructions
from runner.timing import ReviewClock


def test_rule_crud_preserves_base_prompt_and_existing_rules(owner):
    _, _, agent, _, _ = setup(owner)
    path = "/api/agents/" + agent["id"] + "/rules"
    rule = owner.post(path, json={"name": "E2E", "instructions": "Verify real proof."}).json()["rules"][0]
    second = owner.post(path, json={"name": "Compatibility", "instructions": "Verify compatibility."}).json()["rules"][1]
    edited = owner.patch(path + "/" + rule["id"], json={"enabled": False}).json()
    assert edited["rules"][1] == second and not edited["rules"][0]["enabled"]
    assert owner.patch(path + "/" + rule["id"], json={"instructions": None}).status_code == 422
    owner.delete(path + "/" + rule["id"]).raise_for_status()
    saved = owner.get("/api/resources/agents/" + agent["id"]).json()
    assert saved["instructions"] == agent["instructions"] and saved["rules"] == [second]


@pytest.mark.parametrize("status", [None, "not_run", "passed", "failed"])
def test_required_rules_cannot_be_omitted_or_silently_waived(status):
    rule = {"id": "e2e", "name": "E2E", "instructions": "Real proof", "required": True, "enabled": True}
    result = {"checks": [] if status is None else [{"rule_id": "e2e", "name": "E2E", "status": status,
              "required": False, "details": "Observed evidence"}], "limitations": []}
    enforce_checks({"rules": [rule]}, result)
    assert result["checks"][0]["required"]
    assert bool(result["limitations"]) == (status in (None, "not_run"))
    enforce_checks({"rules": [rule]}, result)
    assert len(result["checks"]) == 1


def test_only_enabled_rules_reach_review_prompt_and_replies_keep_focus():
    config = {"instructions": "Base task", "rules": [
        {"id": "yes", "name": "Active", "instructions": "Verify real E2E", "enabled": True, "required": True},
        {"id": "no", "name": "Disabled", "instructions": "Should not run", "enabled": False},
    ]}
    prompt = agent_instructions(config, ReviewClock(0), "Attached base skill")
    assert "Verify real E2E" in prompt and "Should not run" not in prompt
    assert "Base task" in prompt and "Attached base skill" in prompt and "rule_id: yes" in prompt
    result = {"checks": [], "limitations": []}
    enforce_checks({**config, "task": "reply"}, result)
    assert result == {"checks": [], "limitations": []}


@pytest.mark.parametrize("outcome", ["passed", "not_run"])
def test_missing_rule_id_can_be_corrected_before_worker_finishes(owner, outcome):
    from test_previews import AUTH, seed

    from app.db import Job, Session

    seed(output_type="review", supports_finish_retry=True,
         rules=[{"id": "e2e", "name": "E2E", "required": True}])
    result = {"status": "completed", "summary": "E2E checked", "findings": [], "limitations": [],
              "checks": [{"name": "e2e: translation coverage", "status": outcome, "required": True,
                          "details": "Verified coverage" if outcome == "passed" else "Execution evidence unavailable"}]}
    path = "/api/internal/jobs/preview-job/finish"
    response = owner.post(path, headers=AUTH, json=result)
    assert response.status_code == 422 and "rule_id" in response.json()["detail"]
    with Session() as db:
        assert db.get(Job, "preview-job").status == "running"
        assert db.get(Job, "preview-job").result == {}
    result["checks"][0]["rule_id"] = "e2e"
    assert owner.post(path, headers=AUTH, json=result).status_code == 200
    with Session() as db:
        saved = db.get(Job, "preview-job").result
        assert saved["checks"][0]["status"] == outcome
        assert bool(saved["limitations"]) == (outcome == "not_run")


def test_older_workers_keep_their_completion_contract_during_rollout(owner):
    from test_previews import AUTH, seed

    from app.db import Job, Session

    seed(output_type="review", rules=[{"id": "e2e", "name": "E2E", "required": True}])
    result = {"status": "completed", "summary": "Review result", "findings": [], "checks": [], "limitations": []}
    assert owner.post("/api/internal/jobs/preview-job/finish", headers=AUTH, json=result).status_code == 200
    with Session() as db:
        saved = db.get(Job, "preview-job").result
        assert saved["checks"][0]["status"] == "not_run"
        assert saved["limitations"]
