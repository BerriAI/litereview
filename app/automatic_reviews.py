"""Repository-owned review rules evaluated against signed GitHub PR events."""

import hashlib
import json
import re
import time

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from . import github, review_sessions, reviews
from .access import permits
from .db import AutomaticReviewClaim, Resource, Run, Session, uid

EVENTS = {"opened", "ready_for_review", "synchronize", "reopened", "labeled"}


def matching_rules(repository, resources, payload, pr):
    if pr.get("state") != "open" or pr.get("draft"):
        return []
    author = pr.get("user", {}).get("login", "").lower()
    if not author:
        return []
    grant = next((r for r in resources.values() if r.kind == "access"
                  and r.data["subject"] == "repo"
                  and r.data["name"].lower() == repository.data["name"].lower()), None)
    action = payload["action"]
    matched = []
    for rule in repository.data.get("review_rules", []):
        if not rule["enabled"] or action not in rule["events"]:
            continue
        if rule["authors"] and author not in rule["authors"]:
            continue
        if action == "labeled" and (
            payload.get("label", {}).get("name", "").casefold() != rule["label"].casefold()
            or rule["label"].casefold() not in {x["name"].casefold() for x in pr.get("labels", [])}
        ):
            continue
        target = resources.get(rule["target_id"])
        if target and target.kind in ("agents", "groups") and permits(grant, target.id):
            matched.append((rule, target))
    return matched


def claim_key(repository_id, number, head, config):
    value = [repository_id, number, head, config["agent_id"], config["instance"]]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


async def accept(payload, delivery):
    requested_at = time.time()
    state = github.setup_state()
    action = payload.get("action")
    event_pr = payload.get("pull_request", {})
    head = event_pr.get("head", {}).get("sha", "")
    number = event_pr.get("number")
    repo = payload.get("repository", {}).get("full_name", "")
    installation = payload.get("installation", {}).get("id")
    if (state.get("disabled") or action not in EVENTS or not isinstance(number, int)
            or number <= 0 or not re.fullmatch(r"[a-fA-F0-9]{40}", head)):
        return None
    delivery_key = "auto:" + hashlib.sha256(delivery.encode()).hexdigest()
    with Session() as db:
        existing = db.scalar(select(Run).where(Run.delivery == delivery_key))
        if existing:
            return existing.id
        resources = {r.id: r for r in db.scalars(select(Resource))}
        repository = next((r for r in resources.values() if r.kind == "repos"
                           and r.data["name"].lower() == repo.lower()), None)
        if (not repository or repository.data["installation_id"] != installation
                or not matching_rules(repository, resources, payload, event_pr)):
            return None
    # Confirm state and authorship with GitHub, and ignore obsolete deliveries.
    pr = (await github.repo_request(repo, installation, "GET", f"pulls/{number}")).json()
    if pr.get("head", {}).get("sha") != head or not matching_rules(repository, resources, payload, pr):
        return None
    base = pr["base"]["sha"]
    diff = (await github.repo_request(repo, installation, "GET", f"compare/{base}...{head}",
                                    headers={"Accept": "application/vnd.github.diff"})).text
    # PostgreSQL serializes this repository; the unique claim also protects SQLite
    # and concurrent delivery races. No GitHub side effects happen in this path.
    for attempt in range(3):
        with review_sessions.transaction(repo, number) as db:
            github_row = db.get(Resource, "github", with_for_update=True)
            current = github_row.data if github_row else {}
            if current.get("disabled") or current.get("generation", "") != state.get("generation", ""):
                return None
            saved = db.get(Resource, repository.id, with_for_update=True)
            if not saved or saved.data["installation_id"] != installation or saved.data["name"].lower() != repo.lower():
                return None
            resources = {r.id: r for r in db.scalars(select(Resource))}
            matched = matching_rules(saved, resources, payload, pr)
            groups = []
            for rule, target in matched:
                group = {**target.data, "target_id": target.id,
                         "target_kind": "agent" if target.kind == "agents" else "group"}
                if target.kind == "agents":
                    group["members"] = [{"agent_id": target.id, "count": 1}]
                groups.append(group)
            configurations = {}
            for cfg in reviews.expand(groups, resources):
                key = claim_key(saved.id, number, head, cfg)
                configurations.setdefault(key, cfg)
            if not configurations:
                return None
            claims = {c.key: c for c in db.scalars(select(AutomaticReviewClaim).where(
                AutomaticReviewClaim.key.in_(configurations)
            ))}
            pending = {k: c for k, c in configurations.items() if k not in claims}
            if not pending:
                return next(iter(claims.values())).run_id
            run = Run(id=uid(), created_at=requested_at, delivery=delivery_key, repo=repo, pr=number,
                      actor=pr["user"]["login"], installation=installation, head=head, base=base,
                      snapshot={"trigger": {"type": "automatic", "event": action,
                                            "rule_ids": [r["id"] for r, _ in matched]},
                                "groups": groups, "repository": {"id": saved.id, **{
                                    k: v for k, v in saved.data.items() if k not in ("test_secrets", "review_rules")}},
                                "diff": diff, "title": pr["title"], "description": pr.get("body") or ""})
            for key in pending:
                db.add(AutomaticReviewClaim(key=key, run_id=run.id))
            review_sessions.enqueue(db, run, list(pending.values()))
            try:
                db.commit()
                return run.id
            except IntegrityError:
                db.rollback()
                existing = db.scalar(select(Run).where(Run.delivery == delivery_key))
                if existing:
                    return existing.id
                if attempt == 2:
                    raise
