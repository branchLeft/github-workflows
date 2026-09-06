#!/usr/bin/env bash
# Unit tests for graph-pr-report.sh — the step that turns a resolved graph PR
# into a commit status.
#
# Neither sibling suite covered this. graph-pr-check.test.sh feeds a
# hand-built PR object straight to the eligibility logic, and
# graph-pr-resolve.test.sh stops once the PR is found; the posting itself
# lived inline in the workflow YAML, where nothing could execute it. That is
# how it shipped posting to `workflow_run.head_sha` — the PR's BASE commit,
# on the default branch — which put green statuses on merge commits and left
# every graph PR with zero statuses. `posts-to-head-not-base` below is the
# case that would have caught it.
#
# A commit status is a triple — commit, context and state — and all three
# have to be right for it to be seen and believed. The suite asserts each
# one separately: a status on the correct commit under the wrong context
# name is exactly as invisible to a branch rule as one on the wrong commit.
#
# `gh` is stubbed on PATH and records its arguments ONE PER LINE, so the
# assertions match whole argv entries rather than substrings of a joined
# string. A single-argument call that happened to contain the right text
# would pass a substring check and 404 in production.
#
# Usage: tools/graph-pr-report.test.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPORT="$HERE/graph-pr-report.sh"

PASS=0
FAIL=0

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

# Full 40-hex shas: the code does not validate length, but a fixture that
# could not be a real commit is a fixture that never exercises a real URL.
BASE_SHA="ca8a191f4d2b7e0c9a3f6518d0e47b21c6f8a903"
HEAD_SHA="f460d320b71e8c4a5d92f03e6b1a7c8409de25f1"

# A `gh` that serves `pr list` from $STUB_PR_LIST (exiting $STUB_PR_LIST_RC)
# and appends every `gh api` argument, one per line, to $STUB_CALLS.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  pr)
    cat "$STUB_PR_LIST"
    exit "${STUB_PR_LIST_RC:-0}"
    ;;
  api)
    { printf 'CALL\n'; printf '%s\n' "$@"; } >> "$STUB_CALLS"
    echo '{"state":"stubbed"}'
    exit "${STUB_API_RC:-0}"
    ;;
  *)
    echo "stub gh: unexpected subcommand $1" >&2
    exit 64
    ;;
esac
STUB
chmod +x "$TMP/bin/gh"
export PATH="$TMP/bin:$PATH"

pr_object() { # extra-file-path ("" for none) -> one PR object
  local extra="$1"
  jq -n --arg base "$BASE_SHA" --arg head "$HEAD_SHA" --arg extra "$extra" '{
    number: 42,
    title: "chore(graphify): update knowledge graph [skip ci]",
    author: {login: "github-actions[bot]"},
    headRefName: "graphify",
    headRefOid: $head,
    baseRefName: "main",
    baseRefOid: $base,
    state: "OPEN",
    isCrossRepository: false,
    files: ([{path: "graphify-out/graph.json"}]
            + (if $extra == "" then [] else [{path: $extra}] end))
  }'
}

run_report() { # pr-list-body sha -> sets OUT, RC, CALLS
  local list="$1" sha="$2"
  export STUB_PR_LIST="$TMP/pr-list.json"
  export STUB_CALLS="$TMP/calls.txt"
  printf '%s' "$list" > "$STUB_PR_LIST"
  : > "$STUB_CALLS"
  OUT=$(REPO="branchLeft/example" SHA="$sha" CONTEXT="graph-pr-check" "$REPORT" 2>&1)
  RC=$?
  CALLS=$(cat "$STUB_CALLS")
}

check() { # name expected actual
  if [ "$2" = "$3" ]; then
    PASS=$((PASS + 1))
    printf 'ok   %-36s -> %s\n' "$1" "$3"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %-36s -> expected %s, got %s\n' "$1" "$2" "$3"
    printf '     script output: %s\n' "$OUT"
    printf '     recorded argv: %s\n' "$(printf '%s' "$CALLS" | tr '\n' ' ')"
  fi
}

# Whole-argv match, not substring: the recorded file has one argument per line.
has_arg() { if printf '%s\n' "$CALLS" | grep -qxF -- "$1"; then echo yes; else echo no; fi; }
api_calls() { printf '%s\n' "$CALLS" | grep -cxF 'CALL'; }

ONE_CLEAN_PR=$(jq -n --argjson pr "$(pr_object "")" '[$pr]')
ONE_DIRTY_PR=$(jq -n --argjson pr "$(pr_object "hetzner/index.ts")" '[$pr]')

