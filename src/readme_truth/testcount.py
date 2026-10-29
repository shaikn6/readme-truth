"""Count a repository's tests.

The default is a static count: parse the test files and count what pytest or
`go test` would collect, without importing or running anything. `collect`
runs the real collector, and a CI-produced file can be read instead.
"""

from __future__ import annotations

import ast
import configparser
import fnmatch
import math
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from .repo import Repo


@dataclass
class Count:
    tests: int
    exact: bool  # False: the static count is an estimate
    source: str
    lower_bound: bool = False  # True: the only uncertainty can only add tests
    functions: int | None = None
    files: int | None = None
    per_path: dict[str, int] = field(default_factory=dict)  # test file -> tests


# ---------------------------------------------------------------- pytest (static)


@dataclass
class _Fixture:
    params: int  # 1 when the fixture is not parametrized
    args: list[str]


class _Uncertain(Exception):
    pass


def _pytest_config(repo: Repo) -> dict[str, str | list[str]]:
    ini = repo.pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {})
    if ini:
        return ini
    for name, section in (("pytest.ini", "pytest"), ("tox.ini", "pytest"), ("setup.cfg", "tool:pytest")):
        if name in repo.file_set:
            cp = configparser.ConfigParser(interpolation=None)
            try:
                cp.read_string(repo.read(name))
            except configparser.Error:
                continue
            if cp.has_section(section):
                return dict(cp[section])
    return {}


def _as_list(v: str | list[str] | None) -> list[str]:
    if v is None:
        return []
    return v if isinstance(v, list) else v.split()


def _deselected_marks(addopts: list[str]) -> tuple[set[str], bool]:
    """Marks excluded by `-m "not a and not b"` in addopts; flag if the expression is too complex."""
    try:
        args = shlex.split(" ".join(addopts))
    except ValueError:
        return set(), False
    marks: set[str] = set()
    for i, a in enumerate(args):
        expr = None
        if a == "-m" and i + 1 < len(args):
            expr = args[i + 1]
        elif a.startswith("-m") and len(a) > 2:
            expr = a[2:]
        if expr is not None:
            parts = [p.strip() for p in expr.split(" and ")]
            if not all(re.fullmatch(r"not \w+", p) for p in parts):
                return marks, False
            marks |= {p[4:] for p in parts}
        if a == "-k" or a.startswith("-k"):
            return marks, False
    return marks, True


def _mark_names(decorators: list[ast.expr]) -> set[str]:
    out = set()
    for d in decorators:
        node = d.func if isinstance(d, ast.Call) else d
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute) and node.value.attr == "mark":
            out.add(node.attr)
    return out


