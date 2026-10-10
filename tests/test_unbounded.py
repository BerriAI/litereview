import asyncio
import base64
import io
import json
import tarfile
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select
from test_harnesses import seed_job
from test_reviews import mock_github, payload, setup, webhook

from app import github, providers, reviews
from app.db import Artifact, Job, ReviewPublication, Run, Session
from runner import agent, harness, tools
from runner.bridge import content_blocks


class ArchiveStream(httpx.AsyncByteStream):
    def __init__(self, size):
        self.size = size
        self.read = 0
        self.closed = False

    async def __aiter__(self):
        while self.read < self.size:
            chunk = b"x" * min(64 * 1024, self.size - self.read)
            self.read += len(chunk)
            yield chunk

    async def aclose(self):
        self.closed = True


def archive_transport(monkeypatch, stream, status=200):
    actual_client = httpx.AsyncClient
    requests = []

    async def respond(request):
        requests.append(request)
        if request.url.host == "api.github.com":
            return httpx.Response(302, headers={"location": "https://codeload.github.com/repo/tar.gz/sha"})
        return httpx.Response(status, stream=stream)

    monkeypatch.setattr(github, "token", AsyncMock(return_value="github-only-token"))
    monkeypatch.setattr(
        github.httpx, "AsyncClient", lambda **kw: actual_client(**kw, transport=httpx.MockTransport(respond))
    )
    return requests


@pytest.mark.parametrize("disconnect", [False, True])
async def test_archive_streams_past_50_mb_and_closes_on_disconnect(monkeypatch, disconnect):
    stream = ArchiveStream(50_000_001)
    requests = archive_transport(monkeypatch, stream)
    delivered = 0

    async def send(message):
        nonlocal delivered
        if message["type"] == "http.response.start":
            assert message["status"] == 200
            assert stream.read == 0  # No API-side prebuffering.
        else:
            delivered += len(message["body"])
            if disconnect:
                raise asyncio.CancelledError()

    response = github.ArchiveResponse("owner/repo", 42, "sha")
    if disconnect:
        with pytest.raises(asyncio.CancelledError):
            await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), send)
    else:
        await response({"type": "http", "asgi": {"spec_version": "2.4"}}, AsyncMock(), send)
        assert delivered == stream.size
    assert stream.closed
    assert requests[0].headers["authorization"] == "Bearer github-only-token"
    assert "authorization" not in requests[1].headers


def test_archive_endpoint_preserves_auth_and_download_error_detail(client, monkeypatch):
    seed_job()
    stream = ArchiveStream(0)
    requests = archive_transport(monkeypatch, stream, status=503)
    path = "/api/internal/jobs/test-job/archive"
    assert client.get(path).status_code == 401
    assert not requests
    response = client.get(path, headers={"authorization": "Bearer job-token"})
    assert response.status_code == 502
    assert response.json()["detail"] == "GitHub repository download failed (HTTP 503)."
    assert stream.closed


def test_extraction_exceeds_old_expanded_and_entry_limits(tmp_path):
    class Zeros:
        def read(self, size):
            return b"\0" * size

    source = tmp_path / "large.tar.gz"
    with tarfile.open(source, mode="w:gz") as archive:
        member = tarfile.TarInfo("repo/large.bin")
        member.size = 250_000_001
        archive.addfile(member, Zeros())
        for index in range(30_002):
            archive.addfile(tarfile.TarInfo(f"repo/files/{index}"))
    root = tmp_path / "checkout"
    with source.open("rb") as raw:
        agent.extract_archive(raw, root)
    assert (root / "large.bin").stat().st_size == 250_000_001
    assert len(list((root / "files").iterdir())) == 30_002


