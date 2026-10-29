# Security policy

## Reporting a vulnerability

Email **nagizaazs@gmail.com** with a description, the affected version and steps to reproduce. Please do not
open a public issue for a security problem. You will get a reply within a week.

## What the tool runs

By default readme-truth only reads files: it parses Markdown, configuration and test files and never imports
or executes code from the repository it checks. Three options change that, and are off unless you pass them:

- `--collect` runs `pytest --collect-only` or `go test -list`, which imports the repository's test code.
- `--test-count-cmd` runs the shell command you give it.
- `--check-links` sends HTTP requests to the URLs in the README.

In the GitHub Action, inputs reach the shell through environment variables, not string interpolation.
Do not pass untrusted text (for example a pull request title) as `args`.
