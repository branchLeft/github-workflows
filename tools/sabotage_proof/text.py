"""Text for an edit, written without the indentation of the file it will be found in."""

from __future__ import annotations

import textwrap


def indented(text: str, by: int) -> str:
    """Dedent `text`, dropping one leading newline, then put every line that has text on it `by` spaces in.

    The lines of a block keep their indentation relative to each other and lose the rest,
    so a case is written as a triple-quoted block and the column the block sits at in the
    file it breaks is the one number `by`. A line with nothing on it stays empty. The text
    ends in a newline only if the block does, so a fragment may stop in the middle of a line.
    """
    body = textwrap.dedent(text.removeprefix("\n"))
    return "".join(f"{' ' * by}{line}" if line.strip() else line for line in body.splitlines(keepends=True))
