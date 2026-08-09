# github-workflows

Shared, parameterized reusable GitHub Actions workflows for repos owned by
the `branchLeft` org. One workflow lives here per reusable capability; each
target repo keeps a thin caller workflow that just points at the pinned
version here.

## Why a separate repo

Keeping the actual logic in one place means a fix or improvement lands once
and every caller repo picks it up by bumping a tag, instead of copy-pasted
YAML drifting out of sync across `shared-infra`, `ghost-platform`,
`ghost-platform-docs`, `website`, `components`, `architecture-diagrams`, and
whatever comes next.

## Versioning

Callers should reference an exact tag (`@v1.0.3`), not `@main` — `main` is
the dev branch here and can change without warning. Tags are immutable
org-wide (a ruleset blocks moving or deleting them), so there's no
moving-`@v1`-forward convention — every change, including fixes, ships as a
new patch/minor tag:

```bash
git tag -s v1.0.3 -m "v1.0.3: <what changed>" <commit>
git push origin v1.0.3
```

The tag ruleset requires signatures, so `-s` rather than `-a`.

Bumping every caller means a one-line PR per repo (`@v1.0.2` → `@v1.0.3` in
each caller workflow) rather than a single silent update — more PR noise, but
every caller's history shows exactly which version it's on and when it
changed.

A workflow here must never hardcode a tag of this repo internally. Tags are
immutable, so a literal ref inside a workflow pins callers to a revision that
has no relationship to the one they asked for — `docs-lint.yml` uses
`github.job_workflow_sha` to load its own rules from the exact commit the
caller resolved.

## Workflows

### `graphify.yml`

Headless, incremental knowledge-graph rebuild via `graphify extract`
(graphifyy's CI-native command — no Claude Code / Agent-tool dependency, it
calls the LLM backend directly). Commits `graphify-out/` back to the
triggering branch as the `graphify-bot` identity, or raises it as a pull
request where that branch is protected — see **Publish modes** below.

**Caller usage** — add to the target repo as
`.github/workflows/graphify.yml`:

```yaml
name: graphify

on:
  push:
    branches: [main]
    paths-ignore:
      - "graphify-out/**"

jobs:
  graphify:
    permissions:
      contents: write
    uses: branchLeft/github-workflows/.github/workflows/graphify.yml@v1.0.3
    secrets: inherit
```

The `permissions:` block is on the calling job, not optional: the org default
is `contents: read`, and a reusable workflow cannot grant itself more than the
caller allows, so without it the run fails before the first step.

**Publish modes.** The `publish` input decides how the graph reaches the
default branch:

| `publish` | Behaviour | Use when |
|---|---|---|
| `direct` (default) | Commits straight to the triggering branch. | The default branch accepts direct pushes. |
| `pull-request` | Force-pushes the graph to the `publish-branch` branch (default `graphify`) and opens a PR against the default branch, reusing the open one if there is one. | A ruleset protects the default branch. |

A caller in `pull-request` mode needs `pull-requests: write` alongside
`contents: write`:

```yaml
jobs:
  graphify:
    permissions:
      contents: write
      pull-requests: write
    uses: branchLeft/github-workflows/.github/workflows/graphify.yml@v1.0.3
    with:
      publish: pull-request
    secrets: inherit
```

Two consequences of that mode are worth knowing before enabling it:

- The graph branch is rebuilt from the default branch on every run, not
  accumulated, so the open PR always carries exactly one commit and can never
  fall behind its base. Review comments on it do not survive the next run.
- The PR is opened by `GITHUB_TOKEN`, and GitHub does not start workflow runs
  from events that token raises. Required status checks on the PR therefore
  stay pending forever, and merging it needs an actor with ruleset bypass.

**Requires** the `GEMINI_API_KEY` org secret to do doc/image (semantic)
extraction. Without it, the workflow still runs — it falls back to
`--code-only` (AST-only graph, no LLM call, no cost) and prints a notice.
See the runbook in this repo's PR description / the branchLeft plan archive
for how to obtain and set that key — it requires a Google account with API
access, which this automation cannot provision on its own.

**Org-level config this workflow reads:**
- `vars.GRAPHIFY_GEMINI_MODEL` — default Gemini model (repo caller can
  override via the `gemini-model` input).
- `secrets.GEMINI_API_KEY` — optional; enables semantic extraction.

**What deliberately never gets committed.** `graphify-out/` is a shared
artifact, so anything in it that describes the machine that built it is
wrong everywhere else:

- `.graphify_root` — the absolute scan root, rewritten on every run. Several
  read paths prefer it over the root derived from `graph.json`'s own
  location, and `build.py`'s merge-root inference is one of them: given a
  foreign path it silently resolves to a directory that does not exist, and
  deleted files then stop pruning from the graph. With the file absent, every
  consumer falls back to the derived root, which is correct on any machine.
- `.graphify_python` — absolute path to the interpreter that has graphify
  installed. Only ever written locally, so a committed copy pins one
  developer's home directory into the repo forever. Its only consumer probes
  for executability first and has fallbacks, so losing it costs nothing.

The commit step stages `graphify-out/` and then drops both from the index, so
callers stay clean even if a repo has not added them to its own `.gitignore`.
Caller repos should still ignore them, so local commits behave the same way.

`GRAPHIFY_NO_BACKUP=1` is set for the same reason: graphify's pre-overwrite
snapshot into `graphify-out/<date>/` protects an un-reproducible local build,
but in CI the previous graph is already in git history, so each snapshot is a
duplicate copy of the graph added to the repo on every day it changes.

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
    uses: branchLeft/github-workflows/.github/workflows/docs-lint.yml@v1.0.3
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
