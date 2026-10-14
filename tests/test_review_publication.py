import asyncio

import httpx
import pytest
from markdown_it import MarkdownIt
from sqlalchemy import select
from test_reviews import mock_github, payload, setup, webhook

from app import github, reviews
from app.db import Job, Run, Session
from app.publication import diff_lines

DIFF = """diff --git a/app.py b/app.py
--- a/app.py
+++ b/app.py
@@ -1,3 +1,3 @@
 context
-old
+new
 tail
"""
FINDING = {
    "title": "Fix the cache",
    "severity": "medium",
    "explanation": "The stale entry is reused.",
    "file": "app.py",
    "line": 2,
    "evidence": ["Reproduced with two requests."],
}


def finish(run_id, status="completed", *, one=False, findings=None):
    with Session.begin() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == run_id).order_by(Job.id)).all()
        for job in jobs[:1] if one else jobs:
            job.status = status
            job.result = {
                "summary": "Checked the changed paths.",
                "findings": findings or [],
                "limitations": [],
            }
        return jobs[0].id


class GitHubFixture:
    def __init__(self, run_id):
        self.run_id = run_id
        self.calls, self.comments, self.threads = [], [], []
        self.reactions = {}
        self.sequence = 6_000_000_000
        self.head = "a" * 40

    async def request(self, repo, installation, method, suffix, **kwargs):
        assert (repo, installation) == ("owner/repo", 42)
        body = kwargs.get("json")
        self.calls.append((method, suffix, body))
        if method == "GET" and suffix == "pulls/1":
            return httpx.Response(200, json={"head": {"sha": self.head}, "base": {"sha": "b" * 40},
                                            "state": "open", "title": "Test PR", "body": ""})
        if method == "GET" and suffix.startswith("compare/"):
            return httpx.Response(200, text=DIFF)
        if method == "GET":
            source = self.threads if suffix.startswith("pulls/") else self.comments
            page = int(suffix.split("page=")[-1])
            return httpx.Response(200, json=source[(page - 1) * 100 : page * 100])
        if "/reactions" in suffix:
            if method == "DELETE":
                parent = suffix.rsplit("/", 1)[0]
                self.reactions.pop(parent, None)
                return httpx.Response(204)
            if suffix not in self.reactions:
                self.sequence += 1
                self.reactions[suffix] = self.sequence
            return httpx.Response(200, json={"id": self.reactions[suffix]})
        if method == "PATCH":
            comment = next((c for c in self.comments + self.threads if c["id"] == int(suffix.split("/")[-1])), None)
            if not comment:
                raise github.GitHubError(httpx.Response(404, json={"message": "Not Found"}), method, suffix)
            comment.update(body)
            return httpx.Response(200, json=comment)
        if method == "DELETE":
            self.threads = [c for c in self.threads if c["id"] != int(suffix.split("/")[-1])]
            return httpx.Response(204)
        assert method == "POST"
        self.sequence += 1
        comment = {"id": self.sequence, **body, "user": {"type": "Bot", "login": "litereview[bot]"}}
        if suffix.endswith("/replies"):
            comment["in_reply_to_id"] = int(suffix.split("/")[-2])
        (self.threads if suffix.startswith("pulls/") else self.comments).append(comment)
        return httpx.Response(201, json=comment)


@pytest.fixture
def publication(owner, monkeypatch):
    setup(owner)
    mock_github(monkeypatch)
    run_id = webhook(owner, payload()).json()["run_id"]
    with Session.begin() as db:
        run = db.get(Run, run_id)
        run.snapshot = {**run.snapshot, "diff": DIFF}
    fake = GitHubFixture(run_id)
    monkeypatch.setattr(github, "repo_request", fake.request)
    return fake


