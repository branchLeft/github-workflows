"""Read what a host wrote for secrets, and name the Ansible task that failed.

One home for the scan of a host's output. The plan step of the ssh-check-hosts action reads each
host's diff through it, and the apply job of ansible-gated-apply.yml reads the standard error of a
host whose playbook failed through it. The text is read as bytes (Latin-1, so one character is one
byte, as a grep in the C locale reads it) and is never printed: the two lines on standard output
carry a list of pattern names and, when nothing matched, the name of one task.

    host_output_scan.py FILE [--keylines FILE] [--allowances TEXT]

    hits=<pattern names, comma and space separated, or none>
    task=<the text between the brackets of the last TASK header before the first fatal line, at most
          160 characters, or empty; always empty when hits is not none>

With --report the script prints instead, for a host whose diff is withheld, where its bytes are:
up to 15 lines `<bytes> TASK [<role> : <task name>]`, largest first, and for each pattern that
matched the task it fell under. A line of that report is a size, a pattern name or a TASK header
name (cut at 120 characters, any character outside letters, digits, space and `_ . / : ; , ( ) = -`
turned into `?`, and replaced by its place in the output when it reads as a secret itself); it is
never the text of any other line.

What the scan reads, and the named allowances: README, section "What the scan reads".
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import NamedTuple

USAGE_ERROR = 2
TASK_LIMIT = 160
REPORT_ROWS = 15
REPORT_HITS = 30
NAME_LIMIT = 120

# A pattern is a name and an extended regular expression, read without regard to case. The list is
# a backstop: the control is no_log and diff: false on the task (ANS-16).
PATTERNS: tuple[tuple[str, str], ...] = (
    ("private-key", r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY"),
    ("aws-access-key", r"(AKIA|ASIA)[0-9A-Z]{16}"),
    ("github-token", r"(gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{20,})"),
    ("slack-token", r"xox[abprs]-[A-Za-z0-9-]{10,}"),
    ("payment-key", r"sk_(live|test)_[0-9A-Za-z]{20,}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."),
    (
        "credential-assignment",
        r"(passw(or)?d|passphrase|secret|token|api[_-]?key|private[_-]?key|credential)"
        r'[A-Za-z0-9_.-]*"?\s*[:=]\s*"?[^\s"]{4,}',
    ),
)
# The one pattern that reads the copy with the shapes that are never a credential taken out.
ASSIGNMENT = "credential-assignment"
KEY_MATERIAL = "ci-key-material"

# The four shapes, in order, as a regular expression and what replaces each match. The copy is
# lower-cased first, so each is written in lower case.
SHAPES: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(expression, re.ASCII), replacement)
    for expression, replacement in (
        # the sudoers tag
        (r"(^|[\s)])nopasswd:", r"\1"),
        # a ${NAME} or ${NAME:?...} reference
        (r"\$\{[a-z_][a-z0-9_]*(:?\?[^}]*)?\}", ""),
        # a $(cat PATH) reference
        (r"\$\(cat [^\s)]+\)", ""),
        # a key that names a file or a path, whose value starts with / or ~
        (r'[a-z0-9_.-]*[_.-](file|filename|path|dir)"?\s*[:=](\s*"?[/~])', r"\2"),
        # a value that is true, false, null or none
        (r'[:=]\s*"?(true|false|null|none)"?\s*([\],}]|$)', r" \2"),
    )
)

# ASCII upper case to lower case only, as `tr '[:upper:]' '[:lower:]'` does in the C locale.
LOWER = {code: code + 32 for code in range(ord("A"), ord("Z") + 1)}
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
HEADER_BEFORE = "--- before"
HEADER_AFTER = "+++ after: "
TASK_HEADER = "TASK ["
FATAL = "fatal: "
# A header the report reads: the whole line must be `TASK [name]`, a space and a run of stars.
TASK_HEADER_LINE = re.compile(r"TASK \[(.*)\] \*+", re.ASCII)
# Where output that belongs to no TASK begins: the play banner, the recap and a handler's banner.
OUTSIDE_STARTS = ("PLAY [", "PLAY RECAP", "RUNNING HANDLER [")
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9 _./:;,()=-]", re.ASCII)
OUTSIDE = "outside any TASK"


class Allowance(NamedTuple):
    """One line of one file, as written in the allowances of the action: `path|line`."""

    path: str
    line: str


class Verdict(NamedTuple):
    """What the scan found: the names of the patterns that matched, and the failed task."""

    hits: list[str]
    task: str


def parse_allowances(text: str) -> list[Allowance]:
    """Read `path|line` entries, one per line; an entry with no path is dropped."""
    entries: list[Allowance] = []
    for entry in text.split("\n"):
        cut = entry.find("|")
        if cut > 0:
            entries.append(Allowance(entry[:cut], entry[cut + 1 :]))
    return entries


def split_lines(text: str) -> list[str]:
    """The lines of ``text``, with none for a final newline."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def applies(file: str, allowance: Allowance) -> bool:
    """Whether the path of a diff header is the allowance's path or ends in `/` and that path."""
    return file == allowance.path or (len(file) > len(allowance.path) and file.endswith("/" + allowance.path))


