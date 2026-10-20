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