class _Module:
    """One test module: literal lookups and the parametrize arithmetic."""

    def __init__(self, tree: ast.Module, fixtures: dict[str, _Fixture]) -> None:
        self.tree = tree
        self.fixtures = dict(fixtures)
        self.consts: dict[str, ast.expr] = {}
        self.exact = True
        self.lower_bound = True
        for node in tree.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                self.consts[node.targets[0].id] = node.value
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value:
                self.consts[node.target.id] = node.value
        self.fixtures.update(_fixtures_in(tree, self))

    def length(self, node: ast.expr, depth: int = 0) -> int:
        if depth > 5:
            raise _Uncertain
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            if any(isinstance(e, ast.Starred) for e in node.elts):
                return sum(self.length(e.value, depth + 1) if isinstance(e, ast.Starred) else 1 for e in node.elts)
            return len(node.elts)
        if isinstance(node, ast.Dict):
            return len(node.keys)
        if isinstance(node, ast.Name) and node.id in self.consts:
            return self.length(self.consts[node.id], depth + 1)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id == "range" and all(isinstance(a, ast.Constant) for a in node.args):
                return len(range(*[a.value for a in node.args]))
            if node.func.id in ("list", "tuple", "sorted", "enumerate") and len(node.args) == 1:
                return self.length(node.args[0], depth + 1)
        if isinstance(node, (ast.ListComp, ast.GeneratorExp)) and len(node.generators) == 1:
            gen = node.generators[0]
            if not gen.ifs:
                return self.length(gen.iter, depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            return self.length(node.left, depth + 1) + self.length(node.right, depth + 1)
        raise _Uncertain

    def param_count(self, decorators: list[ast.expr]) -> int:
        n = 1
        for d in decorators:
            if not (isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr == "parametrize"):
                continue
            values = d.args[1] if len(d.args) > 1 else next((k.value for k in d.keywords if k.arg == "argvalues"), None)
            try:
                n *= max(1, self.length(values)) if values is not None else 1
            except _Uncertain:
                self.exact = False  # unknown length; counted as 1, so the total is a lower bound
        return n

    def fixture_factor(self, args: list[str]) -> int:
        seen: set[str] = set()

        def walk(name: str, depth: int) -> None:
            fx = self.fixtures.get(name)
            if fx is None or name in seen or depth > 10:
                return
            seen.add(name)
            for a in fx.args:
                walk(a, depth + 1)

        for a in args:
            walk(a, 0)
        return math.prod(self.fixtures[s].params for s in seen)


def _fixtures_in(tree: ast.AST, mod: _Module | None) -> dict[str, _Fixture]:
    out: dict[str, _Fixture] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for d in node.decorator_list:
            call = d if isinstance(d, ast.Call) else None
            func = call.func if call else d
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "fixture":
                continue
            params = 1
            fname = node.name
            if call:
                for k in call.keywords:
                    if k.arg == "params":
                        try:
                            params = (mod or _Module(ast.Module(body=[], type_ignores=[]), {})).length(k.value)
                        except _Uncertain:
                            if mod:
                                mod.exact = False
                    elif k.arg == "name" and isinstance(k.value, ast.Constant):
                        fname = k.value.value
            out[fname] = _Fixture(params=params, args=[a.arg for a in node.args.args])
    return out


def _arg_names(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    return [a.arg for a in fn.args.args if a.arg not in ("self", "cls")]


def _count_module(
    source: str, conftest_fixtures: dict[str, _Fixture], deselect: set[str], funcs: str = "test", classes: str = "Test"
) -> tuple[int, int, bool, bool]:
    """Return (tests, test functions, exact, lower_bound) for one module."""
    tree = ast.parse(source)
    mod = _Module(tree, conftest_fixtures)
    module_marks: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in node.targets):
            vals = node.value.elts if isinstance(node.value, (ast.List, ast.Tuple)) else [node.value]
            module_marks |= _mark_names(vals)
        if isinstance(node, ast.FunctionDef) and node.name == "pytest_generate_tests":
            mod.exact = False
    if module_marks & deselect:
        return 0, 0, mod.exact, mod.lower_bound
    classes_by_name = {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}
    tests = functions = 0

    def count_fn(fn: ast.FunctionDef | ast.AsyncFunctionDef, factor: int, marks: set[str]) -> None:
        nonlocal tests, functions
        if (_mark_names(fn.decorator_list) | marks) & deselect:
            return
        functions += 1
        tests += factor * mod.param_count(fn.decorator_list) * mod.fixture_factor(_arg_names(fn))

    def methods(cls: ast.ClassDef, depth: int = 0) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
        found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}
        for base in cls.bases:
            name = base.id if isinstance(base, ast.Name) else getattr(base, "attr", "")
            if name in classes_by_name and depth < 5:
                found.update(methods(classes_by_name[name], depth + 1))
            elif name not in ("object", "TestCase", "IsolatedAsyncioTestCase") and depth == 0 and name:
                mod.exact = False  # inherits tests we cannot see
        for n in cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                found[n.name] = n
        return found

    def is_unittest(cls: ast.ClassDef) -> bool:
        return any(getattr(b, "attr", getattr(b, "id", "")).endswith("TestCase") for b in cls.bases)

    def visit_class(cls: ast.ClassDef, factor: int, marks: set[str]) -> None:
        if not (cls.name.startswith(classes) or is_unittest(cls)):
            return
        body_names = {t.id for n in cls.body if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)}
        if "__test__" in body_names:
            return
        meths = methods(cls)
        if "__init__" in meths and not is_unittest(cls):
            return
        marks = marks | _mark_names(cls.decorator_list)
        if marks & deselect:
            return
        factor *= mod.param_count(cls.decorator_list)
        for name, fn in meths.items():
            if name.startswith(funcs):
                count_fn(fn, factor, marks)
        for n in cls.body:
            if isinstance(n, ast.ClassDef):
                visit_class(n, factor, marks)

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith(funcs):
            count_fn(node, 1, set())
        elif isinstance(node, ast.ClassDef):
            visit_class(node, 1, set())
    return tests, functions, mod.exact, mod.lower_bound


def pytest_files(repo: Repo) -> list[str]:
    cfg = _pytest_config(repo)
    patterns = _as_list(cfg.get("python_files")) or ["test_*.py", "*_test.py"]
    roots = [r.strip("./") for r in _as_list(cfg.get("testpaths"))]
    ignore = [a.split("=", 1)[1].strip("./") for a in _as_list(cfg.get("addopts")) if a.startswith("--ignore=")]
    out = []
    for f in repo.files:
        p = PurePosixPath(f)
        if p.suffix != ".py" or not any(fnmatch.fnmatch(p.name, pat) for pat in patterns):
            continue
        if any(part in {"node_modules", ".venv", "venv", "site-packages", ".tox"} for part in p.parts):
            continue
        if roots and not any(f == r or f.startswith(r + "/") for r in roots):
            continue
        if any(f == i or f.startswith(i + "/") for i in ignore):
            continue
        out.append(f)
    return out


