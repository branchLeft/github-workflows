# Comment narrative for `verify-image-attestation.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-7)

```text
# Verifies that an image was attested by the expected workflow of the named repo, on
# refs/heads/main. Fail closed: exit 0 only when gh verified an attestation for
# exactly that digest, signer and ref. Usage, identities and rationale:
# verify-image-attestation.md beside this script.
#
# VERIFY_PLAIN_ERRORS=1 prints refusals as plain text, not ::error:: annotations.
```

## Block 2 (source lines 54-60)

```text
# gh refuses to start without a non-empty token, even on --bundle-from-oci, which
# makes no GitHub API call: it reads the attestation from the registry and checks it
# against the Sigstore trust root. On that path only, and only when neither GH_TOKEN
# nor GITHUB_TOKEN holds a value, gh is given a fixed constant that is not a credential
# so its local gate passes. Verification is cryptographic, not account-based. A real
# token already set is left alone. Without the flag gh calls the API and needs a real
# token, so that path refuses here instead. The constant is never printed.
```
