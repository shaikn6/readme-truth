import subprocess
import textwrap
from pathlib import Path

import pytest


@pytest.fixture
def make_repo(tmp_path: Path):
    """Write a tiny repository from {path: text}; optionally make it a git repo."""

    def make(files: dict[str, str], git: bool = False) -> Path:
        root = tmp_path / "repo"
        for rel, text in files.items():
            p = root / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(text).lstrip("\n"), encoding="utf-8")
        if git:
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
        return root

    return make


def checks(report, name: str) -> list:
    return [f for f in report.findings if f.check == name]
