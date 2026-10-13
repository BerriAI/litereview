import json
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest

from app import harnesses, spend
from app.db import Job, ModelUsage, Resource, Run, Session

lookup_rates = spend.lookup_rates
RATES = {"input_cost_per_token": Decimal("0.000002"),
         "output_cost_per_token": Decimal("0.000010"),
         "cache_read_input_token_cost": Decimal("0.0000001"),
         "cache_creation_input_token_cost": Decimal("0.0000025")}


def add_run(run_id, actor="moe", repo="BerriAI/litellm", status="completed", check=False):
    with Session.begin() as db:
        run = Run(id=run_id, delivery=run_id, actor=actor, repo=repo, pr=1,
                  head="a" * 40, base="b" * 40, status=status, snapshot={"harness_check": check})
        db.add(run)
    return run


def add_job(job_id, run_id, calls=1, **config):
    with Session.begin() as db:
        job = Job(id=job_id, run_id=run_id, status="completed", model_calls=calls,
                  config={"name": job_id, "model": "test-model", **config})
        db.add(job)
    return job


def charge(job, cost, complete=True):
    meter = spend.UsageMeter(job)
    meter.result({"usage": {"input_tokens": 100, "output_tokens": 10, "cost": cost}})
    meter.end(complete=complete)
    return meter


def test_fragmented_stream_is_metered_once_without_tracing(owner):
    add_run("one")
    job = add_job("one-job", "one")
    meter = spend.UsageMeter(job, RATES)
    event = b'data: ' + json.dumps({"type": "response.completed", "response": {"usage": {
        "input_tokens": 1000, "output_tokens": 50, "input_tokens_details": {"cached_tokens": 900},
    }}}).encode() + b'\r\n\r\ndata: [DONE]\n\n'
    for offset in range(0, len(event), 7):
        meter.feed(event[offset:offset + 7], sse=True)
    meter.end(complete=True)
    meter.end(complete=True)
    with Session() as db:
        rows = db.query(ModelUsage).all()
        assert len(rows) == 1
        assert rows[0].cost_usd == Decimal("0.000790")
        assert rows[0].cached_tokens == 900
        assert rows[0].source == "estimated"
    assert owner.get("/api/runs/one").json()["spend"] == {
        "cost_usd": 0.00079, "complete": True, "estimated": True, "unpriced_calls": 0,
    }


def test_anthropic_usage_is_cumulative_and_separates_cache_writes(owner):
    add_run("anthropic")
    meter = spend.UsageMeter(add_job("anthropic-job", "anthropic"), RATES)
    meter.result({"type": "message_start", "message": {"usage": {
        "input_tokens": 100, "output_tokens": 1, "cache_read_input_tokens": 200,
        "cache_creation_input_tokens": 40,
    }}})
    meter.result({"type": "message_delta", "usage": {"output_tokens": 20}})
    meter.result({"type": "message_stop"})
    meter.end()
    with Session() as db:
        row = db.get(ModelUsage, meter.id)
        assert row.cost_usd == Decimal("0.000520")
        assert row.output_tokens == 20
        assert row.complete == 1


def test_clean_http_eof_without_terminal_event_keeps_stream_cost_partial(owner):
    add_run("truncated")
    meter = spend.UsageMeter(add_job("truncated-job", "truncated"), RATES)
    meter.feed(b'data: {"type":"message_start","message":{"usage":{"input_tokens":100,"output_tokens":1}}}\n\n', sse=True)
    meter.end(complete=True)
    data = owner.get("/api/spend").json()["total"]
    assert data["cost_usd"] == pytest.approx(.00021)
    assert data["unpriced_runs"] == 1
    assert data["priced_runs"] == 0
    assert data["average_cost_usd"] is None


@pytest.mark.parametrize("cost", [0, 0.125])
def test_reported_cost_overrides_token_estimate_including_zero(owner, cost):
    add_run("reported")
    meter = spend.UsageMeter(add_job("reported-job", "reported"), RATES)
    meter.feed(json.dumps({"usage": {"input_tokens": 100, "output_tokens": 20, "cost": cost}}).encode())
    meter.end(complete=True)
    with Session() as db:
        row = db.get(ModelUsage, meter.id)
        assert row.cost_usd == Decimal(str(cost))
        assert row.source == "reported"


