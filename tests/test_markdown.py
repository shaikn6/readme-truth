from readme_truth.markdown import parse, slugify, split_row

DOC = """# Title
Some `inline/path.py` and a [link](docs/a.md#part) and ![img](img.png).

## Second Heading: with *style*
<a name="custom"></a>
<img src="logo.svg">

```bash
make test
```

| A | B |
|---|---|
| 1 | 0.5 |
| 2 | 0.6 |

[ref]: other.md
## Second Heading: with *style*
"""


def test_parse_pieces():
    doc = parse(DOC)
    assert [h[1] for h in doc.headings][:2] == ["Title", "Second Heading: with *style*"]
    assert {"title", "second-heading-with-style", "second-heading-with-style-1", "custom"} <= doc.anchors
    assert ("inline/path.py", 2) in doc.code_spans
    targets = {link.target for link in doc.links}
    assert {"docs/a.md#part", "img.png", "logo.svg", "other.md"} <= targets
    assert doc.fences[0].lang == "bash" and doc.fences[0].lines == ["make test"]
    assert doc.fences[0].start == 9 and 8 in doc.in_fence and 10 in doc.in_fence
    table = doc.tables[0]
    assert table.header == ["A", "B"] and [r[1] for r in table.rows] == [["1", "0.5"], ["2", "0.6"]]
    assert all(no not in doc.in_fence for no, _ in doc.prose())


def test_slugify_and_rows():
    assert slugify("Hello, `World`! [x](y)") == "hello-world-x"
    assert slugify("A & B") == "a--b"
    assert split_row("| a | b \\| c |") == ["a", "b \\| c"]


def test_unclosed_fence_swallows_rest():
    doc = parse("```\nx\ny")
    assert doc.fences[0].lines == ["x", "y"]
