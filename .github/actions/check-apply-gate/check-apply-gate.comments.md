# Comment narrative for `check-apply-gate.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 409-414)

```text
    """The operands the input names, one per line, whitespace folded, blank lines
    dropped. An empty value gives none, which switches the check off.

    Raises:
        Unreadable: a line holds a top-level `&&`, so it names two operands.
    """
```
