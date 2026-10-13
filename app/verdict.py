"""One readiness verdict for the UI, GitHub and MCP. Models supply evidence, not grades."""


def open_history(job):
    return [f for f in job.config.get("finding_history", []) if f.get("status", "open") == "open"]


def enforce_history(job, result):
    if not job.config.get("show_score") or job.config.get("task") == "reply" or job.config.get("output_type") == "preview" or job.config.get("fusion_parent_id"):
        return
    assessments = {a["id"]: a for a in result.get("finding_assessments", [])}
    for finding in open_history(job):
        assessment = assessments.get(finding["id"], {})
        if assessment.get("status", "uncertain") == "uncertain":
            result["limitations"].append(f"Earlier finding needs verification: {finding['finding']['title']} ({finding['id']}).")


def verdict(job, finding_states=None):
    if not job.config.get("show_score") or job.config.get("output_type") == "preview" or job.config.get("task") == "reply":
        return None
    result = job.result or {}
    state = "running" if job.status not in {"completed", "failed", "timed_out", "superseded"} else "incomplete"
    if result.get("skipped"):
        state = "skipped"
    elif job.status == "superseded":
        state = "superseded"
    elif job.status == "completed" and not result.get("limitations") and not any(
        c.get("required") and c.get("status") == "not_run" for c in result.get("checks", [])
    ):
        states = finding_states or {}
        findings = [f for i, f in enumerate(result.get("findings", []))
                    if states.get(str(i), {}).get("status") != "withdrawn"]
        assessments = {a["id"]: a for a in result.get("finding_assessments", [])}
        for previous in open_history(job):
            assessment = assessments.get(previous["id"], {})
            if assessment.get("status", "uncertain") == "uncertain":
                return {"state": "incomplete", "score": None}
            if assessment["status"] == "open":
                findings.append(previous["finding"])
        failed = any(c.get("required") and c.get("status") == "failed" for c in result.get("checks", []))
        score = min([{"critical": 1, "high": 2, "medium": 3, "low": 4}.get(f.get("severity"), 3)
                     for f in findings] + ([3] if failed else [5]))
        return {"state": "ready" if score == 5 else "blocked", "score": score,
                "findings": len(findings), "required_check_failed": failed}
    return {"state": state, "score": None}


def headline(value):
    return {"ready": "Ready for human review", "blocked": "Changes needed", "incomplete": "Review incomplete",
            "running": "Review in progress", "skipped": "Review skipped", "superseded": "Review superseded"}[value["state"]]
