"""Incremental GitHub publication, checkpointed after each external side effect."""

import ast
import re
from collections import defaultdict

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from . import github, review_sessions, tracing
from .db import Job, ReviewFinding, ReviewPublication, Run, Session
from .publication_lock import publication_lock
from .reviews import (
    GITHUB_COMMENT_CHARACTERS,
    TERMINAL,
    comment_parts,
    evidence_details,
    md,
    ordered_jobs,
    render_comment,
)
from .settings import settings


def load_state(key):
    with Session() as db:
        row = db.get(ReviewPublication, key)
        return dict(row.data) if row else {}


def save_state(key, state):
    with Session.begin() as db:
        row = db.get(ReviewPublication, key)
        if row:
            row.data = dict(state)
        else:
            db.add(ReviewPublication(key=key, data=dict(state)))


async def request(run, method, suffix, **kwargs):
    return await github.repo_request(run.repo, run.installation, method, suffix, **kwargs)


async def marked_comments(run, suffix):
    comments, page = {}, 1
    actor_id = None
    if settings().github_token and not run.installation:
        actor_id = (await github.request("GET", "/user", token=settings().github_token)).json()["id"]
    while True:
        batch = (await request(run, "GET", f"{suffix}?per_page=100&page={page}")).json()
        for comment in batch:
            author = comment["user"]
            if author.get("id") == actor_id if actor_id else author.get("type") == "Bot":
                marker = comment.get("body", "").split("\n", 1)[0]
                comments[marker] = comment
        if len(batch) < 100:
            return comments
        page += 1


async def write_comments(run, body, existing, state, key):
    ids = list(state.get("comment_ids", []))
    parts = comment_parts(run, body)
    for index, part in enumerate(parts):
        marker = part.split("\n", 1)[0]
        comment_id = ids[index] if index < len(ids) else existing.get(marker, {}).get("id")
        if comment_id:
            try:
                await request(run, "PATCH", f"issues/comments/{comment_id}", json={"body": part})
            except github.GitHubError as exc:
                if exc.upstream_status != 404:
                    raise
                if index == 0:
                    state.pop("eyes_id", None)
                    state.pop("eyes_requested", None)
                comment_id = existing.get(marker, {}).get("id")
                if comment_id == (ids[index] if index < len(ids) else None):
                    comment_id = None
                if comment_id:
                    await request(run, "PATCH", f"issues/comments/{comment_id}", json={"body": part})
        if not comment_id:
            comment_id = (
                await request(run, "POST", f"issues/{run.pr}/comments", json={"body": part})
            ).json()["id"]
        if index == len(ids):
            ids.append(comment_id)
        else:
            ids[index] = comment_id
        state["comment_ids"] = ids
        save_state(key, state)
    # Retain overflow slots for later reviews, but never leave an old verdict
    # visible beneath a shorter replacement report.
    for index, comment_id in enumerate(ids[len(parts):], len(parts)):
        marker = parts[0].split("\n", 1)[0].removesuffix(" -->") + f":part:{index + 1} -->"
        body = f"{marker}\n\nThis section is no longer needed. [View the latest review](https://github.com/{run.repo}/pull/{run.pr}#issuecomment-{ids[0]})."
        try:
            await request(run, "PATCH", f"issues/comments/{comment_id}", json={"body": body})
        except github.GitHubError as exc:
            if exc.upstream_status != 404:
                raise
    return ids[0]


async def add_eyes(run, comment_id, state, key):
    if not state.get("eyes_id"):
        # Persist intent too: completion may race a lost POST response.
        state["eyes_requested"] = True
        save_state(key, state)
        response = await request(
            run, "POST", f"issues/comments/{comment_id}/reactions", json={"content": "eyes"}
        )
        state["eyes_id"] = response.json()["id"]
        save_state(key, state)


