# Comment narrative for `test_ansible_gated_step.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-8)

```text
"""Tests of ansible-gated-step.yml, run against the text of each step.

Each step's `run:` text runs under bash with `ssh`, `timeout` and `yq` stubbed on
PATH, and the `${{ }}` expressions of its `env:` are bound from a context the test
builds: an expression the context lacks fails the test, so no step passes on a
value nobody set. tools/sabotage_step.py breaks each control and requires its
test here to go red. The workflow's own page is ansible-gated-step.md.
"""
```
