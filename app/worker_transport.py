"""Decode source-code payloads after the job endpoint authenticates its worker."""

import gzip
import json
import zlib

from fastapi import HTTPException, Request


async def worker_json(request: Request):
    encoding = request.headers.get("content-encoding", "identity").strip().lower()
    if encoding == "identity":
        return await request.json()
    if encoding != "gzip":
        raise HTTPException(415, "Unsupported job request content encoding.")
    try:
        body = gzip.decompress(await request.body())
    except (OSError, EOFError, zlib.error):
        raise HTTPException(400, "Invalid compressed job request.") from None
    try:
        return json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Submit the job request as a JSON object.") from None
