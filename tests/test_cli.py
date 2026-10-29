import json

from readme_truth import __version__
from readme_truth.cli import main
from readme_truth.engine import Options, run

FILES = {
    "tests/test_a.py": "def test_a(): pass\ndef test_b(): pass\n",
    "LICENSE": "MIT License\n\nPermission is hereby granted, free of charge\n",
    "README.md": "# Demo\n\n![t](https://img.shields.io/badge/tests-3%20passing-green)\n\n"
    "Run `pytest`: 3 passed, 97% coverage.\n\nSee [docs](docs/none.md).\n\n"
    "<!-- readme-truth: ignore -->\nAlso 9 tests.\n\nAnd 8 tests. <!-- readme-truth: ignore -->\n\n"
    "<!-- readme-truth: ignore -->\n```text\nexample: 7 tests\n\n6 tests\n```\n",
    "docs/extra.md": "Python 3.8+\n",
    "pyproject.toml": '[project]\nname = "demo"\nrequires-python = ">=3.11"\n',
    "CHANGELOG.md": "Python 3.1\n",
}


def test_text_output_and_exit_code(make_repo, capsys):
    root = make_repo(FILES)
    assert main(["check", str(root)]) == 1
    out = capsys.readouterr().out
    assert "README.md:3: error [test-count] Claimed 3 tests; found 2" in out
    assert "README.md:7: error [link]" in out
    assert "9 tests" not in out and "8 tests" not in out
    assert out.strip().splitlines()[-1].startswith("8 claims found")


def test_fail_on_levels(make_repo, capsys):
    root = make_repo({**FILES, "README.md": "# Demo\n\nPython 3.11.\n\n[a](#nope)\n"})
    assert main(["check", str(root)]) == 0
    assert main(["check", str(root), "--fail-on", "warning"]) == 1
    assert main(["check", str(root), "--fail-on", "never", "--format", "json"]) == 0
    capsys.readouterr()


def test_json_github_sarif(make_repo, capsys, monkeypatch):
    root = make_repo(FILES)
    main(["check", str(root), "--format", "json", "--docs"])
    data = json.loads(capsys.readouterr().out)
    assert data["version"] == __version__ and data["summary"]["files"] == ["README.md", "docs/extra.md"]
    assert {f["check"] for f in data["findings"]} == {"test-count", "link", "version"}
    assert data["findings"][0]["fixable"] is True
    monkeypatch.chdir(root)
    main(["check", ".", "--format", "github"])
    gh = capsys.readouterr().out
    assert "::error file=README.md,line=3,title=readme-truth test-count::" in gh
    main(["check", ".", "--format", "sarif", "--select", "link"])
    sarif = json.loads(capsys.readouterr().out)
    assert sarif["runs"][0]["results"][0]["ruleId"] == "link"


def test_select_ignore_and_errors(make_repo, capsys, tmp_path):
    root = make_repo(FILES)
    assert main(["check", str(root), "--select", "nope"]) == 2
    assert main(["check", str(root), "--ignore", "test-count,link"]) == 0
    assert main(["check", str(tmp_path / "empty")]) == 2
    capsys.readouterr()


def test_fix_rewrites_counts(make_repo, capsys):
    root = make_repo(FILES)
    assert main(["check", str(root), "--fix", "--select", "test-count"]) == 0
    text = (root / "README.md").read_text()
    assert "tests-2%20passing" in text and "2 passed" in text and "9 tests" in text
    assert "fixed 2 claim(s) in README.md" in capsys.readouterr().err


def test_explicit_files_and_options(make_repo):
    root = make_repo(FILES)
    rep = run(root, Options(files=["docs/extra.md"]))
    assert rep.files == ["docs/extra.md"] and [f.check for f in rep.findings] == ["version"]


def test_module_entry_point():
    import subprocess
    import sys

    r = subprocess.run([sys.executable, "-m", "readme_truth", "--version"], capture_output=True, text=True)
    assert r.stdout.strip() == f"readme-truth {__version__}"
