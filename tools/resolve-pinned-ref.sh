#!/usr/bin/env bash
# Resolves the revision of branchLeft/github-workflows that the caller pinned a
# reusable workflow to, and writes it to GITHUB_OUTPUT as `ref`. Why, and why the
# body is inlined in the workflows: tools/resolve-pinned-ref.md.
set -euo pipefail
workflow_re='^[A-Za-z0-9._-]+\.yml$'
[[ "${CALLED_WORKFLOW:-}" =~ $workflow_re ]] || { echo "::error::CALLED_WORKFLOW must name a workflow file."; exit 1; }
# `|| true` so that no match reaches the count below with its message, rather
# than aborting under `set -e` with a bare exit 1.
workflow_pat="uses:[[:space:]]*['\"]?branchLeft/github-workflows/\.github/workflows/${CALLED_WORKFLOW//./\\.}"
ref_pat="@[^[:space:]\"'#]+"
refs=$(grep -rhoE "${workflow_pat}${ref_pat}" .github/workflows/ 2>/dev/null | sed 's/.*@//' | sort -u || true)
n=$(printf '%s' "$refs" | grep -c . || true)
if [ "$n" -ne 1 ]; then
  echo "::error::expected exactly one pinned ${CALLED_WORKFLOW} revision under .github/workflows/, found ${n}: ${refs}. Refusing to run unpinned tooling."
  exit 1
fi
printf 'ref=%s\n' "$refs" >> "${GITHUB_OUTPUT:-/dev/stdout}"
echo "Running branchLeft/github-workflows@$refs"