@pytest.mark.parametrize("json_body", [True, False])
async def test_download_failure_is_actionable_and_records_activity(json_body):
    response = httpx.Response(
        413,
        **(
            {"json": {"detail": "Upstream archive unavailable"}}
            if json_body
            else {"text": "<html>too large</html>"}
        ),
    )
    async with httpx.AsyncClient(
        base_url="https://relay.test/", transport=httpx.MockTransport(lambda _: response)
    ) as client:
        relay = SimpleNamespace(client=client, event=AsyncMock())
        with pytest.raises(RuntimeError, match="Upstream archive unavailable" if json_body else "HTTP 413"):
            await agent.prepare_repository(relay)
    relay.event.assert_awaited_once_with("Downloading repository at the reviewed commit")


async def test_worker_extracts_streamed_checkout_and_closes_temporary_file(tmp_path, monkeypatch):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w:gz") as archive:
        member = tarfile.TarInfo("repo/run.sh")
        member.size, member.mode = 5, 0o755
        archive.addfile(member, io.BytesIO(b"hello"))
    temporary = agent.TemporaryFile()
    monkeypatch.setattr(agent, "TemporaryFile", lambda: temporary)
    monkeypatch.setattr(agent, "REPO", tmp_path / "repo")
    async with httpx.AsyncClient(
        base_url="https://relay.test/",
        transport=httpx.MockTransport(lambda _: httpx.Response(200, content=payload.getvalue())),
    ) as client:
        relay = SimpleNamespace(client=client, event=AsyncMock())
        await agent.prepare_repository(relay)
    assert temporary.closed
    assert (agent.REPO / "run.sh").read_bytes() == b"hello"
    assert (agent.REPO / "run.sh").stat().st_mode & 0o111
    assert [call.args[0] for call in relay.event.await_args_list] == [
        "Downloading repository at the reviewed commit", "Extracting repository",
    ]


