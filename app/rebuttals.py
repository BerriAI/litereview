"""Evidence-based follow-ups to findings, using the existing review workers."""

import logging
import time

import anyio
from fastapi import HTTPException
from sqlalchemy import select

from . import github, review_sessions, reviews
from .access import allows_anyone, caller_grant, caller_permits, permits
from .db import Job, Resource, ReviewFinding, ReviewPublication, Run, Session, uid
from .settings import settings


def find_parent(db, repo, number, comment_id):
    finding = db.scalar(select(ReviewFinding).where(ReviewFinding.comment_id == comment_id))
    if finding:
        job = db.get(Job, finding.job_id)
        run = db.get(Run, job.run_id)
        return finding if run.repo.lower() == repo.lower() and run.pr == number else None
    # Upgrade existing published threads from their persisted GitHub link.
    for job, run in db.execute(select(Job, Run).join(Run, Run.id == Job.run_id).where(
            Run.repo == repo, Run.pr == number)):
        publication = db.get(ReviewPublication, f"job:{job.id}")
        for position, url in (publication.data.get("findings", {}) if publication else {}).items():
            if url and url.endswith(f"#discussion_r{comment_id}"):
                session = review_sessions.get_session(db, run, job.config["agent_id"])
                finding = ReviewFinding(id=f"{job.id}:{position}", session_id=session.id,
                                        job_id=job.id, position=int(position), comment_id=comment_id)
                db.add(finding)
                db.flush()
                return finding
    return None


async def thread_comments(repo, installation, number, root, through):
    comments, page = [], 1
    while True:
        batch = (await github.repo_request(repo, installation, "GET",
                 f"pulls/{number}/comments?per_page=100&page={page}")).json()
        comments.extend({"id": c["id"], "author": c["user"]["login"], "body": c["body"]}
                        for c in batch if (c["id"] == root or c.get("in_reply_to_id") == root)
                        and c["id"] <= through)
        if len(batch) < 100:
            return sorted(comments, key=lambda c: c["id"])
        page += 1


async def accept(payload, delivery):
    if payload.get("action") != "created" or github.setup_state().get("disabled"):
        return None
    comment = payload.get("comment", {})
    root, comment_id = comment.get("in_reply_to_id"), comment.get("id")
    actor = comment.get("user", {})
    if not root or not comment_id or actor.get("type") == "Bot":
        return None
    repo, number = payload["repository"]["full_name"], payload["pull_request"]["number"]
    installation = payload.get("installation", {}).get("id", 0)
    key = f"reply:{comment_id}"
    with review_sessions.transaction(repo, number) as db:
        existing = db.scalar(select(Run.id).where(Run.delivery == key))
        if existing:
            return existing
        finding = find_parent(db, repo, number, root)
        if not finding:
            return None
        original = db.get(Job, finding.job_id)
        original_run = db.get(Run, original.run_id)
        resources = {r.id: r for r in db.scalars(select(Resource))}
        repository = next((r for r in resources.values() if r.kind == "repos"
                           and r.data["name"].lower() == repo.lower()), None)
        if not repository or installation != repository.data["installation_id"] or installation != original_run.installation:
            return None
    target = resources.get(original.config.get("target_id"))
    if not original.config.get("target_id"):
        target = (resources.get(original.config["agent_id"]) if original.config.get("target_kind") == "agent"
                  else next((r for r in resources.values() if r.kind == "groups"
                             and r.data["name"] == original.config.get("group")), None))
    identity = None
    if not allows_anyone(target) and settings().sso_required:
        from .sso import caller_identity
        identity = caller_identity(actor)
        if not identity:
            return None
    grant = caller_grant(resources.values(), actor["login"], identity.sub if identity else None)
    repo_grant = next((r for r in resources.values() if r.kind == "access"
                       and r.data["subject"] == "repo" and r.data["name"].lower() == repo.lower()), None)
    if (not target or not permits(repo_grant, target.id)
            or not (allows_anyone(target) or caller_permits(grant, target.id))):
        return None
    if target.kind == "groups" and not any(m["agent_id"] == original.config["agent_id"]
                                           for m in target.data["members"]):
        return None
    if (not allows_anyone(target)
            and await github.permission(repo, installation, actor["login"]) not in ("read", "triage", "write", "maintain", "admin")):
        return None
    pr = (await github.repo_request(repo, installation, "GET", f"pulls/{number}")).json()
    if pr["state"] != "open":
        return None
    history = await thread_comments(repo, installation, number, root, comment_id)
    if not any(c["id"] == comment_id for c in history):
        history.append({"id": comment_id, "author": actor["login"], "body": comment.get("body", "")})
    agent = resources.get(original.config["agent_id"])
    if not agent:
        return None
    cfg = reviews.expand([{"name": agent.data["name"], "target_kind": "agent",
                           "members": [{"agent_id": agent.id, "count": 1}]}], resources)[0]
    cfg.pop("fusion", None)  # The final review model adjudicates this one finding.
    cfg.update(task="reply", minimum_review_seconds=0, review_mode="full")
    head, base = pr["head"]["sha"], pr["base"]["sha"]
    diff = (await github.repo_request(repo, installation, "GET", f"compare/{base}...{head}",
            headers={"Accept": "application/vnd.github.diff"})).text
    run = Run(id=uid(), delivery=key, repo=repo, pr=number, actor=actor["login"], installation=installation,
              head=head, base=base, snapshot={
                  "trigger": {"type": "reply"}, "groups": [], "title": pr["title"],
                  "description": pr.get("body") or "", "diff": diff,
                  "repository": {"id": repository.id, **{k: v for k, v in repository.data.items() if k != "test_secrets"}},
                  "reply": {"finding_id": finding.id, "root_comment_id": root, "comment_id": comment_id,
                            "original_head": original_run.head,
                            "finding": original.result["findings"][finding.position], "comments": history},
              })
    with review_sessions.transaction(repo, number) as db:
        if github.setup_state().get("disabled"):
            return None
        existing = db.scalar(select(Run.id).where(Run.delivery == key))
        return existing or review_sessions.enqueue(db, run, [cfg])


