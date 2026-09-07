# Runbook: prerequisites for a caller repo

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
Error calling workflow 'branchLeft/github-workflows/.github/workflows/<name>.yml@v1'.
The workflow is requesting 'contents: write', but is only allowed 'contents: read'.
```

A repo admin grants a new caller repo write permission — scoped per repo, so
everything else in the org stays read-only by default:

```bash
gh api -X PUT repos/branchLeft/<repo>/actions/permissions/workflow \
  -f default_workflow_permissions=write \
  -F can_approve_pull_request_reviews=false
```
