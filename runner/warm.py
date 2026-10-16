"""Prepare a trusted baseline without review, GitHub, or test credentials."""

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

DEPENDENCY_DIRS = (".venv", "node_modules")
CACHE_ENV = {"CARGO_TARGET_DIR": "/root/.cache/litereview/cargo"}
NATIVE_ARTIFACTS = Path("/opt/litereview/native-artifacts.json")
NATIVE_SUFFIXES = {".so", ".pyd", ".node"}


def preserve_unchanged_times(previous, staged):
    """Match normal checkout semantics without trusting archive mtimes."""
    unchanged = set()
    previous_root = previous.resolve()
    for directory, dirs, files in os.walk(staged, topdown=False):
        path = Path(directory)
        old_directory = previous / path.relative_to(staged)
        # Validate the parent once, then lstat each leaf without following links.
        # The staged tree contains only files/directories from extract_archive.
        if (not old_directory.is_dir() or old_directory.is_symlink()
                or not old_directory.resolve().is_relative_to(previous_root)):
            continue
        with os.scandir(old_directory) as entries:
            previous_entries = {entry.name: entry for entry in entries}
        for name in files:
            target = path / name
            old = previous_entries.get(name)
            if old is None:
                continue
            old_stat = old.stat(follow_symlinks=False)
            target_stat = target.stat()
            if stat.S_ISREG(old_stat.st_mode) and old_stat.st_size == target_stat.st_size:
                with open(old.path, "rb") as a, target.open("rb") as b:
                    same = hashlib.file_digest(a, "sha256").digest() == hashlib.file_digest(b, "sha256").digest()
                if same:
                    os.utime(target, ns=(target_stat.st_atime_ns, old_stat.st_mtime_ns))
                    unchanged.add(target)
        if (previous_entries.keys() == set(dirs + files)
                and all(path / name in unchanged for name in dirs + files)):
            os.utime(path, ns=(path.stat().st_atime_ns, old_directory.stat().st_mtime_ns))
            unchanged.add(path)


def restore_native_outputs(previous, staged):
    if not NATIVE_ARTIFACTS.exists():
        return
    artifacts = json.loads(NATIVE_ARTIFACTS.read_text())
    NATIVE_ARTIFACTS.unlink()
    for name in artifacts:
        old, target = previous / name, staged / name
        if (target.parent.is_dir() and not target.exists() and old.is_file() and not old.is_symlink()
                and old.resolve().is_relative_to(previous.resolve())
                and target.resolve().is_relative_to(staged.resolve())):
            shutil.copy2(old, target)


def replace_source(archive, root):
    from .agent import extract_archive

    root.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=root.parent) as temporary:
        staged = Path(temporary) / "repo"
        extract_archive(archive, staged)
        preserve_unchanged_times(root, staged)
        restore_native_outputs(root, staged)
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
    original_files = {p.relative_to(root) for p in root.rglob("*") if p.is_file()}
    original_dirs = {p.relative_to(root) for p in root.rglob("*") if p.is_dir()}
    # No service, job, GitHub, model, MCP, or repository test credentials.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG") if key in os.environ}
    env.update(CACHE_ENV)
    subprocess.run(command, shell=True, cwd=root, env=env, check=True)
    # Keep baseline source for content comparisons and only deliberately cached
    # generated files. Native editable installs place extensions in the source
    # tree; deleting them while keeping their installed metadata breaks reuse.
    native = []
    for directory, dirs, files in os.walk(root):
        parent = Path(directory)
        for name in list(dirs):
            path = parent / name
            if parent == root and name in DEPENDENCY_DIRS and not path.is_symlink():
                dirs.remove(name)
            elif path.relative_to(root) not in original_dirs or path.is_symlink():
                dirs.remove(name)
                if path.is_symlink():
                    path.unlink()
                else:
                    shutil.rmtree(path)
        for name in files:
            path = parent / name
            if path.relative_to(root) in original_files and not path.is_symlink():
                continue
            if path.suffix in NATIVE_SUFFIXES and not path.is_symlink():
                native.append(str(path.relative_to(root)))
            else:
                path.unlink()
    NATIVE_ARTIFACTS.write_text(json.dumps(native))


if __name__ == "__main__":
    config = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[1]).unlink()
    prepare(Path(config["archive"]), config["command"], Path("/workspace/repo"))
