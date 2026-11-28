# Comment narrative for `host_keys.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-6)

```text
"""Per-host keys: the same break is made in the action (the plan side) and in the apply step, each with its own red test.

A table of near-identical cases is built here rather than written out twice. Each edit
is a fragment found exactly once in its file, so a change to the text makes the case fail
to apply, not pass.
"""
```
