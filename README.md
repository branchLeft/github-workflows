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

Callers should reference an exact tag (`@v1.0.1`), not `@main` — `main` is
the dev branch here and can change without warning. Tags are immutable
org-wide (a ruleset blocks moving or deleting them), so there's no
moving-`@v1`-forward convention — every change, including fixes, ships as a
new patch/minor tag:

```bash
git tag -a v1.0.1 -m "v1.0.1: <what changed>" <commit>
git push origin v1.0.1
```

Bumping every caller means a one-line PR per repo (`@v1.0.0` → `@v1.0.1` in
each `.github/workflows/graphify.yml`) rather than a single silent update —
more PR noise, but every caller's history shows exactly which version it's
on and when it changed.

## Workflows

### `graphify.yml`

Headless, incremental knowledge-graph rebuild via `graphify extract`
(graphifyy's CI-native command — no Claude Code / Agent-tool dependency, it
calls the LLM backend directly). Commits `graphify-out/` back to the
triggering branch as the `graphify-bot` identity.

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
    uses: branchLeft/github-workflows/.github/workflows/graphify.yml@v1
    secrets: inherit
```

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
