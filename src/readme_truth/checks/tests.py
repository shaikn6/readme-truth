"""Test-count and coverage claims."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..markdown import Doc
from ..model import Edit, Finding, Result, Severity
from ..repo import Repo
from ..testcount import Count

NUM = r"(\d{1,3}(?:,\d{3})+|\d+)"
APPROX = r"(~|≈|about |around |roughly |approximately |nearly |almost |over |more than |at least )?"
BADGE_RE = re.compile(
    r"shields\.io/badge/[^/\s)\"-]*?tests?[^/\s)\"-]*-" + NUM + r"(\+|%2B)?(?:%20|_|\s)*(?:passing|passed|tests?)?",
    re.IGNORECASE,
)
FRACTION_RES = [
    re.compile(r"\bpass(?:es|ed|ing)?\s+" + NUM + r"\s*/\s*" + NUM + r"\b", re.IGNORECASE),
    re.compile(r"(?<![\w.,/])" + NUM + r"\s*/\s*" + NUM + r"\s+(?:tests?\s+)?pass", re.IGNORECASE),
]
PASSED_RE = re.compile(
    r"(?<![\w.,/$-])" + APPROX + NUM + r"(\+)?\**\s+(?:tests?\s+)?(?:passed|passing)\b", re.IGNORECASE
)
TESTS_RE = re.compile(
    r"(?<![\w.,/$-])" + APPROX + NUM + r"(\+)?\**\s+((?:[A-Za-z-]+\s+){0,2}?)"
    r"(tests|test cases|test functions)\b(?!\s*(?:/|per\b))",
    re.IGNORECASE,
)
FILES_RE = re.compile(r"\b(?:across|in)\s+" + NUM + r"\s+(?:test\s+)?(?:files|modules)\b", re.IGNORECASE)
PATH_RE = re.compile(r"[\w./-]*[\w-]+(?:\.(?:py|go|ts|js|tsx|jsx)|/)")
# Words allowed between the number and "tests" ("231 offline tests"). Anything else ("500 while offline
# tests", "2 scikit-learn tests") is not a whole-suite count.
SUITE_WORDS = {
    "unit",
    "offline",
    "automated",
    "hermetic",
    "pytest",
    "python",
    "go",
    "passing",
    "green",
    "total",
    "deterministic",
    "fast",
    "local",
    "core",
    "independent",
    "isolated",
}
FAILED_RUN_RE = re.compile(r"\b[1-9]\d* (?:failed|errors?)\b")
SUBSET_CONTEXT = re.compile(r"\b(adds?|added|adding|includes? \d|another|per (?:file|module|rule))\b", re.IGNORECASE)


@dataclass
class Claim:
    line: int
    kind: str  # tests | functions | files
    value: int
    approx: str  # "" exact, "min" at least, "about" roughly
    text: str
    spans: list[tuple[int, int]]  # number positions on the line, for --fix
    scope: list[str]  # test files the claim is limited to (empty: whole suite)
    lang: str  # go | python | "" (no hint)


def _int(s: str) -> int:
    return int(s.replace(",", ""))


def _approx(prefix: str | None, plus: str | None) -> str:
    p = (prefix or "").strip().lower()
    if plus or p in ("over", "more than", "at least"):
        return "min"
    return "about" if p else ""


LANG_RE = re.compile(r"\b(go test|go)\b(?! to)|\b(pytest|python)\b", re.IGNORECASE)


def _lang(line: str, pos: int) -> str:
    """The language named closest to a claim: 'Go: 3 tests ... Python: 2 tests' names one per claim."""
    hints = [(abs(m.start() - pos), "go" if m.group(1) else "python") for m in LANG_RE.finditer(line)]
    return min(hints)[1] if hints else ""


def _scope(line: str, counts: list[Count]) -> list[str] | None:
    """Test files a claim is limited to; None when it names a path we cannot tie to tests."""
    all_files = {f for c in counts for f in c.per_path}
    for m in PATH_RE.finditer(line):
        p = m.group(0).strip("./") if not m.group(0).endswith("/") else m.group(0).strip("./") + "/"
        if not p or "://" in line[max(0, m.start() - 8) : m.start()]:
            continue
        if p in all_files:
            return [p]
        under = [f for f in all_files if f.startswith(p.rstrip("/") + "/")]
        if under and len(under) < len(all_files):
            return under
    return []


def find_claims(doc: Doc, counts: list[Count]) -> list[Claim]:
    claims: list[Claim] = []
    for no, line in enumerate(doc.lines, 1):
        # Output of a failing run ("1 failed, 435 passed") illustrates an error, it does not size the suite.
        if not SUBSET_CONTEXT.search(line) and not FAILED_RUN_RE.search(line):
            claims += _line_claims(no, line, counts)
    return [c for c in claims if c.value >= 2]


def _line_claims(no: int, line: str, counts: list[Count]) -> list[Claim]:
    out: list[Claim] = []
    taken: list[tuple[int, int]] = []
    scope = _scope(line, counts) or []

    def add(kind: str, value: int, approx: str, spans: list[tuple[int, int]], m: re.Match) -> None:
        if any(s < e2 and e > s2 for s, e in spans for s2, e2 in taken):
            return  # the same number already matched a more specific pattern
        taken.extend(spans)
        out.append(Claim(no, kind, value, approx, m.group(0).strip(), spans, scope, _lang(line, spans[0][0])))

    for m in BADGE_RE.finditer(line):
        add("tests", _int(m.group(1)), "min" if m.group(2) else "", [m.span(1)], m)
    for rx in FRACTION_RES:
        for m in rx.finditer(line):
            spans = [m.span(1), m.span(2)] if m.group(1) == m.group(2) else [m.span(2)]
            add("tests", _int(m.group(2)), "", spans, m)
    for m in PASSED_RE.finditer(line):
        add("tests", _int(m.group(2)), _approx(m.group(1), m.group(3)), [m.span(2)], m)
    for m in TESTS_RE.finditer(line):
        words = m.group(4).lower().split()
        if any(w not in SUITE_WORDS for w in words):
            continue
        kind = "functions" if m.group(5).lower() == "test functions" else "tests"
        add(kind, _int(m.group(2)), _approx(m.group(1), m.group(3)), [m.span(2)], m)
    if taken:
        for m in FILES_RE.finditer(line):
            add("files", _int(m.group(1)), "", [m.span(1)], m)
    return out


def _matches(claim: Claim, actual: int, alternatives: tuple[int, ...] = ()) -> bool:
    if claim.kind == "tests" and not claim.approx and claim.value in alternatives:
        return True  # the count with one declared marker deselected (`-m "not network"`)
    if claim.approx == "min":
        return actual >= claim.value
    if claim.approx == "about":
        return abs(actual - claim.value) <= 0.1 * max(actual, claim.value)
    return actual == claim.value


def _pick(claim: Claim, counts: list[Count]) -> tuple[int, Count] | None:
    """The count a claim should be compared with, or None when it cannot be checked."""
    if claim.lang:
        lang_counts = [c for c in counts if ("Go" in c.source) == (claim.lang == "go")]
        counts = lang_counts or counts
    if len(counts) != 1:
        return None
    c = counts[0]
    if claim.scope:
        if not c.per_path:
            return None
        return sum(c.per_path.get(f, 0) for f in claim.scope), c
    value = {"tests": c.tests, "functions": c.functions, "files": c.files}[claim.kind]
    return (value, c) if value is not None else None


def check_test_counts(doc: Doc, repo: Repo, counts: list[Count]) -> Result:
    res = Result()
    claims = find_claims(doc, counts)
    res.claims = len(claims)
    for cl in claims:
        if not counts:
            continue
        picked = _pick(cl, counts)
        if picked is None:
            # Several suites (say Go and Python): accept a match with any of them.
            if any(_matches(cl, getattr(c, cl.kind) or 0) for c in counts):
                res.verified += 1
            continue
        actual, c = picked
        alternatives = tuple(c.tests - n for n in c.marker_counts.values()) if not cl.scope else ()
        if _matches(cl, actual, alternatives):
            res.verified += 1
            continue
        if c.exact:
            sev = Severity.ERROR
        elif c.lower_bound and actual > cl.value and cl.approx != "min":
            sev = Severity.ERROR  # even the lower bound is above the claim
        elif c.lower_bound:
            continue  # the true count may be higher; cannot tell
        else:
            sev = Severity.WARNING
        res.verified += 1
        noun = {"tests": "tests", "functions": "test functions", "files": "test files"}[cl.kind]
        scope = f" in {', '.join(cl.scope)}" if cl.scope else ""
        edits = []
        if not cl.approx and c.exact:
            edits = [Edit(cl.line, s, e, str(actual)) for s, e in cl.spans]
        line = doc.lines[cl.line - 1]
        fixed = line
        for ed in sorted(edits, key=lambda x: -x.start):
            fixed = fixed[: ed.start] + ed.new + fixed[ed.end :]
        res.findings.append(
            Finding(
                check="test-count",
                file=doc.path,
                line=cl.line,
                claim=cl.text,
                claimed=f"{cl.value}{'+' if cl.approx == 'min' else ''} {noun}{scope}",
                actual=f"{actual}{'' if c.exact else (' (lower bound)' if c.lower_bound else ' (estimate)')}",
                evidence=c.source,
                severity=sev,
                suggestion=fixed.strip() if edits else _count_hint(actual, c),
                edits=edits,
            )
        )
    res.findings.extend(_contradictions(doc, claims, counts))
    return res


def _count_hint(actual: int, c: Count) -> str:
    """What to write instead: the number if it is exact, otherwise how to get the real one."""
    if c.lower_bound and not c.exact:
        return (
            f"the real count is at least {actual}: run `pytest --collect-only -q` "
            "(or your runner's equivalent) and use that number"
        )
    return f"update the count to {actual}"


def _contradictions(doc: Doc, claims: list[Claim], counts: list[Count]) -> list[Finding]:
    if len(counts) != 1:
        return []  # no suite here (a profile README quoting other repos), or several suites
    whole = [c for c in claims if c.kind == "tests" and not c.approx and not c.scope]
    values = Counter(c.value for c in whole)
    if len(values) < 2:
        return []
    first = {v: next(c for c in whole if c.value == v) for v in values}
    ordered = sorted(first.values(), key=lambda c: c.line)
    where = ", ".join(f"{c.value} on line {c.line}" for c in ordered)
    return [
        Finding(
            check="contradiction",
            file=doc.path,
            line=ordered[1].line,
            claim=ordered[1].text,
            claimed=f"different test counts ({where})",
            actual="one suite",
            evidence="the README quotes the same quantity twice",
            severity=Severity.WARNING,
            suggestion="state one test count, or generate it in CI",
        )
    ]


# ---------------------------------------------------------------- coverage

COV_BADGE_RE = re.compile(r"shields\.io/badge/coverage-(\d+(?:\.\d+)?)%25", re.IGNORECASE)
COV_RES = [
    re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?%\s+(?:[\w-]+\s+){0,2}?coverage", re.IGNORECASE),
    re.compile(r"coverage(?:\s+(?:is|of|at))?:?\s+\**(\d+(?:\.\d+)?)\s?%", re.IGNORECASE),
    re.compile(r"\bcov(?:erage)?\s+(?:gate|floor|minimum|threshold)\s+(?:of\s+)?\**(\d+(?:\.\d+)?)\s?%", re.IGNORECASE),
]
# Gate phrases that do not repeat the word "coverage"; used only on lines that mention it.
GATE_RES = [
    re.compile(r"(?:fails? (?:below|under)|enforced at|fail[-_ ]under[= ]?)\s*\**(\d+(?:\.\d+)?)\s?%", re.IGNORECASE),
    re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s?%\s+(?:gate|floor|threshold|minimum)\b", re.IGNORECASE),
]
GATE_BEFORE = re.compile(
    r"(gate|fail[-_ ]?under|enforced at|enforces?|minimum|at least|floor|threshold|≥|>=|below|"
    r"under)\W*(?:of\W*)?\**$",
    re.IGNORECASE,
)
GATE_AFTER = re.compile(r"^\**\s*(?:line\s+)?(?:coverage\s+)?(gate|floor|minimum|threshold|required)", re.IGNORECASE)
FAIL_UNDER_RE = re.compile(r"(?:--cov-fail-under[= ]|fail_under\s*[=:]\s*)(\d+(?:\.\d+)?)")


def coverage_gates(repo: Repo) -> set[float]:
    gates: set[float] = set()
    fu = repo.pyproject.get("tool", {}).get("coverage", {}).get("report", {}).get("fail_under")
    if fu is not None:
        gates.add(float(fu))
    sources = [
        f
        for f in repo.files
        if f in ("pyproject.toml", "setup.cfg", "tox.ini", ".coveragerc", "Makefile", "noxfile.py", "pytest.ini")
        or re.match(r"\.github/workflows/.*\.ya?ml$", f)
    ]
    for f in sources:
        gates.update(float(m) for m in FAIL_UNDER_RE.findall(repo.read(f)))
    return gates


def read_coverage(path: str) -> float | None:
    """Total line coverage in percent from coverage.xml, coverage.json, lcov.info or a Go profile."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    try:
        if text.lstrip().startswith("<"):
            return float(ET.fromstring(text).get("line-rate", "nan")) * 100
        if text.lstrip().startswith("{"):
            return float(json.loads(text)["totals"]["percent_covered"])
    except (ET.ParseError, KeyError, ValueError, json.JSONDecodeError):
        return None
    if text.startswith("mode:"):
        total = covered = 0
        for ln in text.splitlines()[1:]:
            parts = ln.split()
            if len(parts) == 3:
                total += int(parts[1])
                covered += int(parts[1]) if int(parts[2]) > 0 else 0
        return 100 * covered / total if total else None
    lf = sum(int(x) for x in re.findall(r"^LF:(\d+)", text, re.MULTILINE))
    lh = sum(int(x) for x in re.findall(r"^LH:(\d+)", text, re.MULTILINE))
    return 100 * lh / lf if lf else None


