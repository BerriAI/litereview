import hashlib
import time
import uuid
from pathlib import Path

from sqlalchemy import (
    JSON,
    BigInteger,
    Float,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    Text,
    create_engine,
    event,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from sqlalchemy.schema import CreateIndex

from .settings import settings


def uid():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class Resource(Base):
    __tablename__ = "resources"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    secret: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[float] = mapped_column(Float, default=time.time)


review_target_names = Index(
    "uq_review_target_names",
    func.lower(Resource.data["name"].as_string()),
    unique=True,
    sqlite_where=Resource.kind.in_(["agents", "groups"]),
    postgresql_where=Resource.kind.in_(["agents", "groups"]),
)


access_names = Index(
    "uq_access_names",
    func.lower(Resource.data["name"].as_string()),
    unique=True,
    sqlite_where=Resource.kind == "access",
    postgresql_where=Resource.kind == "access",
)


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    delivery: Mapped[str] = mapped_column(String(150), unique=True)
    repo: Mapped[str] = mapped_column(String(250))
    pr: Mapped[int] = mapped_column(Integer)
    actor: Mapped[str] = mapped_column(String(100))
    head: Mapped[str] = mapped_column(String(40))
    base: Mapped[str] = mapped_column(String(40))
    installation: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(30), default="queued")
    snapshot: Mapped[dict] = mapped_column(JSON)
    display_snapshot: Mapped[dict | None] = mapped_column(JSON, nullable=True, deferred=True)
    comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class RepositorySnapshot(Base):
    __tablename__ = "repository_snapshots"
    repository_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")
    requested_fingerprint: Mapped[str] = mapped_column(String(64), default="")
    image_id: Mapped[str] = mapped_column(String(100), default="")
    source_sha: Mapped[str] = mapped_column(String(40), default="")
    built_at: Mapped[float] = mapped_column(Float, default=0)
    expires_at: Mapped[float] = mapped_column(Float, default=0)
    retry_after: Mapped[float] = mapped_column(Float, default=0)
    lease_until: Mapped[float] = mapped_column(Float, default=0)
    build_token: Mapped[str] = mapped_column(String(40), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    config: Mapped[dict] = mapped_column(JSON)
    display_config: Mapped[dict | None] = mapped_column(JSON, nullable=True, deferred=True)
    token_hash: Mapped[str] = mapped_column(String(64), default="")
    sandbox_id: Mapped[str] = mapped_column(String(100), default="")
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    logs: Mapped[list] = mapped_column(JSON, default=list)


# Keep dashboard reads independent of immutable source, diffs and skill bundles.
# These projections never replace the worker's original snapshot.
JOB_DISPLAY_KEYS = (
    "agent_id", "name", "group", "instance", "model", "run_order", "target_kind", "target_id",
    "fusion_parent_id", "fusion_index", "fusion_stage", "fusion_role", "branch_decision", "comparison_parent_id", "session_id",
    "review_scope", "scope_reason", "output_type", "show_score", "finding_history", "task",
    "timeout_seconds", "review_fingerprint",
)


def run_display(snapshot):
    return {**{k: snapshot[k] for k in ("trigger", "notices", "harness_check", "request_only") if k in snapshot},
            "reply": True if snapshot.get("reply") is not None else None,
            "groups": [{"name": g.get("name"), "members": g.get("members", [])}
                       for g in snapshot.get("groups", [])]}


def job_display(config):
    return {**{k: config[k] for k in JOB_DISPLAY_KEYS if k in config}, "fusion": bool(config.get("fusion")),
            "session_prepared": bool(config.get("session_prepared")),
            "timeout_seconds": config.get("timeout_seconds")}


job_metadata = func.coalesce(Job.display_config, Job.config)
run_metadata = func.coalesce(Run.display_snapshot, Run.snapshot)


@event.listens_for(Run, "before_insert")
@event.listens_for(Run, "before_update")
def project_run(mapper, connection, row):
    if "snapshot" in row.__dict__:
        row.display_snapshot = run_display(row.snapshot)


@event.listens_for(Job, "before_insert")
@event.listens_for(Job, "before_update")
def project_job(mapper, connection, row):
    if "config" in row.__dict__:
        row.display_config = job_display(row.config)


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    name: Mapped[str] = mapped_column(Text)
    content: Mapped[bytes] = mapped_column(LargeBinary)
    mime: Mapped[str] = mapped_column(String(80), default="image/png")


class ModelUsage(Base):
    __tablename__ = "model_usage"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    model: Mapped[str] = mapped_column(String(250))
    cost_usd = mapped_column(Numeric(24, 12), nullable=True)
    source: Mapped[str] = mapped_column(String(30), default="unavailable")
    input_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    cached_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    complete: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class ReviewPublication(Base):
    __tablename__ = "review_publications"
    # Separate run/agent records checkpoint GitHub side effects for restart recovery.
    key: Mapped[str] = mapped_column(String(90), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, default=dict)


class ReviewFeedback(Base):
    __tablename__ = "review_feedback"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    reporter: Mapped[str] = mapped_column(String(320))
    rating: Mapped[str] = mapped_column(String(10), index=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float)
    updated_at: Mapped[float] = mapped_column(Float, index=True)


class AutomaticReviewClaim(Base):
    __tablename__ = "automatic_review_claims"
    # Atomic with Run/Job insertion; survives redeliveries and overlapping rules.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)


class ReviewSession(Base):
    __tablename__ = "review_sessions"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    repo: Mapped[str] = mapped_column(String(250))
    pr: Mapped[int] = mapped_column(Integer)
    agent_id: Mapped[str] = mapped_column(String(40), index=True)
    active_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pending_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    baseline_run_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    fingerprint: Mapped[str] = mapped_column(String(64), default="")


class ReviewFinding(Base):
    __tablename__ = "review_findings"
    id: Mapped[str] = mapped_column(String(90), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    job_id: Mapped[str] = mapped_column(String(40), index=True)
    position: Mapped[int] = mapped_column(Integer)
    comment_id: Mapped[int | None] = mapped_column(BigInteger, unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="open")
    decision: Mapped[dict] = mapped_column(JSON, default=dict)


class ReviewTiming(Base):
    __tablename__ = "review_timing"
    job_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    started_at: Mapped[float] = mapped_column(Float, default=time.time)


class SSOIdentity(Base):
    __tablename__ = "sso_identities"
    sub: Mapped[str] = mapped_column(String(255), primary_key=True)
    email: Mapped[str] = mapped_column(String(320))
    domain: Mapped[str] = mapped_column(String(255))
    audience: Mapped[str] = mapped_column(String(255))
    # Legacy single-session fields; startup moves their value to sso_sessions.
    expires_at: Mapped[float] = mapped_column(Float, default=0)
    session_id: Mapped[str] = mapped_column(String(64), default="")
    github_id: Mapped[str | None] = mapped_column(String(40), unique=True, nullable=True)
    github_login: Mapped[str] = mapped_column(String(100), default="")


class SSOSession(Base):
    __tablename__ = "sso_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    identity_sub: Mapped[str] = mapped_column(String(255), index=True)
    audience: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class WorkspaceAdmin(Base):
    __tablename__ = "workspace_admins"
    identity_sub: Mapped[str] = mapped_column(String(255), primary_key=True)
    granted_by: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class LoginState(Base):
    __tablename__ = "login_states"
    state_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    browser_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[float] = mapped_column(Float)
    data: Mapped[dict] = mapped_column(JSON)


class MCPOAuth(Base):
    __tablename__ = "mcp_oauth"
    connection_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    revision: Mapped[str] = mapped_column(String(40), default=uid)
    secret: Mapped[str] = mapped_column(Text, default="")


class MCPClient(Base):
    __tablename__ = "mcp_clients"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    data: Mapped[dict] = mapped_column(JSON)
    secret: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class MCPAuthorization(Base):
    __tablename__ = "mcp_authorizations"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    client_id: Mapped[str] = mapped_column(String(100), index=True)
    data: Mapped[dict] = mapped_column(JSON)
    expires_at: Mapped[float] = mapped_column(Float, index=True)


class MCPGrant(Base):
    __tablename__ = "mcp_grants"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    identity_sub: Mapped[str] = mapped_column(String(255), index=True)
    client_id: Mapped[str] = mapped_column(String(100), index=True)
    scopes: Mapped[list] = mapped_column(JSON)
    resource: Mapped[str] = mapped_column(Text)
    audience: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[float] = mapped_column(Float, default=time.time)
    expires_at: Mapped[float] = mapped_column(Float)
    revoked: Mapped[bool] = mapped_column(default=False)


class MCPToken(Base):
    __tablename__ = "mcp_tokens"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    grant_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    expires_at: Mapped[float] = mapped_column(Float)
    used: Mapped[bool] = mapped_column(default=False)


url = settings().database_url
if url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)
if url.startswith("sqlite"):
    Path("data").mkdir(exist_ok=True)
engine = create_engine(
    url, pool_pre_ping=True, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}
)
Session = sessionmaker(engine, expire_on_commit=False)


