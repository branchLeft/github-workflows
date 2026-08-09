#!/usr/bin/env bash
# Report drift between the committed ruleset payloads and each repo's live state.
# Private repos on GitHub Free return 403 for the rulesets endpoint — that's
# reported as blocked, not as drift. Exits non-zero on missing or drifted.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

repos=(ghost-platform ghost-platform-docs ghost-platform-tenant-template shared-infra)
if [ $# -gt 0 ]; then
  repos=("$@")
fi

status=0

for repo in "${repos[@]}"; do
  echo "== ${repo} =="
  live=$(gh api "repos/branchLeft/${repo}/rulesets" 2>&1) || true
  if echo "$live" | grep -q "Upgrade to GitHub Pro"; then
    echo "  live: blocked (GitHub Free) — payloads not applied"
    continue
  fi

  for payload in rulesets/"${repo}"/*.json; do
    [ -e "$payload" ] || continue
    want_name=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$payload")
    id=$(printf '%s' "$live" | python3 -c '
import json, sys
want = sys.argv[1]
print(next((r["id"] for r in json.load(sys.stdin) if r["name"] == want), ""))' "$want_name")

    if [ -z "$id" ]; then
      echo "  MISSING: ${want_name}"
      status=1
      continue
    fi

    drift=$(diff <(python3 normalize.py < "$payload") \
                 <(gh api "repos/branchLeft/${repo}/rulesets/${id}" | python3 normalize.py) || true)
    if [ -z "$drift" ]; then
      echo "  ok: ${want_name} (${id})"
    else
      echo "  DRIFT: ${want_name} (${id})"
      printf '%s\n' "$drift" | sed 's/^/    /'
      status=1
    fi
  done
done

exit "$status"