async def remove_eyes(run, comment_id, state, key):
    if state.get("eyes_requested") and not state.get("eyes_id"):
        try:
            await add_eyes(run, comment_id, state, key)
        except github.GitHubError as exc:
            if exc.upstream_status != 404:
                raise
    if state.get("eyes_id"):
        try:
            await request(run, "DELETE", f"issues/comments/{comment_id}/reactions/{state['eyes_id']}")
        except github.GitHubError as exc:
            if exc.upstream_status != 404:
                raise
        state.pop("eyes_id")
    state.pop("eyes_requested", None)
    save_state(key, state)


def diff_lines(diff):
    """Return right-side lines in the captured diff, including context lines."""
    allowed, path, number, remaining = {}, None, 0, 0
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            path, remaining = None, 0
        elif line.startswith("+++ ") and not remaining:
            raw = line[4:]
            try:
                path = ast.literal_eval(raw) if raw.startswith('"') else raw
                if raw.startswith('"') and re.search(r"\\[0-7]{3}", raw):
                    path = path.encode("latin-1").decode("utf-8")
            except (ValueError, SyntaxError, UnicodeError):
                path = None
            if path and path.startswith("b/"):
                path = path[2:]
                allowed.setdefault(path, set())
            else:
                path = None
        elif match := re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", line):
            number, remaining = int(match[1]), int(match[2] or 1)
        elif path and remaining and line.startswith((" ", "+")):
            allowed[path].add(number)
            number += 1
            remaining -= 1
    return allowed


def finding_body(run, job, index, finding, parent_id):
    return "\n\n".join(
        [
            f"<!-- litereview:{run.id}:job:{job.id}:finding:{index} -->",
            f"#### {md(finding['severity'].capitalize())} · {md(finding['title'])}",
            md(finding["explanation"]),
            *([evidence_details(finding["evidence"])] if finding.get("evidence") else []),
            f"[Latest summary by {md(job.config['name'])}](https://github.com/{run.repo}/pull/{run.pr}#issuecomment-{parent_id}) · [View this review]({settings().public_url}/runs/{run.id}) · Reviewed `{run.head[:12]}`",
        ]
    )


async def publish_findings(run, job, parent_id, state, key, allowed):
    links = dict(state.get("findings", {}))
    existing = None
    for index, finding in enumerate(job.result.get("findings", [])):
        if job.config.get("session_id"):
            with review_sessions.transaction(run.repo, run.pr) as db:
                identity = f"{job.id}:{index}"
                if not db.get(ReviewFinding, identity):
                    db.add(ReviewFinding(id=identity, job_id=job.id, position=index,
                                        session_id=job.config["session_id"]))
        if str(index) in links:
            continue
        path, line = finding.get("file"), finding.get("line")
        body = finding_body(run, job, index, finding, parent_id)
        if not path or line not in allowed.get(path, ()) or len(body) > GITHUB_COMMENT_CHARACTERS:
            links[str(index)] = None  # Keep the full finding in the parent comment.
        else:
            if existing is None:
                existing = await marked_comments(run, f"pulls/{run.pr}/comments")
            marker = body.split("\n", 1)[0]
            comment = existing.get(marker)
            if not comment:
                try:
                    comment = (
                        await request(
                            run,
                            "POST",
                            f"pulls/{run.pr}/comments",
                            json={
                                "body": body,
                                "commit_id": run.head,
                                "path": path,
                                "line": line,
                                "side": "RIGHT",
                            },
                        )
                    ).json()
                except github.GitHubError as exc:
                    invalid_anchor = any(
                        isinstance(error, dict)
                        and any(
                            field in error.get("field", "").split(".")
                            for field in ("line", "path", "commit_id")
                        )
                        for error in exc.errors
                    )
                    if exc.upstream_status != 422 or not invalid_anchor:
                        raise
                    # GitHub may reject an outdated anchor. Preserve the finding
                    # in the agent comment; transport/permission errors still retry.
            links[str(index)] = (
                f"https://github.com/{run.repo}/pull/{run.pr}#discussion_r{comment['id']}"
                if comment
                else None
            )
            if comment and job.config.get("session_id"):
                with review_sessions.transaction(run.repo, run.pr) as db:
                    db.get(ReviewFinding, f"{job.id}:{index}").comment_id = comment["id"]
        state["findings"] = links
        save_state(key, state)
    return links


