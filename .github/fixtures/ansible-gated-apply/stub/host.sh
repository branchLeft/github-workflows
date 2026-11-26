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
  # A host whose playbook fails, and whose output matches a secret pattern: the
  # output is withheld, and no artifact holds it (the self-test of the action).
  *secret-fail-stub*)
    printf 'pull-trigger verdict=error exit=3 reason=ok mode=check commit=%s playbook=ansible/playbooks/stub.yml changed=-\n' "$commit"
    printf 'PLAY [all]\nTASK [role : Render the config] ****\nok: [host]\nTASK [role : Write the file] ****\nfatal: [host]: FAILED! => {"msg": "api_key = stub-scan-hit-0001"}\n' >&2
    exit 3
    ;;
  # A host whose playbook fails with output that is clean: the output is shown, and
  # published to the run as an artifact (the self-test of the action).
  *fail-stub*)
    printf 'pull-trigger verdict=error exit=3 reason=ok mode=check commit=%s playbook=ansible/playbooks/stub.yml changed=-\n' "$commit"
    printf 'PLAY [all]\nTASK [role : Install the package] ****\nok: [host]\nTASK [role : Apply the stub] ****\nfatal: [host]: FAILED! => {"msg": "stub-visible-failure"}\n' >&2
    exit 3
    ;;
  # A host whose role refuses, with a task and a bounded refusal message shaped like the
  # demo1 refusal of a secret-bearing file (the self-test of the action). The text is clean.
  *demo1-stub*)
    printf 'pull-trigger verdict=error exit=3 reason=ok mode=check commit=%s playbook=ansible/playbooks/stub.yml changed=-\n' "$commit"
    printf 'PLAY [all]\nTASK [branchleft.demo1_build : Refuse a host that holds a secret-bearing file the holder list does not name] ****\nfatal: [host]: FAILED! => {"changed": false, "msg": "Refused: a secret-bearing file on this host is not named on the holder list (holder list has 10 row(s))."}\n' >&2
    exit 3
    ;;
  *)
    printf 'pull-trigger verdict=would-change exit=11 reason=ok mode=check commit=%s playbook=ansible/playbooks/stub.yml changed=1\n' "$commit"
    printf -- '--- before\n+++ after\n@@ -1 +1 @@\n-greeting = OLD_VALUE\n+greeting = NEW_VALUE\n' >&2
    exit 11
    ;;
esac