def blank_allowed(lines: list[str], allowances: list[Allowance]) -> list[str]:
    """Blank each line that is exactly an allowance, under the diff header of its file.

    The whole line, after its first character and with its outer whitespace trimmed, must equal the
    entry's text. Only the text is blanked: its first character stays.
    """
    result: list[str] = []
    file = ""
    for line in lines:
        if line.startswith(HEADER_BEFORE):
            file = ""
        elif line.startswith(HEADER_AFTER):
            file = line[len(HEADER_AFTER) :]
        elif file != "" and line[:1] in ("-", "+", " "):
            text = line[1:].lstrip(" \t").rstrip(" \t\r")
            if any(text == a.line and applies(file, a) for a in allowances):
                line = line[:1]
        result.append(line)
    return result


def scrub(line: str) -> str:
    """The lower-cased line with the shapes that are never a credential taken out."""
    text = line.translate(LOWER)
    for shape, replacement in SHAPES:
        text = shape.sub(replacement, text)
    return text


BLOCK_SCALAR = re.compile(r'^(\s*(?:-\s+)?[^\s:]*[:=]\s*"?)[>|][-+0-9]*\s*$')


def block_scalar_lines(lines: list[str], diff: bool | None = None) -> list[str]:
    """Each `key: >-` or `key: |` line joined with the more-indented lines under it.

    The assignment pattern reads one line at a time, so a secret written on the line after a block
    scalar indicator is read here, beside its key. In a diff each line starts with one character
    (`+`, `-` or a space) that is not part of the text, so it is taken off before the indent is
    measured. Blank lines are skipped; a line at the key's indent or less ends the scalar.
    """
    if diff is None:
        diff = any(line.startswith(HEADER_BEFORE) for line in lines)
    bodies = [line[1:] if diff and line[:1] in ("+", "-", " ") else line for line in lines]
    joined: list[str] = []
    for index, body in enumerate(bodies):
        head = BLOCK_SCALAR.match(body)
        if head is None:
            continue
        indent = len(body) - len(body.lstrip(" "))
        values: list[str] = []
        for follow in bodies[index + 1 :]:
            if follow.strip() == "":
                continue
            if len(follow) - len(follow.lstrip(" ")) <= indent:
                break
            values.append(follow.strip())
        joined.append(head.group(1) + " ".join(values))
    return joined


def matches(expression: re.Pattern[str], lines: list[str]) -> bool:
    """Whether any one line holds the expression."""
    return any(expression.search(line) for line in lines)


def failed_task(lines: list[str]) -> str:
    """The text between the brackets of the last TASK header before the first fatal line."""
    task = ""
    for line in lines:
        if line.startswith(TASK_HEADER):
            task = line[len(TASK_HEADER) :]
            close = task.find("]")
            if close >= 0:
                task = task[:close]
        elif line.startswith(FATAL):
            return task[:TASK_LIMIT]
    return ""


def detect(lines: list[str], key_lines: list[str], allowances: list[Allowance], diff: bool | None = None) -> list[str]:
    """The names of the patterns that match the lines, in the order of PATTERNS, key material last."""
    blanked = blank_allowed(lines, allowances)
    scanned = [scrub(line) for line in blanked + block_scalar_lines(blanked, diff)]
    hits: list[str] = []
    for name, expression in PATTERNS:
        compiled = re.compile(expression, re.IGNORECASE | re.ASCII)
        if matches(compiled, scanned if name == ASSIGNMENT else lines):
            hits.append(name)
    if any(key and key in line for key in key_lines for line in lines):
        hits.append(KEY_MATERIAL)
    return hits