def summary_identity(run, job):
    return review_sessions.session_id(run.repo, run.pr, job.config["agent_id"])


def reviewer_runs(run, job):
    return select(Run.id).join(Job, Job.run_id == Run.id).where(
        Run.repo == run.repo, Run.pr == run.pr,
        Job.config["agent_id"].as_string() == job.config["agent_id"],
        Run.snapshot["reply"].as_string().is_(None),
        Run.snapshot["harness_check"].as_boolean().is_not(True),
        Job.config["fusion_parent_id"].as_string().is_(None),
        Job.config["comparison_parent_id"].as_string().is_(None),
    )


def adopt_summary(run, job, existing, state):
    if state.get("comment_ids"):
        return
    with Session() as db:
        previous = db.execute(reviewer_runs(run, job).add_columns(Job.id)).all()
    markers = {f"<!-- litereview:{rid}:job:{jid} -->" for rid, jid in previous}
    markers.add(f"<!-- litereview:summary:{summary_identity(run, job)} -->")
    candidates = [existing[m] for m in markers if m in existing]
    if candidates:
        first = min(candidates, key=lambda c: c["id"])
        marker = first["body"].split("\n", 1)[0]
        ids = [first["id"]]
        while part := existing.get(marker.removesuffix(" -->") + f":part:{len(ids) + 1} -->"):
            ids.append(part["id"])
        state["comment_ids"] = ids
        for _, jid in previous:
            checkpoint = load_state(f"job:{jid}")
            if checkpoint.get("comment_ids", [])[:1] == ids[:1]:
                for field in ("eyes_id", "eyes_requested"):
                    if field in checkpoint:
                        state[field] = checkpoint[field]
                break


def summary_body(run, jobs, all_jobs, newer):
    sections = [f"<!-- litereview:summary:{summary_identity(run, jobs[0])} -->"]
    with Session() as db:
        for job in jobs:
            decisions = {str(f.position): {"status": f.status, **f.decision} for f in db.scalars(
                select(ReviewFinding).where(ReviewFinding.job_id == job.id))}
            body = render_comment(run, [job], newer, agent_comment=True, all_jobs=all_jobs,
                                  finding_links=load_state(f"job:{job.id}").get("findings"),
                                  finding_decisions=decisions)
            section, footer = body.split("\n", 1)[1].rsplit("\n---\n", 1)
            sections.append(section.strip())
    return "\n\n".join(sections) + "\n\n---\n" + footer


async def retire_legacy_progress(run, job, checkpoint, parent_id):
    ids = checkpoint.get("comment_ids", [])
    if not ids or ids[0] == parent_id or checkpoint.get("complete"):
        return
    body = (f"This reviewer’s latest result is in its [summary](https://github.com/{run.repo}/pull/{run.pr}"
            f"#issuecomment-{parent_id}). [View this run]({settings().public_url}/runs/{run.id}).")
    for comment_id in ids:
        try:
            await request(run, "PATCH", f"issues/comments/{comment_id}", json={"body": body})
        except github.GitHubError as exc:
            if exc.upstream_status != 404:
                raise
    await remove_eyes(run, ids[0], checkpoint, f"job:{job.id}")