async def publish_pending():
    with Session() as db:
        ids = list(db.scalars(select(Run.id).where(Run.published < 2,
                              Run.snapshot["reply"].as_string().is_not(None))))
    for run_id in ids:
        await publish_one(run_id)


async def publish_one(run_id):
    from .publication import load_state
    from .publication_lock import publication_lock

    state_key = f"reply:{run_id}"
    if load_state(state_key).get("retry_at", 0) > time.time():
        return
    try:
        with anyio.fail_after(30):
            await publish_reply(run_id)
    except Exception as exc:
        message = exc.detail if isinstance(exc, HTTPException) else "GitHub follow-up publishing failed; will retry."
        with publication_lock(run_id) as acquired:
            if not acquired:
                return
            with Session.begin() as db:
                run = db.get(Run, run_id)
                if run.published == 2:
                    return
                state = db.get(ReviewPublication, state_key)
                if not state:
                    state = ReviewPublication(key=state_key, data={})
                    db.add(state)
                state.data = {**state.data, "retry_at": time.time() + 60}
                run.error = message
        logging.getLogger("litereview").exception("Could not publish thread follow-up %s; will retry", run_id)


def record_decision(run, job, finding, verdict):
    """Save finding history so the queue can advance independently of GitHub writes."""
    with review_sessions.transaction(run.repo, run.pr) as db:
        key = f"reply:{run.id}"
        state = db.get(ReviewPublication, key)
        if not state:
            state = ReviewPublication(key=key, data={})
            db.add(state)
        if state.data.get("decision_recorded"):
            return dict(state.data)
        saved = db.get(ReviewFinding, finding.id)
        resolve_thread = (True if verdict["verdict"] == "withdraw" else
                          False if verdict["verdict"] == "uphold" and saved.status == "withdrawn" else None)
        if verdict["verdict"] == "withdraw":
            saved.status = "withdrawn"
        elif verdict["verdict"] == "uphold":
            saved.status = "open"
        if verdict["verdict"] != "uncertain" or not saved.decision:
            saved.decision = {**verdict, "head": run.head, "run_id": run.id}
        publication = db.get(ReviewPublication, f"job:{finding.job_id}")
        if publication:
            publication.data = {**publication.data, "complete": False}
            db.get(Run, db.get(Job, finding.job_id).run_id).published = 1
        state.data = {**state.data, "verdict": verdict, "decision_recorded": True,
                      "resolve_thread": resolve_thread}
        db.get(Run, run.id).status = "completed" if job.status == "completed" and not job.result.get("limitations") else "incomplete"
        return dict(state.data)


