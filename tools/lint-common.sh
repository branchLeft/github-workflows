#!/usr/bin/env bash
# Shared scan/report/exemption machinery for docs-lint.sh and opv-lint.sh. Sourced, never executed.
# A caller sets LINT_IGNOREFILE and LINT_PREFIX before calling run_rule or check_malformed_suppression.
# No dependencies beyond bash, grep and awk: some callers have no Node. Design: lint-common.md beside this file.

# ---------------------------------------------------------------------------
# Repo root + scratch dir. Callers run from anywhere; every path after this
# is relative to the repo root.
# ---------------------------------------------------------------------------
lint_common_init() {
  REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || {
    echo "$(basename "$0"): not inside a git repository" >&2; return 2; }
  cd "$REPO_ROOT" || return 2

  TMPDIR_LINT=$(mktemp -d) || return 2
  # The EXIT trap is deliberately NOT set here: a trap on EXIT registered
  # inside a function is torn down the moment that function returns in at
  # Narrative: lint-common.comments.md, block 1.
  return 0
}

# ---------------------------------------------------------------------------
# File sets. Populates $TMPDIR_LINT/all from either the explicit file list
# passed in (how pre-commit invokes a check) or the full tracked tree.
# ---------------------------------------------------------------------------
lint_all_files() { # file... (may be zero args)
  # Bash 3.2 (macOS's shipped /bin/bash) treats "${arr[@]}" on a zero-element
  # array as an unbound-variable reference under `set -u` -- the callers of
  # Narrative: lint-common.comments.md, block 2.
  ALL_FILES="$TMPDIR_LINT/all"
  if [ "$#" -gt 0 ]; then
    printf '%s\n' "$@" > "$ALL_FILES"
  else
    git ls-files > "$ALL_FILES"
  fi
}

# Mode. Absent mode file means enforce, so a new repo is protected without
# opting in. `warn` is the ratchet: the full tree is advisory, but files this
# branch touched are still enforced -- so CI is green on day one and no new
# violation can land from that moment.
lint_resolve_mode() { # mode-file mode-override
  local mode_file="$1" override="$2"
  MODE="enforce"
  [ -f "$mode_file" ] && MODE=$(tr -d '[:space:]' < "$mode_file")
  [ -n "$override" ] && MODE="$override"
  case "$MODE" in
    warn|enforce) ;;
    *) echo "$(basename "$0"): $mode_file must contain 'warn' or 'enforce', got '$MODE'" >&2; return 2 ;;
  esac

  ENFORCED="$TMPDIR_LINT/enforced"
  if [ "$MODE" = "enforce" ]; then
    cp "$ALL_FILES" "$ENFORCED"
  else
    local base
    base=$(git merge-base origin/main HEAD 2>/dev/null || git merge-base main HEAD 2>/dev/null || true)
    if [ -n "$base" ]; then
      git diff --name-only --diff-filter=d "$base"...HEAD 2>/dev/null > "$ENFORCED"
    else
      : > "$ENFORCED"
    fi
    # Uncommitted work counts too, so pre-commit and CI agree.
    git diff --name-only --diff-filter=d HEAD 2>/dev/null >> "$ENFORCED"
    sort -u "$ENFORCED" -o "$ENFORCED"
  fi
  return 0
}

# Splits $ALL_FILES into markdown and "code" (the extension list every
# check here cares about), dropping entries that don't exist on disk (a
# renamed-away file can still be named in a diff-derived list).
lint_classify_files() {
  grep -E '\.(md|mdx)$' "$ALL_FILES" > "$TMPDIR_LINT/md" 2>/dev/null
  grep -E '\.(ts|tsx|js|jsx|mjs|cjs|py|sh|bash|ya?ml|tf)$' "$ALL_FILES" > "$TMPDIR_LINT/code" 2>/dev/null
  # HTML design files: a separate class, because markdownlint and the
  # markdown-structure rules do not apply to them. Only the name and gating
  # rules run over this class (docs-lint.sh).
  grep -E '\.html$' "$ALL_FILES" > "$TMPDIR_LINT/html" 2>/dev/null
  : > "$TMPDIR_LINT/md.exists"; : > "$TMPDIR_LINT/code.exists"; : > "$TMPDIR_LINT/html.exists"
  while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done < "$TMPDIR_LINT/md"   > "$TMPDIR_LINT/md.exists"
  while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done < "$TMPDIR_LINT/code" > "$TMPDIR_LINT/code.exists"
  while IFS= read -r f; do [ -f "$f" ] && printf '%s\n' "$f"; done < "$TMPDIR_LINT/html" > "$TMPDIR_LINT/html.exists"
  mv "$TMPDIR_LINT/md.exists" "$TMPDIR_LINT/md"
  mv "$TMPDIR_LINT/code.exists" "$TMPDIR_LINT/code"
  mv "$TMPDIR_LINT/html.exists" "$TMPDIR_LINT/html"
  MD_FILES="$TMPDIR_LINT/md"
  CODE_FILES="$TMPDIR_LINT/code"
  HTML_FILES="$TMPDIR_LINT/html"
}

