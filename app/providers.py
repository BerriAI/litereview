import base64
import json
from contextlib import asynccontextmanager

import httpx
import litellm
from fastapi import HTTPException
from litellm.llms.custom_httpx.http_handler import AsyncHTTPHandler
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .db import Resource, Session
from .security import decrypt

litellm.suppress_debug_info = True


def connection(resource_id):
    with Session() as db:
        r = db.get(Resource, resource_id)
        if not r or r.kind != "connections":
            raise HTTPException(409, "The selected connection no longer exists.")
        return {"id": r.id, **r.data, "api_key": decrypt(r.secret)}


def snapshot_connection(snapshot):
    # Metadata is immutable; only the credential reference is resolved at call time.
    current = connection(snapshot["id"])
    if snapshot.get("type") == "mcp":
        if current["type"] != "mcp" or current["base_url"] != snapshot["base_url"]:
            raise HTTPException(
                409, "The MCP endpoint changed after this review started. Start a new review."
            )
        return {**snapshot, "auth_type": current.get("auth_type", "bearer"), "api_key": current["api_key"]}
    return {**snapshot, "api_key": current["api_key"]}


def model_capacity(model):
    """Use published model capacities when available; never invent a token budget."""
    try:
        info = litellm.get_model_info(model)
    except Exception:
        return {}
    return {
        name: info[key]
        for name, key in (("context", "max_input_tokens"), ("output", "max_output_tokens"))
        if isinstance(info.get(key), int) and info[key] > 0
    }


class UntimedHTTPHandler(AsyncHTTPHandler):
    async def post(self, *args, **kwargs):
        # LiteLLM coerces Anthropic's Timeout(None) to 600 seconds before calling this adapter.
        kwargs["timeout"] = httpx.Timeout(None)
        return await super().post(*args, **kwargs)


async def complete(conn, model, messages, tools, *, trace=None, meter=None):
    provider = conn["type"]
    prefix = "anthropic" if provider == "anthropic" else "openai"
    model_name = model if model.startswith(prefix + "/") and provider != "gateway" else prefix + "/" + model
    client = UntimedHTTPHandler(timeout=httpx.Timeout(None)) if provider == "anthropic" else None
    try:
        response = await litellm.acompletion(
            model=model_name,
            api_key=conn["api_key"],
            api_base=(conn.get("base_url") or None),
            messages=messages,
            tools=tools or None,
            timeout=httpx.Timeout(None),
            num_retries=1,
            **({"client": client} if client else {}),
        )
        if trace is not None:
            trace.result(response.model_dump(exclude_none=True))
        if meter is not None:
            meter.result(response.model_dump(exclude_none=True))
            reported = (getattr(response, "_hidden_params", None) or {}).get("response_cost")
            from .spend import amount
            if provider == "gateway" and amount(reported) is not None:
                meter.reported = amount(reported)
        return response.choices[0].message.model_dump(exclude_none=True)
    except Exception as exc:
        # Provider exceptions can include request headers; never expose their text.
        raise HTTPException(
            502,
            f"Model request failed ({type(exc).__name__}). Check the model ID, credentials, and image/tool support.",
        ) from None
    finally:
        if client:
            await client.close()


async def models(conn):
    if conn["type"] == "mcp":
        return []
    base = conn.get("base_url") or (
        "https://api.anthropic.com/v1" if conn["type"] == "anthropic" else "https://api.openai.com/v1"
    )
    headers = {"Authorization": "Bearer " + conn["api_key"]}
    if conn["type"] == "anthropic":
        headers = {"x-api-key": conn["api_key"], "anthropic-version": "2023-06-01"}
    async with httpx.AsyncClient(timeout=None) as client:
        r = await client.get(base.rstrip("/") + "/models", headers=headers)
    if not r.is_success:
        raise HTTPException(
            400,
            f"Model discovery returned {r.status_code}. Check the connection URL and key; you can also enter a model ID manually.",
        )
    return sorted(x["id"] for x in r.json().get("data", []))


