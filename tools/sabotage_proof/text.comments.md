# Comment narrative for `text.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 9-15)

```text
    """Dedent `text`, dropping one leading newline, then put every line that has text on it `by` spaces in.

    The lines of a block keep their indentation relative to each other and lose the rest,
    so a case is written as a triple-quoted block and the column the block sits at in the
    file it breaks is the one number `by`. A line with nothing on it stays empty. The text
    ends in a newline only if the block does, so a fragment may stop in the middle of a line.
    """
```
