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
