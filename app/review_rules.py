"""Keep required saved rules visible when a worker omits their evidence."""

import time

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .db import Resource, Session
from .schemas import ReviewRuleInstruction
from .security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class RulePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1)
    instructions: str | None = Field(default=None, min_length=1)
    enabled: bool | None = None
    required: bool | None = None


def change_rule(agent_id, rule_id, data):
    with Session.begin() as db:
        agent = db.get(Resource, agent_id, with_for_update=True)
        if not agent or agent.kind != "agents":
            raise HTTPException(404, "Agent not found. List agents to find its ID.")
        rules = list(agent.data.get("rules", []))
        existing = next((r for r in rules if r["id"] == rule_id), None)
        if rule_id and not existing:
            raise HTTPException(404, "Rule not found. Read this agent's rules again.")
        if existing:
            index = rules.index(existing)
            if data is None:
                rules.pop(index)
            else:
                rules[index] = ReviewRuleInstruction(**{**existing, **data, "id": rule_id}).model_dump()
        else:
            rule = ReviewRuleInstruction(**data).model_dump()
            if any(r["id"] == rule["id"] for r in rules):
                raise HTTPException(409, "A rule with this ID already exists. Use update_rule.")
            rules.append(rule)
        agent.data = {**agent.data, "rules": rules}
        agent.updated_at = time.time()
        return {"agent_id": agent.id, "rules": rules, "updated_at": agent.updated_at}


@router.post("/api/agents/{agent_id}/rules")
def add_rule(agent_id: str, rule: ReviewRuleInstruction):
    return change_rule(agent_id, None, rule.model_dump())


@router.patch("/api/agents/{agent_id}/rules/{rule_id}")
def update_rule(agent_id: str, rule_id: str, changes: RulePatch):
    if any(value is None for value in changes.model_dump(exclude_unset=True).values()):
        raise HTTPException(422, "Rule fields cannot be null. Delete the rule to remove it.")
    return change_rule(agent_id, rule_id, changes.model_dump(exclude_unset=True))


@router.delete("/api/agents/{agent_id}/rules/{rule_id}")
def delete_rule(agent_id: str, rule_id: str):
    return change_rule(agent_id, rule_id, None)


def validate_checks(config, result):
    if not config.get("supports_finish_retry") or config.get("task") == "reply" or result["status"] != "completed":
        return
    reported = {check.get("rule_id") for check in result["checks"]}
    missing = [rule["id"] for rule in config.get("rules", [])
               if rule.get("enabled", True) and rule.get("required", True) and rule["id"] not in reported]
    if missing:
        raise HTTPException(422,
            "Missing structured checks for required rules: " + ", ".join(missing) + ". "
            "Add a checks entry with each exact rule_id, status and evidence in details, then submit again. "
            "A rule mentioned in summary or check name does not supply rule_id. "
            "If you could not verify a rule, use status=not_run and explain the actual gap; do not invent a pass.")
    required = {rule["id"] for rule in config.get("rules", [])
                if rule.get("enabled", True) and rule.get("required", True)}
    empty = [check["rule_id"] for check in result["checks"]
             if check.get("rule_id") in required and not check.get("details", "").strip()]
    if empty:
        raise HTTPException(422,
            "Required checks need evidence or a specific verification gap in details: "
            + ", ".join(dict.fromkeys(empty)) + ". Correct these checks and submit again; do not invent a pass.")


def enforce_checks(config, result):
    if config.get("task") == "reply":
        return  # A finding reply assesses one finding, not every PR-wide rule.
    for rule in config.get("rules", []):
        if not rule.get("enabled", True):
            continue
        checks = [c for c in result["checks"] if c.get("rule_id") == rule["id"]]
        if not checks:
            checks = [{"rule_id": rule["id"], "name": rule["name"], "status": "not_run",
                       "details": "The reviewer did not provide evidence for this saved rule."}]
            result["checks"].extend(checks)
        for check in checks:
            check["required"] = rule.get("required", True)
            if check["required"] and not check.get("details", "").strip():
                # Older workers cannot retry submission. Preserve an observed failure,
                # but never treat an unsupported pass as verified coverage.
                if check["status"] == "passed":
                    check["status"] = "not_run"
                check["details"] = "The reviewer provided no evidence or verification explanation for this saved rule."
                limitation = f"{rule['name']}: {check['details']}"
                if limitation not in result["limitations"]:
                    result["limitations"].append(limitation)
            if check["required"] and check["status"] == "not_run":
                limitation = f"{rule['name']}: {check['details']}"
                if limitation not in result["limitations"]:
                    result["limitations"].append(limitation)
