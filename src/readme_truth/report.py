"""Output formats: text, json, github annotations, sarif."""

from __future__ import annotations

import json
import os

from . import __version__
from .engine import Report
from .model import Finding, Severity


def summary(report: Report) -> dict:
    claims = sum(r.claims for r in report.per_check.values())
    verified = sum(r.verified for r in report.per_check.values())
    sev = {str(s): sum(1 for f in report.findings if f.severity == s) for s in Severity}
    return {
        "files": report.files,
        "claims": claims,
        "verified": verified,
        "findings": len(report.findings),
        "by_severity": sev,
        "test_count_source": report.test_count_source,
        "by_check": {
            k: {"claims": r.claims, "verified": r.verified, "findings": len(r.findings)}
            for k, r in report.per_check.items()
        },
    }


def text(report: Report) -> str:
    out = []
    for f in report.findings:
        out.append(f"{f.file}:{f.line}: {f.severity} [{f.check}] {f.message()}")
        out.append(f"    claim: {f.claim[:160]}")
        if f.suggestion:
            out.append(f"    fix:   {f.suggestion[:160]}")
    s = summary(report)
    sev = s["by_severity"]
    out.append(
        f"{s['claims']} claims found, {s['verified']} checked against the repo, {s['findings']} findings "
        f"({sev['error']} errors, {sev['warning']} warnings, {sev['note']} notes). "
        f"Test count source: {s['test_count_source']}."
    )
    return "\n".join(out)


def as_json(report: Report) -> str:
    return json.dumps(
        {
            "tool": "readme-truth",
            "version": __version__,
            "root": report.root,
            "summary": summary(report),
            "findings": [f.to_dict() for f in report.findings],
        },
        indent=2,
    )


def _rel(report: Report, f: Finding) -> str:
    path = os.path.relpath(os.path.join(report.root, f.file))
    return path.replace(os.sep, "/")


def github(report: Report) -> str:
    level = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.NOTE: "notice"}
    lines = []
    for f in report.findings:
        msg = (f.message() + (f" Suggested: {f.suggestion}" if f.suggestion else "")).replace("%", "%25")
        msg = msg.replace("\r", "%0D").replace("\n", "%0A")
        lines.append(f"::{level[f.severity]} file={_rel(report, f)},line={f.line},title=readme-truth {f.check}::{msg}")
    lines.append(text(report).splitlines()[-1])
    return "\n".join(lines)


def sarif(report: Report) -> str:
    level = {Severity.ERROR: "error", Severity.WARNING: "warning", Severity.NOTE: "note"}
    rules = sorted({f.check for f in report.findings})
    results = [
        {
            "ruleId": f.check,
            "level": level[f.severity],
            "message": {"text": f.message() + (f" Suggested: {f.suggestion}" if f.suggestion else "")},
            "locations": [
                {"physicalLocation": {"artifactLocation": {"uri": _rel(report, f)}, "region": {"startLine": f.line}}}
            ],
        }
        for f in report.findings
    ]
    doc = {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {"name": "readme-truth", "version": __version__, "rules": [{"id": r} for r in rules]}
                },
                "results": results,
            }
        ],
    }
    return json.dumps(doc, indent=2)


FORMATS = {"text": text, "json": as_json, "github": github, "sarif": sarif}
