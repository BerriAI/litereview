"""One editable rating per person and reviewer result."""

import hashlib
import json
import time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .db import Job, ReviewFeedback, Run, Session
from .security import require_admin, require_user

router = APIRouter()


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rating: Literal["up", "down"]
    note: str = Field(default="", max_length=4000)


def identity(job_id, user):
    person = ["google", user["sub"]] if user.get("auth") == "google" else ["github", user["login"].lower()]
    return hashlib.sha256(json.dumps([job_id, person]).encode()).hexdigest()


def public(row):
    return {"rating": row.rating, "note": row.note, "updated_at": row.updated_at}


def eligible(run, job):
    return bool(run.pr > 0 and job.status in ("completed", "failed", "timed_out")
                and not run.snapshot.get("harness_check") and not run.snapshot.get("reply")
                and not job.config.get("fusion_parent_id") and not job.config.get("comparison_parent_id")
                and not job.result.get("skipped"))


def mine(db, job_ids, user):
    rows = db.scalars(select(ReviewFeedback).where(ReviewFeedback.id.in_([identity(jid, user) for jid in job_ids])))
    return {row.job_id: public(row) for row in rows}


@router.put("/api/jobs/{job_id}/feedback")
def save_feedback(job_id: str, data: FeedbackInput, user=Depends(require_user)):
    with Session.begin() as db:
        job = db.get(Job, job_id)
        run = db.get(Run, job.run_id) if job else None
        if not job or not run:
            raise HTTPException(404, "Review not found.")
        if not eligible(run, job):
            raise HTTPException(409, "Feedback is available after this reviewer finishes.")
        now = time.time()
        values = {"id": identity(job_id, user), "job_id": job_id,
                  "reporter": user.get("email") or user.get("login") or user.get("github_login", ""),
                  "rating": data.rating, "note": data.note.strip(), "created_at": now, "updated_at": now}
        insert = pg_insert if db.bind.dialect.name == "postgresql" else sqlite_insert
        db.execute(insert(ReviewFeedback).values(**values).on_conflict_do_update(
            index_elements=[ReviewFeedback.id],
            set_={k: v for k, v in values.items() if k not in ("id", "job_id", "created_at")}))
        return public(db.get(ReviewFeedback, values["id"]))


@router.get("/api/feedback", dependencies=[Depends(require_admin)])
def feedback(rating: Literal["up", "down"] | None = None, agent_id: str = "",
             limit: int = Query(30, ge=1, le=100), offset: int = Query(0, ge=0)):
    display = func.coalesce(Job.display_config, Job.config)
    stmt = select(ReviewFeedback, Run.id, Run.repo, Run.pr, Run.head,
                  display["agent_id"].as_string(), display["name"].as_string(), display["model"].as_string()
                  ).join(Job, Job.id == ReviewFeedback.job_id).join(Run, Run.id == Job.run_id)
    if agent_id:
        stmt = stmt.where(display["agent_id"].as_string() == agent_id)
    if rating:
        stmt = stmt.where(ReviewFeedback.rating == rating)
    with Session() as db:
        total = db.scalar(select(func.count()).select_from(stmt.subquery()))
        rows = db.execute(stmt.order_by(ReviewFeedback.updated_at.desc(), ReviewFeedback.id).offset(offset).limit(limit))
        items = [{"id": row.id, "job_id": row.job_id, **public(row), "reporter": row.reporter,
                  "run_id": rid, "repo": repo, "pr": pr, "head": head,
                  "agent_id": aid, "agent_name": name, "model": model}
                 for row, rid, repo, pr, head, aid, name, model in rows]
        has_more = offset + len(items) < total
        return {"items": items, "total": total, "has_more": has_more,
                "next_offset": offset + len(items) if has_more else None}
