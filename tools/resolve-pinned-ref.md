# resolve-pinned-ref.sh

Resolves the revision of branchLeft/github-workflows that the calling repo
pinned a reusable workflow to, and writes it to GITHUB_OUTPUT as `ref`. Run it
from the caller's checkout root with `CALLED_WORKFLOW` naming the file.

Nothing the runner provides identifies the called revision: the
`job_workflow_sha` context is empty inside a reusable workflow, and an empty
`ref:` makes actions/checkout fall back to the default branch, so a caller
pinned to a tag would silently run main. The caller's own workflow files do
name it. Exactly one distinct pin is required: two callers naming different
revisions is drift, and guessing which governs reintroduces the silence.

The reusable workflows carry this body inline, because the pinned revision
cannot be checked out before it is known; `resolve-pinned-ref.test.sh` asserts
every line of the script appears in each workflow that inlines it.