def _same_at_precision(claimed: str, actual: float) -> bool:
    d = len(claimed.split(".")[1]) if "." in claimed else 0
    value, unit = float(claimed), 10**-d
    # coverage.py rounds; some tools truncate. Accept either.
    return abs(actual - value) <= unit / 2 + 1e-9 or value <= actual < value + unit


def check_coverage(doc: Doc, repo: Repo, coverage_file: str | None) -> Result:
    res = Result()
    actual = read_coverage(coverage_file) if coverage_file else None
    if actual is None and not coverage_file:
        for name in ("coverage.xml", "coverage.json", "lcov.info", "coverage/lcov.info", "cover.out", "coverage.out"):
            if name in repo.file_set:
                actual, coverage_file = read_coverage(str(repo.root / name)), name
                break
    gates = coverage_gates(repo)
    for no, line in enumerate(doc.lines, 1):
        hits: list[re.Match] = list(COV_BADGE_RE.finditer(line))
        for rx in COV_RES + (GATE_RES if re.search(r"\bcov", line, re.IGNORECASE) else []):
            hits += [m for m in rx.finditer(line) if not any(h.span(1) == m.span(1) for h in hits)]
        for m in hits:
            res.claims += 1
            value = m.group(1)
            before, after = line[: m.start(1)][-30:], line[m.end(1) :].lstrip("% ")
            is_gate = (
                bool(GATE_BEFORE.search(before.rstrip()) or GATE_AFTER.match(after)) or "gate" in m.group(0).lower()
            )
            if is_gate:
                if not gates:
                    continue
                res.verified += 1
                if float(value) not in gates:
                    res.findings.append(
                        Finding(
                            check="coverage",
                            file=doc.path,
                            line=no,
                            claim=m.group(0).strip(),
                            claimed=f"a {value}% coverage gate",
                            actual="fail-under " + ", ".join(f"{g:g}%" for g in sorted(gates)),
                            evidence="coverage settings in the repo",
                            severity=Severity.WARNING,
                            suggestion=f"say {max(gates):g}% or change the configured gate",
                        )
                    )
                continue
            if actual is None:
                continue
            res.verified += 1
            if not _same_at_precision(value, actual):
                d = len(value.split(".")[1]) if "." in value else 0
                new = f"{actual:.{d}f}"
                res.findings.append(
                    Finding(
                        check="coverage",
                        file=doc.path,
                        line=no,
                        claim=m.group(0).strip(),
                        claimed=f"{value}% coverage",
                        actual=f"{actual:.2f}%",
                        evidence=f"coverage report {coverage_file}",
                        severity=Severity.ERROR,
                        suggestion=(line[: m.start(1)] + new + line[m.end(1) :]).strip(),
                        edits=[Edit(no, m.start(1), m.end(1), new)],
                    )
                )
    return res
