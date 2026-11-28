# Comment narrative for `spend-gate.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 99-103)

```text
    """Why the change summary cannot be squared with the listed steps, or None.

    The summary may count fewer changes than are listed (a replace is several steps),
    never more (a change that is not listed cannot be judged) and never none while
    a changing step is listed. A `read` count is the clean reads, which are not changes."""
```
