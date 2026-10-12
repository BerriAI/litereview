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
