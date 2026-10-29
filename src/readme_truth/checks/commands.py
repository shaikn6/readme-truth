"""Commands in fenced shell blocks: make targets, scripts, CLI flags, pip extras, npm scripts."""

from __future__ import annotations

import ast
import re
import shlex
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import PurePosixPath

from ..markdown import Doc
from ..model import Finding, Result, Severity
from ..repo import Repo, join, normpath

SHELL_LANGS = {"", "bash", "sh", "shell", "console", "zsh", "shell-session", "sh-session", "terminal", "shellsession"}
WRAPPERS = {"sudo", "time", "exec", "nohup", "env", "command"}
RUNNERS = {("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("hatch", "run"), ("pdm", "run"), ("rye", "run")}
PYTEST_PLUGINS = {
    "--cov": "pytest-cov",
    "-n": "pytest-xdist",
    "--benchmark": "pytest-benchmark",
    "--timeout": "pytest-timeout",
    "--asyncio-mode": "pytest-asyncio",
    "--hypothesis": "hypothesis",
}
MAKE_RULE_RE = re.compile(r"^([^\s:#=.$][^:=#]*?|\.[\w-]+[^:=#]*?)\s*::?(?!=)", re.MULTILINE)
INLINE_CMD_RE = re.compile(r"^(?:python3?|pytest|make|pip3?|npm|yarn|pnpm|go|uv|poetry)\s+\S")
OPERATORS = {"&&", "||", ";", "|", "&", "(", ")", ";;", "|&"}


@dataclass
class CLI:
    flags: set[str] = field(default_factory=set)
    subcommands: set[str] = field(default_factory=set)
    dynamic: bool = False  # some flags are built at run time; do not judge


@dataclass
class _State:
    cwd: str
    created: set[str] = field(default_factory=set)
    clones: set[str] = field(default_factory=set)  # `git clone` targets: cd-ing into one means the repo root
    lost: bool = False  # cd'd somewhere we cannot follow


def _commands(fence_lines: list[str], start: int, console: bool) -> list[tuple[int, list[str], list[str]]]:
    """(line, tokens, redirect targets) per simple command, joining backslash continuations."""
    out = []
    buf, first = "", None
    for i, raw in enumerate(fence_lines):
        line = raw.rstrip()
        if console and first is None and not re.match(r"^\s*(\(\S+\)\s*)?[$%>]\s", line):
            continue  # output, not a command
        line = re.sub(r"^\s*(\(\S+\)\s*)?[$%]\s+", "", line)
        if first is None:
            first = start + i
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        try:
            lex = shlex.shlex(buf, posix=True, punctuation_chars=True)
            lex.whitespace_split = True
            lex.commenters = "#"
            tokens = list(lex)
        except ValueError:
            tokens = []
        cmd: list[str] = []
        redirects: list[str] = []
        pending_redirect = False
        for t in [*tokens, ";"]:
            if pending_redirect:
                redirects.append(t)
                pending_redirect = False
            elif t in (">", ">>", "2>", "&>", "<"):
                pending_redirect = t != "<"
            elif t in OPERATORS or set(t) <= set("&|;()"):
                if cmd:
                    out.append((first, cmd, redirects))
                cmd, redirects = [], []
            else:
                cmd.append(t)
        buf, first = "", None
    return out


def _strip(tokens: list[str]) -> list[str]:
    while tokens:
        if re.fullmatch(r"[A-Za-z_]\w*=.*", tokens[0]) or tokens[0] in WRAPPERS:
            tokens = tokens[1:]
        elif len(tokens) > 1 and (tokens[0], tokens[1]) in RUNNERS:
            tokens = tokens[2:]
        elif re.match(r"^\.?\w*venv/bin/", tokens[0]):
            tokens = [tokens[0].rsplit("/", 1)[1], *tokens[1:]]
        else:
            break
    return tokens


class CommandChecker:
    def __init__(self, doc: Doc, repo: Repo) -> None:
        self.doc, self.repo = doc, repo
        self.res = Result()
        self.doc_dir = join(str(PurePosixPath(doc.path).parent))
        self.scripts = self._console_scripts()
        self.declared = self._declared_text()

    # -- helpers
    def _path(self, st: _State, p: str) -> str:
        return join(st.cwd, p)

    def _is_created(self, st: _State, rel: str) -> bool:
        return any(rel == c or rel.startswith(c + "/") for c in st.created)

    def _exists(self, st: _State, p: str) -> bool | None:
        """True/False, or None when the path cannot be judged."""
        if st.lost or not p or p.startswith(("$", "~", "/", "<", "{")) or any(c in p for c in "*?$<>{}"):
            return None
        rel = self._path(st, p)
        if self._is_created(st, rel) or rel.startswith(".."):
            return None
        # docs/contributing.md shows commands run from the repo root, not from docs/.
        return self.repo.exists(rel) or (st.cwd == self.doc_dir != "" and self.repo.exists(normpath(p)))

    def _flag(
        self,
        no: int,
        claim: str,
        claimed: str,
        actual: str,
        evidence: str,
        sev: Severity = Severity.ERROR,
        suggestion: str = "",
    ) -> None:
        self.res.findings.append(
            Finding(
                check="command",
                file=self.doc.path,
                line=no,
                claim=claim,
                claimed=claimed,
                actual=actual,
                evidence=evidence,
                severity=sev,
                suggestion=suggestion,
            )
        )

    def _require(self, st: _State, no: int, cmd: str, p: str, what: str) -> None:
        ok = self._exists(st, p)
        if ok is None:
            return
        self.res.claims += 1
        self.res.verified += 1
        if not ok:
            ignored = self.repo.ignored(self._path(st, p))
            # A bare name (`python app.py`, `cd myproject`) is often the reader's own file in a tutorial.
            bare = what == "directory" or (what in ("script", "source file") and "/" not in p.strip("./"))
            sev = Severity.NOTE if bare else Severity.WARNING if ignored else Severity.ERROR
            self._flag(
                no,
                cmd,
                f"{what} `{p}` exists",
                "missing" + (" (git ignores it)" if ignored else ""),
                "git ls-files",
                sev,
                "fix the path or add the step that creates it",
            )

    def _declared_text(self) -> str:
        names = [
            f
            for f in self.repo.files
            if re.search(
                r"(^|/)(pyproject\.toml|setup\.py|setup\.cfg|tox\.ini|Pipfile|environment\.ya?ml|requirements[\w.-]*\.(txt|in))$",
                f,
            )
        ]
        return "\n".join(self.repo.read(f) for f in names) + "\n" + "\n".join(self.doc.lines)

    def _console_scripts(self) -> dict[str, str]:
        pp = self.repo.pyproject
        entries = dict(pp.get("project", {}).get("scripts", {}))
        entries.update(pp.get("tool", {}).get("poetry", {}).get("scripts", {}))
        out = {}
        for name, target in entries.items():
            if isinstance(target, str):
                f = self._module_file(target.split(":")[0])
                if f:
                    out[name] = f
        return out

    def _module_file(self, module: str) -> str | None:
        base = module.replace(".", "/")
        for root in ("", "src/", "python/", "python/src/"):
            for cand in (f"{root}{base}.py", f"{root}{base}/__main__.py", f"{root}{base}/__init__.py"):
                if cand in self.repo.file_set:
                    return cand
        return None

    def _package_exists(self, module: str) -> bool:
        top = module.split(".")[0]
        return any(
            self.repo.exists(f"{r}{top}") or f"{r}{top}.py" in self.repo.file_set
            for r in ("", "src/", "python/", "python/src/")
        )

    # -- the commands
    def run(self) -> Result:
        created: set[str] = set()  # outputs persist across blocks: a README is read top to bottom
        clones: set[str] = set()
        blocks = [
            (f.lines, f.start, f.lang in ("console", "shell-session", "sh-session", "shellsession"))
            for f in self.doc.fences
            if f.lang in SHELL_LANGS
        ]
        # Inline code that starts with a command we understand (`pytest --cov=src`) is a command too.
        blocks += [
            ([text], no, False)
            for text, no in self.doc.code_spans
            if INLINE_CMD_RE.match(text) or (text.split()[0] in self.scripts and " " in text)
        ]
        for lines, start, console in sorted(blocks, key=lambda b: b[1]):
            st = _State(cwd=self.doc_dir, created=created, clones=clones)
            for no, tokens, redirects in _commands(lines, start, console):
                for r in redirects:
                    st.created.add(self._path(st, r))
                tokens = _strip(tokens)
                if tokens:
                    self._one(st, no, tokens)
        return self.res

    def _one(self, st: _State, no: int, t: list[str]) -> None:
        prog, args = t[0], t[1:]
        cmd = " ".join(t)
        if prog == "cd":
            self._cd(st, no, cmd, args)
        elif prog in ("mkdir", "touch"):
            st.created.update(self._path(st, a) for a in args if not a.startswith("-"))
        elif prog == "git" and args[:1] == ["clone"]:
            pos = [a for a in args[1:] if not a.startswith("-")]
            if pos:
                name = pos[1] if len(pos) > 1 else re.sub(r"\.git$", "", pos[0].rstrip("/").split("/")[-1])
                st.clones.add(self._path(st, name))
        elif prog in ("cp", "mv", "ln"):
            pos = [a for a in args if not a.startswith("-")]
            if len(pos) >= 2:
                for src in pos[:-1]:
                    self._require(st, no, cmd, src, "source file")
                st.created.add(self._path(st, pos[-1]))
        elif prog in ("curl", "wget"):
            for flag in ("-o", "-O", "--output"):
                if flag in args and args.index(flag) + 1 < len(args):
                    st.created.add(self._path(st, args[args.index(flag) + 1]))
        elif prog in ("virtualenv",) or (prog == "uv" and args[:1] == ["venv"]):
            st.created.add(self._path(st, next((a for a in args if not a.startswith("-") and a != "venv"), ".venv")))
        elif re.fullmatch(r"python(3(\.\d+)?)?|py", prog):
            self._python(st, no, cmd, args)
        elif prog in ("pip", "pip3") or (prog == "uv" and args[:1] == ["pip"]):
            self._pip(st, no, cmd, args[1:] if prog == "uv" else args)
        elif prog == "pytest":
            self._pytest(st, no, cmd, args)
        elif prog in ("make", "gmake"):
            self._make(st, no, cmd, args)
        elif prog in ("npm", "yarn", "pnpm", "bun"):
            self._npm(st, no, cmd, prog, args)
        elif prog == "go" and args[:1] and args[0] in ("run", "build", "test", "install", "vet"):
            if "-o" in args and args.index("-o") + 1 < len(args):
                st.created.add(self._path(st, args[args.index("-o") + 1]))
            for a in args[1:]:
                if a.startswith("./") and not a.startswith("./..."):
                    self._require(st, no, cmd, a.removesuffix("/...") or ".", "Go package")
        elif prog in ("bash", "sh", "zsh", "source", ".", "node", "ts-node", "tsx", "deno", "Rscript"):
            pos = [a for a in args if not a.startswith("-") and a != "run"]
            if pos and re.search(r"\.(sh|bash|zsh|js|mjs|cjs|ts|R)$", pos[0]) and "venv/bin/activate" not in pos[0]:
                self._require(st, no, cmd, pos[0], "script")
        elif prog.startswith("./") or prog.startswith("bin/") or prog.startswith("scripts/"):
            self._require(st, no, cmd, prog, "executable")
        elif prog in ("docker", "docker-compose", "kubectl", "helm"):
            for flag in ("-f", "--file", "--values"):
                for i, a in enumerate(args[:-1]):
                    if a == flag and re.search(r"\.(ya?ml|json)$|Dockerfile", args[i + 1]):
                        self._require(st, no, cmd, args[i + 1], "file")
        elif prog in self.scripts:
            self._cli(no, cmd, self.scripts[prog], args, prog)

    def _cd(self, st: _State, no: int, cmd: str, args: list[str]) -> None:
        target = next((a for a in args if not a.startswith("-")), "")
        rel = self._path(st, target)
        if rel in st.clones or target.rstrip("/") == self.repo.root.name:
            st.cwd, st.lost = "", False  # into the freshly cloned repo
        elif not target or target.startswith(("$", "~", "/", "-")) or st.lost or self._is_created(st, rel):
            st.lost = True  # somewhere we cannot follow
        elif self._exists(st, target):
            st.cwd = rel
        else:
            self._require(st, no, cmd, target, "directory")
            st.lost = True

    def _python(self, st: _State, no: int, cmd: str, args: list[str]) -> None:
        if not args or args[0] == "-c":
            return
        if args[0] == "-m" and len(args) > 1:
            mod, rest = args[1], args[2:]
            if mod == "venv":
                pos = [a for a in rest if not a.startswith("-")]
                if pos:
                    st.created.add(self._path(st, pos[0]))
            elif mod == "pip":
                self._pip(st, no, cmd, rest)
            elif mod == "pytest":
                self._pytest(st, no, cmd, rest)
            elif self._package_exists(mod):
                self.res.claims += 1
                self.res.verified += 1
                f = self._module_file(mod)
                if f is None:
                    self._flag(
                        no,
                        cmd,
                        f"module `{mod}` exists",
                        "no such module in the repo",
                        "source tree",
                        suggestion="fix the module path",
                    )
                else:
                    self._cli(no, cmd, f, rest, f"python -m {mod}")
            return
        script = next((a for a in args if not a.startswith("-")), None)
        if script and script.endswith(".py"):
            ok = self._exists(st, script)
            self._require(st, no, cmd, script, "script")
            if ok:
                rest = args[args.index(script) + 1 :]
                self._cli(no, cmd, self._path(st, script), rest, script)

    def _pip(self, st: _State, no: int, cmd: str, args: list[str]) -> None:
        if not args or args[0] != "install":
            return
        it = iter(args[1:])
        for a in it:
            if a in ("-r", "--requirement", "-c", "--constraint"):
                self._require(st, no, cmd, next(it, ""), "requirements file")
            elif a.startswith("-") and a not in ("-e", "--editable"):
                continue
            elif m := re.fullmatch(r"([^\[\s]*)\[([^\]]+)\]", a):
                self._extras(st, no, cmd, m.group(1), [e.strip() for e in m.group(2).split(",")])

    def _extras(self, st: _State, no: int, cmd: str, target: str, extras: list[str]) -> None:
        project = self.repo.pyproject.get("project", {})
        if target in ("", ".", "./") or (re.fullmatch(r"\.?/?[\w./-]+", target) and self._exists(st, target)):
            base = self._path(st, target)
        elif _norm(target) == _norm(project.get("name", "\0")):
            base = ""
        else:
            return
        pyproject = join(base, "pyproject.toml")
        data = self.repo.toml(pyproject) if pyproject in self.repo.file_set else {}
        proj = data.get("project", {})
        if not data or "optional-dependencies" in proj.get("dynamic", []):
            return
        defined = {_norm(k) for k in proj.get("optional-dependencies", {})}
        defined |= {_norm(k) for k in data.get("tool", {}).get("poetry", {}).get("extras", {})}
        for e in extras:
            self.res.claims += 1
            self.res.verified += 1
            if _norm(e) not in defined:
                self._flag(
                    no,
                    cmd,
                    f"extra `[{e}]` exists",
                    "defined extras: " + (", ".join(sorted(defined)) or "none"),
                    pyproject,
                    suggestion="use one of the defined extras or add it to pyproject.toml",
                )

    def _pytest(self, st: _State, no: int, cmd: str, args: list[str]) -> None:
        for a in args:
            for flag, plugin in PYTEST_PLUGINS.items():
                if (
                    a == flag
                    or a.startswith(flag + "=")
                    or (flag != "-n" and a.startswith(flag + "-"))
                    or (flag == "--cov" and a.startswith("--cov"))
                ):
                    self.res.claims += 1
                    self.res.verified += 1
                    if not re.search(re.escape(plugin).replace(r"\-", "[-_]"), self.declared, re.IGNORECASE):
                        self._flag(
                            no,
                            cmd,
                            f"`{flag}` works",
                            f"{plugin} is not declared in any dependency file",
                            "pyproject/requirements/setup files",
                            Severity.WARNING,
                            f"add {plugin} to the dev dependencies",
                        )
                    break
            if not a.startswith("-") and ("/" in a or a.endswith(".py")) and "=" not in a:
                self._require(st, no, cmd, a.split("::")[0], "test path")

    def _make(self, st: _State, no: int, cmd: str, args: list[str]) -> None:
        cwd, makefile, targets = st.cwd, None, []
        it = iter(args)
        for a in it:
            if a == "-C":
                cwd = join(cwd, next(it, ""))
            elif a == "-f":
                makefile = next(it, "")
            elif a.startswith("-") or "=" in a:
                continue
            else:
                targets.append(a)
        if st.lost:
            return
        names = [makefile] if makefile else ["GNUmakefile", "makefile", "Makefile"]
        found = next((join(cwd, n) for n in names if join(cwd, n) in self.repo.file_set), None)
        self.res.claims += 1
        self.res.verified += 1
        if found is None:
            self._flag(
                no,
                cmd,
                "a Makefile",
                f"no Makefile in `{cwd or '.'}`",
                "git ls-files",
                suggestion="add the Makefile or fix the command",
            )
            return
        defined, uncertain = make_targets(self.repo.read(found))
        for tgt in targets:
            self.res.claims += 1
            self.res.verified += 1
            if tgt not in defined and not uncertain:
                self._flag(
                    no,
                    cmd,
                    f"make target `{tgt}`",
                    f"not defined in {found}",
                    found,
                    suggestion="add the target or fix the command",
                )

    def _npm(self, st: _State, no: int, cmd: str, prog: str, args: list[str]) -> None:
        if not args:
            return
        if args[0] in ("run", "run-script") and len(args) > 1:
            name = args[1]
        elif args[0] in ("test", "start", "t") and prog != "bun":
            name = "test" if args[0] == "t" else args[0]
        else:
            return
        pj_path = join(st.cwd, "package.json")
        if st.lost or pj_path not in self.repo.file_set:
            return
        scripts = self.repo.json(pj_path).get("scripts", {})
        self.res.claims += 1
        self.res.verified += 1
        if name not in scripts and not (name == "start" and self.repo.exists(join(st.cwd, "server.js"))):
            self._flag(
                no,
                cmd,
                f"npm script `{name}`",
                f"not in {pj_path} scripts",
                pj_path,
                suggestion="add the script or fix the command",
            )

    def _cli(self, no: int, cmd: str, path: str, args: list[str], prog: str) -> None:
        cli = cli_for(self.repo, path)
        if cli is None or cli.dynamic:
            return
        if cli.subcommands and args and not args[0].startswith("-") and not re.search(r"[/.<{$]", args[0]):
            self.res.claims += 1
            self.res.verified += 1
            if args[0] not in cli.subcommands:
                self._flag(
                    no,
                    cmd,
                    f"`{prog} {args[0]}`",
                    "subcommands: " + ", ".join(sorted(cli.subcommands)),
                    path,
                    suggestion="use an existing subcommand",
                )
        for a in args:
            if a == "--" or not a.startswith("-") or re.fullmatch(r"-\d.*", a):
                if a == "--":
                    break
                continue
            flag = a.split("=", 1)[0]
            if flag in ("-h", "--help"):
                continue
            self.res.claims += 1
            self.res.verified += 1
            known = flag in cli.flags or (
                flag.startswith("--") and len([f for f in cli.flags if f.startswith(flag)]) == 1
            )
            if not known:
                self._flag(
                    no,
                    cmd,
                    f"`{prog}` accepts `{flag}`",
                    "not defined by the argument parser",
                    path,
                    suggestion="use a defined flag: "
                    + ", ".join(sorted(f for f in cli.flags if f.startswith("--"))[:8]),
                )


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.lower())


def make_targets(text: str) -> tuple[set[str], bool]:
    """Targets defined in a Makefile; True when includes or variable targets make the list incomplete."""
    text = re.sub(r"\\\n", " ", text)
    defined: set[str] = set()
    uncertain = bool(re.search(r"^-?include\s", text, re.MULTILINE))
    for m in MAKE_RULE_RE.finditer(text):
        for tgt in m.group(1).split():
            if "%" in tgt or "$" in tgt:
                uncertain = True
            defined.add(tgt)
    return defined, uncertain


@lru_cache(maxsize=256)
def _parse_cli(text: str) -> tuple[CLI, list[str]]:
    cli = CLI()
    imports: list[str] = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        cli.dynamic = True
        return cli, imports
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            base = "." * node.level + (node.module or "")
            imports.append(base)
            imports.extend(f"{base}{'' if base.endswith('.') else '.'}{a.name}" for a in node.names)
        elif isinstance(node, ast.Import):
            imports.extend(a.name for a in node.names)
            if any(a.name in ("typer", "fire", "docopt") for a in node.names):
                cli.dynamic = True
        if isinstance(node, ast.ImportFrom) and node.module in ("typer", "fire", "docopt"):
            cli.dynamic = True
        if not isinstance(node, ast.Call):
            continue
        name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
        if name in ("add_argument", "option", "argument"):
            for a in node.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    for part in a.value.split("/"):
                        if part.startswith("-"):
                            cli.flags.add(part.strip())
                elif name == "add_argument":
                    cli.dynamic = True
        elif name == "add_parser" and node.args and isinstance(node.args[0], ast.Constant):
            cli.subcommands.add(node.args[0].value)
            for k in node.keywords:
                if k.arg == "aliases" and isinstance(k.value, (ast.List, ast.Tuple)):
                    cli.subcommands |= {e.value for e in k.value.elts if isinstance(e, ast.Constant)}
        elif name == "ArgumentParser" and any(k.arg == "parents" for k in node.keywords):
            cli.dynamic = True
        elif name in ("Option", "Argument", "parse_known_args", "add_command", "make_pass_decorator") or any(
            k.arg in ("cls", "context_settings", "params") for k in node.keywords
        ):
            cli.dynamic = True  # options or commands assembled elsewhere; do not judge
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in node.decorator_list:
                if (
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in ("command", "group")
                ):
                    arg = d.args[0].value if d.args and isinstance(d.args[0], ast.Constant) else None
                    if isinstance(d.func.value, ast.Name) and d.func.value.id == "click":
                        continue  # the top-level command itself
                    cli.subcommands.add(arg or node.name.replace("_", "-"))
    return cli, imports


def cli_for(repo: Repo, path: str) -> CLI | None:
    """Flags and subcommands defined in a script and the in-repo modules it imports (one hop)."""
    cli, imports = _parse_cli(repo.read(path))
    merged = CLI(set(cli.flags), set(cli.subcommands), cli.dynamic)
    pkg_dir = str(PurePosixPath(path).parent)
    for mod in imports:
        if mod.startswith("."):
            rel = PurePosixPath(pkg_dir)
            for _ in range(len(mod) - len(mod.lstrip(".")) - 1):
                rel = rel.parent
            cand = [f"{rel}/{mod.lstrip('.').replace('.', '/')}.py"] if mod.lstrip(".") else []
        else:
            base = mod.replace(".", "/")
            cand = [f"{r}{base}.py" for r in ("", "src/", "python/")] + [f"{r}{base}/__init__.py" for r in ("", "src/")]
        for c in cand:
            c = normpath(c)
            if c in repo.file_set and c != path:
                sub, _ = _parse_cli(repo.read(c))
                merged.flags |= sub.flags
                merged.subcommands |= sub.subcommands
                merged.dynamic |= sub.dynamic
                break
    if not merged.flags and not merged.subcommands:
        return None
    return merged


def check_commands(doc: Doc, repo: Repo) -> Result:
    return CommandChecker(doc, repo).run()
