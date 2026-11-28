# Comment narrative for `test_verify_tag_signer.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-7)

```text
"""Tests for verify-tag-signer.py, run as CI runs it: a subprocess and its real
exit code, over tags made and signed by real git and ssh-keygen.

The Sabotage class breaks each control in a copy of the script (or of the
code owners file), and the test that guards it must go red there and green on
the unmodified copy. Reasons for each case: verify-tag-signer.md.
"""
```
