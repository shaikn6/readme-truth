"""Collect every headline number of the evaluation into eval/results/summary.json.

The README's Results section is written from this file, so its numbers trace to a
committed source (readme-truth checks that on itself).
"""

from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
RES, TP = HERE / "results", HERE / "third_party"
SETS = {
    "A": ["requests", "flask", "httpx", "cobra", "gin"],
    "B": ["click", "starlette", "pydantic", "viper", "cli"],
    "C": ["black", "rich", "typer", "chi", "logrus"],
}


def tp_key(repo: str, f: dict) -> str:
    return f"{repo}|{f['file']}|{f['line']}|{f['check']}|{f['claim']}"


def sample(set_name: str, mode: str, n: int = 20) -> list[tuple[str, dict]]:
    """The seeded sample that was read by hand (seed 0, as drawn at the time)."""
    items = [
        (r, f)
        for r in SETS[set_name]
        for f in json.loads((TP / f"{r}_set{set_name}_{mode}.json").read_text())["findings"]
    ]
    if len(items) <= n:
        return items
    random.seed(0)
    return [items[k] for k in sorted(random.sample(range(len(items)), n))]


def precision(items: list[tuple[str, dict]], verdicts: dict) -> dict:
    by_sev: Counter = Counter()
    real = 0
    for repo, f in items:
        v = verdicts[tp_key(repo, f)]["verdict"]
        real += v == "real"
        by_sev[(f["severity"], v)] += 1
    return {
        "read": len(items),
        "real": real,
        "fp": len(items) - real,
        "by_severity": {f"{s}/{v}": c for (s, v), c in sorted(by_sev.items())},
    }


def split_summary(name: str, review: dict) -> dict:
    d = json.loads((RES / f"{name}.json").read_text())
    in_scope = [lb for lb in d["labels"] if lb["type"]]
    per_type: dict[str, list[int]] = {}
    for lb in in_scope:
        per_type.setdefault(lb["type"], [0, 0])
        per_type[lb["type"]][0] += lb["detected"]
        per_type[lb["type"]][1] += 1
    claims = sum(v for k, c in d["claims"].items() if k.endswith("/after") for v in c.values())
    verdicts = Counter()
    for f in d["after"]:
        v = review.get(f"{f['repo']}|{f['file']}|{f['line']}|{f['check']}", {}).get("verdict", "unreviewed")
        verdicts[(f["severity"], v)] += 1
    fp = sum(c for (_, v), c in verdicts.items() if v == "fp")
    fp_warn = sum(c for (s, v), c in verdicts.items() if v == "fp" and s != "note")
    return {
        "repos": len(d["repos"]),
        "discrepancies": len(d["labels"]),
        "in_scope": len(in_scope),
        "detected": sum(lb["detected"] for lb in in_scope),
        "recall_by_type": {t: f"{a}/{b}" for t, (a, b) in sorted(per_type.items())},
        "misses": [lb["id"] for lb in in_scope if not lb["detected"]],
        "after_claims": claims,
        "after_fp": fp,
        "after_fp_warning_or_error": fp_warn,
        "after_fp_rate_percent": round(100 * fp / claims, 2),
        "after_real": sum(c for (_, v), c in verdicts.items() if v == "real"),
        "after_by_severity": {f"{s}/{v}": c for (s, v), c in sorted(verdicts.items())},
    }


def main() -> None:
    review = json.loads((HERE / "after_review.json").read_text())
    tp = json.loads((TP / "review.json").read_text())
    out = {
        "dev": split_summary("dev", review),
        "heldout_first_run": split_summary("heldout_first_run", review),
        "heldout": split_summary("heldout", review),
        "third_party": {
            "A_docs_first_run": precision(
                [
                    (r, f)
                    for r in SETS["A"]
                    for f in json.loads((TP / f"{r}_setA_docs_first_run.json").read_text())["findings"]
                ],
                tp,
            ),
            "B_readme": precision(sample("B", "readme"), tp),
            "B_docs_sample": precision(sample("B", "docs"), tp),
            "C_readme": precision(sample("C", "readme"), tp),
            "C_docs_sample": precision(sample("C", "docs"), tp),
        },
        "third_party_claims": {
            s: {
                m: sum(json.loads((TP / f"{r}_set{s}_{m}.json").read_text())["summary"]["claims"] for r in SETS[s])
                for m in ("readme", "docs")
            }
            for s in ("B", "C")
        },
        "third_party_A_docs_first_run_claims": sum(
            json.loads((TP / f"{r}_setA_docs_first_run.json").read_text())["summary"]["claims"] for r in SETS["A"]
        ),
        "third_party_findings": {
            s: {
                m: sum(json.loads((TP / f"{r}_set{s}_{m}.json").read_text())["summary"]["findings"] for r in SETS[s])
                for m in ("readme", "docs")
            }
            for s in ("B", "C")
        },
    }
    (RES / "summary.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
