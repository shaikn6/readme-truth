from conftest import checks

from readme_truth.checks.paths import tree_paths
from readme_truth.engine import Options, run
from readme_truth.model import Severity

FILES = {
    "src/app/main.py": "",
    "docs/guide.md": "# Guide\n## Setup Steps\n",
    "data/results.json": "{}",
    ".gitignore": "runs/\n",
    "package.json": '{"dependencies": {"three": "^0.1.0"}}',
}


def test_paths(make_repo):
    root = make_repo(
        {
            **FILES,
            "README.md": """
        Code in `src/app/main.py`, results in `data/results.json` and `results.json`.
        Missing: `src/app/gone.py`, `notes.txt`, and docs/missing.md in prose.
        Curves land in `runs/`. The run writes `out/report.csv`.
        Not paths: `owner/repo`, `BAAI/bge-small-en-v1.5`, `three.js`, `http://x/y.py`, `*.py`, `$HOME/x.py`,
        `models prefixed with ollama/`, `1.2/3`, `make`, `foo`.

        ```
        repo/
        ├── src/
        │   └── app/
        │       └── main.py      # entry
        ├── docs/guide.md
        ├── ...
        └── missing_dir/
        ```
    """,
        },
        git=True,
    )
    rep = run(root, Options(select={"path"}))
    got = {(f.line, f.claim, f.severity) for f in rep.findings}
    assert got == {
        (2, "src/app/gone.py", Severity.WARNING),
        (2, "notes.txt", Severity.NOTE),
        (2, "docs/missing.md", Severity.WARNING),
        (3, "runs/", Severity.NOTE),
        (3, "out/report.csv", Severity.NOTE),
        (14, "missing_dir/", Severity.WARNING),
    }


def test_tree_paths():
    lines = ["proj/", "├── a/", "│   ├── b.py", "│   └── c/", "│       └── d.py", "└── e.md  # note", "    plain"]
    assert [p for _, p in tree_paths(lines, 1)] == ["a/", "a/b.py", "a/c/", "a/c/d.py", "e.md"]


def test_links(make_repo):
    root = make_repo(
        {
            **FILES,
            "README.md": """
        # Readme
        [ok](docs/guide.md) [anchor](docs/guide.md#setup-steps) [bad anchor](docs/guide.md#nope)
        [missing](docs/nope.md) [self](#readme) [self bad](#zzz) [top](#top) [web](https://example.com)
        [gh](../../issues) [mail](mailto:a@b.c) <img src="docs/logo.png"> [q](data/results.json?raw=1)
        [ignored](runs/x.png)
    """,
        },
        git=True,
    )
    rep = run(root, Options(select={"link"}))
    got = sorted((f.line, f.claim, f.severity) for f in rep.findings)
    assert got == [
        (2, "docs/guide.md#nope", Severity.WARNING),
        (3, "#zzz", Severity.WARNING),
        (3, "docs/nope.md", Severity.ERROR),
        (4, "docs/logo.png", Severity.ERROR),
        (5, "runs/x.png", Severity.ERROR),
    ]
    assert "git ignores it" in rep.findings[-1].actual


def test_external_links(make_repo, monkeypatch):
    from readme_truth.checks import links

    codes = {"https://a.example/ok": 200, "https://a.example/gone": 404, "https://a.example/down": None}
    monkeypatch.setattr(links, "_status", lambda url: codes[url])
    root = make_repo(
        {"README.md": "[a](https://a.example/ok) [b](https://a.example/gone) [c](https://a.example/down)\n"}
    )
    rep = run(root, Options(select={"link"}, check_links=True))
    assert [f.claim for f in checks(rep, "link")] == ["https://a.example/gone"]
    assert rep.per_check["link"].verified == 2


def test_status_handles_errors(monkeypatch):
    import urllib.error

    from readme_truth.checks import links

    def boom(req, timeout):
        if req.method == "HEAD":
            raise urllib.error.HTTPError(req.full_url, 405, "no", {}, None)
        raise urllib.error.HTTPError(req.full_url, 404, "gone", {}, None)

    monkeypatch.setattr(links.urllib.request, "urlopen", boom)
    assert links._status("https://x.example") == 404

    def offline(req, timeout):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(links.urllib.request, "urlopen", offline)
    assert links._status("https://x.example") is None

    class Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(links.urllib.request, "urlopen", lambda req, timeout: Resp())
    assert links._status("https://x.example") == 200
