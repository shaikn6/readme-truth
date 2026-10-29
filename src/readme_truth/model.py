"""Findings and per-check results."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import IntEnum


class Severity(IntEnum):
    NOTE = 0
    WARNING = 1
    ERROR = 2

    def __str__(self) -> str:
        return self.name.lower()


@dataclass
class Edit:
    """A safe in-place rewrite used by --fix: replace text[start:end] on one line."""

    line: int
    start: int
    end: int
    new: str


@dataclass
class Finding:
    check: str
    file: str
    line: int
    claim: str  # the README text that makes the claim
    claimed: str  # what the README says
    actual: str  # what the repository shows
    evidence: str  # where `actual` came from
    severity: Severity
    suggestion: str = ""
    edits: list[Edit] = field(default_factory=list)

    def message(self) -> str:
        return f"Claimed {self.claimed}; found {self.actual} ({self.evidence})."

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = str(self.severity)
        d["message"] = self.message()
        del d["edits"]
        d["fixable"] = bool(self.edits)
        return d


@dataclass
class Result:
    """What one check saw: how many claims, how many it could verify, findings."""

    claims: int = 0
    verified: int = 0
    findings: list[Finding] = field(default_factory=list)

    def add(self, other: Result) -> None:
        self.claims += other.claims
        self.verified += other.verified
        self.findings.extend(other.findings)
