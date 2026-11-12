#!/usr/bin/env python3
"""Check that a caller's attestation verify job cannot pass silently.
The rules and their reasons are in check-verify-job-hardened.md beside this file."""

from __future__ import annotations

import argparse
import re
import sys
from typing import NamedTuple

CALL = re.compile(r"(?:^|/)\.github/actions/verify-package-attestations(?:@|$)", re.IGNORECASE)
SOURCE = re.compile(r"^branchLeft/github-workflows/\.github/actions/verify-package-attestations@\S+$", re.IGNORECASE)
GRANTS = {"contents": "read", "packages": "read"}
BLOCK_HEADER = re.compile(r"[|>][+-]?[1-9]?[+-]?")
PLAIN_KEY = re.compile(r"^([^\s'\"\[\]{}&*!|>%@`#,?][^#]*?)[ \t]*:(?:[ \t]+(.*))?$")
QUOTED_KEY = re.compile(r"^(\"(?:[^\"\\]|\\.)*\"|'(?:[^']|'')*')[ \t]*:(?:[ \t]+(.*))?$")


class Finding(NamedTuple):
    code: str
    job: str
    message: str


class Unreadable(Exception):
    """The workflow is not a shape this script reads with certainty; the check fails."""


class Scalar(NamedTuple):
    text: str
    style: str  # plain, quoted, block, flow or null



def is_sequence_item(content: str) -> bool:
    return content == "-" or content.startswith("- ")


def strip_comment(text: str) -> str:
    """The text up to a `#` that starts a comment: one at the start or after a space or
    tab, outside a quoted string. A quote opens only at the start of a token, so an
    apostrophe inside a plain word is not one."""
    quote = ""
    i = 0
    while i < len(text):
        char = text[i]
        if quote:
            if quote == '"' and char == "\\":
                i += 2
                continue
            if char == quote:
                if quote == "'" and text[i + 1 : i + 2] == "'":
                    i += 2
                    continue
                quote = ""
        elif char in "'\"" and (i == 0 or text[i - 1] in " \t[{,("):
            quote = char
        elif char == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
        i += 1
    return text.rstrip()


def closing_quote(text: str, start: int) -> int:
    """The index of the quote that closes the string opened at `start`, or -1."""
    quote = text[start]
    i = start + 1
    while i < len(text):
        char = text[i]
        if quote == '"' and char == "\\":
            i += 2
            continue
        if char == quote:
            if quote == "'" and text[i + 1 : i + 2] == "'":
                i += 2
                continue
            return i
        i += 1
    return -1


def bracket_depth(text: str) -> int:
    depth, i = 0, 0
    while i < len(text):
        char = text[i]
        if char in "'\"" and (i == 0 or text[i - 1] in " \t[{,:"):
            end = closing_quote(text, i)
            if end == -1:
                return depth
            i = end
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        i += 1
    return depth


def split_entry(content: str) -> tuple[str, str] | None:
    """The key and the raw rest of a `key: rest` line, or None when it is not one."""
    quoted = QUOTED_KEY.match(content)
    if quoted:
        raw = quoted.group(1)
        if raw[0] == '"':
            if "\\" in raw:
                raise Unreadable("a double-quoted key holds an escape, which can spell any name")
            key = raw[1:-1]
        else:
            key = raw[1:-1].replace("''", "'")
        return key, quoted.group(2) or ""
    plain = PLAIN_KEY.match(content)
    if plain:
        return plain.group(1).strip(), plain.group(2) or ""
    return None