def test_large_pr_and_full_diff_are_accepted(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    old_request = github.repo_request
    diff = "diff --git a/file b/file\n" + "+long change\n" * 200_000

    async def request(*args, **kwargs):
        response = await old_request(*args, **kwargs)
        if args[3].startswith("compare/"):
            return httpx.Response(200, text=diff)
        return httpx.Response(200, json={**response.json(), "changed_files": 1000})

    monkeypatch.setattr(github, "repo_request", request)
    response = webhook(owner, payload())
    with Session() as db:
        run = db.get(Run, response.json()["run_id"])
        assert run.status == "queued"
        assert run.snapshot["diff"] == diff
        assert db.scalar(select(Job).where(Job.run_id == run.id)) is not None


def test_large_agent_groups_and_skill_files_can_be_saved(owner, monkeypatch):
    _, _, reviewer, _, group = setup(owner)
    reviewer.update(name="reviewer" * 10, instructions="inspect " * 3000, minimum_minutes=121)
    assert owner.put("/api/resources/agents/" + reviewer["id"], json=reviewer).status_code == 200
    group["members"] = [{"agent_id": reviewer["id"], "count": 31}]
    assert owner.put("/api/resources/groups/" + group["id"], json=group).status_code == 200
    skill = {
        "name": "large",
        "instructions": "x" * 100_001,
        "files": {
            **{f"folder/file-{i}": base64.b64encode(b"content").decode() for i in range(51)},
            "large": base64.b64encode(b"x" * 2_000_001).decode(),
        },
    }
    saved = owner.post("/api/resources/skills", json=skill)
    assert saved.status_code == 200 and saved.json()["files"] == skill["files"]
    mock_github(monkeypatch)
    run_id = webhook(owner, payload()).json()["run_id"]
    with Session() as db:
        assert len(db.scalars(select(Job).where(Job.run_id == run_id)).all()) == 31
    assert len(reviews.command("@litereview " + " ".join(f"agent{i}" for i in range(21)))) == 21


def test_large_requests_results_logs_and_screenshots_are_preserved(client):
    seed_job()
    root = "/api/internal/jobs/test-job/"
    headers = {"authorization": "Bearer job-token"}
    # Neither incoming request size nor per-event history trims the user's data.
    with Session() as db:
        db.get(Job, "test-job").logs = [{"message": str(i)} for i in range(201)]
        db.commit()
    message = "x" * 12_000_001
    response = client.post(root + "event", headers=headers, json={"message": message})
    assert response.status_code == 200
    with Session() as db:
        logs = db.get(Job, "test-job").logs
        assert len(logs) == 202 and logs[0]["message"] == "0" and logs[-1]["message"] == message
        db.add_all(Artifact(job_id="test-job", name="previous", content=b"png") for _ in range(20))
        db.commit()
    raw = b"\x89PNG\r\n\x1a\n" + b"x" * 2_000_001
    response = client.post(
        root + "artifact",
        headers=headers,
        json={
            "data": base64.b64encode(raw).decode(),
            "name": "label" * 100,
        },
    )
    assert response.status_code == 200
    with Session() as db:
        artifact = db.get(Artifact, response.json()["id"])
        assert artifact.content == raw and artifact.name == "label" * 100
    result = {
        "status": "completed",
        "summary": "summary" * 1000,
        "findings": [
            {"title": "t" * 201, "severity": "low", "explanation": "e" * 5001, "evidence": ["e"] * 11}
        ]
        * 31,
        "limitations": ["limitation"] * 31,
        "checks": [{"name": "n" * 201, "status": "passed", "required": False, "details": "d" * 2001}] * 31,
    }
    assert client.post(root + "finish", headers=headers, json=result).status_code == 200
    with Session() as db:
        saved = db.get(Job, "test-job").result
        assert saved["summary"] == result["summary"]
        assert len(saved["findings"]) == len(saved["checks"]) == 31


def test_existing_postgres_artifact_name_column_is_widened(client):
    from sqlalchemy import inspect, text

    from app.db import engine, init_db

    if engine.dialect.name != "postgresql":
        pytest.skip("PostgreSQL enforces the legacy VARCHAR length")
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE artifacts ALTER COLUMN name TYPE VARCHAR(150)"))
    init_db()
    init_db()  # Safe on a subsequent startup, with existing data retained.
    column = next(c for c in inspect(engine).get_columns("artifacts") if c["name"] == "name")
    assert getattr(column["type"], "length", None) is None


async def test_tools_and_bridge_return_full_contents(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "REPO", tmp_path)
    contents = "needle " + "x" * 1000 + "\n"
    (tmp_path / "large.txt").write_text(contents * 2100)
    for i in range(3001):
        (tmp_path / f"empty-{i}").touch()
    diff = "+change\n" * 10_000
    toolset = tools.Toolset({"config": {"capabilities": ["terminal"]}, "pr": {"diff": diff}}, AsyncMock())
    read = await toolset.execute("read_file", {"path": "large.txt"})
    assert read["content"].endswith("2100: " + contents.rstrip())
    assert len((await toolset.execute("list_files", {}))["files"]) == 3002
    assert len((await toolset.execute("search", {"query": "needle"}))["matches"].splitlines()) == 2100
    assert (await toolset.execute("read_diff", {}))["diff"] == diff
    assert (await toolset.execute("read_diff", {"offset": 1, "length": 5}))["diff"] == diff[1:6]
    shell = await toolset.shell("cat large.txt")
    assert shell["output"] == contents * 2100
    assert json.loads(content_blocks(shell)["content"][0]["text"])["output"] == shell["output"]
    for _ in range(21):
        await toolset.picture(b"png", "screenshot")
    assert toolset.relay.await_count == 21


async def test_harness_events_have_no_line_size_limit():
    stream = asyncio.StreamReader()
    long_line = b"x" * 16_000_001 + b"\n"
    stream.feed_data(long_line + b"{}\nlast")
    stream.feed_eof()
    assert [line async for line in harness.stream_lines(stream)] == [long_line, b"{}\n", b"last"]


async def test_model_request_has_no_invented_output_or_time_budget(monkeypatch):
    complete = AsyncMock(
        return_value=SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(model_dump=lambda **kw: {"content": "done"}))]
        )
    )
    monkeypatch.setattr(providers.litellm, "acompletion", complete)
    await providers.complete({"type": "openai", "api_key": "test"}, "model", [], [])
    assert "max_tokens" not in complete.call_args.kwargs
    assert all(value is None for value in complete.call_args.kwargs["timeout"].as_dict().values())
    from litellm.litellm_core_utils.completion_timeout import CompletionTimeout
    from litellm.utils import supports_httpx_timeout

    resolved = CompletionTimeout.resolve(
        complete.call_args.kwargs["timeout"],
        {},
        "openai",
        global_timeout=None,
        supports_httpx_timeout=supports_httpx_timeout,
    )
    assert all(value is None for value in resolved.as_dict().values())


