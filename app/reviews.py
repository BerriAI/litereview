import asyncio
import re
import secrets
import time
from collections import Counter

import anyio
from fastapi import HTTPException
from markdown_it import MarkdownIt
from sqlalchemy import String, cast, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from runner.catalog import HARNESS_VERSIONS, review_capabilities, validate_connection

from . import fusion, github, review_defaults, review_sessions
from .access import allows_anyone, caller_grant, caller_permits, permits
from .db import Job, Resource, ReviewSession, Run, Session, uid
from .limits import job_timeout, timing_limits
from .security import digest
from .settings import settings

TERMINAL = review_sessions.TERMINAL
COMMAND = re.compile(r"(?<![\w@./-])@litereview(?![\w/-]|\.\w)|^light[ \t]+review(?![\w/-])", re.I)
UNAVAILABLE_COMMAND = (
    "This review command is unavailable for this repository or caller. "
    "Check the agent or group names and the permissions in People and Repositories."
)


def command(body):
    try:
        return [item["name"] for item in command_requests(body)]
    except ValueError:
        return []


def command_paragraphs(body):
    """Read ordinary Markdown prose without activating quoted or code examples."""
    quoted = 0
    for block in MarkdownIt("commonmark").parse(body or ""):
        if block.type == "blockquote_open":
            quoted += 1
        elif block.type == "blockquote_close":
            quoted -= 1
        elif block.type == "inline" and not quoted:
            parts, linked = [], 0
            literal_tag = None
            for token in block.children or []:
                if token.type == "html_inline":
                    tag = re.match(r"</?(code|pre|a|blockquote)(?:\s|>)", token.content, re.I)
                    if tag:
                        if token.content.startswith("</"):
                            if literal_tag == tag[1].lower():
                                literal_tag = None
                        elif literal_tag is None:
                            literal_tag = tag[1].lower()
                    parts.append("\0")
                elif literal_tag:
                    continue
                elif token.type == "link_open":
                    linked += 1
                    parts.append("\0")
                elif token.type == "link_close":
                    linked -= 1
                elif linked:
                    continue
                elif token.type == "text":
                    parts.append(token.content)
                elif token.type in ("softbreak", "hardbreak"):
                    parts.append(" ")
                elif token.type == "code_inline":
                    # Backticks may format a reviewer name after a real mention,
                    # but a command shown inside backticks must never run.
                    parts.append("\0" + ("" if COMMAND.search(token.content) else token.content) + "\0")
                elif token.type == "image":
                    parts.append("\0")
            yield "".join(parts)


def command_requests(body, names=None):
    """Select exact saved names within each command's paragraph; ignore prose."""
    selected, mentioned = {}, False
    for paragraph in command_paragraphs(body):
        matches = list(COMMAND.finditer(paragraph))
        for index, match in enumerate(matches):
            mentioned = True
            end = matches[index + 1].start() if index + 1 < len(matches) else len(paragraph)
            tail = paragraph[match.end():end].lower()
            if re.search(r"(?:^|[\s\0,;:(])t=", tail):
                raise ValueError(
                    "Set minimum review time in LiteReview → PR agents. "
                    "Select reviewers with @litereview name1 name2."
                )
            if not tail.strip(" \t\r\n.,:;!?"):
                selected[""] = {"name": ""}
                continue
            for word in re.split(r"[\s\0,;:!?()\[\]{}]+", tail):
                name = word.strip(".\"'")
                if re.fullmatch(r"[a-z][a-z0-9_-]*", name) and (names is None or name in names):
                    selected[name] = {"name": name}
    if mentioned and not selected:
        raise ValueError(UNAVAILABLE_COMMAND)
    return list(selected.values())


def is_command(body):
    try:
        return bool(command_requests(body))
    except ValueError:
        return True


def comment_run(db, repo, comment_id, delivery=None):
    keys = [delivery] if delivery else []
    if comment_id is not None:
        keys.append(f"comment:{comment_id}")
    # New deliveries use the stable GitHub comment ID.
    existing = db.scalar(select(Run.id).where(Run.delivery.in_(keys)).limit(1))
    if existing or comment_id is None:
        return existing
    # Older runs retain the original delivery GUID. Preserve their history
    # and recognize them when redelivering after an upgrade.
    return db.scalar(select(Run.id).where(
        func.lower(Run.repo) == repo.lower(),
        cast(Run.snapshot["trigger_comment_id"].as_string(), String) == str(comment_id),
    ).order_by(Run.created_at).limit(1))


