"""Durable per-request model accounting, independent of optional tracing."""
import hashlib
import json
import logging
import time
from collections import defaultdict
from decimal import Decimal, InvalidOperation

import httpx
from sqlalchemy import case, func, select

from .db import Job, ModelUsage, Resource, Run, Session, uid

logger = logging.getLogger(__name__)
PRICE_KEYS = (
    "input_cost_per_token", "output_cost_per_token", "cache_read_input_token_cost",
    "cache_creation_input_token_cost",
)
_price_cache = {}


def amount(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and 0 <= result < Decimal("1000000000") else None
    except (InvalidOperation, ValueError, TypeError):
        return None


async def lookup_rates(connection, model):
    """Snapshot gateway rates; otherwise use the installed public price catalog."""
    from . import providers

    cache_key = (connection.get("id"), connection.get("base_url"), model,
                 hashlib.sha256(connection.get("api_key", "").encode()).hexdigest())
    cached = _price_cache.get(cache_key)
    if cached and cached[0] > time.time():
        return cached[1]
    info = None
    if connection["type"] == "gateway" and connection.get("base_url"):
        base = connection["base_url"].rstrip("/").removesuffix("/v1")
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                response = await client.get(base + "/model/info", headers={
                    "Authorization": "Bearer " + connection["api_key"],
                })
            if response.is_success:
                matches = [r.get("model_info", {}) for r in response.json().get("data", [])
                           if r.get("model_name") == model]
                # An alias with differently priced deployments has no single reliable rate.
                if matches and all({k: m.get(k) for k in PRICE_KEYS} ==
                                   {k: matches[0].get(k) for k in PRICE_KEYS} for m in matches):
                    info = matches[0]
        except (httpx.HTTPError, ValueError, AttributeError, TypeError):
            pass
    else:
        try:
            info = providers.litellm.get_model_info(model)
        except Exception:
            pass
    rates = None
    if isinstance(info, dict):
        rates = {key: amount(info.get(key)) for key in PRICE_KEYS}
        if rates[PRICE_KEYS[0]] is None or rates[PRICE_KEYS[1]] is None:
            rates = None
    if len(_price_cache) >= 256:
        _price_cache.pop(next(iter(_price_cache)))
    _price_cache[cache_key] = (time.time() + 600, rates)
    return rates


class UsageMeter:
    def __init__(self, job, rates=None):
        self.id = uid()
        self.rates = rates
        self.usage = {}
        self.reported = None
        self.buffer = bytearray()
        self.overflow = False
        self.ended = False
        self.final = False
        self.streaming = False
        self.protocol = ""
        self.enabled = True
        try:
            with Session.begin() as db:
                db.add(ModelUsage(id=self.id, job_id=job.id, model=job.config["model"]))
        except Exception:
            self.enabled = False
            logger.warning("Model accounting could not start; this request's cost is unavailable.")

    def result(self, data):
        if not isinstance(data, dict):
            return
        response = data.get("response") or data.get("message") or data
        if not isinstance(response, dict):
            return
        if data.get("type") in ("response.completed", "response.incomplete", "response.failed", "message_stop"):
            self.final = True
        if data.get("type", "").startswith("message_") or response.get("type") == "message":
            self.protocol = "anthropic"
        usage = response.get("usage") or data.get("usage")
        if not isinstance(usage, dict):
            return
        cost = amount(usage.get("cost"))
        if cost is not None:
            self.reported = cost
        for key, value in usage.items():
            if value is not None:
                self.usage[key] = value

    def _parse(self, raw):
        if raw.strip() == b"[DONE]":
            self.final = True
            return
        try:
            self.result(json.loads(raw))
        except (ValueError, TypeError, AttributeError, RecursionError):
            pass

    def feed(self, chunk, sse=False):
        self.streaming = self.streaming or sse
        for part in chunk.splitlines(keepends=True) if sse else (chunk,):
            if len(self.buffer) + len(part) > 4 * 1024 * 1024:
                self.overflow = True
                self.buffer.clear()
            if not self.overflow:
                self.buffer.extend(part)
            if sse and part.endswith(b"\n"):
                if not self.overflow and self.buffer.startswith(b"data:"):
                    self._parse(self.buffer[5:])
                self.buffer.clear()
                self.overflow = False

    def values(self):
        def tokens(*names):
            for name in names:
                value = self.usage.get(name)
                if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                    return value
            return None
        inp, out = tokens("input_tokens", "prompt_tokens"), tokens("output_tokens", "completion_tokens")
        details = self.usage.get("input_tokens_details") or self.usage.get("prompt_tokens_details") or {}
        cached = details.get("cached_tokens", 0) if isinstance(details, dict) else 0
        if not isinstance(cached, int) or isinstance(cached, bool) or cached < 0:
            cached = 0
        created = tokens("cache_creation_input_tokens") or 0
        anthropic = self.protocol == "anthropic" or "cache_read_input_tokens" in self.usage
        if anthropic:
            cached = tokens("cache_read_input_tokens") or 0
        cost, source = self.reported, "reported"
        if cost is None:
            source = "estimated"
            rates = self.rates
            if inp is not None and out is not None and rates and (anthropic or cached <= inp):
                read_rate = rates.get("cache_read_input_token_cost")
                write_rate = rates.get("cache_creation_input_token_cost")
                if (not cached or read_rate is not None) and (not created or write_rate is not None):
                    uncached = inp if anthropic else inp - cached
                    cost = (Decimal(uncached) * rates["input_cost_per_token"]
                            + Decimal(out) * rates["output_cost_per_token"]
                            + Decimal(cached) * (read_rate or 0) + Decimal(created) * (write_rate or 0))
        return {
            "input_tokens": inp, "output_tokens": out, "cached_tokens": cached if inp is not None else None,
            "cost_usd": cost, "source": source if cost is not None else "unavailable",
        }

    def end(self, complete=False):
        if self.ended or not self.enabled:
            return
        self.ended = True
        if self.buffer and not self.overflow:
            self._parse(self.buffer[5:] if self.buffer.startswith(b"data:") else self.buffer)
        self.buffer.clear()
        try:
            with Session.begin() as db:
                row = db.get(ModelUsage, self.id)
                if row:
                    for key, value in self.values().items():
                        setattr(row, key, value)
                    # A clean HTTP EOF can still truncate an SSE response after partial usage.
                    row.complete = int(self.final or (complete and not self.streaming))
        except Exception:
            logger.warning("Model accounting could not finish; this request's cost is unavailable.")


def job_costs(db, job_ids=None):
    statement = select(
        ModelUsage.job_id, func.sum(ModelUsage.cost_usd), func.count(ModelUsage.id),
        func.count(ModelUsage.cost_usd),
        func.sum(case(((ModelUsage.complete == 1) & ModelUsage.cost_usd.is_not(None), 1), else_=0)),
        func.sum(case((ModelUsage.source == "estimated", 1), else_=0)),
    ).group_by(ModelUsage.job_id)
    if job_ids is not None:
        statement = statement.where(ModelUsage.job_id.in_(job_ids))
    return {row[0]: row[1:] for row in db.execute(statement)}


def cost_summary(jobs, costs):
    total = Decimal(0)
    known = missing = estimated = 0
    for job in jobs:
        cost, recorded, priced, complete, estimates = costs.get(job.id, (None, 0, 0, 0, 0))
        total += cost or 0
        known += priced
        missing += max(job.model_calls, recorded) - (complete or 0)
        estimated += estimates or 0
    return {"cost_usd": float(total) if known or not missing else None,
            "complete": missing == 0, "estimated": bool(estimated), "unpriced_calls": missing}


def run_costs(db, runs):
    ids = [r.id for r in runs]
    jobs = db.execute(select(Job.id, Job.run_id, Job.model_calls).where(Job.run_id.in_(ids))).all()
    grouped = defaultdict(list)
    for job in jobs:
        grouped[job.run_id].append(job)
    costs = job_costs(db, [j.id for j in jobs])
    return {r.id: cost_summary(grouped[r.id], costs) for r in runs}


def dashboard(db):
    snapshot = func.coalesce(Run.display_snapshot, Run.snapshot)
    config = func.coalesce(Job.display_config, Job.config)
    runs = db.execute(select(Run.id, Run.actor, Run.repo, Run.pr, Run.status)
                      .where(snapshot["harness_check"].as_boolean().is_not(True),
                             snapshot["request_only"].as_boolean().is_not(True))
                      .order_by(Run.created_at.desc(), Run.id)).all()
    jobs = db.execute(select(Job.id, Job.run_id, Job.model_calls,
                             config["agent_id"].as_string().label("agent_id"),
                             config["name"].as_string().label("name"),
                             config["fusion_parent_id"].as_string().label("fusion_parent_id"))
                      .where(Job.run_id.in_([r.id for r in runs])).order_by(Job.id)).all()
    costs = job_costs(db, [j.id for j in jobs])
    jobs_by_run = defaultdict(list)
    jobs_by_id = {job.id: job for job in jobs}
    for job in jobs:
        jobs_by_run[job.run_id].append(job)
    run_summaries = {run.id: cost_summary(jobs_by_run[run.id], costs) for run in runs}
    users, repos, prs = defaultdict(list), defaultdict(list), defaultdict(list)
    agents, agent_runs, agent_costs = {}, defaultdict(list), defaultdict(dict)
    names = dict(db.execute(select(Resource.id, Resource.data["name"].as_string())
                            .where(Resource.kind == "agents")).all())
    for run in runs:
        users[run.actor.casefold()].append(run)
        repos[run.repo.casefold()].append(run)
        prs[(run.repo.casefold(), run.pr)].append(run)
        agent_jobs = defaultdict(list)
        for job in jobs_by_run[run.id]:
            parent = jobs_by_id.get(job.fusion_parent_id)
            source = parent if parent and parent.run_id == run.id else job
            agent_id, name = source.agent_id, source.name or "Unknown agent"
            key = agent_id or "legacy:" + name.casefold()
            agents.setdefault(key, {"id": key, "name": names.get(agent_id, name),
                                    "deleted": bool(agent_id and agent_id not in names)})
            agent_jobs[key].append(job)
        for key, grouped_jobs in agent_jobs.items():
            # Multiple instances/groups and all fusion stages contribute cost,
            # but each agent counts this PR run only once.
            agent_runs[key].append(run)
            agent_costs[key][run.id] = cost_summary(grouped_jobs, costs)

    def aggregate(rows, summaries):
        known = [summaries[r.id] for r in rows if summaries[r.id]["cost_usd"] is not None]
        complete = [summaries[r.id] for r in rows if summaries[r.id]["complete"]
                    and r.status in ("completed", "incomplete", "failed", "canceled", "superseded")]
        return {
            "runs": len(rows), "cost_usd": sum(c["cost_usd"] for c in known) if known or not rows else None,
            "average_cost_usd": sum(c["cost_usd"] or 0 for c in complete) / len(complete) if complete else None,
            "priced_runs": len(complete), "unpriced_runs": sum(not summaries[r.id]["complete"] for r in rows),
            "estimated": any(c["estimated"] for c in known),
        }
    def breakdown(groups):
        return sorted([{"name": name, **aggregate(rows, run_summaries)} for name, rows in groups.items()],
                      key=lambda row: (-(row["cost_usd"] or 0), row["name"]))
    agent_breakdown = sorted(
        [{**agents[key], **aggregate(rows, agent_costs[key])} for key, rows in agent_runs.items()],
        key=lambda row: (-(row["cost_usd"] or 0), row["name"].casefold(), row["id"]),
    )
    pull_requests = sorted([{"name": f"{rows[0].repo} #{number}", "repo": rows[0].repo, "pr": number,
                             **aggregate(rows, run_summaries)} for (_, number), rows in prs.items()],
                           key=lambda row: (-(row["cost_usd"] or 0), row["name"]))
    return {"total": aggregate(runs, run_summaries), "users": breakdown(users),
            "repositories": breakdown(repos), "agents": agent_breakdown, "pull_requests": pull_requests}
