"""Score readme-truth against the audit labels.

    python eval/run_eval.py --snap <dir> --split dev|heldout|all

<dir>/<repo>/before and <dir>/<repo>/after are checkouts of each repository at
the last commit before the 2026-10-28 fix PRs and at HEAD (see eval/README.md).
Writes eval/results/<split>.json and prints the tables.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))
sys.path.insert(0, str(HERE))

from labels import ACCEPT, REPOS, labels  # noqa: E402

from readme_truth.engine import Options, run  # noqa: E402
from readme_truth.model import Severity  # noqa: E402


def split(name: str) -> list[str]:
    """Fixed before any development: the 11 repos first in sha256 order are dev, the rest held out."""
    ordered = sorted(REPOS, key=lambda r: hashlib.sha256(r.encode()).hexdigest())
    return {"dev": ordered[:11], "heldout": ordered[11:], "all": ordered}[name]


def hit(label: dict, f: dict) -> bool:
    if f["check"] not in ACCEPT.get(label["type"], {label["type"]}) or f["file"] != label["file"]:
        return False
    lo, hi = min(label["lines"]), max(label["lines"])
    return lo - 2 <= f["line"] <= hi


def key(repo: str, f: dict) -> str:
    return f"{repo}|{f['file']}|{f['line']}|{f['check']}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True)
    ap.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    args = ap.parse_args()
    opts = Options(docs=True)
    review = json.loads((HERE / "after_review.json").read_text()) if (HERE / "after_review.json").exists() else {}
    repos = split(args.split)
    out: dict = {"split": args.split, "repos": repos, "labels": [], "after": [], "before_unlabelled": [], "claims": {}}
    for repo in repos:
        found = {}
        for state in ("before", "after"):
            rep = run(Path(args.snap) / repo / state, opts)
            found[state] = [f.to_dict() for f in rep.findings]
            out["claims"][f"{repo}/{state}"] = {k: r.claims for k, r in rep.per_check.items()}
        mine = [lb for lb in labels() if lb["repo"] == repo]
        for lb in mine:
            if lb["type"]:
                hits = [f for f in found["before"] if hit(lb, f)]
                still = [f for f in found["after"] if hit(lb, f)]
                lb = dict(
                    lb,
                    detected=bool(hits),
                    severity=max((f["severity"] for f in hits), key=lambda s: Severity[s.upper()], default=None),
                    after_hits=len(still),
                )
            out["labels"].append(lb)
        labelled = {id(f) for lb in mine if lb["type"] for f in found["before"] if hit(lb, f)}
        out["before_unlabelled"] += [dict(f, repo=repo) for f in found["before"] if id(f) not in labelled]
        for f in found["after"]:
            out["after"].append(dict(f, repo=repo, review=review.get(key(repo, f))))
    res_dir = HERE / "results"
    res_dir.mkdir(exist_ok=True)
    (res_dir / f"{args.split}.json").write_text(json.dumps(out, indent=1))
    print_tables(out)
    return 0


def print_tables(out: dict) -> None:
    in_scope = [lb for lb in out["labels"] if lb["type"]]
    print(
        f"split={out['split']} repos={len(out['repos'])} audit discrepancies={len(out['labels'])} "
        f"in scope={len(in_scope)} out of scope={len(out['labels']) - len(in_scope)}"
    )
    by_type = defaultdict(lambda: [0, 0])
    for lb in in_scope:
        by_type[lb["type"]][0] += lb["detected"]
        by_type[lb["type"]][1] += 1
    print("\nRecall on in-scope audit discrepancies at the 'before' commits")
    for t, (d, n) in sorted(by_type.items()):
        print(f"  {t:18} {d}/{n}")
    d = sum(v[0] for v in by_type.values())
    n = sum(v[1] for v in by_type.values())
    print(f"  {'total':18} {d}/{n}")
    for lb in in_scope:
        mark = "hit " if lb["detected"] else "MISS"
        sev, after = lb["severity"], lb["after_hits"]
        print(f"   {mark} {lb['id']:6} {lb['type']:17} {lb['what'][:70]} (severity {sev}, after: {after})")
    print("\nOut of scope by category")
    for why, c in Counter(lb["why"] for lb in out["labels"] if not lb["type"]).most_common():
        print(f"  {c:3} {why}")
    claims_after = Counter()
    for k, v in out["claims"].items():
        if k.endswith("/after"):
            claims_after.update(v)
    print("\nFindings on the 'after' commits (HEAD), by check and review verdict")
    rows = defaultdict(Counter)
    for f in out["after"]:
        verdict = (f["review"] or {}).get("verdict", "unreviewed")
        rows[f["check"]][(f["severity"], verdict)] += 1
    for check in sorted(set(claims_after) | set(rows)):
        cells = ", ".join(f"{s}/{v}={c}" for (s, v), c in sorted(rows[check].items()))
        print(f"  {check:18} claims={claims_after.get(check, 0):4}  {cells}")
    fp = [f for f in out["after"] if (f["review"] or {}).get("verdict") == "fp"]
    total_claims = sum(claims_after.values())
    print(
        f"\n  false positives: {len(fp)} of {total_claims} claims checked "
        f"({100 * len(fp) / max(1, total_claims):.2f}%); unreviewed: "
        f"{sum(1 for f in out['after'] if not f['review'])}"
    )
    print(f"\nUnlabelled findings at 'before': {len(out['before_unlabelled'])}")


if __name__ == "__main__":
    raise SystemExit(main())