async def test_agents_finish_independently_and_eyes_are_removed(publication):
    fake = publication
    with Session.begin() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == fake.run_id).order_by(Job.id)).all()
        jobs[1].config = {**jobs[1].config, "agent_id": "other-reviewer", "name": "other-reviewer"}
        second_id = jobs[1].id
    await reviews.publish(fake.run_id)
    assert not fake.comments and len(fake.reactions) == 1
    assert list(fake.reactions) == ["issues/comments/12345/reactions"]
    calls = len(fake.calls)
    await reviews.publish(fake.run_id)
    assert len(fake.calls) == calls  # Polls without transitions do not call GitHub.
    finish(fake.run_id, one=True, findings=[FINDING])
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 1 and len(fake.threads) == 1 and len(fake.reactions) == 1
    completed = next(c for c in fake.comments if "### ✅" in c["body"])
    assert f"#discussion_r{fake.threads[0]['id']}" in completed["body"]
    assert "stale entry" not in completed["body"]
    assert "stale entry" in fake.threads[0]["body"] and "Reproduced" in fake.threads[0]["body"]
    body = fake.threads[0]["body"]
    assert body.index("stale entry") < body.index("<details>") < body.index("Reproduced") < body.index("</details>")
    assert "<summary>Evidence</summary>" in body and "<details open" not in body
    assert f"#issuecomment-{completed['id']}" in fake.threads[0]["body"]
    assert {k: fake.threads[0][k] for k in ("commit_id", "path", "line", "side")} == {
        "commit_id": "a" * 40,
        "path": "app.py",
        "line": 2,
        "side": "RIGHT",
    }
    with Session() as db:
        assert db.get(Run, fake.run_id).status == "running"
    with Session.begin() as db:
        job = db.get(Job, second_id)
        job.status = "failed"
        job.result = {"summary": "Could not finish.", "findings": [], "limitations": []}
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 2 and not fake.reactions
    assert any("### ⚠️ other-reviewer" in c["body"] for c in fake.comments)
    assert any("Review incomplete." in c["body"] for c in fake.comments)
    calls = len(fake.calls)
    await reviews.publish(fake.run_id)
    assert len(fake.calls) == calls
    with Session() as db:
        assert db.get(Run, fake.run_id).published == 2
        assert db.get(Run, fake.run_id).status == "incomplete"


@pytest.mark.parametrize("effect", ["eyes", "parent", "thread", "remove_eyes"])
async def test_lost_responses_recover_without_duplicates(publication, monkeypatch, effect):
    fake = publication
    if effect == "remove_eyes":
        await reviews.publish(fake.run_id)
    if effect != "eyes":
        finish(fake.run_id, findings=[FINDING] if effect != "parent" else [])
    lost = False

    async def request(*args, **kwargs):
        nonlocal lost
        response = await fake.request(*args, **kwargs)
        method, suffix = args[2:4]
        matches = {
            "eyes": method == "POST" and suffix.endswith("/reactions"),
            "parent": method == "POST" and suffix == "issues/1/comments",
            "thread": method == "POST" and suffix == "pulls/1/comments",
            "remove_eyes": method == "DELETE",
        }
        if not lost and matches[effect]:
            lost = True
            raise httpx.ReadTimeout("Response lost after GitHub accepted the request")
        return response

    monkeypatch.setattr(github, "repo_request", request)
    with pytest.raises(httpx.ReadTimeout):
        await reviews.publish(fake.run_id)
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == (0 if effect == "eyes" else 1)
    assert len(fake.threads) == (2 if effect in ("thread", "remove_eyes") else 0)
    assert len(fake.reactions) == (1 if effect == "eyes" else 0)


@pytest.mark.parametrize(
    "finding",
    [
        {**FINDING, "line": 99},
        {**FINDING, "file": "other.py"},
        {**FINDING, "file": None},
        {**FINDING, "explanation": "x" * 70_000},
    ],
)
async def test_unanchored_and_oversized_findings_are_preserved(publication, finding):
    fake = publication
    finish(fake.run_id, findings=[finding])
    await reviews.publish(fake.run_id)
    assert not fake.threads
    body = "".join(c["body"].split("\n", 1)[1] for c in fake.comments)
    assert finding["explanation"] in body and FINDING["evidence"][0] in body
    assert "<summary>Evidence</summary>" in body
    assert all(len(c["body"]) <= reviews.GITHUB_COMMENT_CHARACTERS for c in fake.comments)