def test_spend_groups_runs_and_counts_all_fusion_stages_once(owner):
    add_run("fusion", actor="Moe")
    parent = add_job("parent", "fusion", fusion={"branches": [{}, {}]})
    left = add_job("left", "fusion", fusion_parent_id=parent.id, fusion_index=0)
    right = add_job("right", "fusion", fusion_parent_id=parent.id, fusion_index=1)
    for job, cost in [(parent, ".2"), (left, ".3"), (right, ".5")]:
        charge(job, cost)
    add_run("failed", actor="moe", status="incomplete")
    charge(add_job("failed-job", "failed"), ".4")
    add_run("other", actor="alex", repo="BerriAI/docs")
    charge(add_job("other-job", "other"), ".6")
    add_run("test", check=True)
    charge(add_job("test-job", "test"), "99")
    data = owner.get("/api/spend").json()
    assert data["total"]["runs"] == 3
    assert data["total"]["cost_usd"] == 2
    assert data["total"]["average_cost_usd"] == pytest.approx(2 / 3)
    assert data["users"][0]["name"] == "moe"
    assert data["users"][0]["runs"] == 2
    assert data["users"][0]["cost_usd"] == 1.4
    assert data["repositories"][0]["runs"] == 2
    detail = owner.get("/api/runs/fusion").json()
    assert detail["spend"]["cost_usd"] == 1
    assert len(detail["jobs"]) == 1
    assert detail["jobs"][0]["spend"]["cost_usd"] == 1
    assert len(owner.get("/api/runs").json()) == 3


def test_unknown_historical_and_interrupted_costs_are_not_zero_or_averaged(owner):
    add_run("old")
    add_job("old-job", "old", calls=10)
    add_run("partial")
    job = add_job("partial-job", "partial", calls=2)
    charge(job, ".2", complete=False)
    meter = spend.UsageMeter(job)
    meter.end(complete=True)  # Successful request without usage or rates.
    add_run("complete")
    charge(add_job("complete-job", "complete"), ".8")
    data = owner.get("/api/spend").json()["total"]
    assert data["cost_usd"] == 1
    assert data["unpriced_runs"] == 2
    assert data["priced_runs"] == 1
    assert data["average_cost_usd"] == .8
    assert owner.get("/api/runs/old").json()["spend"]["cost_usd"] is None
    assert owner.get("/api/runs/partial").json()["spend"]["unpriced_calls"] == 2


def test_spend_requires_login_and_empty_workspace_is_zero(client, owner):
    assert owner.get("/api/spend").json()["total"]["cost_usd"] == 0
    assert owner.get("/api/spend").json()["agents"] == []
    owner.post("/api/logout")
    assert client.get("/api/spend").status_code == 401


def test_pr_spend_combines_retries_and_fusion_and_retains_superseded_cost(owner):
    add_run("first")
    charge(add_job("main", "first"), ".5")
    charge(add_job("branch", "first", fusion_parent_id="main"), ".2")
    add_run("retry", status="superseded")
    charge(add_job("retry-job", "retry"), ".3")
    with Session.begin() as db:
        db.get(Job, "retry-job").status = "superseded"
    add_run("other-pr")
    with Session.begin() as db:
        db.get(Run, "other-pr").pr = 2
    charge(add_job("other-pr-job", "other-pr"), ".1")
    add_run("other-repo", repo="BerriAI/lens")
    charge(add_job("other-repo-job", "other-repo"), ".4")
    add_run("duplicate")
    with Session.begin() as db:
        db.get(Run, "duplicate").snapshot = {"request_only": True}
    data = owner.get("/api/spend").json()
    row = next(r for r in data["pull_requests"] if r["repo"] == "BerriAI/litellm" and r["pr"] == 1)
    assert row["runs"] == 2 and row["cost_usd"] == pytest.approx(1)
    assert len(data["pull_requests"]) == 3
    assert sum(r["cost_usd"] for r in data["pull_requests"]) == pytest.approx(data["total"]["cost_usd"])
    filtered = owner.get("/api/runs?repo=BerriAI/litellm&pr=2").json()
    assert [r["id"] for r in filtered] == ["other-pr"]


