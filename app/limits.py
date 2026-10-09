"""Snapshot bounded execution limits for each accepted review."""

from .settings import settings


def job_timeout(config):
    return config.get("timeout_seconds", settings().job_timeout)


def model_rounds(config):
    return config.get("max_rounds", settings().max_rounds)


def timing_limits(minutes):
    minimum = minutes * 60
    # Leave the normal job budget for setup and finalization beyond the minimum.
    return {
        "minimum_review_seconds": minimum,
        "timeout_seconds": settings().job_timeout + minimum,
        "max_rounds": settings().max_rounds * (1 + (minimum + 599) // 600),
    }