@pytest.mark.parametrize(
    "status,errors,fallback",
    [
        (422, [{"field": "pull_request_review_thread.line", "code": "invalid"}], True),
        (422, [{"message": "Spammed too quickly"}], False),
        (403, [], False),
        (502, [], False),
    ],
)
async def test_invalid_anchor_falls_back_but_transient_errors_retry(
    publication, monkeypatch, status, errors, fallback
):
    fake = publication
    finish(fake.run_id, findings=[FINDING])

    async def request(*args, **kwargs):
        if args[2:4] == ("POST", "pulls/1/comments"):
            raise github.GitHubError(httpx.Response(status, json={"errors": errors}), "POST", args[3])
        return await fake.request(*args, **kwargs)

    monkeypatch.setattr(github, "repo_request", request)
    if fallback:
        await reviews.publish(fake.run_id)
        assert all("The stale entry is reused." in c["body"] for c in fake.comments)
    else:
        with pytest.raises(github.GitHubError):
            await reviews.publish(fake.run_id)
        with Session() as db:
            assert db.get(Run, fake.run_id).published < 2
        monkeypatch.setattr(github, "repo_request", fake.request)
        await reviews.publish(fake.run_id)
        assert len(fake.threads) == 2


async def test_rejected_and_already_published_runs(publication):
    fake = publication
    run_id = reviews.reject_command("rejected", "owner/repo", 1, "trusted", 42, "Unknown reviewer.")
    await reviews.publish(run_id)
    assert not fake.reactions and "Review not started" in fake.comments[0]["body"]
    with Session.begin() as db:
        db.get(Run, fake.run_id).published = 2
    calls = len(fake.calls)
    await reviews.publish(fake.run_id)
    assert len(fake.calls) == calls


async def test_new_head_and_legacy_progress_comment(publication):
    fake = publication
    fake.head = "c" * 40
    fake.comments.append({"id": 99, "body": "Old progress", "user": {"type": "Bot"}})
    with Session.begin() as db:
        run = db.get(Run, fake.run_id)
        run.published, run.comment_id = 1, 99
    finish(fake.run_id, findings=[FINDING])
    await reviews.publish(fake.run_id)
    assert "✅ Reviews finished" in fake.comments[0]["body"]
    assert all("New commits were pushed" in c["body"] for c in fake.comments[1:])
    assert all(c["commit_id"] == "a" * 40 for c in fake.threads)
    assert not fake.reactions


def test_diff_lines_include_context_and_ignore_deleted_and_binary_files():
    diff = (
        DIFF
        + """diff --git a/gone b/gone
--- a/gone
+++ /dev/null
@@ -1 +0,0 @@
-removed
 diff --git not a file header
"""
        + """diff --git "a/space file.py" "b/space file.py"
--- "a/space file.py"
+++ "b/space file.py"
@@ -10,0 +11,2 @@
+first
+second
"""
    )
    assert diff_lines(diff) == {"app.py": {1, 2, 3}, "space file.py": {11, 12}}


def test_report_headings_stay_below_attribution_and_code_is_preserved():
    code = "```python\n# comment\nprint('<tag> @user')\n```\n\n    # indented comment\n"
    report = (
        "# Verdict\n\n## Evidence\n\nSetext title\n=====\n\n> ### Quote heading\n\n- ### List heading\n\n"
        + code
    )
    rendered = reviews.md(report)
    assert code in rendered
    assert all(int(t.tag[1:]) >= 4 for t in MarkdownIt().parse(rendered) if t.type == "heading_open")
    assert "Verdict" in rendered and "Setext title" in rendered