def test_agent_spend_includes_instances_groups_and_fusion_without_double_counting(owner):
    with Session.begin() as db:
        db.add(Resource(id="agent-a", kind="agents", data={"name": "renamed-reviewer"}))
        db.add(Resource(id="agent-b", kind="agents", data={"name": "second-reviewer"}))
    add_run("shared")
    parent = add_job("parent", "shared", agent_id="agent-a", name="old-name", group="group-one")
    # Children inherit attribution from their parent even in old snapshots.
    left = add_job("left", "shared", fusion_parent_id=parent.id)
    right = add_job("right", "shared", fusion_parent_id=parent.id)
    repeated = add_job("repeated", "shared", agent_id="agent-a", name="old-name", group="group-two", instance=2)
    other = add_job("other", "shared", agent_id="agent-b", name="second-reviewer")
    for job, cost in [(parent, ".2"), (left, ".3"), (right, ".5"), (repeated, "1"), (other, ".4")]:
        charge(job, cost)
    add_run("again", status="failed")
    charge(add_job("again-a", "again", agent_id="agent-a", name="renamed-reviewer"), "1")
    add_run("private-check", check=True)
    charge(add_job("check", "private-check", agent_id="agent-a"), "99")
    data = owner.get("/api/spend").json()
    assert data["total"]["runs"] == 2
    assert data["total"]["cost_usd"] == pytest.approx(3.4)
    assert data["agents"] == [
        {"id": "agent-a", "name": "renamed-reviewer", "deleted": False, "runs": 2, "cost_usd": 3,
         "average_cost_usd": 1.5, "priced_runs": 2, "unpriced_runs": 0, "estimated": False},
        {"id": "agent-b", "name": "second-reviewer", "deleted": False, "runs": 1, "cost_usd": .4,
         "average_cost_usd": .4, "priced_runs": 1, "unpriced_runs": 0, "estimated": False},
    ]
    assert sum(row["cost_usd"] for row in data["agents"]) == pytest.approx(data["total"]["cost_usd"])


def test_agent_spend_preserves_deleted_agents_partial_costs_and_distinct_identities(owner):
    add_run("old")
    add_job("unknown", "old", agent_id="deleted", name="reviewer", calls=3)
    add_run("partial")
    charge(add_job("partial", "partial", agent_id="deleted", name="reviewer"), ".2", complete=False)
    add_run("new")
    charge(add_job("new", "new", agent_id="new-id", name="reviewer"), ".8")
    add_run("running", status="running")
    charge(add_job("running", "running", agent_id="new-id", name="reviewer"), ".4")
    rows = {row["id"]: row for row in owner.get("/api/spend").json()["agents"]}
    assert rows["deleted"]["deleted"] is True
    assert rows["deleted"]["runs"] == 2
    assert rows["deleted"]["cost_usd"] == .2
    assert rows["deleted"]["unpriced_runs"] == 2
    assert rows["deleted"]["average_cost_usd"] is None
    assert rows["new-id"]["cost_usd"] == pytest.approx(1.2)
    assert rows["new-id"]["average_cost_usd"] == .8
    assert rows["new-id"]["priced_runs"] == 1


def test_agent_cost_completeness_is_independent_of_other_agents_in_run(owner):
    add_run("mixed")
    charge(add_job("priced", "mixed", agent_id="priced"), "1")
    add_job("unpriced", "mixed", agent_id="unpriced")
    meter = spend.UsageMeter(add_job("estimate", "mixed", agent_id="estimate"), RATES)
    meter.result({"usage": {"input_tokens": 100, "output_tokens": 10}})
    meter.end(complete=True)
    rows = {row["id"]: row for row in owner.get("/api/spend").json()["agents"]}
    assert rows["priced"]["average_cost_usd"] == 1
    assert rows["unpriced"]["cost_usd"] is None
    assert rows["estimate"]["estimated"] is True


@pytest.mark.parametrize("value", [True, -1, "NaN", "Infinity", {}, "oops"])
def test_bad_cost_values_never_become_spend(value):
    assert spend.amount(value) is None


@pytest.mark.asyncio
async def test_gateway_pricing_uses_alias_and_caches_safe_rates(monkeypatch):
    spend._price_cache.clear()
    actual = httpx.AsyncClient
    calls = []
    async def upstream(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"model_name": "my-alias", "model_info": {
            "input_cost_per_token": .000002, "output_cost_per_token": .00001,
            "cache_read_input_token_cost": .0000001,
        }}]})
    monkeypatch.setattr(spend.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(upstream)))
    connection = {"id": "gateway", "type": "gateway", "base_url": "https://gateway.test/v1", "api_key": "secret"}
    rates = await lookup_rates(connection, "my-alias")
    assert rates["input_cost_per_token"] == Decimal("0.000002")
    assert await lookup_rates(connection, "my-alias") == rates
    assert len(calls) == 1
    assert str(calls[0].url) == "https://gateway.test/model/info"
    assert "secret" not in str(spend._price_cache)


