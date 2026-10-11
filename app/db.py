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
    func,
    inspect,
    select,
    text,
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
    comment_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    published: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[float] = mapped_column(Float, default=time.time)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=uid)
    run_id: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    config: Mapped[dict] = mapped_column(JSON)
    token_hash: Mapped[str] = mapped_column(String(64), default="")
    sandbox_id: Mapped[str] = mapped_column(String(100), default="")
    started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    finished_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    logs: Mapped[list] = mapped_column(JSON, default=list)


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


class AutomaticReviewClaim(Base):
    __tablename__ = "automatic_review_claims"
    # Atomic with Run/Job insertion; survives redeliveries and overlapping rules.
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(40), index=True)


class CommentSweepCursor(Base):
    __tablename__ = "comment_sweep_cursors"
    # Scope progress to the connected repository and installation.
    key: Mapped[str] = mapped_column(String(90), primary_key=True)
    scanned_through: Mapped[float] = mapped_column(Float)


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
