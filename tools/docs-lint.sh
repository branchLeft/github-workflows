#!/usr/bin/env bash
# Enforces the mechanical parts of branchLeft's documentation standard.
# Rule reference and rationale: tools/docs-lint-rules.md
#
# Scan/report/exemption machinery shared with opv-lint.sh lives in
# tools/lint-common.sh; this file owns only its own rule ids, patterns and
# messages.
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
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do FILES_FROM_ARGS+=("$1"); shift; done ;;
    -*) echo "docs-lint: unknown option $1" >&2; exit 2 ;;
    *) FILES_FROM_ARGS+=("$1"); shift ;;
  esac
done

# BASH_SOURCE, not $0: this file is sourced directly by docs-lint.test.sh
# (DOCS_LINT_SOURCE_ONLY=1 source docs-lint.sh) as well as executed, and $0
# does not change on `source` -- it would stay the caller's own path.
# shellcheck source=tools/lint-common.sh
source "$(dirname "${BASH_SOURCE[0]}")/lint-common.sh"

lint_common_init || exit $?
trap 'rm -rf "$TMPDIR_LINT"' EXIT
# Bash 3.2 treats "${arr[@]}" on an empty array as unbound under `set -u`,
# so the expansion is guarded here rather than passed unconditionally.
if [ ${#FILES_FROM_ARGS[@]} -gt 0 ]; then
  lint_all_files "${FILES_FROM_ARGS[@]}"
else
  lint_all_files
fi

LINT_IGNOREFILE=".docs-lintignore"
LINT_PREFIX="docs-lint-disable-next-line"
LINT_FAILMSG="Failing: see tools/docs-lint-rules.md for each rule and how to fix it."

lint_resolve_mode ".docs-lint.mode" "$MODE_OVERRIDE" || exit $?
lint_classify_files

# Test-only escape hatch: `DOCS_LINT_SOURCE_ONLY=1 source docs-lint.sh` runs
# everything above (option parsing, lint-common's own function and pattern
# definitions) and returns before any file is scanned, so docs-lint.test.sh
# can call blank_spans directly against the real patterns and assert on its
# output instead of only on a rule's final verdict.
CHECK_SOURCE_ONLY="${DOCS_LINT_SOURCE_ONLY:-0}"

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

# DL012 catches the *replacement* id shape for branchLeft's own items:
# branchLeft/repo#N. Rationale, scope and known limitations: docs-lint-rules.md.
# Scoped to the literal `branchLeft` owner, not any owner/repo#N: an upstream
# reference (`actions/runner#1327`) is a workaround citation, not board
# narration, and a generic owner/repo#N shape false-matches doc URL fragments.
DL012_MATCH="\\bbranchLeft/[A-Za-z0-9._-]+#[0-9]+\\b"

if lint_source_only; then
  return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# DL000 — a suppression with no rule id or no reason
# ---------------------------------------------------------------------------
cat "$MD_FILES" "$CODE_FILES" > "$TMPDIR_LINT/md_and_code"
check_malformed_suppression "DL000" "$LINT_PREFIX" "DL[0-9]{3}" "$TMPDIR_LINT/md_and_code"

for scope in md code; do
  ext=md_scannable; [ "$scope" = "code" ] && ext=code_scannable
  list="$MD_FILES"; [ "$scope" = "code" ] && list="$CODE_FILES"
  run_rule DL002 "$list" "$GATE_MATCH" "" "$ext" \
    "process gating names a person; use a role (platform owner / repo admin / operator)"
  run_rule DL001 "$list" "$NAME_MATCH" "$GATE_MATCH" "$ext" \
    "names a person; state the decision itself, or use a role noun"
  run_rule DL009 "$list" "$DL009_MATCH" "$DL009_EXCEPT" \
    "$ext" "story, backlog or standards-gap id; describe the change, not the ticket"
done

run_rule DL004 "$MD_FILES" '^\*\*Status:\*\*' "" md_scannable \
  "status header; a committed document is the current description of the thing"
run_rule DL005 "$MD_FILES" '~~|\b[Ss]upersed(ed|es|ing)\b|\bstruck through\b' "" md_scannable \
  "superseded-decision trail; delete the old thing and describe the new one"
run_rule DL006 "$MD_FILES" \
  '\*\*[^*]{0,40}20[0-9]{2}-[0-9]{2}-[0-9]{2}[^*]{0,40}\*\*|\([^)]{0,20}20[0-9]{2}-[0-9]{2}-[0-9]{2}[^)]{0,20}\)|[Vv]erified (live |on )?20[0-9]{2}-|\bAs of 20[0-9]{2}-|\bApplied 20[0-9]{2}-' \
  "" md_scannable "dated decision or verification log; history belongs in git and the PR"
run_rule DL007 "$CODE_FILES" \
  '[Vv]erified (live|against the live|hands-on)|\bconfirmed (live|in production) on\b' "" code_scannable \
  "verification log in a comment; state the constraint, not when it was checked"
run_rule DL008 "$CODE_FILES" \
  "[Aa]dversarial review|\\bround [0-9]\\b|\\bper [A-Z][a-z]+'s .{0,30}decision|\\bas (discussed|agreed|we decided)\\b" \
  '\bround [0-9] (trip|robin)\b' code_scannable \
  "development-process narration in a comment"
run_rule DL012 "$CODE_FILES" "$DL012_MATCH" "" code_scannable \
  "branchLeft/repo#N work-item reference in a comment; state the constraint, put the reference in the PR or a doc"

# ---------------------------------------------------------------------------
# DL010 — links must resolve inside the repo
# ---------------------------------------------------------------------------
# Findings are collected to a file rather than piped, so that `report` runs in
# this shell and its counts reach the summary and the exit code.
: > "$TMPDIR_LINT/dl010"
while IFS= read -r file; do
  is_exempt "$LINT_IGNOREFILE" "$file" DL010 && continue
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
done < "$MD_FILES"

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
  is_exempt "$LINT_IGNOREFILE" "$file" DL011 && continue
  awk -v f="$file" '
    function flush() {
      if (run >= 7 && start > 20) printf "%s\t%d\t%d\n", f, start, run
      run = 0
    }
    /^[[:space:]]*(\/\/|#|\*|\/\*)/ { if (run == 0) start = NR; run++; next }
    { flush() }
    END { flush() }
  ' "$file" >> "$TMPDIR_LINT/dl011"
done < "$CODE_FILES"

while IFS=$'\t' read -r f ln len; do
  [ -n "$f" ] || continue
  report DL011 "$f" "$ln" "$len-line comment block; if an operator would follow it as a procedure it is a runbook, not a comment" "" DL011
done < "$TMPDIR_LINT/dl011"

# ---------------------------------------------------------------------------
# Structural markdown hygiene. Optional: skipped where Node is unavailable,
# because it is the only part of this script with a runtime dependency.
# ---------------------------------------------------------------------------
if [ -s "$MD_FILES" ] && command -v npx >/dev/null 2>&1; then
  CFG="$(dirname "$0")/docs.markdownlint-cli2.jsonc"
  if ! npx --yes markdownlint-cli2@0.18.1 --config "$CFG" $(cat "$MD_FILES") 2>&1 | sed 's/^/markdownlint: /'; then
    if [ "$MODE" = "enforce" ]; then FAILED=1; fi
    bump "markdownlint"
  fi
else
  [ -s "$MD_FILES" ] && echo "docs-lint: npx unavailable, skipping structural markdown checks"
fi

lint_summary_and_exit "docs-lint"
