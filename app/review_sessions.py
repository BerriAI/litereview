"""One durable queue per repository, pull request and reviewer."""

import hashlib
import json
import time
from collections import defaultdict
from contextlib import contextmanager

from sqlalchemy import select, text

from . import fusion, github
from .db import Job, ReviewFinding, ReviewSession, Run, Session

TERMINAL = {"completed", "failed", "timed_out", "superseded"}


@contextmanager
def transaction(repo, pr):
    # Lock before reading: this also protects a session's very first insertion.
    with Session() as db:
        if db.bind.dialect.name == "postgresql":
            key = int.from_bytes(hashlib.sha256(f"review:{repo.lower()}:{pr}".encode()).digest()[:8], signed=True)
            db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})
        else:
            db.execute(text("BEGIN IMMEDIATE"))
        yield db
        db.commit()


def session_id(repo, pr, agent_id):
    return hashlib.sha256(json.dumps([repo.lower(), pr, agent_id]).encode()).hexdigest()


def fingerprint(cfg):
    excluded = {"group", "instance", "run_order", "target_kind", "target_id", "review_mode"}
    return hashlib.sha256(json.dumps({k: v for k, v in cfg.items() if k not in excluded},
                                    sort_keys=True).encode()).hexdigest()


def jobs_for(db, session, run_id):
    return list(db.scalars(select(Job).where(Job.run_id == run_id,
                                            Job.config["agent_id"].as_string() == session.agent_id)))


def refresh(db, session):
    """Release completed work and select the next queued task, including replies."""
    if session.active_run_id:
        jobs = jobs_for(db, session, session.active_run_id)
        if any(j.status not in TERMINAL for j in jobs):
            return
        run = db.get(Run, session.active_run_id)
        if run and run.snapshot.get("reply") and run.published < 2:
            return  # Publish the decision before the next task reads finding history.
        primary = [j for j in jobs if not j.config.get("fusion_parent_id")
                   and not j.config.get("comparison_parent_id")]
        for job in primary:
            for index in range(len(job.result.get("findings", []))):
                identity = f"{job.id}:{index}"
                if not db.get(ReviewFinding, identity):
                    db.add(ReviewFinding(id=identity, session_id=session.id, job_id=job.id, position=index))
        if (run and not run.snapshot.get("reply") and primary
                and all(j.status == "completed" and not j.result.get("limitations") for j in primary)):
            session.baseline_run_id = run.id
            session.fingerprint = primary[0].config.get("review_fingerprint", "")
        session.active_run_id = None
    next_job = db.scalar(select(Job).join(Run, Run.id == Job.run_id).where(
        Job.config["session_id"].as_string() == session.id, Job.status == "blocked"
    ).order_by(Run.created_at, Run.id, Job.id))
    if next_job:
        session.active_run_id = next_job.run_id
        if session.pending_run_id == next_job.run_id:
            session.pending_run_id = None
        for job in jobs_for(db, session, next_job.run_id):
            if job.status == "blocked":
                job.status = "waiting" if job.config.get("fusion_stage") == "aggregator" else "queued"


def get_session(db, run, agent_id):
    key = session_id(run.repo, run.pr, agent_id)
    session = db.get(ReviewSession, key)
    if not session:
        session = ReviewSession(id=key, repo=run.repo.lower(), pr=run.pr, agent_id=agent_id)
        db.add(session)
        db.flush()
        # Adopt pre-upgrade work so a deploy does not bypass duplicate protection.
        previous = db.scalar(select(Run).join(Job, Job.run_id == Run.id).where(
            Run.repo == run.repo, Run.pr == run.pr, Run.id != run.id,
            Job.config["agent_id"].as_string() == agent_id,
            Job.status.not_in(TERMINAL), Run.snapshot["harness_check"].as_boolean().is_not(True)
        ).order_by(Run.created_at))
        if previous:
            session.active_run_id = previous.id
    refresh(db, session)
    return session


