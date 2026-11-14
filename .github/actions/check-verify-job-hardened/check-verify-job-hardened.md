# check-verify-job-hardened

`check-verify-job-hardened.py` checks, in a calling repo's own CI, that the job which
runs the [`verify-package-attestations`](../verify-package-attestations/verify-package-attestations.md)
action cannot turn a failed check into a pass. The action fails closed. A caller can
still undo that around it: a `continue-on-error`, an `if:` or a missing grant leaves the
repo's own tests green and the security check silent. This is the one shared test of
that wiring, so a caller writes none of its own.

It reads one workflow file the caller names and nothing else. It cannot see a workflow
it is not given, so it does not police callers: a repo that does not run it is not
checked, and the check only binds once its job is a required status check, which is a
repository setting the owner makes.

## What it reads

Every job that has a step whose `uses:` names the action, in any letter case, is a
verify job, and each is checked. A workflow with no such job fails
(`VERIFY-NONE`), so deleting or renaming the job away is red, not green. The optional
`job` input (`--job` on the command line) names job ids that must exist and call the
action, which fails a rename the same way (`VERIFY-NO-JOB`, `VERIFY-NOT-CALLED`).

## The rules, for each verify job

| Code | Fails when | Why |
| --- | --- | --- |
| `VERIFY-CONTINUE-ON-ERROR` | The job, or any step of the job, has a `continue-on-error` whose value is not an unquoted `false`. A `true`, an expression such as `${{ matrix.x }}` or `${{ false }}`, a quoted string, an empty value and a block scalar all fail. | A failure that is forgiven reads as a pass. An expression cannot be evaluated here, so it is refused. |
| `VERIFY-IF-JOB` | The job has an `if:`, whatever its value. | A job skipped by a condition reads as a pass to a required status check. |
| `VERIFY-IF-STEP` | The step that calls the action has an `if:`, whatever its value. | The job is green while the check never ran. |
| `VERIFY-NEEDS-IF` | A job in the verify job's `needs` chain, followed all the way up, has an `if:`. | A job whose needed job is skipped is skipped too. A `needs` with no `if:` anywhere in the chain is accepted: a failed needed job fails the run, so nothing is hidden. |
| `VERIFY-PERMISSIONS` | The job has no `permissions:` of its own, or it grants anything but exactly `contents: read` and `packages: read`. `read-all`, `write-all`, `{}`, an extra grant and a `write` all fail. A flow mapping with exactly the two grants passes, in either order. | The action needs those two and makes no write and holds no secret. The workflow-level block is not enough: the job states its own, so a later change to the workflow level cannot widen it. |
| `VERIFY-SOURCE` | The step does not use `branchLeft/github-workflows/.github/actions/verify-package-attestations@REF`, in any letter case. | A copy of the action in the caller's own tree, or another organisation's action of the same name, can pass anything. |
| `VERIFY-NONE`, `VERIFY-NO-JOB`, `VERIFY-NOT-CALLED` | See above. | A missing job is the quietest silent pass. |
| `VERIFY-UNREADABLE` | The file is missing, is not text, or holds a form the reader will not guess at. | The check fails closed: what it cannot read with certainty it does not pass. |

Each finding is one `::error` annotation naming the code, the job and the reason, and
the program exits 1 if there is any.

## What makes a workflow unreadable

The reader is strict and has no YAML library: it parses the block-style subset that
workflows use and refuses the rest, because each of these can hide a key from a reader
that does not resolve them:

- a tab in the indentation;
- an anchor (`&x`), an alias (`*x`), a tag (`!!str`) or a merge key (`<<:`);
- a second document in the file;
- a key that appears twice in one mapping;
- a double-quoted key with an escape, which can spell any name;
- a nested sequence;
- a job, or a step, written as a flow mapping (`{ ... }`), or `steps` written as a flow list;
- a `needs` entry that names a job not in the workflow;
- a line that is not a mapping entry where one is expected.

Flow collections as values (`branches: [main]`, `env: { A: b }`) are read as opaque text
and are fine anywhere but as a job or a step. Quoted keys (`"if":`) are unquoted before
they are compared, and key names are compared without regard to letter case.

## What it does not check

Stated so that nothing here is read as more than it is:

- **A step other than the verify step carrying an `if:`** (the checkout step, say) is
  not refused. Only the job and the verify step are, because a skipped checkout leaves
  the scan with no lockfile, and the action fails closed on that.
- **The `with:` of the verify step**, such as a narrowed `root`, is not checked.
- **That the step still runs.** The check reads the text of the workflow. It does not run it.
- **The workflow's triggers**, a path filter on `on:` for one. A workflow that never
  starts is pending on a required check, not green.
- **That the check itself is run.** A caller removes the step that calls this and the
  check is gone. The required status check and the code owners of `.github/` are the
  control for that.
- **Self-hosted runners**, a `runs-on` that is not GitHub-hosted: the action refuses a
  self-hosted attestation signer, not a self-hosted verify job.
- **The ref of the pinned action**: a moving ref such as a branch name is accepted. The
  caller's own pin check owns that.

## How a caller runs it

A step in a credential-free job that has checked the repo out, on `pull_request` and on
push to the default branch. The pin is the full commit of the signed tag of this repo
that first carries the action; until that tag exists there is no line to add.

```yaml
      - name: Check the verify job cannot pass silently
        uses: branchLeft/github-workflows/.github/actions/check-verify-job-hardened@FULL_COMMIT_SHA # vX.Y.Z
        with:
          workflow: .github/workflows/package-attestations-ci.yml
          job: verify
```

| Input | Default | Meaning |
| --- | --- | --- |
| `workflow` | required | The workflow file, relative to the workspace root. |
| `job` | empty | Whitespace-separated job ids that must exist and call the verify action. Empty requires only that at least one job calls it. |

Put the step in a job of its own, or in the repo's own test or lint job, never in the
verify job: that job would then check itself.

The same program runs without the action, from a checkout of this repo at the pinned
commit, for a caller whose tests are not a workflow:

```bash
python3 -I check-verify-job-hardened.py --workflow .github/workflows/ci.yml --job verify-package-attestations
```

Exit 0 and one `ok:` line when every verify job is clean, exit 1 and one `::error`
line per finding otherwise, exit 2 for a command-line mistake.

## How it is proven

`test_check_verify_job_hardened.py` holds, over two passing fixtures (a dedicated
workflow, and a verify job among other jobs that carry the forbidden words as text,
comments, `run:` bodies, env names and unrelated steps):

- the harmless look-alikes, each one edit from a passing fixture, which must stay green;
- every forbidden form, each one edit from a passing fixture, which must be named with
  its code, including a quoted key, a key in another letter case, a block scalar and a
  multi-line condition;
- every unreadable form, which must raise;
- the command line, including a missing file and a file that is not text;
- the Sabotage class, which breaks each control in a copy of this directory and requires
  the tests that guard it to go red there, and the unmodified copy to be green.

A case that stops changing its fixture fails, so a fixture edit cannot quietly empty a
case. This repo's `check-verify-job-hardened-self.yml` runs the tests and the action on
every pull request.
