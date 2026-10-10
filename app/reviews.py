import asyncio
import re
import secrets
import time
from collections import Counter

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from runner.catalog import HARNESS_VERSIONS, review_capabilities

from . import github, tracing
from .access import caller_grant, caller_permits, permits
from .db import Job, Resource, Run, Session, uid
from .limits import job_timeout, timing_limits
from .security import digest
from .settings import settings

TERMINAL = {"completed", "failed", "timed_out"}


def command(body):
    try:
        return [item["name"] for item in command_requests(body)]
    except ValueError:
        return []


def command_requests(body):
    match = re.fullmatch(r"\s*(?:@litereview|light\s+review)\s+(.+?)\s*", body or "", re.I | re.S)
    if not match:
        return []
    selected = []
    for token in match[1].lower().split():
        if token.startswith("t="):
            raise ValueError(
                "Set minimum review time in LiteReview → PR agents. "
                "Comments use only reviewer names: @litereview name1 name2."
            )
        elif re.fullmatch(r"[a-z0-9_-]+", token):
            selected.append({"name": token})
        else:
            raise ValueError("Use @litereview name1 name2; names may contain letters, digits, _ and -.")
    unique = {}
    for item in selected:
        unique[item["name"]] = item
    return list(unique.values())


def expand(groups, resources):
    jobs = []
    for group in groups:
        for member in group["members"]:
            agent = resources.get(member["agent_id"])
            if not agent or agent.kind != "agents":
                raise HTTPException(409, "A group references a missing agent.")
            data = agent.data.copy()
            data["capabilities"] = review_capabilities(data.get("capabilities", []))
            data.setdefault("harness", "builtin")
            data["harness_version"] = HARNESS_VERSIONS[data["harness"]]
            minutes = data.get("minimum_minutes", 0)
            data.update(timing_limits(minutes, data.get("maximum_minutes"), data.get("max_model_calls")))
            conn = resources.get(data["connection_id"])
            if not conn or conn.kind != "connections" or conn.data["type"] == "mcp":
                raise HTTPException(409, "An agent needs a model connection.")
            data["connection"] = {"id": conn.id, **conn.data}
            data["skills"] = [{"id": sid, **resources[sid].data} for sid in data["skill_ids"]]
            data["mcp_definitions"] = []
            for mcp in data["mcps"]:
                resource = resources.get(mcp["connection_id"])
                if not resource or resource.kind != "connections" or resource.data["type"] != "mcp":
                    raise HTTPException(409, "An MCP connection is missing.")
                selected = set(mcp["tools"])
                definitions = [t for t in resource.data["tools"] if t["name"] in selected]
                if len(definitions) != len(selected):
                    raise HTTPException(409, "An MCP tool changed; test the connection and update the agent.")
                data["mcp_definitions"].append(
                    {
                        "id": resource.id,
                        "name": resource.data["name"],
                        "type": "mcp",
                        "base_url": resource.data["base_url"],
                        "tools": definitions,
                    }
                )
            for instance in range(1, member["count"] + 1):
                jobs.append(
                    {
                        **data,
                        "agent_id": agent.id,
                        "group": group["name"],
                        "instance": instance,
                        "target_kind": group.get("target_kind", "group"),
                    }
                )
    return jobs


def reject_command(delivery, repo, pr, actor, installation, reason):
    run = Run(
        id=uid(),
        delivery=delivery,
        repo=repo,
        pr=pr,
        actor=actor,
        installation=installation,
        head="",
        base="",
        status="rejected",
        snapshot={"rejection": reason},
    )
    with Session() as db:
        db.add(run)
        try:
            db.commit()
            return run.id
        except IntegrityError:
            db.rollback()
            return db.scalar(select(Run).where(Run.delivery == delivery)).id


