"""--fix: rewrite numeric claims in place where the finding carries a safe edit."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from .model import Finding


def apply(root: str, findings: list[Finding]) -> dict[str, int]:
    """Apply every edit; return the number of edits per file."""
    by_file: dict[str, list] = defaultdict(list)
    for f in findings:
        by_file[f.file].extend(f.edits)
    done: dict[str, int] = {}
    for rel, edits in by_file.items():
        if not edits:
            continue
        path = Path(root) / rel
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        applied = set()
        for e in sorted(edits, key=lambda e: (e.line, -e.start)):
            key = (e.line, e.start, e.end)
            if key in applied:
                continue
            applied.add(key)
            line = lines[e.line - 1]
            lines[e.line - 1] = line[: e.start] + e.new + line[e.end :]
        path.write_text("".join(lines), encoding="utf-8")
        done[rel] = len(applied)
    return done
