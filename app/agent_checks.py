"""Private, disposable harness checks. Never reads or comments on a GitHub repo."""

import io
import secrets
import tarfile

from sqlalchemy import select

from runner.catalog import HARNESS_VERSIONS, validate_connection

from . import fusion
from .db import Resource, Run, Session, uid


def archive():
    files = {
        "maths.py": "def add(a, b):\n    return a + b\n",
        "check.py": "from maths import add\nassert add(2, 3) == 5\nprint('terminal-check-passed')\n",
        "app.py": '''from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.send_header('Content-Type', 'text/html'); self.end_headers()
        self.wfile.write(b"""<html><body style="font:20px sans-serif;padding:40px"><h1>Harness check</h1>
        <canvas id="swatch" width="100" height="100"></canvas><p><label>Review note <input id="note"></label></p>
        <button onclick="document.getElementById('status').textContent='Saved: '+document.getElementById('note').value">Save</button>
        <p id="status">Not saved</p><script>let c=document.getElementById('swatch').getContext('2d');c.fillStyle='rgb(255,0,0)';c.fillRect(0,0,100,100);</script></body></html>""")
    def log_message(self,*args): pass
HTTPServer(('127.0.0.1',8765),Handler).serve_forever()
''',
    }
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w:gz") as tar:
        for name, value in files.items():
            info = tarfile.TarInfo("check/" + name)
            raw = value.encode()
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
    return stream.getvalue()


def check_instructions(capabilities):
    instructions = (
        "This is a harness capability check. Read maths.py and the attached skill's supporting file. "
        "Report the supporting file's check word in your summary. Verify add(2, 3) returns 5; there are no planted defects. "
    )
    if "terminal" in capabilities:
        instructions += "Use your native terminal tool to run python /workspace/repo/check.py and report its actual output. "
    if "browser" in capabilities:
        instructions += "Use the browser tool to visit http://127.0.0.1:8765, inspect the screenshot, fill Review note with browser-ok and click Save. Inspect the result and report the swatch colour and saved text. "
    if "computer" in capabilities:
        instructions += "Use the computer tool to navigate Chromium to http://127.0.0.1:8765, take a screenshot, type desktop-ok in the Review note field, click Save using screen coordinates and inspect a fresh screenshot. Report the colour and saved text. "
    instructions += "Finish with findings=[] and limitations=[] only if every requested check succeeds."
    return instructions


def enqueue(data, connection):
    validate_connection(data.harness, connection["type"])
    token = secrets.token_hex(5)
    import base64

    cfg = {
        **data.model_dump(),
        "name": "harness-check",
        "group": "harness-check",
        "instance": 1,
        "target_kind": "agent",
        "agent_id": "check",
        "harness_version": HARNESS_VERSIONS[data.harness],
        "connection": {k: v for k, v in connection.items() if k != "api_key"},
        "instructions": check_instructions(data.capabilities),
        "skill_ids": ["check-skill"],
        "mcps": [],
        "mcp_definitions": [],
        "skills": [
            {
                "id": "check-skill",
                "name": "Harness check",
                "instructions": "Read skills/check-skill/reference.txt and include its exact check word in your review summary.",
                "files": {"reference.txt": base64.b64encode(token.encode()).decode()},
            }
        ],
    }
    if cfg.get("fusion"):
        cfg["fusion"] = {"branches": [
            {**{k: v for k, v in branch.items()
                if k in {"name", "connection_id", "model", "reasoning_effort", "capabilities"}},
             "instructions": check_instructions(branch.get("capabilities")
                 if branch.get("capabilities") is not None else data.capabilities),
             "rules": []}
            for branch in cfg["fusion"]["branches"]
        ]}
    run = Run(
        id=uid(),
        delivery="check-" + uid(),
        repo="Harness check",
        pr=0,
        actor="workspace",
        head="0" * 40,
        base="0" * 40,
        snapshot={
            "harness_check": True,
            "check_word": token,
            "groups": [],
            "title": "Harness check",
            "description": "Private capability check",
            "diff": "+def add(a, b):\n+    return a + b\n",
            "repository": {
                "id": "",
                "name": "Harness check",
                "setup_command": "",
                "start_command": "python app.py" if data.capabilities else "",
                "readiness_url": "http://127.0.0.1:8765" if data.capabilities else "",
            },
        },
    )
    with Session() as db:
        resources = {r.id: r for r in db.scalars(select(Resource))}
        resources["check-skill"] = Resource(id="check-skill", kind="skills", data=cfg["skills"][0])
        cfg = fusion.snapshot_config(cfg, resources)
        db.add(run)
        fusion.enqueue(db, run.id, cfg)
        db.commit()
    return {"id": run.id, "status": "queued"}
