# Fixture for the ansible-gated-apply self-test

A tiny Ansible project that `ansible-gated-apply-self.yml` passes to the
reusable workflow as its `working-directory`. It holds one role with a
container-only Molecule scenario, the lint configuration the workflow expects at
the project root, the pinned tool lists, and `stub/host.sh`, the stand-in for a
host in the plan job's dry run. Nothing here is delivered to a host.

The pins in `ansible/requirements.txt` and `ansible/requirements.yml` are a copy
of the pins a calling repository keeps. They are bumped here by hand when the
workflow's tool set changes, and Dependabot does not read them.
