from conftest import checks

from readme_truth.checks.numbers import DataPool, check_numbers
from readme_truth.engine import Options, run
from readme_truth.markdown import parse
from readme_truth.model import Severity
from readme_truth.repo import Repo

MIT = "MIT License\n\nPermission is hereby granted, free of charge, to any person\n"
APACHE = "                                 Apache License\n                           Version 2.0, January 2004\n"


def test_license_matches_and_mismatches(make_repo):
    root = make_repo(
        {
            "LICENSE": MIT,
            "README.md": """
        [![License](https://img.shields.io/badge/license-Apache--2.0-blue)](LICENSE)
        Released under the MIT license.
        The dataset is licensed under CC BY 4.0 and the logo under Apache 2.0.

        ## License
        MIT
    """,
        }
    )
    got = [(f.line, f.claimed) for f in checks(run(root, Options(select={"license"})), "license")]
    assert got == [(1, "Apache-2.0 licence")]


def test_license_file_missing_or_unknown(make_repo):
    root = make_repo({"README.md": "## License\nApache License 2.0\n"})
    f = checks(run(root, Options(select={"license"})), "license")
    assert f[0].actual == "no LICENSE file" and f[0].severity == Severity.ERROR
    root2 = make_repo({"README.md": "## License\nGPLv3\n", "COPYING": "Some custom terms\n"})
    rep = run(root2, Options(select={"license"}))
    assert rep.findings == [] and rep.per_check["license"].claims == 1


def test_license_apache_file(make_repo):
    root = make_repo({"LICENSE": APACHE, "README.md": "Licensed under the Apache License, Version 2.0.\n"})
    assert run(root, Options(select={"license"})).findings == []


def test_number_provenance(make_repo):
    root = make_repo(
        {
            "results.json": '{"auc": 0.7735, "ece": 0.0115, "n": 1572, "rate": 0.215, "delta": -0.0123, '
            '"big": 2600000}',
            "eval/metrics.csv": "model,acc\nlr,88.24\n",
            "README.md": """
            | Model | AUC | ECE | Correct | Rate | Acc | Delta | Params | Note |
            |---|---|---|---|---|---|---|---|---|
            | HGB | 0.774 | 0.012 | 1,572 | 21.5% | 88.2 | -0.012 | 2.6M | TLS 1.2 minimum in prose |
            | LR | 0.746 | 0.5 ± 0.01 | 3 | 2026 | v1.2.3 | 0.0124 | 9 | 12 |

            | Endpoint | Limit |
            |---|---|
            | /a | 100 |
        """,
        }
    )
    rep = run(root, Options(select={"number-provenance"}))
    f = checks(rep, "number-provenance")
    assert len(f) == 1 and f[0].line == 1 and f[0].severity == Severity.NOTE
    assert "0.746, 0.5, 0.0124, 12" in f[0].actual
    assert "1.2" not in f[0].actual.split(":")[1].split(", ")


def test_number_provenance_needs_data(make_repo):
    root = make_repo({"README.md": "| a | b |\n|---|---|\n| x | 0.5 |\n"})
    assert run(root, Options(select={"number-provenance"})).findings == []
    repo = Repo(root)
    assert check_numbers(parse("no tables", "README.md"), repo).claims == 0


def test_data_pool_skips_large_and_lockfiles(make_repo, monkeypatch):
    from readme_truth.checks import numbers

    root = make_repo({"package-lock.json": '{"v": 0.123}', "big.json": '{"v": 0.456}', "CHANGELOG.md": "0.789"})
    monkeypatch.setattr(numbers, "MAX_FILE_BYTES", 5)
    pool = DataPool(Repo(root), set())
    assert pool.files == ["big.json"] and len(pool.values) == 0


def test_license_about_other_work_is_ignored(make_repo):
    root = make_repo(
        {
            "LICENSE": MIT,
            "README.md": """
            ## License
            MIT. The upstream repository [X](https://github.com/x/y) is Apache-2.0; check with its authors.
            This project is licensed under the MIT License.
        """,
        }
    )
    rep = run(root, Options(select={"license"}))
    assert rep.findings == [] and rep.per_check["license"].claims == 1
