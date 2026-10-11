"""Native model protocols for external harnesses; credentials stay in the API."""

import httpx
from fastapi import HTTPException
from fastapi.responses import JSONResponse, StreamingResponse

from . import providers, spend, tracing
from .limits import count_model_call
from .worker_transport import worker_json

PROTOCOLS = {"chat/completions", "responses", "messages", "messages/count_tokens"}


async def relay_model(job, request, path):
    if path not in PROTOCOLS:
        raise HTTPException(404, "Unsupported model endpoint.")
    body = await worker_json(request)
    cfg = job.config
    connection = providers.snapshot_connection(cfg["connection"])
    kind = connection["type"]
    if kind == "anthropic" and not path.startswith("messages"):
        raise HTTPException(400, "This model connection uses the Anthropic Messages API.")
    if kind == "openai" and path.startswith("messages"):
        raise HTTPException(400, "This model connection uses the OpenAI API.")
    if path != "messages/count_tokens":
        if not count_model_call(job):
            raise HTTPException(429, "Model round limit reached.")
    for key in (
        "api_key",
        "api_base",
        "api_version",
        "base_url",
        "custom_llm_provider",
        "litellm_params",
        "extra_headers",
        "organization",
    ):
        body.pop(key, None)
    # Harness defaults (including small background models) cannot escape the job's selection.
    body["model"] = cfg["model"]
    if path == "responses":
        body["store"] = False
        body["background"] = False
    base = connection.get("base_url") or (
        "https://api.anthropic.com/v1" if kind == "anthropic" else "https://api.openai.com/v1"
    )
    base = base.rstrip("/")
    if (kind == "anthropic" or path.startswith("messages")) and not base.endswith("/v1"):
        base += "/v1"
    headers = {"content-type": "application/json"}
    if kind == "anthropic":
        headers["x-api-key"] = connection["api_key"]
    else:
        headers["authorization"] = "Bearer " + connection["api_key"]
    if path.startswith("messages"):
        headers["anthropic-version"] = request.headers.get("anthropic-version", "2023-06-01")
        if request.headers.get("anthropic-beta"):
            headers["anthropic-beta"] = request.headers["anthropic-beta"]
    client = httpx.AsyncClient(timeout=None)
    trace = tracing.ModelTrace(job, body, enabled=path != "messages/count_tokens")
    meter = (spend.UsageMeter(job, await spend.lookup_rates(connection, cfg["model"]))
             if path != "messages/count_tokens" else None)
    try:
        response = await client.send(
            client.build_request("POST", base + "/" + path, json=body, headers=headers), stream=True
        )
    except httpx.HTTPError:
        if meter:
            meter.end()
        trace.end(error=True)
        await client.aclose()
        raise HTTPException(502, "The harness could not reach the model endpoint.") from None
    except BaseException:
        if meter:
            meter.end()
        trace.end(error=True)
        await client.aclose()
        raise
    if not response.is_success:
        if meter:
            meter.end()
        status = response.status_code
        trace.end(error=True, status=status)
        await response.aclose()
        await client.aclose()
        return JSONResponse(
            {
                "error": {
                    "type": "provider_error",
                    "message": f"Model endpoint returned {status} for {path}. Check this harness/model combination.",
                }
            },
            status_code=status,
        )

    if meter and "text/event-stream" not in response.headers.get("content-type", ""):
        # Streaming headers are sent before final usage and can contain a placeholder zero.
        meter.reported = spend.amount(response.headers.get("x-litellm-response-cost"))

    async def stream():
        complete = False
        try:
            async for chunk in response.aiter_bytes():
                trace.feed(chunk, sse="text/event-stream" in response.headers.get("content-type", ""))
                if meter:
                    meter.feed(chunk, sse="text/event-stream" in response.headers.get("content-type", ""))
                yield chunk
            complete = True
        finally:
            if meter:
                meter.end(complete=complete)
            trace.end(error=not complete, status=response.status_code)
            await response.aclose()
            await client.aclose()

    return StreamingResponse(stream(), media_type=response.headers.get("content-type", "application/json"))