def test_native_relay_persists_cost_without_changing_response(client, monkeypatch):
    from test_harnesses import seed_job
    seed_job()
    monkeypatch.setattr(spend, "lookup_rates", AsyncMock(return_value=RATES))
    monkeypatch.setattr(harnesses.providers, "snapshot_connection", lambda _: {
        "type": "gateway", "base_url": "https://gateway.test", "api_key": "secret",
    })
    actual = httpx.AsyncClient
    payload = b'data: {"type":"response.completed","response":{"usage":{"input_tokens":100,"output_tokens":10,"cost":0.42}}}\n\ndata: [DONE]\n\n'
    async def upstream(request):
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=payload)
    monkeypatch.setattr(harnesses.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(upstream)))
    response = client.post("/api/internal/jobs/test-job/llm/responses", json={"stream": True},
                           headers={"authorization": "Bearer job-token"})
    assert response.content == payload
    with Session() as db:
        row = db.query(ModelUsage).one()
        assert row.cost_usd == Decimal(".42")
        assert row.complete == 1


@pytest.mark.parametrize("stream,expected,source", [(False, ".09", "reported"), (True, ".0003", "estimated")])
def test_gateway_header_cost_is_used_only_after_nonstream_response(client, monkeypatch, stream, expected, source):
    from test_harnesses import seed_job
    seed_job()
    monkeypatch.setattr(spend, "lookup_rates", AsyncMock(return_value=RATES))
    monkeypatch.setattr(harnesses.providers, "snapshot_connection", lambda _: {
        "type": "gateway", "base_url": "https://gateway.test", "api_key": "secret",
    })
    actual = httpx.AsyncClient
    body = json.dumps({"usage": {"input_tokens": 100, "output_tokens": 10}}).encode()
    async def upstream(request):
        return httpx.Response(200, headers={
            "content-type": "text/event-stream" if stream else "application/json",
            "x-litellm-response-cost": "0" if stream else ".09",
        }, content=b"data: " + body + b"\n\ndata: [DONE]\n\n" if stream else body)
    monkeypatch.setattr(harnesses.httpx, "AsyncClient", lambda **kwargs: actual(transport=httpx.MockTransport(upstream)))
    response = client.post("/api/internal/jobs/test-job/llm/responses", json={"stream": stream},
                           headers={"authorization": "Bearer job-token"})
    assert response.status_code == 200
    with Session() as db:
        row = db.query(ModelUsage).one()
        assert row.cost_usd == Decimal(expected)
        assert row.source == source


def test_pr_spend_is_separate_for_each_requesting_user(owner):
    add_run("moe-first", actor="Moe")
    charge(add_job("moe-main", "moe-first"), ".5")
    charge(add_job("moe-branch", "moe-first", fusion_parent_id="moe-main"), ".2")
    add_run("moe-retry", actor="moe", status="superseded")
    charge(add_job("moe-retry-job", "moe-retry"), ".3")
    add_run("tin-first", actor="tin")
    charge(add_job("tin-job", "tin-first"), ".4")
    data = owner.get("/api/spend").json()
    rows = {r["actor"]: r for r in data["user_pull_requests"]}
    assert rows["moe"]["runs"] == 2 and rows["moe"]["cost_usd"] == pytest.approx(1)
    assert rows["tin"]["runs"] == 1 and rows["tin"]["cost_usd"] == pytest.approx(.4)
    users = {r["name"]: r for r in data["users"]}
    assert users["moe"]["pull_requests"] == 1 and users["moe"]["average_runs_per_pr"] == 2
    assert users["moe"]["average_pr_cost_usd"] == pytest.approx(1)
    assert users["tin"]["average_pr_cost_usd"] == pytest.approx(.4)
    assert sum(r["cost_usd"] for r in rows.values()) == pytest.approx(data["pull_requests"][0]["cost_usd"])
    assert {r["id"] for r in owner.get("/api/runs?repo=BerriAI/litellm&pr=1&actor=MOE").json()} == {"moe-first", "moe-retry"}