def test_agent_response_is_visible_without_generic_narrative_replacement():
    run = Run(id="clean", repo="owner/repo", head="a" * 40, status="completed")
    job = Job(
        config={"name": "yujong", "target_kind": "agent", "group": "yujong", "instance": 1},
        status="completed",
        result={"summary": "Long review narrative.", "findings": [], "limitations": []},
    )
    body = reviews.render_comment(run, [job])
    assert body.startswith("<!-- litereview:clean -->\n\n### yujong\n\nLong review narrative.")
    assert "No issues found" not in body
    assert "Review complete" not in body and "· 1" not in body and "1 agents" not in body
    assert body.endswith(f"Reviewed [`{'a' * 12}`](https://github.com/owner/repo/commit/{'a' * 40})")


def test_findings_and_incomplete_coverage_stay_visible():
    run = Run(id="partial", repo="owner/repo", head="a" * 40, status="incomplete")
    job = Job(
        config={"name": "security", "target_kind": "agent", "group": "security", "instance": 1},
        status="failed",
        result={
            "summary": "Could not finish browser checks.",
            "findings": [
                {
                    "title": "Token exposed",
                    "severity": "high",
                    "explanation": "Remove @all <script>.",
                    "file": "app/auth.py",
                    "line": 42,
                }
            ],
            "limitations": ["Browser unavailable."],
        },
    )
    body = reviews.render_comment(run, [job], newer=True)
    assert "New commits were pushed" in body
    assert "**High · Token exposed** · [app/auth.py:42]" in body
    assert "#L42" in body and "Browser unavailable." in body
    assert "Review incomplete." in body and "No issues found" not in body
    assert "Browser unavailable." in body and "Token exposed" in body
    assert "Could not finish browser checks." in body
    assert "@all" not in body and "<script>" not in body


async def test_completion_between_lost_eyes_response_and_retry(publication, monkeypatch):
    fake = publication
    lost = False

    async def request(*args, **kwargs):
        nonlocal lost
        response = await fake.request(*args, **kwargs)
        suffix = args[3]
        if not lost and args[2] == "POST" and suffix == "issues/comments/12345/reactions":
            lost = True
            raise httpx.ReadTimeout("Lost reaction response")
        return response

    monkeypatch.setattr(github, "repo_request", request)
    with pytest.raises(httpx.ReadTimeout):
        await reviews.publish(fake.run_id)
    finish(fake.run_id)
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 1 and not fake.reactions


async def test_retry_scans_all_pages_and_ignores_user_markers(publication):
    fake = publication
    first_id = finish(fake.run_id, findings=[FINDING])
    marker = f"<!-- litereview:{fake.run_id}:job:{first_id} -->"
    fake.comments = [{"id": i, "body": marker, "user": {"type": "User"}} for i in range(100)]
    fake.comments.append({"id": 999, "body": marker, "user": {"type": "Bot"}})
    await reviews.publish(fake.run_id)
    assert all(c["body"] == marker for c in fake.comments[:100])
    assert "### ✅" in fake.comments[100]["body"]
    assert len(fake.comments) == 101
    assert any(path.endswith("page=2") for _, path, _ in fake.calls)


async def test_one_agent_publication_error_does_not_block_other_agent(publication, monkeypatch):
    fake = publication
    failed_id = finish(fake.run_id, findings=[FINDING])

    async def request(*args, **kwargs):
        if args[2:4] == ("POST", "pulls/1/comments") and failed_id in kwargs["json"]["body"]:
            raise httpx.ReadTimeout("One failed request")
        return await fake.request(*args, **kwargs)

    monkeypatch.setattr(github, "repo_request", request)
    with pytest.raises(httpx.ReadTimeout):
        await reviews.publish(fake.run_id)
    assert len(fake.threads) == 1
    assert any("Reply in thread" in c["body"] for c in fake.comments)
    monkeypatch.setattr(github, "repo_request", fake.request)
    await reviews.publish(fake.run_id)
    assert len(fake.threads) == 2


