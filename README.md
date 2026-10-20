# github-workflows

Shared, parameterized reusable GitHub Actions workflows for repos owned by
the `branchLeft` org. One workflow lives here per reusable capability; each
target repo keeps a thin caller workflow that just points at the pinned
version here.

## Why a separate repo

Keeping the actual logic in one place means a fix or improvement lands once
and every caller repo picks it up by bumping a tag, instead of copy-pasted
YAML drifting out of sync across `shared-infra`, `ghost-platform`,
`ghost-platform-docs`, `website`, `components`, and whatever comes next.

## Versioning

Callers should reference an exact tag (`@v1.0.6`), not `@main` — `main` is
the dev branch here and can change without warning. Tags are immutable
org-wide (a ruleset blocks moving or deleting them), so there's no
moving-`@v1`-forward convention — every change, including fixes, ships as a
new patch/minor tag:

```bash
git tag -s v1.0.3 -m "v1.0.3: <what changed>" <commit>
git push origin v1.0.3
```

The tag ruleset requires signatures, so `-s` rather than `-a`.

Bumping every caller means a one-line PR per repo (`@v1.0.5` → `@v1.0.6` in
each caller workflow) rather than a single silent update — more PR noise, but
every caller's history shows exactly which version it's on and when it
changed.

A workflow here must never hardcode a tag of this repo internally. Tags are
immutable, so a literal ref inside a workflow pins callers to a revision that
has no relationship to the one they asked for — `docs-lint.yml` uses
`github.job_workflow_sha` to load its own rules from the exact commit the
caller resolved.

## Workflows

### `docs-lint.yml`

Enforces the mechanical parts of the org documentation standard over markdown
and code comments. Rules, suppression syntax and rationale:
[`tools/docs-lint-rules.md`](tools/docs-lint-rules.md).

**Caller usage** — add to the target repo as
`.github/workflows/docs-lint.yml`:

```yaml
name: docs-lint

on:
  pull_request:
  push:
    branches: [main]

jobs:
  docs-lint:
    uses: branchLeft/github-workflows/.github/workflows/docs-lint.yml@v1.0.6
```

No secrets, no write permission, no per-repo allow-list change — the job is
`actions/checkout` plus shell.

**Per-repo configuration** is by file, not by workflow input, so the same
caller block works everywhere:

- `.docs-lint.mode` — absent means enforce. A repo whose existing docs are not
  clean yet commits one containing `warn`, which makes the full-tree scan
  advisory while still failing on files the branch touched. Deleting the file
  is the flip to enforce.
- `.docs-lintignore` — tab-separated `glob`, rule ids, reason. Use it for
  files that legitimately match a rule, such as a document about the rules.

Adopting the gate in a repo that has never run it is therefore a two-file
change, and the ratchet means the first PR is green.

### `merged-status.yml`

Records on a GitHub Projects board that a pull request's work has landed,
once **every** pull request naming the same issue has stopped being open. A
merge is evidence about one edge: an issue routinely spans several pull
requests in several repos, so the question is re-asked of all of them before
anything is written. Logic and rationale:
[`tools/merged-status.py`](tools/merged-status.py).

Unlike the lint workflows this one holds a credential. `GITHUB_TOKEN` cannot
read an organisation project at all — `projectV2` answers `NOT_FOUND` — so the
caller passes a GitHub App id and private key, and the job mints a short-lived
installation token with `openssl` and `curl`. No third-party action is used
for it, so the job still passes under `allowed_actions: selected`.

**Caller usage** — add to the target repo as
`.github/workflows/merged-status.yml`:

```yaml
name: merged-status

on:
  pull_request:
    types: [closed]

jobs:
  merged-status:
    if: github.event.pull_request.merged == true
    uses: branchLeft/github-workflows/.github/workflows/merged-status.yml@vX.Y.Z
    with:
      link-kinds: closing+refs
      from-statuses: In progress,In review
      projects-app-id: ${{ vars.BRANCHLEFT_PROJECTS_APP_ID }}
    secrets:
      projects_app_key: ${{ secrets.BRANCHLEFT_PROJECTS_APP_KEY }}
```

Organisation-level Actions secrets are **not** delivered to a private repo's
runner on the free plan — measured, with `GITHUB_TOKEN` arriving beside an
empty organisation secret in the same step. Set both at repository level.