def expand(groups, resources):
    jobs = []
    for group in groups:
        for member in group["members"]:
            agent = resources.get(member["agent_id"])
            if not agent or agent.kind != "agents":
                raise HTTPException(409, "A group references a missing agent.")
            data = agent.data.copy()
            data["capabilities"] = review_capabilities(data.get("capabilities", []))
            data["harness"] = "codex"
            data["harness_version"] = HARNESS_VERSIONS[data["harness"]]
            minutes = data.get("minimum_minutes", 0)
            data.update(timing_limits(minutes, data.get("maximum_minutes"), data.get("max_model_calls")))
            conn = resources.get(data["connection_id"])
            if not conn or conn.kind != "connections" or conn.data["type"] == "mcp":
                raise HTTPException(409, "An agent needs a model connection.")
            try:
                validate_connection("codex", conn.data["type"])
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from None
            data["connection"] = {"id": conn.id, **conn.data}
            data = fusion.snapshot_tools(data, resources)
            data = fusion.snapshot_config(data, resources)
            for instance in range(1, member["count"] + 1):
                jobs.append(
                    {
                        **data,
                        "agent_id": agent.id,
                        "group": group["name"],
                        "instance": instance,
                        "target_kind": group.get("target_kind", "group"),
                        "target_id": group.get("target_id"),
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
    requested_at = time.time()
    github_state = github.setup_state()
    if github_state.get("disabled"):
        return None
    if payload.get("action") != "created" or "pull_request" not in payload.get("issue", {}):
        return None
    comment_data = payload.get("comment", {})
    comment_id = comment_data.get("id")
    original_delivery = delivery
    if comment_id is not None:
        delivery = f"comment:{comment_id}"
    if comment_data.get("user", {}).get("type") == "Bot":
        return None
    body = comment_data.get("body", "")
    if not is_command(body):
        return None
    repo = payload["repository"]["full_name"]
    actor = comment_data["user"]["login"]
    pr = payload["issue"]["number"]
    installation = payload.get("installation", {}).get("id", 0)
    with Session() as db:
        existing = comment_run(db, repo, comment_id, original_delivery)
        if existing:
            return existing
        resources = {r.id: r for r in db.scalars(select(Resource)).all()}
    repos = [r for r in resources.values() if r.kind == "repos" and r.data["name"].lower() == repo.lower()]
    if not repos or repos[0].data["installation_id"] != installation:
        return None
    targets = {r.data["name"].lower(): r for r in resources.values() if r.kind in ("agents", "groups")}
    try:
        requests = command_requests(body, targets)
    except ValueError as exc:
        return reject_command(delivery, repo, pr, actor, installation, str(exc))
    requested_targets = [review_defaults.selected(resources) if not r["name"] else targets.get(r["name"])
                         for r in requests]
    restricted = any(not allows_anyone(target) for target in requested_targets)
    if restricted:
        perm = await github.permission(repo, installation, actor)
        if perm not in ("read", "triage", "write", "maintain", "admin"):
            return None
    identity = None
    if restricted and settings().sso_required:
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
                "once before requesting a review. Your linked GitHub account can then request reviews "
                "without signing in again.",
            )
    grants = {(r.data["subject"], r.data["name"]): r for r in resources.values() if r.kind == "access"}
    caller_access = caller_grant(resources.values(), actor, identity.sub if identity else None)
    repo_access = grants.get(("repo", repo.lower()))
    selected, selected_ids = [], set()
    for target in requested_targets:
        if not target:
            return reject_command(
                delivery, repo, pr, actor, installation,
                "Choose a default reviewer in LiteReview → PR agents, then comment @litereview again.",
            )
        group = target.data.copy() if target else None
        allowed = permits(repo_access, target.id) and (
            allows_anyone(target) or caller_permits(caller_access, target.id))
        if not allowed:
            # Generic rejection avoids exposing group membership to unauthorized readers.
            return reject_command(
                delivery,
                repo,
                pr,
                actor,
                installation,
                UNAVAILABLE_COMMAND,
            )
        if target.id in selected_ids:
            continue
        selected_ids.add(target.id)
        group["target_kind"] = "agent" if target.kind == "agents" else "group"
        group["target_id"] = target.id
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
        created_at=requested_at,
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
    with review_sessions.transaction(repo, pr) as db:
        existing = comment_run(db, repo, comment_id, original_delivery)
        if existing:
            return existing
        github_row = db.get(Resource, "github", with_for_update=True)
        state = github_row.data if github_row else {}
        if state.get("disabled") or state.get("generation", "") != github_state.get("generation", ""):
            return None
        review_sessions.enqueue(db, run, configurations)
    return run.id


def ordered_jobs(run, jobs):
    # Older accepted runs already retain command/group/member order in their
    # immutable snapshot. Reconstruct it without changing or restarting jobs.
    positions = {}
    for group in run.snapshot.get("groups", []):
        for member in group.get("members", []):
            for instance in range(1, member.get("count", 1) + 1):
                positions[(group["name"], member["agent_id"], instance)] = len(positions)

    def order(job):
        cfg = job.config
        position = cfg.get("run_order")
        if position is None:
            position = positions.get(
                (cfg.get("group"), cfg.get("agent_id"), cfg.get("instance", 1)), len(positions)
            )
        return position, cfg.get("instance", 1), job.id

    return sorted((job for job in jobs if not job.config.get("fusion_parent_id")
                   and not job.config.get("comparison_parent_id")), key=order)


def md(value):
    # Prevent model output from causing mentions or breaking attribution headings.
    value = str(value)
    lines = value.splitlines(keepends=True)
    tokens = MarkdownIt().parse(value)
    protected = set()
    for token in tokens:
        if token.type in ("fence", "code_block"):
            protected.update(range(*token.map))
    for index, line in enumerate(lines):
        if index not in protected:
            lines[index] = line.replace("@", "@\u200b").replace("<", "&lt;")
    # Token maps distinguish actual headings from fenced/indented code.
    for token in reversed(tokens):
        if token.type != "heading_open" or int(token.tag[1:]) > 3:
            continue
        start, end = token.map
        if token.markup.startswith("#"):
            lines[start] = re.sub(r"(?<!\S)#{1,3}(?=\s|$)", "####", lines[start], count=1)
        else:
            lines[start] = re.sub(r"^([ >\t]*(?:(?:[-+*]|\d+[.)])\s+)?)", r"\1#### ", lines[start], count=1)
            lines[end - 1] = "\n" if lines[end - 1].endswith("\n") else ""
    return "".join(lines)


def render_comment(run, jobs, newer=False, *, agent_comment=False, all_jobs=None, finding_links=None,
                   finding_decisions=None):
    if run.status == "rejected":
        return f"<!-- litereview:{run.id} -->\n### Review not started\n\n{md(run.snapshot['rejection'])}"
    marker = (
        f"<!-- litereview:{run.id}:job:{jobs[0].id} -->" if agent_comment else f"<!-- litereview:{run.id} -->"
    )
    lines = [marker, ""]
    if newer:
        lines += [f"> New commits were pushed. This review covers `{run.head[:12]}`.", ""]
    counts = Counter((j.config.get("group"), j.config["name"]) for j in (all_jobs or jobs))
    for job in jobs:
        c = job.config
        label = md(c["name"])
        if c.get("target_kind") != "agent":
            label += f" · {md(c['group'])}"
        if counts[(c.get("group"), c["name"])] > 1:
            label += f" · Run {c['instance']}"
        incomplete = job.status in TERMINAL and (job.status != "completed" or job.result.get("limitations"))
        icon = (("⚠️ " if incomplete else "✅ ") if job.status in TERMINAL else "👀 ") if agent_comment else ""
        lines += [f"### {icon}{label}", ""]
        if job.status not in TERMINAL:
            lines += ["Stopping this review for a newer commit." if job.status == "cancelling"
                      else "Waiting for this agent’s current work." if job.status == "blocked"
                      else "Waiting to start." if job.status == "queued"
                      else "Preparing the review sandbox." if job.status == "starting"
                      else "Review in progress.", ""]
            continue
        result = job.result
        if c.get("review_scope"):
            lines += [f"Scope: {c['review_scope']} · {c.get('scope_reason', '')}", ""]
        incomplete = job.status != "completed" or result.get("limitations")
        visible, notes = [], []
        if job.status == "superseded":
            visible += ["**Review superseded.**", ""]
        elif incomplete:
            visible += ["**Review incomplete.**", ""]
        elif any(check["status"] == "not_run" for check in result.get("checks", [])):
            visible += ["**Completed · Some checks not run.**", ""]
        if result.get("summary"):
            visible += [md(result["summary"]), ""]
        elif not incomplete and not result.get("findings"):
            visible += ["**No issues found.**", ""]
        for index, finding in enumerate(result.get("findings", [])):
            thread = (finding_links or {}).get(str(index))
            decision = (finding_decisions or {}).get(str(index), {})
            if decision.get("status") == "withdrawn":
                lines_title = f"~~{md(finding['title'])}~~ · Withdrawn after verification"
                visible += [f"- [{lines_title}]({thread})" if thread else f"- {lines_title}",
                            "", md(decision.get("reason", "")), ""]
                continue
            if thread:
                visible += [
                    f"- **{md(finding['severity'].capitalize())}** · [{md(finding['title'])}]({thread}) · Reply in thread",
                    "",
                ]
                continue
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
            for evidence in finding.get("evidence", []):
                visible += [md(evidence), ""]
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
            lines += [
                "<details>",
                "<summary>Checks and execution details</summary>",
                "",
                note_text,
                "",
                "</details>",
                "",
            ]
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
    root_marker = body.split("\n", 1)[0]
    remaining = body.removeprefix(root_marker)
    while remaining:
        number = len(parts) + 1
        marker = root_marker if number == 1 else root_marker.removesuffix(" -->") + f":part:{number} -->"
        prefix = marker + "\n"
        available = GITHUB_COMMENT_CHARACTERS - len(prefix)
        end = len(remaining) if len(remaining) <= available else remaining.rfind("\n", 0, available) + 1
        if end <= 0:
            end = available
        parts.append(prefix + remaining[:end])
        remaining = remaining[end:]
    return parts


async def publish(run_id):
    from .publication import publish_run

    await publish_run(run_id)


async def publish_one(run_id, reply=False):
    if reply:
        from . import rebuttals
        await rebuttals.publish_one(run_id)
        return
    try:
        with anyio.fail_after(30):
            await publish(run_id)
    except Exception as exc:
        with Session.begin() as db:
            db.get(Run, run_id).error = (
                exc.detail if isinstance(exc, HTTPException) else "GitHub publishing failed; will retry."
            )


async def dispatcher():
    from .runtime import cancel_review, launch, terminate, watch_existing

    launching = {}
    stopping = {}
    preparing = {}
    publishing = {}
    # Publishers hold a database connection for their GitHub-side-effect lock.
    # Keep connections available for the API and queue; sandbox launches stay uncapped.
    publication_slots = asyncio.Semaphore(4)

    async def publish_in_slot(run):
        async with publication_slots:
            await publish_one(run.id, bool(run.snapshot.get("reply")))

    try:
        while True:
            try:
                # GitHub work is independent per session/run. A slow comparison or
                # comment must never hold up sandbox launches for other PRs.
                with Session() as db:
                    sessions = db.scalars(select(ReviewSession).where(ReviewSession.active_run_id.is_not(None))).all()
                    for session in sessions:
                        if session.id not in preparing:
                            preparing[session.id] = asyncio.create_task(
                                review_sessions.prepare_one(session.repo, session.pr, session.id))
                now = time.time()
                with Session() as db:
                    fusion.advance(db)
                    db.commit()
                    for job in db.scalars(select(Job).where(Job.status == "cancelling")):
                        # An in-flight create must save its sandbox ID before we can
                        # confirm termination. After restart, recover it by name.
                        creating = job.id in launching and not launching[job.id].done()
                        if job.id not in stopping and (job.sandbox_id or not creating):
                            stopping[job.id] = asyncio.create_task(cancel_review(job.id))
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
                        .where(or_(Job.config["session_id"].as_string().is_(None),
                                   Job.config["session_prepared"].as_boolean().is_(True)))
                        .order_by(Run.created_at, Job.id)
                    )
                    if settings().concurrency:
                        count = db.scalar(
                            select(func.count()).select_from(Job).where(Job.status.in_(["starting", "running", "cancelling"]))
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
                    if run.id not in publishing:
                        publishing[run.id] = asyncio.create_task(publish_in_slot(run))
                for tasks in (launching, stopping, preparing, publishing):
                    for job_id, task in list(tasks.items()):
                        if task.done():
                            del tasks[job_id]
                            task.result()
            except asyncio.CancelledError:
                raise
            except Exception:
                import logging

                logging.getLogger("litereview").exception("Dispatcher iteration failed")
            await asyncio.sleep(3)
    finally:
        tasks = [task for group in (launching, stopping, preparing, publishing) for task in group.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
