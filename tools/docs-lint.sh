#!/usr/bin/env bash
# Enforces the mechanical parts of branchLeft's documentation standard.
# Rule reference and rationale: tools/docs-lint-rules.md
#
# No runtime dependencies beyond bash + grep + awk, because three of the repos
# this runs in have no package.json and no Node.
#
# Usage:
#   docs-lint.sh [--explain] [--mode warn|enforce] [FILE...]
#
# With no FILE arguments it scans every tracked file. With FILE arguments it
# scans only those (this is how pre-commit invokes it).

set -uo pipefail

EXPLAIN=0
MODE_OVERRIDE=""
FILES_FROM_ARGS=()

while [ $# -gt 0 ]; do
  case "$1" in
    --explain) EXPLAIN=1; shift ;;
    --mode) MODE_OVERRIDE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do FILES_FROM_ARGS+=("$1"); shift; done ;;
    -*) echo "docs-lint: unknown option $1" >&2; exit 2 ;;
    *) FILES_FROM_ARGS+=("$1"); shift ;;
  esac
done

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
  echo "docs-lint: not inside a git repository" >&2; exit 2; }
cd "$REPO_ROOT" || exit 2

# ---------------------------------------------------------------------------
# Mode. Absent .docs-lint.mode means enforce, so a new repo is protected
# without having to opt in. `warn` is the ratchet: the full tree is advisory,
# but files changed in this branch are still enforced.
# ---------------------------------------------------------------------------
MODE="enforce"
[ -f .docs-lint.mode ] && MODE=$(tr -d '[:space:]' < .docs-lint.mode)
[ -n "$MODE_OVERRIDE" ] && MODE="$MODE_OVERRIDE"
case "$MODE" in
  warn|enforce) ;;
  *) echo "docs-lint: .docs-lint.mode must contain 'warn' or 'enforce', got '$MODE'" >&2; exit 2 ;;
esac

TMPDIR_LINT=$(mktemp -d) || exit 2
trap 'rm -rf "$TMPDIR_LINT"' EXIT

