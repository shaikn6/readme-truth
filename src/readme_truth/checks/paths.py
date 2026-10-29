"""File and directory paths named in prose, inline code and layout trees."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from ..markdown import Doc
from ..model import Finding, Result, Severity
from ..repo import Repo, join, normpath
from .versions import dependencies

EXTENSIONS = {
    "py",
    "md",
    "json",
    "jsonl",
    "yml",
    "yaml",
    "toml",
    "txt",
    "csv",
    "tsv",
    "sh",
    "go",
    "ts",
    "tsx",
    "js",
    "jsx",
    "ipynb",
    "cfg",
    "ini",
    "lock",
    "png",
    "jpg",
    "jpeg",
    "svg",
    "gif",
    "pdf",
    "html",
    "css",
    "sql",
    "proto",
    "rs",
    "java",
    "rb",
    "mod",
    "sum",
    "tf",
    "rego",
    "parquet",
    "pt",
    "onnx",
    "xml",
    "mjs",
    "cjs",
}
PROSE_PATH_RE = re.compile(r"(?<![\w/.:@-])((?:\.{1,2}/)?[\w.-]+(?:/[\w.-]+)+\.([A-Za-z]{1,8}))(?![\w/])")
TREE_RE = re.compile(r"^(?P<lead>[\s│|]*)(?:├──|└──|\|--|`--|\+--)\s*(?P<name>[^#\s][^#]*?)\s*(?:#.*)?$")
PREFIX_RE = re.compile(r"\bprefix(?:ed|es)?\b", re.IGNORECASE)  # `ollama/` as a model-name prefix, not a dir
# The README says the path is created, downloaded, supplied by the reader, or deliberately not committed.
OUTPUT_WORDS = re.compile(
    r"\b(writes?|written|creates?|generated?|generates|produces?|outputs?|saved?|saves|"
    r"downloads?|downloaded|lands? in|appears? in|will be|you get|after running|put|place|supply|your|"
    r"not included|not committed|(?:git)?-?ignored|kept out of git|not checked in|not in the repo)\b",
    re.IGNORECASE,
)


def _candidate(token: str) -> str | None:
    token = token.strip().strip("'\"").rstrip(".,;:)")
    if not token or " " in token or "://" in token or token.startswith(("~", "$", "/", "-", "@", "<", "#")):
        return None
    if any(ch in token for ch in "*?<>{}[]|=,$%`\\") or token.count(":") or token in (".", "..", "./"):
        return None
    if re.fullmatch(r"[\d./]+", token):  # numbers, dates, ratios
        return None
    base = token.rstrip("/")
    ext = PurePosixPath(base).suffix.lstrip(".")
    if "/" not in token and ext.lower() not in EXTENSIONS:
        return None
    return token


class PathResolver:
    def __init__(self, repo: Repo, doc_dir: str) -> None:
        self.repo, self.doc_dir = repo, doc_dir

    def resolve(self, token: str) -> bool:
        rel = normpath(token)
        if self.repo.exists(rel) or self.repo.exists(join(self.doc_dir, rel)):
            return True
        if "/" not in rel:
            return rel in self.repo.basenames
        # Paths written relative to a subdirectory (cd python && ..., or tests/fixtures/...).
        suffix = "/" + rel
        return any(f.endswith(suffix) for f in self.repo.files) or any(d.endswith(suffix) for d in self.repo.dirs)


BLOCK_START = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|#|\||>)")


def _context(doc: Doc, no: int, token: str) -> str:
    """The sentence that mentions the path; sentences often wrap across lines."""
    lines = doc.lines
    start = no - 1
    while start > 0 and lines[start - 1].strip() and not BLOCK_START.match(lines[start]):
        start -= 1
    end = no
    while end < len(lines) and lines[end].strip() and not BLOCK_START.match(lines[end]):
        end += 1
    paragraph = " ".join(ln.strip() for ln in lines[start:end])
    for sentence in re.split(r"(?<=[.!?;:])\s+(?=[A-Z`(])", paragraph):
        if token in sentence:
            return sentence
    return lines[no - 1]


def _finding(doc: Doc, repo: Repo, no: int, token: str, where: str, context: str) -> Finding | None:
    ignored = repo.ignored(normpath(token))
    produced = bool(OUTPUT_WORDS.search(context))
    if produced:
        return None  # the README itself says the path is created, downloaded or not committed
    if ignored:
        sev, why = (
            Severity.NOTE,
            "missing from a fresh clone: git ignores it, so it only exists after something creates it",
        )
    elif "/" not in token.rstrip("/") and where != "layout tree":
        sev, why = Severity.NOTE, "no file or directory with this name anywhere in the repo"
    else:
        sev, why = Severity.WARNING, "no such file or directory in the repo"
    return Finding(
        check="path",
        file=doc.path,
        line=no,
        claim=token,
        claimed=f"`{token}` exists ({where})",
        actual=why,
        evidence="git ls-files",
        severity=sev,
        suggestion="fix the path, commit the file, or say how to create it",
    )


def check_paths(doc: Doc, repo: Repo) -> Result:
    res = Result()
    resolver = PathResolver(repo, join(str(PurePosixPath(doc.path).parent)))
    seen: set[tuple[int, str]] = set()
    deps = {re.sub(r"[-_.]", "", d) for d in dependencies(repo)}

    def consider(no: int, token: str, where: str, context: str) -> None:
        cand = _candidate(token)
        if cand is None or (no, cand) in seen:
            return
        base = normpath(cand)
        ext = PurePosixPath(base).suffix.lstrip(".").lower()
        if "/" in base and ext not in EXTENSIONS and not cand.endswith("/") and not repo.exists(base.split("/")[0]):
            return  # owner/name (a GitHub repo or a model id), not a repo path
        if ext and ext not in EXTENSIONS and not cand.endswith("/"):
            return  # a dotted code reference such as src/scorer.classify
        if "/" not in base and re.sub(r"(\.js|js)$|[-_.]", "", base.lower()) in deps:
            return  # a library name such as three.js
        if "/" not in base and not ext and not cand.endswith("/"):
            return
        seen.add((no, cand))
        res.claims += 1
        res.verified += 1
        if not resolver.resolve(cand) and (f := _finding(doc, repo, no, cand, where, context)):
            res.findings.append(f)

    link_lines = {(ln.line, ln.target) for ln in doc.links}
    for text, no in doc.code_spans:
        if (no, text) not in link_lines and not PREFIX_RE.search(doc.lines[no - 1]):
            consider(no, text, "inline code", _context(doc, no, text))
    for no, line in doc.prose():
        stripped = re.sub(r"`[^`]*`|\]\([^)]*\)|https?://\S+|<[^>]+>", " ", line)
        for m in PROSE_PATH_RE.finditer(stripped):
            if m.group(2).lower() in EXTENSIONS:
                consider(no, m.group(1), "prose", _context(doc, no, m.group(1)))
    for fence in doc.fences:
        for no, path in tree_paths(fence.lines, fence.start):
            consider(no, path, "layout tree", "")
    return res


def tree_paths(lines: list[str], start: int) -> list[tuple[int, str]]:
    """Rebuild full paths from a `tree`-style listing."""
    out: list[tuple[int, str]] = []
    stack: list[tuple[int, str]] = []  # (indent, dir name)
    root_skipped = False
    for i, line in enumerate(lines):
        m = TREE_RE.match(line)
        if not m:
            if not root_skipped and line.strip().endswith("/") and i == 0:
                root_skipped = True  # the listing's root (usually the repo name)
            continue
        name = m.group("name").strip()
        indent = len(m.group("lead"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if name.startswith("...") or name in ("…",) or " " in name.rstrip("/"):
            continue
        path = "/".join([d for _, d in stack] + [name.rstrip("/")])
        out.append((start + i, path + ("/" if name.endswith("/") else "")))
        if name.endswith("/"):
            stack.append((indent, name.rstrip("/")))
    return out
