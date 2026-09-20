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
delivery, and the reason both exist is that the first cannot answer it alone:
the closing trailers GitHub acts on and the non-closing `Refs` spelling parse
to the same edge, and in an estate where hand-delivered work uses `Refs` by
convention, the keyword separates nothing. `from-statuses` carries the
discrimination — an unstarted backlog epic is not completed by a merge that
mentioned it, whatever keyword did the mentioning.

An unrecognised value for either is refused rather than narrowed, because a
typo that quietly wrote nothing would look exactly like an estate with
nothing to write.

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
