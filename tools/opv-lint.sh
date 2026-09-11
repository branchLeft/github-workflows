#!/usr/bin/env bash
# Blocks inline operational values committed in place of a reference.
# Rule reference and rationale: tools/opv-lint-rules.md
#
# Scan/report/exemption machinery shared with docs-lint.sh lives in
# tools/lint-common.sh; this file owns only its own rule ids, patterns and
# messages.
#
# Unlike docs-lint's comment-only / fence-blanked scan, OPV001/OPV003/OPV004
# read the whole file (raw_scannable): a leaked key, a literal secret
# assignment or a bare host address is exactly as real inside a pasted
# command, a YAML value or a table cell as it is inside a comment, and
# blanking any of those out would blank out the thing this check exists to
# catch. OPV002 is the deliberate exception -- an unsubstituted placeholder
# is only a defect inside a copy-pasteable command, so it reads ONLY
# bash/sh/shell/console fences in markdown (command_fence_scannable).
#
# No runtime dependencies beyond bash + grep + awk, matching docs-lint.sh's
# own constraint.
#
# Usage:
#   opv-lint.sh [--explain] [--mode warn|enforce] [FILE...]
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
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --) shift; while [ $# -gt 0 ]; do FILES_FROM_ARGS+=("$1"); shift; done ;;
    -*) echo "opv-lint: unknown option $1" >&2; exit 2 ;;
    *) FILES_FROM_ARGS+=("$1"); shift ;;
  esac
done

# BASH_SOURCE, not $0: this file is sourced directly by opv-lint.test.sh
# (OPV_LINT_SOURCE_ONLY=1 source opv-lint.sh) as well as executed, and $0
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

LINT_IGNOREFILE=".opv-lintignore"
LINT_PREFIX="opv-disable-next-line"
LINT_FAILMSG="Failing: see tools/opv-lint-rules.md for each rule and how to fix it."

lint_resolve_mode ".opv-lint.mode" "$MODE_OVERRIDE" || exit $?
lint_classify_files
cat "$MD_FILES" "$CODE_FILES" > "$TMPDIR_LINT/all_scannable"
ALL_SCANNABLE="$TMPDIR_LINT/all_scannable"

# Test-only escape hatch, mirroring docs-lint.sh's own: `OPV_LINT_SOURCE_ONLY=1
# source opv-lint.sh` runs everything above and returns before any file is
# scanned, so opv-lint.test.sh can exercise the real pattern constants
# instead of a copy that could drift from them.
CHECK_SOURCE_ONLY="${OPV_LINT_SOURCE_ONLY:-0}"

# ---------------------------------------------------------------------------
# OPV001 — a committed concrete operational value: an IPv4 literal/private
# subnet (outside loopback/documentation/link-local/four netmask literals),
# or one of the estate's fixed operational hostnames (edge1/app1/db1/mx1,
# word-bounded). Two shapes, reported under one rule id via two run_rule
# calls below. Private ranges, public constants and CIDR prose are
# deliberately NOT excluded. Full rationale and known limitations:
# tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
OPV001_OCTET="(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
OPV001_ADDR_MATCH="\\b${OPV001_OCTET}\\.${OPV001_OCTET}\\.${OPV001_OCTET}\\.${OPV001_OCTET}\\b"
OPV001_ADDR_EXCEPT="\\b127(\\.[0-9]{1,3}){3}\\b|\\b0\\.0\\.0\\.0\\b|\\b255\\.255\\.255\\.255\\b|\\b255\\.255\\.255\\.0\\b|\\b255\\.255\\.0\\.0\\b|\\b255\\.0\\.0\\.0\\b|\\b192\\.0\\.2\\.[0-9]{1,3}\\b|\\b198\\.51\\.100\\.[0-9]{1,3}\\b|\\b203\\.0\\.113\\.[0-9]{1,3}\\b|\\b169\\.254(\\.[0-9]{1,3}){2}\\b"
OPV001_HOST_MATCH="\\b(edge1|app1|db1|mx1)\\b"

# ---------------------------------------------------------------------------
# OPV002 — an unsubstituted placeholder in a copy-pasteable command. Scoped
# to bash/sh/shell/console fences ONLY (command_fence_scannable) -- the
# opposite scope from OPV001/OPV003/OPV004. A placeholder is a lowercase
# token with a hyphen or dot (`<edge1-ipv4>`), or a bare word from a fixed
# list (`<host>`, `<secret>`, ...). The angle brackets must run right up
# against the token with no space inside, which is what excludes shell
# redirection/comparison without a shell parser; a heredoc opener has no
# closing `>` so it can never match either shape. Full rationale and known
# limitations: tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
OPV002_HYPHEN_MATCH="<[a-z0-9]+([.-][a-z0-9]+)+>"
OPV002_BARE_WORDS="host|domain|secret|password|passphrase|token|key|value|ip|uuid|id|salt|region|hostname|address|url|email|port|username|repo|slug"
OPV002_BARE_MATCH="<(${OPV002_BARE_WORDS})>"

