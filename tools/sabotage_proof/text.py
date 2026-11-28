"""Text for an edit, written without the indentation of the file it will be found in."""

from __future__ import annotations

import textwrap


def indented(text: str, by: int) -> str:
    """Dedent `text`, dropping one leading newline, then put every line that has text on it `by` spaces in.

    See text.comments.md, block 1.
    """
    body = textwrap.dedent(text.removeprefix("\n"))
    return "".join(f"{' ' * by}{line}" if line.strip() else line for line in body.splitlines(keepends=True))
