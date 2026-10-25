#!/usr/bin/env bash
# Resolves the revision of branchLeft/github-workflows that the calling repo
# pinned a reusable workflow to, and writes it to GITHUB_OUTPUT as `ref`.
# Run from the caller's checkout root with CALLED_WORKFLOW naming the file.
#
# Nothing the runner provides identifies the called revision: the
# job_workflow_sha context is empty inside a reusable workflow, and an empty
# `ref:` makes actions/checkout fall back to the default branch, so a caller
# pinned to a tag would silently run main. The caller's own workflow files do
# name it. Exactly one distinct pin is required: two callers naming different
# revisions is drift, and guessing which governs reintroduces the silence.
#
# The reusable workflows carry this body inline, because the pinned revision
# cannot be checked out before it is known; resolve-pinned-ref.test.sh asserts
# every line below appears in each workflow that inlines it.
set -euo pipefail
workflow_re='^[A-Za-z0-9._-]+\.yml$'
[[ "${CALLED_WORKFLOW:-}" =~ $workflow_re ]] || { echo "::error::CALLED_WORKFLOW must name a workflow file."; exit 1; }
# `|| true` so that no match reaches the count below with its message, rather
# than aborting under `set -e` with a bare exit 1.
refs=$(grep -rhoE "uses:[[:space:]]*['\"]?branchLeft/github-workflows/\.github/workflows/${CALLED_WORKFLOW//./\\.}@[^[:space:]\"'#]+" .github/workflows/ 2>/dev/null | sed 's/.*@//' | sort -u || true)
n=$(printf '%s' "$refs" | grep -c . || true)
if [ "$n" -ne 1 ]; then
  echo "::error::expected exactly one pinned ${CALLED_WORKFLOW} revision under .github/workflows/, found ${n}: ${refs}. Refusing to run unpinned tooling."
  exit 1
fi
printf 'ref=%s\n' "$refs" >> "${GITHUB_OUTPUT:-/dev/stdout}"
echo "Running branchLeft/github-workflows@$refs"