async def publish_reply(run_id):
    from .publication import load_state, marked_comments, save_state
    from .publication_lock import publication_lock

    with Session() as db:
        run = db.get(Run, run_id)
        reply = run.snapshot.get("reply")
        if not reply or run.published == 2:
            return
        finding = db.get(ReviewFinding, reply["finding_id"])
        parent_run_id = db.get(Job, finding.job_id).run_id
    # Share the original review's lock so refreshing its finding summary cannot
    # race that publisher or a newer follow-up's thread action.
    with publication_lock(run_id, parent_run_id=parent_run_id) as acquired:
        if not acquired:
            return
        with Session() as db:
            run = db.get(Run, run_id)
            reply = run.snapshot.get("reply")
            if not reply or run.published == 2:
                return
            job = db.scalar(select(Job).where(Job.run_id == run.id))
            if not job or job.status not in reviews.TERMINAL:
                return
            finding = db.get(ReviewFinding, reply["finding_id"])
        state_key = f"reply:{run.id}"
        state = load_state(state_key)
        verdict = state.get("verdict") or job.result.get("rebuttal") or {"verdict": "uncertain", "reason": job.result.get("summary") or "Could not verify this reply.", "evidence": []}
        if not state.get("decision_recorded") and (job.status != "completed" or job.result.get("limitations")):
            verdict = {**verdict, "verdict": "uncertain", "reason": "Could not complete verification. " + " ".join(job.result.get("limitations", []))}
        current = (await github.repo_request(run.repo, run.installation, "GET", f"pulls/{run.pr}")).json()
        stale = current["head"]["sha"] != run.head or current.get("state", "open") != "open"
        if stale:
            verdict = {"verdict": "uncertain", "reason": "The PR changed during this follow-up. The thread was left unchanged; reply again to reassess the current code.", "evidence": []}
        if not state.get("decision_recorded"):
            state = record_decision(run, job, finding, verdict)
        # Old publication retries must never undo a more recent adjudication.
        with Session() as db:
            latest = db.get(ReviewFinding, finding.id).decision.get("run_id") == run.id
        warning = ""
        if state.get("resolve_thread") is not None and not stale and latest:
            try:
                await github.resolve_review_thread(run.repo, run.installation, run.pr, reply["root_comment_id"],
                                                   expected_head=run.head, resolved=state["resolve_thread"])
            except HTTPException as exc:
                if exc.status_code != 403:
                    raise
                warning = exc.detail
        # Keep any historical adjudication in finding history. The published
        # response can explain why its delayed thread action is now obsolete.
        if not latest and state.get("resolve_thread") is not None and not stale:
            warning = "A newer follow-up has already updated this finding; this reply will not change the thread."
        marker = f"<!-- litereview:{run.id}:reply -->"
        label = {"withdraw": "Finding withdrawn", "uphold": "Finding still applies", "uncertain": "Unable to conclude"}[verdict["verdict"]]
        body = "\n\n".join([marker, f"**{label}**", reviews.md(verdict["reason"]),
                             *([reviews.evidence_details(verdict["evidence"])] if verdict.get("evidence") else []),
                             *([reviews.md(warning)] if warning else []),
                             f"Reviewed `{run.head[:12]}` · [View follow-up]({settings().public_url}/runs/{run.id})"])
        existing = await marked_comments(run, f"pulls/{run.pr}/comments")
        comment_ids, markers = [], set()
        for part in reviews.comment_parts(run, body):
            part_marker = part.split("\n", 1)[0]
            markers.add(part_marker)
            posted = existing.get(part_marker)
            if posted:
                if posted.get("body") != part:
                    await github.repo_request(run.repo, run.installation, "PATCH", f"pulls/comments/{posted['id']}", json={"body": part})
            else:
                posted = (await github.repo_request(run.repo, run.installation, "POST",
                          f"pulls/{run.pr}/comments/{reply['root_comment_id']}/replies", json={"body": part})).json()
            comment_ids.append(posted["id"])
            save_state(state_key, {**state, "comment_ids": comment_ids})
        # A changed PR can replace a long pending withdrawal with a short notice.
        for old_marker, posted in existing.items():
            if old_marker.startswith(marker.removesuffix(" -->") + ":part:") and old_marker not in markers:
                await github.repo_request(run.repo, run.installation, "DELETE", f"pulls/comments/{posted['id']}")
        save_state(state_key, {**state, "comment_ids": comment_ids, "verdict": verdict, "retry_at": 0})
        with Session.begin() as db:
            completed = db.get(Run, run.id)
            completed.published = 2
            completed.error = warning