# ---------------------------------------------------------------------------
# Exemption tier 1: a path glob in an ignorefile.
# Narrative: lint-common.comments.md, block 3.
is_exempt() { # ignorefile path rule -> 0 if exempt
  local ignorefile="$1" path="$2" rule="$3" glob rules line
  [ -f "$ignorefile" ] || return 1
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
  done < "$ignorefile"
  return 1
}

# ---------------------------------------------------------------------------
# Exemption tier 2: inline suppression on the preceding line. The rule id and
# Narrative: lint-common.comments.md, block 4.
is_disabled() { # prefix file line rule -> 0 if suppressed
  local prefix="$1" file="$2" ln="$3" rule="$4" prev
  [ "$ln" -gt 1 ] || return 1
  prev=$(sed -n "$((ln - 1))p" "$file" 2>/dev/null)
  # The reason must start alphanumeric, so a comment terminator (`-->`, `*/`)
  # doesn't get counted as one.
  printf '%s' "$prev" | grep -qE "${prefix}[[:space:]]+${rule}[[:space:]]+[[:alnum:]]" || return 1
  return 0
}

# A suppression with no rule id or no reason. Shared because both docs-lint
# and opv-lint need the identical check against their own prefix and rule-id
# shape; only the zero-rule id, the prefix and the rule-id pattern differ.
check_malformed_suppression() { # zero-rule prefix rule-id-ere file-list
  local zero_rule="$1" prefix="$2" rule_ere="$3" list="$4" file hits h
  while IFS= read -r file; do
    is_exempt "$LINT_IGNOREFILE" "$file" "$zero_rule" && continue
    hits=$(grep -nE "$prefix" "$file" | grep -vE "${prefix}[[:space:]]+(${rule_ere})[[:space:]]+[[:alnum:]]")
    [ -n "$hits" ] || continue
    while IFS= read -r h; do
      [ -n "$h" ] || continue
      report "$zero_rule" "$file" "${h%%:*}" "suppression needs a rule id and a reason: ${prefix} <ID> <why>"
    done <<< "$hits"
  done < "$list"
}

# ---------------------------------------------------------------------------
# Scannable line extraction. Every extractor preserves the file's own line
# Narrative: lint-common.comments.md, block 5.