def static_pytest(repo: Repo) -> Count | None:
    files = pytest_files(repo)
    if not files:
        return None
    cfg = _pytest_config(repo)
    deselect, ok = _deselected_marks(_as_list(cfg.get("addopts")))
    exact, lower = ok, ok
    conftests: dict[str, dict[str, _Fixture]] = {}
    for f in repo.files:
        if PurePosixPath(f).name == "conftest.py":
            try:
                conftests[str(PurePosixPath(f).parent)] = _fixtures_in(ast.parse(repo.read(f)), None)
                if "pytest_generate_tests" in repo.read(f):
                    exact = False
            except SyntaxError:
                exact = lower = False
    total = functions = 0
    per_path: dict[str, int] = {}
    for f in files:
        fixtures: dict[str, _Fixture] = {}
        parents = list(PurePosixPath(f).parents)[::-1]  # outermost first, so inner conftests override
        for parent in parents:
            fixtures.update(conftests.get(str(parent), {}))
        try:
            n, fn, ex, lb = _count_module(repo.read(f), fixtures, deselect)
        except SyntaxError:
            exact = lower = False
            continue
        exact &= ex
        lower &= lb
        total += n
        functions += fn
        per_path[f] = n
    return Count(
        tests=total,
        exact=exact,
        lower_bound=lower and not exact,
        functions=functions,
        files=len(files),
        per_path=per_path,
        source=f"static count of {len(files)} pytest files",
    )


# ---------------------------------------------------------------- go / js (static)

GO_TEST_RE = re.compile(r"^func (Test[A-Z0-9_]\w*|Test)\(\w+ \*testing\.T\)", re.MULTILINE)
JS_TEST_RE = re.compile(r"(?<![\w.])(?:it|test)(?:\.(?:only|skip|concurrent))?\s*\(")


def static_go(repo: Repo) -> Count | None:
    files = [f for f in repo.files if f.endswith("_test.go") and "/vendor/" not in f"/{f}"]
    if not files:
        return None
    per_path, subtests = {}, False
    for f in files:
        text = repo.read(f)
        per_path[f] = len(GO_TEST_RE.findall(text))
        subtests |= "t.Run(" in text
    total = sum(per_path.values())
    # go test -list counts top-level tests; READMEs that quote -v output also count subtests.
    return Count(
        tests=total,
        exact=not subtests,
        lower_bound=subtests,
        functions=total,
        files=len(files),
        per_path=per_path,
        source=f"static count of {len(files)} Go test files",
    )


def static_js(repo: Repo) -> Count | None:
    rx = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")
    files = [f for f in repo.files if rx.search(f) and "node_modules/" not in f]
    if not files:
        return None
    per_path = {f: len(JS_TEST_RE.findall(repo.read(f))) for f in files}
    return Count(
        tests=sum(per_path.values()),
        exact=False,
        files=len(files),
        per_path=per_path,
        source=f"static estimate from {len(files)} JS/TS test files",
    )


def static_counts(repo: Repo) -> list[Count]:
    """One count per test framework found (a repo can have Go and Python suites)."""
    return [c for c in (static_pytest(repo), static_go(repo), static_js(repo)) if c]


# ---------------------------------------------------------------- real collectors and CI files

SUMMARY_RE = re.compile(r"(\d+)(?:/(\d+))? tests? collected")
RESULT_RE = re.compile(r"(\d+) (passed|failed|skipped|xfailed|xpassed|errors?)")


def collect(repo: Repo, timeout: int = 600) -> Count | None:
    """Run the real collector: pytest --collect-only, or go test -list."""
    if static_pytest(repo):
        r = _run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"], repo, timeout)
        m = SUMMARY_RE.search(r)
        if m:
            return Count(tests=int(m.group(1)), exact=True, source="pytest --collect-only")
    if static_go(repo):
        r = _run(["go", "test", "-list", ".", "./..."], repo, timeout)
        tests = [ln for ln in r.splitlines() if ln.startswith("Test")]
        if tests:
            return Count(tests=len(tests), exact=True, source="go test -list")
    return None


def from_command(repo: Repo, cmd: str, timeout: int = 600) -> Count | None:
    """Run a user-supplied command; the last integer it prints is the count."""
    out = _run(cmd, repo, timeout, shell=True)
    nums = re.findall(r"\d+", out)
    return Count(tests=int(nums[-1]), exact=True, source=f"`{cmd}`") if nums else None


def from_file(path: str) -> Count | None:
    """Read a CI-produced count: JUnit XML, pytest summary output, or a bare integer."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    if text.lstrip().startswith("<"):
        try:
            root = ET.fromstring(text)
        except ET.ParseError:
            return None
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        total = sum(int(s.get("tests", 0)) for s in suites) if suites else int(root.get("tests", 0))
        return Count(tests=total, exact=True, source=f"JUnit file {path}")
    results = RESULT_RE.findall(text)
    if results:
        n = sum(int(k) for k, what in results if what in ("passed", "failed", "skipped", "xfailed", "xpassed"))
        return Count(tests=n, exact=True, source=f"test summary in {path}")
    nums = re.findall(r"\d+", text)
    return Count(tests=int(nums[0]), exact=True, source=f"count file {path}") if nums else None


def _run(cmd: list[str] | str, repo: Repo, timeout: int, shell: bool = False) -> str:
    try:
        r = subprocess.run(cmd, cwd=repo.root, capture_output=True, text=True, timeout=timeout, shell=shell)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout + "\n" + r.stderr
