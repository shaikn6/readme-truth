"""Parse a Markdown file into the pieces the checks look at.

This is not a full CommonMark parser. It keeps line numbers for every piece,
which is what a finding needs, and it is good enough for READMEs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FENCE_RE = re.compile(r"^(\s{0,3})(`{3,}|~{3,})\s*([\w+-]*)")
HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
INLINE_CODE_RE = re.compile(r"(`+)(.+?)\1")
LINK_RE = re.compile(r"(!?)\[((?:[^\[\]]|\[[^\]]*\])*)\]\(\s*<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\s*\)")
REF_DEF_RE = re.compile(r"^\s{0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+.*)?$")
HTML_ATTR_RE = re.compile(r"""\b(href|src)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
HTML_ANCHOR_RE = re.compile(r"""<a\s[^>]*\b(?:name|id)\s*=\s*["']([^"']+)["']""", re.IGNORECASE)
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


@dataclass
class Fence:
    lang: str
    start: int  # 1-based line number of the first content line
    lines: list[str]


@dataclass
class Link:
    text: str
    target: str
    line: int
    image: bool = False


@dataclass
class Table:
    start: int  # line of the header row
    header: list[str]
    rows: list[tuple[int, list[str]]]


@dataclass
class Doc:
    path: str  # relative to the repo root
    lines: list[str]
    fences: list[Fence] = field(default_factory=list)
    in_fence: set[int] = field(default_factory=set)
    headings: list[tuple[int, str, int]] = field(default_factory=list)  # level, text, line
    links: list[Link] = field(default_factory=list)
    code_spans: list[tuple[str, int]] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    anchors: set[str] = field(default_factory=set)

    def prose(self) -> list[tuple[int, str]]:
        """(line number, text) for every line outside fenced code."""
        return [(i, t) for i, t in enumerate(self.lines, 1) if i not in self.in_fence]


def slugify(text: str) -> str:
    """GitHub's heading anchor algorithm (close enough for READMEs)."""
    text = INLINE_CODE_RE.sub(lambda m: m.group(2), text)
    text = LINK_RE.sub(lambda m: m.group(2), text)
    text = re.sub(r"<[^>]+>", "", text).strip().lower()
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def split_row(line: str) -> list[str]:
    s = line.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    return [c.strip() for c in re.split(r"(?<!\\)\|", s)]


def parse(text: str, path: str = "README.md") -> Doc:
    doc = Doc(path=path, lines=text.splitlines())
    fence: Fence | None = None
    marker = ""
    for no, line in enumerate(doc.lines, 1):
        if fence is not None:
            stripped = line.strip()
            if stripped.startswith(marker) and set(stripped) <= {marker[0]}:
                fence = None
            else:
                fence.lines.append(line)
            doc.in_fence.add(no)
            continue
        m = FENCE_RE.match(line)
        if m:
            marker = m.group(2)
            fence = Fence(lang=m.group(3).lower(), start=no + 1, lines=[])
            doc.fences.append(fence)
            doc.in_fence.add(no)
            continue
        _scan_prose_line(doc, no, line)
    _assign_slugs(doc)
    _find_tables(doc)
    return doc


def _scan_prose_line(doc: Doc, no: int, line: str) -> None:
    h = HEADING_RE.match(line)
    if h:
        doc.headings.append((len(h.group(1)), h.group(2), no))
    for m in INLINE_CODE_RE.finditer(line):
        doc.code_spans.append((m.group(2).strip(), no))
    bare = INLINE_CODE_RE.sub("", line)
    for m in LINK_RE.finditer(bare):
        doc.links.append(Link(m.group(2), m.group(3), no, image=m.group(1) == "!"))
    r = REF_DEF_RE.match(bare)
    if r:
        doc.links.append(Link(r.group(1), r.group(2), no))
    for m in HTML_ATTR_RE.finditer(bare):
        doc.links.append(Link("", m.group(2), no, image=m.group(1).lower() == "src"))
    for m in HTML_ANCHOR_RE.finditer(bare):
        doc.anchors.add(m.group(1))


def _assign_slugs(doc: Doc) -> None:
    seen: dict[str, int] = {}
    for _, text, _ in doc.headings:
        slug = slugify(text)
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        doc.anchors.add(slug if n == 0 else f"{slug}-{n}")


def _find_tables(doc: Doc) -> None:
    lines = doc.lines
    i = 1
    while i < len(lines):
        no = i + 1  # 1-based line of lines[i]
        if no not in doc.in_fence and TABLE_SEP_RE.match(lines[i]) and "|" in lines[i - 1]:
            table = Table(start=no - 1, header=split_row(lines[i - 1]), rows=[])
            j = i + 1
            while j < len(lines) and "|" in lines[j] and (j + 1) not in doc.in_fence:
                table.rows.append((j + 1, split_row(lines[j])))
                j += 1
            doc.tables.append(table)
            i = j
        else:
            i += 1


BLOCK_START = re.compile(r"^\s*([-*+]\s|\d+[.)]\s|#|\||>)")


def sentence_at(doc: Doc, no: int, token: str) -> str:
    """The sentence that mentions the path; sentences often wrap across lines."""
    lines = doc.lines
    start = no - 1
    while start > 0 and lines[start - 1].strip() and not BLOCK_START.match(lines[start]):
        start -= 1
    end = no
    while end < len(lines) and lines[end].strip() and not BLOCK_START.match(lines[end]):
        end += 1
    paragraph = " ".join(ln.strip() for ln in lines[start:end])
    for sentence in re.split(r"(?<=[.!?;:])\s+(?=[A-Z`(])", paragraph):
        if token in sentence:
            return sentence
    return lines[no - 1]


def heading_at(doc: Doc, no: int) -> str:
    """Text of the closest heading above a line."""
    return next((text for _, text, line in reversed(doc.headings) if line < no), "")