**`link-kinds` and `from-statuses` are required and have no default here.**
Together they are the only place this workflow answers what counts as
delivery, and the reason both exist is that the first cannot answer it alone.
A repo that delivers some paths by hand has every reason to require the
**non-closing** `Refs` spelling on exactly the pull requests that did the
delivering, so that the merge cannot close an issue whose work is not yet
live; there, `link-kinds: closing` writes nothing, ever. `from-statuses`
carries the discrimination instead — an unstarted item in a backlog column is
not completed by a merge that mentioned it in passing, whatever keyword did
the mentioning.

An unrecognised or empty value for either is refused rather than narrowed,
because a typo that quietly wrote nothing would look exactly like a board with
nothing to write.

**Both trailer spellings are read**: the plain `Refs org/repo#12` and the
markdown hyperlink `Refs [ISSUE repo#12](https://github.com/org/repo/issues/12)`.
The second is not decoration — a repo may mandate it so a reader can tell an
issue from a pull request, which share a number space and redirect to each
other. The target is taken from the **URL**, never the link text, because the
text routinely omits the owner and reading it would resolve a cross-repo
reference against the wrong repository. Fenced blocks and quoted lines are not
read at all: a quoted `Closes #123` is a report of what something else said.

An **open** cross-referencing pull request blocks the write even when this
workflow cannot find a trailer in its body — an unparseable reference is the
absence of evidence, not evidence of absence. A landed one is held to the
trailer. Both rules fail towards not writing.

### Pinning, and the one thing that will bite an adopter

The job resolves its own revision by grepping the **caller's** checked-out
`.github/workflows/` for the pin, because nothing the runner exposes names it.
It requires exactly one distinct match and refuses on none or two.

**That grep is textual. A second pin in a comment counts.** Pasting the caller
block above into a repo that already has a real caller — or leaving an old pin
commented out beside a new one — produces `found 2` and the job refuses. Keep
exactly one occurrence of the string `merged-status.yml@` in that directory.

### `opv-lint.yml`

Blocks a committed operational value in place of the reference the org
convention mandates: a concrete address or fixed hostname, an unsubstituted
placeholder in a copy-pasteable command, an access key id, or a credential
assigned directly instead of via `read`. Rules, suppression syntax and
rationale: [`tools/opv-lint-rules.md`](tools/opv-lint-rules.md). Shares its
scan/report/exemption machinery with `docs-lint.yml`
([`tools/lint-common.sh`](tools/lint-common.sh)) rather than a second
implementation of the same mechanism.

**Caller usage** — add to the target repo as
`.github/workflows/opv-lint.yml`, pinned to the tag cut once this workflow
first ships (see `git tag -l` in this repo for the current one — `opv-lint.yml`
is not in any tag yet, so `@v1.0.6` above would 404 if copied verbatim today):

```yaml
name: opv-lint

on:
  pull_request:
  push:
    branches: [main]

jobs:
  opv-lint:
    uses: branchLeft/github-workflows/.github/workflows/opv-lint.yml@vX.Y.Z
```

No secrets, no write permission, no per-repo allow-list change.

**Per-repo configuration**, same shape as docs-lint:

- `.opv-lint.mode` — absent means enforce; a repo adopting the gate against
  existing violations commits one containing `warn` to ratchet in, exactly
  as `.docs-lint.mode` does.
- `.opv-lintignore` — tab-separated `glob`, rule ids, reason, for a file
  whose entire purpose is to document or test the pattern this check would
  otherwise flag.

This is the guard, not a sweep: adopting it in a repo carrying existing
violations is expected to turn CI red (or advisory, in warn mode) on the
instances already there, fixed caller by caller rather than in bulk.

### `ansible-gated-apply.yml`

The CI for a repository whose Ansible roles reach hosts by the gated pull
(ANS-17): lint, Molecule, a check-mode plan of the exact commit on each host, and
an apply that waits for the deployment gate. The standard it applies is
`docs/stacks/ansible.md` in `branchLeft/standards` (ANS-4, ANS-5, ANS-6, ANS-10,
ANS-15, ANS-16, ANS-17) and `CI-7`, `CI-12`, `CI-13`.

