from readme_truth.fix import apply
from readme_truth.model import Edit, Finding, Severity


def _finding(file: str, edits: list[Edit]) -> Finding:
    return Finding(
        check="test-count",
        file=file,
        line=edits[0].line if edits else 1,
        claim="",
        claimed="",
        actual="",
        evidence="",
        severity=Severity.ERROR,
        edits=edits,
    )


def test_two_edits_on_one_line_both_land(tmp_path):
    # The left edit grows the line, so only the right-to-left order keeps the
    # right edit's offsets valid. A left-to-right apply would corrupt the line.
    (tmp_path / "f.md").write_text("badge 4 passing, 222 funcs\n", encoding="utf-8")
    finding = _finding(
        "f.md",
        [
            Edit(line=1, start=6, end=7, new="4000"),  # 4 -> 4000 (grows by 3)
            Edit(line=1, start=17, end=20, new="99"),  # 222 -> 99
        ],
    )
    done = apply(str(tmp_path), [finding])
    assert done == {"f.md": 2}
    assert (tmp_path / "f.md").read_text(encoding="utf-8") == "badge 4000 passing, 99 funcs\n"


def test_duplicate_edit_applied_once(tmp_path):
    # The same (line, start, end) carried by two findings is applied a single time.
    (tmp_path / "f.md").write_text("tests-52 passing\n", encoding="utf-8")
    edit = Edit(line=1, start=6, end=8, new="60")
    done = apply(str(tmp_path), [_finding("f.md", [edit]), _finding("f.md", [edit])])
    assert done == {"f.md": 1}
    assert (tmp_path / "f.md").read_text(encoding="utf-8") == "tests-60 passing\n"


def test_finding_without_edits_leaves_file_untouched(tmp_path):
    (tmp_path / "touched.md").write_text("12 tests\n", encoding="utf-8")
    (tmp_path / "skipped.md").write_text("no edit here\n", encoding="utf-8")
    done = apply(
        str(tmp_path),
        [
            _finding("touched.md", [Edit(line=1, start=0, end=2, new="15")]),
            _finding("skipped.md", []),
        ],
    )
    assert done == {"touched.md": 1}
    assert (tmp_path / "skipped.md").read_text(encoding="utf-8") == "no edit here\n"