@pytest.mark.parametrize("terminal", [False, True])
async def test_overlapping_publishers_do_not_duplicate_comments_or_threads(
    publication, monkeypatch, terminal
):
    fake = publication
    finish(fake.run_id, findings=[FINDING] if terminal else [])
    entered, release = asyncio.Event(), asyncio.Event()
    first_post = True

    async def request(*args, **kwargs):
        nonlocal first_post
        if first_post and args[2:4] == ("POST", "pulls/1/comments" if terminal else "issues/1/comments"):
            first_post = False
            # The first publisher has already listed comments and is now in
            # flight to GitHub. A second process must not use that empty list.
            entered.set()
            await release.wait()
        return await fake.request(*args, **kwargs)

    monkeypatch.setattr(github, "repo_request", request)
    first = asyncio.create_task(reviews.publish(fake.run_id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        await asyncio.wait_for(reviews.publish(fake.run_id), 2)
    finally:
        release.set()
        await first
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 1
    assert len(fake.threads) == (2 if terminal else 0)


async def test_cancelled_publisher_releases_lock_and_recovers_post(publication, monkeypatch):
    fake = publication
    finish(fake.run_id)
    posted = asyncio.Event()

    async def request(*args, **kwargs):
        response = await fake.request(*args, **kwargs)
        if args[2:4] == ("POST", "issues/1/comments"):
            posted.set()
            await asyncio.Event().wait()
        return response

    monkeypatch.setattr(github, "repo_request", request)
    first = asyncio.create_task(reviews.publish(fake.run_id))
    await asyncio.wait_for(posted.wait(), 2)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    monkeypatch.setattr(github, "repo_request", fake.request)
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 1


def test_publication_lock_excludes_another_process_and_releases_on_exit(client):
    import subprocess
    import sys
    import time

    from app.publication_lock import publication_lock

    child = subprocess.Popen(
        [
            sys.executable,
            "-u",
            "-c",
            "from app.publication_lock import publication_lock; "
            "ctx = publication_lock('process-test'); print(ctx.__enter__(), flush=True); input()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "True"
        with publication_lock("process-test") as acquired:
            assert not acquired
    finally:
        child.kill()
        child.communicate(timeout=5)
    # PostgreSQL releases the lock after its backend observes the closed socket.
    # Reap of the client process does not synchronize with that server event.
    deadline = time.monotonic() + 3
    while True:
        with publication_lock("process-test") as acquired:
            if acquired:
                break
        assert time.monotonic() < deadline, "Publication lock was not released after client exit"
        time.sleep(0.02)


async def test_active_reviews_only_react_to_the_request(publication):
    fake = publication
    with Session.begin() as db:
        jobs = db.scalars(select(Job).where(Job.run_id == fake.run_id)).all()
        for job in jobs:
            job.status = 'blocked'
    await reviews.publish(fake.run_id)
    assert not fake.comments and not fake.threads
    assert len(fake.reactions) == 1
    calls = list(fake.calls)
    with Session() as db:
        assert db.get(Run, fake.run_id).status == 'queued'
    for status in ['queued', 'starting', 'running', 'waiting', 'cancelling', 'running']:
        with Session.begin() as db:
            for job in db.scalars(select(Job).where(Job.run_id == fake.run_id)):
                job.status = status
        await reviews.publish(fake.run_id)
        assert not fake.comments and not fake.threads
        await reviews.publish(fake.run_id)
        assert fake.calls == calls
    with Session() as db:
        assert db.get(Run, fake.run_id).status == 'running'


async def test_reruns_reuse_first_summary_and_keep_inline_findings(publication):
    fake = publication
    finish(fake.run_id, findings=[FINDING])
    await reviews.publish(fake.run_id)
    original_id = fake.comments[0]["id"]
    original_body = fake.comments[0]["body"]
    threads = [dict(c) for c in fake.threads]
    fake.head = "c" * 40
    newer = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "new-review")
    await reviews.publish(newer)
    assert len(fake.comments) == 1 and fake.comments[0]["id"] == original_id
    body = fake.comments[0]["body"]
    assert body == original_body
    assert newer not in body
    assert FINDING["title"] in body and "Checked the changed paths." in body
    assert "Review in progress" not in body and "### 👀" not in body
    finish(newer, findings=[{**FINDING, "title": "New finding"}])
    await reviews.publish(newer)
    assert len(fake.comments) == 1 and "New finding" in fake.comments[0]["body"]
    assert fake.threads[:2] == threads and len(fake.threads) == 3
    assert all(f"#issuecomment-{original_id}" in t["body"] for t in fake.threads)
    assert not fake.reactions


async def test_fusion_only_publishes_the_finished_review(publication):
    fake = publication
    with Session.begin() as db:
        parent = db.scalar(select(Job).where(Job.run_id == fake.run_id))
        parent.status = "waiting"
        parent.config = {**parent.config, "fusion_stage": "aggregator"}
        pid = parent.id
        for index, role in enumerate(["Correctness", "UI drift"]):
            db.add(Job(id=f"progress-{index}", run_id=fake.run_id, status="running", config={
                **parent.config, "fusion_parent_id": pid, "fusion_index": index,
                "fusion_stage": "branch", "fusion_role": role}))
    await reviews.publish(fake.run_id)
    assert not fake.comments
    calls = list(fake.calls)
    with Session.begin() as db:
        db.get(Job, "progress-0").status = "completed"
    await reviews.publish(fake.run_id)
    assert not fake.comments
    await reviews.publish(fake.run_id)
    assert fake.calls == calls
    with Session.begin() as db:
        db.get(Job, "progress-1").status = "completed"
        db.get(Job, "progress-1").result = {"skipped": True}
        db.get(Job, pid).status = "running"
    await reviews.publish(fake.run_id)
    assert not fake.comments and fake.calls == calls
    finish(fake.run_id)
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 1 and "### ✅ reviewer" in fake.comments[0]["body"]
    assert "specialists completed" not in fake.comments[0]["body"]
    assert not fake.reactions


async def test_repeated_instances_wait_for_the_whole_reviewer(publication):
    fake = publication
    await reviews.publish(fake.run_id)
    calls = list(fake.calls)
    finish(fake.run_id, one=True, findings=[FINDING])
    await reviews.publish(fake.run_id)
    assert not fake.comments and not fake.threads and fake.calls == calls
    finish(fake.run_id, findings=[FINDING])
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 1 and len(fake.threads) == 2
    assert not fake.reactions


async def test_adopts_legacy_progress_and_retires_other_instance_comments(publication):
    from app.publication import add_eyes, load_state, save_state

    fake = publication
    with Session() as db:
        run = db.get(Run, fake.run_id)
        jobs = db.scalars(select(Job).where(Job.run_id == run.id).order_by(Job.id)).all()
    original = []
    for job in jobs:
        comment = (await fake.request(run.repo, run.installation, "POST", "issues/1/comments",
            json={"body": f"<!-- litereview:{run.id}:job:{job.id} -->\n\nReviewing…"})).json()
        original.append(comment["id"])
        checkpoint = {"comment_ids": [comment["id"]], "ready": True, "status": "queued"}
        await add_eyes(run, comment["id"], checkpoint, f"job:{job.id}")
        save_state(f"job:{job.id}", checkpoint)
    finish(run.id)
    await reviews.publish(run.id)
    assert [c["id"] for c in fake.comments] == original
    assert "### ✅ reviewer" in fake.comments[0]["body"]
    assert "Reviewing…" not in str(fake.comments)
    assert f"#issuecomment-{original[0]}" in fake.comments[1]["body"]
    assert not fake.reactions
    assert all(load_state(f"job:{j.id}")["complete"] for j in jobs)


async def test_older_completion_or_reply_cannot_replace_newer_summary(publication):
    from app.db import ReviewPublication

    fake = publication
    finish(fake.run_id)
    await reviews.publish(fake.run_id)
    fake.head = "c" * 40
    newer = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "new-head")
    finish(newer)
    await reviews.publish(newer)
    body = fake.comments[0]["body"]
    await reviews.publish(fake.run_id)
    with Session.begin() as db:
        db.get(Run, fake.run_id).published = 1
        for job in db.scalars(select(Job).where(Job.run_id == fake.run_id)):
            checkpoint = db.get(ReviewPublication, f"job:{job.id}")
            checkpoint.data = {**checkpoint.data, "complete": False}
    await reviews.publish(fake.run_id)
    assert len(fake.comments) == 1 and fake.comments[0]["body"] == body
    assert newer in body


@pytest.mark.parametrize("lost_response", [False, True])
async def test_deleted_summary_is_recreated_once(publication, monkeypatch, lost_response):
    fake = publication
    finish(fake.run_id)
    await reviews.publish(fake.run_id)
    removed = fake.comments.pop()["id"]
    newer = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "rerun")
    finish(newer)
    lost = False

    async def request(*args, **kwargs):
        nonlocal lost
        response = await fake.request(*args, **kwargs)
        if lost_response and not lost and args[2:4] == ("POST", "issues/1/comments"):
            lost = True
            raise httpx.ReadTimeout("Lost replacement response")
        return response

    monkeypatch.setattr(github, "repo_request", request)
    if lost_response:
        with pytest.raises(httpx.ReadTimeout):
            await reviews.publish(newer)
    await reviews.publish(newer)
    assert len(fake.comments) == 1 and fake.comments[0]["id"] != removed
    assert newer in fake.comments[0]["body"]


async def test_shorter_rerun_clears_outdated_overflow(publication):
    fake = publication
    finish(fake.run_id)
    with Session.begin() as db:
        for job in db.scalars(select(Job).where(Job.run_id == fake.run_id)):
            job.result = {**job.result, "summary": "Outdated verdict.\n" * 5000}
    await reviews.publish(fake.run_id)
    original = [c["id"] for c in fake.comments]
    assert len(original) > 1
    newer = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "shorter")
    finish(newer)
    await reviews.publish(newer)
    assert [c["id"] for c in fake.comments] == original
    assert all("Outdated verdict" not in c["body"] for c in fake.comments)
    assert all("View the latest review" in c["body"] for c in fake.comments[1:])


