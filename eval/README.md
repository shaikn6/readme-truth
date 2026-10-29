# Evaluation

readme-truth was built against real drift, not invented examples. In October 2026 the 22 public
repositories of the author were audited by hand: a fresh clone of each, its quickstart followed, its tests
and headline experiment run, and every README claim compared with what happened. The audit listed 76
discrepancies. The fixes then landed as pull requests on 2026-10-28. Each repository therefore has a labelled
**before** state (the last commit before the fix PRs) and an **after** state (HEAD).

## Labels

[`labels.py`](labels.py) lists all 76 audit discrepancies. Each one is either:

- **in scope (14)**: detectable from the repository at the before commit without running anything
  (a test count against the test files, a version against `go.mod`, a path against the tree, a command
  against the Makefile or dependency files, a table against committed data); or
- **out of scope (62)**, with a reason:

| Why out of scope | Count |
|---|---|
| Needs the code to run (output, behaviour, dependency resolution) | 16 |
| Wording, semantics or research interpretation | 10 |
| Lives outside the repo (other repos, web pages, live data, network) | 8 |
| Missing instruction, incomplete listing, illustrative code, markdown lint, image provenance | 7 |
| Machine environment or installed extras | 6 |
| Timing or throughput | 6 |
| Drift only visible after re-running an experiment | 5 |
| Number in prose (provenance checks tables only) | 2 |
| Coverage claim with no committed coverage report | 2 |

The scope rule was fixed before the checker ran. It describes the design, not what the checker
happened to catch.

## Dev and held-out split

The repositories were split once, before any code was written: sorted by the SHA-256 of their name, the
first 11 are **dev** and the other 11 are **held out** (`split()` in [`run_eval.py`](run_eval.py)).
The checker was built and tuned on dev only. The held-out split was run once, at commit `24a3915`, and
those first-run numbers are reported as they came out. The held-out false positives were then fixed, and the
post-fix numbers are reported separately. They are no longer unbiased.

One caveat: the audit text for all 22 repositories was read while writing the labels, so the author knew
what kinds of drift the held-out repositories contained.

## Results

Recall counts an in-scope discrepancy as caught when a finding of the matching type lands on its lines at
the before commit. Any severity counts. False positives are every finding at the after commits that was
read by hand and judged wrong ([`after_review.json`](after_review.json) has a verdict and a note for each).
Findings at the after commits that point at real, still-unfixed drift are counted as real.

| | Dev (tuned on) | Held-out, first run | Held-out, after fixes |
|---|---|---|---|
| In-scope discrepancies caught | 10/10 | 2/4 | 2/4 |
| test-count | 5/5 | 2/2 | 2/2 |
| version | 3/3 | - | - |
| path | 1/1 | 0/1 | 0/1 |
| command | 1/1 | - | - |
| number-provenance | - | 0/1 | 0/1 |
| Claims checked at the after commits | 464 | 981 | 970 |
| False positives at the after commits | 1 (0.22%) | 30 (3.06%) | 12 (1.24%) |
| ... of which warning or error | 0 | 8 | 0 |
| Real drift still present at the after commits | 7 | 9 | 9 |

The real drift at the after commits is drift the fix PRs missed or that arrived later. Examples: a
SECURITY.md template telling readers to `pip install -r requirements.txt` in two repos that have no such file,
a README test count that two later PRs made stale (`495` claimed, at least `526` collected), "Go 1.26+"
against `go 1.26.4` in `go.mod`, and a CONTRIBUTING guide promising an 80% coverage gate where CI enforces 95%.

### Misses

- **gr-2** (number provenance): a reliability table whose bands and rates do not match `calibration.json`.
  The repository commits about 506,000 numbers, so whole-number percentages such as `48%` match *something*
  by chance. Provenance only has power in repositories with little committed data.
- **ow-1** (path): `bin/validate   # against a SOCRATES report in data/raw`. The path is in a shell comment,
  and `data/` is gitignored, so `data/raw` looks like prose rather than a repo path.

### What the held-out run changed

The first held-out run produced 8 wrong warnings or errors. The checker was fixed, not the data:

- "500 while offline tests passed" read as a test count: only a fixed list of suite words may sit between the
  number and "tests" now.
- "231 offline tests" against 233 collected: a claim equal to the total minus one declared pytest marker
  (`network`) is accepted.
- A sentence about another project's licence read as this repo's licence: licence text now needs "this
  project/code" phrasing or a short line in a License section.
- A dotted code reference (`src/scorer.classify_attack`) read as a path.
- Notes for paths the README itself says are downloaded, gitignored or kept out of git are dropped. The
  sentence around the path is read, not just the line.

## Static test count

The default test count parses test files without importing them. Against the counts pytest reported in the
audit runs ([`counter_check.py`](counter_check.py)) it is equal in 9 of 10 repositories. The tenth builds
parameter lists at run time, so the counter reports a lower bound (488 against 494) and only flags claims
below it.

## Third-party repositories

Shallow clones of popular Python and Go projects, run once per set. Every flagged item in a set, or a seeded
random sample of 20 when there were more, was read by hand ([`third_party/review.json`](third_party/review.json)).

| Set | Repositories | Mode | Claims | Findings | Read | Real |
|---|---|---|---|---|---|---|
| A | requests, flask, httpx, cobra, gin | `--docs` | 184 | 21 | 21 | 6 |
| B | click, starlette, pydantic, viper, urfave/cli | README | 17 | 2 | 2 | 0 |
| B | same | `--docs` | 755 | 135 | 20 | 0 |
| C | black, rich, typer, chi, logrus | README | 36 | 14 | 14 | 3 |
| C | same | `--docs` | 897 | 400 | 20 | 1 |

Set A was used to find false positives (its 13 errors were all wrong: commands in `docs/` resolved
against `docs/` instead of the repo root, Go feature notes read as requirements, a failing-run transcript
read as a test count, mkdocs extensionless links). Set B was fresh when run and exposed the mkdocs problems
in `--docs` mode: `{#id}` anchors, indented fences, generated API anchors, release notes. Set C was fresh,
run once at commit `628a7ee`, and nothing was changed after it.

**Precision estimate (sets B and C, fresh):** in the default README mode, 3 of 3 warnings and errors were
real (rich says "Python 3.8 or later" while its `pyproject.toml` requires `>=3.9.0`; two typer README links
point at `tutorial/install.md`, which lives under `docs/`). All 13 notes were wrong: they were files that a
tutorial asks the reader to create (`main.py`, `hello.py`). In `--docs` mode, 1 of 40 sampled findings was
real. Documentation sites full of tutorials and generated pages are not a good target yet.

## Reproduce

```bash
pip install -e .
eval/make_snapshots.sh ../snap                       # clones the 22 repos, before/after worktrees
python eval/run_eval.py --snap ../snap --split dev
python eval/run_eval.py --snap ../snap --split heldout
python eval/run_eval.py --rescore eval/results/heldout_first_run.json
python eval/counter_check.py --snap ../snap
python eval/summarize.py                             # writes results/summary.json
```