def init_db():
    Base.metadata.create_all(engine)
    # Additive migration: old API instances and existing Modal workers remain compatible.
    for table, column, source, project in [
        (Run, "display_snapshot", "snapshot", run_display),
        (Job, "display_config", "config", job_display),
    ]:
        with engine.begin() as connection:
            if column not in {c["name"] for c in inspect(connection).get_columns(table.__tablename__)}:
                connection.execute(text(f"ALTER TABLE {table.__tablename__} ADD COLUMN {column} JSON"))
        # Bound memory while backfilling old reviews, including large diffs and skill files.
        while True:
            with Session.begin() as db:
                rows = db.execute(select(table.id, getattr(table, source)).where(
                    getattr(table, column).is_(None)).limit(20)).all()
                if not rows:
                    break
                for identity, data in rows:
                    db.execute(update(table).where(table.id == identity).values({column: project(data)}))
    # create_all does not add indexes to a table that already exists.
    with engine.begin() as connection:
        connection.execute(CreateIndex(review_target_names, if_not_exists=True))
        connection.execute(CreateIndex(access_names, if_not_exists=True))
        if connection.dialect.name == "postgresql":
            name = next(c for c in inspect(connection).get_columns("artifacts") if c["name"] == "name")
            if getattr(name["type"], "length", None) is not None:
                connection.execute(text("ALTER TABLE artifacts ALTER COLUMN name TYPE TEXT"))

    with Session.begin() as db:
        # Preserve currently valid browser cookies across the upgrade. Clearing
        # the old fields in the same transaction prevents resurrection on restart.
        for identity in db.scalars(select(SSOIdentity).where(SSOIdentity.session_id != "").with_for_update()):
            if identity.expires_at > time.time():
                db.add(
                    SSOSession(
                        token_hash=hashlib.sha256(identity.session_id.encode()).hexdigest(),
                        identity_sub=identity.sub,
                        audience=identity.audience,
                        expires_at=identity.expires_at,
                    )
                )
            identity.session_id = ""
            identity.expires_at = 0
        # Migrate saved reviewers only. Accepted jobs retain their snapshots;
        # running sandboxes continue, and retired queued runtimes need a new review.
        for row in db.scalars(select(Resource).where(Resource.kind == "agents")):
            if row.data.get("harness") != "codex":
                row.data = {**row.data, "harness": "codex"}
                row.updated_at = time.time()