class Parser:
    """A strict reader of the block-style YAML subset workflows use. Anything it cannot
    read with certainty raises Unreadable: tabs in indentation, anchors, aliases, tags,
    merge keys, a second document, a duplicate key, a nested sequence, a line that is not
    a mapping entry. Flow collections are kept as opaque text."""

    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.i = 0

    def peek(self) -> tuple[int, int, str] | None:
        j = self.i
        while j < len(self.lines):
            line = self.lines[j]
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                indent = len(line) - len(line.lstrip(" "))
                if line[indent : indent + 1] == "\t":
                    raise Unreadable(f"line {j + 1}: a tab in the indentation")
                return j, indent, line.strip()
            j += 1
        return None

    def document(self) -> dict:
        first = self.peek()
        if first and (first[2] == "---" or first[2].startswith("--- ")):
            self.i = first[0] + 1
        nxt = self.peek()
        if nxt is None:
            raise Unreadable("the workflow is empty")
        node = self.block(nxt[1])
        left = self.peek()
        if left is not None:
            raise Unreadable(f"line {left[0] + 1}: text after the end of the document")
        if not isinstance(node, dict):
            raise Unreadable("the top level is not a mapping")
        return node

    def block(self, indent: int):
        found = self.peek()
        if found is None:
            return Scalar("", "null")
        return self.sequence(indent) if is_sequence_item(found[2]) else self.mapping(indent)

    def mapping(self, indent: int) -> dict:
        result: dict = {}
        while True:
            found = self.peek()
            if found is None or found[1] < indent:
                break
            j, here, content = found
            if here > indent:
                raise Unreadable(f"line {j + 1}: unexpected indentation")
            if is_sequence_item(content):
                raise Unreadable(f"line {j + 1}: a sequence item where a mapping key is expected")
            entry = split_entry(content)
            if entry is None:
                raise Unreadable(f"line {j + 1}: not a mapping entry")
            key, rest = entry
            if key == "<<":
                raise Unreadable(f"line {j + 1}: a merge key")
            if key in result:
                raise Unreadable(f"line {j + 1}: the key `{key}` appears twice in one mapping")
            result[key] = self.value(rest, indent, j, same_indent_sequence=True)
        return result

    def sequence(self, indent: int) -> list:
        items: list = []
        while True:
            found = self.peek()
            if found is None or found[1] != indent or not is_sequence_item(found[2]):
                break
            j, _, content = found
            after = content[1:]
            lead = len(after) - len(after.lstrip(" "))
            body = after.strip()
            if body == "" or body.startswith("#"):
                self.i = j + 1
                deeper = self.peek()
                items.append(self.block(deeper[1]) if deeper and deeper[1] > indent else Scalar("", "null"))
                continue
            if is_sequence_item(body):
                raise Unreadable(f"line {j + 1}: a nested sequence")
            if body[0] not in "[{" and split_entry(body) is not None:
                self.lines[j] = " " * (indent + 1 + lead) + body
                self.i = j
                items.append(self.mapping(indent + 1 + lead))
            else:
                items.append(self.value(body, indent, j, same_indent_sequence=False))
        return items

    def value(self, rest: str, parent: int, j: int, same_indent_sequence: bool):
        text = strip_comment(rest).strip()
        self.i = j + 1
        if text == "":
            nxt = self.peek()
            if nxt and (nxt[1] > parent or (same_indent_sequence and nxt[1] == parent and is_sequence_item(nxt[2]))):
                return self.block(nxt[1])
            return Scalar("", "null")
        if BLOCK_HEADER.fullmatch(text):
            return self.block_scalar(parent)
        if text[0] in "&*!":
            raise Unreadable(f"line {j + 1}: an anchor, an alias or a tag")
        if text[0] in "[{":
            return self.flow(text, j)
        if text[0] in "'\"":
            return self.quoted(text, j)
        return self.plain(text, parent)

    def block_scalar(self, parent: int) -> Scalar:
        body: list[str] = []
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if line.strip():
                if len(line) - len(line.lstrip(" ")) <= parent:
                    break
                body.append(line.strip())
            self.i += 1
        return Scalar("\n".join(body), "block")

    def plain(self, text: str, parent: int) -> Scalar:
        while True:
            nxt = self.peek()
            if nxt is None or nxt[1] <= parent:
                break
            text += " " + strip_comment(nxt[2])
            self.i = nxt[0] + 1
        return Scalar(text.strip(), "plain")

    def quoted(self, text: str, j: int) -> Scalar:
        buffer = text
        while closing_quote(buffer, 0) == -1:
            if self.i >= len(self.lines):
                raise Unreadable(f"line {j + 1}: a quoted string that never closes")
            buffer += " " + self.lines[self.i].strip()
            self.i += 1
        end = closing_quote(buffer, 0)
        if strip_comment(buffer[end + 1 :]).strip():
            raise Unreadable(f"line {j + 1}: text after a quoted string")
        return Scalar(buffer[1:end], "quoted")

    def flow(self, text: str, j: int) -> Scalar:
        buffer = strip_comment(text)
        while bracket_depth(buffer) > 0:
            if self.i >= len(self.lines):
                raise Unreadable(f"line {j + 1}: a flow collection that never closes")
            buffer += " " + strip_comment(self.lines[self.i].strip())
            self.i += 1
        return Scalar(buffer.strip(), "flow")


