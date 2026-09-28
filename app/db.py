import time
import uuid
from pathlib import Path

from sqlalchemy import JSON, BigInteger, Float, Integer, LargeBinary, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

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
    name: Mapped[str] = mapped_column(String(150))
    content: Mapped[bytes] = mapped_column(LargeBinary)
    mime: Mapped[str] = mapped_column(String(80), default="image/png")


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
