#!/usr/bin/env bash
# Blocks inline operational values committed in place of a reference.
# Rule reference and rationale: tools/opv-lint-rules.md
#
# Scan/report/exemption machinery shared with docs-lint.sh lives in
# tools/lint-common.sh; this file owns only its own rule ids, patterns and
# messages.
#
# Unlike docs-lint's comment-only / fence-blanked scan, every OPV rule reads
# the whole file (raw_scannable): a leaked key, a literal secret assignment
# or a bare host address is exactly as real inside a pasted command, a YAML
# value or a table cell as it is inside a comment, and blanking any of those
# out would blank out the thing this check exists to catch.
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
# OPV001 — an AWS-style access key id literal
# ---------------------------------------------------------------------------
# `AKIA`/`ASIA` (long-term / temporary-session credential) plus exactly 16
# more base32-ish characters is a reserved, unambiguous AWS shape: nothing
# else is generated in that form, so this fires with no except pattern.
#
# Known limitation: a Hetzner Object Storage access key id has no comparable
# fixed prefix to key on, so a Hetzner-issued key id is not caught by shape
# here -- OPV002 catches it if it is committed the way this estate's runbooks
# actually commit one, as a direct assignment.
OPV001_MATCH="\\b(AKIA|ASIA)[A-Z0-9]{16}\\b"

# ---------------------------------------------------------------------------
# OPV002 — a secret/token assigned directly instead of via `read`
# ---------------------------------------------------------------------------
# The mandated pattern (CLAUDE.md's `## Replying`) is `read -rs VAR; export
# VAR`. This catches its opposite: a credential-shaped name on the left of an
# `=`, immediately followed by a quoted value that is not a variable
# reference. A bare `export VAR` (no `=`) is the correct form and never
# matches; `export VAR="$VAR"` and `export VAR="$(cmd)"` are re-exports of an
# already-read value and are excluded because the character right after the
# opening quote is `$`.
#
# Known limitation: this is `=`-assignment-shaped only. A value handed to a
# command positionally (`printf '...%s...' 'literal secret'`) has no `=` to
# anchor on. A token-by-token scan of a whole line to catch that shape was
# tried elsewhere and found to be the wrong design: an embedded apostrophe in
# the quoted value closes a naive quoted-token match early and lets most of
# the offending lines in a file through silently. That shape is deliberately
# left uncaught here rather than reproduced.
#
# Known limitation: a YAML `key: value` credential (a Pulumi config secret
# written in place rather than through `pulumi config set --secret`) is also
# not matched -- `:` was deliberately not added alongside `=` because a
# credential-name substring appears in plenty of non-secret YAML keys
# (`passwordHashAlgo: bcrypt`, `tokenTtlSeconds: 3600`), and a colon-based
# match would need a much narrower name list to avoid teaching people to
# ignore this rule the way DL009's own known limitations describe.
OPV002_CRED_NAME="[A-Za-z0-9_]*(SECRET|PASSWORD|PASSPHRASE|ACCESS_KEY|PRIVATE_KEY|TOKEN|CREDENTIAL|API_KEY|ENCRYPTIONSALT)[A-Za-z0-9_]*"
OPV002_MATCH="\\b(export[[:space:]]+)?${OPV002_CRED_NAME}[[:space:]]*=[[:space:]]*['\"][^'\"\$]"

# ---------------------------------------------------------------------------
# OPV003 — a bare IPv4 host address outside an obviously-public/example range
# ---------------------------------------------------------------------------
# Octets validated <=255, so a version-shaped string with an out-of-range
# component (`999.1.1.1`) is rejected rather than merely filtered, and a
# dotted 4-part string only ever matches when every component is a real
# octet.
OPV003_OCTET="(25[0-5]|2[0-4][0-9]|1[0-9]{2}|[1-9]?[0-9])"
OPV003_MATCH="\\b${OPV003_OCTET}\\.${OPV003_OCTET}\\.${OPV003_OCTET}\\.${OPV003_OCTET}\\b"
# Excluded as never a real host: loopback, unspecified, broadcast, the three
# IANA documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24),
# link-local/metadata (169.254.0.0/16), and the handful of fixed netmask
# literals that show up in networking config rather than as an address.
# Private ranges (10.x, 172.16-31.x, 192.168.x) are deliberately NOT
# excluded: they are real internal topology, which is exactly the class this
# rule exists to catch.
OPV003_EXCEPT="\\b127(\\.[0-9]{1,3}){3}\\b|\\b0\\.0\\.0\\.0\\b|\\b255\\.255\\.255\\.255\\b|\\b255\\.255\\.255\\.0\\b|\\b255\\.255\\.0\\.0\\b|\\b255\\.0\\.0\\.0\\b|\\b192\\.0\\.2\\.[0-9]{1,3}\\b|\\b198\\.51\\.100\\.[0-9]{1,3}\\b|\\b203\\.0\\.113\\.[0-9]{1,3}\\b|\\b169\\.254(\\.[0-9]{1,3}){2}\\b"

if lint_source_only; then
  return 0 2>/dev/null || exit 0
fi

# ---------------------------------------------------------------------------
# OPV000 — a suppression with no rule id or no reason (mirrors DL000)
# ---------------------------------------------------------------------------
check_malformed_suppression "OPV000" "$LINT_PREFIX" "OPV[0-9]{3}" "$ALL_SCANNABLE"

run_rule OPV001 "$ALL_SCANNABLE" "$OPV001_MATCH" "" raw_scannable \
  "committed AWS-style access key id; read it (read -rs VAR; export VAR), never commit the value"
run_rule OPV002 "$ALL_SCANNABLE" "$OPV002_MATCH" "" raw_scannable \
  "credential assigned directly instead of via read; use read -rs VAR; export VAR"
run_rule OPV003 "$ALL_SCANNABLE" "$OPV003_MATCH" "$OPV003_EXCEPT" raw_scannable \
  "committed host address; thread it through a lookup (e.g. hcloud server describe) or an env var, not a literal"

lint_summary_and_exit "opv-lint"
