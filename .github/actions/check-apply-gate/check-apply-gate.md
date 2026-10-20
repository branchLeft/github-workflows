# check-apply-gate

`check-apply-gate.py` checks, in a calling repo's own CI, that a workflow
keeps the contract of the no-op deploy gate. The gate skips an apply job, and
the approval it would ask for, when the plan-has-changes action reports a plan
that changes nothing. The decision is `plan-has-changes`; this is the check
that the caller wired it up so the skip can only happen on that verdict.

It reads one workflow file the caller names and nothing else. It cannot see a
workflow it is not given, so it does not police callers: each caller's CI
invokes it on its own file, and a caller that does not invoke it is not checked.

## The contract

For each `PLAN_JOB:APPLY_JOB` pair, the apply job:

- needs the plan job (`GATE-NEEDS`);
- has an `if:` (`GATE-IF-MISSING`) with no `||` outside a quoted string
  (`GATE-IF-OR`), because an `||` lets one operand alone start the job;
- has no `always()`, `failure()` or `cancelled()` in it (`GATE-IF-STATUS`),
  because those run the job when the plan job failed or was skipped, and an
  unset verdict then reads as "ask";
- has exactly one operand that mentions `has_changes`, and it is exactly
  `needs.<plan>.outputs.has_changes != 'false'` as its own top-level `&&`
  operand (`GATE-IF-OPERAND`). It skips only on an explicit `'false'`: an unset
  output, a negation or a test for `'true'` all fail.

The plan job:

- publishes the job output `has_changes` as exactly
  `${{ steps.<verdict-step>.outputs.has_changes }}` (`GATE-OUTPUT`);
- has exactly one step that uses the plan-has-changes action (`GATE-ACTION`);
- has exactly one step with the id given as `verdict-step` (`GATE-VERDICT-STEP`),
  and it runs `if: always()` (`GATE-VERDICT-ALWAYS`), so a verdict is written
  whatever an earlier step did;
- if the verdict step is not the action step, a non-comment line under that
  step's own `env:` binds the action's `has_changes` output to a variable, and a
  non-comment line reads that variable as `$NAME` or `${NAME}`
  (`GATE-VERDICT-SOURCE`). A binding under `with:` or anywhere else does not
  count. That proves the output is bound and read, not what the step does with
  it: that is the caller's own test;
- if `summary-step` is given, the verdict step comes before the step of that
  name, so the verdict is the first line of the summary (`GATE-ORDER`).

The saved plan, named by `plan-file`, can hold resource inputs and ciphertext, so
for every listed plan job:

- exactly one step has the name given as `save-step` (default `Save the update
  plan for the no-change check`), it sets `continue-on-error: true`, and it runs
  `--save-plan <plan-file> > /dev/null` once: the redirect comes straight after
  the file and nothing follows it on that command, so `2> /dev/null` and
  `> /dev/null; echo done` both fail, and nothing else in the step names the
  file (`GATE-PLAN-SAVE`);
- the step that uses the plan-has-changes action is given `plan-file: <plan-file>`
  (`GATE-PLAN-INPUT`);
- no other step names the plan file, saves a plan, uploads an artifact, or globs
  over the working directory's JSON files (`GATE-PLAN-READ`), and no job that is
  not a listed plan job names the plan file or saves a plan. The plan file is
  matched by its stem, so `preview-plan`, `preview-plan.js` and `preview-plan*`
  are caught as well as `preview-plan.json`. The one exemption is a step that
  `uses` this check (`check-apply-gate@`) in a job that is not a plan job, because
  it has to pass the plan file's name on; any other step in that job is read as
  before. Comment lines are not code and are not read. The globs it knows are
  `*.json` and `preview*`; a spelling it does not know passes, so this is a floor,
  not a proof;
- the workflow as a whole does not mention `PULUMI_EXPERIMENTAL`
  (`GATE-PLAN-EXPERIMENTAL`), so the saved plan does not depend on the
  experimental switch;
- the workflow as a whole does not use `--show-secrets` (`GATE-PLAN-SHOW-SECRETS`),
  which writes plaintext secrets into the saved plan.

A workflow this script cannot read (no `jobs:`, a job listed twice, a malformed
pair, a missing file) fails with `GATE-UNREADABLE`. Nothing it does not
understand passes.

## What stays the caller's

These are about one repo's own jobs, so each repo keeps its own test for them:
which jobs are gated applies at all and that a new one gets this check; the
other operands of an apply `if:` (the push and main operands, the template
guard); an apply job's other `needs`; the plan job's own `if:`, environment,
permissions and credentials; the exact command a repo's save step runs beyond
its `--save-plan` and `/dev/null`, and the command that produces `preview.json`;
and what a caller does with the verdict when it combines it with another check.
Nothing the check holds is kept in a caller's test as well.

## Limits

It reads text, line by line, for the YAML a workflow file in this org uses: job
keys at two spaces, job fields at four, steps at six. It reads scalar, flow-list
and block-list `needs`, and plain, quoted, folded and literal `if:` values. It is
not a YAML parser: anchors, merge keys and flow-style jobs are not read, and a
job written that way fails as unreadable or as the finding it hides, never as a
pass. A comment is cut at the first `#` that follows a space or tab, found with a
plain scan, so a very long line costs one pass.

**Run it on `pull_request` and make it a required check.** The plan jobs run only
on a push to main, so a check wired only to push finds a broken gate after it has
merged.

## Tests

`test_check_apply_gate.py` holds a passing fixture for each shape of caller and
one-edit mutations of it for each code. The self-test workflow also runs the
action over `fixtures/`: the passing fixtures must pass, and a failing one must
fail the step.
