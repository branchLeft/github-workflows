#!/usr/bin/env bash
# Checks the runner seam of the reusable merged-status workflow. The job takes
# its runner from the caller's runs-on input, and that input defaults to
# GitHub-hosted ubuntu-latest, so a caller that passes nothing runs as before.
#
# Text checks on exact lines: this repo's CI carries no YAML parser, and each
# assertion names the single line it expects.
#
# Usage: tools/merged-status-workflow.test.sh   (run from anywhere)

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WF="$HERE/../.github/workflows/merged-status.yml"

PASS=0
FAIL=0

# assert_line NAME LINE: exactly one line of the workflow equals LINE.
assert_line() {
  local name="$1" want="$2" n
  n=$(grep -cxF -e "$want" "$WF")
  if [ "$n" -eq 1 ]; then
    PASS=$((PASS + 1))
    echo "ok   $name"
  else
    FAIL=$((FAIL + 1))
    echo "FAIL $name: expected exactly one line '$want', found $n"
  fi
}

# The runs-on input block is the four lines after its key. The default must
# sit inside that block, not match a stray line elsewhere in the file.
input_block=$(grep -A3 -x -e '      runs-on:' "$WF")
if printf '%s\n' "$input_block" | grep -qxF -e '        default: '\''["ubuntu-latest"]'\'''; then
  PASS=$((PASS + 1))
  echo "ok   runs-on input defaults to ubuntu-latest"
else
  FAIL=$((FAIL + 1))
  echo "FAIL runs-on input defaults to ubuntu-latest: default line missing from its input block"
fi

if printf '%s\n' "$input_block" | grep -qxF -e '        type: string'; then
  PASS=$((PASS + 1))
  echo "ok   runs-on input is a string"
else
  FAIL=$((FAIL + 1))
  echo "FAIL runs-on input is a string: type line missing from its input block"
fi

assert_line "job takes its runner from the input" '    runs-on: ${{ fromJSON(inputs.runs-on) }}'

# Exactly one job-level runs-on, so a second hardcoded one cannot sit beside it.
job_runs_on=$(grep -c -e '^    runs-on:' "$WF")
if [ "$job_runs_on" -eq 1 ]; then
  PASS=$((PASS + 1))
  echo "ok   exactly one job-level runs-on"
else
  FAIL=$((FAIL + 1))
  echo "FAIL exactly one job-level runs-on: found $job_runs_on"
fi

# The mint step writes the App key and the installation token into the
# workspace. The trap removes them on failure too, so it must be in that step
# and set before the key is written.
trap_want="          trap 'rm -f key.pem token.json installations.json' EXIT"
assert_line "mint step clears its secrets on any exit" "$trap_want"
trap_line=$(grep -nxF -e "$trap_want" "$WF" | cut -d: -f1)
key_line=$(grep -nF -e '> key.pem' "$WF" | cut -d: -f1)
if [ -n "$trap_line" ] && [ -n "$key_line" ] && [ "$trap_line" -lt "$key_line" ]; then
  PASS=$((PASS + 1))
  echo "ok   secrets trap is set before the key is written"
else
  FAIL=$((FAIL + 1))
  echo "FAIL secrets trap is set before the key is written: trap line '${trap_line:-none}', key line '${key_line:-none}'"
fi

echo "merged-status workflow seam: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
