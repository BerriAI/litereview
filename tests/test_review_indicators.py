"""GitHub must distinguish finished execution from a review without issues."""
from types import SimpleNamespace

import pytest

from app import reviews, verdict

FINDING = {
    "title": "Revalidate attachments when switching endpoints",
    "severity": "medium",
    "explanation": "A PDF attached in Chat reaches the transcription helper after switching endpoints.",
}


def render(*, status="completed", findings=(), checks=(), limitations=(), decisions=None,
           show_score=False, **config):
    run = SimpleNamespace(id="run", status=status, head="a" * 40, repo="BerriAI/litellm", pr=46934)
    job = SimpleNamespace(
        id="job", status=status,
        config={"name": "gauntlet", "group": "gauntlet", "target_kind": "agent",
                "instance": 1, "show_score": show_score, **config},
        result={"summary": "", "findings": list(findings), "checks": list(checks),
                "limitations": list(limitations)},
    )
    return job, reviews.render_comment(run, [job], agent_comment=True, finding_decisions=decisions)


@pytest.mark.parametrize("show_score", [False, True])
def test_confirmed_finding_never_gets_a_clean_checkmark(show_score):
    job, body = render(findings=[FINDING], show_score=show_score)
    assert "### ⚠️ gauntlet" in body
    assert "✅" not in body and "No issues found" not in body
    assert FINDING["title"] in body
    assert ("Changes needed" in body) == show_score
    assert (verdict.verdict(job) is not None) == show_score


@pytest.mark.parametrize("required", [False, True])
@pytest.mark.parametrize("status", ["failed", "not_run"])
def test_checks_without_findings_cannot_look_clean(required, status):
    _, body = render(checks=[{"name": "Reproducer", "status": status,
                              "required": required, "details": "See captured execution evidence."}])
    assert "### ⚠️ gauntlet" in body
    assert "✅" not in body and "No issues found" not in body
    if status == "failed":
        assert "Completed · Checks failed" in body
    elif required:
        assert "Review incomplete" in body
    else:
        assert "Some checks not run" in body


def test_46934_failed_reproducer_stays_visible_after_finding_withdrawal():
    # Actual shape of the unscored gauntlet review, including its later withdrawal.
    _, body = render(
        findings=[FINDING],
        checks=[{"name": "Decisive attachment reproducer", "status": "failed", "required": True,
                 "details": "Expected transcription helper not to be called; called once with a PDF."},
                {"name": "Live browser end-to-end verification", "status": "not_run", "required": False,
                 "details": "No configured browser capability or live authenticated proxy stack."}],
        decisions={"0": {"status": "withdrawn", "reason": "Verified fixed on a later head."}},
    )
    assert "### ⚠️ gauntlet" in body and "✅" not in body
    assert "Withdrawn after verification" in body
    assert "Completed · Checks failed" in body
    assert "Verified fixed on a later head" in body


def test_withdrawn_finding_alone_does_not_leave_warning():
    _, body = render(findings=[FINDING], decisions={"0": {"status": "withdrawn", "reason": "Verified."}})
    assert "### ✅ gauntlet" in body
    assert "Withdrawn after verification" in body


@pytest.mark.parametrize("status,limitations", [("failed", []), ("timed_out", []),
                                               ("completed", ["Could not inspect a required file."])])
def test_incomplete_execution_never_gets_a_clean_checkmark(status, limitations):
    _, body = render(status=status, limitations=limitations)
    assert "### ⚠️ gauntlet" in body
    assert "Review incomplete" in body and "No issues found" not in body


def test_unverified_previous_finding_prevents_clean_header_without_scoring():
    _, body = render(finding_history=[{"id": "old", "status": "open", "finding": FINDING}])
    assert "### ⚠️ gauntlet" in body
    assert "Review incomplete" in body and "No issues found" not in body


@pytest.mark.parametrize("config", [{"output_type": "preview"}, {"task": "reply"}])
def test_nonreview_completion_uses_neutral_icon(config):
    _, body = render(**config)
    assert "### 📝 gauntlet" in body
    assert "✅" not in body and "Ready for human review" not in body


def test_clean_completed_review_keeps_checkmark():
    _, body = render(checks=[{"name": "Reproducer", "status": "passed", "required": True,
                              "details": "Supported requests passed; negative control failed."}])
    assert "### ✅ gauntlet" in body
    assert "No issues found" in body