**Nothing here holds a root shell.** The only host access is an SSH key whose
line in `authorized_keys` runs one forced command that takes one commit id. The
plan job holds the check key and the apply job holds the apply key. The host,
not this workflow, decides what that commit may do: it refuses a commit that is
not on its `main`, is older than the last applied, or is not signed by an
allowed key.

#### The jobs, and what each may do

| Job | Runs when | May do | Holds |
| --- | --- | --- | --- |
| `Discover the Ansible checks` | every run | validate the inputs, decide whether the run may reach a host, list the Molecule shards | nothing |
| `Lint the Ansible inputs` | every run | yamllint, ansible-lint (production profile, `--strict`), mypy, ruff | nothing |
| `Molecule: <role>` | every run, one per role | `molecule test` (with the idempotence step) and `molecule check` on containers on the runner | nothing; fails if `DOCKER_HOST` is set (ANS-15) |
| `Plan the apply (check mode on each host)` | a push to the default branch of the repository itself | ask each host for a check-mode run of this commit, show the diff, decide `has_changes` | the check key |
| `Apply the planned commit (after approval)` | the same push, the plan succeeded, `has_changes` is not `false`, not a dry run | ask each host to apply the planned commit, then a second time to prove nothing is left | the apply key, the environment's approval |
| `Ansible checks passed` | every run, even after a failure | go red unless every job ended as expected for this kind of run | nothing |

A pull request runs the first three and the gate only. The plan cannot run on a
pull request: the host takes only a commit that is on its `main`.

The apply is skipped, visibly, when there is nothing to change: the first line of
the plan's job summary says so and no approval is requested. `has_changes` is
`false` only when every host answered with exit status 0, the summary line
`verdict=unchanged` and `changed=0` for this commit. Anything else is `true`, and
a host that failed, refused, did not answer, or gave an answer that could not be
read fails the plan job. A task that cannot run in check mode must make the host
report a change (ANS-10); that is the role's job, and this workflow believes the
host's count.

The apply job refuses unless all of these hold, and each is a step that fails the
job, not only a condition: it is a push, to the default branch, not in a fork,
not a dry run; the environment input is not empty; the commit is the one that was
planned; the hosts are the hosts that were planned (a digest of the list); and,
after the approval, the default branch head is still that commit. A newer push
has its own plan and its own approval.

#### How a repository calls it

Add one caller, pinned to an exact tag (see Versioning). The workflow is not in
any tag yet, so use the tag cut once it ships.

```yaml
name: ansible

on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read

jobs:
  ansible:
    uses: branchLeft/github-workflows/.github/workflows/ansible-gated-apply.yml@vX.Y.Z
    with:
      environment: production
    secrets:
      ssh_hosts: ${{ secrets.ANSIBLE_HOSTS }}
      ssh_known_hosts: ${{ secrets.ANSIBLE_KNOWN_HOSTS }}
      ssh_check_key: ${{ secrets.ANSIBLE_CHECK_KEY }}
```

The caller does not pass the apply key. The required status check is the gate
job, shown on the caller as `ansible / Ansible checks passed`.

