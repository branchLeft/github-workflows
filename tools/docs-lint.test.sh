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

# The real DL009 except pattern, read out of the script itself (not
# retyped here) so a future edit to the pattern can't silently desync from
# what assert_blank below actually exercises.
DL009_EXCEPT_LIVE=$(
  cd "$WORK" || exit 2
  set --
  DOCS_LINT_SOURCE_ONLY=1 source "$LINT" >/dev/null 2>&1
  printf '%s' "$DL009_EXCEPT"
)

# case name, fixture content, rule id, expect-rule-flagged (yes|no), expect-exit (0|1)
# The fixture extension is chosen by rule: DL001/DL008 only scan comment
# lines (code_scannable), the rest scan markdown prose.
case_rule() {
  local name="$1" content="$2" rule="$3" expect_flag="$4" expect_exit="$5"
  local file="fixture.md"
  case "$rule" in
    DL001|DL008) file="fixture.sh" ;;
  esac
  printf '%s\n' "$content" > "$file"
  local out rc
  # Force docs-lint.sh's plain-text report format regardless of the ambient
  # environment: under GITHUB_ACTIONS=true it emits `::error ...title=DL009::`
  # workflow-command annotations instead, which this harness doesn't parse.
  out=$(GITHUB_ACTIONS=false "$LINT" --explain --mode enforce "$file" 2>&1)
  rc=$?
  local has_flag=no
  printf '%s' "$out" | grep -q "\[$rule\]" && has_flag=yes

  local ok=1
  if [ "$has_flag" != "$expect_flag" ]; then
    ok=0
    echo "FAIL: $name -- expected $rule=$expect_flag, got $has_flag"
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

# DL009-only convenience wrapper, kept so the existing case names below read
# the same as before this file grew rule-generic.
case_() {
  local name="$1" content="$2" expect_dl009="$3" expect_exit="$4"
  case_rule "$name" "$content" DL009 "$expect_dl009" "$expect_exit"
}

# Whitebox: assert on blank_spans' own output, not just a rule's final
# verdict. This matters because the final verdict alone cannot distinguish a
# genuinely span-aware exclusion from a text-search removal that happens to
# self-heal for today's except patterns (every one of which is a literal
# prefix of the id family it collides with) -- it would only diverge on a
# future except pattern that isn't a prefix, by which point a verdict-only
# test suite would already be lying green. Sourcing with
# DOCS_LINT_SOURCE_ONLY=1 runs the real docs-lint.sh up through blank_spans'
# definition and returns before it touches any file, so this calls the
# actual production function, not a reimplementation that could drift from it.
assert_blank() {
  local name="$1" text="$2" except="$3" expected="$4"
  local got
  got=$(
    cd "$WORK" || exit 2
    set --
    DOCS_LINT_SOURCE_ONLY=1 source "$LINT" >/dev/null 2>&1
    blank_spans "$text" "$except"
  )
  if [ "$got" = "$expected" ]; then
    PASS=$((PASS + 1))
    echo "ok   $name"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: $name -- expected [$expected], got [$got]"
  fi
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

# --- Span-aware exclusion: an except-pattern hit elsewhere on the line must
# not blank out a real, distinct hit on the same line ------------------------
case_rule "DL009: Q52 is still caught when Q3 shares its line" \
  "Revenue improved in Q3, tracked as Q52." \
  DL009 yes 1

case_rule "DL009: S10 is still caught when S3 shares its line" \
  "Migrating S3 storage, tracked as S10." \
  DL009 yes 1

case_rule "DL001: an unrelated 'Rob' mention is still caught when 'Rob-gated' shares its line" \
  $'#!/bin/bash\n# per Rob'"'"'s decision, this stays Rob-gated for now' \
  DL001 yes 1

case_rule "DL008: 'as agreed' is still caught when the exempt 'round 3 robin' shares its line" \
  $'#!/bin/bash\n# batches use round 3 robin scheduling, as agreed for fairness' \
  DL008 yes 1

# --- Span-aware exclusion: a line containing only the exempted token must
# still be fully suppressed, for every affected rule ------------------------
case_rule "DL009: Q3 alone (no other id) stays exempt" \
  "Revenue improved in Q3 alone." \
  DL009 no 0

case_rule "DL009: S3 alone (no other id) stays exempt" \
  "Static assets are served from S3 alone." \
  DL009 no 0

# DL002 has no except pattern and always fires on that gating phrase by
# design, so the line still exits 1 -- this case isolates DL001's own
# exemption only.
case_rule "DL001: 'Rob-gated' alone (no bare 'Rob') stays exempt from DL001" \
  $'#!/bin/bash\n# this step is Rob-gated and Rob-only' \
  DL001 no 1

case_rule "DL008: 'round 3 trip' alone (no other process narration) stays exempt" \
  $'#!/bin/bash\n# scheduling happens in round 3 trip order for delivery batches' \
  DL008 no 0

# --- Whitebox: exemption removal is by matched position, not matched text --
# The real hazard here is an exempt token that is a *prefix* of the real id
# on the same line: a text-search removal strips the first literal
# occurrence of the exempted string, which for a prefix pair lands inside
# the real id rather than on the exempted token itself.
assert_blank \
  "blank_spans: exempt S3 removed from its own span, not from inside S30" \
  "S30 uses S3 storage" "$DL009_EXCEPT_LIVE" \
  "S30 uses    storage"

assert_blank \
  "blank_spans: exempt Q1 removed from its own span, not from inside Q10" \
  "Q10 tracked in Q1" "$DL009_EXCEPT_LIVE" \
  "Q10 tracked in   "

# The verdict-level case still matters as an end-to-end check, but on its
# own it cannot tell a genuinely span-aware removal from a text-search
# removal that happens to self-heal for a prefix pair -- that's exactly what
# the two assert_blank cases above are for.
case_rule "DL009: S30 the real id is still caught when S3 shares its line" \
  "S30 uses S3 storage." \
  DL009 yes 1

case_rule "DL009: Q10 the real id is still caught when Q1 shares its line" \
  "Q10 tracked in Q1." \
  DL009 yes 1

# --- GITHUB_ACTIONS annotation format ---------------------------------------
# The report() branch every consuming repo's Actions run actually renders.
# The plain-text cases above force GITHUB_ACTIONS=false and would not catch a
# break in the `::error file=...,title=DL009::` printf at docs-lint.sh:169.
annotation_case() {
  local file="fixture.md"
  printf '%s\n' "See Q52 for the full write-up." > "$file"
  local out rc
  out=$(GITHUB_ACTIONS=true "$LINT" --explain --mode enforce "$file" 2>&1)
  rc=$?
  if printf '%s' "$out" | grep -qE '^::error file=fixture\.md,line=[0-9]+,title=DL009::'; then
    PASS=$((PASS + 1))
    echo "ok   GITHUB_ACTIONS=true emits a DL009 error annotation"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL: GITHUB_ACTIONS=true emits a DL009 error annotation -- no matching ::error line"
    echo "$out" | sed 's/^/    /'
  fi
  if [ "$rc" != "1" ]; then
    FAIL=$((FAIL + 1))
    echo "FAIL: GITHUB_ACTIONS=true exit code -- expected 1, got $rc"
  fi
  rm -f "$file"
}
annotation_case

echo
echo "docs-lint.test.sh: $PASS passed, $FAIL failed"
[ "$FAIL" = "0" ]
