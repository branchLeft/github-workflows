#!/usr/bin/env bash
# Apply the ruleset payloads in rulesets/<repo>-*.json to the live repos.
# Idempotent: a live ruleset with the same name is updated in place rather than
# duplicated, so this is safe to re-run against a repo already carrying them.
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

repos=(ghost-platform ghost-platform-docs ghost-platform-tenant-template shared-infra)
if [ $# -gt 0 ]; then
  repos=("$@")
fi

for repo in "${repos[@]}"; do
  for payload in rulesets/"${repo}"/*.json; do
    [ -e "$payload" ] || continue
    want_name=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$payload")
    echo "== ${repo}: ${want_name} =="

    id=$(gh api "repos/branchLeft/${repo}/rulesets" \
      --jq ".[] | select(.name == \"${want_name}\") | .id" 2>/dev/null || true)

    if [ -n "$id" ]; then
      gh api --method PUT "repos/branchLeft/${repo}/rulesets/${id}" --input "$payload" \
        --jq '"updated \(.id)"'
    else
      gh api --method POST "repos/branchLeft/${repo}/rulesets" --input "$payload" \
        --jq '"created \(.id)"'
    fi
  done
done
