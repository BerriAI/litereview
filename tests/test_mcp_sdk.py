"""Real HTTP and official SDK OAuth acceptance against an isolated local workspace."""

import asyncio
import re
import socket
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import OAuthClientMetadata


class MemoryStorage:
    tokens = None
    client_info = None

    async def get_tokens(self):
        return self.tokens

    async def set_tokens(self, tokens):
        self.tokens = tokens

    async def get_client_info(self):
        return self.client_info

    async def set_client_info(self, client_info):
        self.client_info = client_info


def test_official_sdk_oauth_and_workspace_evaluations(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    with (tmp_path / "server.log").open("w+") as log:
        process = subprocess.Popen([sys.executable, "-m", "scripts.mcp_fixture", "--port", str(port)], stdout=log, stderr=log)
        try:
            for _ in range(150):
                if process.poll() is not None:
                    log.seek(0)
                    raise AssertionError(log.read())
                try:
                    if httpx.get(url + "/health", timeout=1).is_success:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                raise AssertionError("Local MCP fixture did not start.")
            asyncio.run(exercise_client(url))
        finally:
            process.terminate()
            process.wait(timeout=10)


async def exercise_client(url):
    callback = {}
    storage = MemoryStorage()

    async def redirect_handler(authorization_url):
        # Only the Google identity is seeded; discovery, DCR, consent, PKCE and token exchange are real HTTP.
        async with httpx.AsyncClient(base_url=url) as browser:
            await browser.get("/__fixture/login")
            pending = await browser.get(authorization_url)
            page = await browser.get(pending.headers["location"])
            csrf = re.search(r'name="csrf" value="([^"]+)"', page.text)[1]
            approved = await browser.post(pending.headers["location"], data={"csrf": csrf, "decision": "allow"})
            callback.update(parse_qs(urlparse(approved.headers["location"]).query))

    async def callback_handler():
        return callback["code"][0], callback["state"][0]

    oauth = OAuthClientProvider(server_url=url + "/api/mcp", storage=storage,
        client_metadata=OAuthClientMetadata(client_name="Official SDK acceptance",
            redirect_uris=[url + "/callback"], grant_types=["authorization_code", "refresh_token"],
            response_types=["code"], token_endpoint_auth_method="none"),
        redirect_handler=redirect_handler, callback_handler=callback_handler)
    async with httpx.AsyncClient(auth=oauth) as http:
        async with streamable_http_client(url + "/api/mcp", http_client=http) as (read, write, _):
            async with ClientSession(read, write) as session:
                initialized = await session.initialize()
                assert "litereview_get_workspace" in initialized.instructions
                assert str((await session.list_resources()).resources[0].uri) == "litereview://guide"
                assert (await session.list_prompts()).prompts[0].name == "configure_litereview"
                assert (await session.read_resource("litereview://guide")).contents
                assert (await session.get_prompt("configure_litereview", {"goal": "Show options"})).messages

                async def call(name, **arguments):
                    result = await session.call_tool("litereview_" + name, arguments)
                    assert not result.isError, result
                    return result.structuredContent

                workspace = await call("get_workspace")
                agents = (await call("list_resources", kind="agents", include_details=True))["items"]
                oss, code = next(a for a in agents if a["name"] == "oss-review"), next(a for a in agents if a["name"] == "code-review")
                skill = await call("get_resource", kind="skills", resource_id=oss["skill_ids"][0])
                group = (await call("list_resources", kind="groups", include_details=True))["items"][0]
                repo = (await call("list_resources", kind="repos", include_details=True))["items"][0]
                grant = next(g for g in (await call("list_resources", kind="access", include_details=True))["items"] if g["subject"] == "repo")
                connection = await call("get_resource", kind="connections", resource_id=oss["connection_id"])
                guide = (await call("get_guide"))["instructions"]
                schema = await call("get_schema", kind="agents")
                assert "private in Runs" in guide and "Responses-compatible" in guide
                assert "skipped or omitted required rule makes verification incomplete" in guide
                assert "cannot access this MCP" in guide and "first review is full" in guide
                assert oss["id"] in grant["target_ids"] and oss["trigger_access"] == "anyone"
                assert oss["rules"][0]["enabled"] and oss["rules"][0]["required"]
                assert repo["review_rules"][0]["target_id"] == code["id"]
                answers = [f"{oss['name']},{oss['review_mode']}", f"{skill['name']},{oss['rules'][0]['id']}",
                    f"{code['review_mode']},no", f"{sum(m['count'] for m in group['members'])},{'yes' if group['id'] in grant['target_ids'] else 'no'}",
                    f"{code['name']},{repo['review_rules'][0]['events'][0]}", f"{oss['model']},{connection['type']},Responses",
                    "not_run,no", "no", f"{'yes' if workspace['is_admin'] else 'no'},{schema['properties']['trigger_access']['default']}", "yes,no"]
                expected = [q.findtext("answer") for q in ET.parse(Path("docs/MCP_EVAL.xml")).getroot()]
                assert answers == expected  # Verified answer key; this does not claim an LLM evaluation ran.
                before_refresh = storage.tokens.access_token
                oauth.context.token_expiry_time = time.time() - 1
                assert (await call("get_workspace"))["is_admin"]
                assert storage.tokens.access_token != before_refresh
                added = await call("add_rule", agent_id=oss["id"], rule={"name": "Acceptance", "instructions": "Check evidence."})
                await call("update_rule", agent_id=oss["id"], rule_id=added["rules"][-1]["id"], changes={"enabled": False})
                saved = await call("get_resource", kind="agents", resource_id=oss["id"])
                assert saved["instructions"] == oss["instructions"] and len(saved["rules"]) == 2
