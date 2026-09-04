#!/usr/bin/env bash
# Decides whether an open pull request is a graph-rebuild PR of the shape
# the org's `--admin` merge authorisation covers: opened by the CI bot from
# the force-rebuilt `graphify` branch, titled `chore(graphify): ...`, with a
# diff confined to `graphify-out/`. That last check is the substantive one —
# it is what stops a graph PR smuggling a source change through a merge path
# that otherwise skips review.
#
# This mirrors (does not call) the eligibility logic the workspace-root merge
# hook already applies before it exercises the `--admin` merge; that hook
# lives in a different, private repo and is not a dependency of this one.
#
# Usage:
#   graph-pr-check.sh < pr.json
#   graph-pr-check.sh pr.json
#
# Input is one JSON object shaped like `gh pr view --json
# number,title,author,headRefName,headRefOid,baseRefName,state,isCrossRepository,files`.
#
# Prints one line (PASS or FAIL: <reason>) and exits 0 on PASS, 1 on FAIL.

set -uo pipefail

GRAPH_BRANCH="graphify"
GRAPH_DIR="graphify-out/"
TITLE_PREFIX="chore(graphify)"
DEFAULT_BRANCHES="main master"
BOT_LOGINS="github-actions github-actions[bot] app/github-actions"
# `gh pr view --json files` returns a bounded page; a PR with at least this
# many files may have paths we never saw, so the diff cannot be verified.
MAX_FILES=100

INPUT="${1:--}"
if [ "$INPUT" = "-" ]; then
  JSON=$(cat)
else
  JSON=$(cat "$INPUT")
fi

fail() { printf 'FAIL: %s\n' "$1"; exit 1; }

echo "$JSON" | jq -e . >/dev/null 2>&1 || fail "input is not valid JSON"

state=$(echo "$JSON" | jq -r '.state // empty')
[ "$state" = "OPEN" ] || fail "state is '${state}', not OPEN"

cross=$(echo "$JSON" | jq -r '.isCrossRepository // false')
[ "$cross" = "false" ] || fail "PR comes from a fork"

author=$(echo "$JSON" | jq -r '.author.login // empty')
match=0
for bot in $BOT_LOGINS; do
  [ "$author" = "$bot" ] && match=1
done
[ "$match" = "1" ] || fail "author '${author}' is not the CI bot"

head_ref=$(echo "$JSON" | jq -r '.headRefName // empty')
[ "$head_ref" = "$GRAPH_BRANCH" ] || fail "head branch is '${head_ref}', not '${GRAPH_BRANCH}'"

base_ref=$(echo "$JSON" | jq -r '.baseRefName // empty')
base_ok=0
for b in $DEFAULT_BRANCHES; do
  [ "$base_ref" = "$b" ] && base_ok=1
done
[ "$base_ok" = "1" ] || fail "base branch is '${base_ref}', not a default branch"

title=$(echo "$JSON" | jq -r '.title // empty')
case "$title" in
  "$TITLE_PREFIX"*) ;;
  *) fail "title does not start with '${TITLE_PREFIX}'" ;;
esac

head_sha=$(echo "$JSON" | jq -r '.headRefOid // empty')
[ -n "$head_sha" ] || fail "no head commit reported, so the check cannot be pinned to it"

file_count=$(echo "$JSON" | jq '.files | length')
[ "$file_count" -gt 0 ] || fail "no files reported"
[ "$file_count" -lt "$MAX_FILES" ] || fail "${file_count} files reported — the list may be truncated, so the diff is unverified"

# A `..` segment resolves outside the directory whatever the prefix says.
traversal=$(echo "$JSON" | jq -r --arg dir "$GRAPH_DIR" \
  '[.files[].path | select(split("/") | index(".."))] | .[0] // empty')
if [ -n "$traversal" ]; then
  fail "path escapes its directory: ${traversal}"
fi

outside=$(echo "$JSON" | jq -r --arg dir "$GRAPH_DIR" \
  '[.files[].path | select(startswith($dir) | not)]')
outside_count=$(echo "$outside" | jq 'length')
if [ "$outside_count" -gt 0 ]; then
  first=$(echo "$outside" | jq -r '.[0]')
  fail "changes ${outside_count} path(s) outside ${GRAPH_DIR}: ${first}"
fi

echo "PASS"
exit 0