def enqueue(db, run, configurations):
    """Called inside transaction() by both manual and automatic triggers."""
    groups = defaultdict(list)
    for position, cfg in enumerate(configurations):
        groups[cfg["agent_id"]].append({**cfg, "run_order": position})
    notices, created = [], False
    db.add(run)
    db.flush()
    for agent_id, configs in groups.items():
        session = get_session(db, run, agent_id)
        signature = fingerprint(configs[0])
        reply = bool(run.snapshot.get("reply"))
        if not reply:
            for existing_id in (session.active_run_id, session.pending_run_id):
                existing = db.get(Run, existing_id) if existing_id else None
                existing_jobs = jobs_for(db, session, existing_id) if existing else []
                if (existing and not existing.snapshot.get("reply") and existing.head == run.head
                        and existing.base == run.base and existing_jobs
                        and existing_jobs[0].config.get("review_fingerprint", signature) == signature):
                    notices.append({"name": configs[0]["name"], "run_id": existing.id,
                                    "head": existing.head, "message": "Already requested"})
                    break
            else:
                existing = None
            if existing:
                continue
            pending = db.get(Run, session.pending_run_id) if session.pending_run_id else None
            active = db.get(Run, session.active_run_id) if session.active_run_id else None
            newer = max((r for r in (active, pending) if r and not r.snapshot.get("reply")),
                        key=lambda r: r.created_at, default=None)
            if newer and newer.created_at > run.created_at:
                notices.append({"name": configs[0]["name"], "run_id": newer.id,
                                "head": newer.head, "message": "Newer request already queued"})
                continue
            if session.active_run_id and session.pending_run_id:
                for job in jobs_for(db, session, session.pending_run_id):
                    if job.status == "blocked":
                        job.status, job.finished_at = "superseded", time.time()
                        job.result = {"summary": "Replaced by a newer requested review before starting.",
                                      "findings": [], "limitations": []}
        blocked = bool(session.active_run_id)
        before = set(db.scalars(select(Job.id).where(Job.run_id == run.id)))
        for cfg in configs:
            fusion.enqueue(db, run.id, {**cfg, "session_id": session.id, "review_fingerprint": signature})
        db.flush()
        if blocked:
            for job in jobs_for(db, session, run.id):
                if job.id not in before:
                    job.status = "blocked"
            if not reply:
                session.pending_run_id = run.id
        else:
            session.active_run_id = run.id
        created = True
    run.snapshot = {**run.snapshot, "notices": notices, "request_only": not created}
    if not created:
        run.status = "not_started"
    return run.id


def finding_context(db, session):
    rows = db.scalars(select(ReviewFinding).where(ReviewFinding.session_id == session.id)).all()
    result = []
    for row in rows:
        job = db.get(Job, row.job_id)
        if job and row.position < len(job.result.get("findings", [])):
            result.append({"id": row.id, "finding": job.result["findings"][row.position],
                           "status": row.status, "decision": row.decision})
    return result


async def prepare():
    """Prepare the diff once per session/run before any of its sandboxes launch."""
    with Session() as db:
        keys = [(s.repo, s.pr, s.id) for s in db.scalars(select(ReviewSession).where(
            ReviewSession.active_run_id.is_not(None)))]
    for repo, pr, key in keys:
        try:
            await prepare_session(repo, pr, key)
        except Exception:
            import logging
            logging.getLogger("litereview").exception("Could not prepare review session %s; will retry", key)


async def prepare_session(repo, pr, key):
    with transaction(repo, pr) as db:
        session = db.get(ReviewSession, key)
        refresh(db, session)
        jobs = jobs_for(db, session, session.active_run_id) if session.active_run_id else []
        candidates = [j for j in jobs if j.status in ("queued", "waiting")
                      and not j.config.get("session_prepared")]
        if not candidates:
            return
        run = db.get(Run, session.active_run_id)
        baseline = db.get(Run, session.baseline_run_id) if session.baseline_run_id else None
        cfg = candidates[0].config
        history = finding_context(db, session)
        compatible = (baseline and session.fingerprint == cfg["review_fingerprint"]
                      and baseline.base == run.base and baseline.head != run.head)
    diff, scope, start = run.snapshot["diff"], "full", run.base
    reason = "Full PR review."
    if cfg.get("review_mode", "full") != "full" and not run.snapshot.get("reply"):
        reason = "Full PR review: no compatible earlier completed review."
        if compatible:
            comparison = (await github.repo_request(repo, run.installation, "GET",
                          f"compare/{baseline.head}...{run.head}")).json()
            if comparison.get("status") == "ahead" and comparison.get("merge_base_commit", {}).get("sha") == baseline.head:
                diff = (await github.repo_request(repo, run.installation, "GET",
                        f"compare/{baseline.head}...{run.head}",
                        headers={"Accept": "application/vnd.github.diff"})).text
                scope, start, reason = "incremental", baseline.head, "Changes since the last successful review."
    with transaction(repo, pr) as db:
        session = db.get(ReviewSession, key)
        if session.active_run_id != run.id:
            return
        jobs = jobs_for(db, session, run.id)
        for job in jobs:
            if job.config.get("session_prepared"):
                continue
            job.config = {**job.config, "session_prepared": True, "review_diff": diff,
                          "review_scope": scope, "review_from": start, "scope_reason": reason,
                          "finding_history": history}
            if (scope == "incremental" and job.config.get("review_mode") == "compare"
                    and not job.config.get("fusion_parent_id") and not job.config.get("comparison_parent_id")):
                shadow = {**job.config, "comparison_parent_id": job.id, "review_scope": "full",
                          "review_diff": run.snapshot["diff"], "review_from": run.base,
                          "scope_reason": "Private full-review comparison.", "finding_history": [],
                          "minimum_review_seconds": job.config.get("minimum_minutes", 0) * 60}
                shadow["instructions"] = shadow["instructions"].removesuffix("\n\n" + fusion.AGGREGATOR_INSTRUCTIONS)
                fusion.enqueue(db, run.id, shadow)