| Input | Default | Meaning |
| --- | --- | --- |
| `environment` | required | The deployment environment the apply job runs in. Must not be empty. |
| `working-directory` | `.` | Directory holding the Ansible directory, `.ansible-lint` and `.yamllint`. |
| `ansible-directory` | `ansible` | Directory, relative to `working-directory`, holding `requirements.txt`, `requirements.yml` and `pyproject.toml`. |
| `role-directories` | `roles/*` | Globs, one per line, relative to `ansible-directory`; each match must hold `molecule/default/molecule.yml`. A repository with patterns too lists `patterns/*`. |
| `python-paths` | `.` | Paths mypy and ruff check, relative to `ansible-directory`. Narrow it if a role keeps two modules of one name. |
| `dry-run` | `false` | Replace the SSH call with `dry-run-stub` and never run the apply job. It can only skip an apply, never perform one. |
| `dry-run-stub` | empty | With `dry-run`, the executable, relative to `working-directory`, that stands in for a host. |
| `host-timeout-minutes` | 65 | One host call is stopped after this long (the host's own limit is one hour by default). |
| `plan-timeout-minutes` | 90 | Bound of the plan job. |
| `apply-timeout-minutes` | 180 | Bound of the apply job, not counting the wait for approval. |

| Secret | Passed by | Meaning |
| --- | --- | --- |
| `ssh_hosts` | the caller | One `user@host` per line. A secret so the job log masks the names; the job summary shows `host 1`, `host 2` in this order. |
| `ssh_known_hosts` | the caller | The hosts' public keys in `known_hosts` form. Host key checking is on and the global file is not read. |
| `ssh_check_key` | the caller | Private key of the plan job. On the host it may run the forced command in check mode only. |
| `ssh_apply_key` | not passed: an environment secret | Private key of the apply job. It is read because the apply job runs in the environment, so a job that has not passed the approval cannot read it. |

#### Setup the owner does once per repository

These are settings and keys, not a script, and the commands belong in the
runbook in `ghost-platform-docs`, not here.

1. A deployment environment with the name the caller passes: required reviewers
   set to the owner, self-review not allowed, and deployment branches limited to
   the default branch.
2. The apply key as a secret of that environment, and the check key, the host
   list and the known-hosts lines as repository secrets.
3. On each host, the `authorized_keys` lines for the two keys, one with
   `--mode check` and one with `--mode apply` in the forced command, restricted
   to the runners' source and with no shell, forwarding or terminal. The line and
   the sudo variant are described in the `pull_trigger` role's README in
   `branchLeft/shared-infra`.
4. The caller workflow above, and the gate job as a required status check.

#### What it assumes of the host (the `pull_trigger` role)

The host side is a separate piece of work and the interface below is read from
the role's README as written for its pull request, not from a release. If that
README changes, so does this list.

- The remote command is the 40-digit commit id and nothing else; the mode is
  fixed by the key's forced command.
- Standard output is one line: `pull-trigger verdict=V exit=N reason=R mode=M
  commit=SHA playbook=P changed=C`. Standard error is the playbook's output,
  with `--diff` in check mode.
- The exit status is 0 (unchanged), 10 (applied), 11 (check mode would change),
  20, 21, 30, 31 or 40 (refused, busy, failed, timed out, error). A second request
  of the same commit exits 0 when the apply left nothing to change.

#### Failure modes

| Seen as | Cause | What to do |
| --- | --- | --- |
| Plan job red, `Plan failed:` first line | A host refused, was busy, failed, timed out, did not answer, or answered with a line this workflow cannot read or for another commit | Read the row for that host in the job summary, then the host's log. No approval was requested. |
| Plan row says the diff matched a secret pattern | A line of the diff looks like a credential, or holds the check key | Fix the task with `no_log: true` and `diff: false` (ANS-16). The diff is withheld and the job fails. |
| Plan row says the diff is too large | More than 300 KB for a host or 900 KB in all | Split the change. The approver must be able to read the whole plan. |
| Apply job red, "moved on" | The default branch changed during the wait | Nothing to do: the newer push has its own plan and approval. |
| Apply job red, "second run" | The apply left something to change, so the role is not idempotent (ANS-11) | Fix the role. The host is already at the applied state. |
| Apply job red on one host | Hosts after it were not touched; hosts before it were applied | Read the host's log, fix forward with a new commit. |
| Gate job red, "expected skipped" | A plan or apply ran in a run that cannot reach a host | A defect in a caller's use of the workflow. |

#### Named limits

- **The host route is not part of this workflow.** The role that installs the
  forced command is a separate pull request. Until it is delivered and a caller
  consumes this workflow, nothing has run against a real host. The first live
  use is a separate piece of work. The self-test runs the plan job with the host
  call stubbed.
- **The approval is GitHub's, and this workflow cannot see whether the
  environment has reviewers.** An environment with no reviewers lets the apply
  run at once. The workflow refuses an empty environment name but cannot read the
  environment's protection with its own token. The setting is checked by the
  ruleset and settings audit (CI-12), not here.
- **Runner addresses are not fixed.** A `from=` restriction on the key can only
  name the networks the runners come from, so it is as wide as GitHub-hosted
  runners unless the caller supplies its own.
- **The diff is public.** The job summary is readable by everyone who can read the
  repository, and the playbook's output reaches it. The scan is a backstop with a
  fixed list of patterns; the control is `no_log` and `diff: false` on a task that
  handles a secret (ANS-16). A pattern hit on a harmless line fails the job until
  the task hides its value.
- **The plan cannot show a change before the merge.** The host takes only a commit
  of its own `main`, so the plan runs after the merge and before the approval,
  which is the order ANS-17 describes. A pull request is checked by lint and
  Molecule only.
- **Check mode has limits (ANS-10).** `has_changes` is the host's count. A role
  whose tasks skip silently in check mode reports fewer changes than an apply
  would make; the standard makes that the role's defect.
- **Collections and tools install from the network** at their pinned versions, as
  in `shared-infra`; vendoring is decided by the foundation ticket (ANS-4).
- **The Molecule run is the plain `molecule test` and `molecule check`** in each
  role directory, with `ROLE_TEST_RUN_ID` set to a value unique to the run. A
  scenario that needs more than that (a harness, unit tests inside a container)
  is not covered by this workflow.
- **No change detection.** Every call runs every check; a caller cannot narrow
  it by path without making the gate job unreliable as a required check.
- **A schedule is not wired.** The nightly drift run of ANS-17 is a check-mode run
  that applies nothing; it is a later addition to this workflow.

The self-test is `.github/workflows/ansible-gated-apply-self.yml`. The tests of
every shell step are `tools/test_ansible_gated_apply.py`, which run the step text
from the workflow under bash with `ssh`, `gh` and `timeout` replaced by stubs:

```bash
python3 -m unittest discover -s tools -p 'test_ansible_gated_apply.py' -v
```

## Actions

A reusable workflow is a whole job; a composite action is a step inside the
caller's own job, which is what a decision needs when it reads files that only
that job has produced.

### `.github/actions/plan-has-changes`

Decides whether a saved Pulumi plan would change anything, so that an apply job
can skip, and the approval it would have asked for never be requested, when the
answer is no. Fails closed: only a plan that parsed and is wholly unchanged
gives `false`. Rules, limits and rationale:
[`plan-has-changes.md`](.github/actions/plan-has-changes/plan-has-changes.md).
The decision lives here once; a caller holds a `uses:` step and none of its
logic.

**Pin the commit, not the bare tag.** The standards workflow gate (CI-1)
requires every `uses:` that is not a reusable workflow to name a 40-character
commit SHA with a version comment, so the caller pins the commit the tag points
to and says which tag it is. Tags are immutable (see Versioning), so the two
name the same bytes. Read the commit with
`git ls-remote https://github.com/branchLeft/github-workflows 'refs/tags/vX.Y.Z^{}'`
(the `^{}` peels the signed tag to its commit). Dependabot's `github-actions`
ecosystem moves both the SHA and the comment. Nothing greps the caller for the
pin: the action runs its script from `github.action_path`, which is the revision
the caller resolved, so there is no second-pin trap of the kind
`merged-status.yml` has.

**Caller usage** — a step in the plan job, after the two Pulumi previews:

```yaml
jobs:
  plan:
    outputs:
      has_changes: ${{ steps.noop.outputs.has_changes }}
    steps:
      - name: Decide whether the plan changes anything
        id: noop
        if: always()
        uses: branchLeft/github-workflows/.github/actions/plan-has-changes@FULL_COMMIT_SHA # vX.Y.Z
        with:
          preview-json: preview.json
          plan-file: preview-plan.json
          working-directory: infra/hosts

  apply:
    needs: [plan]
    if: needs.plan.outputs.has_changes != 'false'
```

| Input | Default | Meaning |
|---|---|---|
| `preview-json` | required | The `pulumi preview --json` output. |
| `plan-file` | required | The plan saved by `pulumi preview --save-plan`. A missing file reads as `true`. |
| `working-directory` | `.` | Directory both paths are relative to, itself relative to the workspace root. The action does not read your job's `defaults.run.working-directory`: omit this input and the paths are read from the workspace root, so name the directory here. |
| `write-summary` | `true` | Append the headline to the job summary. `false` leaves the summary to the caller. Any other value fails the step. |

| Output | Meaning |
|---|---|
| `has_changes` | `false` only for a plan that parsed and changes nothing, outputs included; `true` otherwise. Unset only if the verdict could not be written, which the apply job's `!= 'false'` reads as "ask". |
| `summary_file` | A file holding the headline and, where the plan was not understood, the line naming what was not. For a caller that combines this verdict with another and writes the one headline itself (`write-summary: 'false'`). |

The apply job must skip only on an explicit `'false'`, as above. `!= 'false'`
is the whole contract: an unset output and every verdict the action could not
establish must still ask. Check that in your own CI with `check-apply-gate`,
below, rather than with a test of your own.

**What it needs, and what it gets.** It needs `python3` on the runner, which
`ubuntu-latest` has. The script reads two JSON files, makes no network call and
needs no secret. But a composite step inherits the calling job's `env:`, and a
plan job's `env:` usually holds the production plan credentials (the state
passphrase, the cloud token, the storage keys). So code from this repo runs with
whatever the plan job has in its environment. Callers can keep credentials off
this step by scoping them with `env:` to the steps that need them (the Pulumi
login and previews), not to the job.

