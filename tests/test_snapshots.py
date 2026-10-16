import asyncio
import io
import tarfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_harnesses import seed_job

from app import runtime, snapshots
from app.db import Job, RepositorySnapshot, Resource, Run, Session
from runner import warm

REPO = {"id": "repo", "name": "org/project", "installation_id": 12, "setup_command": "uv sync --frozen"}


def archive(files):
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w:gz") as tar:
        for path, content in files.items():
            entry = tarfile.TarInfo("github-prefix/" + path)
            entry.size = len(content)
            tar.addfile(entry, io.BytesIO(content))
    raw.seek(0)
    return raw


def save_snapshot(**overrides):
    values = dict(repository_id="repo", fingerprint=snapshots.fingerprint(REPO), image_id="im-cached",
                  requested_fingerprint=snapshots.fingerprint(REPO), source_sha="a" * 40,
                  built_at=time.time(), expires_at=time.time() + 86400, retry_after=time.time() + 86400)
    with Session.begin() as db:
        db.add(RepositorySnapshot(**{**values, **overrides}))


def test_replacement_preserves_only_dependencies_and_exact_new_source(tmp_path):
    root = tmp_path / "repo"
    (root / ".venv").mkdir(parents=True)
    (root / ".venv/installed").write_text("cached package")
    (root / "removed.py").write_text("old source")
    (root / ".env").write_text("stale generated config")
    (root / "generated").mkdir()
    (root / "generated/old.py").write_text("old generated source")
    warm.replace_source(archive({"new.py": b"exact PR source", "uv.lock": b"changed dependencies"}), root)
    assert (root / "new.py").read_text() == "exact PR source"
    assert (root / "uv.lock").read_text() == "changed dependencies"
    assert (root / ".venv/installed").read_text() == "cached package"
    assert {p.name for p in root.iterdir()} == {"new.py", "uv.lock", ".venv"}


def test_head_owned_paths_win_over_cached_dependencies(tmp_path):
    root = tmp_path / "repo"
    (root / "node_modules").mkdir(parents=True)
    (root / "node_modules/old").write_text("old")
    warm.replace_source(archive({"node_modules/new": b"head owns this directory"}), root)
    assert not (root / "node_modules/old").exists()
    assert (root / "node_modules/new").read_text() == "head owns this directory"


