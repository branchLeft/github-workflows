# attest-npm-tarball

A composite action that signs one packed npm tarball with a GitHub artifact
attestation (SLSA build provenance, keyless), then proves the attestation before
anything is published. A publish workflow packs once, calls this action on the
`.tgz`, and then publishes that same `.tgz` file, so the bytes attested are the
bytes published. The registry is not asked to support provenance: the attestation
is stored by GitHub and keyed by the tarball's `sha256`.

The consumer check that reads the result is the sibling action
`verify-package-attestations`. Both run the one program,
`../verify-package-attestations/verify-package-attestations.py`, so the publisher
and the consumer judge an attestation by the same rules. The rules are in
[`verify-package-attestations.md`](../verify-package-attestations/verify-package-attestations.md).

## The steps, in order

1. **Check the inputs.** The tarball ends in `.tgz` and is a file; `repo` is
   `owner/name`; `signer-workflow` is a workflow file of that repository; `ref` is an
   exact `refs/tags/...` or `refs/heads/...`. Nothing is attested for an identity
   that is not well formed.
2. **Attest the tarball** with `actions/attest-build-provenance`, pinned by commit.
3. **Verify the attestation through the GitHub API**, for exactly the identity the
   caller wrote: repository, signer workflow, source ref, GitHub-hosted runner, and
   a commit that is on `main`. The identity table is checked first. The verify is
   retried (`attempts`, `pause-seconds`) because the attestation can take a moment
   to reach the API.
4. **Prove a wrong identity is rejected.** A wrong signer workflow and a wrong
   source ref must each be refused, and refused for the identity: a network,
   Sigstore, token or HTTP-status fault is not a refusal and fails the step. If a
   wrong identity is accepted, the verify command proves nothing and the step fails.

No step can be skipped by a condition or forgiven by `continue-on-error`. A step
that fails fails the job, so the publish step after it does not run.

## What the caller gives and what it must hold

| Input | Meaning |
| --- | --- |
| `tarball` | Path of the packed tarball. |
| `repo` | `owner/name` of the repository that owns the signer workflow. Required, no default. |
| `signer-workflow` | `owner/name/.github/workflows/file.yml` of the workflow expected to sign. Required, no default. |
| `ref` | The exact ref the attested run must have. Required, no default. Build it from the version the job packed, not from the ref that triggered the run: an identity read from the run being checked agrees with itself and proves nothing. |
| `github-token` | The token the lookups use. The run's own is enough; pass nothing wider. |
| `attempts`, `pause-seconds` | The retry. Defaults 5 and 12: waits of 12, 24, 36 and 48 seconds. |

The job that calls it needs `id-token: write` and `attestations: write`, held by
that job alone. The caller's job also holds whatever it already needs to pack and
publish; this action asks for nothing else and publishes nothing.

## Why the order matters

Attest and verify run before the publish step, so a tarball that cannot be proven
is never published. The action must run in the same job as the pack, on the same
file: attesting a rebuilt tarball attests different bytes, because a tarball is not
reproducible unless the packing is.
