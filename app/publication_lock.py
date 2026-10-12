"""Exclude overlapping publishers, including during rolling deployments."""

import hashlib
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout
from sqlalchemy import text

from .db import engine


@contextmanager
def publication_lock(run_id, *, parent_run_id=None):
    if engine.dialect.name == "postgresql":
        # A dedicated transaction holds the lock across all GitHub calls and
        # checkpoint writes. Other runs and worker result writes remain free.
        # Connection/process exit releases it without a lease that could expire
        # while a GitHub request is still in flight.
        names = sorted({run_id, parent_run_id} if parent_run_id else {run_id})
        with engine.begin() as connection:
            yield all(connection.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {
                "key": int.from_bytes(hashlib.sha256(f"publication:{name}".encode()).digest()[:8], signed=True)
            }) for name in names)
    else:
        # Local SQLite deployments coordinate processes via the database's
        # adjacent lock file. Do not unlink it: another process may hold its inode.
        path = Path(engine.url.database or "data/litereview.db").resolve()
        lock = FileLock(str(path) + "-publication.lock", timeout=0)
        try:
            lock.acquire()
        except Timeout:
            yield False
        else:
            try:
                yield True
            finally:
                lock.release()
