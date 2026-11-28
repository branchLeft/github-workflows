# Comment narrative for `plan-has-changes.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 318-323)

```text
    """Whether a step is the read of a StackReference, which changes nothing.

    A StackReference is read on every run to resolve another stack's outputs: it creates,
    updates and deletes nothing, in this stack or the other. Only that type, and only the
    shape a real preview gives it, counts: any other `read`, or a StackReference step that
    carries a diff, a flag, an odd key or a type that is not its URN's, is a change."""
```

## Block 2 (source lines 527-532)

```text
    """Where the preview lists the resource, the goal says what its new state says.

    The preview's old and new state already agree (a `same` step that differs is a
    change), so a goal that matches the new state matches the old one: `protect` and
    `dependencies` are held to the stored values here, which is the owner's
    "unchanged" test for the keys the preview can show."""
```
