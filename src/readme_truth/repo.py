"""Read-only view of the repository being checked."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
from functools import cached_property
from pathlib import Path, PurePosixPath

SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "env",
    ".tox",
    "__pycache__",
    "site-packages",
    "dist",
    "build",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "vendor",
}


class Repo:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    @cached_property
    def files(self) -> list[str]:
        """Tracked files (git), or every file on disk outside the usual tool dirs."""
        try:
            out = subprocess.run(
                ["git", "-C", str(self.root), "ls-files", "-z"], capture_output=True, check=True, timeout=60
            ).stdout.decode()
            files = [f for f in out.split("\0") if f]
            if files:
                # ls-files lists deleted-but-unstaged files too; keep what exists.
                return sorted(f for f in files if (self.root / f).exists())
        except (OSError, subprocess.SubprocessError):
            pass
        found = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            rel = Path(dirpath).relative_to(self.root)
            found.extend((rel / f).as_posix() for f in filenames)
        return sorted(found)

    @cached_property
    def file_set(self) -> set[str]:
        return set(self.files)

    @cached_property
    def dirs(self) -> set[str]:
        out = {""}
        for f in self.files:
            p = PurePosixPath(f).parent
            while str(p) != ".":
                out.add(str(p))
                p = p.parent
        return out

    @cached_property
    def basenames(self) -> set[str]:
        return {PurePosixPath(f).name for f in self.files} | {PurePosixPath(d).name for d in self.dirs}

    def exists(self, rel: str) -> bool:
        rel = normpath(rel)
        return rel in self.file_set or rel in self.dirs

    def ignored(self, rel: str) -> bool:
        """True when git ignores the path (a generated, local-only location)."""
        try:
            r = subprocess.run(
                ["git", "-C", str(self.root), "check-ignore", "-q", "--no-index", normpath(rel)],
                capture_output=True,
                timeout=30,
            )
            return r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def read(self, rel: str) -> str:
        try:
            return (self.root / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def toml(self, rel: str) -> dict:
        try:
            return tomllib.loads(self.read(rel))
        except tomllib.TOMLDecodeError:
            return {}

    def json(self, rel: str) -> dict:
        try:
            data = json.loads(self.read(rel) or "{}")
        except json.JSONDecodeError:
            return {}
        return data if isinstance(data, dict) else {}

    @cached_property
    def pyproject(self) -> dict:
        return self.toml("pyproject.toml")

    @cached_property
    def package_json(self) -> dict:
        return self.json("package.json")

    def glob(self, pattern: str) -> list[str]:
        rx = re.compile(pattern)
        return [f for f in self.files if rx.search(f)]


def normpath(rel: str) -> str:
    rel = rel.strip()
    if rel.startswith("./"):
        rel = rel[2:]
    rel = os.path.normpath(rel) if rel not in ("", ".", "./") else ""
    return "" if rel == "." else rel.replace(os.sep, "/").rstrip("/")


def join(*parts: str) -> str:
    """Join repo-relative parts, ignoring empty ones ('' is the repo root)."""
    return normpath("/".join(p for p in parts if p not in ("", ".")))
