#!/usr/bin/env bash
# The stand-in for the receiver of a pushed host, in the dry run of the plan job
# and in the self-test of the action. It receives the SSH destination, the mode,
# the commit and the playbook as arguments and a git bundle on standard input, as
# the real receiver does. It verifies the bundle with git into an empty scratch
# repository, requires the bundle's HEAD to be the commit it was asked about, and
# answers in the format of the receiver's summary line. It applies nothing and
# reaches nothing.
set -euo pipefail

dest="$1"
mode="$2"
commit="$3"
playbook="$4"

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

say() {
  # verdict, exit status, reason, changed
  printf 'push-receiver verdict=%s exit=%s reason=%s mode=%s commit=%s playbook=%s changed=%s\n' "$1" "$2" "$3" "$mode" "$commit" "$playbook" "$4"
}

cat > "$scratch/in.bundle"
git init -q "$scratch/repo" > /dev/null 2>&1
if ! git -C "$scratch/repo" bundle verify "$scratch/in.bundle" > /dev/null 2>&1; then
  say refused 20 bad-request 0
  exit 20
fi
if ! git -C "$scratch/repo" fetch -q "$scratch/in.bundle" HEAD > /dev/null 2>&1; then
  say refused 20 bad-request 0
  exit 20
fi
if [ "$(git -C "$scratch/repo" rev-parse FETCH_HEAD)" != "$commit" ]; then
  say refused 20 bad-request 0
  exit 20
fi

case "$dest" in
  # A receiver that answers with the line of the pull trigger: the judge must
  # refuse it, because a pushed host speaks the push receiver's line only.
  *wrong-line*)
    printf 'pull-trigger verdict=unchanged exit=0 reason=ok mode=%s commit=%s playbook=%s changed=0\n' "$mode" "$commit" "$playbook"
    exit 0
    ;;
  *first-stub*)
    say unchanged 0 ok 0
    exit 0
    ;;
  *)
    if [ "$mode" = apply ]; then
      say applied 10 ok 1
      exit 10
    fi
    say would-change 11 ok 1
    printf -- '--- before\n+++ after\n@@ -1 +1 @@\n-greeting = OLD_VALUE\n+greeting = NEW_VALUE\n' >&2
    exit 11
    ;;
esac
