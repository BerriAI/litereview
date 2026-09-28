"""Seed acceptance-test configuration through the real API; reads secrets only from .env."""

import base64
import os

import httpx
from dotenv import load_dotenv

load_dotenv(".env")
base = os.environ["PUBLIC_URL"]
with httpx.Client(base_url=base + "/api", timeout=60) as client:
    r = client.post("/login", json={"token": os.environ["ADMIN_TOKEN"]})
    r.raise_for_status()

    def save(kind, data):
        existing = client.get("/resources/" + kind).json()
        row = next((x for x in existing if x["name"] == data["name"]), None)
        response = (
            client.put("/resources/" + kind + "/" + row["id"], json=data)
            if row
            else client.post("/resources/" + kind, json=data)
        )
        if not response.is_success:
            raise RuntimeError(response.text)
        return response.json()

    gateway = save(
        "connections",
        {
            "name": "Sandbox gateway",
            "type": "gateway",
            "base_url": os.environ["GATEWAY_URL"] + "/v1",
            "api_key": os.environ["GATEWAY_API_KEY"],
        },
    )
    repo = save(
        "repos",
        {
            "name": "BerriAI/litereview",
            "installation_id": 0,
            "start_command": "python -m http.server 3000 --directory tests/fixtures/review_site",
            "readiness_url": "http://localhost:3000",
        },
    )
    skill = save(
        "skills",
        {
            "name": "Evidence-based review",
            "description": "Confirm behavior and cite concrete evidence.",
            "instructions": "Only report actionable defects introduced in this PR. Read supporting criteria in checklist.txt. When using a visual tool, inspect the screenshot and check the visible outcome after interaction. Do not invent findings.",
            "files": {
                "checklist.txt": base64.b64encode(
                    b"Check calculations with representative inputs. For UI checks, look for unreadable content and broken interactions."
                ).decode()
            },
        },
    )
    agents = {}
    for name, caps, instructions in [
        (
            "Frontend reviewer",
            ["browser"],
            "Review the PR UI changes. Open the local readiness URL with Browser, inspect actual screenshots, click Complete purchase, and inspect the result. Check the canvas rendering visually. Report concrete visible regressions. Stay focused on the UI.",
        ),
        (
            "Pentest reviewer",
            ["terminal"],
            "Review the changed code for correctness and security. Read relevant files and run a few small Python tests for the order-total helper. Report only issues supported by evidence. Do not scan external targets.",
        ),
        (
            "General reviewer",
            [],
            "Inspect the PR diff and changed files. Review the order-total calculation and related changes. Stay focused; finish once the changed behavior is understood.",
        ),
        (
            "Desktop reviewer",
            ["computer"],
            "Review the local application using the desktop. Use the computer tool with Ctrl+L, type the readiness URL, press Enter, inspect the screenshot, and click the purchase area. Inspect the actual rendered canvas for readability and verify the visible outcome. Report visual defects with screenshot evidence. Finish promptly after checking the changed UI.",
        ),
    ]:
        agent = save(
            "agents",
            {
                "name": name,
                "instructions": instructions,
                "connection_id": gateway["id"],
                "model": "claude-haiku-4-5-20251001",
                "capabilities": caps,
                "skill_ids": [skill["id"]],
            },
        )
        agents[name] = agent
    for group, name, count in [
        ("frontend", "Frontend reviewer", 1),
        ("pentest", "Pentest reviewer", 1),
        ("security", "General reviewer", 5),
        ("desktop", "Desktop reviewer", 1),
    ]:
        save(
            "groups",
            {
                "name": group,
                "description": name,
                "members": [{"agent_id": agents[name]["id"], "count": count}],
                "repos": [repo["name"]],
                "caller_policy": "allowlist",
                "callers": ["moe-berri"],
            },
        )
    print(
        "Seeded live configuration: 4 reusable agents, 4 groups, one skill, and gateway/repository connections."
    )