async def accept(payload, delivery):
    github_state = github.setup_state()
    if github_state.get("disabled"):
        return None
    if payload.get("action") != "created" or "pull_request" not in payload.get("issue", {}):
        return None
    comment_data = payload.get("comment", {})
    if comment_data.get("user", {}).get("type") == "Bot":
        return None
    parse_error = None
    try:
        requests = command_requests(comment_data.get("body", ""))
    except ValueError as exc:
        requests, parse_error = [], str(exc)
    if not requests and not parse_error:
        return None
    repo = payload["repository"]["full_name"]
    actor = comment_data["user"]["login"]
    pr = payload["issue"]["number"]
    installation = payload.get("installation", {}).get("id", 0)
    with Session() as db:
        existing = db.scalar(select(Run).where(Run.delivery == delivery))
        if existing:
            return existing.id
        resources = {r.id: r for r in db.scalars(select(Resource)).all()}
    repos = [r for r in resources.values() if r.kind == "repos" and r.data["name"].lower() == repo.lower()]
    if not repos or repos[0].data["installation_id"] != installation:
        return None
    perm = await github.permission(repo, installation, actor)
    if perm not in ("read", "triage", "write", "maintain", "admin"):
        return None
    identity = None
    if settings().sso_required:
        from .sso import caller_identity

        identity = caller_identity(comment_data.get("user", {}))
        if not identity:
            return reject_command(
                delivery,
                repo,
                pr,
                actor,
                installation,
                "Sign in to LiteReview with your workspace Google account and link your GitHub account "
                "before requesting a review. Google authorization must be renewed when it expires.",
            )
    if parse_error:
        return reject_command(delivery, repo, pr, actor, installation, parse_error)
    grants = {(r.data["subject"], r.data["name"]): r for r in resources.values() if r.kind == "access"}
    caller_access = caller_grant(resources.values(), actor, identity.sub if identity else None)
    repo_access = grants.get(("repo", repo.lower()))
    selected = []
    for selection in requests:
        name = selection["name"]
        target = next(
            (
                r
                for r in resources.values()
                if r.kind in ("agents", "groups") and r.data["name"].lower() == name
            ),
            None,
        )
        group = target.data.copy() if target else None
        allowed = target and caller_permits(caller_access, target.id) and permits(repo_access, target.id)
        if not allowed:
            # Generic rejection avoids exposing group membership to unauthorized readers.
            return reject_command(
                delivery,
                repo,
                pr,
                actor,
                installation,
                "This review command is unavailable for this repository or caller. Check the agent or group names and the permissions in People and Repositories.",
            )
        group["target_kind"] = "agent" if target.kind == "agents" else "group"
        if target.kind == "agents":
            group["members"] = [{"agent_id": target.id, "count": 1}]
        selected.append(group)
    pr_info = (await github.repo_request(repo, installation, "GET", f"pulls/{pr}")).json()
    if pr_info["state"] != "open":
        return reject_command(
            delivery, repo, pr, actor, installation, "LiteReview only reviews open pull requests."
        )
    head, base = pr_info["head"]["sha"], pr_info["base"]["sha"]
    diff = (
        await github.repo_request(
            repo,
            installation,
            "GET",
            f"compare/{base}...{head}",
            headers={"Accept": "application/vnd.github.diff"},
        )
    ).text
    repository = {k: v for k, v in repos[0].data.items() if k != "test_secrets"}
    repository["id"] = repos[0].id
    run = Run(
        id=uid(),
        delivery=delivery,
        repo=repo,
        pr=pr,
        actor=actor,
        installation=installation,
        head=head,
        base=base,
        snapshot={
            "trigger_comment_id": comment_data.get("id"),
            "groups": selected,
            "repository": repository,
            "diff": diff,
            "title": pr_info["title"],
            "description": (pr_info.get("body") or ""),
        },
    )
    configurations = expand(selected, resources)
    with Session() as db:
        github_row = db.get(Resource, "github", with_for_update=True)
        state = github_row.data if github_row else {}
        if state.get("disabled") or state.get("generation", "") != github_state.get("generation", ""):
            return None
        db.add(run)
        for cfg in configurations:
            db.add(Job(id=uid(), run_id=run.id, config=cfg))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            return db.scalar(select(Run).where(Run.delivery == delivery)).id
    return run.id


def md(value):
    # Prevent model output from causing mentions or breaking attribution headings.
    return str(value).replace("@", "@\u200b").replace("<", "&lt;").replace(">", "&gt;")