async def publish_reviewer(run, jobs, all_jobs, existing, newer, allowed):
    with Session() as db:
        latest = db.scalar(reviewer_runs(run, jobs[0]).order_by(Run.created_at.desc(), Run.id.desc()).limit(1))
    if latest != run.id:
        # A delayed completion or old finding reply must not replace the latest summary.
        summary_ids = load_state(f"summary:{summary_identity(run, jobs[0])}").get("comment_ids", [])
        for job in jobs:
            state = load_state(f"job:{job.id}")
            if summary_ids:
                await retire_legacy_progress(run, job, state, summary_ids[0])
            save_state(f"job:{job.id}", {**state, "ready": True, "status": job.status,
                                       "complete": job.status in TERMINAL})
            if job.status in TERMINAL:
                tracing.finish_job(job, run)
        return
    key = f"summary:{summary_identity(run, jobs[0])}"
    state = load_state(key)
    adopt_summary(run, jobs[0], existing, state)
    state["run_id"] = run.id
    parent_id = await write_comments(run, summary_body(run, jobs, all_jobs, newer), existing, state, key)
    if any(job.status not in TERMINAL for job in jobs):
        await add_eyes(run, parent_id, state, key)
    errors = []
    for job in jobs:
        job_key = f"job:{job.id}"
        checkpoint = load_state(job_key)
        if checkpoint.get("complete"):
            continue
        await retire_legacy_progress(run, job, checkpoint, parent_id)
        checkpoint["comment_ids"] = [parent_id]
        save_state(job_key, checkpoint)
        try:
            if job.status in TERMINAL:
                tracing.finish_job(job, run)
                await publish_findings(run, job, parent_id, checkpoint, job_key, allowed)
            checkpoint.update(ready=True, status=job.status)
            save_state(job_key, checkpoint)
        except (HTTPException, httpx.HTTPError) as exc:
            errors.append(exc)
    await write_comments(run, summary_body(run, jobs, all_jobs, newer), existing, state, key)
    if all(job.status in TERMINAL for job in jobs):
        await remove_eyes(run, parent_id, state, key)
    if errors:
        raise errors[0]
    for job in jobs:
        if job.status in TERMINAL:
            checkpoint = load_state(f"job:{job.id}")
            save_state(f"job:{job.id}", {**checkpoint, "complete": True})


async def publish_run(run_id):
    # Acquire before reading checkpoints or listing comments. A marker only
    # recovers a completed POST; it cannot stop two simultaneous POSTs.
    with Session() as db:
        run = db.get(Run, run_id)
        pr_key = f"pr:{run.repo.lower()}:{run.pr}"
    with publication_lock(run_id, parent_run_id=pr_key) as acquired:
        if acquired:
            await _publish_run(run_id)


async def acknowledge(run_id):
    """React after durable acceptance; ordinary publication retries failures."""
    try:
        with publication_lock(run_id) as acquired:
            if not acquired:
                return
            with Session() as db:
                run = db.get(Run, run_id)
                if run.status == "rejected" or run.snapshot.get("request_only"):
                    return
            trigger = run.snapshot.get("trigger_comment_id")
            key = f"run:{run.id}"
            state = load_state(key)
            if trigger and not state.get("acknowledged") and run.published < 2:
                await add_eyes(run, trigger, state, key)
                state["acknowledged"] = True
                save_state(key, state)
    except Exception:
        import logging
        logging.getLogger("litereview").exception("Could not acknowledge review %s; publication will retry", run_id)


