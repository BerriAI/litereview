"""Small failure records that survive a broken callback without logging request data."""

import json
import time
from pathlib import Path

PREFIX = "LITEREVIEW_DIAGNOSTIC "


class ReviewError(RuntimeError):
    def __init__(self, message, *, stage, safe_message=None, http_status=None, exit_code=None):
        super().__init__(message)
        self.stage = stage
        self.safe_message = safe_message or message
        self.http_status = http_status
        self.exit_code = exit_code


def failure_record(exc, stage="review", *, kind="failure"):
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    record = {"kind": kind, "time": time.time(), "stage": stage, "error_type": type(exc).__name__}
    if isinstance(exc, ReviewError):
        record.update(stage=exc.stage, message=exc.safe_message)
        for key in ("http_status", "exit_code"):
            if (value := getattr(exc, key)) is not None:
                record[key] = value
    elif isinstance(exc, TimeoutError):
        record["message"] = "The review reached its execution time limit."
    else:
        # Exceptions can contain credentials, request bodies, or repository text.
        # Preserve their type and code location, never arbitrary exception strings.
        record["message"] = f"{type(exc).__name__} during {stage}."
        trace = exc.__traceback__
        if trace:
            while trace.tb_next:
                trace = trace.tb_next
            location = f"{Path(trace.tb_frame.f_code.co_filename).name}:{trace.tb_lineno}"
            record["message"] += f" Location: {location}."
    return record


def emit(record):
    print(PREFIX + json.dumps(record), flush=True)


def parse(line):
    if not line.startswith(PREFIX):
        return None
    try:
        value = json.loads(line[len(PREFIX):])
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("kind") not in ("failure", "submission_failed"):
        return None
    if not all(isinstance(value.get(key), str) for key in ("stage", "message", "error_type")):
        return None
    return value
