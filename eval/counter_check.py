"""Compare the static test count with the count pytest reported in the audit's runs.

python eval/counter_check.py --snap <dir>   # writes eval/results/counter_check.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from readme_truth.repo import Repo  # noqa: E402
from readme_truth.testcount import static_pytest  # noqa: E402

# Tests collected by pytest at the "before" commit, from the audit's run logs
# (passed + skipped + deselected; georisk-agent: 229 passed, 2 skipped, 2 deselected).
COLLECTED = {
    "llm-gateway": 424,
    "nvidia-nim-rag-techniques": 138,
    "on-device-llm-optimizer": 67,
    "event-impact-signals": 65,
    "exec-rl": 13,
    "finance-agent-crew": 333,
    "credit-arena": 13,
    "llm-safety-auditor": 494,
    "nano-finbert": 430,
    "georisk-agent": 233,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snap", required=True)
    args = ap.parse_args()
    rows = []
    for repo, real in COLLECTED.items():
        c = static_pytest(Repo(Path(args.snap) / repo / "before"))
        rows.append(
            {
                "repo": repo,
                "pytest": real,
                "static": c.tests,
                "exact": c.exact,
                "lower_bound": c.lower_bound,
                "agrees": c.tests == real if c.exact else c.tests <= real,
            }
        )
    out = {"rows": rows, "equal": sum(r["static"] == r["pytest"] for r in rows), "total": len(rows)}
    (HERE / "results" / "counter_check.json").write_text(json.dumps(out, indent=1) + "\n")
    for r in rows:
        print(f"{r['repo']:28} pytest={r['pytest']:4} static={r['static']:4} exact={r['exact']}")
    print(f"equal: {out['equal']}/{out['total']}")


if __name__ == "__main__":
    main()
