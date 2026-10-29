# Changelog

## 0.1.0 (unreleased)

First version.

- `readme-truth check`: test counts, coverage, versions, paths, commands, links, licence, number provenance
  and self-contradictions in README.md (and other Markdown docs with `--docs`).
- Static test counts for pytest, Go and JS/TS; `--collect`, `--test-count-cmd` and `--test-count-file` for
  exact counts.
- Output as text, JSON, GitHub annotations or SARIF; `--fail-on` exit codes; `--fix` for numeric claims;
  `<!-- readme-truth: ignore -->` comments.
- Composite GitHub Action (`action.yml`) and an example workflow.
- Evaluation against a hand audit of 22 repositories, with a dev/held-out split and three third-party sets
  (see `eval/README.md`).
