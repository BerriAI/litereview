"""Local OAuth + Streamable HTTP server for protocol tests and browser fixtures."""

import base64
import hashlib
import json
import secrets
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit


@contextmanager
def mcp_server(port=0):
    state = {
        "calls": [],
        "codes": {},
        "tokens": set(),
        "refreshes": 0,
        "registration": True,
        "token_status": 200,
        "tools_status": 200,
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, code, body=None, headers=None):
            encoded = json.dumps(body).encode() if body is not None else b""
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            base = state["base"]
            path = urlsplit(self.path).path
            if path == "/.well-known/oauth-protected-resource/mcp":
                return self.respond(
                    200,
                    {
                        "resource": state.get("resource", base + "/mcp"),
                        "authorization_servers": [base],
                        "scopes_supported": ["tools:read"],
                    },
                )
            if path == "/.well-known/oauth-authorization-server":
                return self.respond(
                    200,
                    {
                        "issuer": base,
                        "authorization_endpoint": base + "/authorize",
                        "token_endpoint": base + "/token",
                        "response_types_supported": ["code"],
                        "code_challenge_methods_supported": ["S256"],
                        "token_endpoint_auth_methods_supported": ["none", "client_secret_post"],
                        **({"registration_endpoint": base + "/register"} if state["registration"] else {}),
                    },
                )
            if path == "/authorize":
                params = {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}
                code = secrets.token_urlsafe(16)
                state["codes"][code] = params
                return self.respond(
                    302,
                    headers={
                        "Location": params["redirect_uri"]
                        + "?"
                        + urlencode({"code": code, "state": params["state"]})
                    },
                )
            self.respond(405)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            body = (
                json.loads(raw)
                if "application/json" in self.headers.get("Content-Type", "")
                else {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            )
            state["calls"].append((self.path, body, dict(self.headers)))
            if self.path == "/register":
                return self.respond(
                    201, {"client_id": "fixture-client", "token_endpoint_auth_method": "none"}
                )
            if self.path == "/token":
                if state["token_status"] != 200:
                    return self.respond(state["token_status"], {"error": "invalid_grant"})
                if body["grant_type"] == "authorization_code":
                    params = state["codes"].pop(body["code"])
                    challenge = (
                        base64.urlsafe_b64encode(hashlib.sha256(body["code_verifier"].encode()).digest())
                        .decode()
                        .rstrip("=")
                    )
                    assert params["code_challenge"] == challenge
                    assert body["client_id"] == params["client_id"]
                    assert body["redirect_uri"] == params["redirect_uri"]
                    assert body["resource"] == params["resource"]
                else:
                    assert body["refresh_token"].startswith("refresh-")
                    state["refreshes"] += 1
                token = "fixture-token-" + secrets.token_urlsafe(12)
                state["tokens"].add(token)
                return self.respond(
                    200,
                    {
                        "access_token": token,
                        "refresh_token": "refresh-" + token,
                        "token_type": "Bearer",
                        "expires_in": 3600,
                    },
                )
            if self.path == "/mcp":
                auth = self.headers.get("Authorization", "").removeprefix("Bearer ")
                if auth not in state["tokens"]:
                    return self.respond(
                        401,
                        headers={
                            "WWW-Authenticate": f'Bearer resource_metadata="{state["base"]}/.well-known/oauth-protected-resource/mcp"'
                        },
                    )
                method = body["method"]
                if method.startswith("notifications/"):
                    return self.respond(202)
                if method == "initialize":
                    result = {
                        "protocolVersion": body["params"]["protocolVersion"],
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "Local fixture", "version": "1.0"},
                    }
                elif method == "tools/list":
                    if state["tools_status"] != 200:
                        return self.respond(state["tools_status"])
                    second = body.get("params", {}).get("cursor") == "page2"
                    result = {
                        "tools": [
                            {
                                "name": "fetch_document" if second else "search_documents",
                                "description": "Read a fixture document."
                                if second
                                else "Search fixture documents.",
                                "inputSchema": {"type": "object", "properties": {}},
                            }
                        ],
                        **({} if second else {"nextCursor": "page2"}),
                    }
                elif method == "tools/call":
                    result = {"content": [{"type": "text", "text": "Fixture tool executed"}]}
                else:
                    return self.respond(400)
                return self.respond(200, {"jsonrpc": "2.0", "id": body["id"], "result": result})
            self.respond(404)

        def do_DELETE(self):
            self.respond(405)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    state["base"] = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
