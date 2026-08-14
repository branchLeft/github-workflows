# Runbook: enabling graphify CI (and full semantic extraction)

## 0. Prerequisites for a caller repo

Two platform constraints govern which repos can call the workflows here.
Both are satisfied for the repos wired today; they matter when adding a new
one.

**A public repo cannot call a reusable workflow stored in a private repo.**
A hard GitHub restriction, and `access_level=organization` does not override
it — that setting only extends access to other private and internal repos in
the org. A caller that hits this fails with `workflow was not found`. This
repo is public so that public callers work; it holds no secrets or
tenant-identifying data by design, just parameterized CI logic.

**A reusable workflow's `permissions:` block can only grant what the calling
repo already allows.** The org-wide default is `read`, so a workflow
requesting `contents: write` gets capped before it runs, failing with:

```text
Error calling workflow 'branchLeft/github-workflows/.github/workflows/graphify.yml@v1'.
The workflow is requesting 'contents: write', but is only allowed 'contents: read'.
```

A repo admin grants a new caller repo write permission — scoped per repo, so
everything else in the org stays read-only by default:

```bash
gh api -X PUT repos/branchLeft/<repo>/actions/permissions/workflow \
  -f default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=false
```

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
  --repos shared-infra,ghost-platform,ghost-platform-docs,website,components,github-workflows
```

`gh` will prompt you to paste the key value (or pipe it in via `--body`,
but typing/pasting at the prompt keeps it out of shell history). Scoped to
`selected` repos deliberately — this excludes `Ghost`, `Ghost-CLI`,
`docker-library-ghost` and `Ghost-architecture-diagrams` (the `TryGhost` forks), so the key is never exposed
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