def scan(text: str, key_lines: list[str], allowances: list[Allowance]) -> Verdict:
    """Scan one host's output. The task is named only when no pattern matched."""
    lines = split_lines(CONTROL.sub("", text))
    hits = detect(lines, key_lines, allowances)
    return Verdict(hits, "" if hits else failed_task(lines))


class Segment(NamedTuple):
    """The lines under one TASK header (`task` is its number from 1) or under none (`task` is 0)."""

    task: int
    name: str
    lines: list[str]


def segments(lines: list[str]) -> list[Segment]:
    """Cut the output at each TASK header and at each play, recap or handler banner."""
    result = [Segment(0, "", [])]
    count = 0
    for line in lines:
        header = TASK_HEADER_LINE.fullmatch(line.rstrip("\r"))
        if header is not None:
            count += 1
            result.append(Segment(count, header.group(1), [line]))
        elif line.startswith(OUTSIDE_STARTS):
            result.append(Segment(0, "", [line]))
        else:
            result[-1].lines.append(line)
    return result


def label(segment: Segment, key_lines: list[str]) -> str:
    """The published name of a task: its cleaned, cut header text, or its number if that reads as a secret."""
    if segment.task == 0:
        return OUTSIDE
    cleaned = UNSAFE_NAME.sub("?", segment.name)[:NAME_LIMIT]
    if detect([segment.name, cleaned], key_lines, []):
        return f"TASK number {segment.task} (its name reads as a secret and is not shown)"
    return f"TASK [{cleaned}]"


def report(text: str, key_lines: list[str], allowances: list[Allowance]) -> list[str]:
    """Where the bytes of a withheld output are, and which task each matched pattern fell under.

    Every line is built from a byte count, a pattern name, a fixed phrase or a label(), and from
    nothing else: no line of the output, and no value, path or diff text, is ever copied.
    """
    lines = split_lines(CONTROL.sub("", text))
    diff = any(line.startswith(HEADER_BEFORE) for line in lines)
    parts = segments(lines)
    labels = [label(part, key_lines) for part in parts]
    sizes: dict[str, int] = {}
    for part, name in zip(parts, labels):
        sizes[name] = sizes.get(name, 0) + sum(len(line) + 1 for line in part.lines)
    outside = sizes.pop(OUTSIDE, 0)
    ranked = sorted(sizes.items(), key=lambda item: (-item[1], item[0]))
    out = [f"output bytes by task, largest first ({min(REPORT_ROWS, len(ranked))} of {len(ranked)} tasks):"]
    out += [f"{size} {name}" for name, size in ranked[:REPORT_ROWS]]
    out.append(f"{outside} {OUTSIDE}")
    found = detect(lines, key_lines, allowances)
    if not found:
        out.append("scan hits by task: none")
        return out
    out.append("scan hits by task:")
    rows: list[str] = []
    seen = {name: [] for name in found}
    for part, name in zip(parts, labels):
        for hit in detect(part.lines, key_lines, allowances, diff):
            if hit in seen and name not in seen[hit]:
                seen[hit].append(name)
    for hit in found:
        rows += [f"{hit} in {name}" for name in seen[hit] or ["no one task"]]
    out += rows[:REPORT_HITS]
    if len(rows) > REPORT_HITS:
        out.append(f"{len(rows) - REPORT_HITS} more hit rows not shown")
    return out


def read_latin1(path: Path) -> str:
    """The file as one character per byte."""
    return path.read_bytes().decode("latin-1")


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", maxsplit=1)[0])
    parser.add_argument("file", type=Path)
    parser.add_argument("--keylines", type=Path)
    parser.add_argument("--allowances", default="")
    parser.add_argument("--report", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return USAGE_ERROR if stop.code else 0
    try:
        text = read_latin1(args.file)
        key_lines = split_lines(read_latin1(args.keylines)) if args.keylines else []
    except OSError:
        print("host_output_scan: a file could not be read", file=sys.stderr)
        return USAGE_ERROR
    allowances = parse_allowances(args.allowances)
    if args.report:
        sys.stdout.buffer.write(("\n".join(report(text, key_lines, allowances)) + "\n").encode("latin-1"))
        return 0
    verdict = scan(text, key_lines, allowances)
    out = f"hits={', '.join(verdict.hits) or 'none'}\ntask={verdict.task}\n"
    sys.stdout.buffer.write(out.encode("latin-1"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
