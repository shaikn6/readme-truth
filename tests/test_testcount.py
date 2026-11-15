from readme_truth.repo import Repo
from readme_truth.testcount import collect, from_command, from_file, static_counts, static_go, static_js, static_pytest


def test_pytest_static_counts_parametrize_classes_fixtures(make_repo):
    root = make_repo(
        {
            "pyproject.toml": """
            [tool.pytest.ini_options]
            testpaths = ["tests"]
            addopts = "-m 'not slow'"
        """,
            "tests/conftest.py": """
            import pytest
            @pytest.fixture(params=[1, 2])
            def backend(request):
                return request.param
        """,
            "tests/test_a.py": """
            import pytest
            CASES = [1, 2, 3]
            def test_plain(): pass
            @pytest.mark.parametrize("x", CASES)
            def test_param(x): pass
            @pytest.mark.parametrize("x", [1, 2])
            @pytest.mark.parametrize("y", range(3))
            def test_stacked(x, y): pass
            def test_fixture(backend): pass
            @pytest.mark.slow
            def test_slow(): pass
            def helper(): pass
            class TestThing:
                def test_one(self): pass
                def test_two(self): pass
            class TestChild(TestThing):
                def test_three(self): pass
            class NotATest:
                def test_ignored(self): pass
            class TestWithInit:
                def __init__(self): pass
                def test_ignored(self): pass
        """,
            "tests/test_unit.py": """
            import unittest
            class Checks(unittest.TestCase):
                def test_a(self): pass
            async def test_async(): pass
        """,
            "other/test_outside.py": "def test_not_in_testpaths(): pass\n",
        }
    )
    c = static_pytest(Repo(root))
    # 1 + 3 + 6 + 2 (fixture params) + 2 (TestThing) + 3 (TestChild) + 1 (unittest) + 1 (async)
    assert c.tests == 19
    assert c.files == 2 and c.exact
    assert c.per_path["tests/test_unit.py"] == 2


def test_pytest_unresolved_parametrize_is_lower_bound(make_repo):
    root = make_repo(
        {
            "tests/test_x.py": """
        import pytest
        @pytest.mark.parametrize("x", load_cases())
        def test_x(x): pass
    """
        }
    )
    c = static_pytest(Repo(root))
    assert c.tests == 1 and not c.exact and c.lower_bound


def test_pytest_complex_mark_expression_is_estimate(make_repo):
    root = make_repo({"pytest.ini": "[pytest]\naddopts = -m 'slow or fast'\n", "test_x.py": "def test_x(): pass\n"})
    c = static_pytest(Repo(root))
    assert c.tests == 1 and not c.exact and not c.lower_bound


def test_pytest_syntax_error_and_generate_tests(make_repo):
    root = make_repo(
        {
            "tests/test_bad.py": "def test(:\n",
            "tests/test_ok.py": """
        def pytest_generate_tests(metafunc): pass
        def test_ok(): pass
    """,
        }
    )
    c = static_pytest(Repo(root))
    assert c.tests == 1 and not c.exact


def test_go_and_js_counts(make_repo):
    root = make_repo(
        {
            "a_test.go": 'package a\nfunc TestA(t *testing.T) {}\nfunc TestB(t *testing.T) { t.Run("x", nil) }\n'
            "func helper(t *testing.T) {}\nfunc TestMain(m *testing.M) {}\n",
            "web/app.test.ts": "it('a', () => {})\ntest.skip('b', () => {})\ndescribe('c', () => {})\n",
        }
    )
    repo = Repo(root)
    go = static_go(repo)
    assert go.tests == 2 and go.lower_bound and not go.exact
    js = static_js(repo)
    assert js.tests == 2 and not js.exact
    assert len(static_counts(repo)) == 2


def test_no_tests(make_repo):
    repo = Repo(make_repo({"README.md": "x"}))
    assert static_counts(repo) == [] and collect(repo) is None