# ---------------------------------------------------------------------------
# File sets
# ---------------------------------------------------------------------------
ALL_FILES="$TMPDIR_LINT/all"
if [ ${#FILES_FROM_ARGS[@]} -gt 0 ]; then
  printf '%s\n' "${FILES_FROM_ARGS[@]}" > "$ALL_FILES"
else
  git ls-files > "$ALL_FILES"
fi

# Files this run is allowed to fail on. In enforce mode that's everything; in
# warn mode only what this branch touched.
ENFORCED="$TMPDIR_LINT/enforced"
if [ "$MODE" = "enforce" ]; then
  cp "$ALL_FILES" "$ENFORCED"
else
  BASE=$(git merge-base origin/main HEAD 2>/dev/null || git merge-base main HEAD 2>/dev/null || true)
  if [ -n "$BASE" ]; then
    git diff --name-only --diff-filter=d "$BASE"...HEAD 2>/dev/null > "$ENFORCED"
  else
    : > "$ENFORCED"
  fi
  # Uncommitted work counts too, so pre-commit and CI agree.
  git diff --name-only --diff-filter=d HEAD 2>/dev/null >> "$ENFORCED"
  sort -u "$ENFORCED" -o "$ENFORCED"
fi

grep -E '\.(md|mdx)$' "$ALL_FILES" > "$TMPDIR_LINT/md" 2>/dev/null
grep -E '\.(ts|tsx|js|jsx|mjs|cjs|py|sh|bash|ya?ml|tf)$' "$ALL_FILES" > "$TMPDIR_LINT/code" 2>/dev/null
: > "$TMPDIR_LINT/md.exists"; : > "$TMPDIR_LINT/code.exists"
while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done < "$TMPDIR_LINT/md"   > "$TMPDIR_LINT/md.exists"
while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done < "$TMPDIR_LINT/code" > "$TMPDIR_LINT/code.exists"
mv "$TMPDIR_LINT/md.exists" "$TMPDIR_LINT/md"
mv "$TMPDIR_LINT/code.exists" "$TMPDIR_LINT/code"

# ---------------------------------------------------------------------------
# .docs-lintignore  —  glob<TAB>RULE_IDS<TAB># reason
# `*` matches across directory separators, so `templates/*` covers nested paths.
# ---------------------------------------------------------------------------
is_exempt() { # path rule -> 0 if exempt
  [ -f .docs-lintignore ] || return 1
  local path="$1" rule="$2" glob rules line
  while IFS= read -r line; do
    case "$line" in ''|\#*) continue ;; esac
    glob=$(printf '%s' "$line" | cut -f1)
    rules=$(printf '%s' "$line" | cut -f2)
    [ -n "$glob" ] || continue
    glob=${glob//\*\*/\*}
    # shellcheck disable=SC2254
    case "$path" in
      $glob)
        case ",$rules," in
          *",$rule,"*|*",ALL,"*) return 0 ;;
        esac
        ;;
    esac
  done < .docs-lintignore
  return 1
}

# Inline suppression on the preceding line. The rule id and a reason are both
# mandatory; a bare disable is itself reported (DL000).
is_disabled() { # file line rule -> 0 if suppressed
  local file="$1" ln="$2" rule="$3" prev
  [ "$ln" -gt 1 ] || return 1
  prev=$(sed -n "$((ln - 1))p" "$file" 2>/dev/null)
  # The reason must start alphanumeric, so a comment terminator (`-->`, `*/`)
  # doesn't get counted as one.
  printf '%s' "$prev" | grep -qE "docs-lint-disable-next-line[[:space:]]+$rule[[:space:]]+[[:alnum:]]" || return 1
  return 0
}

# ---------------------------------------------------------------------------
# Scannable line extraction
# ---------------------------------------------------------------------------
# Markdown with fenced code blocks blanked, so a rule never fires on a sample
# command or an example of the thing it forbids. Line numbers are preserved.
md_scannable() {
  awk '
    /^[[:space:]]*(```|~~~)/ { fence = !fence; print ""; next }
    { print (fence ? "" : $0) }
  ' "$1"
}

# Comment lines only. Deliberately crude: it over-matches (a `#` in YAML data)
# but never under-matches, and it never reads string literals — which is why
# legitimate prose in JSX/marketing copy is structurally out of scope.
code_scannable() {
  awk '{ if ($0 ~ /^[[:space:]]*(\/\/|#|\*|\/\*)/) print $0; else print "" }' "$1"
}

# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
FAILED=0
declare -a SUMMARY_IDS=()
declare -a SUMMARY_COUNTS=()

bump() { # rule
  local i
  for i in "${!SUMMARY_IDS[@]}"; do
    if [ "${SUMMARY_IDS[$i]}" = "$1" ]; then
      SUMMARY_COUNTS[$i]=$(( ${SUMMARY_COUNTS[$i]} + 1 )); return
    fi
  done
  SUMMARY_IDS+=("$1"); SUMMARY_COUNTS+=(1)
}

report() { # rule file line message matched-text
  local rule="$1" file="$2" ln="$3" msg="$4" matched="${5:-}"
  local level="warning" detail=""
  if grep -qxF "$file" "$ENFORCED" && [ "$rule" != "DL011" ]; then
    level="error"; FAILED=1
  fi
  [ "$rule" = "DL011" ] && level="notice"
  [ "$EXPLAIN" = "1" ] && [ -n "$matched" ] && detail=" -- matched: $matched"
  bump "$rule"
  if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
    printf '::%s file=%s,line=%s,title=%s::%s%s\n' "$level" "$file" "$ln" "$rule" "$msg" "$detail"
  else
    printf '%s:%s: [%s] %s %s%s\n' "$file" "$ln" "$rule" "$level" "$msg" "$detail"
  fi
}

# Blank every span an ERE ($2) matches out of text ($1), replacing each with
# spaces of the same length so match offsets earlier in the same line stay
# valid. Blanking by position (not by searching for the matched text again)
# matters: a text-search removal can strip the wrong occurrence of that text
# (e.g. blanking "S3" must never eat the "S3" inside "S30"). LC_ALL=C is
# forced so grep's byte offsets and bash's substring indexing agree on
# multibyte content — under a UTF-8 locale they diverge (grep counts bytes,
# bash counts characters) and offsets silently misalign.
blank_spans() { # text except -> blanked text on stdout
  local text="$1" except="$2" reduced off span len
  export LC_ALL=C
  reduced="$text"
  while IFS=: read -r off span; do
    [ -n "$off" ] || continue
    len=${#span}
    reduced="${reduced:0:off}$(printf '%*s' "$len" '')${reduced:off+len}"
  done < <(printf '%s\n' "$text" | grep -boE "$except")
  printf '%s' "$reduced"
}

# rule, file-list, match-ere, except-ere (or ""), extractor, message
run_rule() {
  local rule="$1" list="$2" match="$3" except="$4" extractor="$5" msg="$6"
  local file scan hits ln text
  while IFS= read -r file; do
    is_exempt "$file" "$rule" && continue
    scan="$TMPDIR_LINT/scan"
    "$extractor" "$file" > "$scan"
    if [ -n "$except" ]; then
      # Whole-line exclusion would drop a real hit that merely shares a line
      # with an exempted token, so each candidate line is re-tested with its
      # exempted spans blanked out first: only a line that still matches
      # afterwards is a genuine hit.
      hits=""
      while IFS= read -r h; do
        [ -n "$h" ] || continue
        ln=${h%%:*}; text=${h#*:}
        reduced=$(blank_spans "$text" "$except")
        printf '%s\n' "$reduced" | grep -qE "$match" && hits="$hits$h"$'\n'
      done < <(grep -nE "$match" "$scan")
    else
      hits=$(grep -nE "$match" "$scan")
    fi
    [ -n "$hits" ] || continue
    while IFS= read -r h; do
      [ -n "$h" ] || continue
      ln=${h%%:*}; text=${h#*:}
      is_disabled "$file" "$ln" "$rule" && continue
      report "$rule" "$file" "$ln" "$msg" "$(printf '%s' "$text" | sed 's/^[[:space:]]*//' | cut -c1-90)"
    done <<< "$hits"
  done < "$list"
}

# ---------------------------------------------------------------------------
# Content rules
# ---------------------------------------------------------------------------
NAME_MATCH="\\bRob\\b|\\bRob's\\b|\\bRobert\\b"
# Case-sensitive by design: it must not match rob@branchleft.co.uk, which is an
# operational value runbooks legitimately carry. \b rejects Roboto, robots,
# robust, probably, problem.
GATE_MATCH="\\bRob-(only|gated)\\b"

# `S3` is deliberately exempt: in this corpus it is both an AWS service and a
# real story id, and no pattern separates them. Missing the occasional story
# S3 costs far less than 60 false positives teaching people to ignore the
# linter. Story ids S7 upward are still caught. `Q1`-`Q4` get the same
# exemption for the same reason: calendar quarters in roadmap and marketing
# prose collide with the low end of the Q-item id space.
DL009_MATCH="\\bS[0-9]{1,3}\\b|\\bB[0-9]{1,3}\\b|\\bQ[0-9]{1,3}\\b"
DL009_EXCEPT="\\bS3\\b|S3-|\\bB[0-9]+(GB|MB|KB|Gi|Mi|B)\\b|\\bQ[1-4]\\b"

# DL012 catches the *replacement* id shape: owner/repo#N, the only form the
# work-tracking standard now mints. It is deliberately code-scope only and
# has no md counterpart -- owner/repo#N is the correct, expected home for a
# reference in commit messages, PR bodies and Markdown prose, and is not a
# defect there. A bare `#N` is deliberately not matched: it collides with
# CSS ids, shell parameter expansion and much else, and no pattern separates
# those from a real reference -- the same reasoning that keeps DL009 off S3
# and Q1-Q4. Comments are excluded from carrying a work-item reference at
# all (branchLeft's code-comment style: a comment states what the code
# itself cannot, never development-process context), so this fires on a
# correctly-formed reference exactly as readily as a malformed one.
DL012_MATCH="\\b[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+#[0-9]+\\b"

# Test-only escape hatch: `DOCS_LINT_SOURCE_ONLY=1 source docs-lint.sh` runs
# everything above (option parsing, the git-root check, blank_spans/run_rule's
# own definitions, and the match/except constants) and returns before any
# file is scanned, so docs-lint.test.sh can call blank_spans directly against
# the real patterns and assert on its output instead of only on a rule's
# final verdict.
if [ "${DOCS_LINT_SOURCE_ONLY:-0}" = "1" ]; then
  return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# DL000 — a suppression with no rule id or no reason
# ---------------------------------------------------------------------------
while IFS= read -r file; do
  is_exempt "$file" DL000 && continue
  hits=$(grep -nE 'docs-lint-disable-next-line' "$file" | grep -vE 'docs-lint-disable-next-line[[:space:]]+DL[0-9]{3}[[:space:]]+[[:alnum:]]')
  [ -n "$hits" ] || continue
  while IFS= read -r h; do
    [ -n "$h" ] || continue
    report "DL000" "$file" "${h%%:*}" "suppression needs a rule id and a reason: docs-lint-disable-next-line DL006 <why>"
  done <<< "$hits"
done < <(cat "$TMPDIR_LINT/md" "$TMPDIR_LINT/code")

for scope in md code; do
  ext=md_scannable; [ "$scope" = "code" ] && ext=code_scannable
  run_rule DL002 "$TMPDIR_LINT/$scope" "$GATE_MATCH" "" "$ext" \
    "process gating names a person; use a role (platform owner / repo admin / operator)"
  run_rule DL001 "$TMPDIR_LINT/$scope" "$NAME_MATCH" "$GATE_MATCH" "$ext" \
    "names a person; state the decision itself, or use a role noun"
  run_rule DL009 "$TMPDIR_LINT/$scope" "$DL009_MATCH" "$DL009_EXCEPT" \
    "$ext" "story, backlog or standards-gap id; describe the change, not the ticket"
done

run_rule DL004 "$TMPDIR_LINT/md" '^\*\*Status:\*\*' "" md_scannable \
  "status header; a committed document is the current description of the thing"
run_rule DL005 "$TMPDIR_LINT/md" '~~|\b[Ss]upersed(ed|es|ing)\b|\bstruck through\b' "" md_scannable \
  "superseded-decision trail; delete the old thing and describe the new one"
run_rule DL006 "$TMPDIR_LINT/md" \
  '\*\*[^*]{0,40}20[0-9]{2}-[0-9]{2}-[0-9]{2}[^*]{0,40}\*\*|\([^)]{0,20}20[0-9]{2}-[0-9]{2}-[0-9]{2}[^)]{0,20}\)|[Vv]erified (live |on )?20[0-9]{2}-|\bAs of 20[0-9]{2}-|\bApplied 20[0-9]{2}-' \
  "" md_scannable "dated decision or verification log; history belongs in git and the PR"
run_rule DL007 "$TMPDIR_LINT/code" \
  '[Vv]erified (live|against the live|hands-on)|\bconfirmed (live|in production) on\b' "" code_scannable \
  "verification log in a comment; state the constraint, not when it was checked"
run_rule DL008 "$TMPDIR_LINT/code" \
  "[Aa]dversarial review|\\bround [0-9]\\b|\\bper [A-Z][a-z]+'s .{0,30}decision|\\bas (discussed|agreed|we decided)\\b" \
  '\bround [0-9] (trip|robin)\b' code_scannable \
  "development-process narration in a comment"
run_rule DL012 "$TMPDIR_LINT/code" "$DL012_MATCH" "" code_scannable \
  "owner/repo#N work-item reference in a comment; state the constraint, put the reference in the PR or a doc"

# ---------------------------------------------------------------------------
# DL010 — links must resolve inside the repo
# ---------------------------------------------------------------------------
# Findings are collected to a file rather than piped, so that `report` runs in
# this shell and its counts reach the summary and the exit code.
: > "$TMPDIR_LINT/dl010"
while IFS= read -r file; do
  is_exempt "$file" DL010 && continue
  dir=$(dirname "$file")
  while IFS= read -r h; do
    [ -n "$h" ] || continue
    ln=${h%%:*}; target=${h#*:}
    target=${target#](}; target=${target%)}
    case "$target" in
      http://*|https://*|mailto:*|\#*|'') continue ;;
    esac
    target=${target%%#*}
    target=${target%% *}
    [ -n "$target" ] || continue
    case "$target" in /*) resolved="$target" ;; *) resolved="$dir/$target" ;; esac
    abs=$(cd "$(dirname "$resolved")" 2>/dev/null && pwd)/$(basename "$resolved")
    if [ -z "${abs%/*}" ] || ! printf '%s' "$abs" | grep -q "^$REPO_ROOT/"; then
      printf '%s\t%s\t%s\t%s\n' "$file" "$ln" "escapes" "$target" >> "$TMPDIR_LINT/dl010"
    elif [ ! -e "$resolved" ]; then
      printf '%s\t%s\t%s\t%s\n' "$file" "$ln" "missing" "$target" >> "$TMPDIR_LINT/dl010"
    fi
  done < <(md_scannable "$file" | grep -noE '\]\([^)]+\)')
done < "$TMPDIR_LINT/md"

while IFS=$'\t' read -r file ln kind target; do
  [ -n "$file" ] || continue
  if [ "$kind" = "escapes" ]; then
    report DL010 "$file" "$ln" "link escapes the repo; use an absolute GitHub URL for cross-repo references" "$target"
  else
    report DL010 "$file" "$ln" "link target does not exist" "$target"
  fi
done < "$TMPDIR_LINT/dl010"

# ---------------------------------------------------------------------------
# DL011 — long comment blocks. Advisory forever: "probably belongs in a doc"
# is a review judgement, not something a regex can settle.
# ---------------------------------------------------------------------------
: > "$TMPDIR_LINT/dl011"
while IFS= read -r file; do
  is_exempt "$file" DL011 && continue
  awk -v f="$file" '
    function flush() {
      if (run >= 7 && start > 20) printf "%s\t%d\t%d\n", f, start, run
      run = 0
    }
    /^[[:space:]]*(\/\/|#|\*|\/\*)/ { if (run == 0) start = NR; run++; next }
    { flush() }
    END { flush() }
  ' "$file" >> "$TMPDIR_LINT/dl011"
done < "$TMPDIR_LINT/code"

while IFS=$'\t' read -r f ln len; do
  [ -n "$f" ] || continue
  report DL011 "$f" "$ln" "$len-line comment block; if an operator would follow it as a procedure it is a runbook, not a comment"
done < "$TMPDIR_LINT/dl011"

# ---------------------------------------------------------------------------
# Structural markdown hygiene. Optional: skipped where Node is unavailable,
# because it is the only part of this script with a runtime dependency.
# ---------------------------------------------------------------------------
if [ -s "$TMPDIR_LINT/md" ] && command -v npx >/dev/null 2>&1; then
  CFG="$(dirname "$0")/docs.markdownlint-cli2.jsonc"
  if ! npx --yes markdownlint-cli2@0.18.1 --config "$CFG" $(cat "$TMPDIR_LINT/md") 2>&1 | sed 's/^/markdownlint: /'; then
    if [ "$MODE" = "enforce" ]; then FAILED=1; fi
    bump "markdownlint"
  fi
else
  [ -s "$TMPDIR_LINT/md" ] && echo "docs-lint: npx unavailable, skipping structural markdown checks"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo
echo "docs-lint summary (mode: $MODE)"
if [ ${#SUMMARY_IDS[@]} -eq 0 ]; then
  echo "  clean"
else
  for i in "${!SUMMARY_IDS[@]}"; do
    printf '  %-14s %s\n' "${SUMMARY_IDS[$i]}" "${SUMMARY_COUNTS[$i]}"
  done
fi

if [ "$FAILED" = "1" ]; then
  echo
  echo "Failing: see tools/docs-lint-rules.md for each rule and how to fix it."
  exit 1
fi
if [ "$MODE" = "warn" ] && [ ${#SUMMARY_IDS[@]} -gt 0 ]; then
  echo
  echo "Advisory only: this repo is in warn mode. Files changed on this branch are still enforced."
  echo "Remove .docs-lint.mode once the tree is clean."
fi
exit 0
