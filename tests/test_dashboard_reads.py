"""Dashboard reads must not load or rewrite workers' immutable source bundles."""

from sqlalchemy import event, select, text

from app.db import Artifact, Job, Resource, Run, Session, engine, init_db


def seed():
    with Session.begin() as db:
        db.add(Resource(id="agent", kind="agents", data={"name": "review"}))
        for index in range(3):
            db.add(Run(id=f"run-{index}", delivery=f"delivery-{index}", repo="example/repo", pr=1,
                       actor="author", head="a" * 40, base="b" * 40, status="completed",
                       snapshot={"diff": "large-diff-" * 10000, "trigger": {"source": "comment"},
                                 "groups": [{"name": "group", "members": [{"agent_id": "agent", "count": 1}]}]}))
            db.add(Job(id=f"job-{index}", run_id=f"run-{index}", status="completed",
                       config={"agent_id": "agent", "name": "review", "group": "group", "instance": 1,
                               "model": "model", "session_id": "session", "review_scope": "incremental",
                               "instructions": "source-" * 10000, "skills": [{"files": {"x": "bundle"}}]},
                       result={"summary": "Finished", "findings": [], "limitations": []}))
        db.add(Artifact(id="artifact", job_id="job-2", name="proof.png", mime="image/png", content=b"binary" * 10000))


def test_compact_detail_preserves_results_and_worker_config(owner):
    seed()
    full = owner.get("/api/runs/run-2").json()
    compact = owner.get("/api/runs/run-2?compact=true").json()
    assert compact["trigger"] == full["trigger"]
    job = compact["jobs"][0]
    for field in ("result", "artifacts", "finding_states", "session_history", "spend"):
        assert job[field] == full["jobs"][0][field]
    assert job["config"]["review_scope"] == "incremental"
    assert "instructions" not in job["config"]
    assert "skills" not in job["config"]
    assert "instructions" in full["jobs"][0]["config"]  # Existing API/MCP clients retain the full response.
    with Session() as db:
        assert len(db.get(Job, "job-2").config["instructions"]) == 70000
        assert len(db.get(Run, "run-2").snapshot["diff"]) == 110000


def test_detail_queries_metadata_without_loading_artifact_bytes(owner):
    seed()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        assert owner.get("/api/runs/run-2?compact=true").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    artifact_queries = [sql for sql in statements if "FROM artifacts" in sql]
    assert len(artifact_queries) == 1
    assert "artifacts.content" not in artifact_queries[0]
    job_queries = [sql for sql in statements if "FROM jobs" in sql]
    assert all("jobs.config," not in sql for sql in job_queries)


def test_legacy_backfill_and_updates_preserve_usage_and_spend(owner):
    seed()
    before = owner.get("/api/spend").json()
    # Simulate a pre-migration deployment, including existing rows with NULL projections.
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE runs DROP COLUMN display_snapshot"))
        connection.execute(text("ALTER TABLE jobs DROP COLUMN display_config"))
    init_db()
    init_db()  # Safe on subsequent boots.
    with Session.begin() as db:
        run = db.get(Run, "run-1")
        run.snapshot = {**run.snapshot, "notices": ["A valid rebuttal"], "reply": {"comment_id": 123}}
        job = db.get(Job, "job-1")
        job.config = {**job.config, "review_scope": "full"}
    assert owner.get("/api/resources/agents").json()[0]["usage_count"] == 2
    assert owner.get("/api/spend").json() == before
    detail = owner.get("/api/runs/run-1?compact=true").json()
    assert detail["notices"] == ["A valid rebuttal"]
    assert detail["jobs"][0]["config"]["review_scope"] == "full"
    with Session() as db:
        assert db.scalar(select(Job.display_config).where(Job.id == "job-1"))["review_scope"] == "full"
        assert len(db.get(Job, "job-1").config["instructions"]) == 70000


def test_null_projection_fallback_and_pagination(owner):
    seed()
    # A rolling deployment may still receive inserts from an older instance.
    with engine.begin() as connection:
        connection.execute(text("UPDATE runs SET display_snapshot = NULL"))
        connection.execute(text("UPDATE jobs SET display_config = NULL"))
    first = owner.get("/api/runs?limit=2").json()
    last = owner.get("/api/runs?limit=2&offset=2").json()
    assert first["total"] == last["total"] == 3
    assert len(first["items"]) == 2 and len(last["items"]) == 1
    assert first["has_more"] and not last["has_more"]
    assert owner.get("/api/resources/agents").json()[0]["usage_count"] == 3
    assert owner.get("/api/runs/run-2?compact=true").status_code == 200


def test_workspace_bootstrap_is_authenticated(client, owner):
    assert owner.get("/api/workspace").status_code == 200
    owner.post("/api/logout")
    assert client.get("/api/workspace").status_code == 401


def test_run_search_filters_before_pagination(owner):
    seed()
    with Session.begin() as db:
        db.get(Run, "run-0").repo = "example/needle"
    found = owner.get("/api/runs?limit=1&query=NEEDLE").json()
    assert found["total"] == 1 and found["items"][0]["id"] == "run-0"
    assert owner.get("/api/runs?limit=1&query=%25").json()["total"] == 0


async def test_running_session_poll_does_not_read_skill_bundles(owner):
    from app import review_sessions
    from app.db import ReviewSession

    seed()
    with Session.begin() as db:
        db.add(ReviewSession(id="session", repo="example/repo", pr=1, agent_id="agent", active_run_id="run-2"))
        job = db.get(Job, "job-2")
        job.status = "running"
        job.config = {**job.config, "session_prepared": True}
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        await review_sessions.prepare_session("example/repo", 1, "session")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert not any("jobs.config," in sql or "SELECT jobs.config " in sql for sql in statements)
    with Session() as db:
        assert len(db.get(Job, "job-2").config["instructions"]) == 70000


def test_fusion_poll_keeps_full_worker_config_available(owner):
    from types import SimpleNamespace

    from app import fusion

    seed()
    with Session.begin() as db:
        parent = db.get(Job, "job-0")
        parent.status = "waiting"
        for index in (1, 2):
            child = db.get(Job, f"job-{index}")
            child.run_id = parent.run_id
            child.config = {**child.config, "fusion_parent_id": parent.id, "fusion_index": index - 1}
    with Session.begin() as db:
        fusion.advance(db)
        # Polling must not replace the ORM's immutable config with metadata.
        reports = fusion.reports(db, SimpleNamespace(id="job-0", run_id="run-0"))
        assert len(reports) == 2
        assert all(len(report["instructions"]) == 70000 for report in reports)
    with Session() as db:
        assert db.get(Job, "job-0").status == "queued"
        assert len(db.get(Job, "job-0").config["instructions"]) == 70000
