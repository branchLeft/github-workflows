# verify-package-attestations

`verify-package-attestations.py` is the one home of two checks on the signed build
attestation of a `@branchleft` npm tarball:

- `scan ROOT`, the consumer check. It reads every `pnpm-lock.yaml` and
  `package-lock.json` under `ROOT`, and for each locked `@branchleft` package that
  is not listed as published before attestation began, it downloads the tarball and
  requires an attestation from the repository, workflow and ref that the identity
  table names for that package.
- `check-tarball` and `prove-wrong-identity`, the publisher checks, run by the
  composite action `attest-npm-tarball` right after it attests a tarball.

The composite action `verify-package-attestations` beside this file wraps `scan`.
A repository calls the action and holds no copy of the program.

## The identity table

`IDENTITIES` in the program is a reviewed literal. It is never read from the
lockfile, the tarball or the attestation, and the command line has no way to name
another table: an identity supplied by the thing being checked would let it vouch
for itself. One row per published package:

| Package | Repository | Workflow file | Ref the attested run must have |
| --- | --- | --- | --- |
| `@branchleft/ghost-platform-render-core` | `branchLeft/ghost-platform` | `publish-render-core.yml` | tag `render-core-vX.Y.Z`, equal to the version |
| `@branchleft/ghost-platform-tenant` | `branchLeft/ghost-platform` | `publish-tenant-package.yml` | tag `vX.Y.Z`, equal to the version |
| `@branchleft/ghost-platform-broker-api` | `branchLeft/ghost-platform` | `speckify.yml` | `refs/heads/main` |
| `@branchleft/hetzner-host` | `branchLeft/shared-infra` | `publish-hetzner-host.yml` | tag `hetzner-host-vX.Y.Z`, equal to the version |
| `@branchleft/components`, `@branchleft/brand-branchleft`, `@branchleft/brand-publicpress` | `branchLeft/components` | `publish.yml` | a tag `vX.Y.Z`; the tag is not the package version |
| `@branchleft/tsconfig`, `@branchleft/prettier-config`, `@branchleft/vitest-config`, `@branchleft/eslint-config` | `branchLeft/standards` | `publish.yml` | tag `vX.Y.Z`, equal to the version |

Each row also carries `first_attested` and `grandfathered`. `first_attested` is
`UNSET` on every row today, because no package has been published through an
attesting workflow yet. `grandfathered` is the exact list of versions published
before attestation began: the versions the repositories' lockfiles pinned when the
list was derived (`fixtures/pinned-before-attestation.tsv`, one `package<TAB>version`
line each). Only a listed version passes without an attestation, whether the row is
`UNSET` or has a floor. Every other version of the package, and every version of a
package whose list is empty, must carry a verified attestation, so the check
**fails closed** for it, with a message that names the row. The failure message of
a version that is not attested says which of four things is wrong:

| Situation | What the message says |
| --- | --- |
| The row is `UNSET` and its list is empty | None passes until the floor is set. |
| The row is `UNSET` and the version is not in its list | Only the listed versions pass. |
| The version is below `first_attested` and not listed in `grandfathered` | List it in a reviewed change, or install an attested version. |
| The version is at or above `first_attested` | An attestation is required. |

A version listed in `grandfathered` passes with no download and no lookup. Extending
a list is a reviewed change to the table and is right only for a version that was
published before attestation began. Setting the floor is a reviewed change made
after the first attested release of that package exists: set `first_attested` to
that version and list, by exact version, every earlier version the registry holds.
A listed version at or above the floor, a floor that is not a version, a listed
version that is not a version and a version listed twice are all refused by the
table check, which runs before every scan.

A `@branchleft` package that is not in the table is refused: add a reviewed row
before it can be installed.

## What `scan` checks, in order, for each entry

1. The lockfile gives a `sha512` integrity and a tarball URL of the form
   `https://npm.pkg.github.com/download/@branchleft/NAME/VERSION/HASH` whose name and
   version are the entry's own.
2. The tarball is downloaded with `curl`. The token travels on standard input, never
   on a command line. curl does not forward a custom Authorization header to a
   different host on a redirect; that is curl's documented behaviour and has not
   yet been exercised against GitHub Packages.
3. The downloaded bytes' `sha512` equals the lockfile `integrity`. This ties the
   attestation to what the install fetches.
4. `package/package.json` inside the tarball names the same package and version as
   the lockfile.
5. `gh attestation verify` is run with the repository, the signer workflow, the
   issuer, the SLSA provenance predicate, `--deny-self-hosted-runners` and, where the
   row has a ref template, `--source-ref`.
6. The program then reads the certificate fields of the result itself and requires,
   independently of what `gh` accepted: the attested subject digest is this
   tarball's `sha256`; the issuer is GitHub Actions'; the workflow repository and
   source repository are the row's; the signer identity is exactly
   `https://github.com/REPO/.github/workflows/FILE@REF`; the source ref matches the
   row's pattern (and equals the template for the version where there is one); the
   runner is GitHub-hosted; the signer and source commits are the same 40-hex sha.
7. The attested commit is on `main`: `gh api repos/REPO/compare/main...COMMIT` must
   answer `behind` or `identical`. Any other answer, an empty answer or a failed call
   refuses.

Every refusal is collected and printed; the exit status is 1 if any entry was
refused. A lockfile that cannot be read, a `@branchleft` line the reader does not
understand, a tarball URL outside the section it reads, a missing token and an
absence of any lockfile are refusals too. The scope is matched in any case, so a
`@BRANCHLEFT` name is read and refused, never skipped. In `package-lock.json` the
GitHub Packages tarball URLs under `packages` are counted against the entries read,
so an entry under an alias key refuses. A fault inside the program exits 2.

## The publisher checks

`check-tarball` takes the identity as literals from the calling workflow
(`--repo`, `--signer-workflow`, `--ref`). Before any call, the repo, signer workflow
file and ref must match a row of the identity table: a consumer that no row claims is
refused by `check-inputs` and by every publisher check, so the mismatch shows before the
first publish. It then runs steps 5 to 7 above for that
identity, and retries up to five times because an attestation can take a moment to
reach the API. `prove-wrong-identity` then requires `gh` to refuse a wrong signer
workflow and a wrong source ref, and to refuse them **for the identity**: a refusal
that carries a network, Sigstore, token or HTTP-status fault is not a refusal, and
a wrong identity that `gh` accepts fails the step because it would mean the verify
command proves nothing.

## What it does not do

- It does not make GitHub Packages verify anything. The registry is unchanged; the
  attestation is stored by GitHub, keyed by the tarball's `sha256`.
- It does not check the lockfile's other, third-party packages.
- It does not decide when a version is released. Setting a floor is a reviewed
  change to the table.

## Tests

`test_verify_package_attestations.py` runs the program against a stub `gh` and a
stub `curl` in `fixtures/`. The stub `gh` answers in the words the real `gh` used
in a read-only run against a live attestation, and `fixtures/real-gh-verify-output.json`
is that attestation's result without its bundle. A second stub mode is a `gh` that
accepts anything, to prove the program refuses a wrong tarball even then. The
`Sabotage` class breaks each control in a copy of the directory and requires the
named tests to go red there, then requires the unmodified copy to be green.
