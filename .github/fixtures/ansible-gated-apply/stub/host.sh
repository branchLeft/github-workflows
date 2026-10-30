#!/usr/bin/env bash
# The stand-in for a host in the dry run of the plan job. It receives the SSH
# destination and the commit, answers in the format of the host's trigger in
# check mode, and prints a diff on standard error. It reaches nothing.
set -euo pipefail

dest="$1"
commit="$2"

case "$dest" in
  # A host that answers with the line of the push receiver although it is pulled
  # from: the judge must refuse it (the self-test of the action).
  *wrong-line*)
    printf 'push-receiver verdict=unchanged exit=0 reason=ok mode=check commit=%s playbook=ansible/playbooks/stub.yml changed=0\n' "$commit"
    exit 0
    ;;
  *first-stub*)
    printf 'pull-trigger verdict=unchanged exit=0 reason=ok mode=check commit=%s playbook=ansible/playbooks/stub.yml changed=0\n' "$commit"
    exit 0
    ;;
  *)
    printf 'pull-trigger verdict=would-change exit=11 reason=ok mode=check commit=%s playbook=ansible/playbooks/stub.yml changed=1\n' "$commit"
    printf -- '--- before\n+++ after\n@@ -1 +1 @@\n-greeting = OLD_VALUE\n+greeting = NEW_VALUE\n' >&2
    exit 11
    ;;
esac