async def draft_completion(conn, model, messages):
    """One bounded text request; drafting does not run agent tools or a sandbox."""
    prefix = "anthropic" if conn["type"] == "anthropic" else "openai"
    model_name = model if model.startswith(prefix + "/") and conn["type"] != "gateway" else prefix + "/" + model
    try:
        response = await litellm.acompletion(
            model=model_name, api_key=conn["api_key"], api_base=conn.get("base_url") or None,
            messages=messages, max_tokens=4096, timeout=60, num_retries=0,
        )
        return response.choices[0].message.content
    except Exception as exc:
        raise HTTPException(502, f"Draft creation failed ({type(exc).__name__}). Check the model and connection in Settings → Agent creation.") from None


@asynccontextmanager
async def mcp_session(conn):
    from .mcp_oauth import MCPAuth

    oauth = conn.get("auth_type") == "oauth"
    headers = (
        {"Authorization": "Bearer " + conn["api_key"]}
        if conn.get("api_key") and conn.get("auth_type", "bearer") == "bearer"
        else {}
    )
    auth = MCPAuth(conn["id"], conn["base_url"]) if oauth else None
    async with httpx.AsyncClient(
        headers=headers, auth=auth, timeout=None, follow_redirects=False
    ) as client:
        async with streamable_http_client(conn["base_url"], http_client=client) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def mcp_error(exc):
    if isinstance(exc, HTTPException):
        return exc
    for child in getattr(exc, "exceptions", []):
        found = mcp_error(child)
        if found:
            return found
    return None


async def mcp_tools(conn):
    try:
        async with mcp_session(conn) as session:
            tools, cursor, seen = [], None, set()
            while True:
                result = await session.list_tools(cursor=cursor)
                tools.extend(
                    {"name": t.name, "description": t.description or "", "inputSchema": t.inputSchema}
                    for t in result.tools
                )
                cursor = result.nextCursor
                if not cursor:
                    return tools
                if cursor in seen:
                    raise ValueError("MCP server repeated a pagination cursor")
                seen.add(cursor)
    except Exception as exc:
        if error := mcp_error(exc):
            raise error from None
        raise HTTPException(
            400, "MCP connection failed. Check the Streamable HTTP endpoint and credentials."
        ) from None


async def mcp_call(conn, name, arguments):
    try:
        async with mcp_session(conn) as session:
            result = await session.call_tool(name, arguments)
            return result.model_dump(mode="json")
    except Exception as exc:
        if error := mcp_error(exc):
            raise error from None
        raise HTTPException(502, f"MCP tool {name} failed. Check the connection and server logs.") from None


async def probe(conn, model, vision=False):
    content = 'Call capability_check with value "ok".'
    expected = "ok"
    if vision:
        # A solid red 16x16 PNG is generated without an imaging dependency.
        import struct
        import zlib

        def chunk(kind, value):
            return struct.pack("!I", len(value)) + kind + value + struct.pack("!I", zlib.crc32(kind + value))

        png = (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack("!2I5B", 16, 16, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0" * 16) * 16))
            + chunk(b"IEND", b"")
        )
        content = [
            {
                "type": "text",
                "text": "Inspect the image. Call capability_check with its dominant color in lowercase.",
            },
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode()},
            },
        ]
        expected = "red"
    message = await complete(
        conn,
        model,
        [{"role": "user", "content": content}],
        [
            {
                "type": "function",
                "function": {
                    "name": "capability_check",
                    "description": "Confirm capability.",
                    "parameters": {
                        "type": "object",
                        "properties": {"value": {"type": "string"}},
                        "required": ["value"],
                    },
                },
            }
        ],
    )
    for call in message.get("tool_calls", []):
        try:
            if (
                call["function"]["name"] == "capability_check"
                and json.loads(call["function"]["arguments"]).get("value", "").lower() == expected
            ):
                return {"ok": True, "vision": vision, "tools": True}
        except (ValueError, KeyError):
            pass
    raise HTTPException(400, "This model did not pass the image/tool capability check. Choose another model.")
