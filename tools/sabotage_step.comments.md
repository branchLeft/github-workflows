# Comment narrative for `sabotage_step.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-8)

```text
"""Sabotage proof for tools/test_ansible_gated_step.py: break each control, require its test red.

Each case edits a copy of the repository so that one control no longer holds and
runs the named tests there; every named test must go red. Before any case, the
unedited tree must pass the whole suite. The controls are listed in
.github/workflows/ansible-gated-step.md. Run one case with a part of its name as
an argument, or list them with --list.
"""
```