# Markdown with fenced code blocks blanked, so a rule never fires on a sample
# command or an example of the thing it forbids.
md_scannable() {
  awk '
    /^[[:space:]]*(```|~~~)/ { fence = !fence; print ""; next }
    { print (fence ? "" : $0) }
  ' "$1"
}

# Comment lines only. Deliberately crude: it over-matches (a `#` in YAML
# data) but never under-matches, and it never reads string literals -- which
# is why legitimate prose in JSX/marketing copy is structurally out of scope.
code_scannable() {
  awk '{ if ($0 ~ /^[[:space:]]*(\/\/|#|\*|\/\*)/) print $0; else print "" }' "$1"
}

# The whole file, unblanked. For a rule whose target can appear anywhere in
# committed content -- a credential or an address, not process narration --
# restricting to comments or excluding fenced examples would miss exactly
# the paste-and-run lines that matter most.
raw_scannable() {
  cat "$1"
}

# The inverse of md_scannable: everything BLANKED except the content of a
# fenced code block whose language is a shell/command dialect
# Narrative: lint-common.comments.md, block 6.
command_fence_scannable() {
  awk '
    /^[[:space:]]*(```|~~~)/ {
      if (fence) { fence = 0; print ""; next }
      line = $0
      sub(/^[[:space:]]*(```|~~~)/, "", line)
      n = split(line, parts, /[[:space:]]+/)
      lang = tolower(parts[1])
      fence = (lang == "bash" || lang == "sh" || lang == "shell" || lang == "console") ? 1 : 0
      print ""
      next
    }
    { print (fence ? $0 : "") }
  ' "$1"
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

report() { # rule file line message matched-text advisory(optional: non-empty = never fails)
  local rule="$1" file="$2" ln="$3" msg="$4" matched="${5:-}" advisory="${6:-}"
  local level="warning" detail=""
  if [ -z "$advisory" ] && grep -qxF "$file" "$ENFORCED"; then
    level="error"; FAILED=1
  fi
  [ -n "$advisory" ] && level="notice"
  [ "${EXPLAIN:-0}" = "1" ] && [ -n "$matched" ] && detail=" -- matched: $matched"
  bump "$rule"
  if [ "${GITHUB_ACTIONS:-}" = "true" ]; then
    printf '::%s file=%s,line=%s,title=%s::%s%s\n' "$level" "$file" "$ln" "$rule" "$msg" "$detail"
  else
    printf '%s:%s: [%s] %s %s%s\n' "$file" "$ln" "$rule" "$level" "$msg" "$detail"
  fi
}

# Blank every span an ERE ($2) matches out of text ($1), replacing each with
# spaces of the same length so match offsets earlier in the same line stay
# Narrative: lint-common.comments.md, block 7.
blank_spans() { # text except ci(optional: "1" = case-insensitive) -> blanked text on stdout
  local text="$1" except="$2" ci="${3:-}" reduced off span len flags="-boE"
  export LC_ALL=C
  [ -n "$ci" ] && flags="-iboE"
  reduced="$text"
  while IFS=: read -r off span; do
    [ -n "$off" ] || continue
    len=${#span}
    reduced="${reduced:0:off}$(printf '%*s' "$len" '')${reduced:off+len}"
  done < <(printf '%s\n' "$text" | grep $flags "$except")
  printf '%s' "$reduced"
}

# rule, file-list, match-ere, except-ere (or ""), extractor, message,
# advisory ("1" = never fails, only a notice: mirrors docs-lint's DL011),
# Narrative: lint-common.comments.md, block 8.
run_rule() {
  local rule="$1" list="$2" match="$3" except="$4" extractor="$5" msg="$6" advisory="${7:-}" ci="${8:-}"
  local file scan hits ln text reduced mflags="-nE" qflags="-qE"
  [ -n "$ci" ] && mflags="-inE" && qflags="-iqE"
  while IFS= read -r file; do
    is_exempt "$LINT_IGNOREFILE" "$file" "$rule" && continue
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
        reduced=$(blank_spans "$text" "$except" "$ci")
        printf '%s\n' "$reduced" | grep $qflags "$match" && hits="$hits$h"$'\n'
      done < <(grep $mflags "$match" "$scan")
    else
      hits=$(grep $mflags "$match" "$scan")
    fi
    [ -n "$hits" ] || continue
    while IFS= read -r h; do
      [ -n "$h" ] || continue
      ln=${h%%:*}; text=${h#*:}
      is_disabled "$LINT_PREFIX" "$file" "$ln" "$rule" && continue
      report "$rule" "$file" "$ln" "$msg" "$(printf '%s' "$text" | sed 's/^[[:space:]]*//' | cut -c1-90)" "$advisory"
    done <<< "$hits"
  done < "$list"
}

# ---------------------------------------------------------------------------
# Summary + exit. `label` is the check name shown in the header (e.g.
# "docs-lint", "opv-lint").
# ---------------------------------------------------------------------------
lint_summary_and_exit() { # label
  local label="$1"
  echo
  echo "$label summary (mode: $MODE)"
  if [ ${#SUMMARY_IDS[@]} -eq 0 ]; then
    echo "  clean"
  else
    local i
    for i in "${!SUMMARY_IDS[@]}"; do
      printf '  %-14s %s\n' "${SUMMARY_IDS[$i]}" "${SUMMARY_COUNTS[$i]}"
    done
  fi

  if [ "$FAILED" = "1" ]; then
    echo
    echo "${LINT_FAILMSG:-See the rules doc for each rule and how to fix it.}"
    exit 1
  fi
  if [ "$MODE" = "warn" ] && [ ${#SUMMARY_IDS[@]} -gt 0 ]; then
    echo
    echo "Advisory only: this repo is in warn mode. Files changed on this branch are still enforced."
    echo "Remove the mode file once the tree is clean."
  fi
  exit 0
}

# Test-only escape hatch, mirroring docs-lint.sh's own: a caller can
# `CHECK_SOURCE_ONLY=1 source <check>.sh` to load every function and pattern
# Narrative: lint-common.comments.md, block 9.
lint_source_only() {
  [ "${CHECK_SOURCE_ONLY:-0}" = "1" ]
}
