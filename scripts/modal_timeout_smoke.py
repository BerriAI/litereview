"""Opt-in real Modal hard-timeout check (consumes a short sandbox run)."""

import modal

from app.runtime import client, sandbox_image
from app.settings import settings

c = client()
app = modal.App.lookup(settings().modal_app_name, client=c)
sandbox = modal.Sandbox.create(
    "python", "-c", "import time; time.sleep(30)", app=app, image=sandbox_image(), client=c, timeout=10
)
try:
    try:
        sandbox.wait()
    except modal.exception.SandboxTimeoutError:
        print("PASS: Modal enforced the hard timeout for", sandbox.object_id)
    else:
        raise AssertionError("The sandbox did not time out.")
finally:
    sandbox.terminate()
