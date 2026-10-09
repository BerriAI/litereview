"""Only agent-selected limits bound a review; absent limits stay absent."""

from sqlalchemy import update

from .db import Job, Session

# Modal requires a lifetime and supports at most 24 hours per sandbox.
# This is infrastructure capacity, never an implicit agent setting.
SANDBOX_MAX_SECONDS = 24 * 60 * 60


def job_timeout(config):
    return config.get("timeout_seconds") or None


def model_rounds(config):
    return config.get("max_rounds") or None


def timing_limits(minutes, maximum_minutes=None, max_model_calls=None):
    return {
        "minimum_review_seconds": minutes * 60,
        "timeout_seconds": maximum_minutes * 60 if maximum_minutes else None,
        "max_rounds": max_model_calls,
    }


def count_model_call(job):
    """Count every request and atomically enforce an optional shared budget."""
    statement = update(Job).where(Job.id == job.id)
    limit = model_rounds(job.config)
    if limit is not None:
        statement = statement.where(Job.model_calls < limit)
    with Session() as db:
        changed = db.execute(statement.values(model_calls=Job.model_calls + 1)).rowcount
        db.commit()
    return bool(changed)
