# attest-and-verify-image

The one home of the attest-then-verify step for every container image a
branchLeft repository publishes. A calling workflow pushes an image, reads the
pushed digest back, then calls this composite action, which:

1. checks that the digest is `sha256:<64 hex>` and the image name carries no tag;
2. signs the digest with a keyless GitHub artifact attestation (SLSA build
   provenance), stored beside the image in the registry;
3. verifies the attestation through the GitHub API, then from the registry's own
   copy of the bundle, each with up to five attempts;
4. requires a wrong signer identity to be refused, and refused for the identity
   rather than for a network, token or Sigstore fault.

Any step that fails fails the job. No step can be skipped by a condition.

## Calling it

```yaml
jobs:
  push:
    permissions:
      contents: read
      packages: write
      id-token: write
      attestations: write
    steps:
      # ... build, log in to the registry, push, read the digest back ...
      - name: Attest and verify the pushed image
        uses: branchLeft/github-workflows/.github/actions/attest-and-verify-image@<full commit sha>
        with:
          image: ${{ env.IMAGE }}
          digest: ${{ steps.digest.outputs.digest }}
          signer-workflow: branchLeft/<repo>/.github/workflows/<publish-file>.yml
          repo: branchLeft/<repo>
```

- `image` is the lower-case name without a tag, `digest` the pushed manifest
  digest.
- `signer-workflow` and `repo` are required and have no default. Pass them as
  literals. An identity read from the run being checked (`github.workflow_ref`
  and the like) would agree with itself and prove nothing. The signer must be a
  workflow file of the repository named in `repo`.
- Pin the action to a full commit sha. The scripts are read from the action's own
  directory at that commit, so the caller needs no checkout of this repository
  and carries no copy of the scripts.
- The job needs `id-token: write`, `attestations: write` and `packages: write`,
  held by the publishing job alone, and must already be logged in to the
  registry.

## Files

| File | Purpose |
| --- | --- |
| `action.yml` | The composite action. |
| `verify-image-attestation.sh` | The verifier, which wraps `gh attestation verify` with every identity flag fixed. Also the script a host runs. See `verify-image-attestation.md`. |
| `prove-wrong-identity-rejected.sh` | The wrong-identity control. |
| `test-verify-image-attestation.sh` | Offline test of both scripts against a stub `gh`. See `test-verify-image-attestation.md`. |
| `action.test.js` | Lint of the action definition and the scripts' fixed flags. |

CI for this directory is `.github/workflows/attest-and-verify-image-ci.yml`.