# ---------------------------------------------------------------------------
# OPV003 — an AWS-style access key id literal. `AKIA`/`ASIA` + 16 more chars
# is a reserved, unambiguous shape, so this needs no except pattern. Known
# limitations (Hetzner has no comparable shape): tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
OPV003_MATCH="\\b(AKIA|ASIA)[A-Z0-9]{16}\\b"

# ---------------------------------------------------------------------------
# OPV004 — a credential-shaped variable assigned a literal via `=` instead of
# the mandated `read -rs VAR; export VAR`. Case-insensitive (camelCase/
# snake_case are the idiom in .py/.js/.ts). `export VAR="$VAR"` and
# `export VAR="$(cmd)"` are excluded as pure re-exports, and a fixed set of
# non-secret-shaped literals (true/false/yes/no/on/off/enabled/disabled) is
# excluded too -- but `${VAR:-a-literal-default}` is NOT excluded: a default
# expansion can still carry a real fallback secret. Known limitations
# (assignment-shaped only, no YAML `key:` form, the boolean list is fixed
# and small): tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
OPV004_CRED_NAME="[A-Za-z0-9_]*(SECRET|PASSWORD|PASSPHRASE|ACCESS_KEY|PRIVATE_KEY|TOKEN|CREDENTIAL|API_KEY|ENCRYPTIONSALT)[A-Za-z0-9_]*"
OPV004_MATCH="\\b(export[[:space:]]+)?${OPV004_CRED_NAME}[[:space:]]*=[[:space:]]*['\"][^'\"]*['\"]"
# The except pattern mirrors MATCH's own prefix (same credential name, same
# `=`) so it only ever blanks a safe VALUE, never an unrelated quoted token
# elsewhere on the line. A pure re-export's value is exactly `$IDENT`,
# `${IDENT}` or `$(...)` inside double quotes and nothing else -- `${VAR:-x}`
# does not fit this shape (no `:`/`-`/`=`/`+` operator is allowed between the
# identifier and the closing brace), so it falls through to MATCH unblanked.
OPV004_SAFE="\\b(export[[:space:]]+)?${OPV004_CRED_NAME}[[:space:]]*=[[:space:]]*(\"\\\$(\\{[A-Za-z_][A-Za-z0-9_]*\\}|[A-Za-z_][A-Za-z0-9_]*|\\([^\"]*\\))\"|['\"](true|false|yes|no|on|off|enabled|disabled)['\"])"

if lint_source_only; then
  return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# OPV000 — a suppression with no rule id or no reason (mirrors DL000)
# ---------------------------------------------------------------------------
check_malformed_suppression "OPV000" "$LINT_PREFIX" "OPV[0-9]{3}" "$ALL_SCANNABLE"

run_rule OPV001 "$ALL_SCANNABLE" "$OPV001_ADDR_MATCH" "$OPV001_ADDR_EXCEPT" raw_scannable \
  "committed host address; thread it through a lookup (e.g. hcloud server describe) or an env var, not a literal"
run_rule OPV001 "$ALL_SCANNABLE" "$OPV001_HOST_MATCH" "" raw_scannable \
  "committed fixed operational hostname; thread it through a lookup or an env var, not a literal"
run_rule OPV002 "$MD_FILES" "$OPV002_HYPHEN_MATCH" "" command_fence_scannable \
  "unsubstituted placeholder in a copy-pasteable command; resolve it before committing, or restructure the step as a lookup"
run_rule OPV002 "$MD_FILES" "$OPV002_BARE_MATCH" "" command_fence_scannable \
  "unsubstituted placeholder in a copy-pasteable command; resolve it before committing, or restructure the step as a lookup"
run_rule OPV003 "$ALL_SCANNABLE" "$OPV003_MATCH" "" raw_scannable \
  "committed AWS-style access key id; read it (read -rs VAR; export VAR), never commit the value"
run_rule OPV004 "$ALL_SCANNABLE" "$OPV004_MATCH" "$OPV004_SAFE" raw_scannable \
  "credential assigned directly instead of via read; use read -rs VAR; export VAR" "" 1

lint_summary_and_exit "opv-lint"
