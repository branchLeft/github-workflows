# Comment narrative for `verify-package-attestations.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-15)

```text
"""Check the signed build attestation of the @branchleft npm tarballs.

Usage:
  verify-package-attestations.py scan ROOT
  verify-package-attestations.py check-inputs TARBALL --repo R --signer-workflow S --ref REF
  verify-package-attestations.py check-tarball TARBALL --repo R --signer-workflow S --ref REF
  verify-package-attestations.py prove-wrong-identity TARBALL --repo R --signer-workflow S --ref REF
  verify-package-attestations.py self-test

Needs GH_TOKEN (all but self-test and check-inputs), and `gh` and `curl` on PATH. Exit 0 only when
every check passed; 1 when a check refused; 2 on a usage error or a fault in this
program. Every unexpected state is a refusal: this program has no "skip" path.
The identity table, the rules and the reasons: verify-package-attestations.md.
"""
```

## Block 2 (source lines 76-83)

```text
# One row per published @branchleft package. `first_attested` is UNSET until the
# first release published through the attesting workflow is known. `grandfathered`
# is the exact list of versions published before attestation began; only those
# versions pass without an attestation, on a floored row and on an UNSET row alike,
# and every other version fails closed. The list is derived from the versions the
# repositories' lockfiles pin, and is extended only in a reviewed change.
# `ref_template` is the exact ref when the tag maps to the package
# version; None where it does not (the check then uses `ref_regex` alone).
```

## Block 3 (source lines 430-435)

```text
    """What is wrong with one verified result, read from the certificate fields.

    gh already enforces the flags it was given. These are the same facts read again
    from the certificate, which the workflow that was attested cannot alter, so a gh
    that is wrong, old or replaced still cannot make a bad tarball pass.
    """
```
