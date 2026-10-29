"""Number provenance: numbers in README tables should come from a committed data file."""

from __future__ import annotations

import bisect
import re
from array import array
from functools import cached_property

from ..markdown import Doc
from ..model import Finding, Result, Severity
from ..repo import Repo

DATA_EXT = (".json", ".jsonl", ".csv", ".tsv", ".md")
SKIP_DOCS = re.compile(r"(^|/)(CHANGELOG|HISTORY|CONTRIBUTING|CODE_OF_CONDUCT|SECURITY|LICENSE)[^/]*$", re.IGNORECASE)
DATA_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")
CELL_NUM_RE = re.compile(
    r"(?<![\w.^/:-])([-+\u2212]?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|[kKM]\b|x\b|\u00d7)?(?![\w.]*\d*[/:])"
)
MAX_FILE_BYTES = 5_000_000
MAX_NUMBERS = 3_000_000


class DataPool:
    """Every number in the repo's data files, sorted, for range lookups."""

    def __init__(self, repo: Repo, exclude: set[str]) -> None:
        self.repo, self.exclude = repo, exclude

    @cached_property
    def files(self) -> list[str]:
        return [
            f
            for f in self.repo.files
            if f.lower().endswith(DATA_EXT)
            and f not in self.exclude
            and not SKIP_DOCS.search(f)
            and not f.endswith(("package-lock.json", "package.json", "tsconfig.json"))
            and "node_modules/" not in f
        ]

    @cached_property
    def values(self) -> array:
        vals: list[float] = []
        for f in self.files:
            path = self.repo.root / f
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            vals.extend(float(x) for x in DATA_NUM_RE.findall(self.repo.read(f)))
            if len(vals) > MAX_NUMBERS:
                break
        return array("d", sorted(vals))

    def has(self, lo: float, hi: float) -> bool:
        i = bisect.bisect_left(self.values, lo)
        return i < len(self.values) and self.values[i] <= hi


def _candidates(sign: str, whole: str, frac: str | None, suffix: str | None) -> list[tuple[float, float]]:
    """Intervals a data value may fall in for the README number to be a rounding of it."""
    value = float(whole.replace(",", "") + (frac or ""))
    if sign in ("-", "\u2212"):
        value = -value
    d = len(frac) - 1 if frac else 0
    unit = 10.0**-d
    scale = {"k": 1e3, "K": 1e3, "M": 1e6}.get(suffix or "", 1.0)
    out = []
    for factor in [1.0, 0.01] if suffix == "%" else [scale]:
        v, u = value * factor, unit * factor
        # Rounded half either way, or truncated, at the precision the README shows.
        out.append((min(v - u / 2, v), max(v + u / 2, v + u) - 1e-12 * max(1.0, abs(v))))
        if v < 0:
            out.append((v - u, v + u / 2))
    return out


def _skip(whole: str, frac: str | None, suffix: str | None, cell: str) -> bool:
    if frac is None and suffix is None:
        n = int(whole.replace(",", ""))
        if n <= 10 or 1900 <= n <= 2100:  # small integers and years carry no provenance signal
            return True
    return bool(re.search(r"\bv?\d+\.\d+\.\d+\b|\d{4}-\d{2}-\d{2}|\d+:\d+", cell))  # versions, dates, times


def check_numbers(doc: Doc, repo: Repo, pool: DataPool | None = None) -> Result:
    res = Result()
    if not doc.tables:
        return res
    pool = pool or DataPool(repo, {doc.path})
    if not pool.files:
        return res  # nothing committed to trace numbers to; every table would be an "orphan"
    for table in doc.tables:
        if not any(re.search(r"\d\.\d|\d\s*%", c) for _, cells in table.rows for c in cells):
            continue  # no measurements (decimals or percentages): a reference table, not results
        orphans: list[str] = []
        total = 0
        for _, cells in table.rows:
            for cell in cells:
                clean = re.sub(r"`[^`]*`|\[([^\]]*)\]\([^)]*\)|\*\*|__", r"\1", cell)
                if len(re.findall(r"[A-Za-z]", CELL_NUM_RE.sub("", clean))) > 4:
                    continue  # a prose cell ("TLS 1.2 minimum"), not a measurement
                clean = re.sub(r"±\s*[\d.]+", "", clean)  # spreads are computed when the table is written
                for m in CELL_NUM_RE.finditer(clean):
                    sign, whole, frac, suffix = m.groups()
                    if _skip(whole, frac, suffix, clean):
                        continue
                    total += 1
                    if not any(pool.has(lo, hi) for lo, hi in _candidates(sign, whole, frac, suffix)):
                        orphans.append(m.group(0).strip())
        res.claims += total
        res.verified += total
        if orphans:
            shown = ", ".join(dict.fromkeys(orphans))
            res.findings.append(
                Finding(
                    check="number-provenance",
                    file=doc.path,
                    line=table.start,
                    claim=" | ".join(table.header)[:120],
                    claimed=f"a results table with {total} numbers",
                    actual=f"{len(orphans)} of them in no committed data file: {shown[:200]}",
                    evidence=f"{len(pool.files)} data files (json/csv/tsv/md)",
                    severity=Severity.NOTE,
                    suggestion="regenerate the table from a committed results file, or commit the file it came from",
                )
            )
    return res
