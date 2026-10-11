"""Per-sandbox tools and model bridge used by exactly one selected harness."""

import asyncio
import gzip
import json
import socket
from contextlib import asynccontextmanager

import httpx
import jsonschema
import uvicorn
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from .tools import definitions


def content_blocks(result):
    result = dict(result)
    images = result.pop("images", [])
    if result.get("image"):
        images.append(result.pop("image"))
    content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
    for image in images:
        prefix, data = image.split(",", 1)
        content.append({"type": "image", "data": data, "mimeType": prefix[5:].split(";")[0]})
    return {"content": content, "isError": bool(result.get("isError"))}


class Bridge:
    def __init__(self, tools, relay, cfg):
        self.tools, self.relay = tools, relay
        self.finished = asyncio.Event()
        self.result = None
        self.model_error = None
        # Native command execution is supplied by the harness; never offer two terminals.
        self.definitions = {
            "lr_" + item["function"]["name"]: item["function"]
            for item in definitions(cfg)
            if item["function"]["name"] != "terminal"
        }
        self.cfg = cfg

    async def call(self, name, arguments):
        try:
            if self.finished.is_set():
                raise ValueError("This review is already finished.")
            schema = self.definitions.get(name)
            if schema is None:
                raise ValueError("This tool is not enabled for this agent.")
            jsonschema.validate(arguments, schema["parameters"])
            await self.relay.event("Using " + name)
            if name == "lr_finish_review":
                if self.cfg.get("minimum_review_seconds"):
                    self.tools.clock.require_elapsed()
                self.result = {**arguments, "status": "completed"}
                self.finished.set()
                return content_blocks({"submitted": True})
            return content_blocks(await self.tools.execute(name.removeprefix("lr_"), arguments))
        except Exception as exc:
            return {"content": [{"type": "text", "text": str(exc)}], "isError": True}

    async def list_http(self, request):
        return JSONResponse(
            [
                {"name": name, "description": item["description"], "inputSchema": item["parameters"]}
                for name, item in self.definitions.items()
            ]
        )

    async def status_http(self, request):
        return JSONResponse({"finished": self.finished.is_set(), **self.tools.clock.status()})

    async def call_http(self, request):
        body = await request.json()
        return JSONResponse(await self.call(body.get("name"), body.get("arguments", {})))

    async def model_http(self, request: Request):
        path = request.path_params["path"]
        if path == "models" and request.method == "GET":
            return JSONResponse({"object": "list", "data": [{"id": self.cfg["model"], "object": "model"}]})
        headers = {k: v for k, v in request.headers.items() if k in ("anthropic-version", "anthropic-beta")}
        try:
            upstream = await self.relay.client.send(
                self.relay.client.build_request(
                    "POST",
                    "llm/" + path,
                    # Repository text may look like shell injection to a hosting WAF.
                    # HTTP compression preserves the source without changing job auth.
                    content=gzip.compress(await request.body()),
                    headers={**headers, "content-type": "application/json", "content-encoding": "gzip"},
                ),
                stream=True,
            )
        except httpx.HTTPError:
            self.model_error = "The harness could not reach the model relay."
            return JSONResponse({"error": {"message": "Model relay unavailable."}}, status_code=502)
        if not upstream.is_success:
            self.model_error = (
                "The model endpoint rejected the request (HTTP "
                + str(upstream.status_code)
                + "). "
                + (
                    "Check provider credits and rate limits."
                    if upstream.status_code == 429
                    else "Check the selected model, credentials, and harness compatibility."
                )
            )
        else:
            self.model_error = None

        async def stream():
            try:
                async for chunk in upstream.aiter_bytes():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            stream(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    @asynccontextmanager
    async def serve(self):
        mcp = Server("LiteReview tools")

        @mcp.list_tools()
        async def list_tools():
            return [
                types.Tool(name=name, description=item["description"], inputSchema=item["parameters"])
                for name, item in self.definitions.items()
            ]

        @mcp.call_tool()
        async def call_tool(name, arguments):
            return types.CallToolResult(**await self.call(name, arguments))

        manager = StreamableHTTPSessionManager(mcp, stateless=True, json_response=True)

        class MCPApp:
            async def __call__(self, scope, receive, send):
                await manager.handle_request(scope, receive, send)

        app = Starlette(
            routes=[
                Route("/tools", self.list_http),
                Route("/status", self.status_http),
                Route("/call", self.call_http, methods=["POST"]),
                Route("/v1/{path:path}", self.model_http, methods=["POST", "GET"]),
                Route("/mcp", MCPApp(), methods=["POST", "GET", "DELETE"]),
            ]
        )
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False, lifespan="off"))
        async with manager.run():
            task = asyncio.create_task(server.serve(sockets=[sock]))
            try:
                while not server.started:
                    if task.done():
                        await task
                        raise RuntimeError("Could not start harness tool bridge.")
                    await asyncio.sleep(0.01)
                yield "http://127.0.0.1:" + str(sock.getsockname()[1])
            finally:
                server.should_exit = True
                await task
                sock.close()
