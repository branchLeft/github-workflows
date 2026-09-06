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
# `gh` is stubbed on PATH: no network, no token, no repo required.
#
# Usage: tools/graph-pr-report.test.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPORT="$HERE/graph-pr-report.sh"

PASS=0
FAIL=0

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

BASE_SHA="ca8a191f0000000000000000000000000000000"
HEAD_SHA="f460d3200000000000000000000000000000000"

# A `gh` that reads its canned `pr list` output from $STUB_PR_LIST and
# records every `gh api` invocation, one per line, into $STUB_CALLS.
mkdir -p "$TMP/bin"
cat > "$TMP/bin/gh" <<'STUB'
#!/usr/bin/env bash
case "$1" in
  pr)
    cat "$STUB_PR_LIST"
    ;;
  api)
    printf '%s\n' "$*" >> "$STUB_CALLS"
    echo '{"state":"stubbed"}'
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

run_report() { # pr-list-json sha -> sets OUT, RC, CALLS
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
    printf 'ok   %-34s -> %s\n' "$1" "$3"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %-34s -> expected %s, got %s\n' "$1" "$2" "$3"
    printf '     script output: %s\n' "$OUT"
  fi
}

contains() { case "$2" in *"$1"*) echo yes ;; *) echo no ;; esac; }

ONE_CLEAN_PR=$(jq -n --argjson pr "$(pr_object "")" '[$pr]')
ONE_DIRTY_PR=$(jq -n --argjson pr "$(pr_object "hetzner/index.ts")" '[$pr]')

# --- a clean graph PR posts success, on the PR's own head commit -----------
run_report "$ONE_CLEAN_PR" "$BASE_SHA"
check "clean-pr/exit-code"            "0"   "$RC"
check "clean-pr/one-api-call"         "1"   "$(printf '%s\n' "$CALLS" | grep -c 'statuses/')"
check "clean-pr/state-success"        "yes" "$(contains "state=success" "$CALLS")"

# The regression pin. The status URL must carry the PR's headRefOid; posting
# to the resolved base sha is the shipped defect, and it read as green on a
# commit that had already merged.
check "posts-to-head-not-base"        "yes" "$(contains "statuses/$HEAD_SHA" "$CALLS")"
check "never-posts-to-base"           "no"  "$(contains "statuses/$BASE_SHA" "$CALLS")"

# --- a graph PR carrying a source file posts failure, same commit ----------
run_report "$ONE_DIRTY_PR" "$BASE_SHA"
check "dirty-pr/state-failure"        "yes" "$(contains "state=failure" "$CALLS")"
check "dirty-pr/reason-in-description" "yes" "$(contains "outside graphify-out/" "$CALLS")"
check "dirty-pr/still-posts-to-head"  "yes" "$(contains "statuses/$HEAD_SHA" "$CALLS")"
check "dirty-pr/never-posts-to-base"  "no"  "$(contains "statuses/$BASE_SHA" "$CALLS")"
# The verdict travels in the status, not in the job's exit code: this job is
# a workflow_run job whose own conclusion appears on no pull request.
check "dirty-pr/exit-code"            "0"   "$RC"

# --- nothing to report posts nothing --------------------------------------
run_report "[]" "$BASE_SHA"
check "no-open-pr/exit-code"          "0"   "$RC"
check "no-open-pr/no-api-call"        "0"   "$(printf '%s' "$CALLS" | grep -c 'statuses/')"

run_report "$ONE_CLEAN_PR" "0000000000000000000000000000000000000000"
check "unmatched-sha/no-api-call"     "0"   "$(printf '%s' "$CALLS" | grep -c 'statuses/')"

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
check "no-head-oid/exit-code"         "1"   "$RC"
check "no-head-oid/no-api-call"       "0"   "$(printf '%s' "$CALLS" | grep -c 'statuses/')"

echo
echo "graph-pr-report tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
