"""Hosted previews reuse the existing agent–PR session and immutable job results."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import defer, load_only

from .db import Artifact, Job, ReviewSession, Run, job_metadata
from .settings import settings


def url(job, historical=False):
    sid = job.config.get("session_id")
    if job.config.get("output_type") != "preview" or not sid:
        return None
    return f"{settings().public_url}/previews/{sid}" + (f"?job={job.id}" if historical else "")


def validate(db, job, result):
    preview = result.get("preview")
    if job.config.get("output_type") != "preview":
        result.pop("preview", None)
        return
    if not preview:
        if result["status"] == "completed" and job.config.get("fusion_stage") != "branch":
            raise HTTPException(422, "Submit the preview document with title, summary and HTML.")
        return
    ids = set(preview["artifact_ids"])
    sources = [job.id, *db.scalars(select(Job.id).where(
        Job.run_id == job.run_id, job_metadata["fusion_parent_id"].as_string() == job.id))]
    allowed = set(db.scalars(select(Artifact.id).where(Artifact.job_id.in_(sources), Artifact.id.in_(ids))))
    if ids - allowed:
        raise HTTPException(422, "Preview media must be saved artifacts from this job or its fusion branches.")


def detail(db, sid, job_id=None):
    session = db.get(ReviewSession, sid)
    if not session:
        raise HTTPException(404, "Preview not found.")
    history = db.execute(select(Job.id.label("job_id"), Run.id.label("run_id"), Run.head,
                                Job.status, Run.created_at).join(Run, Run.id == Job.run_id).where(
        job_metadata["session_id"].as_string() == sid,
        job_metadata["output_type"].as_string() == "preview",
        job_metadata["fusion_parent_id"].as_string().is_(None),
        job_metadata["comparison_parent_id"].as_string().is_(None),
    ).order_by(Run.created_at.desc(), Run.id.desc(), Job.id)).all()
    selected = next((row for row in history if not job_id or row.job_id == job_id), None)
    if not selected:
        raise HTTPException(404, "Preview not found.")
    job = db.scalar(select(Job).where(Job.id == selected.job_id).options(defer(Job.logs)))
    run = db.scalar(select(Run).where(Run.id == selected.run_id).options(defer(Run.snapshot)))
    children = list(db.scalars(select(Job).options(load_only(Job.id, Job.status, Job.display_config, Job.result)).where(Job.run_id == run.id,
        job_metadata["fusion_parent_id"].as_string() == job.id).order_by(job_metadata["fusion_index"].as_integer())))
    media = db.execute(select(Artifact.id, Artifact.name, Artifact.mime).where(
        Artifact.job_id.in_([job.id, *[c.id for c in children]]))).all()
    return {
        "session_id": sid, "job_id": job.id, "run_id": run.id, "repo": run.repo, "pr": run.pr,
        "head": run.head, "agent": job.config["name"], "status": job.status,
        "preview": job.result.get("preview"), "limitations": job.result.get("limitations", []),
        "checks": job.result.get("checks", []), "summary": job.result.get("summary", ""),
        "artifacts": [dict(a._mapping) for a in media],
        "branches": [{"name": (c.display_config or {}).get("fusion_role", ""), "status": c.status,
                      "skipped": bool(c.result.get("skipped"))}
                     for c in children],
        "history": [dict(row._mapping) for row in history],
        "is_latest": job.id == history[0].job_id, "url": url(job),
    }, run