def test_count_files(tmp_path):
    junit = tmp_path / "junit.xml"
    junit.write_text('<testsuites><testsuite tests="7"/><testsuite tests="3"/></testsuites>')
    assert from_file(str(junit)).tests == 10
    one = tmp_path / "one.xml"
    one.write_text('<testsuite tests="4"/>')
    assert from_file(str(one)).tests == 4
    summary = tmp_path / "out.txt"
    summary.write_text("==== 229 passed, 2 skipped, 1 warning in 3.1s ====")
    assert from_file(str(summary)).tests == 231
    plain = tmp_path / "n.txt"
    plain.write_text("42\n")
    assert from_file(str(plain)).tests == 42
    bad = tmp_path / "bad.xml"
    bad.write_text("<broken")
    assert from_file(str(bad)) is None
    empty = tmp_path / "empty.txt"
    empty.write_text("none")
    assert from_file(str(empty)) is None
    assert from_file(str(tmp_path / "missing")) is None


def test_from_command(make_repo):
    repo = Repo(make_repo({"x.txt": ""}))
    assert from_command(repo, "echo collected 12 tests; echo 13").tests == 13
    assert from_command(repo, "echo none") is None


def test_collect_runs_pytest(make_repo):
    root = make_repo({"tests/test_a.py": "import pytest\n@pytest.mark.parametrize('x', [1, 2])\ndef test_a(x): pass\n"})
    c = collect(Repo(root))
    assert c is not None and c.tests == 2 and c.source == "pytest --collect-only"


def test_pytest_length_evaluator_literal_forms(make_repo):
    # Each file exercises one branch of the static length evaluator (range, list(),
    # comprehension, name + binop, dict, starred). None of this runs the tests.
    root = make_repo(
        {
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tests/test_range.py": """
                import pytest
                @pytest.mark.parametrize("x", range(3))
                def test_r(x): pass
            """,
            "tests/test_listcall.py": """
                import pytest
                @pytest.mark.parametrize("x", list(range(4)))
                def test_l(x): pass
            """,
            "tests/test_comp.py": """
                import pytest
                @pytest.mark.parametrize("x", [i for i in range(5)])
                def test_c(x): pass
            """,
            "tests/test_binop.py": """
                import pytest
                CASES = [1, 2]
                @pytest.mark.parametrize("x", CASES + [3, 4])
                def test_b(x): pass
            """,
            "tests/test_dict.py": """
                import pytest
                @pytest.mark.parametrize("x", {"a": 1, "b": 2})
                def test_d(x): pass
            """,
            "tests/test_starred.py": """
                import pytest
                CASES = [1, 2]
                @pytest.mark.parametrize("x", [*CASES, 9])
                def test_s(x): pass
            """,
        }
    )
    c = static_pytest(Repo(root))
    assert c.exact
    assert c.per_path["tests/test_range.py"] == 3
    assert c.per_path["tests/test_listcall.py"] == 4
    assert c.per_path["tests/test_comp.py"] == 5
    assert c.per_path["tests/test_binop.py"] == 4
    assert c.per_path["tests/test_dict.py"] == 2
    assert c.per_path["tests/test_starred.py"] == 3
    assert c.tests == 21


def test_pytest_module_pytestmark_deselected(make_repo):
    root = make_repo(
        {
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\naddopts = "-m \'not slow\'"\n',
            "tests/test_mod.py": """
                import pytest
                pytestmark = pytest.mark.slow
                def test_a(): pass
                def test_b(): pass
            """,
        }
    )
    c = static_pytest(Repo(root))
    assert c.per_path["tests/test_mod.py"] == 0
    assert c.tests == 0


def test_pytest_dunder_test_false_and_nested_class(make_repo):
    root = make_repo(
        {
            "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "tests/test_cls.py": """
                class TestOptedOut:
                    __test__ = False
                    def test_ignored(self): pass
                class TestOuter:
                    def test_a(self): pass
                    class TestInner:
                        def test_b(self): pass
            """,
        }
    )
    c = static_pytest(Repo(root))
    # TestOptedOut is skipped; TestOuter contributes test_a plus the nested TestInner's test_b.
    assert c.per_path["tests/test_cls.py"] == 2