def test_dependency_symlinks_are_not_transferred(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").touch()
    (root / ".venv").symlink_to(outside, target_is_directory=True)
    warm.replace_source(archive({"source.py": b"new"}), root)
    assert not (root / ".venv").exists()
    assert (outside / "keep").exists()


def test_failed_extraction_never_overlays_cached_source(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    (root / "keep").write_text("original")
    with pytest.raises(ValueError, match="Unsafe"):
        warm.replace_source(archive({"first": b"new", "../escape": b"bad"}), root)
    assert {p.name for p in root.iterdir()} == {"keep"}
    assert not (tmp_path / "escape").exists()


def test_warming_excludes_service_and_test_secrets_and_discards_baseline_source(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    path = tmp_path / "source.tgz"
    path.write_bytes(archive({"source.py": b"trusted baseline"}).read())
    for name in ("LR_JOB_TOKEN", "GITHUB_TOKEN", "OPENAI_API_KEY", "TEST_PASSWORD"):
        monkeypatch.setenv(name, "must-not-enter-snapshot")

    def setup(command, *, shell, cwd, env, check):
        assert command == "install dependencies" and shell and check
        assert set(env) <= {"PATH", "HOME", "LANG"}
        assert (cwd / "source.py").exists()
        (cwd / ".venv").mkdir()
        (cwd / ".venv/package").write_text("installed")
        (cwd / ".env").write_text("generated")

    monkeypatch.setattr(warm.subprocess, "run", setup)
    warm.prepare(path, "install dependencies", root)
    assert not path.exists()
    assert {p.name for p in root.iterdir()} == {".venv"}


@pytest.mark.parametrize("change", [
    {"setup_command": "uv sync --all-extras"}, {"name": "other/repo"}, {"installation_id": 13},
    {"setup_command": ""},
])
def test_changed_configuration_cannot_reuse_snapshot(client, change):
    save_snapshot()
    assert snapshots.image_for(REPO) == "im-cached"
    assert snapshots.image_for({**REPO, **change}) is None


def test_runtime_changes_expiry_and_repository_scope_invalidate(client, monkeypatch):
    save_snapshot()
    assert snapshots.image_for({**REPO, "id": "another-repository"}) is None
    monkeypatch.setattr(snapshots, "runtime_fingerprint", lambda: "different runner/runtime")
    assert snapshots.image_for(REPO) is None


def test_expired_snapshot_is_not_reused(client):
    save_snapshot(expires_at=time.time() - 1)
    assert snapshots.image_for(REPO) is None


def test_refresh_claim_deduplicates_rollouts_and_recovers_stale_lease(client):
    first = snapshots.claim("repo", REPO)
    assert first and snapshots.claim("repo", REPO) is None
    with Session.begin() as db:
        db.get(RepositorySnapshot, "repo").lease_until = time.time() - 1
    assert snapshots.claim("repo", REPO) not in (None, first)


async def test_failed_refresh_retains_compatible_snapshot_and_backs_off(client, monkeypatch):
    save_snapshot(retry_after=0)
    build = AsyncMock(side_effect=RuntimeError("private setup output"))
    monkeypatch.setattr(snapshots, "build", build)
    await snapshots.refresh("repo", REPO)
    assert snapshots.image_for(REPO) == "im-cached"
    await snapshots.refresh("repo", REPO)
    assert build.await_count == 1
    state = snapshots.status("repo", REPO)
    assert state["state"] == "ready" and not state["refreshing"]
    assert "private setup output" not in state["last_error"]


async def test_successful_refresh_persists_and_configuration_change_rebuilds_immediately(client, monkeypatch):
    monkeypatch.setattr(snapshots, "build", AsyncMock(return_value=("im-new", "b" * 40)))
    await snapshots.refresh("repo", REPO)
    assert snapshots.image_for(REPO) == "im-new"
    assert snapshots.status("repo", REPO)["source_sha"] == "b" * 40
    assert snapshots.claim("repo", REPO) is None
    assert snapshots.claim("repo", {**REPO, "setup_command": "changed"})


async def test_shutdown_releases_build_lease(client, monkeypatch):
    monkeypatch.setattr(snapshots, "build", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await snapshots.refresh("repo", REPO)
    with Session() as db:
        assert db.get(RepositorySnapshot, "repo").lease_until == 0


async def test_restore_failure_launches_cold_without_failing_review(client, monkeypatch):
    seed_job()
    with Session.begin() as db:
        job = db.get(Job, "test-job")
        job.config = {**job.config, "harness": "codex"}
        run = db.get(Run, job.run_id)
        run.snapshot = {**run.snapshot, "repository": REPO}
    save_snapshot()
    calls = []

    def create(*args):
        calls.append(args)
        if len(args) == 5:
            raise RuntimeError("expired Modal image")
        return SimpleNamespace(object_id="sb-cold")

    monkeypatch.setattr(runtime.settings(), "public_url", "https://review.test")
    monkeypatch.setattr(runtime, "create_sandbox", create)
    monkeypatch.setattr(runtime, "watch", AsyncMock())
    monkeypatch.setattr(runtime, "terminate", AsyncMock())
    await runtime.launch("test-job", "job-token")
    assert len(calls) == 2 and calls[0][-1] == "im-cached" and len(calls[1]) == 4
    assert snapshots.image_for(REPO) is None
    with Session() as db:
        job = db.get(Job, "test-job")
        assert job.sandbox_id == "sb-cold" and job.status != "failed"
        assert any("falling back" in event["message"] for event in job.logs)


def test_snapshot_status_requires_login_and_has_no_image_or_secret(owner):
    with Session.begin() as db:
        db.add(Resource(id="repo", kind="repos", data=REPO, secret="encrypted-secret"))
    save_snapshot()
    response = owner.get("/api/repositories/repo/snapshot")
    assert response.status_code == 200 and response.json()["state"] == "ready"
    assert "im-cached" not in response.text and "encrypted-secret" not in response.text
    owner.cookies.clear()
    assert owner.get("/api/repositories/repo/snapshot").status_code == 401


async def test_maintenance_discovers_real_repository_resources_and_skips_empty_setup(owner, monkeypatch):
    warm_repo = owner.post("/api/resources/repos", json={
        "name": "org/warm", "setup_command": "uv sync --frozen",
    }).json()
    cold_repo = owner.post("/api/resources/repos", json={"name": "org/cold"}).json()
    refresh = AsyncMock()
    monkeypatch.setattr(snapshots, "refresh", refresh)
    monkeypatch.setattr(snapshots.asyncio, "sleep", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await snapshots.maintain()
    assert refresh.await_count == 1
    assert refresh.call_args.args[0] == warm_repo["id"] != cold_repo["id"]
