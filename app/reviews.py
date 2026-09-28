import asyncio
import re
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from . import github
from .db import Job, Resource, Run, Session, uid
from .security import digest
from .settings import settings

TERMINAL = {"completed", "failed", "timed_out"}


def command(body):
    match = re.fullmatch(r"\s*@litereview\s+([A-Za-z0-9_\-\s]+)\s*", body or "", re.IGNORECASE)
    if not match:
        return []
    return list(dict.fromkeys(match[1].lower().split()))[:20]


def expand(groups, resources):
    jobs = []
    for group in groups:
        for member in group["members"]:
            agent = resources.get(member["agent_id"])
            if not agent or agent.kind != "agents":
                raise HTTPException(409, "A group references a missing agent.")
            data = agent.data.copy()
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
                    {"id": resource.id, "name": resource.data["name"], "tools": definitions}
                )
            for instance in range(1, member["count"] + 1):
                jobs.append({**data, "agent_id": agent.id, "group": group["name"], "instance": instance})
    if len(jobs) > 30:
        raise HTTPException(400, "A review can launch at most 30 agent instances.")
    return jobs


async def accept(payload, delivery):
    if payload.get("action") != "created" or "pull_request" not in payload.get("issue", {}):
        return None
    comment_data = payload.get("comment", {})
    if comment_data.get("user", {}).get("type") == "Bot":
        return None
    names = command(comment_data.get("body", ""))
    if not names:
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
    selected = []
    for name in names:
        group = next(
            (r.data for r in resources.values() if r.kind == "groups" and r.data["name"] == name), None
        )
        allowed = group and repo.lower() in [r.lower() for r in group["repos"]]
        if group and group["caller_policy"] == "allowlist":
            allowed = allowed and actor.lower() in [u.lower() for u in group["callers"]]
        else:
            allowed = allowed and perm in ("write", "maintain", "admin")
        if not allowed:
            # Generic rejection avoids exposing group membership to unauthorized readers.
            raise HTTPException(403, "This review command is unavailable for this repository or caller.")
        selected.append(group)
    pr_info = (await github.repo_request(repo, installation, "GET", f"pulls/{pr}")).json()
    if pr_info["state"] != "open":
        raise HTTPException(400, "LiteReview only reviews open pull requests.")
    if pr_info.get("changed_files", 0) > 300:
        raise HTTPException(400, "This PR exceeds the v1 limit of 300 changed files.")
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
    if len(diff) > 2_000_000:
        raise HTTPException(400, "The PR diff exceeds the v1 limit of 2 MB.")
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
            "groups": selected,
            "repository": repository,
            "diff": diff,
            "title": pr_info["title"],
            "description": (pr_info.get("body") or "")[:20000],
        },
    )
    configurations = expand(selected, resources)
    with Session() as db:
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
    done = all(j.status in TERMINAL for j in jobs)
    title = "Review complete" if done else "Review in progress"
    if done and any(j.status != "completed" or j.result.get("limitations") for j in jobs):
        title = "Review finished with incomplete checks"
    lines = [
        f"<!-- litereview:{run.id} -->",
        f"## LiteReview · {title}",
        f"Reviewed commit `{run.head[:12]}` · {len(jobs)} agents",
        "",
    ]
    if newer:
        lines += ["**Newer commits exist. These results apply to the commit above.**", ""]
    for job in jobs:
        c = job.config
        lines += [f"### {md(c['group'])} / {md(c['name'])} · {c['instance']}", ""]
        if job.status not in TERMINAL:
            lines += [f"{job.status.capitalize()}…", ""]
            continue
        result = job.result
        if job.status != "completed" or result.get("limitations"):
            lines += ["**Incomplete review**", ""]
        if result.get("summary"):
            lines += [md(result["summary"]), ""]
        if not result.get("findings") and job.status == "completed" and not result.get("limitations"):
            lines += ["**No findings.**", ""]
        for finding in result.get("findings", []):
            lines += [
                f"- **{md(finding['severity'].upper())}: {md(finding['title'])}** — {md(finding['explanation'])}"
            ]
            if finding.get("file"):
                from urllib.parse import quote

                path = finding["file"].lstrip("/")
                suffix = f"#L{finding['line']}" if finding.get("line") else ""
                lines += [
                    f"  [{md(path)}{':' + str(finding['line']) if finding.get('line') else ''}](https://github.com/{run.repo}/blob/{run.head}/{quote(path)}{suffix})"
                ]
        for limit in result.get("limitations", []):
            lines += [f"- Limitation: {md(limit)}"]
        lines += [""]
    lines += [f"[View run and screenshots]({settings().public_url}/runs/{run.id})"]
    return "\n".join(lines)[:60000]


async def publish(run_id):
    with Session() as db:
        run = db.get(Run, run_id)
        jobs = db.scalars(select(Job).where(Job.run_id == run_id)).all()
        done = all(j.status in TERMINAL for j in jobs)
        if run.published == 2 or (run.published == 1 and not done):
            return
    newer = False
    if done:
        info = (await github.repo_request(run.repo, run.installation, "GET", f"pulls/{run.pr}")).json()
        newer = info["head"]["sha"] != run.head
    body = render_comment(run, jobs, newer)
    if not run.comment_id:
        # Recover a successful POST whose response/DB commit was lost.
        page = 1
        while True:
            comments = (
                await github.repo_request(
                    run.repo, run.installation, "GET", f"issues/{run.pr}/comments?per_page=100&page={page}"
                )
            ).json()
            found = next(
                (
                    c
                    for c in comments
                    if f"<!-- litereview:{run.id} -->" in c.get("body", "")
                    and (c["user"].get("type") == "Bot" or settings().github_token)
                ),
                None,
            )
            if found:
                run.comment_id = found["id"]
                break
            if len(comments) < 100:
                break
            page += 1
    if run.comment_id:
        await github.repo_request(
            run.repo, run.installation, "PATCH", f"issues/comments/{run.comment_id}", json={"body": body}
        )
    else:
        result = await github.repo_request(
            run.repo, run.installation, "POST", f"issues/{run.pr}/comments", json={"body": body}
        )
        run.comment_id = result.json()["id"]
    with Session() as db:
        saved = db.get(Run, run_id)
        saved.comment_id = run.comment_id
        saved.published = 2 if done else 1
        saved.status = (
            "completed"
            if done and all(j.status == "completed" and not j.result.get("limitations") for j in jobs)
            else "incomplete"
            if done
            else "running"
        )
        saved.error = ""
        db.commit()


async def dispatcher():
    from .runtime import launch, terminate

    launching = {}
    while True:
        try:
            now = time.time()
            with Session() as db:
                active = db.scalars(select(Job).where(Job.status.in_(["starting", "running"]))).all()
                for job in active:
                    if job.started_at and now - job.started_at > settings().job_timeout + 300:
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
                db.commit()
                count = db.scalar(
                    select(func.count()).select_from(Job).where(Job.status.in_(["starting", "running"]))
                )
                pending = db.scalars(
                    select(Job).where(Job.status == "queued").limit(max(0, settings().concurrency - count))
                ).all()
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
                    task.result()
                    del launching[job_id]
        except asyncio.CancelledError:
            raise
        except Exception:
            import logging

            logging.getLogger("litereview").exception("Dispatcher iteration failed")
        await asyncio.sleep(3)
