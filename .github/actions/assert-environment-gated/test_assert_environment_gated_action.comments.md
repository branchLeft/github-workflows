# Comment narrative for `test_assert_environment_gated_action.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-7)

```text
"""Tests for the action around assert-environment-gated.py.

The script's own tests are test_assert_environment_gated.py. These run the
action's `run:` body, cut out of action.yml, under bash with a stub `gh` first on
PATH, so what is asserted is the step CI runs and not a copy of it. The failure
that matters is the step passing over an environment that gates nothing.
"""
```