STATUS_URL="repos/branchLeft/example/statuses/$HEAD_SHA"
BASE_URL="repos/branchLeft/example/statuses/$BASE_SHA"

# --- a clean graph PR posts success, on the PR's own head commit -----------
run_report "$ONE_CLEAN_PR" "$BASE_SHA"
check "clean-pr/exit-code"              "0"   "$RC"
check "clean-pr/one-api-call"           "1"   "$(api_calls)"
check "clean-pr/state-success"          "yes" "$(has_arg 'state=success')"
check "clean-pr/context-name"           "yes" "$(has_arg 'context=graph-pr-check')"

# The regression pin. The status URL must be the PR's headRefOid; posting to
# the resolved base sha is the shipped defect, and it read as green on a
# commit that had already merged.
check "posts-to-head-not-base"          "yes" "$(has_arg "$STATUS_URL")"
check "never-posts-to-base"             "no"  "$(has_arg "$BASE_URL")"

# --- a graph PR carrying a source file posts failure, same commit ----------
run_report "$ONE_DIRTY_PR" "$BASE_SHA"
check "dirty-pr/state-failure"          "yes" "$(has_arg 'state=failure')"
check "dirty-pr/context-name"           "yes" "$(has_arg 'context=graph-pr-check')"
check "dirty-pr/reason-in-description"  "yes" "$(has_arg 'description=FAIL: changes 1 path(s) outside graphify-out/: hetzner/index.ts')"
check "dirty-pr/still-posts-to-head"    "yes" "$(has_arg "$STATUS_URL")"
check "dirty-pr/never-posts-to-base"    "no"  "$(has_arg "$BASE_URL")"
# The verdict travels in the status, not in the job's exit code: this job is
# a workflow_run job whose own conclusion appears on no pull request.
check "dirty-pr/exit-code"              "0"   "$RC"

# A wrong context name is as invisible as a wrong commit — pin it explicitly.
check "dirty-pr/no-stray-context"       "no"  "$(has_arg 'context=graphify')"

# --- nothing to report posts nothing --------------------------------------
run_report "[]" "$BASE_SHA"
check "no-open-pr/exit-code"            "0"   "$RC"
check "no-open-pr/no-api-call"          "0"   "$(api_calls)"

run_report "$ONE_CLEAN_PR" "0000000000000000000000000000000000000000"
check "unmatched-sha/exit-code"         "0"   "$RC"
check "unmatched-sha/no-api-call"       "0"   "$(api_calls)"

# --- a resolver that could not ANSWER is an error, not "nothing to report" -
# The distinction matters: silently reporting "superseded or already merged"
# on a resolver failure leaves the job green having checked nothing, which is
# the same observable state as the defect this file fixes.
run_report 'not json at all' "$BASE_SHA"
check "unparseable-list/exit-code"      "1"   "$RC"
check "unparseable-list/no-api-call"    "0"   "$(api_calls)"

run_report '{"message":"Bad credentials"}' "$BASE_SHA"
check "api-error-object/exit-code"      "1"   "$RC"
check "api-error-object/no-api-call"    "0"   "$(api_calls)"

# --- gh pr list itself failing is an error --------------------------------
STUB_PR_LIST_RC=1 run_report "" "$BASE_SHA"
check "pr-list-fails/exit-code"         "1"   "$RC"
check "pr-list-fails/no-api-call"       "0"   "$(api_calls)"
unset STUB_PR_LIST_RC

# --- a PR shape with no head commit is a job failure, not a silent skip ----
NO_HEAD_PR=$(jq -n --arg base "$BASE_SHA" '[{
  number: 42,
  title: "chore(graphify): update knowledge graph [skip ci]",
  author: {login: "github-actions[bot]"},
  headRefName: "graphify",
  headRefOid: "",
  baseRefName: "main",
  baseRefOid: $base,
  state: "OPEN",
  isCrossRepository: false,
  files: [{path: "graphify-out/graph.json"}]
}]')
run_report "$NO_HEAD_PR" "$BASE_SHA"
check "no-head-oid/exit-code"           "1"   "$RC"
check "no-head-oid/no-api-call"         "0"   "$(api_calls)"

# --- a failed POST must not be reported as a posted status ----------------
export STUB_API_RC=1
run_report "$ONE_CLEAN_PR" "$BASE_SHA"
check "post-fails/exit-code"            "1"   "$RC"
check "post-fails/attempted-once"       "1"   "$(api_calls)"
unset STUB_API_RC

echo
echo "graph-pr-report tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
