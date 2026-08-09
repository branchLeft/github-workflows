# Runbook: enabling graphify CI (and full semantic extraction)

## -1. Make this repo public (blocking — do this first)

`website`, `components`, and `architecture-diagrams` are public repos;
this repo is private. GitHub does not allow a public repository to call a
reusable workflow stored in a private repository — this is a hard platform
restriction, not something the `access_level=organization` setting (below)
can override; that setting only extends access to other private/internal
repos in the org. Confirmed live: `website`'s first run failed with
`workflow was not found`, and `components`/`architecture-diagrams` will hit
the same error once triggered. `shared-infra`, `ghost-platform`, and
`ghost-platform-docs` are private, so they're unaffected.

This repo holds no secrets or tenant-identifying data by design — just
parameterized CI logic — so making it public fits the same standard already
applied to `website`/`components`. Repo visibility is Rob-only per
workspace convention:

```bash
gh repo edit branchLeft/github-workflows --visibility public --accept-visibility-change-consequences
```

## 0. Grant the caller repos write permission (blocking — do this first)

As of 2026-08-09, every graphify run fails before it even reaches the
`--code-only` fallback:

```
Invalid workflow file: .github/workflows/graphify.yml#L10
Error calling workflow 'branchLeft/github-workflows/.github/workflows/graphify.yml@v1'.
The workflow is requesting 'contents: write', but is only allowed 'contents: read'.
```

This isn't a bug in `graphify.yml` — every repo in the org (confirmed via
`gh api repos/branchLeft/<repo>/actions/permissions/workflow` across all 7)
inherits the org-wide default Actions permission, which is `read`. A
reusable workflow's `permissions:` block can only grant what the *calling*
repo's Actions setting already allows, so `contents: write` gets capped
before it ever runs.

This is a repo/org settings change, not a file I can PR — per workspace
convention (`CLAUDE.md`: "repo settings" are Rob-only), you need to run one
of these yourself:

**Scoped (recommended)** — only the 6 rollout repos + this one get write,
everything else in the org stays safely read-only by default:

```bash
for r in shared-infra ghost-platform ghost-platform-docs website components architecture-diagrams github-workflows; do
  gh api -X PUT repos/branchLeft/$r/actions/permissions/workflow \
    -f default_workflow_permissions=write \
    -F can_approve_pull_request_reviews=false
done
```

**Org-wide (simpler, broader blast radius)** — every future repo defaults
to write-enabled workflows unless overridden per-repo:

```bash
gh api -X PUT orgs/branchLeft/actions/permissions/workflow \
  -f default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=false
```

Once this is set, the `--code-only` fallback (step 1 below is optional)
should work immediately on the next push to `main` in any of the 6 repos.

## 1. Get a Gemini API key (optional — enables semantic extraction)

Everything works without this step — the `graphify.yml` reusable workflow
falls back to `--code-only` (AST graph only, zero cost) when
`GEMINI_API_KEY` isn't set. This section is the remaining step to turn on
full extraction (docs, papers, images) org-wide.

This needs a Google account with API access, which nothing in this
automation can provision on its own — that's the whole reason it's a
runbook and not a script.

1. Go to [Google AI Studio](https://aistudio.google.com/apikey) and sign in.
2. Create an API key. For anything beyond light/free-tier use, attach it to
   a GCP project with billing enabled (AI Studio walks you through this) —
   the free tier's rate limits will throttle a multi-repo CI fleet.
3. Copy the key. Don't paste it into chat, a PR, or a commit — go straight
   to the `gh` command below.

## 2. Set it as an org secret, scoped to the repos that use it

Run this yourself (not me — this puts a live credential in your shell, and
you should be the one holding it):

```bash
gh secret set GEMINI_API_KEY \
  --org branchLeft \
  --visibility selected \
  --repos shared-infra,ghost-platform,ghost-platform-docs,website,components,architecture-diagrams,github-workflows
```

`gh` will prompt you to paste the key value (or pipe it in via `--body`,
but typing/pasting at the prompt keeps it out of shell history). Scoped to
`selected` repos deliberately — this excludes `Ghost`, `Ghost-CLI`, and
`docker-library-ghost` (the `TryGhost` forks), so the key is never exposed
to workflows running in the context of upstream-derived CI.

If a new branchLeft-owned repo gets a `graphify.yml` caller workflow later
(e.g. `ghost-platform-tenants`, once it has real content), add it to
`--repos` by re-running the same command with the fuller list — `gh` will
update the existing secret's visibility rather than erroring.

## 3. Optionally tune the default model

Already set to `gemini-2.5-flash` as an org variable
(`GRAPHIFY_GEMINI_MODEL`) — cheap, adequate for structured extraction,
doesn't need frontier reasoning. To change it:

```bash
gh variable set GRAPHIFY_GEMINI_MODEL --org branchLeft --body "<model-name>"
```

Check [ai.google.dev/pricing](https://ai.google.dev/pricing) for current
model names/pricing before changing — Google's Flash-tier naming and
pricing shifts fairly often.

## 4. Verify

Push a trivial doc change to any wired repo, then check the Actions run:

- The `Run graphify extract` step should show `--backend gemini` in its
  command, not `--code-only`.
- `graphify-out/GRAPH_REPORT.md` and `graph.json` in the resulting commit
  should include nodes from doc/image files, not just AST.
- Check spend against the estimate in that repo's PR description (or the
  original graphify remediation plan) via
  [Google AI Studio's usage dashboard](https://aistudio.google.com/usage) —
  expected to round to cents/month at this repo fleet's size.