async def test_different_run_publishers_share_the_pr_lock(publication, monkeypatch):
    fake = publication
    finish(fake.run_id)
    entered, release = asyncio.Event(), asyncio.Event()

    async def request(*args, **kwargs):
        if args[2:4] == ("POST", "issues/1/comments") and not entered.is_set():
            entered.set()
            await release.wait()
        return await fake.request(*args, **kwargs)

    monkeypatch.setattr(github, "repo_request", request)
    first = asyncio.create_task(reviews.publish(fake.run_id))
    try:
        await asyncio.wait_for(entered.wait(), 2)
        newer = await reviews.accept(payload(body="@litereview reviewer", comment_id=2), "concurrent-rerun")
        finish(newer)
        await asyncio.wait_for(reviews.publish(newer), 2)
        assert not fake.comments
    finally:
        release.set()
        await first
    await reviews.publish(newer)
    assert len(fake.comments) == 1 and newer in fake.comments[0]["body"]


async def test_mcp_requests_leave_one_visible_attributed_command(publication):
    fake = publication
    with Session.begin() as db:
        run = db.get(Run, fake.run_id)
        run.snapshot = {**run.snapshot, "trigger_comment_id": None,
                        "trigger": {"type": "mcp", "command": "@litereview reviewer"}}
    await reviews.publish(fake.run_id)
    requests = [c for c in fake.comments if "litereview:request:" in c["body"]]
    assert len(requests) == 1
    assert "@litereview reviewer" in requests[0]["body"]
    assert "Requested by @" in requests[0]["body"] and "through MCP" in requests[0]["body"]
    assert fake.comments[0]["id"] == requests[0]["id"]
    calls = len(fake.calls)
    await reviews.publish(fake.run_id)
    assert len(fake.calls) == calls
    finish(fake.run_id)
    await reviews.publish(fake.run_id)
    assert len([c for c in fake.comments if "litereview:request:" in c["body"]]) == 1
    assert not fake.reactions
