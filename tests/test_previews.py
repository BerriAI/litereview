from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app import fusion, github
from app.db import Artifact, Job, ReviewSession, Run, Session
from app.security import digest
from app.verdict import verdict
from runner.bridge import Bridge
from runner.tools import Toolset, definitions

SID = "a" * 64
HEAD = "b" * 40
DOC = {"title": "Keep the selected provider", "summary": "Requests retain their provider when the first attempt fails.",
       "html": "<p>The fallback now uses the selected provider.</p>", "artifact_ids": []}
AUTH = {"authorization": "Bearer preview-worker"}


def seed(status="running", **config):
    with Session.begin() as db:
        db.add(ReviewSession(id=SID, repo="berriai/example", pr=12, agent_id="preview"))
        db.add(Run(id="preview-run", delivery="preview", repo="BerriAI/example", pr=12, actor="moe", head=HEAD,
                   base="c" * 40, installation=123, snapshot={"title": "Fix", "description": "", "diff": "",
                   "repository": {"id": "repo", "name": "BerriAI/example"}}, status="running"))
        db.add(Job(id="preview-job", run_id="preview-run", status=status, token_hash=digest("preview-worker"),
                   config={"session_id": SID, "agent_id": "preview", "name": "preview", "instance": 1,
                           "target_kind": "agent", "output_type": "preview", "capabilities": ["terminal", "computer"], **config}))


def test_preview_requires_auth_and_reports_current_head_and_history(owner, monkeypatch):
    seed("completed")
    with Session.begin() as db:
        db.get(Job, "preview-job").result = {"preview": DOC, "limitations": [], "checks": []}
    request = AsyncMock(return_value=httpx.Response(200, json={"head": {"sha": HEAD}}))
    monkeypatch.setattr(github, "repo_request", request)
    payload = owner.get(f"/api/previews/{SID}").json()
    assert payload["preview"] == DOC
    assert payload["stale"] is False
    assert payload["is_latest"] is True
    assert payload["history"][0]["job_id"] == "preview-job"
    request.return_value = httpx.Response(200, json={"head": {"sha": "new"}})
    assert owner.get(f"/api/previews/{SID}").json()["stale"] is True
    request.side_effect = httpx.ConnectError("offline")
    assert owner.get(f"/api/previews/{SID}").json()["stale"] is None
    assert owner.get(f"/api/previews/{SID}?job=unrelated").status_code == 404
    owner.post("/api/logout")
    assert owner.get(f"/api/previews/{SID}").status_code == 401


def test_finished_preview_accepts_only_own_or_branch_media_and_can_retry(owner):
    seed()
    with Session.begin() as db:
        db.add(Job(id="child", run_id="preview-run", config={"fusion_parent_id": "preview-job"}))
        db.add(Artifact(id="own", job_id="child", name="Demo", content=b"video", mime="video/mp4"))
        db.add(Artifact(id="other", job_id="unrelated", name="Private", content=b"png"))
    result = {"status": "completed", "summary": "A clearer fallback.", "findings": [], "limitations": []}
    path = "/api/internal/jobs/preview-job/finish"
    assert owner.post(path, headers=AUTH, json=result).status_code == 422
    preview = {**DOC, "html": '<video controls src="artifact:other"></video>', "artifact_ids": ["other"]}
    assert owner.post(path, headers=AUTH, json={**result, "preview": preview}).status_code == 422
    preview["artifact_ids"] = ["own"]
    assert owner.post(path, headers=AUTH, json={**result, "preview": preview}).status_code == 200
    detail = owner.get("/api/runs/preview-run?compact=true").json()["jobs"][0]
    assert detail["config"]["output_type"] == "preview"
    assert detail["preview_url"].endswith(f"/previews/{SID}?job=preview-job")
    assert detail["verdict"] is None


