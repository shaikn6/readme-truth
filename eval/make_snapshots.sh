#!/usr/bin/env bash
# Clone the 22 audited repositories (read-only) and check out two snapshots of each:
#   <dir>/<repo>/before  last commit before the 2026-10-28 fix PRs (2026-10-28 03:00 local time)
#   <dir>/<repo>/after   HEAD at the time of the evaluation (pinned below)
set -euo pipefail
dir=${1:?usage: make_snapshots.sh <dir>}
mkdir -p "$dir/clones"
# repo  after-commit (HEAD when the evaluation ran)
while read -r repo after; do
  [ -d "$dir/clones/$repo" ] || git clone -q --no-checkout "https://github.com/shaikn6/$repo.git" "$dir/clones/$repo"
  before=$(git -C "$dir/clones/$repo" rev-list -1 --before='2026-10-28 03:00' "$after")
  for state in before after; do
    sha=$before; [ "$state" = after ] && sha=$after
    [ -d "$dir/$repo/$state" ] || git -C "$dir/clones/$repo" worktree add -q --detach "$(cd "$dir" && pwd)/$repo/$state" "$sha"
  done
done <<'LIST'
credit-arena 9b5ea73
event-impact-signals 300cc0d
exec-rl 41a270e
fin-lora 6c02b6f
finance-agent-crew e9b090e
finarena 9635d24
fintech-devsecops-pipeline 2fd98a7
georisk-agent b6bd1b0
guard-gap-detector c77a0e7
ledger-service c45009c
lethe a29ec6e
llm-gateway 528c290
llm-safety-auditor 7c77928
mcp-diagram-agent 31168ef
nano-finbert 82182c8
nvidia-nim-rag-techniques 51b591d
on-device-llm-optimizer 88d30d7
orbitwatch 1147b0d
portfolio 823b6b9
shaikn6 fc0342f
sigdet 32977ec
trade-arena 6257992
LIST
