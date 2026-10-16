"""Contract regressions from the October 16 audit, not model-recall measurements."""

import copy
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from test_previews import AUTH, seed

from app.db import Job, Session
from app.verdict import verdict
from runner.bridge import Bridge
from runner.repository_guidance import guidance_index

FIXTURES = Path(__file__).resolve().parents[1] / "evals/review_quality"


def audit_case():
    rules = json.loads((FIXTURES / "rules.json").read_text())["rules"]
    result = json.loads((FIXTURES / "false_clean_46839.json").read_text())["result"]
    return rules, result


@pytest.mark.parametrize("retry", [True, False])
def test_recorded_prose_only_blocker_cannot_finish_ready(owner, retry):
    rules, result = audit_case()
    seed(output_type="review", show_score=True, supports_finish_retry=retry, rules=rules)
    response = owner.post("/api/internal/jobs/preview-job/finish", headers=AUTH, json=result)
    with Session() as db:
        job = db.get(Job, "preview-job")
        if retry:
            assert response.status_code == 422
            assert "compatibility-notice" in response.json()["detail"]
            assert job.status == "running" and not job.result
        else:
            assert response.status_code == 200
            assert verdict(job) == {"state": "incomplete", "score": None}


@pytest.mark.parametrize("compatible,expected", [(False, "blocked"), (True, "ready")])
def test_compatibility_failure_blocks_even_without_inline_findings(owner, compatible, expected):
    rules, result = audit_case()
    result = copy.deepcopy(result)
    result["summary"] = "No correctness findings." if compatible else "Breaking title marker is missing."
    result["checks"] += [
        {"rule_id": "compatibility-notice", "name": "Compatibility requirements", "required": False,
         "status": "passed" if compatible else "failed",
         "details": "200 to 400 is documented with impact/migration; "
                    + ("fix(proxy)!: is present." if compatible else "title lacks fix(proxy)!:.")},
        {"rule_id": "behavioral-coverage", "name": "Changed behavior", "required": True,
         "status": "passed", "details": "Traced the guard, both callers, and the 58 executed tests; "
                                          "compatibility requirement is represented separately."},
    ]
    seed(output_type="review", show_score=True, supports_finish_retry=True, rules=rules)
    response = owner.post("/api/internal/jobs/preview-job/finish", headers=AUTH, json=result)
    assert response.status_code == 200
    with Session() as db:
        job = db.get(Job, "preview-job")
        assert verdict(job)["state"] == expected
        assert verdict(job)["score"] == (5 if compatible else 3)
        assert job.result["checks"][-2]["required"] is True


@pytest.mark.parametrize("retry", [True, False])
@pytest.mark.parametrize("status", ["passed", "failed", "not_run"])
def test_required_rule_needs_evidence_or_specific_gap(owner, retry, status):
    rule = {"id": "proof", "name": "Proof", "required": True}
    result = {"status": "completed", "summary": "Done", "findings": [], "limitations": [],
              "checks": [{"rule_id": "proof", "name": "Proof", "status": status,
                          "required": False, "details": " \n "}]}
    seed(output_type="review", show_score=True, supports_finish_retry=retry, rules=[rule])
    response = owner.post("/api/internal/jobs/preview-job/finish", headers=AUTH, json=result)
    with Session() as db:
        job = db.get(Job, "preview-job")
        if retry:
            assert response.status_code == 422 and "details" in response.json()["detail"]
            assert job.status == "running"
        else:
            assert response.status_code == 200
            assert verdict(job)["state"] == "incomplete"
            assert job.result["checks"][0]["status"] == ("not_run" if status == "passed" else status)


def test_guidance_index_covers_tests_and_callers_without_following_symlinks(tmp_path):
    expected = ["AGENTS.md", "CODING_STANDARDS.md", "docs/CONTRIBUTING.md",
                "src/CLAUDE.md", "tests/unit/AGENTS.override.md"]
    for name in expected + [".git/AGENTS.md", "node_modules/package/CLAUDE.md", ".venv/AGENTS.md"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Repository standards evidence")
    (tmp_path / "linked").symlink_to(tmp_path / "src", target_is_directory=True)
    (tmp_path / "CONTRIBUTING.md").symlink_to(tmp_path / "AGENTS.md")
    assert guidance_index(tmp_path) == expected
    assert guidance_index(tmp_path / "missing") == []


async def test_finish_tool_rejects_omitted_rules_before_accepting_result():
    rules, result = audit_case()
    relay = AsyncMock()
    bridge = Bridge(AsyncMock(), relay, {"capabilities": [], "rules": rules})
    submitted = {k: v for k, v in result.items() if k in {"summary", "findings", "checks", "limitations"}}
    # Stored API results add optional null fields; the original tool submission omitted them.
    submitted["checks"] = [{k: v for k, v in check.items() if v is not None} for check in submitted["checks"]]
    response = await bridge.call("lr_finish_review", submitted)
    assert response["isError"] and not bridge.finished.is_set()
    relay.assert_not_called()
    submitted["checks"] += [
        {"rule_id": r["id"], "name": r["name"], "status": "not_run", "required": True,
         "details": "The archived result did not establish this requirement."} for r in rules
    ]
    response = await bridge.call("lr_finish_review", submitted)
    assert not response["isError"] and bridge.finished.is_set(), response
    assert relay.call_args.args[0] == "finish"