async def _publish_run(run_id):
    with Session() as db:
        run = db.get(Run, run_id)
        if run.snapshot.get("reply"):
            return  # Rebuttals publish into the original finding thread.
        all_jobs = db.scalars(select(Job).where(Job.run_id == run_id)).all()
        jobs = ordered_jobs(run, all_jobs)
        if run.published == 2:
            return
    done = all(j.status in TERMINAL for j in all_jobs)
    status = (
        "completed"
        if all(j.status == "completed" and not j.result.get("limitations") for j in jobs)
        else "incomplete"
    )
    if jobs and all(j.status == "superseded" for j in jobs):
        status = "superseded"
    if run.snapshot.get("harness_check"):
        if done:
            for job in jobs:
                tracing.finish_job(job, run)
    else:
        key = f"run:{run.id}"
        state = load_state(key)
        if run.snapshot.get("request_only"):
            existing = await marked_comments(run, f"issues/{run.pr}/comments")
            lines = [f"<!-- litereview:{run.id} -->", "### Review already requested", ""]
            for notice in run.snapshot["notices"]:
                lines.append(f"{md(notice['name'])} is already reviewing `{notice['head'][:12]}`. "
                             f"[View existing review]({settings().public_url}/runs/{notice['run_id']}).")
            run.comment_id = await write_comments(run, "\n\n".join(lines), existing, state, key)
            status = "not_started"
        elif run.status == "rejected":
            existing = await marked_comments(run, f"issues/{run.pr}/comments")
            run.comment_id = await write_comments(run, render_comment(run, []), existing, state, key)
            status = "rejected"
        else:
            if run.snapshot.get("notices"):
                notice_key = f"notice:{run.id}"
                notice_state = load_state(notice_key)
                if not notice_state.get("complete"):
                    existing = await marked_comments(run, f"issues/{run.pr}/comments")
                    body = f"<!-- litereview:{run.id}:notice -->\n\n" + "\n\n".join(
                        f"{md(n['name'])} already has a review queued or running for `{n['head'][:12]}`. "
                        f"[View review]({settings().public_url}/runs/{n['run_id']})."
                        for n in run.snapshot["notices"])
                    await write_comments(run, body, existing, notice_state, notice_key)
                    notice_state["complete"] = True
                    save_state(notice_key, notice_state)
            trigger = run.snapshot.get("trigger_comment_id")
            if trigger and not state.get("acknowledged"):
                if not done or run.published == 1:
                    try:
                        await add_eyes(run, trigger, state, key)
                    except github.GitHubError as exc:
                        if exc.upstream_status != 404:
                            raise
                state["acknowledged"] = True
                save_state(key, state)
            pending = [
                j
                for j in jobs
                if not load_state(f"job:{j.id}").get("complete")
                and (j.status in TERMINAL or not load_state(f"job:{j.id}").get("ready")
                     or load_state(f"job:{j.id}").get("status") != j.status)
            ]
            if pending:
                existing = await marked_comments(run, f"issues/{run.pr}/comments")
                newer = False
                if any(j.status in TERMINAL for j in pending):
                    info = (await request(run, "GET", f"pulls/{run.pr}")).json()
                    newer = info["head"]["sha"] != run.head
                allowed = diff_lines(run.snapshot.get("diff", ""))
                errors = []
                reviewers = defaultdict(list)
                for job in jobs:
                    reviewers[job.config["agent_id"]].append(job)
                for reviewer_jobs in reviewers.values():
                    if not any(job in pending for job in reviewer_jobs):
                        continue
                    try:
                        await publish_reviewer(run, reviewer_jobs, jobs, existing, newer, allowed)
                    except (HTTPException, httpx.HTTPError) as exc:
                        errors.append(exc)
                if errors:
                    raise errors[0]
            if done:
                if trigger:
                    await remove_eyes(run, trigger, state, key)
                # Replace an old-version aggregate progress comment with links.
                if run.comment_id:
                    links = [
                        f"- [{md(j.config['name'])}](https://github.com/{run.repo}/pull/{run.pr}#issuecomment-{load_state(f'job:{j.id}')['comment_ids'][0]})"
                        for j in jobs
                        if load_state(f"job:{j.id}").get("comment_ids")
                    ]
                    await request(
                        run,
                        "PATCH",
                        f"issues/comments/{run.comment_id}",
                        json={"body": "### ✅ Reviews finished\n\n" + "\n".join(links)},
                    )
    with Session.begin() as db:
        saved = db.get(Run, run_id)
        saved.comment_id = run.comment_id
        saved.status = status if done else (
            "queued" if all(j.status in ("queued", "blocked", "waiting") for j in all_jobs) else "running"
        )
        saved.published = 2 if done else 1
        saved.error = ""
