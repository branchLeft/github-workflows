#!/usr/bin/env bash
# Unit tests for graph-pr-resolve.sh — the SHA-resolution wiring that sits
# between a `workflow_run` event and graph-pr-check.sh's eligibility logic.
#
# This exists because the eligibility suite (graph-pr-check.test.sh) proved
# nothing about this step: it hand-builds a single PR object and feeds it
# straight to graph-pr-check.sh, never exercising how that object gets
# found in the first place. A first version of the caller workflow matched
# `workflow_run.head_sha` against the PR's `headRefOid` — the wrong field —
# and every one of those 11 cases still passed, because none of them touch
# resolution at all. The `base-commit-resolves` case below is the one that
# would have caught it: it pins that a `workflow_run`-shaped `head_sha`
# (which is always a commit on the DEFAULT branch, never on `graphify` —
# see the header comment in graph-pr-resolve.sh) still resolves the PR.
#
# Usage: tools/graph-pr-resolve.test.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
RESOLVE="$HERE/graph-pr-resolve.sh"

PASS=0
FAIL=0

pr_at() { # baseRefOid headRefOid state -> one PR object
  jq -n --arg base "$1" --arg head "$2" --arg state "$3" '{
    number: 1,
    title: "chore(graphify): update knowledge graph [skip ci]",
    author: {login: "github-actions[bot]"},
    headRefName: "graphify",
    headRefOid: $head,
    baseRefName: "main",
    baseRefOid: $base,
    state: $state,
    isCrossRepository: false,
    files: [{path: "graphify-out/graph.json"}]
  }'
}

# name, pr-list json, sha to resolve, expect FOUND|EMPTY|ERROR
#
# EMPTY and ERROR are deliberately distinct. EMPTY (exit 1) means "no open PR
# matches this sha", an ordinary outcome the caller reports as nothing to do.
# ERROR (exit 2) means "this input is not a pull-request list", which the
# caller must surface rather than silently treat as nothing to do -- otherwise
# a failed `gh pr list` reads as a clean run.
case_check() {
  local name="$1" list="$2" sha="$3" expect="$4"
  local out rc got
  out=$(printf '%s' "$list" | "$RESOLVE" "$sha" 2>&1)
  rc=$?
  case "$rc" in
    0) got="FOUND" ;;
    1) got="EMPTY" ;;
    *) got="ERROR" ;;
  esac

  if [ "$got" = "$expect" ]; then
    PASS=$((PASS + 1))
    printf 'ok   %-32s -> %s\n' "$name" "$got"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %-32s -> expected %s, got %s (%s)\n' "$name" "$expect" "$got" "$out"
  fi
}

# The realistic case: workflow_run.head_sha is the commit that triggered the
# parent run — a commit on main, i.e. the PR's baseRefOid — never its
# headRefOid on the graphify branch. This is the case that would have caught
# the shipped defect (matching against headRefOid resolved nothing, ever).
BASE_SHA="ca8a191f0000000000000000000000000000000"
HEAD_SHA="f460d3200000000000000000000000000000000"
ONE_OPEN_PR=$(jq -n --argjson pr "$(pr_at "$BASE_SHA" "$HEAD_SHA" "OPEN")" '[$pr]')

case_check "base-commit-resolves"      "$ONE_OPEN_PR" "$BASE_SHA" FOUND
case_check "head-commit-does-not-resolve" "$ONE_OPEN_PR" "$HEAD_SHA" EMPTY
case_check "unrelated-sha-does-not-resolve" "$ONE_OPEN_PR" "0000000000000000000000000000000000000000" EMPTY
case_check "empty-pr-list"              "[]" "$BASE_SHA" EMPTY

CLOSED_PR=$(jq -n --argjson pr "$(pr_at "$BASE_SHA" "$HEAD_SHA" "MERGED")" '[$pr]')
case_check "merged-pr-does-not-resolve" "$CLOSED_PR" "$BASE_SHA" EMPTY

# The branch is force-rebuilt, so a stale run's base commit should not match
# a PR that has since moved on to a newer base — only the current base
# matches, an older one that used to be current does not.
STALE_BASE="1111111111111111111111111111111111111111"
case_check "stale-base-does-not-resolve" "$ONE_OPEN_PR" "$STALE_BASE" EMPTY

# Input that is not a PR list must be an ERROR, never an EMPTY. The second
# case is the one that bites: `gh pr list` failing returns well-formed JSON,
# so a bare "is this parseable" check passes it through and the whole run
# reports "superseded or already merged" having examined nothing.
case_check "not-json-is-an-error"        "not json at all" "$BASE_SHA" ERROR
case_check "json-object-is-an-error"     '{"message":"Bad credentials"}' "$BASE_SHA" ERROR
case_check "json-string-is-an-error"     '"hello"' "$BASE_SHA" ERROR

echo
echo "graph-pr-resolve tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