def test_video_upload_playback_ranges_and_auth(owner, monkeypatch):
    seed()
    path = "/api/internal/jobs/preview-job/artifact/video"
    data = b"\x00\x00\x00\x18ftypmp42" + bytes(range(40))
    assert owner.post(path, content=data).status_code == 401
    assert owner.post(path, headers=AUTH, content=b"<script>no</script>").status_code == 400
    response = owner.post(path, headers={**AUTH, "x-artifact-name": "A%20walkthrough"}, content=data)
    assert response.status_code == 200
    assert owner.post(path, headers=AUTH, content=data).json()["id"] == response.json()["id"]
    artifact = "/api/artifacts/" + response.json()["id"]
    full = owner.get(artifact)
    assert full.content == data
    assert full.headers["content-type"] == "video/mp4"
    assert "no-store" in full.headers["cache-control"]
    partial = owner.get(artifact, headers={"range": "bytes=4-7"})
    assert partial.status_code == 206 and partial.content == b"ftyp"
    assert owner.get(artifact, headers={"range": "bytes=-3"}).content == data[-3:]
    assert owner.get(artifact, headers={"range": "bytes=999-"}).status_code == 416
    assert owner.get(artifact, headers={"range": "bytes=0-1,4-5"}).status_code == 416
    assert owner.get(artifact + "/metadata").json()["name"] == "A walkthrough"
    from runner import recording
    monkeypatch.setattr(recording, "MAX_VIDEO_BYTES", 10)
    assert owner.post(path, headers=AUTH, content=data).status_code == 413
    with Session.begin() as db:
        job = db.get(Job, "preview-job")
        job.config = {**job.config, "capabilities": ["terminal"]}
    assert owner.post(path, headers=AUTH, content=data).status_code == 403
    owner.post("/api/logout")
    assert owner.get(artifact).status_code == 401


def test_fusion_preview_uses_evidence_and_supplies_media(owner):
    with Session.begin() as db:
        parent = fusion.enqueue(db, "run", {"name": "preview", "instructions": "Explain", "output_type": "preview",
            "fusion": {"branches": [{"name": "Demo", "connection_id": "c", "model": "m"}]}})
        db.flush()
        child = db.scalar(select(Job).where(Job.config["fusion_parent_id"].as_string() == parent.id))
        db.add(Artifact(id="demo", job_id=child.id, name="Demo", mime="video/mp4", content=b"mp4"))
        db.flush()
        assert fusion.reports(db, parent)[0]["artifacts"][0]["id"] == "demo"
        assert "Assemble" in parent.config["instructions"]
        assert "Reject" not in parent.config["instructions"]


@pytest.mark.parametrize("severity,score", [("critical", 1), ("high", 2), ("medium", 3), ("low", 4)])
def test_server_score_uses_actionable_findings_not_model_caution(severity, score):
    job = SimpleNamespace(status="completed", config={"show_score": True},
                          result={"summary": "4/5 out of caution", "findings": []})
    assert verdict(job)["score"] == 5
    job.result["findings"] = [{"severity": severity}]
    assert verdict(job)["score"] == score
    assert verdict(job, {"0": {"status": "withdrawn"}})["score"] == 5
    job.result["limitations"] = ["Required evidence unavailable"]
    assert verdict(job)["score"] is None
    job.result = {"checks": [{"required": True, "status": "failed"}]}
    assert verdict(job)["state"] == "blocked"
    job.result = {"skipped": True}
    assert verdict(job)["score"] is None


@pytest.mark.asyncio
async def test_rejected_finish_can_be_corrected_without_stopping_harness():
    cfg = {"capabilities": ["terminal"], "output_type": "preview"}
    relay = AsyncMock(side_effect=[RuntimeError("Invalid artifact"), {"ok": True}])
    tools = Toolset({"config": cfg}, relay)
    bridge = Bridge(tools, relay, cfg)
    result = {"summary": "Preview", "findings": [], "limitations": [], "preview": DOC}
    assert (await bridge.call("lr_finish_review", result))["isError"]
    assert not bridge.finished.is_set()
    assert not (await bridge.call("lr_finish_review", result))["isError"]
    assert bridge.finished.is_set()
    assert "recording" not in {d["function"]["name"] for d in definitions(cfg)}
    assert "recording" in {d["function"]["name"] for d in definitions({**cfg, "capabilities": ["browser"]})}