def render_comment(run, jobs, newer=False):
    if run.status == "rejected":
        return f"<!-- litereview:{run.id} -->\n### Review not started\n\n{md(run.snapshot['rejection'])}"
    lines = [f"<!-- litereview:{run.id} -->", ""]
    if newer:
        lines += [f"> New commits were pushed. This review covers `{run.head[:12]}`.", ""]
    counts = Counter((j.config.get("group"), j.config["name"]) for j in jobs)
    for job in jobs:
        c = job.config
        label = md(c["name"])
        if c.get("target_kind") != "agent":
            label += f" · {md(c['group'])}"
        if counts[(c.get("group"), c["name"])] > 1:
            label += f" · Run {c['instance']}"
        lines += [f"### {label}", ""]
        if job.status not in TERMINAL:
            lines += ["Review in progress.", ""]
            continue
        result = job.result
        incomplete = job.status != "completed" or result.get("limitations")
        visible, notes = [], []
        if incomplete:
            visible += ["**Review incomplete.**", ""]
        elif any(check["status"] == "not_run" for check in result.get("checks", [])):
            visible += ["**Completed · Some checks not run.**", ""]
        if result.get("summary"):
            visible += [md(result["summary"]), ""]
        elif not incomplete and not result.get("findings"):
            visible += ["**No issues found.**", ""]
        for finding in result.get("findings", []):
            location = ""
            if finding.get("file"):
                from urllib.parse import quote

                path = finding["file"].lstrip("/")
                suffix = f"#L{finding['line']}" if finding.get("line") else ""
                location = f" · [{md(path)}{':' + str(finding['line']) if finding.get('line') else ''}](https://github.com/{run.repo}/blob/{run.head}/{quote(path)}{suffix})"
            visible += [
                f"**{md(finding['severity'].capitalize())} · {md(finding['title'])}**{location}",
                "",
                md(finding["explanation"]),
                "",
            ]
        if result.get("limitations"):
            visible += ["**Couldn’t complete**", ""]
            visible += [f"- {md(limit)}" for limit in result["limitations"]]
        for check in result.get("checks", []):
            label = {"passed": "Passed", "failed": "Failed", "not_run": "Not run"}[check["status"]]
            scope = "required" if check["required"] else "optional"
            notes += [f"- **{label} · {md(check['name'])}** ({scope}): {md(check['details'])}"]
        if c.get("minimum_review_seconds"):
            notes += [f"Requested minimum: {c['minimum_review_seconds'] // 60} minutes."]
            if result.get("review_seconds") is not None:
                notes += [f"Recorded review time: {result['review_seconds']:g} seconds."]
        note_text = "\n".join(notes).strip()
        section = "\n".join(visible).strip()
        lines += [section, ""]
        if note_text:
            lines += ["<details>", "<summary>Checks and execution details</summary>", "", note_text, "", "</details>", ""]
    lines += [
        "---",
        f"[View review]({settings().public_url}/runs/{run.id}) · Reviewed [`{run.head[:12]}`](https://github.com/{run.repo}/commit/{run.head})",
    ]
    return "\n".join(lines)


# GitHub's issue-comment API accepts at most 65,536 characters per comment.
GITHUB_COMMENT_CHARACTERS = 65_536


def comment_parts(run, body):
    if len(body) <= GITHUB_COMMENT_CHARACTERS:
        return [body]
    parts = []
    remaining = body.removeprefix(f"<!-- litereview:{run.id} -->")
    while remaining:
        number = len(parts) + 1
        marker = f"<!-- litereview:{run.id} -->" if number == 1 else f"<!-- litereview:{run.id}:part:{number} -->"
        prefix = marker + "\n"
        available = GITHUB_COMMENT_CHARACTERS - len(prefix)
        end = len(remaining) if len(remaining) <= available else remaining.rfind("\n", 0, available) + 1
        if end <= 0:
            end = available
        parts.append(prefix + remaining[:end])
        remaining = remaining[end:]
    return parts


