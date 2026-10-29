"""Command-line entry point."""

from __future__ import annotations

import argparse
import sys

from . import __version__
from .engine import CHECKS, Options, run
from .fix import apply
from .model import Severity
from .report import FORMATS

FAIL_ON = {"error": Severity.ERROR, "warning": Severity.WARNING, "note": Severity.NOTE, "never": None}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="readme-truth", description="Check a README's claims against the repository.")
    p.add_argument("--version", action="version", version=f"readme-truth {__version__}")
    sub = p.add_subparsers(dest="command", required=True)
    c = sub.add_parser("check", help="check README claims")
    c.add_argument("path", nargs="?", default=".", help="repository root (default: .)")
    c.add_argument(
        "--file",
        action="append",
        default=[],
        help="Markdown file to check, relative to the root (repeatable; default README.md)",
    )
    c.add_argument("--docs", action="store_true", help="also check other top-level *.md files and docs/**/*.md")
    c.add_argument("--format", choices=sorted(FORMATS), default="text")
    c.add_argument(
        "--fail-on",
        choices=list(FAIL_ON),
        default="error",
        help="exit 1 when a finding at or above this severity exists (default: error)",
    )
    c.add_argument(
        "--collect",
        action="store_true",
        help="count tests with the real collector (pytest --collect-only, go test -list); imports test code",
    )
    c.add_argument("--test-count-cmd", help="shell command whose last printed integer is the test count")
    c.add_argument("--test-count-file", help="CI-produced count: JUnit XML, pytest summary output, or an integer")
    c.add_argument("--coverage-file", help="coverage.xml, coverage.json, lcov.info or a Go cover profile")
    c.add_argument("--check-links", action="store_true", help="also request external URLs (network)")
    c.add_argument("--select", help="comma-separated checks to run: " + ",".join(CHECKS))
    c.add_argument("--ignore", help="comma-separated checks to skip")
    c.add_argument("--fix", action="store_true", help="rewrite simple numeric claims (test counts, coverage) in place")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    select = set(CHECKS)
    for flag, keep in ((args.select, True), (args.ignore, False)):
        if flag:
            names = {n.strip() for n in flag.split(",") if n.strip()}
            unknown = names - set(CHECKS)
            if unknown:
                print(f"readme-truth: unknown check(s): {', '.join(sorted(unknown))}", file=sys.stderr)
                return 2
            select = select & names if keep else select - names
    opts = Options(
        files=args.file,
        docs=args.docs,
        collect=args.collect,
        test_count_cmd=args.test_count_cmd,
        test_count_file=args.test_count_file,
        coverage_file=args.coverage_file,
        check_links=args.check_links,
        select=select,
    )
    report = run(args.path, opts)
    if not report.files:
        print(f"readme-truth: no README.md in {report.root}", file=sys.stderr)
        return 2
    if args.fix:
        done = apply(report.root, report.findings)
        for f, n in done.items():
            print(f"fixed {n} claim(s) in {f}", file=sys.stderr)
        if done:
            report = run(args.path, opts)
    print(FORMATS[args.format](report))
    threshold = FAIL_ON[args.fail_on]
    if threshold is not None and any(f.severity >= threshold for f in report.findings):
        return 1
    return 0
