"""Licence named in the README (badge or text) versus the LICENSE file."""

from __future__ import annotations

import re

from ..markdown import Doc
from ..model import Finding, Result, Severity
from ..repo import Repo

# (id, README pattern, LICENSE-file fingerprint)
LICENSES = [
    ("AGPL-3.0", r"\bAGPL[- ]?v?3", r"GNU AFFERO GENERAL PUBLIC LICENSE"),
    ("LGPL", r"\bLGPL", r"GNU LESSER GENERAL PUBLIC LICENSE"),
    (
        "GPL-3.0",
        r"\bGPL[- ]?v?3|GNU GPL ?v?3|GNU General Public License v?3",
        r"GNU GENERAL PUBLIC LICENSE\s+Version 3",
    ),
    ("GPL-2.0", r"\bGPL[- ]?v?2|GNU GPL ?v?2", r"GNU GENERAL PUBLIC LICENSE\s+Version 2"),
    ("Apache-2.0", r"\bApache(?:[- ]License)?[- ,]*(?:v(?:ersion)?\s*)?2(?:\.0)?", r"Apache License\s+Version 2\.0"),
    ("MPL-2.0", r"\bMPL[- ]?2|Mozilla Public License", r"Mozilla Public License"),
    ("BSD-3-Clause", r"\bBSD[- ]3|3-Clause BSD|BSD 3-Clause", r"Neither the name of"),
    ("BSD-2-Clause", r"\bBSD[- ]2|2-Clause BSD|BSD 2-Clause", r"Redistribution and use in source and binary forms"),
    ("ISC", r"\bISC\b", r"Permission to use, copy, modify, and/or distribute"),
    ("Unlicense", r"\bUnlicense\b", r"This is free and unencumbered software"),
    ("MIT", r"\bMIT\b", r"Permission is hereby granted, free of charge"),
    ("Proprietary", r"\bAll Rights Reserved\b|\bproprietary\b", r"All Rights Reserved"),
]
BADGE_RE = re.compile(
    r"shields\.io/(?:badge/licen[sc]e-([^/?)\s\"]+)-[0-9a-zA-Z]+(?:\.svg)?(?=[?)\s\"]|$)|github/license/[^)\s\"]+)",
    re.IGNORECASE,
)
STATEMENT_RE = re.compile(
    r"\b(licen[cs]ed under|released under|available under|distributed under|under the|"
    r"licen[cs]e:|is licen[cs]ed)\b",
    re.IGNORECASE,
)
THIS_RE = re.compile(
    r"\bthis (project|repo|repository|code|library|package|tool|software|work)\b|"
    r"\b(the|all) (code|source|project)\b|^\W*(released|licensed|distributed|available) under",
    re.IGNORECASE,
)
OTHER_WORK_RE = re.compile(
    r"\b(from|uses?|based on|portions?|third[- ]party|dependenc\w+|logo|dataset|data|model|"
    r"weights|fonts?|icons?|images?|assets?|vendored|upstream|original)\b",
    re.IGNORECASE,
)
LICENSE_FILES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "COPYING", "LICENSE-MIT")


def identify(text: str, file_fingerprint: bool) -> str | None:
    for lid, readme_pat, file_pat in LICENSES:
        if re.search(file_pat if file_fingerprint else readme_pat, text, re.IGNORECASE if not file_fingerprint else 0):
            return lid
    return None


def check_license(doc: Doc, repo: Repo) -> Result:
    res = Result()
    claims: list[tuple[int, str, str]] = []
    in_license_section = False
    for no, line in enumerate(doc.lines, 1):
        if no in doc.in_fence:
            continue
        if re.match(r"^\s{0,3}#{1,6}\s", line):
            in_license_section = bool(re.search(r"licen[cs]e", line, re.IGNORECASE))
            continue
        for m in BADGE_RE.finditer(line):
            if m.group(1):
                lid = identify(m.group(1).replace("--", "-").replace("%20", " ").replace("_", " "), False)
                if lid:
                    claims.append((no, lid, m.group(0)))
        text = BADGE_RE.sub(" ", line)
        about_this = bool(THIS_RE.search(text)) and bool(STATEMENT_RE.search(text))
        # In a License section a bare "MIT" line is the claim; a long sentence there may describe other work.
        short = in_license_section and len(re.findall(r"\w+", re.sub(r"\([^)]*\)", "", text))) <= 8
        if (about_this or short) and not OTHER_WORK_RE.search(text) and (lid := identify(text, False)):
            claims.append((no, lid, line.strip()))
    if not claims:
        return res
    found = next((f for f in LICENSE_FILES if f in repo.file_set), None)
    actual = identify(repo.read(found), True) if found else None
    for no, lid, text in {(n, i): (n, i, t) for n, i, t in claims}.values():
        res.claims += 1
        if found is None:
            res.verified += 1
            res.findings.append(
                Finding(
                    check="license",
                    file=doc.path,
                    line=no,
                    claim=text,
                    claimed=f"{lid} licence",
                    actual="no LICENSE file",
                    evidence="repo root",
                    severity=Severity.ERROR,
                    suggestion=f"add a LICENSE file with the {lid} text",
                )
            )
            continue
        if actual is None:
            continue
        res.verified += 1
        if lid != actual:
            res.findings.append(
                Finding(
                    check="license",
                    file=doc.path,
                    line=no,
                    claim=text,
                    claimed=f"{lid} licence",
                    actual=f"{actual} text in {found}",
                    evidence=found,
                    severity=Severity.ERROR,
                    suggestion=f"say {actual}, or replace {found}",
                )
            )
    return res