async def publish(run_id):
    with Session() as db:
        run = db.get(Run, run_id)
        jobs = db.scalars(select(Job).where(Job.run_id == run_id)).all()
        done = all(j.status in TERMINAL for j in jobs)
        if run.published == 2 or (run.published == 1 and not done):
            return
        if done:
            for job in jobs:
                tracing.finish_job(job, run)
        if run.snapshot.get("harness_check"):
            run.status = (
                "completed"
                if done and all(j.status == "completed" and not j.result.get("limitations") for j in jobs)
                else "incomplete"
                if done
                else "running"
            )
            run.published = 2 if done else 1
            db.commit()
            return
    if run.published == 0 and run.status != "rejected":
        comment_id = run.snapshot.get("trigger_comment_id")
        if comment_id:
            try:
                # GitHub returns the existing reaction on retries, including after a lost response.
                await github.repo_request(
                    run.repo,
                    run.installation,
                    "POST",
                    f"issues/comments/{comment_id}/reactions",
                    json={"content": "eyes"},
                )
            except (HTTPException, httpx.HTTPError):
                if not done:
                    raise
                # A deleted request comment must not prevent publication of finished findings.
        with Session() as db:
            saved = db.get(Run, run_id)
            saved.published = 1
            saved.status = "running"
            saved.error = ""
            db.commit()
    if not done:
        return
    newer = False
    if done and run.status != "rejected":
        info = (await github.repo_request(run.repo, run.installation, "GET", f"pulls/{run.pr}")).json()
        newer = info["head"]["sha"] != run.head
    bodies = comment_parts(run, render_comment(run, jobs, newer))
    existing = {}
    if run.comment_id:
        existing[f"<!-- litereview:{run.id} -->"] = run.comment_id
    if not run.comment_id or len(bodies) > 1:
        # Recover every successful POST, including a lost response halfway through a large review.
        page = 1
        while True:
            comments = (
                await github.repo_request(
                    run.repo, run.installation, "GET", f"issues/{run.pr}/comments?per_page=100&page={page}"
                )
            ).json()
            for comment in comments:
                if comment["user"].get("type") == "Bot" or settings().github_token:
                    marker = comment.get("body", "").split("\n", 1)[0]
                    existing[marker] = comment["id"]
            if len(comments) < 100:
                break
            page += 1
    for index, body in enumerate(bodies):
        marker = body.split("\n", 1)[0]
        comment_id = existing.get(marker)
        if comment_id:
            await github.repo_request(
                run.repo, run.installation, "PATCH", f"issues/comments/{comment_id}", json={"body": body}
            )
        else:
            result = await github.repo_request(
                run.repo, run.installation, "POST", f"issues/{run.pr}/comments", json={"body": body}
            )
            comment_id = result.json()["id"]
        if index == 0:
            run.comment_id = comment_id
    with Session() as db:
        saved = db.get(Run, run_id)
        saved.comment_id = run.comment_id
        saved.published = 2 if done else 1
        saved.status = (
            "rejected"
            if run.status == "rejected"
            else (
                "completed"
                if done and all(j.status == "completed" and not j.result.get("limitations") for j in jobs)
                else "incomplete"
                if done
                else "running"
            )
        )
        saved.error = ""
        db.commit()


async def dispatcher():
    from .runtime import launch, terminate, watch_existing

    launching = {}
    while True:
        try:
            now = time.time()
            with Session() as db:
                active = db.scalars(select(Job).where(Job.status.in_(["starting", "running"]))).all()
                for job in active:
                    timeout = job_timeout(job.config)
                    if timeout is not None and job.started_at and now - job.started_at > timeout + 300:
                        job.status = "timed_out"
                        job.finished_at = now
                        job.result = {
                            "status": "timed_out",
                            "summary": "The sandbox did not finish within its time limit.",
                            "findings": [],
                            "limitations": ["Execution time limit reached."],
                        }
                        if job.sandbox_id:
                            asyncio.create_task(terminate(job.sandbox_id))
                    elif job.sandbox_id and job.id not in launching:
                        launching[job.id] = asyncio.create_task(watch_existing(job.id, job.sandbox_id))
                db.commit()
                queued = (
                    select(Job)
                    .join(Run, Job.run_id == Run.id)
                    .where(Job.status == "queued")
                    .order_by(Run.created_at, Job.id)
                )
                if settings().concurrency:
                    count = db.scalar(
                        select(func.count()).select_from(Job).where(Job.status.in_(["starting", "running"]))
                    )
                    queued = queued.limit(max(0, settings().concurrency - count))
                pending = db.scalars(queued).all()
                for job in pending:
                    token = secrets.token_urlsafe(40)
                    updated = db.execute(
                        update(Job)
                        .where(Job.id == job.id, Job.status == "queued")
                        .values(status="starting", started_at=now, token_hash=digest(token))
                    ).rowcount
                    db.commit()
                    if updated:
                        launching[job.id] = asyncio.create_task(launch(job.id, token))
                runs = db.scalars(select(Run).where(Run.published < 2)).all()
            for run in runs:
                try:
                    await publish(run.id)
                except Exception as exc:
                    with Session() as db:
                        saved = db.get(Run, run.id)
                        saved.error = (
                            exc.detail
                            if isinstance(exc, HTTPException)
                            else "GitHub publishing failed; will retry."
                        )
                        db.commit()
            for job_id, task in list(launching.items()):
                if task.done():
                    del launching[job_id]
                    task.result()
        except asyncio.CancelledError:
            raise
        except Exception:
            import logging

            logging.getLogger("litereview").exception("Dispatcher iteration failed")
        await asyncio.sleep(3)