**Reviewing a Dependabot bump of the pin.** The pull request shows a SHA and a
comment changing, not the code that will run with those credentials. Review it
by the diff of this action's directory across the tag range, not by the CI
result:

```bash
git diff vOLD vNEW -- .github/actions/plan-has-changes .github/actions/check-apply-gate
```

(or the same range at
`https://github.com/branchLeft/github-workflows/compare/vOLD...vNEW`). Read
`action.yml` and the script in full; a green check only shows the tests passed.

### `.github/actions/check-apply-gate`

Checks, in the calling repo's own CI, that a workflow keeps the contract above:
each listed apply job needs its plan job and skips only on an explicit
`has_changes != 'false'` (no `||`, no `always()`), and the plan job publishes the
action's verdict, from a step that runs `if: always()` and before the step that
writes the rest of the job summary. It is a separate action, not a mode of
`plan-has-changes`, because it runs in a different job: a credential-free CI job
that has checked the repo out, never the plan job with production credentials in
its environment. It also holds the rule for the saved plan, which can carry
resource inputs and ciphertext: only the step that saves it and the action may
name it, and nothing uploads, prints or globs it. One home for the checks means a
repo calls it and writes no test of these contracts of its own. It reads only the
workflow file it is given; a repo that does not call it is not checked. Codes,
rules and what stays each repo's own:
[`check-apply-gate.md`](.github/actions/check-apply-gate/check-apply-gate.md).

