# Fixtures for verify-package-attestations

## stub-gh.py

Stands in for `gh` in the tests of verify-package-attestations.py.

Two calls are answered and every other call exits 99:
  gh attestation verify FILE --repo R --signer-workflow S [--source-ref X] ...
  gh api repos/R/compare/main...SHA --jq .status

The verify call looks up $STUB_ATTEST_DIR/<sha256 of FILE>.json, the way the real
call looks the digest up in the attestation API. No file is the real wording for an
unknown digest (HTTP 404). With STUB_ENFORCE unset it then refuses what the real gh
refuses, in the words the real gh used against a live attestation; with
STUB_ENFORCE=0 it is a gh that accepts anything and prints the file as it is.
STUB_FAULT=network|tuf makes the verify call fail the way a fault does.
STUB_COMPARE_STATUS answers the compare call (default behind); STUB_COMPARE_FAIL=1
makes it fail. Every call is appended to $STUB_LOG when that is set.

`run` is the whole stub, so the tests can call it in-process; run as a script it
is the program the tests put on PATH.

## stub-curl.py

Stands in for `curl` in the tests of verify-package-attestations.py.

It answers only the download the verifier makes: a GitHub Packages tarball URL,
saved to --output. The bytes come from $STUB_TARBALLS/<name>-<version>.tgz, with
the scope's slash dropped; a missing file is curl's exit 22 for a 404 answer. The
token must arrive on standard input as a header line and must not be on the
command line. Every call is appended to $STUB_LOG when that is set.

`run` is the whole stub, so the tests can call it in-process; run as a script it
is the program the tests put on PATH.
