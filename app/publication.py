"""Incremental GitHub publication, checkpointed after each external side effect."""

import ast
import re

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from . import github, tracing
from .db import Job, ReviewPublication, Run, Session
from .reviews import GITHUB_COMMENT_CHARACTERS, TERMINAL, comment_parts, md, render_comment
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
    ids = state.get("comment_ids", [])
    for index, part in enumerate(comment_parts(run, body)):
        marker = part.split("\n", 1)[0]
        comment_id = ids[index] if index < len(ids) else existing.get(marker, {}).get("id")
        if comment_id:
            await request(run, "PATCH", f"issues/comments/{comment_id}", json={"body": part})
        else:
            comment_id = (
                await request(run, "POST", f"issues/{run.pr}/comments", json={"body": part})
            ).json()["id"]
        if index == len(ids):
            ids.append(comment_id)
        state["comment_ids"] = ids
        save_state(key, state)
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
            *[md(item) for item in finding.get("evidence", [])],
            f"[Review by {md(job.config['name'])}](https://github.com/{run.repo}/pull/{run.pr}#issuecomment-{parent_id}) · Reviewed `{run.head[:12]}`",
        ]
    )


async def publish_findings(run, job, parent_id, state, key, allowed):
    links = dict(state.get("findings", {}))
    existing = None
    for index, finding in enumerate(job.result.get("findings", [])):
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
        state["findings"] = links
        save_state(key, state)
    return links


async def publish_agent(run, job, jobs, existing, newer, allowed):
    key = f"job:{job.id}"
    state = load_state(key)
    if state.get("complete"):
        return
    terminal = job.status in TERMINAL
    if not state.get("ready"):
        # Create the parent before its inline threads so every finding links back.
        body = render_comment(run, [job], newer, agent_comment=True, all_jobs=jobs)
        if terminal:
            # Keep a compact parent until its linked threads are ready.
            marker = body.split("\n", 1)[0]
            title = next(line for line in body.splitlines() if line.startswith("### "))
            body = f"{marker}\n\n{title}\n\nPublishing review results…"
        parent_id = await write_comments(run, body, existing, state, key)
        if not terminal:
            await add_eyes(run, parent_id, state, key)
        state["ready"] = True
        save_state(key, state)
    parent_id = state["comment_ids"][0]
    if terminal:
        tracing.finish_job(job, run)
        links = await publish_findings(run, job, parent_id, state, key, allowed)
        body = render_comment(run, [job], newer, agent_comment=True, all_jobs=jobs, finding_links=links)
        await write_comments(run, body, existing, state, key)
        await remove_eyes(run, parent_id, state, key)
        state["complete"] = True
        save_state(key, state)


async def publish_run(run_id):
    with Session() as db:
        run = db.get(Run, run_id)
        jobs = db.scalars(select(Job).where(Job.run_id == run_id).order_by(Job.id)).all()
        if run.published == 2:
            return
    done = all(j.status in TERMINAL for j in jobs)
    status = (
        "completed"
        if all(j.status == "completed" and not j.result.get("limitations") for j in jobs)
        else "incomplete"
    )
    if run.snapshot.get("harness_check"):
        if done:
            for job in jobs:
                tracing.finish_job(job, run)
    else:
        key = f"run:{run.id}"
        state = load_state(key)
        if run.status == "rejected":
            existing = await marked_comments(run, f"issues/{run.pr}/comments")
            run.comment_id = await write_comments(run, render_comment(run, []), existing, state, key)
            status = "rejected"
        else:
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
                and (j.status in TERMINAL or not load_state(f"job:{j.id}").get("ready"))
            ]
            if pending:
                existing = await marked_comments(run, f"issues/{run.pr}/comments")
                newer = False
                if any(j.status in TERMINAL for j in pending):
                    info = (await request(run, "GET", f"pulls/{run.pr}")).json()
                    newer = info["head"]["sha"] != run.head
                allowed = diff_lines(run.snapshot.get("diff", ""))
                errors = []
                for job in pending:
                    try:
                        await publish_agent(run, job, jobs, existing, newer, allowed)
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
        saved.status = status if done else "running"
        saved.published = 2 if done else 1
        saved.error = ""
