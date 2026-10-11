"""One installable tree for the release zip and the development install."""

import os
from pathlib import Path

# Path components skipped wherever they appear.
EXCLUDE_ANYWHERE = {
    "CLAUDE.md",
    ".claude",
    ".git",
    ".venv",
    "venv",
    ".tox",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".DS_Store",
}
# Repo-root entries that are development-only.
EXCLUDE_TOP = {
    "docs",
    "tests",
    "tools",
    "dist",
    "mypy.ini",
    "tox.ini",
    "pyproject.toml",
    "requirements-dev.txt",
    "README.md",
    ".gitignore",
    ".github",
}
EXCLUDE_SUFFIX = (".pyc", ".pyo", ".db", ".db-wal", ".db-shm")


def iter_files(root):
    """Every repo-relative path to package, in deterministic order."""
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root)
        at_root = rel == Path(".")
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name not in EXCLUDE_ANYWHERE and not (at_root and name in EXCLUDE_TOP)
        )
        for name in sorted(filenames):
            if name in EXCLUDE_ANYWHERE or name.endswith(EXCLUDE_SUFFIX):
                continue
            if at_root and name in EXCLUDE_TOP:
                continue
            path = rel / name
            if not (root / path).is_symlink():
                yield path
