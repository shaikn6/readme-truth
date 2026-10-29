"""Relative links, #anchors and (opt-in) external links."""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import PurePosixPath

from ..markdown import Doc, parse
from ..model import Finding, Result, Severity
from ..repo import Repo, join, normpath


def check_links(doc: Doc, repo: Repo, external: bool = False) -> Result:
    res = Result()
    doc_dir = PurePosixPath(doc.path).parent
    anchor_cache: dict[str, set[str] | None] = {doc.path: None if doc.generated_anchors else doc.anchors}
    urls: list[tuple[int, str]] = []
    for link in doc.links:
        target = link.target.strip()
        if target.startswith(("http://", "https://")):
            urls.append((link.line, target))
            continue
        if target.startswith(("mailto:", "tel:", "data:", "//", "javascript:")) or "{" in target or "$" in target:
            continue
        path, _, anchor = target.partition("#")
        path = urllib.parse.unquote(path.split("?")[0])
        res.claims += 1
        if path:
            rel = normpath(path.lstrip("/")) if path.startswith("/") else join(str(doc_dir), path)
            if rel.startswith(".."):
                continue  # GitHub web routes such as ../../actions or ../../issues
            res.verified += 1
            found = next((c for c in (rel, f"{rel}.md", f"{rel}/index.md", f"{rel}/README.md") if repo.exists(c)), None)
            if found is None:  # extensionless links are how static doc sites (mkdocs) point at pages
                res.findings.append(
                    Finding(
                        check="link",
                        file=doc.path,
                        line=link.line,
                        claim=link.target,
                        claimed=f"link to `{path}`",
                        actual="no such file" + (" (git ignores it)" if repo.ignored(rel) else ""),
                        evidence="git ls-files",
                        severity=Severity.ERROR,
                        suggestion="fix or remove the link",
                    )
                )
                continue
            target_doc = found
        else:
            res.verified += 1
            target_doc = doc.path
        if anchor and target_doc.lower().endswith((".md", ".markdown")):
            if target_doc not in anchor_cache:
                target = parse(repo.read(target_doc), target_doc)
                anchor_cache[target_doc] = None if target.generated_anchors else target.anchors
            if anchor_cache[target_doc] is None:
                continue
            known = anchor_cache[target_doc] | {"top"}  # browsers scroll #top to the start of the page
            if anchor not in known and urllib.parse.unquote(anchor).lower() not in known:
                res.findings.append(
                    Finding(
                        check="link",
                        file=doc.path,
                        line=link.line,
                        claim=link.target,
                        claimed=f"anchor `#{anchor}`",
                        actual=f"no heading with that anchor in {target_doc}",
                        evidence="heading slugs",
                        severity=Severity.WARNING,
                        suggestion="point the link at an existing heading",
                    )
                )
    if external and urls:
        res.add(_check_external(doc, urls))
    return res


def _status(url: str) -> int | None:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "readme-truth"})
    for attempt in ("HEAD", "GET"):
        req.method = attempt
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            if attempt == "GET" or e.code not in (403, 405, 501):
                return e.code
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
    return None


def _check_external(doc: Doc, urls: list[tuple[int, str]]) -> Result:
    res = Result(claims=len(urls))
    unique = sorted({u for _, u in urls})
    with ThreadPoolExecutor(max_workers=8) as pool:
        status = dict(zip(unique, pool.map(_status, unique), strict=True))
    for no, url in urls:
        code = status[url]
        if code is None:
            continue
        res.verified += 1
        if code in (404, 410):
            res.findings.append(
                Finding(
                    check="link",
                    file=doc.path,
                    line=no,
                    claim=url,
                    claimed="a working URL",
                    actual=f"HTTP {code}",
                    evidence="HTTP request",
                    severity=Severity.WARNING,
                    suggestion="update or remove the link",
                )
            )
    return res
