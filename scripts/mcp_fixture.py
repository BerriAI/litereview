"""Disposable loopback workspace for MCP/client and UI acceptance. Never production auth.

Run: uv run python -m scripts.mcp_fixture
Open http://127.0.0.1:8769/__fixture/login to use the seeded, synthetic admin.
No external credentials or review workers are available. The database is temporary.
"""

import argparse
import asyncio
import os
import tempfile
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8769)
    parser.add_argument("--read-delay", type=float, default=0, help="Delay the first workspace read for loading-state checks.")
    parser.add_argument("--fail-first-read", action="store_true", help="Fail the first workspace read for recovery checks.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="litereview-mcp-") as directory:
        os.environ.update(
            DATABASE_URL=f"sqlite:///{directory}/fixture.db", SECRET_KEY="local-mcp-fixture-" * 4,
            ADMIN_TOKEN="", ADMIN_USERS="", ADMIN_EMAILS="admin@example.test", GOOGLE_SSO_REQUIRED="true",
            GOOGLE_CLIENT_ID="local-fixture", GOOGLE_CLIENT_SECRET="local-fixture",
            GOOGLE_ALLOWED_DOMAINS="example.test", DISPATCHER_ENABLED="false",
            PUBLIC_URL=f"http://127.0.0.1:{args.port}", GITHUB_APP_ID="", GITHUB_PRIVATE_KEY="",
            GITHUB_WEBHOOK_SECRET="", GITHUB_CLIENT_ID="", GITHUB_CLIENT_SECRET="", GITHUB_TOKEN="",
            GATEWAY_API_KEY="", OPENAI_API_KEY="", ANTHROPIC_API_KEY="",
            MODAL_TOKEN_ID="", MODAL_TOKEN_SECRET="", LITEREVIEW_TRACE_ENDPOINT="", LITEREVIEW_TRACE_API_KEY="",
        )
        import uvicorn
        from fastapi.responses import JSONResponse, RedirectResponse
        from starlette.routing import Route

        from app.db import Job, Resource, Run, Session, SSOIdentity, SSOSession, init_db
        from app.main import app
        from app.schemas import SCHEMAS
        from app.security import digest, signed

        init_db()
        resources = [
            ("gateway", "connections", {"name": "Fixture gateway", "type": "gateway", "base_url": "https://models.example.test"}),
            ("fixture-mcp", "connections", {"name": "Fixture MCP server", "type": "mcp", "base_url": "https://tools.example.test/mcp"}),
            ("review-skill", "skills", {"name": "code-review", "instructions": "Report independently verified bugs."}),
            ("oss", "agents", {"name": "oss-review", "connection_id": "gateway", "model": "fixture-cheap-model",
                "instructions": "Review correctness.", "skill_ids": ["review-skill"], "trigger_access": "anyone",
                "review_mode": "incremental", "rules": [{"id": "e2e", "name": "Real end-to-end proof",
                "instructions": "Require inspectable proof from real dependencies. Mocks do not satisfy this rule."}]}),
            ("code", "agents", {"name": "code-review", "connection_id": "gateway", "model": "fixture-review-model",
                "instructions": "Review correctness.", "skill_ids": ["review-skill"], "review_mode": "compare",
                "rules": [{"id": "breaking", "name": "Breaking-change guidance", "instructions": "Verify migration guidance."}]}),
            ("ship", "groups", {"name": "ship-review", "members": [{"agent_id": "code", "count": 2}]}),
            ("repository", "repos", {"name": "example/widget", "installation_id": 1,
                "review_rules": [{"id": "opened", "target_id": "code", "events": ["opened"]}]}),
            ("repository-grant", "access", {"name": "example/widget", "subject": "repo", "scope": "selected", "target_ids": ["oss", "code"]}),
        ]
        with Session.begin() as db:
            db.add(SSOIdentity(sub="fixture-admin", email="admin@example.test", domain="example.test",
                audience="local-fixture", github_login="fixture-admin", github_id="100"))
            db.add(SSOSession(token_hash=digest("fixture-session"), identity_sub="fixture-admin",
                audience="local-fixture", expires_at=time.time() + 86400))
            for rid, kind, data in resources:
                db.add(Resource(id=rid, kind=kind, data=SCHEMAS[kind].model_validate(data).model_dump()))

            for index in range(36):
                db.add(Run(id=f"fixture-run-{index}", delivery=f"fixture-delivery-{index}", repo="example/widget",
                           pr=100 + index, head="a" * 40, base="b" * 40, actor="fixture-author", status="completed",
                           snapshot={"groups": [], "diff": "Synthetic PR diff"}))
                db.add(Job(id=f"fixture-job-{index}", run_id=f"fixture-run-{index}", status="completed",
                           config={"agent_id": "code", "name": "code-review", "model": "fixture-review-model", "instance": 1},
                           result={"status": "completed", "summary": "Synthetic completed review for interface checks.",
                                   "findings": [], "limitations": [], "screenshots": []}))

        first_read = True

        @app.middleware("http")
        async def delayed_read(request, call_next):
            nonlocal first_read
            if request.url.path == "/api/workspace" and first_read:
                first_read = False
                await asyncio.sleep(max(0, args.read_delay))
                if args.fail_first_read:
                    return JSONResponse({"detail": "Fixture connection failed. Retry to reconnect."}, status_code=503)
            return await call_next(request)

        async def login(request):
            response = RedirectResponse("/agents", 303)
            response.set_cookie("lr_session", signed({"auth": "google", "sub": "fixture-admin",
                                                      "session_id": "fixture-session"}), httponly=True, samesite="lax")
            return response

        # This route exists only in this standalone disposable fixture, never in app.main.
        app.router.routes.insert(0, Route("/__fixture/login", login))
        uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
