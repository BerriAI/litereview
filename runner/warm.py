"""Prepare a trusted baseline without review, GitHub, or test credentials."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

DEPENDENCY_DIRS = (".venv", "node_modules")
CACHE_ENV = {"CARGO_TARGET_DIR": "/root/.cache/litereview/cargo"}


def replace_source(archive, root):
    from .agent import extract_archive

    root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root.parent) as temporary:
        staged = Path(temporary) / "repo"
        extract_archive(archive, staged)
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
    # No service, job, GitHub, model, MCP, or repository test credentials.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    env.update(CACHE_ENV)
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
