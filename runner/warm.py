"""Prepare a trusted baseline without review, GitHub, or test credentials."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

DEPENDENCY_DIRS = (".venv", "node_modules")
SOURCE_STAMPS = Path("/opt/litereview/source-stamps.json")


def source_stamps(root):
    stamps = {}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            stamps[str(path.relative_to(root))] = [digest, path.stat().st_mtime_ns]
    return stamps


def restore_stamps(root):
    if not SOURCE_STAMPS.exists():
        return
    stamps = json.loads(SOURCE_STAMPS.read_text())
    SOURCE_STAMPS.unlink()
    for path in root.rglob("*"):
        saved = stamps.get(str(path.relative_to(root)))
        if saved and path.is_file() and not path.is_symlink():
            with path.open("rb") as source:
                digest = hashlib.file_digest(source, "sha256").hexdigest()
            if digest == saved[0]:
                os.utime(path, ns=(path.stat().st_atime_ns, saved[1]))


def replace_source(archive, root):
    from .agent import extract_archive

    root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root.parent) as temporary:
        staged = Path(temporary) / "repo"
        extract_archive(archive, staged)
        # Like a Git checkout, leave timestamps unchanged for identical files.
        # uv uses build-input timestamps; rewriting an identical pyproject would
        # otherwise rebuild every local editable package on every review.
        restore_stamps(staged)
        for name in DEPENDENCY_DIRS:
            cached, target = root / name, staged / name
            if cached.is_dir() and not cached.is_symlink() and not target.exists():
                shutil.move(cached, target)
        if root.exists():
            shutil.rmtree(root)
        staged.rename(root)


def prepare(archive, command, root):
    from .agent import extract_archive

    with archive.open("rb") as source:
        extract_archive(source, root)
    archive.unlink()
    SOURCE_STAMPS.write_text(json.dumps(source_stamps(root)))
    # No service, job, GitHub, model, MCP, or repository test credentials.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    subprocess.run(command, shell=True, cwd=root, env=env, check=True)
    # Preserve explicit dependency directories, never stale baseline source or
    # generated files. Package-manager download caches elsewhere also survive.
    for path in root.iterdir():
        if path.name in DEPENDENCY_DIRS and path.is_dir() and not path.is_symlink():
            continue
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[1]).unlink()
    prepare(Path(config["archive"]), config["command"], Path("/workspace/repo"))
