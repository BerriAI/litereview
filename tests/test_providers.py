"""Protocol tests for direct-provider adapters. No real provider credentials are used."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app import providers


@pytest.fixture
def provider_server():
    observed = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append({"path": self.path, "body": body, "headers": dict(self.headers)})
            if "messages" in self.path:
                response = {
                    "id": "msg_test",
                    "type": "message",
                    "role": "assistant",
                    "model": body["model"],
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool_test",
                            "name": "capability_check",
                            "input": {"value": "red"},
                        }
                    ],
                    "stop_reason": "tool_use",
                    "stop_sequence": None,
                    "usage": {"input_tokens": 10, "output_tokens": 10},
                }
            else:
                response = {
                    "id": "chatcmpl_test",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "tool_calls",
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_test",
                                        "type": "function",
                                        "function": {
                                            "name": "capability_check",
                                            "arguments": '{"value":"red"}',
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
                }
            encoded = json.dumps(response).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}", observed
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    "provider,model", [("openai", "gpt-4o-mini"), ("anthropic", "claude-haiku-4-5-20251001")]
)
async def test_direct_provider_preserves_images_and_tool_results(provider_server, provider, model):
    base, observed = provider_server
    conn = {
        "type": provider,
        "base_url": base + ("/v1/messages" if provider == "anthropic" else "/v1"),
        "api_key": "local-protocol-test-only",
    }
    result = await providers.probe(conn, model, vision=True)
    assert result == {"ok": True, "vision": True, "tools": True}
    body = observed[-1]["body"]
    parts = body["messages"][0]["content"]
    if provider == "anthropic":
        picture = next(p for p in parts if p["type"] == "image")
        assert picture["source"]["type"] == "base64"
        assert picture["source"]["media_type"] == "image/png"
        assert len(picture["source"]["data"]) > 50
    else:
        picture = next(p for p in parts if p["type"] == "image_url")
        assert picture["image_url"]["url"].startswith("data:image/png;base64,")
    assert body["model"] == model
