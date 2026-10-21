# The role that must fail the lint

`roles/lint_fail/tasks/main.yml` holds one task with no name. ansible-lint at the
production profile reports exactly one violation for it:

- Rule id: `name[missing]`
- Exit status of ansible-lint: `2`
- Summary line: `Failed: 1 failure(s), 0 warning(s)`

The self-test job `lint-expected-fail` in `ansible-gated-apply-self.yml` requires
all three. The tree shares the `.ansible-lint` and `.yamllint` of the fixture root
and carries no copy. Its YAML passes yamllint, so the one violation is the only
reason the lint fails. The job proves that ansible-lint, run with the reusable
workflow's own flags, rejects bad input. It does not prove yamllint, mypy or ruff,
and it does not prove that the reusable `lint` job is wired into the gate: the
sabotage of the real fixture proves that.

Do not fix the task. If the rule id changes after an upgrade of the lock, change
this file, the job and the test that compares them in the same pull request.