def named(mapping: dict, name: str) -> list:
    """The values of every key spelled `name`, in any letter case."""
    return [value for key, value in mapping.items() if key.lower() == name]


def one(mapping: dict, name: str, where: str):
    found = named(mapping, name)
    if len(found) > 1:
        raise Unreadable(f"{where} holds `{name}` more than once")
    return found[0] if found else None


def needs_of(job: dict, where: str) -> list[str]:
    node = one(job, "needs", where)
    if node is None or (isinstance(node, Scalar) and node.style == "null"):
        return []
    if isinstance(node, Scalar):
        if node.style == "flow":
            if not node.text.startswith("["):
                raise Unreadable(f"{where} has a `needs` that is not a list")
            names = [part.strip().strip("'\"") for part in node.text[1:-1].split(",")]
            return [name for name in names if name]
        return [node.text]
    if isinstance(node, list) and all(isinstance(item, Scalar) for item in node):
        return [item.text for item in node]
    raise Unreadable(f"{where} has a `needs` that is not a list of job ids")


def grants_of(node) -> dict[str, str] | None:
    """The permission grants as written, or None when the block is not a mapping of scalars."""
    if isinstance(node, dict):
        if all(isinstance(value, Scalar) for value in node.values()):
            return {key: value.text for key, value in node.items()}
        return None
    if isinstance(node, Scalar) and node.style == "flow" and node.text.startswith("{"):
        grants: dict[str, str] = {}
        inner = node.text[1:-1].strip()
        for part in inner.split(",") if inner else []:
            key, colon, value = part.partition(":")
            if not colon:
                return None
            grants[key.strip().strip("'\"")] = value.strip().strip("'\"")
        return grants
    return None


def steps_of(job: dict, job_id: str) -> list[dict]:
    node = one(job, "steps", f"job `{job_id}`")
    if node is None or (isinstance(node, Scalar) and node.style == "null"):
        return []
    if not isinstance(node, list):
        raise Unreadable(f"job `{job_id}` has `steps` that is not a block list")
    steps = []
    for item in node:
        if not isinstance(item, dict):
            raise Unreadable(f"job `{job_id}` has a step that is not a block mapping")
        steps.append(item)
    return steps


def uses_of(step: dict, job_id: str) -> str:
    node = one(step, "uses", f"a step of job `{job_id}`")
    return node.text if isinstance(node, Scalar) else ""


def check_continue(node, where: str, job_id: str) -> list[Finding]:
    if isinstance(node, Scalar) and node.style == "plain" and node.text.lower() == "false":
        return []
    shown = node.text if isinstance(node, Scalar) else "a collection"
    return [
        Finding(
            "VERIFY-CONTINUE-ON-ERROR",
            job_id,
            f"{where} sets `continue-on-error: {shown}`; only a literal `false` is accepted, because any other value can turn a failed check into a pass",
        )
    ]


