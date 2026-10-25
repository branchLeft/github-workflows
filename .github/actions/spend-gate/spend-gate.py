#!/usr/bin/env python3
"""Decide whether a Pulumi preview could cost money: only a wholly free one is FREE.
The rules and their reasons are in spend-gate.md beside this file."""

from __future__ import annotations

import argparse
import importlib.util
import os
import pathlib
import re
import sys
from typing import NamedTuple

# The preview parser is plan-has-changes' and has one home. It is loaded by path
# because its filename has hyphens and `python3 -I` keeps the script directory off
# sys.path. If this fails the script dies before it writes a verdict, and a caller
# reads an unset verdict as PAID.
_SHARED_PATH = pathlib.Path(__file__).resolve().parent.parent / "plan-has-changes" / "plan-has-changes.py"
_SPEC = importlib.util.spec_from_file_location("plan_has_changes", _SHARED_PATH)
assert _SPEC is not None and _SPEC.loader is not None
shared = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(shared)

FREE = "FREE"
PAID = "PAID"

# Types that bill nothing, matched on the full type string. A type nested under a
# parent component reads `parent$child` and is never on this list, so it is PAID.
FREE_TYPES = frozenset(
    {
        "pulumi:pulumi:Stack",
        "hcloud:index/network:Network",
        "hcloud:index/networkSubnet:NetworkSubnet",
        "hcloud:index/networkRoute:NetworkRoute",
        "hcloud:index/firewall:Firewall",
        "hcloud:index/firewallAttachment:FirewallAttachment",
        "hcloud:index/sshKey:SshKey",
        "hcloud:index/placementGroup:PlacementGroup",
    }
)
# Every op that changes a resource. Anything else (refresh, read, discard,
# remove-pending-replace) is not understood and is PAID.
CHANGING_OPS = frozenset(
    {"create", "update", "replace", "create-replacement", "delete", "delete-replaced", "import", "import-replacement"}
)
CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")


class Verdict(NamedTuple):
    verdict: str
    reason: str


def _is_free_type(type_name: str) -> bool:
    """Whether a full type string is on the allowlist: the exact types above, or a provider."""
    return type_name in FREE_TYPES or shared._is_provider_type(type_name)


def _one_line(text: str) -> str:
    """Nothing in a reason can add a line to the output file."""
    return CONTROL_CHARACTERS.sub(" ", text)


def _state_type_agrees(urn: object, step: dict[str, object]) -> bool:
    """A step's old and new state, where present, name the type its URN names."""
    for key in ("oldState", "newState"):
        state = step.get(key)
        if state is None:
            continue
        if not isinstance(state, dict):
            raise shared.Unclear("preview-shape")
        if "type" in state and state["type"] != shared._leaf_type(urn):
            return False
    return True


def _changing_steps(doc: dict[str, object]) -> list[tuple[str, object, dict[str, object]]]:
    """The listed steps that change something, as (op, URN, step), each checked for shape.

    A `same` step that carries its own evidence of a difference is a changing step."""
    changing = []
    for op, urn, step in shared._preview_steps(doc):
        shared._check_keys(step, shared.STEP_KEYS, "preview-shape", "preview step")
        if op != shared.SAME:
            changing.append((op, urn, step))
        elif shared._same_step_differs(urn, step):
            changing.append((op, urn, step))
    return changing


def _check_summary(doc: dict[str, object], changing: list[tuple[str, object, dict[str, object]]]) -> str | None:
    """Why the change summary cannot be squared with the listed steps, or None.

    The summary may count fewer changes than are listed (a replace is several steps),
    never more (a change that is not listed cannot be judged) and never none while
    a changing step is listed."""
    summary = shared._as_dict(doc.get("changeSummary"), "preview-shape")
    for value in summary.values():
        shared._require(shared._is_count(value), "preview-shape")
    counted = sum(int(v) for k, v in summary.items() if k != shared.SAME)
    listed = sum(1 for op, _, _ in changing if op != shared.SAME)
    if counted > listed:
        return "the summary counts changes the preview does not list"
    if changing and counted == 0:
        return "the summary counts no changes though changing steps are listed"
    return None


def _check_envelope(doc: dict[str, object]) -> None:
    """The top-level fields: known keys, no diagnostics, not corrupt."""
    shared._check_keys(doc, shared.PREVIEW_KEYS, "preview-shape", "preview")
    shared._require("config" not in doc or isinstance(doc["config"], dict), "preview-shape")
    shared._require("duration" not in doc or shared._is_count(doc["duration"]), "preview-shape")
    diagnostics = doc.get("diagnostics")
    shared._require(diagnostics is None or (isinstance(diagnostics, list) and not diagnostics), "preview-diagnostics")
    corrupt = doc.get("maybeCorrupt")
    shared._require(corrupt is None or corrupt is False, "preview-shape")


def judge(path: str) -> Verdict:
    """FREE only for a preview that parsed and changes nothing but free types."""
    try:
        doc = shared._as_dict(shared._load(path, "preview-unreadable"), "preview-shape")
        _check_envelope(doc)
        changing = _changing_steps(doc)
        mismatch = _check_summary(doc, changing)
        if mismatch:
            return Verdict(PAID, mismatch)
        if not all(_state_type_agrees(urn, step) for _, urn, step in changing):
            return Verdict(PAID, "a step's state names a different type from its URN")
        bad_ops = {op for op, _, _ in changing if op != shared.SAME and op not in CHANGING_OPS}
        if bad_ops:
            return Verdict(PAID, f"operation not understood: {shared._names(bad_ops)}")
        paid = {urn.type for _, urn, _ in changing if not _is_free_type(urn.type)}
        if paid:
            return Verdict(PAID, f"changes to a type that is not on the free list: {shared._names(paid)}")
        if not changing:
            return Verdict(FREE, "the preview changes no resource")
        free = {urn.type for _, urn, _ in changing}
        return Verdict(FREE, f"every change is to a free type: {shared._names(free)}")
    except shared.Unclear as unclear:
        detail = f" ({unclear.detail})" if unclear.detail else ""
        return Verdict(PAID, f"preview not understood: {unclear.reason}{detail}")
    except Exception:  # noqa: BLE001 -- every failure must reach the caller as PAID
        return Verdict(PAID, "preview not understood: internal-error")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="spend-gate.py", description=__doc__.split("\n")[0])
    parser.add_argument("preview_json")
    parser.add_argument("--github-output", required=True, help="append reason= then verdict= to this file")
    parser.add_argument("--github-summary", required=True, help="append the one-line verdict to this file")
    args = parser.parse_args(argv[1:])

    problem = shared._output_problem(args.github_output, args.github_summary)
    if problem:
        print(f"::error::{problem.replace('plan verdict', 'spend verdict')}")
        return 1

    result = judge(args.preview_json)
    reason = _one_line(result.reason)
    try:
        with open(args.github_summary, "a", encoding="utf-8") as handle:
            handle.write(f"Spend gate: {result.verdict}: {reason}\n\n")
        # The verdict goes last, in the same write as the reason: a failure before
        # it leaves `verdict` unset, which a caller must read as PAID.
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"reason={reason}\nverdict={result.verdict}\n")
    except OSError as error:
        print(f"::error::cannot write the spend verdict: {type(error).__name__}")
        return 1

    print(f"verdict={result.verdict} reason={reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
