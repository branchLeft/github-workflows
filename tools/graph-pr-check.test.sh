#!/usr/bin/env bash
# Unit tests for graph-pr-check.sh, run as fixture PR JSON through the real
# script. The case that matters most is `outside-graphify-out`: it is the one
# thing standing between the `--admin` merge path and an unreviewed source
# change riding in on a graph PR's shape.
#
# Usage: tools/graph-pr-check.test.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
CHECK="$HERE/graph-pr-check.sh"

PASS=0
FAIL=0

base_pr() {
  jq -n '{
    number: 1,
    title: "chore(graphify): update knowledge graph [skip ci]",
    author: {login: "github-actions[bot]"},
    headRefName: "graphify",
    headRefOid: "deadbeef",
    baseRefName: "main",
    state: "OPEN",
    isCrossRepository: false,
    files: [{path: "graphify-out/graph.json"}, {path: "graphify-out/cache/ast/x.json"}]
  }'
}

# name, jq filter applied to base_pr (or "." for unchanged), expect PASS|FAIL
case_check() {
  local name="$1" filter="$2" expect="$3"
  local json out rc
  json=$(base_pr | jq "$filter")
  out=$(printf '%s' "$json" | "$CHECK" 2>&1)
  rc=$?
  local got="FAIL"
  [ "$rc" = "0" ] && got="PASS"

  if [ "$got" = "$expect" ]; then
    PASS=$((PASS + 1))
    printf 'ok   %-32s -> %s\n' "$name" "$out"
  else
    FAIL=$((FAIL + 1))
    printf 'FAIL %-32s -> expected %s, got %s (%s)\n' "$name" "$expect" "$got" "$out"
  fi
}

case_check "clean-graph-pr"            "."                                                          PASS
case_check "wrong-author"              '.author.login = "some-contributor"'                          FAIL
case_check "wrong-head-branch"         '.headRefName = "feature/x"'                                  FAIL
case_check "wrong-title"               '.title = "feat: add a new page"'                              FAIL
case_check "wrong-base-branch"         '.baseRefName = "staging"'                                    FAIL
case_check "fork-pr"                   '.isCrossRepository = true'                                   FAIL
case_check "closed-pr"                 '.state = "CLOSED"'                                            FAIL
case_check "no-files"                  '.files = []'                                                  FAIL
case_check "truncated-files"           '.files = ([range(100)] | map({path: "graphify-out/\(.)"}))'  FAIL
case_check "path-traversal"            '.files += [{path: "graphify-out/../secrets.env"}]'            FAIL
# The case that matters: looks like a graph PR in every other respect, but
# the diff also touches a real source file.
case_check "outside-graphify-out"      '.files += [{path: "tools/docs-lint.sh"}]'                     FAIL

echo
echo "graph-pr-check tests: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
