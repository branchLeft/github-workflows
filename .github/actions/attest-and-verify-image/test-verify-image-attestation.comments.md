# Comment narrative for `test-verify-image-attestation.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-7)

```text
# Proves the verify and wrong-identity scripts refuse what they should, against
# a stub gh on PATH, so no network or attestation is needed. The stub and its
# cases are described in test-verify-image-attestation.md beside this script.
#
# Usage:
#   ./test-verify-image-attestation.sh
```

## Block 2 (source lines 169-173)

```text
# (h) The live wording, from run 41728033706. gh prints "verifying with issuer"
# and then the verify script prints the line naming the identity. A token,
# Sigstore or network fault prints that last line too, so it alone is not a
# refusal. A fake verify script prints each text exactly, beside a copy of the
# prove script.
```
