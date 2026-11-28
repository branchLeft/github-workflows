# Comment narrative for `test_verify_package_attestations.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-11)

```text
"""Tests for verify-package-attestations.py, offline, against a stub gh and a stub curl.

The stubs are in fixtures/. The stub gh refuses what the real gh refused in a read-only
run against a live attestation (fixtures/real-gh-verify-output.json is that
attestation's result, without its bundle). A second mode, STUB_ENFORCE=0, is a gh that
accepts anything: the script must refuse a wrong tarball even then, because it reads the
certificate fields again itself.

The Sabotage class at the end breaks each control in a copy of this directory, requires the
named tests to go red there, and requires the unmodified copy to be green.
"""
```
