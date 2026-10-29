from conftest import checks

from readme_truth.checks.versions import _allowed, _spec
from readme_truth.engine import Options, run
from readme_truth.model import Severity


def run_versions(root):
    return run(root, Options(select={"version"}))


def test_python_requirements(make_repo):
    root = make_repo(
        {
            "pyproject.toml": '[project]\nname = "x"\nrequires-python = ">=3.11,<3.14"\n'
            'dependencies = ["pydantic>=2.5", "fastapi==0.110.1"]\n',
            "README.md": """
                          ![py](https://img.shields.io/badge/python-3.11%2B-blue)
                          Requires Python 3.10 or newer.
                          Tested on Python 3.12. Python 3.14 is not supported yet.
                          Old Pydantic v1 code was dropped.
                          FastAPI 0.95 style. Pydantic v2 and FastAPI 0.110 are fine. Works on Python 3.9.
                      """,
        }
    )
    rep = run_versions(root)
    got = [(f.line, f.claimed, f.severity) for f in rep.findings]
    assert (2, "python 3.10+", Severity.ERROR) in got
    assert (5, "FastAPI 0.95", Severity.WARNING) in got
    assert (5, "python 3.9", Severity.ERROR) in got
    assert any(f.check == "contradiction" for f in rep.findings)
    assert len(got) == 4


def test_go_patch_minimum(make_repo):
    root = make_repo({"go.mod": "module x\n\ngo 1.26.10\n", "README.md": "Requires Go 1.26.\n\nBuilt with Go 1.27.\n"})
    f = checks(run_versions(root), "version")
    assert len(f) == 1 and f[0].line == 1 and f[0].severity == Severity.WARNING
    assert f[0].suggestion == "write Go 1.26.10+"


def test_node_and_package_json(make_repo):
    root = make_repo(
        {
            "package.json": '{"engines": {"node": ">=20"}, "dependencies": {"react": "^18.3.1", '
            '"three": "^0.160.0"}, "devDependencies": {"vite": "~5.2.0"}}',
            "README.md": "Node 18 works. React 19 + Vite 5. Node.js 20+.\n",
        }
    )
    got = sorted((f.claimed, f.severity) for f in checks(run_versions(root), "version"))
    assert got == [("React 19", Severity.WARNING), ("node 18", Severity.ERROR)]


def test_pinned_sources(make_repo):
    root = make_repo(
        {
            "Dockerfile": "FROM python:3.11-slim\n",
            ".nvmrc": "20\n",
            "README.md": "Needs Python 3.12+. Python 3.10 ok. Node 22+ required.\n",
        }
    )
    got = sorted(f.claimed for f in checks(run_versions(root), "version"))
    assert got == ["node 22+", "python 3.12+"]


def test_setup_py_and_no_source(make_repo):
    root = make_repo({"setup.py": "setup(python_requires='>=3.9')\n", "README.md": "Python 3.8+\n\nGo 1.22\n"})
    rep = run_versions(root)
    assert [f.claimed for f in rep.findings] == ["python 3.8+"]
    assert rep.per_check["version"].claims == 2 and rep.per_check["version"].verified == 1


def test_specifier_operators():
    r = _spec(">2.0,!=2.5,<=3.0", "x")
    assert _allowed(r, (2, 1)) and not _allowed(r, (2, 5)) and not _allowed(r, (3, 1)) and not _allowed(r, (2, 0))
    assert _allowed(_spec("~=3.10", "x"), (3, 11)) and not _allowed(_spec("~=3.10", "x"), (4, 0))
    assert _allowed(_spec("^18.2", "x"), (18, 5)) and not _allowed(_spec("^18.2", "x"), (19, 0))
    assert _allowed(_spec("~1.4.2", "x"), (1, 4, 9)) and not _allowed(_spec("~1.4.2", "x"), (1, 5))
    assert _spec("any", "x") is None
