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
| `Discover the Ansible checks` | every run | validate the inputs, decide whether the run may reach a host, list the Molecule shards, fail on a `requirements.yml` that lists anything or a scenario that leaves Molecule's dependency step enabled | nothing |
| `Lint the Ansible inputs` | every run | install the hash lock, then yamllint, ansible-lint (production profile, `--strict`, `--offline`), mypy, ruff | nothing |
| `Molecule: <role>` | every run, one per role | `molecule test` (with the idempotence step) and `molecule check` on containers on the runner | nothing; fails if `DOCKER_HOST` is set (ANS-15) |
| `Plan the apply (check mode on each host)` | a push to the default branch of the repository itself | ask each host for a check-mode run of this commit (through the [`ssh-check-hosts`](#githubactionsssh-check-hosts) action), show the diff, decide `has_changes` | the check key, the host list and the known-hosts lines; `actions: read` |
| `Apply the planned commit (after approval)` | the same push, the plan succeeded, `has_changes` is not `false`, not a dry run | ask each host to apply the planned commit, then a second time to prove nothing is left | the apply key, the host list and the known-hosts lines, the environment's approval |
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
  actions: read

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
job, shown on the caller as `ansible / Ansible checks passed`. The caller grants
`actions: read` because the plan job asks for it (see the next section); without
the grant the whole call fails at startup, on a pull request too.

#### How the plan job loads the host check

The host check is the composite action `ssh-check-hosts`, shared with
`ansible-drift-check.yml`. A reusable workflow cannot name an action of its own
repository by a local path, because `uses: ./` resolves in the caller's
workspace. So the plan job checks this repository out into `.gw` at the commit
the caller pinned, and runs `./.gw/.github/actions/ssh-check-hosts` from there.
That step holds the check key, so the commit is established before the checkout
and nothing falls back to the default branch:

1. The commit is read from the run record, `referenced_workflows` of
   `GET /repos/{caller}/actions/runs/{run_id}`, which names the commit of each
   reusable workflow the run called. Exactly one commit must name this workflow's
   file in this repository, and it must be 40 lowercase hex digits. This is the
   reason for the `actions: read` grant. `github.job_workflow_sha` is not used:
   it is empty in a reusable workflow here, and an empty `ref` makes the checkout
   take the default branch.
2. The path `.gw` must not exist, so no file of the caller can sit where the
   action is read from.
3. After the checkout, `git rev-parse HEAD` in `.gw` must equal that commit and
   the action file must be there.

Any failure stops the job before the key is read.

| Input | Default | Meaning |
| --- | --- | --- |
| `environment` | required | The deployment environment the apply job runs in. Must not be empty. |
| `working-directory` | `.` | Directory holding the Ansible directory, `.ansible-lint` and `.yamllint`. |
| `ansible-directory` | `ansible` | Directory, relative to `working-directory`, holding `requirements.txt` (the hash lock), `pyproject.toml` and, if there is one, a `requirements.yml` that holds only `collections: []`. |
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
- **Tools and collections install from the package index, by hash.**
  `requirements.txt` is a `uv pip compile --universal --generate-hashes` lock, and
  `pip install --require-hashes` refuses a package whose hash it does not list, so
  a caller whose file is unhashed fails at the install (ANS-4). Nothing is fetched
  from Galaxy: the collections come with the `ansible` package in the lock.
  Molecule has two ways to reach Galaxy, and the discover job closes both, with
  the one input named below left open. A
  scenario must set `dependency.enabled: false`. And Molecule's `prerun`, which is
  on by default and which the workflow leaves on because it is what makes a role
  resolve by its full name (with `prerun: false` the converge fails with "role not
  found"), installs from Galaxy every requirements file at six paths under the role
  directory (`requirements.yml`, `roles/`, `collections/`, `tests/`,
  `tests/integration/` and `tests/unit/` `requirements.yml`) and the dependencies
  of a `galaxy.yml`. So each of those requirements files that exists must hold only
  `collections: []`, and no `galaxy.yml` or `galaxy.yaml` may exist anywhere under
  the Ansible directory (wider than the places prerun looks, so that no copy of
  ansible-compat's search rules is needed). The search by name does not see
  through a link, and prerun resolves the role directory and every direct entry of
  it. So the role directory and each direct entry that is a link are refused when
  the link cannot be resolved, when it resolves outside the Ansible directory,
  when it resolves to a file named `galaxy.yml` or `galaxy.yaml`, or when it or its
  parent directory holds either file. Every role directory is also judged by its
  physical path, whether or not it is itself a link, because Molecule runs there:
  one reached through a linked parent directory that leaves the Ansible directory
  is refused. Links deeper in a role are left alone,
  because prerun does not read them and `shared-infra` has legitimate ones there.
  One input is not covered: a role directory whose physical path is directly
  under the Ansible directory (a link inside the tree can put one there), where
  prerun also reads a `galaxy.yml` two levels above it, outside the tree the
  discover job searches. The `requirements.yml` of the Ansible
  directory is held to the same shape, and ansible-lint runs `--offline`. Vendoring
  the packages is not built. A Galaxy route in ansible-compat or Molecule that a
  later release adds is not covered until this list is updated. The lock must pin the tools the
  workflow runs: `ansible`, `ansible-lint`, `molecule` with its Docker plugin,
  `yamllint`, `mypy` and `ruff`.
- **The `requirements.yml` rule is also in `shared-infra`**, in
  `ansible/checks/check_pins.py`, which a reusable workflow cannot import, so the
  rule has two homes and each names the other. Two differences are on purpose: this
  workflow passes an absent `requirements.yml` because it never reads one, and it
  reads only each shard's `molecule/default` scenario, the only one it runs.
- **The Molecule run is the plain `molecule test` and `molecule check`** in each
  role directory, with `ROLE_TEST_RUN_ID` set to a value unique to the run. A
  scenario that needs more than that (a harness, unit tests inside a container)
  is not covered by this workflow.
- **No change detection.** Every call runs every check; a caller cannot narrow
  it by path without making the gate job unreliable as a required check.
- **A schedule is not part of this workflow.** The nightly drift run of ANS-17 is
  its own workflow, `ansible-drift-check.yml`, below.

`tools/run_workflow_steps.py WORKFLOW JOB --input NAME=VALUE --step "Step name"`
runs the named steps of one job exactly as their `run:` text is written, in order,
under `bash -e`, in the job's default working directory. `--step-input STEP
NAME=VALUE` replaces one input for one step. It understands only the expression
`${{ inputs.NAME }}`. Any other expression, a step with `uses`, `if`, `shell` or a
`working-directory` of its own, a missing step or input, or an expression in run
text, stops it with exit status 99 before any step runs. Otherwise it stops at the
first failing step and returns that step's status. A step's lines for `GITHUB_PATH`
and `GITHUB_ENV` reach the steps after it.

The self-test is `.github/workflows/ansible-gated-apply-self.yml`. It runs the
workflow over the fixture, and its job `lint-expected-fail` runs the workflow's
own install and ansible-lint steps (taken from the workflow file by
`tools/run_workflow_steps.py`, so the commands have one home) on
`.github/fixtures/ansible-gated-apply/lint-fail/`. It passes only when ansible-lint
fails with the rule, the one-failure summary and the exit status named in that
directory's README. It proves ansible-lint rejects bad input; it does not prove
yamllint, mypy or ruff. The tests of every shell step are
`tools/test_ansible_gated_apply.py`, which run the step text from the workflows and
the `ssh-check-hosts` action under bash with `ssh`, `gh`, `timeout` and `yq`
replaced by stubs (the real `yq` runs in the self-test's own run of the discover
job):

```bash
python3 -m unittest discover -s tools -p 'test_ansible_gated_apply.py' -v
```

### `ansible-drift-check.yml`

The nightly check that each host still matches the commit it was last given
(ANS-17). It asks each host, in check mode, what an apply of the default branch
head would change. It is red when any host would change or cannot say, and it
opens or updates one issue. **It applies nothing and cannot:** it holds the check
key only, runs in no deployment environment and takes no apply key. The check key
is restricted on the host to the forced command in check mode, so even a changed
workflow could not apply with it.

It is two jobs, so that the credential that can write an issue is never in the job
that holds the SSH key:

| Job | Runs when | May do | Holds |
| --- | --- | --- | --- |
| `Check every host for drift (check mode)` | a `schedule` or `workflow_dispatch` run on the default branch of the repository itself, not a fork | the guard, the pin of the shared actions (as in the plan job above), then the `ssh-check-hosts` action with `fail-on-changes` | the check key, the host list and the known-hosts lines; `contents: read` and `actions: read` |
| `Open or update the drift issue` | the check job failed or was cancelled (a job that reaches its timeout reports `cancelled`, see "Blind spots and noise") after the guard passed; never after a clean run or a refusal by the guard | open one issue, or comment on the open one | `issues: write` and no secret |

The guard refuses every other event, a ref that is not the default branch, a fork,
a commit that is not 40 lowercase hex digits and a label that is not a plain word.
It reads `default_branch` and `fork` from the event payload: if a `schedule`
payload ever lacks them, the guard refuses every run, and the first scheduled run
of a caller shows which it is.

#### How a repository calls it

Add one caller, pinned to an exact tag (see Versioning), from a schedule and from
a manual run. The caller grants the union of the two jobs' permissions.

```yaml
name: ansible-drift

on:
  schedule:
    - cron: '17 2 * * *'
  workflow_dispatch:

permissions:
  contents: read

jobs:
  drift:
    permissions:
      contents: read
      actions: read
      issues: write
    uses: branchLeft/github-workflows/.github/workflows/ansible-drift-check.yml@vX.Y.Z
    with:
      host-label: edge
    secrets:
      ssh_hosts: ${{ secrets.ANSIBLE_HOSTS }}
      ssh_known_hosts: ${{ secrets.ANSIBLE_KNOWN_HOSTS }}
      ssh_check_key: ${{ secrets.ANSIBLE_CHECK_KEY }}
```

| Input | Default | Meaning |
| --- | --- | --- |
| `host-label` | required | A short name for this set of hosts: letters, digits, dot, underscore and dash, at most 32 characters. It names the issue and its label. It is not a host name, because the hosts are secrets. |
| `host-timeout-minutes` | 65 | One host call is stopped after this long. A whole number from 1. |
| `check-timeout-minutes` | 90 | Bound of the check job, from 10 to 350 (GitHub ends a job at 360). The hosts are asked one after another and share this time less five minutes: a call is stopped at the smaller of `host-timeout-minutes` and what is left, and a host reached after that is not asked and is judged `no-answer`. So a run of hung hosts ends red with an outcome and opens the issue; it does not wait for the job bound. |

All three secrets are required and are the same secrets as the plan job's:
`ssh_hosts`, `ssh_known_hosts` and `ssh_check_key`. There is no input for a host
or a key, and no dry run.

#### What a red run means, and how to clear it

The job summary names what each host answered, as `host 1`, `host 2` in the order
of the list, and the first line says which kind of red it is. A host that would
change is **drift**; every other red is not drift and says what it is instead.

| First line says | The host answered | Meaning | To clear |
| --- | --- | --- | --- |
| `drift: N changed` | exit 11, `would-change` | Check mode shows a change for the default branch head. Two causes: the host was edited by hand, or a change was merged and its apply has not run (waiting for approval, or failed). The diff is in the summary. | Merged but not applied: approve or re-run the gated apply of that commit. A hand edit: decide whether the apply should overwrite it or the repository should take it, then apply. Then run this workflow by hand and confirm it is green. |
| `refused: busy` | exit 21 | The host was already running a request when the check came. | Usually nothing: the next night asks again. Run the workflow by hand once the other request is done. |
| `refused: not-on-main`, `rollback`, `unsigned`, `bad-signature`, `signer-not-allowed`, `playbook-not-allowed`, `bad-request`, or `other` | exit 20, with that reason | The host refused the commit: it is not on the host's `main` or is older than the last one applied, or it is not signed by an allowed key, or the request was malformed. `other` is a reason this workflow does not know. | Read the host's own log and the `pull_trigger` role's README in `branchLeft/shared-infra`. A retry does not change a refusal. |
| `unreachable` | the SSH call failed (exit 255) | The host is down or unreachable, the key is rejected, or the host key changed. | Check the host and the key's `authorized_keys` line; run the workflow by hand. |
| `no usable answer` | not one summary line for this commit, or no call at all | The host answered with something else, or for another commit or exit status, or the call was stopped at its limit, or the time given to the check was spent before the host was reached (its row says `not asked`). | Read the host's log. If hosts were not asked, an earlier host was slow or hung: clear that one first, and run the workflow by hand. |
| `failed` | exit 30, 31 or 40, or an answer that contradicts itself | The playbook failed, timed out, or the trigger errored. | Read the host's log and fix forward. |
| `diff withheld` | a diff that matched a secret pattern, or was too large to show | The host would change but the diff cannot be shown. The check still counts it as not clean. | Fix the task with `no_log: true` and `diff: false` (ANS-16), or split the change. |
| `Not clean` with several kinds | more than one of the above | Each kind is counted in the first line. | Clear each. |

An issue whose outcome reads `unknown` means the run stopped before it could judge:
the pin failed, the caller did not grant `actions: read`, or the key or the known
hosts were empty. Read the run.

#### The issue

One open issue per host set: titled `Host drift: <host-label>`, labelled
`host-drift:<host-label>`. A red run opens it if none is open, and otherwise adds
a comment, so a nightly red is one thread, not one issue a night. The workflow
finds it by the label, which only a person with triage rights can set, and never
by a title search. Its body holds the outcome, the counts and the run link, and
never a host name or a diff. The workflow never closes it: close it after a run by
hand is green.

#### Blind spots and noise

- **Red every night** from the merge of a change until its apply is approved and
  has run. That is the intended signal, and each night adds a comment to the one
  issue.
- **No alert when the nightly does not run.** GitHub disables a scheduled
  workflow after 60 days without repository activity. A caller that does not grant
  `issues: write` or `actions: read` gets a failed call at startup, with no job
  and no issue.
- **A job that reaches its timeout reports `cancelled`, not `failure`, to the job
  that needs it.** Measured on a throwaway run of this repository
  ([run 39514617371](https://github.com/branchLeft/github-workflows/actions/runs/39514617371)):
  the needed job ended `cancelled`, and a job that waited on it with `always()` ran
  and read `cancelled`. The alert therefore runs for `failure` and for `cancelled`,
  and a timeout opens an issue whose outcome reads `unknown`. The hosts are judged
  inside the job's time (see `check-timeout-minutes`), so a timeout is reached only
  if something outside the host calls hangs. The cost of covering it: a person who
  cancels the run by hand also gets an `unknown` comment on the issue.
- **A busy host is a refusal and is not retried.** The next night asks again.
- **The check sees what check mode sees (ANS-10).** A task that skips silently in
  check mode hides its drift here as it does in the plan.
- **The commit checked is the default branch head the run was started at.** There
  is no refusal for an older head here; the host refuses a commit it should not take.

The tests of every step are the same module as the gated apply's. `tools/sabotage.py`
breaks each control in a copy of the repository, one exact edit at a time, and requires
the test that guards it to go red, after a run of the unedited tree that must be green.
The `Sabotage proof` job of the self-test workflow runs it on every pull request, so
the log of that job is the recorded red and green of every control. Each case in the
file is one exact edit and the tests (as `Class.method`) that must go red; the
snapshot is regenerated in the copy first, so it cannot stand in for them. An edit
whose text is no longer found fails its case, because the code moved. To add one, put
a `Case` in `CASES`, run `python3 tools/sabotage.py --discover NAME` to see which
tests the edit turns red, and name them. `python3 tools/sabotage.py NAME` runs the
cases whose name contains `NAME`, and `--list` lists them.

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

### `.github/actions/spend-gate`

Decides whether a Pulumi preview could cost money. Reads the same
`pulumi preview --json` file as `plan-has-changes` and says `FREE` only when
every resource that changes is of a type on an explicit free list (Hetzner
networks, subnets, routes, firewalls and their attachments, SSH keys and
placement groups, plus the stack and its providers). Everything else is `PAID`:
servers, volumes, load balancers, IPs and buckets, any unknown type, a preview
that errored, and a preview it cannot read. Rules, the list and its limits:
[`spend-gate.md`](.github/actions/spend-gate/spend-gate.md). No workflow calls it
yet. It loads the preview parser from `plan-has-changes` at the same revision, so
a caller pins this repository once.

| Input | Default | Meaning |
| --- | --- | --- |
| `preview-json` | required | Path to the `pulumi preview --json` output. |
| `working-directory` | `.` | Directory the path is relative to, relative to the workspace root. |

| Output | Meaning |
| --- | --- |
| `verdict` | `FREE` or `PAID`. Unset if the step failed: a caller must read an unset verdict as `PAID`. |
| `reason` | One line saying why, from fixed text and sanitised type names. |

The same line is appended to the job summary. The action gives a verdict only: a
caller must also treat a failed `pulumi preview` step as `PAID`. This repo's
`spend-gate-self.yml` runs the unit tests, which include breaking the free-list
check in a copy and requiring the `PAID` tests to go red, and runs the action over
a `FREE` and a `PAID` fixture on every pull request.

### `.github/actions/ssh-check-hosts`

Asks each host, through the restricted SSH route, what an apply of one commit
would change, judges the answers, scans each diff for secrets and writes the
result to the job summary. It is the one home of the host check: the plan job of
`ansible-gated-apply.yml` and the job of `ansible-drift-check.yml` both call it,
so the host list is validated, the check key is used and the answers are judged in
one place. It holds the check key and never applies.

It is not for a caller's own workflow. It is loaded by the two workflows above, from
a checkout of this repository at the commit the caller pinned (see "How the plan
job loads the host check"), and it takes the secrets as inputs.

| Input | Default | Meaning |
| --- | --- | --- |
| `hosts` | required | One `user@host` per line, from a secret. Each line is checked against a pattern; a duplicate or more than 50 hosts is refused. |
| `known-hosts` | empty | The known_hosts lines, from a secret. Empty only in a dry run. |
| `check-key` | empty | The private key of the check route, from a secret. Empty only in a dry run. |
| `sha` | required | The commit to ask about, 40 lowercase hex digits. |
| `dry-run` | `false` | `true` replaces the SSH call with `dry-run-stub`. |
| `dry-run-stub` | empty | With `dry-run`, the executable, relative to `working-directory`, that stands in for a host. |
| `working-directory` | `.` | What `dry-run-stub` is relative to. |
| `host-timeout` | `65` | One host call is stopped after this many minutes. A whole number from 1. |
| `total-timeout` | empty | Empty puts no bound on the hosts together. A whole number of minutes is the time all the calls share: each is stopped at the smaller of `host-timeout` and what is left, and a host reached after the time is spent is not asked and is judged `no-answer`. The caller's job bound must sit above it. The drift workflow passes its job bound less five minutes; the plan job passes nothing. |
| `environment` | empty | The deployment environment named in the plan wording. Not read when `fail-on-changes` is `true`. |
| `fail-on-changes` | `false` | `false` is the plan: a host that would change is a result, and the step fails only for a host that gave no usable answer. `true` is the drift check: any answer that is not clean also fails the step, and the summary names the kind. |

| Output | Meaning |
| --- | --- |
| `has-changes` | `false` only when every host answered clean for this commit; `true` otherwise. |
| `hosts-digest` | The SHA-256 of the validated host list. The apply job recomputes it from the same secret and refuses a list that differs. |
| `outcome` | `ok`, `drift`, `refused`, `unreachable`, `no-answer`, `failed`, `withheld`, or `mixed`. Empty if the step stopped before it could judge. |
| `detail` | The kinds with counts and no host name, as in the first line of the summary. Empty if the step stopped before it could judge. |

How the host's answer is classified (the host exits are those of the `pull_trigger`
role; this workflow believes its summary line only when it names this commit and the
exit status):

| Host exit and verdict | Kind | Plan (`fail-on-changes` false) | Drift check (`true`) |
| --- | --- | --- | --- |
| 0, `unchanged`, `changed=0` | `ok` | clean | green |
| 11, `would-change` | `drift` | a result, `has-changes` is `true`, the apply waits for approval | red |
| 20, `refused`, reason as listed | `refused: <reason>`, or `refused: other` | red | red |
| 21, `busy` | `refused: busy` | red | red |
| 30, 31, 40, or an answer that contradicts itself | `failed` | red | red |
| 255, the SSH call failed | `unreachable` | red | red |
| not one summary line for this commit | `no-answer` | red | red |
| any of the above with a diff that matched a secret pattern or is too large | `withheld` | red | red |

The self-test is `.github/workflows/ssh-check-hosts-self.yml`: it runs the action on a
real runner against the stand-in host in the fixture, with `fail-on-changes` both
ways, on every pull request.
