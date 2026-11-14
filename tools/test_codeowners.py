#!/usr/bin/env python3
"""Checks .github/CODEOWNERS against a fixed table of sample paths.

Usage: python3 tools/test_codeowners.py [--codeowners PATH]
See tools/test_codeowners.md for what each check proves.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

OWNER = "@branchLeft/branchleft-admin"

SHARED_SET: tuple[str, ...] = (
    "/.standardsignore",
    "/.standards.mode",
    "/.docs-lint*",
    "/.standards-db-tooling",
    "/.github/",
)

RELEASE_LINES: tuple[str, ...] = (
    "/tools/docs-lint-rules.md",
    "/tools/opv-lint-rules.md",
    "/tools/resolve-pinned-ref.md",
)

CATCH_ALL_PATTERNS: frozenset[str] = frozenset({"*", "/*", "**", "/**", "**/*"})

REDUNDANT_BY_DESIGN: dict[str, str] = {
    "/tools/floors.tsv": "the /tools/ line above it already owns that file",
}


@dataclass(frozen=True)
class Entry:
    pattern: str
    owners: tuple[str, ...]


@dataclass(frozen=True)
class Sample:
    path: str
    klass: str
    owned: bool


def _owned(path: str, klass: str) -> Sample:
    return Sample(path, klass, True)


def _released(path: str, klass: str) -> Sample:
    return Sample(path, klass, False)


SAMPLES: tuple[Sample, ...] = (
    _owned(".github/workflows/docs-lint.yml", "criterion 3"),
    _owned("sub/.github/workflows/a.yml", "criterion 3"),
    _owned(".github/actions/spend-gate/spend-gate.py", "criterion 3"),
    _owned("sub/.github/actions/a/b.py", "criterion 3"),
    _owned(".github/dependabot.yml", "criterion 3"),
    _owned("x/CODEOWNERS", "criterion 1"),
    _owned("x/codeowners", "criterion 1"),
    _owned("x/CodeOwners", "criterion 1"),
    _owned("x/ruleset.json", "criterion 1"),
    _owned("x/Ruleset.json", "criterion 1"),
    _owned("x/RULESET.json", "criterion 1"),
    _owned("x/branch-protection.json", "criterion 1"),
    _owned("x/Branch-Protection.json", "criterion 1"),
    _owned("x/BRANCH-PROTECTION.md", "criterion 1"),
    _owned("x/branch_protection.tf", "criterion 1"),
    _owned("x/Branch_Protection.tf", "criterion 1"),
    _owned("x/BRANCH_PROTECTION.md", "criterion 1"),
    _owned("x/branchprotection.json", "criterion 1"),
    _owned("x/BranchProtection.json", "criterion 1"),
    _owned("x/BRANCHPROTECTION.json", "criterion 1"),
    _owned("tools/docs-lint.test.sh", "criterion 1, test of a required gate"),
    _owned("tools/docs-lint-workflow.test.sh", "criterion 1, test of a required gate"),
    _owned("tools/docs-lint.sh", "criterion 1"),
    _owned("tools/merged-status.py", "criterion 1"),
    _owned("tools/sabotage.py", "criterion 1"),
    _owned("tools/resolve-pinned-ref.sh", "criterion 1"),
    _owned("tools/test_ansible_gated_apply.py", "criterion 1, test of a gate"),
    _owned("tools/test_codeowners.py", "criterion 1, test of the rules"),
    _owned("tools/test_codeowners.md", "criterion 1"),
    _owned("tools/floors.tsv", "criterion 1"),
    _owned(".pre-commit-hooks.yaml", "criterion 1"),
    _owned(".opv-lintignore", "criterion 1"),
    _owned(".docs-lintignore", "criterion 1"),
    _owned(".standards.mode", "criterion 1"),
    _owned(".standardsignore", "criterion 1"),
    _owned(".standards-db-tooling", "criterion 1"),
    _owned("keys/allowed_signers", "criterion 2"),
    _owned("ansible/trust/host.txt", "criterion 2"),
    _owned(".npmrc", "criterion 2"),
    _owned("Pulumi.prod.yaml", "criterion 2"),
    _owned("ansible/library/x.py", "criterion 2"),
    _owned("ansible/roles/r/module_utils/x.py", "criterion 2"),
    _owned("ansible/filter_plugins/x.py", "criterion 2"),
    _owned("etc/sudoers", "criterion 2"),
    _owned("etc/sudoers.d/x", "criterion 2"),
    _owned("home/.ssh/authorized_keys", "criterion 2"),
    _owned("mail/x.ts", "criterion 4"),
    _owned("LICENSE", "criterion 8"),
    _released("tools/docs-lint-rules.md", "released gate prose"),
    _released("tools/opv-lint-rules.md", "released gate prose"),
    _released("tools/resolve-pinned-ref.md", "released gate prose"),
    _released("README.md", "unowned prose"),
    _released("RUNBOOK.md", "unowned prose"),
    _released(".editorconfig", "unowned file"),
    _released(".gitignore", "unowned file"),
    _released("scripts/run.sh", "dropped name-only line"),
    _released("x/deploy-notes.md", "dropped name-only line"),
    _released("x/approval.py", "dropped name-only line"),
    _released("x/guard.py", "dropped name-only line"),
)


def parse(lines: Sequence[str]) -> list[Entry]:
    entries: list[Entry] = []
    for raw in lines:
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        tokens = text.split()
        entries.append(Entry(tokens[0], tuple(tokens[1:])))
    return entries


def _glob_to_regex(glob: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("/**", i) and i + 3 == len(glob):
            out.append("/.*")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return "".join(out)


def matches(pattern: str, path: str) -> bool:
    dir_only = pattern.endswith("/")
    body = pattern.rstrip("/")
    anchored = body.startswith("/") or "/" in body
    body = body.lstrip("/")
    last_segment = body.rsplit("/", 1)[-1]
    reaches_children = dir_only or not ("*" in last_segment or "?" in last_segment)
    prefix = "" if anchored else "(?:.*/)?"
    rx = re.compile(prefix + _glob_to_regex(body))
    parts = path.split("/")
    for k in range(1, len(parts) + 1):
        is_dir = k < len(parts)
        allowed = reaches_children if is_dir else not dir_only
        if allowed and rx.fullmatch("/".join(parts[:k])):
            return True
    return False


def owners_of(path: str, entries: Sequence[Entry]) -> tuple[str, ...]:
    result: tuple[str, ...] = ()
    for entry in entries:
        if matches(entry.pattern, path):
            result = entry.owners
    return result


def structural_problems(lines: Sequence[str]) -> list[str]:
    entries = parse(lines)
    problems: list[str] = []
    for entry in entries:
        if entry.pattern in CATCH_ALL_PATTERNS:
            problems.append(f"catch-all line: {entry.pattern}")
        wants_owner = entry.pattern not in RELEASE_LINES
        if wants_owner and entry.owners != (OWNER,):
            problems.append(f"line {entry.pattern} must have exactly the owner team")
        if not wants_owner and entry.owners:
            problems.append(f"release line {entry.pattern} must name no owner")
    if re.search(r"#\d+", "\n".join(lines)):
        problems.append("a work-item reference (#<digits>) appears in the file")
    tail = entries[-len(SHARED_SET):]
    if tuple(e.pattern for e in tail) != SHARED_SET:
        problems.append("the last five lines are not the shared set in template order")
    body = entries[: -len(SHARED_SET)]
    release_block = body[-len(RELEASE_LINES):]
    if tuple(e.pattern for e in release_block) != RELEASE_LINES:
        problems.append("the release lines are not the block just before the shared set")
    if any(e.pattern in RELEASE_LINES for e in body[: -len(RELEASE_LINES)]):
        problems.append("a release line sits before an owner line")
    return problems


def sample_problems(lines: Sequence[str]) -> list[str]:
    entries = parse(lines)
    problems: list[str] = []
    for sample in SAMPLES:
        owned = bool(owners_of(sample.path, entries))
        if owned != sample.owned:
            want = "owned" if sample.owned else "not owned"
            got = "owned" if owned else "not owned"
            problems.append(f"{sample.path} ({sample.klass}): expected {want}, got {got}")
    return problems


def problems_in(lines: Sequence[str]) -> list[str]:
    return structural_problems(lines) + sample_problems(lines)


def _index_of(lines: Sequence[str], pattern: str) -> int:
    for i, raw in enumerate(lines):
        tokens = raw.split()
        if tokens and tokens[0] == pattern and not raw.lstrip().startswith("#"):
            return i
    raise ValueError(f"no line for {pattern}")


def _without(lines: Sequence[str], index: int) -> list[str]:
    return [raw for i, raw in enumerate(lines) if i != index]


def _move_to_end(lines: Sequence[str], pattern: str) -> list[str]:
    i = _index_of(lines, pattern)
    return _without(lines, i) + [lines[i]]


def _move_to_top(lines: Sequence[str], pattern: str) -> list[str]:
    i = _index_of(lines, pattern)
    return [lines[i]] + _without(lines, i)


def _move_before(lines: Sequence[str], pattern: str, before: str) -> list[str]:
    i = _index_of(lines, pattern)
    moved = lines[i]
    rest = _without(lines, i)
    j = _index_of(rest, before)
    return rest[:j] + [moved] + rest[j:]


def sabotage_cases(lines: Sequence[str]) -> list[tuple[str, list[str]]]:
    cases: list[tuple[str, list[str]]] = []
    for i, raw in enumerate(lines):
        tokens = raw.split()
        if tokens and not raw.lstrip().startswith("#"):
            cases.append((f"delete {tokens[0]}", _without(lines, i)))
    cases.append(("append a catch-all", [*lines, f"*  {OWNER}"]))
    cases.append(("append an unowned /.github/actions/", [*lines, "/.github/actions/"]))
    cases.append(("move a release line below the shared set",
                  _move_to_end(lines, RELEASE_LINES[0])))
    cases.append(("move /.github/ to the top", _move_to_top(lines, "/.github/")))
    cases.append(("move /tools/ below the release lines",
                  _move_before(lines, "/tools/", SHARED_SET[0])))
    return cases


def run_sabotage(lines: Sequence[str]) -> list[str]:
    failures: list[str] = []
    for name, broken in sabotage_cases(lines):
        found = problems_in(broken)
        pattern = name.removeprefix("delete ")
        if found:
            print(f"sabotage red as expected: {name} ({len(found)} problem(s))")
        elif name.startswith("delete ") and pattern in REDUNDANT_BY_DESIGN:
            print(f"sabotage green by design: {name} ({REDUNDANT_BY_DESIGN[pattern]})")
        else:
            failures.append(f"sabotage stayed green: {name}")
    return failures


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--codeowners", default=".github/CODEOWNERS")
    args = parser.parse_args(list(argv))
    lines = Path(args.codeowners).read_text(encoding="utf-8").splitlines()
    problems = problems_in(lines)
    if problems:
        print(f"RED: {args.codeowners}")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"green: {len(SAMPLES)} samples and the structure checks pass")
    failures = run_sabotage(lines)
    if failures:
        print("RED: the checks missed a sabotage")
        for failure in failures:
            print(f"  {failure}")
        return 1
    print("green: every sabotage case turned the checks red")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
