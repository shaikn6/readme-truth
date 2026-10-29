"""Run every check over the selected Markdown files."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from . import testcount
from .checks.commands import check_commands
from .checks.license import check_license
from .checks.links import check_links
from .checks.numbers import DataPool, check_numbers
from .checks.paths import check_paths
from .checks.tests import check_coverage, check_test_counts
from .checks.versions import check_versions
from .markdown import Doc, parse
from .model import Finding, Result
from .repo import Repo

CHECKS = ("test-count", "coverage", "version", "path", "command", "link", "license", "number-provenance")
IGNORE_RE = re.compile(r"<!--\s*readme-truth:\s*ignore\s*-->")
SKIP_DOCS = re.compile(r"^(CHANGELOG|HISTORY|CODE_OF_CONDUCT|LICEN[CS]E)", re.IGNORECASE)


@dataclass
class Options:
    files: list[str] = field(default_factory=list)
    docs: bool = False
    collect: bool = False
    test_count_cmd: str | None = None
    test_count_file: str | None = None
    coverage_file: str | None = None
    check_links: bool = False
    select: set[str] = field(default_factory=lambda: set(CHECKS))


@dataclass
class Report:
    root: str
    files: list[str]
    per_check: dict[str, Result]
    test_count_source: str

    @property
    def findings(self) -> list[Finding]:
        out = [f for r in self.per_check.values() for f in r.findings]
        return sorted(out, key=lambda f: (f.file, f.line, f.check))


def markdown_files(repo: Repo, opts: Options) -> list[str]:
    if opts.files:
        return opts.files
    readme = next((f for f in ("README.md", "readme.md", "Readme.md") if f in repo.file_set), None)
    files = [readme] if readme else []
    if opts.docs:
        files += [
            f
            for f in repo.files
            if f.lower().endswith(".md")
            and f != readme
            and (("/" not in f and not SKIP_DOCS.match(f)) or f.startswith("docs/"))
        ]
    return files


def test_counts(repo: Repo, opts: Options) -> list[testcount.Count]:
    if opts.test_count_file:
        c = testcount.from_file(opts.test_count_file)
        return [c] if c else []
    if opts.test_count_cmd:
        c = testcount.from_command(repo, opts.test_count_cmd)
        return [c] if c else []
    if opts.collect:
        c = testcount.collect(repo)
        if c:
            return [c]
    return testcount.static_counts(repo)


def run(root: str | Path, opts: Options | None = None) -> Report:
    opts = opts or Options()
    repo = Repo(root)
    files = markdown_files(repo, opts)
    counts = test_counts(repo, opts) if "test-count" in opts.select else []
    pool = DataPool(repo, set(files))
    per_check = {name: Result() for name in CHECKS if name in opts.select}
    for f in files:
        doc = parse(repo.read(f), f)
        for name, result in per_check.items():
            r = _one(name, doc, repo, opts, counts, pool)
            r.findings = [x for x in r.findings if not _suppressed(doc.lines, x.line)]
            result.add(r)
    source = "; ".join(c.source for c in counts) or "none found"
    return Report(str(repo.root), files, per_check, source)


def _one(name: str, doc: Doc, repo: Repo, opts: Options, counts: list[testcount.Count], pool: DataPool) -> Result:
    if name == "test-count":
        return check_test_counts(doc, repo, counts)
    if name == "coverage":
        return check_coverage(doc, repo, opts.coverage_file)
    if name == "version":
        return check_versions(doc, repo)
    if name == "path":
        return check_paths(doc, repo)
    if name == "command":
        return check_commands(doc, repo)
    if name == "link":
        return check_links(doc, repo, opts.check_links)
    if name == "license":
        return check_license(doc, repo)
    return check_numbers(doc, repo, pool)


def _suppressed(lines: list[str], line: int) -> bool:
    here = lines[line - 1] if 0 < line <= len(lines) else ""
    above = lines[line - 2] if 1 < line <= len(lines) + 1 else ""
    return bool(IGNORE_RE.search(here) or IGNORE_RE.search(above))
