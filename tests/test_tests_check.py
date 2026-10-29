from conftest import checks

from readme_truth.engine import Options, run
from readme_truth.model import Severity

TESTS = {
    "tests/test_a.py": "def test_a(): pass\ndef test_b(): pass\ndef test_c(): pass\n",
    "tests/test_b.py": "def test_d(): pass\n",
}


def test_test_count_claims(make_repo):
    root = make_repo(
        {
            **TESTS,
            "README.md": """
        ![Tests](https://img.shields.io/badge/tests-3%20passing-green)
        Run `pytest`: 4 passed. The suite has **5 tests** (4 test functions) across 2 files.
        It passes 3/3.
        About 4 tests. Over 2 tests. 10+ tests.
        `tests/test_a.py` holds 3 tests.
        This release adds 9 tests. 26 test images. 7 integration tests.
    """,
        }
    )
    rep = run(root, Options(select={"test-count"}))
    found = {(f.line, f.claimed) for f in checks(rep, "test-count")}
    assert found == {(1, "3 tests"), (2, "5 tests"), (3, "3 tests"), (4, "10+ tests")}
    contra = checks(rep, "contradiction")
    assert len(contra) == 1 and "3 on line 1" in contra[0].claimed and "4 on line 2" in contra[0].claimed
    assert all(f.severity == Severity.ERROR for f in checks(rep, "test-count"))
    assert rep.per_check["test-count"].claims == 10


def test_test_count_fix_edit(make_repo):
    root = make_repo({**TESTS, "README.md": "[![t](https://img.shields.io/badge/tests-3%20passing-green)](x)\n"})
    f = checks(run(root, Options(select={"test-count"})), "test-count")[0]
    assert f.edits and f.suggestion == "[![t](https://img.shields.io/badge/tests-4%20passing-green)](x)"


def test_lower_bound_and_estimates(make_repo):
    root = make_repo(
        {
            "tests/test_x.py": "import pytest\n@pytest.mark.parametrize('x', cases())\ndef test_x(x): pass\n"
            "def test_y(): pass\n",
            "README.md": "1 thing. 5 tests here. Also 1 tests.\n",
        }
    )
    assert checks(run(root, Options(select={"test-count"})), "test-count") == []
    root2 = make_repo(
        {
            "tests/test_x.py": "".join(f"def test_{i}(): pass\n" for i in range(6))
            + "import pytest\n@pytest.mark.parametrize('x', cases())\ndef test_z(x): pass\n",
            "README.md": "5 tests here.\n",
        }
    )
    f = checks(run(root2, Options(select={"test-count"})), "test-count")
    assert len(f) == 1 and f[0].severity == Severity.ERROR and not f[0].edits


def test_mixed_go_and_python(make_repo):
    root = make_repo(
        {
            "tests/test_a.py": "def test_a(): pass\ndef test_b(): pass\n",
            "a_test.go": "package a\nfunc TestA(t *testing.T) {}\nfunc TestB(t *testing.T) {}\n"
            "func TestC(t *testing.T) {}\n",
            "README.md": "Go: 3 tests with `go test`. Python: 2 tests via pytest.\n\n"
            "pytest runs 5 tests\n\nIn all 4 tests.\n\nThere are 2 tests.\n",
        }
    )
    rep = run(root, Options(select={"test-count"}))
    lines = [f.line for f in checks(rep, "test-count")]
    assert lines == [3]
    assert checks(rep, "contradiction") == []


def test_ci_count_file_and_cmd(make_repo, tmp_path):
    root = make_repo({**TESTS, "README.md": "12 tests\n"})
    count = tmp_path / "count.txt"
    count.write_text("12")
    assert checks(run(root, Options(test_count_file=str(count))), "test-count") == []
    assert checks(run(root, Options(test_count_cmd="echo 11")), "test-count")[0].actual == "11"
    assert run(root, Options(collect=True)).test_count_source == "pytest --collect-only"


def test_coverage_report_and_gate(make_repo, tmp_path):
    root = make_repo(
        {
            "pyproject.toml": "[tool.coverage.report]\nfail_under = 90\n",
            ".github/workflows/ci.yml": "run: pytest --cov-fail-under=85\n",
            "coverage.xml": '<coverage line-rate="0.9809"></coverage>',
            "README.md": """
            ![c](https://img.shields.io/badge/coverage-98%25-green)
            Coverage is 97% today. 98.09% line coverage.
            CI enforces a 90% gate. Coverage gate of 80%. CI fails below 85%.
        """,
        }
    )
    rep = run(root, Options(select={"coverage"}))
    msgs = sorted((f.line, f.claimed) for f in checks(rep, "coverage"))
    assert msgs == [(2, "97% coverage"), (3, "a 80% coverage gate")]
    assert rep.per_check["coverage"].verified == 6
    other = tmp_path / "cov.json"
    other.write_text('{"totals": {"percent_covered": 97.2}}')
    assert len(checks(run(root, Options(select={"coverage"}, coverage_file=str(other))), "coverage")) == 3


def test_coverage_formats(tmp_path):
    from readme_truth.checks.tests import read_coverage

    lcov = tmp_path / "lcov.info"
    lcov.write_text("SF:a\nLF:10\nLH:8\nend_of_record\n")
    assert read_coverage(str(lcov)) == 80
    go = tmp_path / "cover.out"
    go.write_text("mode: set\na.go:1.1,2.2 3 1\na.go:3.1,4.2 1 0\n")
    assert read_coverage(str(go)) == 75
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert read_coverage(str(bad)) is None
    assert read_coverage(str(tmp_path / "missing")) is None
    empty = tmp_path / "empty"
    empty.write_text("nothing")
    assert read_coverage(str(empty)) is None


def test_marker_deselection_and_suite_words(make_repo):
    root = make_repo(
        {
            "pyproject.toml": '[tool.pytest.ini_options]\nmarkers = ["network: hits the internet"]\n',
            "tests/test_a.py": "import pytest\ndef test_a(): pass\ndef test_b(): pass\n"
            "@pytest.mark.network\ndef test_live(): pass\n",
            "README.md": "2 offline tests (3 with network). It returned a 500 while offline tests passed. "
            "The 2 scikit-learn tests.\n",
        }
    )
    rep = run(root, Options(select={"test-count"}))
    assert rep.findings == [] and rep.per_check["test-count"].verified == 1


def test_no_suite_no_contradiction(make_repo):
    root = make_repo({"README.md": "| repo | 428 tests |\n|---|---|\n| other | 495 tests |\n"})
    rep = run(root, Options(select={"test-count"}))
    assert rep.findings == [] and rep.per_check["test-count"].claims == 2
