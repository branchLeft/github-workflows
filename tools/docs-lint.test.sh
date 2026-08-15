#!/usr/bin/env bash
# Unit tests for docs-lint.sh's content rules. Fixture-driven: each case is a
# snippet of prose fed to the script through a scratch git repo (docs-lint.sh
# requires one — it resolves its scan root with `git rev-parse
# --show-toplevel`) and the exit code / DL009 presence is asserted.
#
# Usage: tools/docs-lint.test.sh

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
LINT="$HERE/docs-lint.sh"

WORK=$(mktemp -d) || exit 2
trap 'rm -rf "$WORK"' EXIT
cd "$WORK" || exit 2
git init -q
git config user.email test@example.com
git config user.name test

PASS=0
FAIL=0

# case name, fixture content, expect-DL009 (yes|no), expect-exit (0|1)
case_() {
  local name="$1" content="$2" expect_dl009="$3" expect_exit="$4"
  local file="fixture.md"
  printf '%s\n' "$content" > "$file"
  local out rc
  out=$("$LINT" --explain --mode enforce "$file" 2>&1)
  rc=$?
  local has_dl009=no
  printf '%s' "$out" | grep -q '\[DL009\]' && has_dl009=yes

  local ok=1
  if [ "$has_dl009" != "$expect_dl009" ]; then
    ok=0
    echo "FAIL: $name -- expected DL009=$expect_dl009, got $has_dl009"
    echo "$out" | sed 's/^/    /'
  fi
  if [ "$rc" != "$expect_exit" ]; then
    ok=0
    echo "FAIL: $name -- expected exit=$expect_exit, got $rc"
  fi
  if [ "$ok" = "1" ]; then
    PASS=$((PASS + 1))
    echo "ok   $name"
  else
    FAIL=$((FAIL + 1))
  fi
  rm -f "$file"
}

# --- Q<n> is now caught, like S<n> and B<n> ---------------------------------
case_ "Q<n> item id is flagged" \
  "See Q52 for the full write-up." \
  yes 1

case_ "S<n> item id is still flagged" \
  "Tracked as S10 in the backlog." \
  yes 1

case_ "B<n> item id is still flagged" \
  "Filed as B22 against the platform board." \
  yes 1

case_ "low Q<n> id (Q5) is still caught, only Q1-Q4 are exempt" \
  "Tracked as Q5 in the backlog." \
  yes 1

# --- False-positive guards --------------------------------------------------
case_ "calendar quarters Q1-Q4 are not flagged" \
  "Revenue improved in Q3 and the launch target is Q4." \
  no 0

case_ "S3 the AWS service is not flagged" \
  "Static assets are served from S3." \
  no 0

case_ "a run id longer than 3 digits does not false-match on a prefix" \
  "Build Q10023 finished clean." \
  no 0

case_ "DEP-<n> is out of scope for DL009 by design" \
  "The pin relaxation is tracked as DEP-12." \
  no 0

echo
echo "docs-lint.test.sh: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
