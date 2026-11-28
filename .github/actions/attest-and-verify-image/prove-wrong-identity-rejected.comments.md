# Comment narrative for `prove-wrong-identity-rejected.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-8)

```text
# Proves a wrong signer workflow is refused for its identity, through both
# verify paths. A network or TUF fault is not a refusal. Why: see the wrong
# identity section of verify-image-attestation.md beside this script.
#
# Usage:
#   ./prove-wrong-identity-rejected.sh <registry/name@sha256:64hex> \
#       <owner/repo> <owner/repo/.github/workflows/file.yml>
```