def check_needs(jobs: dict, job_id: str) -> list[Finding]:
    """A job that needs another is skipped when the needed job is skipped, and a skipped
    job reads as a pass. No job in the chain may carry an `if:`."""
    findings, seen, todo = [], {job_id}, needs_of(jobs[job_id], f"job `{job_id}`")
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        if name not in jobs:
            raise Unreadable(f"job `{job_id}` needs `{name}`, which is not a job in the workflow")
        if named(jobs[name], "if"):
            findings.append(
                Finding(
                    "VERIFY-NEEDS-IF",
                    job_id,
                    f"the job needs `{name}`, which has an `if:`; when `{name}` is skipped this job is skipped, and a skipped job reads as a pass",
                )
            )
        todo += needs_of(jobs[name], f"job `{name}`")
    return findings


def check_caller(jobs: dict, job_id: str) -> list[Finding]:
    job = jobs[job_id]
    steps = steps_of(job, job_id)
    findings: list[Finding] = []
    for node in named(job, "continue-on-error"):
        findings += check_continue(node, "the job", job_id)
    for step in steps:
        label = one(step, "name", f"a step of job `{job_id}`")
        label = f"`{label.text}`" if isinstance(label, Scalar) else "an unnamed step"
        for node in named(step, "continue-on-error"):
            findings += check_continue(node, f"step {label}", job_id)
    if named(job, "if"):
        findings.append(Finding("VERIFY-IF-JOB", job_id, "the job has an `if:`, so it can be skipped and a skipped job reads as a pass"))
    for step in steps:
        uses = uses_of(step, job_id)
        if not CALL.search(uses):
            continue
        if named(step, "if"):
            findings.append(Finding("VERIFY-IF-STEP", job_id, "the verify step has an `if:`, so it can be skipped while the job passes"))
        if not SOURCE.match(uses):
            findings.append(
                Finding("VERIFY-SOURCE", job_id, f"the verify step uses `{uses}`; it must be `branchLeft/github-workflows/.github/actions/verify-package-attestations@REF`")
            )
    findings += check_needs(jobs, job_id)
    node = one(job, "permissions", f"job `{job_id}`")
    if node is None:
        findings.append(Finding("VERIFY-PERMISSIONS", job_id, "the job has no `permissions:` of its own; it must grant exactly `contents: read` and `packages: read`"))
    elif grants_of(node) != GRANTS:
        findings.append(Finding("VERIFY-PERMISSIONS", job_id, "the job's `permissions:` must be exactly `contents: read` and `packages: read`"))
    return findings


def check_workflow(document: dict, wanted: list[str]) -> list[Finding]:
    jobs = one(document, "jobs", "the workflow")
    if not isinstance(jobs, dict):
        raise Unreadable("the workflow has no `jobs:` mapping")
    callers = []
    for job_id, job in jobs.items():
        if not isinstance(job, dict):
            raise Unreadable(f"job `{job_id}` is not a block mapping")
        if any(CALL.search(uses_of(step, job_id)) for step in steps_of(job, job_id)):
            callers.append(job_id)
    findings: list[Finding] = []
    if not callers:
        findings.append(Finding("VERIFY-NONE", "workflow", "no job calls the verify-package-attestations action, so nothing is checked"))
    for name in wanted:
        if name not in jobs:
            findings.append(Finding("VERIFY-NO-JOB", name, "no such job in the workflow"))
        elif name not in callers:
            findings.append(Finding("VERIFY-NOT-CALLED", name, "the job does not call the verify-package-attestations action"))
    for job_id in callers:
        findings += check_caller(jobs, job_id)
    return findings


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-verify-job-hardened.py", description=__doc__.split("\n")[0])
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--job", action="append", default=[], help="job ids that must exist and call the action, whitespace-separated; repeatable")
    args = parser.parse_args(argv[1:])
    wanted = [name for value in args.job for name in value.split()]
    try:
        with open(args.workflow, encoding="utf-8") as handle:
            document = Parser(handle.read()).document()
        found = check_workflow(document, wanted)
    except (OSError, UnicodeDecodeError, Unreadable) as error:
        print(f"::error file={args.workflow}::VERIFY-UNREADABLE {error}")
        return 1
    for item in found:
        print(f"::error file={args.workflow}::{item.code} {item.job}: {item.message}")
    if found:
        return 1
    print(f"ok: {args.workflow} keeps every verify job unskippable, unforgiving and read-only")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
