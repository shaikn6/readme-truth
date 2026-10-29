"""Runtime and framework version claims: 'Python 3.11+', 'Go 1.26', 'Node 20', 'React 19'."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from ..markdown import Doc, heading_at, sentence_at
from ..model import Finding, Result, Severity
from ..repo import Repo

V = r"(\d+(?:\.\d+){0,2})"
RUNTIME_RE = re.compile(
    r"\b(Python|Golang|Go|Node(?:\.js|js)?)\s*(?:version\s*)?(>=|≥|v)?\s*"
    + V
    + r"(\+|\s*or\s+(?:newer|later|higher|above))?(?![\d.]*[\w/-])",
    re.IGNORECASE,
)
RUNTIME_BADGE_RE = re.compile(
    r"shields\.io/badge/(python|go|golang|node(?:js)?)-(?:v|%3E%3D|>=)?" + V + r"(%2B|\+)?", re.IGNORECASE
)
DEP_RE = re.compile(r"\b([A-Z][\w.-]*[A-Za-z]|[a-z][\w-]*[a-z])\s+v?(\d+)(?:\.(\d+|x))?(?:\.\d+)?\b(?![\d.%-])")
HISTORY_RE = re.compile(
    r"\b(dropped|deprecated|removed|no longer|legacy|end[- ]of[- ]life|eol|until|prior to|before|"
    r"used to|migrat\w*|upgrad\w*|back then|previously|was|not supported|unsupported|not yet)\b",
    re.IGNORECASE,
)
REQUIRE_RE = re.compile(
    r"\b(requires?|required|requirements?|needs?|minimum|prerequisites?|at least|install(?:ed|ing)?|supports?|"
    r"supported|tested|built with|runs? on|environment|toolchain|compatible)\b",
    re.IGNORECASE,
)
REQUIRE_SECTION_RE = re.compile(
    r"requir|prereq|install|setup|getting started|quick ?start|environment|develop", re.IGNORECASE
)
# Feature notes ("only go1.8+", "in case you are using Go 1.8 or later") name versions without stating a requirement.
CONDITIONAL_RE = re.compile(
    r"\b(in case|if you|unless|only|unavailable|available (?:since|from|in)|feature)\b", re.IGNORECASE
)
NAME = {"python": "python", "go": "go", "golang": "go", "node": "node", "node.js": "node", "nodejs": "node"}


def vtuple(s: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", s)[:3])


def pad(v: tuple[int, ...]) -> tuple[int, ...]:
    return (*v, 0, 0, 0)[:3]


@dataclass
class Requirement:
    minimum: tuple[int, ...] | None
    allows: list[tuple[str, tuple[int, ...]]]  # (operator, version) pairs from a specifier
    source: str
    pinned: bool = False  # a Docker image or .nvmrc: one version in use, not a supported range


def _spec(spec: str, source: str) -> Requirement | None:
    pairs = [
        (op or "==", vtuple(v)) for op, v in re.findall(r"(>=|<=|==|~=|!=|>|<|\^|~)?\s*v?(\d+(?:\.\d+){0,2})", spec)
    ]
    if not pairs:
        return None
    mins = [v for op, v in pairs if op in (">=", "~=", "^", "~", "==", ">")]
    return Requirement(minimum=max(mins, key=pad) if mins else None, allows=pairs, source=source)


def _cmp(v: tuple[int, ...], bound: tuple[int, ...]) -> int:
    """Compare at the precision both sides share: 'Python 3' satisfies '>=3.11'."""
    k = min(len(v), len(bound))
    a, b = v[:k], bound[:k]
    return (a > b) - (a < b)


def _allowed(req: Requirement, v: tuple[int, ...]) -> bool:
    for op, bound in req.allows:
        c = _cmp(v, bound)
        ok = {
            ">=": c >= 0,
            ">": c > 0 or (c == 0 and len(v) < len(bound)),
            "<": c < 0,
            "<=": c <= 0,
            "==": c == 0,
            "!=": c != 0 or len(v) < len(bound),
            "~=": c >= 0 and _cmp(v, bound[:-1]) == 0,
            "^": c >= 0 and v[:1] == bound[:1],
            "~": c >= 0 and _cmp(v, bound[:2]) == 0,
        }[op]
        if not ok:
            return False
    return True


def requirements(repo: Repo) -> dict[str, Requirement]:
    out: dict[str, Requirement] = {}
    rp = repo.pyproject.get("project", {}).get("requires-python") or repo.pyproject.get("tool", {}).get(
        "poetry", {}
    ).get("dependencies", {}).get("python")
    setup_py = re.search(r"python_requires\s*=\s*[\"']([^\"']+)", repo.read("setup.py"))
    python = (_spec(rp, "pyproject.toml requires-python") if isinstance(rp, str) else None) or (
        _spec(setup_py.group(1), "setup.py python_requires") if setup_py else None
    )
    if python:
        out["python"] = python
    if "go.mod" in repo.file_set and (m := re.search(r"^go\s+(\d+(?:\.\d+){0,2})", repo.read("go.mod"), re.MULTILINE)):
        out["go"] = Requirement(vtuple(m.group(1)), [(">=", vtuple(m.group(1)))], "go.mod")
    engines = repo.package_json.get("engines", {})
    node = engines.get("node") if isinstance(engines, dict) else None
    if isinstance(node, str) and (r := _spec(node.split("||")[0], "package.json engines.node")):
        out["node"] = r
    for name, file in (("python", ".python-version"), ("node", ".nvmrc")):
        if name not in out and file in repo.file_set and (m := re.search(r"\d+(?:\.\d+){0,2}", repo.read(file))):
            out[name] = Requirement(vtuple(m.group(0)), [("==", vtuple(m.group(0)))], file, pinned=True)
    for f in repo.files:
        if f.split("/")[-1].startswith("Dockerfile"):
            for img, ver in re.findall(
                r"^FROM\s+(?:\S+/)?(python|golang|node):(\d+(?:\.\d+){0,2})", repo.read(f), re.MULTILINE | re.IGNORECASE
            ):
                name = NAME.get(img.lower(), img.lower())
                if name not in out:
                    out[name] = Requirement(vtuple(ver), [("==", vtuple(ver))], f"{f} FROM {img}:{ver}", pinned=True)
    return out


def dependencies(repo: Repo) -> dict[str, tuple[str, str]]:
    """Declared dependency name (normalised) -> (version spec, source)."""
    out: dict[str, tuple[str, str]] = {}
    pj = repo.package_json
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        for name, spec in (pj.get(key) or {}).items():
            if isinstance(spec, str):
                out[_norm(name)] = (spec, f"package.json {key}")
    reqs = list(repo.pyproject.get("project", {}).get("dependencies", []))
    for group in repo.pyproject.get("project", {}).get("optional-dependencies", {}).values():
        reqs += group
    for f in repo.files:
        if re.fullmatch(r"requirements[\w.-]*\.(txt|in)", f):
            reqs += repo.read(f).splitlines()
    for r in reqs:
        m = re.match(r"\s*([A-Za-z][\w.-]*)\s*(?:\[[^\]]*\])?\s*([<>=~!^][^;#]*)", r)
        if m and _norm(m.group(1)) not in out:
            out[_norm(m.group(1))] = (m.group(2).strip(), "Python requirements")
    return out


def _norm(name: str) -> str:
    name = name.lower().split("/")[-1]
    name = re.sub(r"(\.js|js)$", "", name) if name not in ("js",) else name
    return re.sub(r"[-_.]", "", name)


@dataclass
class _Claim:
    line: int
    name: str
    version: str
    minimum: bool
    text: str


def check_versions(doc: Doc, repo: Repo) -> Result:
    res = Result()
    reqs = requirements(repo)
    deps = dependencies(repo)
    runtime: list[_Claim] = []
    for no, line in enumerate(doc.lines, 1):
        if HISTORY_RE.search(line):
            continue
        for m in RUNTIME_BADGE_RE.finditer(line):
            runtime.append(_Claim(no, NAME[m.group(1).lower()], m.group(2), bool(m.group(3)), m.group(0)))
        for m in RUNTIME_RE.finditer(line):
            if line[max(0, m.start() - 1) : m.start()] in ("-", "/", "."):
                continue  # part of a badge URL or a path
            sentence = sentence_at(doc, no, m.group(0))
            stated = bool(m.group(4) or REQUIRE_RE.search(sentence) or REQUIRE_SECTION_RE.search(heading_at(doc, no)))
            if not stated or CONDITIONAL_RE.search(sentence):
                continue
            minimum = bool(
                m.group(4)
                or (m.group(2) or "").strip() in (">=", "≥")
                or re.search(r"\b(requires?|need|needs|minimum|at least)\b", line[: m.start()], re.IGNORECASE)
            )
            runtime.append(_Claim(no, NAME[m.group(1).lower()], m.group(3), minimum, m.group(0).strip()))
        for m in DEP_RE.finditer(line):
            name = _norm(m.group(1))
            if name in NAME.values() or name not in deps or len(name) < 3:
                continue
            res.add(_check_dep(doc, no, m, deps[name]))
    for cl in runtime:
        res.claims += 1
        req = reqs.get(cl.name)
        if req is None:
            continue
        res.verified += 1
        v = vtuple(cl.version)
        problem = ""
        if req.pinned:
            # The image only shows one version in use; it contradicts a stated minimum above it.
            if cl.minimum and req.minimum and _cmp(v, req.minimum) > 0:
                problem = "is above the version used in"
        elif not _allowed(req, v):
            problem = "is outside"
        elif cl.minimum and req.minimum and pad(v) != pad(req.minimum):
            problem = "differs from the minimum in"
        if problem:
            spec = ", ".join(f"{op}{'.'.join(map(str, b))}" for op, b in req.allows)
            # Same major.minor and only the patch differs: still worth fixing, but less likely to break anyone.
            close = req.minimum is not None and pad(v)[:2] == pad(req.minimum)[:2]
            res.findings.append(
                Finding(
                    check="version",
                    file=doc.path,
                    line=cl.line,
                    claim=cl.text,
                    claimed=f"{cl.name} {cl.version}{'+' if cl.minimum else ''}",
                    actual=spec,
                    evidence=req.source,
                    severity=Severity.WARNING if close or problem.startswith("differs") else Severity.ERROR,
                    suggestion=f"write {cl.name.capitalize()} {'.'.join(map(str, req.minimum or req.allows[0][1]))}"
                    f"{'+' if req.minimum else ''}",
                )
            )
    res.findings.extend(_runtime_contradictions(doc, runtime))
    return res


def _check_dep(doc: Doc, no: int, m: re.Match, dep: tuple[str, str]) -> Result:
    res = Result(claims=1)
    spec, source = dep
    declared = re.search(r"\d+(?:\.\d+)?", spec)
    if not declared or spec.strip().startswith(("<", "*", "latest")):
        return res
    res.verified = 1
    major = int(m.group(2))
    dmaj, _, dmin = declared.group(0).partition(".")
    lower_only = spec.strip().startswith(">") and "<" not in spec
    if lower_only:
        ok = major >= int(dmaj)
    elif int(dmaj) == 0 and m.group(3) and m.group(3) != "x":
        ok = major == 0 and m.group(3) == (dmin or "0")
    else:
        ok = major == int(dmaj)
    if not ok:
        res.findings.append(
            Finding(
                check="version",
                file=doc.path,
                line=no,
                claim=m.group(0),
                claimed=m.group(0),
                actual=f"{spec}",
                evidence=source,
                severity=Severity.WARNING,
                suggestion=f"write {m.group(1)} {dmaj}",
            )
        )
    return res


def _runtime_contradictions(doc: Doc, claims: list[_Claim]) -> list[Finding]:
    mins: dict[str, dict[tuple[int, ...], _Claim]] = defaultdict(dict)
    for c in claims:
        if c.minimum:
            mins[c.name].setdefault(pad(vtuple(c.version)), c)
    out = []
    for name, seen in mins.items():
        if len(seen) > 1:
            ordered = sorted(seen.values(), key=lambda c: c.line)
            where = ", ".join(f"{c.version} on line {c.line}" for c in ordered)
            out.append(
                Finding(
                    check="contradiction",
                    file=doc.path,
                    line=ordered[1].line,
                    claim=ordered[1].text,
                    claimed=f"different minimum {name} versions ({where})",
                    actual="one minimum",
                    evidence="the README states the requirement twice",
                    severity=Severity.WARNING,
                    suggestion="state one minimum version",
                )
            )
    return out
