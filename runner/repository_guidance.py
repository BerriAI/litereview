"""Index repository standards without trusting repository executable configuration."""

import os
from pathlib import Path

GUIDANCE_NAMES = {"AGENTS.md", "AGENTS.override.md", "CLAUDE.md", "CONTRIBUTING.md", "CODING_STANDARDS.md"}
EXCLUDED_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__"}


def guidance_index(repo: Path) -> list[str]:
    """Keep full paths; reviewers load the guides applicable to each inspected file."""
    paths = []
    for root, dirs, files in os.walk(repo, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not (Path(root) / d).is_symlink())
        for name in sorted(GUIDANCE_NAMES.intersection(files)):
            path = Path(root) / name
            if not path.is_symlink():
                paths.append(path.relative_to(repo).as_posix())
    return paths
