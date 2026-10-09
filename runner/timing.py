"""A monotonic review clock shared by every harness."""

import math
import time
from datetime import datetime, timezone


class ReviewClock:
    def __init__(self, minimum=0):
        self.minimum = minimum
        self.started = None
        self.started_at = None

    def start(self):
        self.started = time.monotonic()
        self.started_at = datetime.now(timezone.utc).isoformat()

    def status(self):
        elapsed = 0 if self.started is None else max(0, time.monotonic() - self.started)
        return {
            "started_at": self.started_at,
            "current_time": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "minimum_seconds": self.minimum,
            "remaining_seconds": math.ceil(max(0, self.minimum - elapsed)),
        }

    def require_elapsed(self):
        remaining = self.status()["remaining_seconds"]
        if remaining:
            raise ValueError(
                f"Minimum review time has not elapsed: {remaining} seconds remain. "
                "Continue useful investigation, tests, and verification within your configured scope. "
                "Do not sleep or busy-wait to fill the time. Use review_clock to check progress."
            )

    def instructions(self):
        if not self.minimum:
            return ""
        return (
            f"\nMinimum review duration: {self.minimum / 60:g} minutes of review after setup. "
            f"Clock at start: {self.started_at}. Check review_clock at the start and before finishing. "
            "Keep working on useful checks, evidence, edge cases, and verification until the minimum "
            "has elapsed. Early finish_review calls are rejected by the harness. Do not sleep, "
            "busy-wait, or repeat checks just to fill time. Maintain your configured scope, skills, "
            "and tool permissions. Hard time, model-call, cancellation, and error limits still apply."
        )
