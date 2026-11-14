# test_ansible_gated_apply.py

Tests of ansible-gated-apply.yml, ansible-drift-check.yml and the ssh-check-hosts
action, run against the text of each step.

Each step's `run:` text runs under bash with `ssh`, `gh` and `timeout` stubbed on
PATH. ANSIBLE_GATED_APPLY_WORKFLOW, ANSIBLE_DRIFT_CHECK_WORKFLOW and
SSH_CHECK_HOSTS_ACTION name a broken copy for a sabotage run. After a change to
any of the three files, regenerate the snapshot with
`python3 tools/test_ansible_gated_apply.py --write-snapshot`. tools/sabotage.py
breaks each control and requires its test here to go red.