def test_freeform_preview_is_isolated_and_only_uses_its_own_media(owner):
    seed("completed")
    document = {**DOC, "artifact_ids": ["image"], "html": '''
      <style>.choices:has(#stream:checked) .normal { display:none }</style>
      <section class="choices"><h2>Choose a path</h2><label><input type="radio" id="stream">Streaming</label>
      <p class="normal">One response</p><img src="artifact:image" onerror="steal()"></section>
      <script>steal()</script><iframe src="https://evil.test"></iframe>
      <meta http-equiv="refresh" content="0;url=https://evil.test">
      <img src="https://evil.test/pixel"><form action="https://evil.test"><input type="password"></form>
      <a href="javascript:steal()">Unsafe</a><a href="https://github.com/BerriAI/example/pull/12">Source</a>
    '''}
    with Session.begin() as db:
        db.get(Job, "preview-job").result = {"preview": document}
        db.add(Artifact(id="image", job_id="preview-job", name="demo", content=b"png"))
    response = owner.get(f"/api/previews/{SID}/document")
    assert response.status_code == 200
    assert 'type="radio"' in response.text and ".choices:has" in response.text
    assert 'src="http://testserver/api/artifacts/image"' in response.text
    assert "steal()" not in response.text and "evil.test" not in response.text
    assert 'type="password"' not in response.text
    assert 'target="_blank" rel="noopener noreferrer"' in response.text
    assert "allow-scripts" not in response.headers["content-security-policy"]
    assert "script-src 'none'" in response.headers["content-security-policy"]
    assert "default-src 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "SAMEORIGIN"
    owner.post("/api/logout")
    assert owner.get(f"/api/previews/{SID}/document").status_code == 401


def test_incremental_score_requires_rechecking_old_findings(owner):
    from app.schemas import ReviewResult
    from app.verdict import enforce_history
    previous = {"id": "old:0", "status": "open", "finding": {"title": "Dropped request", "severity": "high"}}
    job = SimpleNamespace(status="completed", config={"show_score": True, "finding_history": [previous]},
                          result={"findings": [], "limitations": []})
    assert verdict(job)["score"] is None
    enforce_history(job, job.result)
    assert "Dropped request" in job.result["limitations"][0]
    job.result = {"findings": [], "finding_assessments": [{"id": "old:0", "status": "open", "evidence": []}]}
    assert verdict(job)["score"] == 2
    job.result["finding_assessments"][0] = {"id": "old:0", "status": "fixed", "evidence": ["Current handler retains the request; regression test passes."]}
    assert verdict(job)["score"] == 5
    with pytest.raises(ValueError):
        ReviewResult.model_validate({"status": "completed", "summary": "Done", "finding_assessments": [{"id": "old:0", "status": "fixed", "evidence": []}]})
    seed("completed", output_type="review", show_score=True, finding_history=[previous])
    with Session.begin() as db:
        db.get(Job, "preview-job").result = job.result
    assert owner.get("/api/runs/preview-run?compact=true").json()["jobs"][0]["verdict"]["score"] == 5
    with Session.begin() as db:
        db.get(Job, "preview-job").result = {"findings": [], "limitations": []}
    assert owner.get("/api/runs/preview-run?compact=true").json()["jobs"][0]["verdict"]["score"] is None


@pytest.mark.asyncio
async def test_recording_keeps_finalized_file_for_upload_retry(tmp_path):
    from runner.recording import Recording
    recorder = Recording()
    recorder.path = tmp_path / "demo.mp4"
    recorder.path.write_bytes(b"video")
    recorder.process = SimpleNamespace(returncode=0)
    relay = SimpleNamespace(upload_video=AsyncMock(side_effect=[RuntimeError("network"), {"id": "video-id"}]))
    with pytest.raises(RuntimeError, match="network"):
        await recorder.stop(relay)
    assert recorder.path.exists()
    assert (await recorder.stop(relay))["artifact_id"] == "video-id"
    assert recorder.process is None