async def test_anthropic_adapter_disables_sdk_timeout_fallback(monkeypatch):
    sent = []

    async def post(client, *args, **kwargs):
        sent.append(kwargs["timeout"])
        return httpx.Response(200)

    monkeypatch.setattr(providers.AsyncHTTPHandler, "post", post)
    client = providers.UntimedHTTPHandler(timeout=httpx.Timeout(None))
    try:
        await client.post("https://provider.test/", timeout=600)
    finally:
        await client.close()
    assert all(value is None for value in sent[0].as_dict().values())


def test_github_comment_chunks_preserve_full_review():
    run = Run(id="large", repo="owner/repo", head="a" * 40, status="completed")
    job = Job(
        config={"name": "reviewer", "target_kind": "agent"},
        status="completed",
        result={
            "summary": "long review " * 20_000,
            "findings": [],
            "limitations": [],
        },
    )
    body = reviews.render_comment(run, [job])
    parts = reviews.comment_parts(run, body)
    assert len(parts) > 1
    assert all(len(part) <= reviews.GITHUB_COMMENT_CHARACTERS for part in parts)
    assert "".join(part.split("\n", 1)[1] for part in parts) == body.removeprefix("<!-- litereview:large -->")


async def test_multicomment_publication_recovers_a_lost_response(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    run_id = webhook(owner, payload()).json()["run_id"]
    with Session() as db:
        for job in db.scalars(select(Job).where(Job.run_id == run_id)):
            job.status = "completed"
            job.result = {"summary": "full result " * 20_000}
        db.commit()
    comments = []
    interrupted = False

    async def request(repo, installation, method, suffix, **kwargs):
        nonlocal interrupted
        if suffix.startswith("pulls/"):
            return httpx.Response(200, json={"head": {"sha": "a" * 40}})
        if method == "GET":
            return httpx.Response(200, json=comments.copy())
        if suffix.endswith("/reactions"):
            return httpx.Response(200, json={"id": 1})
        if method == "PATCH":
            next(c for c in comments if c["id"] == int(suffix.split("/")[-1]))["body"] = kwargs["json"][
                "body"
            ]
            return httpx.Response(200, json={})
        comment = {"id": len(comments) + 1, "body": kwargs["json"]["body"], "user": {"type": "Bot"}}
        comments.append(comment)
        if len(comments) == 2 and not interrupted:
            interrupted = True
            raise httpx.ReadError("Lost the successful second POST response")
        return httpx.Response(201, json=comment)

    monkeypatch.setattr(github, "repo_request", request)
    with pytest.raises(httpx.ReadError):
        await reviews.publish(run_id)
    await reviews.publish(run_id)
    assert len(comments) > 2
    assert len({c["body"].split("\n", 1)[0] for c in comments}) == len(comments)
    with Session() as db:
        run = db.get(Run, run_id)
        assert run.published == 2
        jobs = db.scalars(select(Job).where(Job.run_id == run_id)).all()
        for job in jobs:
            state = db.get(ReviewPublication, f"job:{job.id}").data
            parts = [next(c["body"] for c in comments if c["id"] == cid) for cid in state["comment_ids"]]
            expected = reviews.render_comment(run, [job], agent_comment=True, all_jobs=jobs)
            marker = expected.split("\n", 1)[0]
            assert "".join(part.split("\n", 1)[1] for part in parts) == expected.removeprefix(marker)
