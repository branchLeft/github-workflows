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

Callers should reference a tag (`@v1`), not `@main` — `main` is the dev
branch here and can change without warning. To ship a change to all callers
at once, cut a new tag once it's tested:

```bash
git tag -f v1 <commit>
git push -f origin v1
```

(Moving-tag convention, same as `actions/checkout@v4` — bump to `v2` instead
of force-moving `v1` if a change is breaking.)

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