**Run it on `pull_request` and make it a required status check.** The plan jobs
run only on a push to main, so a check wired only to push finds a broken gate
after it has merged.

**Caller usage** — a step in a credential-free job that runs on `pull_request` and
on push to main, after the checkout, pinned as above:

```yaml
  apply-gate:
    runs-on: ubuntu-latest
    timeout-minutes: 5
    steps:
      - name: Checkout
        uses: actions/checkout@FULL_COMMIT_SHA # vX.Y.Z
        with:
          persist-credentials: false

      - name: Check the apply gate
        uses: branchLeft/github-workflows/.github/actions/check-apply-gate@FULL_COMMIT_SHA # vX.Y.Z
        with:
          workflow: .github/workflows/infra-ci.yml
          pairs: |
            plan:apply
          verdict-step: noop
          summary-step: Write the plan to the job summary
          plan-file: preview-plan.json
```

| Input | Default | Meaning |
|---|---|---|
| `workflow` | required | The workflow file, relative to the workspace root. |
| `pairs` | required | Whitespace-separated `PLAN_JOB:APPLY_JOB`, job ids as they are keys under `jobs:`. One workflow with several plan and apply jobs lists them all. |
| `verdict-step` | `noop` | Id of the plan-job step whose `has_changes` the job publishes: the plan-has-changes step, or a step that combines its output with another check. |
| `summary-step` | empty | Name of the step that writes the rest of the job summary. When given, the verdict step must come before it. |
| `plan-file` | required | The path `--save-plan` writes, as the workflow spells it. Only the save step and the plan-has-changes step may name it, and the latter must be given this same path. |
| `save-step` | `Save the update plan for the no-change check` | Name of the plan job's step that saves the plan: `continue-on-error: true`, `--save-plan <plan-file>`, stdout to `/dev/null`. |

It fails the step, with one annotation per finding, and has no outputs.

This repo's `plan-has-changes-self.yml` and `check-apply-gate-self.yml` run both
actions' unit tests and the actions themselves on every pull request. The
plan-has-changes run checks the job summary through the runner's own per-step
summary files, a directory layout that is the runner's and not a documented
interface: the check fails, rather than passing, if that layout goes.
