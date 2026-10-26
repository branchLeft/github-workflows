#!/usr/bin/env bash
# Tests for resolve-pinned-ref.sh and for the inline copies of it in the
# reusable workflows. Usage: tools/resolve-pinned-ref.test.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
RESOLVER="${RESOLVER:-$HERE/resolve-pinned-ref.sh}"
WORK=$(mktemp -d) || exit 2
trap 'rm -rf "$WORK"' EXIT

PASS=0
FAIL=0

ok() { PASS=$((PASS + 1)); printf 'ok   %s\n' "$1"; }
bad() { FAIL=$((FAIL + 1)); printf 'FAIL %s\n' "$1"; }

# name, workflow file called, expected exit (0|1), expected ref, then the
# caller's workflow files as NAME=CONTENT pairs.
run_case() {
  local name="$1" called="$2" want_exit="$3" want_ref="$4"
  shift 4
  local dir="$WORK/$name"
  mkdir -p "$dir/.github/workflows"
  local pair
  for pair in "$@"; do
    printf '%s\n' "${pair#*=}" > "$dir/.github/workflows/${pair%%=*}"
  done
  : > "$dir/out"
  local got_exit=0
  (cd "$dir" && CALLED_WORKFLOW="$called" GITHUB_OUTPUT="$dir/out" "$RESOLVER" >"$dir/log" 2>&1) || got_exit=$?
  local got_ref
  got_ref=$(sed -n 's/^ref=//p' "$dir/out")
  if [ "$got_exit" = "$want_exit" ] && [ "$got_ref" = "$want_ref" ]; then
    ok "$name"
  else
    bad "$name (exit $got_exit want $want_exit, ref '$got_ref' want '$want_ref')"
  fi
}

USES='    uses: branchLeft/github-workflows/.github/workflows/docs-lint.yml@'

run_case one-pin docs-lint.yml 0 v1.0.8 "ci.yml=${USES}v1.0.8"
run_case same-pin-twice docs-lint.yml 0 v1.0.8 "a.yml=${USES}v1.0.8" "b.yml=${USES}v1.0.8"
run_case sha-pin docs-lint.yml 0 eefde0410000000000000000000000000000000a "ci.yml=${USES}eefde0410000000000000000000000000000000a # v1.0.8"
run_case quoted-pin docs-lint.yml 0 v1.0.8 "ci.yml=    uses: \"branchLeft/github-workflows/.github/workflows/docs-lint.yml@v1.0.8\""
run_case other-workflow-ignored docs-lint.yml 0 v1.0.8 "ci.yml=${USES}v1.0.8
    uses: branchLeft/github-workflows/.github/workflows/opv-lint.yml@v1.0.2"
run_case comment-ignored docs-lint.yml 0 v1.0.8 "ci.yml=# was docs-lint.yml@v1.0.6
${USES}v1.0.8"
run_case none-fails-closed docs-lint.yml 1 "" "ci.yml=name: nothing"
run_case two-pins-fail-closed docs-lint.yml 1 "" "a.yml=${USES}v1.0.8" "b.yml=${USES}v1.0.6"
run_case dot-is-literal docs-lint.yml 1 "" "ci.yml=    uses: branchLeft/github-workflows/.github/workflows/docs-lintXyml@v1.0.8"
run_case bad-name-refused ../x 1 "" "ci.yml=${USES}v1.0.8"
run_case no-workflows-dir docs-lint.yml 1 ""

# The inline copy in each reusable workflow must carry every line of the
# resolver, so the code the tests exercise is the code that runs.
body=$(grep -vE '^(#|$)' "$RESOLVER")
for wf in docs-lint opv-lint; do
  file="$REPO/.github/workflows/$wf.yml"
  missing=0
  while IFS= read -r line; do
    grep -qxF -- "$(printf '%s' "$line")" <(sed 's/^ \{10\}//' "$file") || { missing=1; printf '  missing from %s: %s\n' "$wf" "$line"; }
  done <<< "$body"
  [ "$missing" = 0 ] && ok "$wf inlines the resolver" || bad "$wf inlines the resolver"
  if grep -qE '^[^#]*\$\{\{[^}]*job_workflow_sha' "$file"; then bad "$wf still reads job_workflow_sha"; else ok "$wf does not read job_workflow_sha"; fi
  if grep -qF 'steps.pin.outputs.ref' "$file"; then ok "$wf checks out the resolved ref"; else bad "$wf checks out the resolved ref"; fi
done

printf '\n%d passed, %d failed\n' "$PASS" "$FAIL"
[ "$FAIL" = 0 ]
