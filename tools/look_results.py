"""Derive, per kind, every result the look of shared-infra's pull_trigger.py can print.

The look (pull_trigger README, "The look") prints `look issue=... kind=K result=R`. R is one of a closed set of
words, a digest, a status or a key, and ssh-check-hosts judges a copy of that line by a regex. This reads the
program with ast and lists every word its result functions can return, so the regex is held to the program and
not to a hand-typed list.

Usage: look_results.py PULL_TRIGGER_PY SOURCE_SHA > tools/look-results.json
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

# The result function of each kind (the program's LookFacts.result dispatches to these).
KIND_FUNCTIONS = {
    "_file_digest": "file_sha256",
    "_unit_state": "unit_active",
    "_http_status": "http_status",
    "_ssh_public_key": "ssh_public_key",
    "_uptime_90d": "uptime_90d",
}
# The one function whose words are shared by every kind: the skip printed when the time is spent.
SHARED_FUNCTION = "_line"
KINDS = ("file_sha256", "unit_active", "http_status", "ssh_public_key", "uptime_90d")


def module_constants(tree: ast.Module) -> dict[str, ast.expr]:
    """Map each module-level name assigned once to its value expression."""
    values: dict[str, ast.expr] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            values[node.target.id] = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            values[node.targets[0].id] = node.value
    return values


def look_word_values(tree: ast.Module) -> dict[str, str]:
    """Map each LookWhy member name to its word, as the enum defines it."""
    words: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "LookWhy":
            for item in node.body:
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant):
                    words[item.targets[0].id] = str(item.value.value)  # type: ignore[attr-defined]
    return words


def set_members(expr: ast.expr) -> set[str]:
    """The string members of a frozenset({...}) or set literal."""
    return {node.value for node in ast.walk(expr) if isinstance(node, ast.Constant) and isinstance(node.value, str)}


def words_of(expr: ast.expr, constants: dict[str, ast.expr], enum_words: dict[str, str], shape: str) -> tuple[set[str], set[str]]:
    """The literal words and the shapes one returned expression can produce."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return {expr.value}, set()
    if isinstance(expr, ast.JoinedStr):
        return set(), {shape}
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "hexdigest":
        return set(), {"sha256"}
    # `" ".join(...)` returns the pairs and the time of a success line: one shape.
    if isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute) and expr.func.attr == "join":
        return set(), {shape}
    if isinstance(expr, ast.Attribute) and expr.attr == "value" and isinstance(expr.value, ast.Attribute):
        member = expr.value.attr
        return ({enum_words[member]} if member in enum_words else set()), set()
    if isinstance(expr, ast.Name) and expr.id in constants:
        return set_members(constants[expr.id]), set()
    if isinstance(expr, ast.IfExp):
        words, shapes = set(), set()
        # `word if word in SET else "other"` returns a member of SET, which the body names only by a local.
        test = expr.test
        if isinstance(test, ast.Compare) and isinstance(test.comparators[0], ast.Name) and test.comparators[0].id in constants:
            words |= set_members(constants[test.comparators[0].id])
        for branch in (expr.body, expr.orelse):
            branch_words, branch_shapes = words_of(branch, constants, enum_words, shape)
            words |= branch_words
            shapes |= branch_shapes
        return words, shapes
    return set(), set()


def derive(tree: ast.Module) -> dict[str, dict[str, list[str]]]:
    constants = module_constants(tree)
    enum_words = look_word_values(tree)
    shape_of = {"_http_status": "status", "_ssh_public_key": "key", "_uptime_90d": "uptime"}
    per_kind: dict[str, dict[str, set[str]]] = {kind: {"words": set(), "shapes": set()} for kind in KINDS}
    shared_words: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if node.name == SHARED_FUNCTION:
            for item in ast.walk(node):
                if isinstance(item, ast.Assign) and isinstance(item.value, ast.Constant) and isinstance(item.value.value, str):
                    shared_words.add(item.value.value)
            continue
        if node.name not in KIND_FUNCTIONS:
            continue
        kind = KIND_FUNCTIONS[node.name]
        for item in ast.walk(node):
            if isinstance(item, ast.Return) and item.value is not None:
                words, shapes = words_of(item.value, constants, enum_words, shape_of.get(node.name, ""))
                per_kind[kind]["words"] |= words
                per_kind[kind]["shapes"] |= shapes
    for kind in KINDS:
        per_kind[kind]["words"] |= shared_words
    return {
        kind: {"words": sorted(values["words"]), "shapes": sorted(values["shapes"])}
        for kind, values in per_kind.items()
    }


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    source, sha = Path(argv[1]), argv[2]
    tree = ast.parse(source.read_text(encoding="utf-8"))
    document = {
        "source": {"file": "ansible/roles/pull_trigger/files/pull_trigger.py", "repo": "branchLeft/shared-infra", "sha": sha},
        "kinds": derive(tree),
    }
    json.dump(document, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
