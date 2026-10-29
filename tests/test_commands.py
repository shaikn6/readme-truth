from readme_truth.checks.commands import make_targets
from readme_truth.engine import Options, run
from readme_truth.model import Severity

CLI_PY = """
import argparse
p = argparse.ArgumentParser()
p.add_argument("--steps", type=int)
p.add_argument("-v", "--verbose", action="store_true")
sub = p.add_subparsers()
sub.add_parser("poll", aliases=["p"])
sub.add_parser("serve")
from pkg import extra
"""
FILES = {
    "Makefile": "test lint:\n\tpytest\nbuild: ; go build\n",
    "scripts/train.py": CLI_PY,
    "pkg/__init__.py": "",
    "pkg/extra.py": "import argparse\nargparse.ArgumentParser().add_argument('--db-path')\n",
    "pkg/cli.py": "import click\n@click.command()\n@click.option('--port', '-p')\n@click.option('--debug/--no-debug')\n"
    "def main(port, debug): pass\n",
    "pkg/dyn.py": "import argparse\np = argparse.ArgumentParser()\nfor f in FLAGS: p.add_argument(f)\n",
    "pkg/__main__.py": "import argparse\nargparse.ArgumentParser().add_argument('--x')\n",
    "pyproject.toml": """
        [project]
        name = "demo-pkg"
        dependencies = []
        [project.optional-dependencies]
        dev = ["pytest"]
        [project.scripts]
        demo = "pkg.cli:main"
        dyn = "pkg.dyn:main"
    """,
    "requirements-dev.txt": "pytest\n",
    "web/package.json": '{"scripts": {"dev": "vite", "test": "vitest"}}',
    "cmd/server/main.go": "package main\n",
    "deploy/compose.yml": "",
}


def findings(make_repo, readme: str):
    root = make_repo({**FILES, "README.md": readme}, git=True)
    rep = run(root, Options(select={"command"}))
    return [(f.line, f.claimed, f.severity) for f in rep.findings], rep


def test_make_and_scripts(make_repo):
    got, _ = findings(
        make_repo,
        """
        ```bash
        make test lint
        make build VAR=1
        make deploy
        make -C web dev
        python scripts/train.py --steps 5 -v --verb --nope
        python3 scripts/missing.py
        python scripts/train.py poll --db-path x
        python scripts/train.py publish
        python app.py
        bash scripts/setup.sh
        ./scripts/run.sh
        ```
    """,
    )
    assert got == [
        (4, "make target `deploy`", Severity.ERROR),
        (5, "a Makefile", Severity.ERROR),
        (6, "`scripts/train.py` accepts `--nope`", Severity.ERROR),
        (7, "script `scripts/missing.py` exists", Severity.ERROR),
        (9, "`scripts/train.py publish`", Severity.ERROR),
        (10, "script `app.py` exists", Severity.NOTE),
        (11, "script `scripts/setup.sh` exists", Severity.ERROR),
        (12, "executable `./scripts/run.sh` exists", Severity.ERROR),
    ]


def test_pip_pytest_npm_go(make_repo):
    got, _ = findings(
        make_repo,
        """
        ```console
        $ pip install -e ".[dev,docs]"
        $ pip install "demo-pkg[gpu]" requests[socks]
        $ pip install -r requirements-dev.txt -r requirements-gone.txt
        $ pytest --cov=src -n auto tests/test_none.py
        collected output line that is not a command
        $ cd web && npm run dev && npm test && npm run build && cd ..
        $ go run ./cmd/server && go build ./cmd/nope/...
        $ docker compose -f deploy/compose.yml -f deploy/gone.yml up
        ```
    """,
    )
    assert got == [
        (2, "extra `[docs]` exists", Severity.ERROR),
        (3, "extra `[gpu]` exists", Severity.ERROR),
        (4, "requirements file `requirements-gone.txt` exists", Severity.ERROR),
        (5, "`--cov` works", Severity.WARNING),
        (5, "`-n` works", Severity.WARNING),
        (5, "test path `tests/test_none.py` exists", Severity.ERROR),
        (7, "npm script `build`", Severity.ERROR),
        (8, "Go package `./cmd/nope` exists", Severity.ERROR),
        (9, "file `deploy/gone.yml` exists", Severity.ERROR),
    ]


def test_console_scripts_modules_and_state(make_repo):
    got, _ = findings(
        make_repo,
        """
        Run `demo --port 8000 --debug --colour` or `python -m pkg --x 1 --y 2` or `python -m pkg.nope`.

        ```sh
        dyn --anything
        python -m pytest -q
        python -m venv .venv && .venv/bin/python scripts/train.py --steps 1
        git clone https://github.com/a/demo.git && cd demo && make test
        go build -o bin/tool ./cmd/server && bin/tool
        echo hi > made.txt && cp made.txt copy.txt && cp gone.txt x.txt
        uv run python scripts/train.py --steps 2
        python -c "print(1)"
        cd nowhere
        python scripts/train.py --nope
        ```
        ```sh
        mkdir out && cd out && python ../scripts/missing.py
        cd ~ && python scripts/missing.py
        ```
    """,
    )
    assert got == [
        (1, "`demo` accepts `--colour`", Severity.ERROR),
        (1, "`python -m pkg` accepts `--y`", Severity.ERROR),
        (1, "module `pkg.nope` exists", Severity.ERROR),
        (9, "source file `gone.txt` exists", Severity.NOTE),
        (12, "directory `nowhere` exists", Severity.NOTE),
    ]


def test_make_targets_parse():
    defined, uncertain = make_targets("all: a \\\n b\n.PHONY: all\nx := 1\ninclude other.mk\n")
    assert {"all", ".PHONY"} <= defined and "x" not in defined and uncertain
    assert make_targets("%.o: %.c\n\tcc\n")[1]
    assert make_targets("a b: c\n") == ({"a", "b"}, False)
