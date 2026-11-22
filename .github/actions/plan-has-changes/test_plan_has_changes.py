#!/usr/bin/env python3
"""Unit tests for plan-has-changes: the failure that matters is a `false` over a change.
Fixture shapes are those the Pulumi CLI wrote for a throwaway local stack."""

import ast
import contextlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


SCRIPT_ENV = "PLAN_HAS_CHANGES_SCRIPT"


def _load_module():
    """Import the script by path: its filename has hyphens, so it is not a
    legal module name for a plain import. PLAN_HAS_CHANGES_SCRIPT points the tests
    at another copy, which is how the sabotage test breaks a copy and not this one."""
    path = pathlib.Path(os.environ.get(SCRIPT_ENV) or pathlib.Path(__file__).resolve().parent / "plan-has-changes.py").resolve()
    spec = importlib.util.spec_from_file_location("plan_has_changes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


noop = _load_module()

_STACK = "urn:pulumi:production::branchleft-mail::"
_ROOT = f"{_STACK}pulumi:pulumi:Stack::branchleft-mail-production"
_SERVER = f"{_STACK}hcloud:index/server:Server::mx1"
_FIREWALL = f"{_STACK}hcloud:index/firewall:Firewall::mail-firewall"

# What an unknown value is written as in a saved plan.
UNKNOWN = "04da6b54-80e4-46f7-96ec-b56ff0331ba9"
# The shape a secret output takes in a saved plan: the secrets provider's
# ciphertext, never the value.
SECRET = {
    "4dabf18193072939515e22adb298388d": "1b47061264138c4ac30d75fd1eb44270",
    "ciphertext": "v1:CIPHERTEXT_PLACEHOLDER",
}
MARKER = "VALUE_MARKER_MUST_NOT_BE_PRINTED"
# Every JSON key of a resource state in the Pulumi engine's checkpoint type (ResourceV3).
STATE_KEYS = (
    "urn custom delete id type inputs outputs parent protect taint external dependencies initErrors"
    " provider propertyDependencies pendingReplacement additionalSecretOutputs aliases customTimeouts"
    " importID retainOnDelete deletedWith replaceWith created modified sourcePosition stackTrace"
    " ignoreChanges hideDiff replaceOnChanges replacementTrigger refreshBeforeUpdate viewOf"
    " resourceHooks extensionRef snippetID"
).split()


def step_of(op, urn):
    """A preview step. A `same` step carries both states, as a real one does (from
    the engine source; no real `same` step has been observed): the old state holds
    the id and outputs, the new one is read before it copies them, and the two
    carry the program position of different runs."""
    step = {"op": op, "urn": urn}
    if op == "same":
        base = {"urn": urn, "custom": False, "type": urn.split("::")[2].rsplit("$", 1)[-1]}
        step["oldState"] = {**base, "id": "ID", "outputs": {"k": "v"}, "sourcePosition": "project:///a#1"}
        step["newState"] = {**base, "sourcePosition": "project:///a#9", "stackTrace": [{"sourcePosition": "b"}]}
    return step


def preview(*steps, summary=None, **extra):
    """A preview JSON document: (op, urn) steps and a change summary."""
    doc = {
        "steps": [step_of(op, urn) for op, urn in steps],
        "duration": 1,
        "changeSummary": summary if summary is not None else {"same": 3},
    }
    doc.update(extra)
    return doc


def no_op_preview():
    return preview(("same", _ROOT))


def clean_goal(**extra):
    """The two diffs every goal of a real plan carries, empty, and any other field given."""
    return {"inputDiff": {}, "outputDiff": {}, **extra}


def plan(output_diff=None, root=_ROOT, root_steps=("same",), others=None, goal=None):
    """A saved plan file: a root stack entry and any other resource entries."""
    root_goal = goal if goal is not None else clean_goal(type="pulumi:pulumi:Stack")
    if goal is None and output_diff is not None:
        root_goal["outputDiff"] = output_diff
    plans = {root: {"goal": root_goal, "steps": list(root_steps), "state": {"k": "v"}}}
    for urn, steps in (others or {}).items():
        plans[urn] = {"goal": clean_goal(), "steps": list(steps)}
    return {"manifest": {"version": "v0"}, "config": {"k": MARKER}, "resourcePlans": plans}


class Files:
    """A temp directory holding the two input files."""

    def __init__(self, testcase):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="plan-has-changes-"))
        testcase.addCleanup(self._cleanup)

    def _cleanup(self):
        for child in self.dir.iterdir():
            child.unlink()
        self.dir.rmdir()

    def write(self, name, content):
        path = self.dir / name
        path.write_text(
            content if isinstance(content, str) else json.dumps(content), encoding="utf-8"
        )
        return str(path)

    def missing(self, name):
        return str(self.dir / name)


class VerdictCase(unittest.TestCase):
    def setUp(self):
        self.files = Files(self)

    def verdict(self, preview_doc, plan_doc):
        return noop.evaluate(
            self.files.write("preview.json", preview_doc),
            self.files.write("plan.json", plan_doc),
        )

    def assertAsks(self, verdict, reason):
        self.assertTrue(verdict.has_changes, verdict)
        self.assertEqual(verdict.reason, reason)


class SameOnlyTests(VerdictCase):
    def test_a_same_only_plan_with_an_empty_output_diff_is_no_change(self):
        verdict = self.verdict(no_op_preview(), plan())
        self.assertFalse(verdict.has_changes)
        self.assertEqual(verdict, noop.NO_CHANGES)

    def test_a_same_only_plan_that_also_lists_same_resources_is_no_change(self):
        verdict = self.verdict(
            preview(("same", _ROOT), ("same", _SERVER)),
            plan(others={_SERVER: ["same"], _FIREWALL: ["same"]}),
        )
        self.assertFalse(verdict.has_changes)

    def test_an_output_diff_of_empty_collections_is_no_change(self):
        verdict = self.verdict(
            no_op_preview(), plan({"adds": {}, "updates": {}, "deletes": []})
        )
        self.assertFalse(verdict.has_changes)


class StepOpTests(VerdictCase):
    def test_every_changing_op_is_a_change(self):
        for op in (
            "create",
            "update",
            "delete",
            "replace",
            "create-replacement",
            "delete-replaced",
            "read",
            "refresh",
            "import",
        ):
            with self.subTest(op=op):
                verdict = self.verdict(
                    preview(("same", _ROOT), (op, _SERVER), summary={"same": 2, op: 1}),
                    plan(),
                )
                self.assertAsks(verdict, "steps-changed")
                self.assertEqual(verdict.count, 1)

    def test_an_unknown_op_is_a_change(self):
        verdict = self.verdict(preview(("same", _ROOT), ("frobnicate", _SERVER)), plan())
        self.assertAsks(verdict, "steps-changed")

    def test_the_root_stack_step_itself_changing_is_a_change(self):
        verdict = self.verdict(preview(("update", _ROOT)), plan())
        self.assertAsks(verdict, "steps-changed")

    def test_the_count_is_the_number_of_changing_steps(self):
        verdict = self.verdict(
            preview(("same", _ROOT), ("create", _SERVER), ("update", _FIREWALL)), plan()
        )
        self.assertEqual(verdict.count, 2)

    def test_a_non_same_op_in_the_plan_file_alone_is_a_change(self):
        verdict = self.verdict(
            no_op_preview(), plan(others={_SERVER: ["same", "update"]})
        )
        self.assertAsks(verdict, "steps-changed")


class OutputOnlyTests(VerdictCase):
    def test_an_added_output_is_a_change(self):
        verdict = self.verdict(no_op_preview(), plan({"adds": {"greeting": "hello"}}))
        self.assertAsks(verdict, "output-changed")
        self.assertEqual(verdict.count, 1)

    def test_an_updated_output_is_a_change(self):
        verdict = self.verdict(no_op_preview(), plan({"updates": {"a": "x", "b": "y"}}))
        self.assertAsks(verdict, "output-changed")
        self.assertEqual(verdict.count, 2)

    def test_a_deleted_output_is_a_change(self):
        verdict = self.verdict(no_op_preview(), plan({"deletes": ["gone"]}))
        self.assertAsks(verdict, "output-changed")

    def test_a_changed_secret_output_is_a_change(self):
        # The secret arrives as ciphertext; the diff itself was computed on the
        # decrypted values inside the CLI, so an entry here means it changed.
        verdict = self.verdict(no_op_preview(), plan({"updates": {"secret_one": SECRET}}))
        self.assertAsks(verdict, "output-changed")

    def test_an_output_unknown_at_preview_time_is_a_change(self):
        verdict = self.verdict(no_op_preview(), plan({"adds": {"id": UNKNOWN}}))
        self.assertAsks(verdict, "output-changed")

    def test_an_output_change_in_a_non_root_resource_is_a_change(self):
        # RegisterResourceOutputs also sets the output diff of a component's
        # entry, so a component-only output change is a change. This test
        # first pinned the opposite, which encoded a gap, and was corrected.
        doc = plan(others={_SERVER: ["same"]})
        doc["resourcePlans"][_SERVER]["goal"]["outputDiff"] = {"adds": {"k": "v"}}
        self.assertAsks(self.verdict(no_op_preview(), doc), "output-changed")


class PlanFileShapeTests(VerdictCase):
    """A plan file this script does not understand reaches the gate."""

    def test_a_missing_plan_file(self):
        verdict = noop.evaluate(
            self.files.write("preview.json", no_op_preview()), self.files.missing("plan.json")
        )
        self.assertAsks(verdict, "plan-unreadable")

    def test_an_empty_plan_file(self):
        verdict = self.verdict(no_op_preview(), "")
        self.assertAsks(verdict, "plan-unreadable")

    def test_an_invalid_json_plan_file(self):
        verdict = self.verdict(no_op_preview(), "{not json")
        self.assertAsks(verdict, "plan-unreadable")

    def test_a_non_utf8_plan_file(self):
        path = self.files.dir / "plan.json"
        path.write_bytes(b"\xff\xfe\x00")
        verdict = noop.evaluate(self.files.write("preview.json", no_op_preview()), str(path))
        self.assertAsks(verdict, "plan-unreadable")

    def test_a_plan_file_that_is_not_an_object(self):
        for content in ([], "null", '"text"', "3"):
            with self.subTest(content=content):
                self.assertAsks(self.verdict(no_op_preview(), content), "plan-shape")

    def test_no_resource_plans_key(self):
        self.assertAsks(self.verdict(no_op_preview(), {"manifest": {}}), "plan-shape")

    def test_empty_resource_plans(self):
        self.assertAsks(self.verdict(no_op_preview(), {"resourcePlans": {}}), "plan-shape")

    def test_resource_plans_that_is_a_list(self):
        self.assertAsks(self.verdict(no_op_preview(), {"resourcePlans": []}), "plan-shape")

    def test_no_root_stack_entry(self):
        doc = plan(root=_SERVER)
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_two_root_stack_entries(self):
        doc = plan()
        doc["resourcePlans"][_ROOT.replace("mail", "other")] = {"goal": clean_goal()}
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_non_urn_key(self):
        doc = plan()
        doc["resourcePlans"]["not-a-urn"] = {"goal": clean_goal()}
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_an_entry_that_is_not_an_object(self):
        doc = plan()
        doc["resourcePlans"][_SERVER] = "nope"
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_root_entry_with_no_goal(self):
        doc = plan()
        del doc["resourcePlans"][_ROOT]["goal"]
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_goal_with_no_output_diff_key(self):
        doc = plan(goal={"type": "pulumi:pulumi:Stack", "inputDiff": {}})
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_output_diff_that_is_not_an_object(self):
        for value in (None, [], "x", 0):
            with self.subTest(value=value):
                doc = plan(goal={"inputDiff": {}, "outputDiff": value})
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_output_diff_with_an_unrecognised_key(self):
        doc = plan({"renames": {"a": "b"}})
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_output_diff_collections_of_the_wrong_type(self):
        for diff in ({"adds": []}, {"updates": "x"}, {"deletes": {}}, {"adds": 3}):
            with self.subTest(diff=diff):
                self.assertAsks(self.verdict(no_op_preview(), plan(diff)), "plan-shape")

    def test_plan_steps_that_are_not_a_list_of_strings(self):
        for steps in ("same", [1], {"a": "same"}):
            with self.subTest(steps=steps):
                doc = plan()
                doc["resourcePlans"][_ROOT]["steps"] = steps
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")


class PreviewShapeTests(VerdictCase):
    """A preview JSON this script does not understand reaches the gate."""

    def test_a_missing_preview_file(self):
        verdict = noop.evaluate(
            self.files.missing("preview.json"), self.files.write("plan.json", plan())
        )
        self.assertAsks(verdict, "preview-unreadable")

    def test_an_empty_preview_file(self):
        self.assertAsks(self.verdict("", plan()), "preview-unreadable")

    def test_an_unparseable_preview_file(self):
        self.assertAsks(self.verdict("{not json", plan()), "preview-unreadable")
        self.assertAsks(self.verdict('{"steps": [', plan()), "preview-unreadable")

    def test_a_non_utf8_preview_file(self):
        path = self.files.dir / "preview.json"
        path.write_bytes(b"\xff\xfe\x00")
        verdict = noop.evaluate(str(path), self.files.write("plan.json", plan()))
        self.assertAsks(verdict, "preview-unreadable")

    def test_a_preview_that_is_not_an_object(self):
        for content in ([], "null", "3"):
            with self.subTest(content=content):
                self.assertAsks(self.verdict(content, plan()), "preview-shape")

    def test_no_steps_key(self):
        self.assertAsks(self.verdict({"changeSummary": {"same": 1}}, plan()), "preview-shape")

    def test_steps_that_is_not_a_list(self):
        self.assertAsks(self.verdict({"steps": "nope"}, plan()), "preview-shape")

    def test_an_empty_steps_list(self):
        self.assertAsks(self.verdict(preview(), plan()), "preview-shape")

    def test_a_step_missing_its_op_or_urn(self):
        for step in ({"urn": _ROOT}, {"op": "same"}, {"op": 1, "urn": _ROOT}, "same"):
            with self.subTest(step=step):
                doc = no_op_preview()
                doc["steps"] = [step]
                self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_a_step_whose_urn_is_not_a_urn(self):
        doc = no_op_preview()
        doc["steps"][0]["urn"] = "urn:pulumi:x"
        self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_no_root_stack_step(self):
        self.assertAsks(self.verdict(preview(("same", _SERVER)), plan()), "no-root-stack")

    def test_two_root_stack_steps(self):
        doc = preview(("same", _ROOT), ("same", _ROOT.replace("mail", "other")))
        self.assertAsks(self.verdict(doc, plan()), "no-root-stack")

    def test_no_change_summary(self):
        doc = no_op_preview()
        del doc["changeSummary"]
        self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_a_change_summary_that_counts_a_change_over_same_steps(self):
        doc = preview(("same", _ROOT), summary={"same": 2, "update": 1})
        verdict = self.verdict(doc, plan())
        self.assertAsks(verdict, "summary-changed")
        self.assertEqual(verdict.count, 1)

    def test_a_change_summary_with_a_bad_count(self):
        for summary in ({"same": "3"}, {"same": True}, {"same": -1}, {"same": None}, []):
            with self.subTest(summary=summary):
                doc = no_op_preview()
                doc["changeSummary"] = summary
                self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_a_maybe_corrupt_preview(self):
        doc = preview(("same", _ROOT), maybeCorrupt=True)
        self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_a_change_summary_of_zero_counts_other_than_same_is_fine(self):
        doc = preview(("same", _ROOT), summary={"same": 3, "create": 0})
        self.assertFalse(self.verdict(doc, plan()).has_changes)


class FailClosedTests(VerdictCase):
    def test_an_unexpected_error_is_a_change(self):
        original = noop._preview_verdict

        def boom(path):
            raise RuntimeError("unexpected")

        noop._preview_verdict = boom
        try:
            verdict = self.verdict(no_op_preview(), plan())
        finally:
            noop._preview_verdict = original
        self.assertAsks(verdict, "internal-error")

    def test_a_file_too_deep_to_parse_is_a_change_not_a_crash(self):
        # Proves a document too deep to parse is `preview-unreadable`, never an
        # exception or `internal-error`. On Python 3.14 this size makes the parser
        # raise RecursionError (the loader's except clause); elsewhere the depth
        # rule may give the same code. The depth rule has its own test.
        verdict = self.verdict("[" * 1000000 + "]" * 1000000, plan())
        self.assertAsks(verdict, "preview-unreadable")


def same_step(urn, **extra):
    return {**step_of("same", urn), **extra}


def preview_of(*steps, summary=None):
    doc = no_op_preview()
    doc["steps"] = list(steps)
    if summary is not None:
        doc["changeSummary"] = summary
    return doc


def listed_server(**state):
    """The preview listing the server as a clean custom same step, as the plan describes it."""
    step = same_step(_SERVER)
    for key in ("oldState", "newState"):
        step[key].update(
            {
                "custom": True,
                "parent": _ROOT,
                "provider": f"{_STACK}pulumi:providers:hcloud::default::ID",
                "dependencies": [_ROOT],
            }
        )
    step["newState"].update(state)
    return preview_of(same_step(_ROOT), step)


def plan_with(urn, entry):
    doc = plan()
    doc["resourcePlans"][urn] = entry
    return doc


class NonRootEntryTests(VerdictCase):
    """A planned resource other than the root must be a clean `same` entry."""

    def test_a_goal_type_that_is_not_the_resource_type(self):
        entry = {"goal": clean_goal(type="update"), "steps": ["same"]}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "plan-shape")

    def test_an_entry_with_no_steps(self):
        entry = {"goal": clean_goal(type="hcloud:index/server:Server")}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "plan-shape")

    def test_an_entry_whose_steps_are_null(self):
        entry = {"goal": clean_goal(), "steps": None}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "plan-shape")

    def test_an_entry_with_empty_steps_and_an_update_goal(self):
        entry = {"goal": clean_goal(type="update"), "steps": []}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "plan-shape")

    def test_an_entry_with_a_non_empty_output_diff(self):
        entry = {"goal": clean_goal(outputDiff={"adds": {"k": "v"}}), "steps": ["same"]}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "output-changed")

    def test_an_entry_with_a_non_empty_input_diff(self):
        goal = {"inputDiff": {"updates": {"k": "v"}}, "outputDiff": {}}
        entry = {"goal": goal, "steps": ["same"]}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "steps-changed")

    def test_an_entry_with_no_output_diff_key(self):
        # The root's missing key is tested apart; every entry of a real plan has it.
        entry = {"goal": {"type": "hcloud:index/server:Server", "inputDiff": {}}, "steps": ["same"]}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "plan-shape")

    def test_a_clean_entry_with_the_resource_type_is_no_change(self):
        goal = {"type": "hcloud:index/server:Server", "outputDiff": {}, "inputDiff": {}}
        entry = {"goal": goal, "steps": ["same"]}
        self.assertFalse(self.verdict(no_op_preview(), plan_with(_SERVER, entry)).has_changes)


class NullAndMissingTests(VerdictCase):
    """A null or absent value is not an empty one."""

    def test_null_diff_collections(self):
        for key in ("adds", "updates", "deletes"):
            with self.subTest(key=key):
                self.assertAsks(
                    self.verdict(no_op_preview(), plan({key: None})), "plan-shape"
                )

    def test_a_null_input_diff_collection(self):
        goal = {"inputDiff": {"adds": None}, "outputDiff": {}}
        entry = {"goal": goal, "steps": ["same"]}
        self.assertAsks(self.verdict(no_op_preview(), plan_with(_SERVER, entry)), "plan-shape")

    def test_root_steps_missing(self):
        doc = plan()
        del doc["resourcePlans"][_ROOT]["steps"]
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_root_steps_null(self):
        doc = plan()
        doc["resourcePlans"][_ROOT]["steps"] = None
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_root_steps_empty(self):
        doc = plan(root_steps=())
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")


class SameStepEvidenceTests(VerdictCase):
    """A `same` step that carries its own evidence of a difference is a change."""

    def differing(self, **extra):
        return preview_of(same_step(_ROOT, **extra))

    def states(self, old=None, new=None):
        """Old and new state around the real shape, with each given key set to its value."""
        return {
            "oldState": {**step_of("same", _ROOT)["oldState"], **(old or {})},
            "newState": {**step_of("same", _ROOT)["newState"], **(new or {})},
        }

    def test_the_real_shaped_same_step_is_no_change(self):
        # Old state holds the id and outputs; the new state has neither yet.
        self.assertFalse(self.verdict(self.differing(), plan()).has_changes)

    def test_an_equal_old_and_new_state_on_every_key_is_no_change(self):
        # Every key a state can carry, equal on both sides, id and outputs included.
        both = {key: f"value-of-{key}" for key in STATE_KEYS}
        both |= {"custom": False, "type": "hcloud:index/server:Server", "outputs": {"o": 1}}
        both |= {key: False for key in noop.FLAG_STATE_KEYS}
        step = same_step(_SERVER, oldState=dict(both), newState=dict(both))
        self.assertFalse(self.verdict(preview_of(same_step(_ROOT), step), plan()).has_changes)

    def test_a_difference_on_any_other_state_key_is_a_change(self):
        # Three shapes per key: a value on the new side only, on the old side only,
        # and two different values. The two lenient sets are pinned just below.
        for key in sorted(set(STATE_KEYS) - noop.DIAGNOSTIC_STATE_KEYS - noop.OLD_SIDE_STATE_KEYS):
            for old, new in (({}, {key: "B"}), ({key: "A"}, {}), ({key: "A"}, {key: "B"})):
                with self.subTest(key=key, old=old, new=new):
                    doc = self.differing(**self.states(old, new))
                    self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_the_two_lenient_sets_are_exactly_what_the_engine_source_shows(self):
        # id and outputs: the old state holds them, the new one is read before it
        # copies them. sourcePosition and stackTrace: where the program registered it.
        self.assertEqual(noop.OLD_SIDE_STATE_KEYS, {"id", "outputs"})
        self.assertEqual(noop.DIAGNOSTIC_STATE_KEYS, {"sourcePosition", "stackTrace"})

    def test_id_and_outputs_on_one_side_only_are_no_change_but_two_values_are(self):
        for key in sorted(noop.OLD_SIDE_STATE_KEYS):
            for side in ("old", "new"):
                with self.subTest(key=key, side=side):
                    states = self.states()
                    states["oldState"].pop(key, None)
                    states["newState"].pop(key, None)
                    states[f"{side}State"][key] = {"x": 1} if key == "outputs" else "X"
                    self.assertFalse(self.verdict(self.differing(**states), plan()).has_changes)
            with self.subTest(key=key, shape="two values"):
                doc = self.differing(**self.states({key: "A"}, {key: "B"}))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_the_program_position_may_differ_and_hides_nothing_else(self):
        for key in sorted(noop.DIAGNOSTIC_STATE_KEYS):
            with self.subTest(key=key):
                doc = self.differing(**self.states({key: "A"}, {key: "B"}))
                self.assertFalse(self.verdict(doc, plan()).has_changes)
        for hidden in ("protect", "dependencies", "aliases", "customTimeouts", "propertyDependencies"):
            with self.subTest(hidden=hidden):
                changed = {"sourcePosition": "B", hidden: "X"}
                doc = self.differing(**self.states({"sourcePosition": "A"}, changed))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def beside_root(self, step):
        """A preview listing the clean root and one more same step."""
        return preview_of(same_step(_ROOT), step)

    def custom_pair(self, urn, old_provider="P", new_provider="P", **new_extra):
        """A custom same step: provider on each side as given (None leaves it off)."""
        step = same_step(urn)
        for state, provider in ((step["oldState"], old_provider), (step["newState"], new_provider)):
            state["custom"] = True
            if provider is not None:
                state["provider"] = provider
        step["newState"].update(new_extra)
        return step

    def test_a_custom_same_step_with_a_provider_on_both_sides_is_no_change(self):
        self.assertFalse(self.verdict(self.beside_root(self.custom_pair(_SERVER)), plan()).has_changes)

    def test_a_custom_new_state_with_no_provider_is_a_change(self):
        for old_provider in ("P", None):
            with self.subTest(old_provider=old_provider):
                doc = self.beside_root(self.custom_pair(_SERVER, old_provider, None))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_a_provider_on_the_new_side_only_is_a_change(self):
        for custom in (True, False):
            with self.subTest(custom=custom):
                step = self.custom_pair(_SERVER, None, "P")
                step["oldState"]["custom"] = step["newState"]["custom"] = custom
                self.assertAsks(self.verdict(self.beside_root(step), plan()), "steps-changed")

    def test_two_different_providers_are_a_change(self):
        doc = self.beside_root(self.custom_pair(_SERVER, "P", "Q"))
        self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_a_provider_resource_itself_needs_no_provider(self):
        urn = f"{_STACK}pulumi:providers:hcloud::default"
        self.assertFalse(self.verdict(self.beside_root(self.custom_pair(urn, None, None)), plan()).has_changes)

    def test_a_component_needs_no_provider(self):
        step = same_step(_SERVER)
        self.assertEqual(step["newState"]["custom"], False)
        self.assertFalse(self.verdict(self.beside_root(step), plan()).has_changes)

    def test_old_and_new_inputs_that_differ_on_the_root(self):
        doc = self.differing(**self.states({"inputs": {"a": 1}}, {"inputs": {"a": 2}}))
        self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_inputs_on_one_side_only(self):
        for old, new in (({"inputs": {"a": 1}}, {}), ({}, {"inputs": {"a": 1}}), ({"inputs": {}}, {})):
            with self.subTest(old=old, new=new):
                doc = self.differing(**self.states(old, new))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_old_and_new_outputs_that_differ_on_another_resource(self):
        other = same_step(_SERVER)
        other["oldState"]["outputs"] = {"a": 1}
        other["newState"]["outputs"] = {"a": 2}
        doc = preview_of(same_step(_ROOT), other)
        self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_an_id_or_parent_that_differs(self):
        for key in ("id", "parent", "provider"):
            with self.subTest(key=key):
                doc = self.differing(**self.states({key: "A"}, {key: "B"}))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_a_parent_on_one_side_only(self):
        doc = self.differing(**self.states({"parent": "P"}, {}))
        self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_outputs_or_id_on_the_old_side_only_is_the_real_shape(self):
        # Disagreed with in review: a real new state is read before it copies
        # these, so one side holding them is no evidence of a change.
        doc = self.differing(**self.states({"outputs": {"k": "v"}, "id": "X"}, {}))
        self.assertFalse(self.verdict(doc, plan()).has_changes)

    def test_a_state_that_is_missing_null_or_not_an_object(self):
        for extra in ({"oldState": None}, {"newState": None}, {"oldState": []}, {"newState": "s"}):
            with self.subTest(extra=extra):
                self.assertAsks(self.verdict(self.differing(**extra), plan()), "preview-shape")

    def test_a_same_step_with_no_states(self):
        step = {"op": "same", "urn": _ROOT}
        self.assertAsks(self.verdict(preview_of(step), plan()), "preview-shape")

    def test_diffs_listed_on_a_same_step(self):
        for key in noop.STEP_DIFF_KEYS:
            with self.subTest(key=key):
                doc = self.differing(**{key: ["a"] if key != "detailedDiff" else {"a": {}}})
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_a_diff_field_of_another_type_is_not_evidence_of_nothing(self):
        for key in noop.STEP_DIFF_KEYS:
            for value in (0, "", False, 1, "x", True):
                with self.subTest(key=key, value=value):
                    doc = self.differing(**{key: value})
                    self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_an_unknown_key_on_a_same_step(self):
        for key in ("diffs", "extra", "inputs"):
            self.assertNotIn(key, noop.STEP_KEYS)
            with self.subTest(key=key):
                self.assertAsks(self.verdict(self.differing(**{key: 0}), plan()), "preview-shape")

    def test_empty_and_null_diff_fields_on_a_same_step_are_no_change(self):
        doc = self.differing(detailedDiff=None, diffReasons=[], replaceReasons={})
        self.assertFalse(self.verdict(doc, plan()).has_changes)

    def test_every_known_step_key_is_accepted(self):
        step = same_step(_ROOT, provider="urn:p", detailedDiff=None, diffReasons=None, replaceReasons=None)
        self.assertEqual(set(step) - noop.STEP_KEYS, set())
        self.assertFalse(self.verdict(preview_of(step), plan()).has_changes)

    def test_a_truthy_pending_flag_on_either_side_is_a_change(self):
        # A same step that still owes work; the four names are written out, not read back.
        for key in ("taint", "pendingReplacement", "delete", "initErrors"):
            for value in (True, 1, "x", [1], {"a": 1}):
                for sides in (("old",), ("new",), ("old", "new")):
                    with self.subTest(key=key, value=value, sides=sides):
                        states = self.states()
                        for side in sides:
                            states[f"{side}State"][key] = value
                        self.assertAsks(self.verdict(self.differing(**states), plan()), "steps-changed")

    def test_a_falsy_pending_flag_on_both_sides_is_no_change(self):
        for key in ("taint", "pendingReplacement", "delete", "initErrors"):
            for value in (False, None, 0, [], {}):
                with self.subTest(key=key, value=value):
                    doc = self.differing(**self.states({key: value}, {key: value}))
                    self.assertFalse(self.verdict(doc, plan()).has_changes)

    def test_the_flag_keys_are_the_four_the_engine_acts_on(self):
        self.assertEqual(set(noop.FLAG_STATE_KEYS), {"taint", "pendingReplacement", "delete", "initErrors"})

    def test_a_flag_on_a_listed_non_root_resource_is_a_change(self):
        step = same_step(_SERVER)
        step["newState"]["taint"] = True
        self.assertAsks(self.verdict(self.beside_root(step), plan()), "steps-changed")

    def test_values_of_a_different_type_are_a_change_though_python_calls_them_equal(self):
        for old, new in (
            (False, 0),
            (0, False),
            (True, 1),
            (1, True),
            ({"a": False}, {"a": 0}),
            ([True], [1]),
            ({"a": [{"b": True}]}, {"a": [{"b": 1}]}),
            ("1", 1),
        ):
            with self.subTest(old=old, new=new):
                doc = self.differing(**self.states({"protect": old}, {"protect": new}))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_numbers_that_are_equal_are_no_change_whatever_their_spelling(self):
        for old, new in ((1, 1.0), ({"a": 2}, {"a": 2.0}), ([0], [0.0])):
            with self.subTest(old=old, new=new):
                doc = self.differing(**self.states({"inputs": old}, {"inputs": new}))
                self.assertFalse(self.verdict(doc, plan()).has_changes)

    def test_a_custom_flag_that_is_not_a_bool_is_a_change(self):
        for value in (0, 1, "true", "", None, [], {}):
            for sides in (("old",), ("new",), ("old", "new")):
                with self.subTest(value=value, sides=sides):
                    states = self.states()
                    for side in sides:
                        states[f"{side}State"]["custom"] = value
                    self.assertAsks(self.verdict(self.differing(**states), plan()), "steps-changed")

    def test_the_provider_exemption_needs_the_whole_type_token(self):
        for token, expected in (
            ("pulumi:providers:hcloud", True),
            ("pulumi:providers:aws", True),
            ("pulumi:providers:", False),
            ("pulumi:providers", False),
            ("pulumi:providers:hcloud:extra", False),
            ("pulumi:providersx:hcloud", False),
            ("xpulumi:providers:hcloud", False),
            ("other:providers:hcloud", False),
            ("hcloud:index/server:Server", False),
            ("", False),
            (None, False),
            (5, False),
            (["pulumi:providers:hcloud"], False),
        ):
            with self.subTest(token=token):
                self.assertIs(noop._is_provider_type(token), expected)

    def test_a_custom_resource_that_only_looks_like_a_provider_needs_a_provider(self):
        for leaf in ("other:providers:hcloud", "pulumi:providersx:hcloud", "xpulumi:providers:hcloud"):
            with self.subTest(leaf=leaf):
                step = self.custom_pair(f"{_STACK}{leaf}::default", None, None)
                for side in ("oldState", "newState"):
                    step[side]["type"] = leaf
                self.assertAsks(self.verdict(self.beside_root(step), plan()), "steps-changed")

    def test_a_state_type_that_is_not_the_urns_type_is_a_change(self):
        for side in ("oldState", "newState"):
            for value in ("pulumi:providers:hcloud", "other:index/thing:Thing", "", 5, None):
                with self.subTest(side=side, value=value):
                    step = same_step(_SERVER)
                    step[side]["type"] = value
                    self.assertAsks(self.verdict(self.beside_root(step), plan()), "steps-changed")

    def test_the_same_foreign_type_on_both_sides_is_still_a_change(self):
        # Equal on both sides, so only the comparison with the URN's own type can see it.
        for value in ("other:index/thing:Thing", "pulumi:providers:hcloud", ""):
            with self.subTest(value=value):
                step = same_step(_SERVER)
                for side in ("oldState", "newState"):
                    step[side]["type"] = value
                self.assertAsks(self.verdict(self.beside_root(step), plan()), "steps-changed")

    def test_a_key_on_one_side_of_a_nested_object_is_a_change_either_way(self):
        for old, new in (
            ({"a": 1}, {"a": 1, "b": 2}),
            ({"a": 1, "b": 2}, {"a": 1}),
            ({"a": {"x": 1}}, {"a": {"x": 1, "y": None}}),
            ({"a": {"x": 1, "y": None}}, {"a": {"x": 1}}),
            ({"a": [{"x": 1}]}, {"a": [{"x": 1, "y": 0}]}),
            ({"a": None}, {}),
            ({}, {"a": None}),
        ):
            with self.subTest(old=old, new=new):
                doc = self.differing(**self.states({"inputs": old}, {"inputs": new}))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_lists_of_different_length_are_a_change_either_way(self):
        for old, new in (
            ([1], [1, 2]),
            ([1, 2], [1]),
            ([], [0]),
            ({"a": [1]}, {"a": [1, 1]}),
            ({"a": [[1], [2]]}, {"a": [[1], [2], []]}),
        ):
            with self.subTest(old=old, new=new):
                doc = self.differing(**self.states({"dependencies": old}, {"dependencies": new}))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_a_provider_type_in_the_state_does_not_exempt_a_resource_with_another_urn(self):
        step = self.custom_pair(_SERVER, None, None)
        step["newState"]["type"] = "pulumi:providers:hcloud"
        self.assertAsks(self.verdict(self.beside_root(step), plan()), "steps-changed")

    def test_a_state_with_no_type_is_not_a_change_by_itself(self):
        step = same_step(_SERVER)
        for side in ("oldState", "newState"):
            step[side].pop("type", None)
        self.assertFalse(self.verdict(self.beside_root(step), plan()).has_changes)

    def test_the_same_urn_listed_twice_is_not_understood(self):
        for urns in ((_ROOT, _SERVER, _SERVER), (_ROOT, _ROOT), (_SERVER, _ROOT, _SERVER)):
            with self.subTest(urns=urns):
                doc = preview_of(*(same_step(u) for u in urns), summary={"same": len(urns)})
                self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_a_duplicate_beside_a_changing_step_still_says_the_steps_changed(self):
        doc = preview(("same", _ROOT), ("create", _SERVER), ("create", _SERVER), summary={"same": 1, "create": 2})
        self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_two_different_urns_are_not_a_duplicate(self):
        other = f"{_STACK}hcloud:index/server:Server::mx2"
        doc = preview_of(same_step(_ROOT), same_step(_SERVER), same_step(other), summary={"same": 3})
        self.assertFalse(self.verdict(doc, plan()).has_changes)


class KnownKeyTests(VerdictCase):
    """A top-level key never seen in either file reaches the gate."""

    def test_an_unknown_preview_key(self):
        for key in ("changes", "resources"):
            self.assertNotIn(key, noop.PREVIEW_KEYS)
            with self.subTest(key=key):
                doc = no_op_preview()
                doc[key] = []
                self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_an_unknown_plan_key(self):
        for key in ("changes", "resources"):
            self.assertNotIn(key, noop.PLAN_KEYS)
            with self.subTest(key=key):
                doc = plan()
                doc[key] = {}
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_an_unknown_key_at_every_level_of_both_files(self):
        key = "zz-never-seen"
        for level in ("preview", "preview-step", "plan", "plan-entry", "plan-goal"):
            for urn in (_ROOT, _SERVER):
                if level in ("preview", "plan") and urn == _SERVER:
                    continue
                with self.subTest(level=level, urn=urn):
                    preview_doc, plan_doc = no_op_preview(), observed_plan()
                    if level == "preview":
                        preview_doc[key] = 1
                    elif level == "preview-step":
                        preview_doc["steps"][0][key] = 1
                    elif level == "plan":
                        plan_doc[key] = 1
                    elif level == "plan-entry":
                        plan_doc["resourcePlans"][urn][key] = 1
                    else:
                        plan_doc["resourcePlans"][urn]["goal"][key] = 1
                    self.assertTrue(self.verdict(preview_doc, plan_doc).has_changes)
        self.assertFalse(self.verdict(no_op_preview(), observed_plan()).has_changes)

    def test_every_known_preview_key_is_accepted(self):
        doc = no_op_preview() | {"config": {}}
        for key in noop.PREVIEW_KEYS - set(doc):
            doc[key] = None
        self.assertFalse(self.verdict(doc, plan()).has_changes)

    def test_every_known_plan_key_is_accepted(self):
        doc = plan()
        for key in noop.PLAN_KEYS - set(doc):
            doc[key] = {}
        self.assertFalse(self.verdict(no_op_preview(), doc).has_changes)


class RootIdentityTests(VerdictCase):
    """The root is the exact stack type, named `<project>-<stack>`."""

    def test_a_preview_whose_only_stack_has_another_type(self):
        urn = f"{_STACK}example:pulumi:Stack::branchleft-mail-production"
        self.assertAsks(self.verdict(preview(("same", urn)), plan()), "no-root-stack")

    def test_a_preview_whose_stack_type_merely_ends_like_the_root(self):
        urn = f"{_STACK}parent$pulumi:pulumi:Stack::branchleft-mail-production"
        self.assertAsks(self.verdict(preview(("same", urn)), plan()), "no-root-stack")

    def test_a_preview_whose_root_is_named_something_else(self):
        urn = f"{_STACK}pulumi:pulumi:Stack::spoof"
        self.assertAsks(self.verdict(preview(("same", urn)), plan()), "no-root-stack")

    def test_a_second_stack_typed_step_beside_the_root(self):
        urn = f"{_STACK}pulumi:pulumi:Stack::other"
        doc = preview(("same", _ROOT), ("same", urn), summary={"same": 2})
        self.assertAsks(self.verdict(doc, plan()), "no-root-stack")

    def test_a_resource_named_like_a_root_urn_in_the_preview(self):
        urn = f"{_STACK}hcloud:index/server:Server::pulumi:pulumi:Stack::x"
        doc = preview(("same", _ROOT), ("same", urn), summary={"same": 2})
        self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_a_resource_named_like_a_root_urn_in_the_plan(self):
        entry = {"goal": clean_goal(), "steps": ["same"]}
        doc = plan_with(f"{_STACK}hcloud:index/server:Server::pulumi:pulumi:Stack::x", entry)
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_second_stack_typed_entry_beside_the_root(self):
        entry = {"goal": clean_goal(), "steps": ["same"]}
        doc = plan_with(f"{_STACK}pulumi:pulumi:Stack::other", entry)
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_plan_whose_only_stack_has_another_type(self):
        doc = plan(root=f"{_STACK}example:pulumi:Stack::branchleft-mail-production")
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_plan_whose_root_is_named_something_else(self):
        doc = plan(root=f"{_STACK}pulumi:pulumi:Stack::spoof")
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")


OBSERVED_PREVIEW_KEYS = frozenset({"config", "steps", "duration", "changeSummary"})
CODE_ONLY_PREVIEW_KEYS = frozenset({"diagnostics", "maybeCorrupt"})
OBSERVED_STEP_KEYS = frozenset({"op", "urn", "provider", "newState", "detailedDiff"})
CODE_ONLY_STEP_KEYS = frozenset({"oldState", "diffReasons", "replaceReasons"})
OBSERVED_PLAN_KEYS = frozenset({"manifest", "config", "resourcePlans"})


def observed_plan():
    """A clean plan with every key the real plan files carried, at every level."""
    root_goal = {
        "type": "pulumi:pulumi:Stack",
        "name": "branchleft-mail-production",
        "custom": False,
        "inputDiff": {},
        "outputDiff": {},
        "protect": False,
        "customTimeouts": {},
    }
    child_goal = {
        "type": "hcloud:index/server:Server",
        "name": "mx1",
        "custom": True,
        "inputDiff": {},
        "outputDiff": {},
        "parent": _ROOT,
        "protect": False,
        "provider": f"{_STACK}pulumi:providers:hcloud::default::ID",
        "propertyDependencies": {"name": []},
        "dependencies": [_ROOT],
        "ignoreChanges": ["userData"],
        "structuredAliases": [{"URN": "", "Name": "old-mx1", "Type": "", "Project": "", "Stack": "", "Parent": "", "NoParent": False}],
        "customTimeouts": {},
    }
    manifest = {"time": "T", "magic": "M", "version": "v"}
    return {
        "manifest": manifest,
        "config": {"branchleft-mail:k": "v"},
        "resourcePlans": {
            _ROOT: {"goal": root_goal, "steps": ["same"], "state": {}, "seed": "S"},
            _SERVER: {"goal": child_goal, "steps": ["same"], "state": {}, "seed": "S"},
        },
    }


class NestedKeyTests(VerdictCase):
    """An unknown key below the top level reaches the gate too."""

    def test_a_plan_with_every_observed_key_is_no_change(self):
        self.assertFalse(self.verdict(no_op_preview(), observed_plan()).has_changes)

    def test_the_observed_keys_are_the_allowlists(self):
        doc = observed_plan()
        entry_keys = {key for entry in doc["resourcePlans"].values() for key in entry}
        goal_keys = {key for entry in doc["resourcePlans"].values() for key in entry["goal"]}
        self.assertEqual(entry_keys, set(noop.ENTRY_KEYS))
        self.assertEqual(goal_keys, set(noop.GOAL_KEYS))

    def test_each_allowlist_is_the_observed_keys_plus_the_named_code_only_ones(self):
        # Observed: a local run of Pulumi CLI v3.255.0 (create cases only). Code
        # only: read in the engine source, never seen in a run. Widening a list
        # fails here until the key is named on one side or the other.
        cases = (
            (noop.PREVIEW_KEYS, OBSERVED_PREVIEW_KEYS, CODE_ONLY_PREVIEW_KEYS),
            (noop.PLAN_KEYS, OBSERVED_PLAN_KEYS, frozenset()),
            (noop.STEP_KEYS, OBSERVED_STEP_KEYS, CODE_ONLY_STEP_KEYS),
        )
        for allowed, observed, from_code in cases:
            with self.subTest(allowed=sorted(allowed)):
                self.assertEqual(set(allowed), set(observed) | set(from_code))
                self.assertEqual(set(observed) & set(from_code), set())

    def test_an_unknown_key_on_the_root_entry(self):
        for key in ("outputs", "changes"):
            self.assertNotIn(key, noop.ENTRY_KEYS)
            with self.subTest(key=key):
                doc = observed_plan()
                doc["resourcePlans"][_ROOT][key] = {}
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_an_unknown_key_on_a_non_root_entry(self):
        doc = observed_plan()
        doc["resourcePlans"][_SERVER]["replaceOn"] = ["x"]
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_an_unknown_goal_key(self):
        # `dependencies`, `ignoreChanges` and `structuredAliases` are the owner's three.
        for key in ("properties", "replaceOn", "aliases", "id", "replaceOnChanges", "deleteBeforeReplace", "retainOnDelete"):
            self.assertNotIn(key, noop.GOAL_KEYS)
            for urn in (_ROOT, _SERVER):
                with self.subTest(key=key, urn=urn):
                    doc = observed_plan()
                    doc["resourcePlans"][urn]["goal"][key] = []
                    self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")


class DetailTests(VerdictCase):
    """A plan-shape verdict names what it did not recognise: names only, never values."""

    def refused(self, mutate):
        doc = observed_plan()
        mutate(doc["resourcePlans"])
        verdict = self.verdict(no_op_preview(), doc)
        self.assertAsks(verdict, "plan-shape")
        return verdict.detail

    def test_unknown_goal_keys_are_named_sorted_across_every_entry(self):
        def mutate(plans):
            plans[_ROOT]["goal"]["aliases"] = [MARKER]
            plans[_SERVER]["goal"]["replaceOnChanges"] = [MARKER]
            plans[_SERVER]["goal"]["aliases"] = [MARKER]

        self.assertEqual(self.refused(mutate), "unrecognised goal key(s): aliases, replaceOnChanges")

    def test_unknown_entry_keys_are_named(self):
        detail = self.refused(lambda plans: plans[_SERVER].update({"replaceOn": MARKER}))
        self.assertEqual(detail, "unrecognised plan entry key(s): replaceOn")

    def test_two_findings_are_joined_and_both_reported(self):
        def mutate(plans):
            plans[_ROOT]["goal"]["replaceOnChanges"] = [MARKER]
            plans[_SERVER]["goal"]["plan_entry_finding"] = []
            plans[_SERVER]["extra"] = 1

        detail = self.refused(mutate)
        self.assertIn("unrecognised plan entry key(s): extra", detail)
        self.assertIn("unrecognised goal key(s): plan_entry_finding, replaceOnChanges", detail)
        self.assertEqual(detail.count("; "), 1)

    def test_a_value_is_never_in_the_detail(self):
        def mutate(plans):
            plans[_SERVER]["goal"]["extra"] = {"k": MARKER, "s": SECRET}
            plans[_SERVER]["state"] = {"k": MARKER}

        detail = self.refused(mutate)
        self.assertEqual(detail, "unrecognised goal key(s): extra")
        for text in (MARKER, "CIPHERTEXT_PLACEHOLDER", SECRET["ciphertext"]):
            self.assertNotIn(text, detail)

    def test_an_odd_key_name_is_not_echoed(self):
        # A key is part of the plan file, which can carry anything: only plain names are printed.
        odd = ("has space", "line\nbreak", "::error::x", "a" * 81, "", "secret=" + MARKER, "<script>")
        for name in odd:
            with self.subTest(name=name):
                detail = self.refused(lambda plans, n=name: plans[_SERVER]["goal"].update({n: 1}))
                self.assertEqual(detail, "unrecognised goal key(s): <odd name>")

    def test_a_long_list_of_names_is_cut_and_counted(self):
        names = [f"key{i:03d}" for i in range(noop.MAX_NAMES + 5)]
        detail = self.refused(lambda plans: plans[_SERVER]["goal"].update({n: 1 for n in names}))
        self.assertTrue(detail.endswith("and 5 more"), detail)
        self.assertEqual(detail.count("key0"), noop.MAX_NAMES)

    def test_an_unreadable_or_clean_plan_has_no_detail(self):
        self.assertEqual(self.verdict(no_op_preview(), observed_plan()).detail, "")
        self.assertEqual(self.verdict(no_op_preview(), "{not json").detail, "")

    def test_a_goal_that_is_wrong_in_another_way_is_still_a_plan_shape_with_no_names(self):
        doc = observed_plan()
        doc["resourcePlans"][_SERVER]["goal"]["customTimeouts"] = {"create": "5m"}
        verdict = self.verdict(no_op_preview(), doc)
        self.assertAsks(verdict, "plan-shape")
        self.assertEqual(verdict.detail, "")

    def test_reason_line_is_empty_without_a_detail(self):
        self.assertEqual(noop.reason_line(noop.Verdict(True, 0, "plan-shape")), "")
        self.assertEqual(noop.reason_line(noop.Verdict(True, 0, "plan-shape", "x: y")), "plan-shape: x: y\n")


_COMP = f"{_STACK}my:mod:Comp::net"
_NESTED = f"{_STACK}my:mod:Comp$hcloud:index/network:Network::n"


class PlanGoalTests(VerdictCase):
    """A goal field other than the diffs holds what a clean plan carries, or is absent."""

    def goal(self, urn, doc):
        return doc["resourcePlans"][urn]["goal"]

    def with_component(self, child_parent=_COMP):
        """The observed plan plus a component under the root and a child of the component."""
        doc = observed_plan()
        comp = clean_goal(type="my:mod:Comp", name="net", custom=False, parent=_ROOT)
        child = clean_goal(type="hcloud:index/network:Network", name="n", custom=True, parent=child_parent)
        doc["resourcePlans"][_COMP] = {"goal": comp, "steps": ["same"]}
        doc["resourcePlans"][_NESTED] = {"goal": child, "steps": ["same"]}
        return doc

    def test_a_component_and_its_child_with_the_right_parents_are_no_change(self):
        self.assertFalse(self.verdict(no_op_preview(), self.with_component()).has_changes)

    def test_an_absent_optional_goal_field_is_the_default(self):
        for urn in (_ROOT, _SERVER):
            for key in sorted(set(noop.GOAL_KEYS) - {"inputDiff", "outputDiff"}):
                with self.subTest(urn=urn, key=key):
                    doc = observed_plan()
                    self.goal(urn, doc).pop(key, None)
                    self.assertFalse(self.verdict(no_op_preview(), doc).has_changes)

    def test_protect_that_is_not_a_boolean_is_a_change(self):
        for urn in (_ROOT, _SERVER):
            for value in ("false", "true", 0, 1, None, []):
                with self.subTest(urn=urn, value=value):
                    doc = observed_plan()
                    self.goal(urn, doc)["protect"] = value
                    self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_custom_timeouts_that_are_not_empty_are_a_change(self):
        for urn in (_ROOT, _SERVER):
            for value in ({"create": "5m"}, {"update": ""}, [], None, "", 0, False):
                with self.subTest(urn=urn, value=value):
                    doc = observed_plan()
                    self.goal(urn, doc)["customTimeouts"] = value
                    self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_goal_name_or_type_that_is_not_the_urns_is_a_change(self):
        for urn in (_ROOT, _SERVER):
            for key, value in (("name", "other"), ("name", None), ("type", "other:Type"), ("type", None)):
                with self.subTest(urn=urn, key=key, value=value):
                    doc = observed_plan()
                    self.goal(urn, doc)[key] = value
                    self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_custom_that_is_not_a_boolean_is_a_change(self):
        for value in ("true", 1, None):
            with self.subTest(value=value):
                doc = observed_plan()
                self.goal(_SERVER, doc)["custom"] = value
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_provider_that_is_not_a_non_empty_string_is_a_change(self):
        for value in ("", 5, None, ["p"]):
            with self.subTest(value=value):
                doc = observed_plan()
                self.goal(_SERVER, doc)["provider"] = value
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_property_dependencies_of_the_wrong_shape_are_a_change(self):
        for value in ([], "x", {"k": "u"}, {"k": [1]}, {"k": None}, None):
            with self.subTest(value=value):
                doc = observed_plan()
                self.goal(_SERVER, doc)["propertyDependencies"] = value
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_child_whose_parent_is_a_second_stack_is_a_change(self):
        other = f"{_STACK}pulumi:pulumi:Stack::other-production"
        for parent in (other, f"{_STACK}hcloud:index/server:Server::not-in-the-plan", "not-a-urn", 5, None, []):
            with self.subTest(parent=parent):
                doc = observed_plan()
                self.goal(_SERVER, doc)["parent"] = parent
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_root_with_a_parent_is_a_change(self):
        for parent in (_ROOT, _SERVER):
            with self.subTest(parent=parent):
                doc = observed_plan()
                self.goal(_ROOT, doc)["parent"] = parent
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_child_that_is_its_own_parent_is_a_change(self):
        doc = observed_plan()
        self.goal(_SERVER, doc)["parent"] = _SERVER
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_component_child_with_no_parent_is_a_change(self):
        doc = self.with_component()
        del self.goal(_NESTED, doc)["parent"]
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_component_child_whose_parent_does_not_build_its_type_is_a_change(self):
        for parent in (_ROOT, _SERVER, _NESTED):
            with self.subTest(parent=parent):
                self.assertAsks(self.verdict(no_op_preview(), self.with_component(parent)), "plan-shape")

    def test_a_component_parent_that_is_not_in_the_plan_is_a_change(self):
        doc = self.with_component()
        del doc["resourcePlans"][_COMP]
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_parent_in_another_stack_or_project_is_a_change(self):
        # The foreign component has the right type chain, so only its stack or
        # project can refuse it.
        for old, new in (("production", "dev"), ("branchleft-mail", "branchleft-hetzner")):
            with self.subTest(old=old):
                foreign = _COMP.replace(old, new)
                doc = self.with_component(child_parent=foreign)
                goal = clean_goal(type="my:mod:Comp", name="net", custom=False)
                doc["resourcePlans"][foreign] = {"goal": goal, "steps": ["same"]}
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def listed_server(self, **state):
        return listed_server(**state)

    def test_a_listed_resource_the_goal_agrees_with_is_no_change(self):
        self.assertFalse(self.verdict(self.listed_server(), observed_plan()).has_changes)

    def test_a_listed_resource_the_goal_disagrees_with_is_a_change(self):
        for field, value in (("custom", False), ("parent", None), ("provider", "other"), ("protect", True)):
            with self.subTest(field=field):
                preview_doc = self.listed_server()
                preview_doc["steps"][1]["newState"][field] = value
                preview_doc["steps"][1]["oldState"][field] = value
                self.assertAsks(self.verdict(preview_doc, observed_plan()), "plan-shape")

    def root_outputs(self, old, state):
        """A clean no-op whose root has these old outputs in the preview and this plan state."""
        preview_doc = no_op_preview()
        preview_doc["steps"][0]["oldState"]["outputs"] = old
        doc = observed_plan()
        doc["resourcePlans"][_ROOT]["state"] = state
        return self.verdict(preview_doc, doc)

    def test_a_plain_root_output_that_differs_from_the_plan_state_is_a_change(self):
        for old, state in (
            ({"o": "v"}, {"o": "w"}),
            ({"o": {"a": [1, 2]}}, {"o": {"a": [1, 3]}}),
            ({"o": False}, {"o": 0}),
            ({"o": 1}, {"o": True}),
            ({"o": "1"}, {"o": 1}),
            ({"o": None}, {"o": "x"}),
        ):
            with self.subTest(old=old, state=state):
                verdict = self.root_outputs(old, state)
                self.assertAsks(verdict, "output-changed")
                self.assertEqual(verdict.count, 1)

    def test_equal_root_outputs_are_no_change(self):
        for old, state in (({"o": "v"}, {"o": "v"}), ({"o": 1}, {"o": 1.0}), ({}, {}), ({"o": {"a": [1]}}, {"o": {"a": [1]}})):
            with self.subTest(old=old, state=state):
                self.assertFalse(self.root_outputs(old, state).has_changes)

    def test_a_secret_root_output_is_not_compared(self):
        # The plan holds ciphertext with a fresh nonce each time, the preview "[secret]":
        # two different ciphertexts, or a secret inside a plain object, are no evidence.
        other = {**SECRET, "ciphertext": "v1:OTHER"}
        for old, state in (
            ({"s": "[secret]"}, {"s": SECRET}),
            ({"s": "[secret]", "o": "v"}, {"s": other, "o": "v"}),
            ({"o": {"s": "[secret]", "p": 1}}, {"o": {"s": SECRET, "p": 1}}),
            ({"s": other}, {"s": SECRET}),
        ):
            with self.subTest(old=old):
                self.assertFalse(self.root_outputs(old, state).has_changes)

    def test_a_secret_on_one_side_only_is_a_change(self):
        # Secret-ness itself changed: the plan's secret faces a plain value, or the
        # preview's placeholder faces a plain value in the plan.
        for old, state in (
            ({"s": "plain"}, {"s": SECRET}),
            ({"s": "[secret]"}, {"s": "plain"}),
            ({"s": 1}, {"s": SECRET}),
            ({"s": {"a": 1}}, {"s": SECRET}),
            ({"s": "[secret]"}, {"s": {"a": 1}}),
        ):
            with self.subTest(old=old, state=state):
                self.assertAsks(self.root_outputs(old, state), "output-changed")

    def test_root_old_outputs_that_are_not_an_object_are_not_understood(self):
        for value in (None, "X", [], ["a"], 1, True):
            with self.subTest(value=value):
                preview_doc = no_op_preview()
                preview_doc["steps"][0]["oldState"]["outputs"] = value
                self.assertAsks(self.verdict(preview_doc, observed_plan()), "preview-shape")

    def test_a_plain_difference_beside_a_secret_is_still_a_change(self):
        verdict = self.root_outputs({"s": "[secret]", "o": "v"}, {"s": SECRET, "o": "w"})
        self.assertAsks(verdict, "output-changed")
        self.assertEqual(verdict.count, 1)

    def test_a_key_on_one_side_of_the_root_outputs_is_the_output_diffs_to_report(self):
        # Pinned: the plan's outputDiff lists adds and deletes, so only shared keys are compared.
        self.assertFalse(self.root_outputs({"o": "v"}, {"o": "v", "extra": "x"}).has_changes)
        self.assertFalse(self.root_outputs({"o": "v", "gone": "x"}, {"o": "v"}).has_changes)

    def test_a_plan_with_no_root_state_is_no_change(self):
        preview_doc = no_op_preview()
        preview_doc["steps"][0]["oldState"]["outputs"] = {"o": "v"}
        doc = observed_plan()
        del doc["resourcePlans"][_ROOT]["state"]
        self.assertFalse(self.verdict(preview_doc, doc).has_changes)

    def test_a_non_root_plan_state_is_not_compared_with_the_preview(self):
        # Decided: a listed child's state carries its provider's `id` among the plain
        # outputs, and only the root's outputs are described by the output diff.
        preview_doc = self.listed_server()
        preview_doc["steps"][1]["oldState"]["outputs"] = {"hex": "abc"}
        doc = observed_plan()
        doc["resourcePlans"][_SERVER]["state"] = {"hex": "zzz"}
        self.assertFalse(self.verdict(preview_doc, doc).has_changes)

    def test_plan_fields_of_the_wrong_type_are_a_change(self):
        for place, key, value in (
            ("entry", "state", []),
            ("entry", "state", "s"),
            ("entry", "state", None),
            ("entry", "seed", 5),
            ("entry", "seed", None),
            ("plan", "manifest", []),
            ("plan", "manifest", None),
            ("plan", "config", "c"),
            ("plan", "config", None),
        ):
            with self.subTest(place=place, key=key, value=value):
                doc = observed_plan()
                target = doc["resourcePlans"][_ROOT] if place == "entry" else doc
                target[key] = value
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_preview_fields_of_the_wrong_type_are_a_change(self):
        for key, value in (("config", []), ("config", None), ("duration", "1"), ("duration", True), ("duration", 1.5), ("duration", -1)):
            with self.subTest(key=key, value=value):
                self.assertAsks(self.verdict(no_op_preview() | {key: value}, observed_plan()), "preview-shape")

    def test_an_entry_with_no_input_diff_is_a_change(self):
        for urn in (_ROOT, _SERVER):
            with self.subTest(urn=urn):
                doc = observed_plan()
                del self.goal(urn, doc)["inputDiff"]
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_an_input_diff_that_is_not_an_object_is_a_change(self):
        for urn in (_ROOT, _SERVER):
            for value in (None, [], "x", 0):
                with self.subTest(urn=urn, value=value):
                    doc = observed_plan()
                    self.goal(urn, doc)["inputDiff"] = value
                    self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_non_empty_input_diff_is_a_change_on_every_entry(self):
        for urn in (_ROOT, _SERVER):
            with self.subTest(urn=urn):
                doc = observed_plan()
                self.goal(urn, doc)["inputDiff"] = {"updates": {"k": "v"}}
                self.assertAsks(self.verdict(no_op_preview(), doc), "steps-changed")


class PreviewFieldTests(VerdictCase):
    """The preview's own top-level fields: diagnostics and maybeCorrupt."""

    def test_diagnostics_that_are_not_empty(self):
        entry = {"severity": "error", "message": "boom"}
        for value in ([entry], "boom", {"k": 1}, 0, 1, False, ""):
            with self.subTest(value=value):
                doc = no_op_preview() | {"diagnostics": value}
                self.assertAsks(self.verdict(doc, plan()), "preview-diagnostics")

    def test_absent_null_and_empty_diagnostics_are_no_change(self):
        for extra in ({}, {"diagnostics": None}, {"diagnostics": []}):
            with self.subTest(extra=extra):
                self.assertFalse(self.verdict(no_op_preview() | extra, plan()).has_changes)

    def test_maybe_corrupt_other_than_absent_null_or_false(self):
        for value in (True, 0, 1, "false", "", {}, []):
            with self.subTest(value=value):
                doc = no_op_preview() | {"maybeCorrupt": value}
                self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_maybe_corrupt_false_or_null_is_accepted_as_a_known_key(self):
        # Fails if the key leaves PREVIEW_KEYS: the unknown-key rule would then
        # reject a value this test requires to be accepted.
        self.assertIn("maybeCorrupt", noop.PREVIEW_KEYS)
        for value in (False, None):
            with self.subTest(value=value):
                doc = no_op_preview() | {"maybeCorrupt": value}
                self.assertFalse(self.verdict(doc, plan()).has_changes)


class StackMatchTests(VerdictCase):
    """The preview and the plan must be for the same stack."""

    def test_a_plan_for_another_stack(self):
        other = _ROOT.replace("production", "dev")
        self.assertAsks(self.verdict(no_op_preview(), plan(root=other)), "stack-mismatch")

    def test_a_plan_for_another_project(self):
        other = _ROOT.replace("branchleft-mail", "branchleft-hetzner")
        self.assertAsks(self.verdict(no_op_preview(), plan(root=other)), "stack-mismatch")

    def test_a_nested_stack_typed_step_beside_the_real_root(self):
        urn = f"{_STACK}my:mod:Comp$pulumi:pulumi:Stack::branchleft-mail-production"
        doc = preview(("same", _ROOT), ("same", urn), summary={"same": 2})
        self.assertAsks(self.verdict(doc, plan()), "no-root-stack")


class JsonHazardTests(VerdictCase):
    def test_a_number_that_overflows_to_infinity(self):
        for literal in ("1e999", "-1e999"):
            with self.subTest(literal=literal):
                text = json.dumps(no_op_preview()).replace('"duration": 1', f'"duration": {literal}')
                self.assertAsks(self.verdict(text, plan()), "preview-unreadable")
                text = json.dumps(plan()).replace('"v0"', literal)
                self.assertAsks(self.verdict(no_op_preview(), text), "plan-unreadable")

    def test_a_finite_exponent_is_fine(self):
        text = json.dumps(no_op_preview() | {"config": {"k": 15}}).replace('"k": 15', '"k": 1.5e3')
        self.assertFalse(self.verdict(text, plan()).has_changes)

    def test_a_duplicate_key_in_the_preview(self):
        text = '{"steps": [], "steps": [], "changeSummary": {"same": 1}}'
        self.assertAsks(self.verdict(text, plan()), "preview-unreadable")

    def test_a_duplicate_key_in_the_plan(self):
        text = json.dumps(plan()).replace('"outputDiff": {}', '"outputDiff": {}, "outputDiff": {}')
        self.assertAsks(self.verdict(no_op_preview(), text), "plan-unreadable")

    def test_nan_and_infinity_in_either_file(self):
        for literal in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(literal=literal):
                text = json.dumps(no_op_preview()).replace('"duration": 1', f'"duration": {literal}')
                self.assertAsks(self.verdict(text, plan()), "preview-unreadable")
                text = json.dumps(plan()).replace('"v0"', literal)
                self.assertAsks(self.verdict(no_op_preview(), text), "plan-unreadable")

    # Written as text, never built as a Python value: json.dumps would hit its own
    # recursion limit. The ceiling keeps every depth test from building a document
    # sized by MAX_DEPTH when it is set absurdly high (that once hung the suite).
    PARSER_SAFE_DEPTH = 900

    def nested_text(self, containers):
        """A JSON document `containers` objects deep, as the value of a `config` key."""
        self.assertLessEqual(noop.MAX_DEPTH, self.PARSER_SAFE_DEPTH, "the depth limit is not below the parser's own")
        return '{"k":' * (containers - 1) + "{}" + "}" * (containers - 1)

    def plan_text_with_config_depth(self, containers):
        """The plan with its `config` as deep as given (the plan object itself is one level)."""
        text = json.dumps(plan() | {"config": 0})
        return text.replace('"config": 0', '"config": ' + self.nested_text(containers - 1))

    def test_the_depth_limit_is_below_what_the_parser_survives(self):
        self.assertLessEqual(noop.MAX_DEPTH, self.PARSER_SAFE_DEPTH)

    def test_a_file_one_level_beyond_the_depth_limit_is_refused(self):
        text = self.plan_text_with_config_depth(noop.MAX_DEPTH + 1)
        self.assertAsks(self.verdict(no_op_preview(), text), "plan-unreadable")

    def test_a_file_exactly_at_the_depth_limit_is_no_change(self):
        text = self.plan_text_with_config_depth(noop.MAX_DEPTH)
        self.assertFalse(self.verdict(no_op_preview(), text).has_changes)

    def test_a_preview_beyond_the_depth_limit_is_refused(self):
        text = json.dumps(no_op_preview() | {"config": 0}).replace(
            '"config": 0', '"config": ' + self.nested_text(noop.MAX_DEPTH)
        )
        self.assertAsks(self.verdict(text, plan()), "preview-unreadable")

    def test_a_file_nested_well_within_the_depth_limit_is_no_change(self):
        text = self.plan_text_with_config_depth(noop.MAX_DEPTH // 2)
        self.assertFalse(self.verdict(no_op_preview(), text).has_changes)

    def test_the_same_file_passed_as_preview_and_plan(self):
        path = self.files.write("both.json", {"steps": [], "resourcePlans": {}})
        self.assertAsks(noop.evaluate(path, path), "same-file")

    def test_the_same_file_by_another_name(self):
        path = pathlib.Path(self.files.write("both.json", no_op_preview()))
        link = self.files.dir / "link.json"
        link.symlink_to(path)
        self.assertAsks(noop.evaluate(str(path), str(link)), "same-file")

    def test_fewer_same_in_the_summary_than_same_steps_listed(self):
        doc = preview_of(same_step(_ROOT), same_step(_SERVER), summary={"same": 1})
        self.assertAsks(self.verdict(doc, plan()), "preview-shape")

    def test_an_equal_same_count_is_no_change(self):
        # The boundary of the `at least` rule: one root step, a summary of one.
        doc = preview_of(same_step(_ROOT), summary={"same": 1})
        verdict = self.verdict(doc, plan())
        self.assertFalse(verdict.has_changes)
        self.assertEqual(verdict, noop.NO_CHANGES)

    def test_more_same_in_the_summary_than_steps_listed_is_the_real_no_op(self):
        # The preview hides same steps other than the root, so the summary
        # counts more than the steps list.
        doc = preview_of(same_step(_ROOT), summary={"same": 4})
        self.assertFalse(self.verdict(doc, plan()).has_changes)


class HeadlineTests(unittest.TestCase):
    def test_no_changes_headline(self):
        self.assertEqual(
            noop.headline(noop.NO_CHANGES),
            "No changes: the apply job is skipped and no approval is requested",
        )

    def test_counted_headlines(self):
        for reason in ("steps-changed", "summary-changed", "output-changed"):
            with self.subTest(reason=reason):
                self.assertEqual(
                    noop.headline(noop.Verdict(True, 3, reason)), "3 changes: approval required"
                )
                self.assertEqual(
                    noop.headline(noop.Verdict(True, 1, reason)), "1 change: approval required"
                )

    def test_a_plan_not_understood_says_so(self):
        for reason in (
            "plan-unreadable",
            "plan-shape",
            "preview-shape",
            "internal-error",
            "no-root-stack",
            "same-file",
        ):
            with self.subTest(reason=reason):
                self.assertEqual(
                    noop.headline(noop.Verdict(True, 0, reason)),
                    "Plan not understood, treated as changes: approval required",
                )


class MainTests(unittest.TestCase):
    def setUp(self):
        self.files = Files(self)

    def run_main(self, preview_doc, plan_doc, outputs=True):
        argv = [
            "plan-has-changes.py",
            self.files.write("preview.json", preview_doc),
            self.files.write("plan.json", plan_doc),
        ]
        out = self.files.dir / "output"
        summary = self.files.dir / "summary"
        if outputs:
            argv += ["--github-output", str(out), "--github-summary", str(summary)]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = noop.main(argv)
        read = lambda p: p.read_text(encoding="utf-8") if p.exists() else ""  # noqa: E731
        return code, buffer.getvalue(), read(out), read(summary)

    def test_a_no_op_writes_false_and_the_no_change_headline_first(self):
        code, stdout, out, summary = self.run_main(no_op_preview(), plan())
        self.assertEqual(code, 0)
        self.assertEqual(out, "has_changes=false\n")
        self.assertEqual(
            summary.splitlines()[0],
            "No changes: the apply job is skipped and no approval is requested",
        )
        self.assertIn("has_changes=false", stdout)

    def test_a_change_writes_true_and_the_count_headline_first(self):
        code, _, out, summary = self.run_main(
            preview(("same", _ROOT), ("create", _SERVER), summary={"same": 1, "create": 1}),
            plan(),
        )
        self.assertEqual(code, 0)
        self.assertEqual(out, "has_changes=true\n")
        self.assertEqual(summary.splitlines()[0], "1 change: approval required")

    def test_an_output_only_change_writes_true(self):
        _, _, out, summary = self.run_main(no_op_preview(), plan({"adds": {"a": 1, "b": 2}}))
        self.assertEqual(out, "has_changes=true\n")
        self.assertEqual(summary.splitlines()[0], "2 changes: approval required")

    def test_an_unreadable_plan_still_exits_zero_with_true(self):
        code, _, out, summary = self.run_main(no_op_preview(), "{not json")
        self.assertEqual(code, 0)
        self.assertEqual(out, "has_changes=true\n")
        self.assertEqual(
            summary.splitlines()[0],
            "Plan not understood, treated as changes: approval required",
        )

    def test_stdout_carries_no_plan_values(self):
        for plan_doc in (
            plan(),
            plan({"adds": {"k": MARKER, "secret_one": SECRET}}),
            "{not json " + MARKER,
        ):
            with self.subTest(plan=str(plan_doc)[:20]):
                _, stdout, out, summary = self.run_main(no_op_preview(), plan_doc)
                for text in (stdout, out, summary):
                    self.assertNotIn(MARKER, text)
                    self.assertNotIn("CIPHERTEXT_PLACEHOLDER", text)

    def test_an_unwritable_output_file_exits_one(self):
        argv = [
            "plan-has-changes.py",
            self.files.write("preview.json", no_op_preview()),
            self.files.write("plan.json", plan()),
            "--github-output",
            str(self.files.dir / "no-such-dir" / "output"),
            "--github-summary",
            str(self.files.dir / "summary"),
        ]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(noop.main(argv), 1)

    def run_with_flags(self, flags):
        argv = [
            "plan-has-changes.py",
            self.files.write("preview.json", no_op_preview()),
            self.files.write("plan.json", plan()),
            *flags,
        ]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            return noop.main(argv), buffer.getvalue()

    def test_an_empty_output_path_exits_one_with_a_message(self):
        summary = self.files.dir / "summary"
        code, stdout = self.run_with_flags(
            ["--github-output", "", "--github-summary", str(summary)]
        )
        self.assertEqual(code, 1)
        self.assertIn("::error::--github-output names no file", stdout)
        self.assertFalse(summary.exists())

    def test_an_empty_summary_path_exits_one_with_a_message(self):
        out = self.files.dir / "output"
        code, stdout = self.run_with_flags(["--github-output", str(out), "--github-summary", ""])
        self.assertEqual(code, 1)
        self.assertIn("::error::--github-summary names no file", stdout)
        self.assertFalse(out.exists())

    def test_a_missing_summary_directory_exits_one(self):
        code, stdout = self.run_with_flags(
            [
                "--github-output",
                str(self.files.dir / "output"),
                "--github-summary",
                str(self.files.dir / "no-such-dir" / "summary"),
            ]
        )
        self.assertEqual(code, 1)
        self.assertIn("::error::cannot write the plan verdict", stdout)

    def test_a_missing_output_flag_exits_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                self.run_with_flags(["--github-summary", str(self.files.dir / "summary")])
        self.assertEqual(raised.exception.code, 2)

    def refused(self, output, summary, expect_in_stdout):
        out_path = pathlib.Path(output)
        code, stdout = self.run_with_flags(["--github-output", output, "--github-summary", summary])
        self.assertEqual(code, 1)
        self.assertIn(expect_in_stdout, stdout)
        return out_path

    def test_a_dangling_symlink_is_refused_and_its_target_is_not_created(self):
        # Opening a dangling link for append would create the file it points at.
        target = self.files.dir / "target"
        link = self.files.dir / "link"
        link.symlink_to(target)
        good = str(self.files.dir / "good")
        self.refused(str(link), good, "::error::--github-output is a symlink to a missing target")
        self.refused(good, str(link), "::error::--github-summary is a symlink to a missing target")
        self.assertFalse(target.exists())
        self.assertFalse((self.files.dir / "good").exists())

    def test_a_plan_shape_detail_reaches_the_summary_and_stdout_and_no_value_does(self):
        doc = plan()
        doc["resourcePlans"][_ROOT]["goal"]["replaceOnChanges"] = [MARKER]
        code, stdout, out, summary = self.run_main(no_op_preview(), doc)
        self.assertEqual(code, 0)
        self.assertEqual(out, "has_changes=true\n")
        lines = summary.splitlines()
        self.assertEqual(lines[0], "Plan not understood, treated as changes: approval required")
        self.assertEqual(lines[1], "plan-shape: unrecognised goal key(s): replaceOnChanges")
        self.assertIn("plan-shape: unrecognised goal key(s): replaceOnChanges", stdout.splitlines())
        for text in (stdout, out, summary):
            self.assertNotIn(MARKER, text)

    def test_the_detail_reaches_the_summary_and_stdout(self):
        doc = preview(("same", _ROOT), ("update", _SERVER), summary={"update": 1, "same": 1})
        code, stdout, _, summary = self.run_main(doc, plan())
        self.assertEqual(code, 0)
        self.assertEqual(summary.splitlines()[0], "1 change: approval required")
        self.assertEqual(summary.splitlines()[1], "steps-changed: step operation(s): update")
        self.assertIn("steps-changed: step operation(s): update", stdout.splitlines())

    def test_a_clean_no_op_has_no_reason_line(self):
        _, stdout, _, summary = self.run_main(no_op_preview(), plan())
        self.assertEqual(summary, noop.HEADLINE_NO_CHANGES + "\n\n")
        self.assertEqual(len(stdout.splitlines()), 1)

    def test_a_whitespace_only_path_is_refused_and_writes_nothing(self):
        # Run in the temp directory, so a path that is wrongly accepted creates
        # its file there and not in the checkout.
        previous = os.getcwd()
        os.chdir(self.files.dir)
        self.addCleanup(os.chdir, previous)
        good = str(self.files.dir / "good")
        for blank in ("   ", "\t", " \n"):
            with self.subTest(blank=blank):
                self.refused(blank, good, "::error::--github-output names no file")
                self.refused(good, blank, "::error::--github-summary names no file")
                self.assertFalse((self.files.dir / "good").exists())
                self.assertFalse(pathlib.Path(blank).exists())

    @unittest.skipUnless(os.path.exists(os.devnull), "needs a null device")
    def test_a_device_file_is_refused(self):
        good = str(self.files.dir / "good")
        self.refused(os.devnull, good, "::error::--github-output is not a regular file")
        self.refused(good, os.devnull, "::error::--github-summary is not a regular file")
        self.assertFalse((self.files.dir / "good").exists())

    @unittest.skipUnless(os.path.exists(os.devnull), "needs a null device")
    def test_a_symlink_to_a_device_file_is_refused(self):
        link = self.files.dir / "link"
        link.symlink_to(os.devnull)
        good = str(self.files.dir / "good")
        self.refused(str(link), good, "::error::--github-output is not a regular file")
        self.refused(good, str(link), "::error::--github-summary is not a regular file")

    def test_a_directory_is_refused(self):
        good = str(self.files.dir / "good")
        self.refused(str(self.files.dir), good, "::error::--github-output is not a regular file")

    def test_the_same_path_for_output_and_summary_is_refused(self):
        same = self.files.dir / "both"
        self.refused(str(same), str(same), "::error::--github-output and --github-summary name the same file")
        self.assertFalse(same.exists())

    def test_the_same_file_by_a_symlink_is_refused(self):
        real = self.files.dir / "real"
        real.write_text("", encoding="utf-8")
        link = self.files.dir / "link"
        link.symlink_to(real)
        self.refused(str(real), str(link), "name the same file")
        self.assertEqual(real.read_text(encoding="utf-8"), "")

    def test_the_same_file_by_a_hard_link_is_refused(self):
        # A hard link has its own name and the same inode: a path comparison,
        # resolved or not, tells them apart; only the file identity does not.
        real = self.files.dir / "real"
        real.write_text("", encoding="utf-8")
        link = self.files.dir / "hard"
        os.link(real, link)
        self.assertNotEqual(os.path.realpath(real), os.path.realpath(link))
        self.refused(str(real), str(link), "name the same file")
        self.refused(str(link), str(real), "name the same file")
        self.assertEqual(real.read_text(encoding="utf-8"), "")

    def test_a_symlink_to_a_regular_file_is_written_through(self):
        real = self.files.dir / "real"
        real.write_text("", encoding="utf-8")
        link = self.files.dir / "link"
        link.symlink_to(real)
        code, _ = self.run_with_flags(
            ["--github-output", str(link), "--github-summary", str(self.files.dir / "summary")]
        )
        self.assertEqual(code, 0)
        self.assertEqual(real.read_text(encoding="utf-8"), "has_changes=false\n")

    def test_a_usage_error_exits_two(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                noop.main(["plan-has-changes.py"])
        self.assertEqual(raised.exception.code, 2)

    def test_the_output_files_are_appended_to_not_replaced(self):
        out = self.files.dir / "output"
        out.write_text("earlier=1\n", encoding="utf-8")
        argv = [
            "plan-has-changes.py",
            self.files.write("preview.json", no_op_preview()),
            self.files.write("plan.json", plan()),
            "--github-output",
            str(out),
            "--github-summary",
            str(self.files.dir / "summary"),
        ]
        with contextlib.redirect_stdout(io.StringIO()):
            noop.main(argv)
        self.assertEqual(out.read_text(encoding="utf-8"), "earlier=1\nhas_changes=false\n")


class ImportTests(unittest.TestCase):
    def test_the_script_keeps_annotations_lazy_so_it_imports_on_python_3_9(self):
        # `Verdict | None` fails at import on 3.9 without it, and unittest then
        # drops this whole module from the count with one synthetic error.
        path = pathlib.Path(__file__).resolve().parent / "plan-has-changes.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names = [alias.name for node in tree.body if isinstance(node, ast.ImportFrom) and node.module == "__future__" for alias in node.names]
        self.assertIn("annotations", names)


FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
_DEP_NET = "urn:pulumi:production::planprobe::random:index/randomPet:RandomPet::net"
_DEP_SUB = "urn:pulumi:production::planprobe::random:index/randomPet:RandomPet::sub"


class OwnerRuledGoalKeyTests(VerdictCase):
    """The owner's ruling of 26 Oct 2026: `dependencies`, `ignoreChanges`,
    `structuredAliases` and `protect: true` read clean when unchanged, and a changed or
    malformed one still reads as a change. Where the preview lists a resource its stored
    state is the "before"; a resource it hides is read for its shape only (see the doc)."""

    def real(self):
        """The preview and plan of the real-shaped fixtures: Pulumi v3.255.0 goals with every
        one of these keys, protect true on two resources, and only the root listed."""
        preview_doc = json.loads((FIXTURES / "preview-real-shape-no-op.json").read_text(encoding="utf-8"))
        plan_doc = json.loads((FIXTURES / "plan-real-shape-protected.json").read_text(encoding="utf-8"))
        return preview_doc, plan_doc

    def goal(self, doc, name):
        urn = next(key for key in doc["resourcePlans"] if key.endswith(f"::{name}"))
        return doc["resourcePlans"][urn]["goal"]

    def test_the_real_shaped_fixtures_carry_all_four_keys_and_are_no_change(self):
        preview_doc, plan_doc = self.real()
        sub = self.goal(plan_doc, "sub")
        self.assertTrue(sub["protect"])
        for key in ("dependencies", "ignoreChanges", "structuredAliases"):
            self.assertIn(key, sub)
        self.assertEqual(self.verdict(preview_doc, plan_doc), noop.NO_CHANGES)

    def test_protect_true_on_a_hidden_resource_is_no_change(self):
        doc = observed_plan()
        doc["resourcePlans"][_SERVER]["goal"]["protect"] = True
        self.assertFalse(self.verdict(no_op_preview(), doc).has_changes)

    def test_protect_true_on_the_root_is_no_change_only_if_the_listed_root_is_protected(self):
        # The preview always lists the root, so its stored state is the "before".
        doc = observed_plan()
        doc["resourcePlans"][_ROOT]["goal"]["protect"] = True
        self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")
        preview_doc = no_op_preview()
        for side in ("oldState", "newState"):
            preview_doc["steps"][0][side]["protect"] = True
        self.assertFalse(self.verdict(preview_doc, doc).has_changes)

    def test_protect_true_on_a_listed_resource_that_is_protected_before_and_after_is_no_change(self):
        preview_doc = listed_server(protect=True)
        preview_doc["steps"][1]["oldState"]["protect"] = True
        doc = observed_plan()
        doc["resourcePlans"][_SERVER]["goal"]["protect"] = True
        self.assertFalse(self.verdict(preview_doc, doc).has_changes)

    def test_protect_that_changed_is_a_change_in_either_direction(self):
        # Pulumi lists a `same` step whose protect flag changed, and the reader reads its two states.
        for old, new in ((True, False), (False, True), (True, None), (None, True)):
            with self.subTest(old=old, new=new):
                preview_doc = listed_server()
                for side, value in (("oldState", old), ("newState", new)):
                    if value is not None:
                        preview_doc["steps"][1][side]["protect"] = value
                doc = observed_plan()
                doc["resourcePlans"][_SERVER]["goal"]["protect"] = bool(new)
                self.assertAsks(self.verdict(preview_doc, doc), "steps-changed")

    def test_a_goal_whose_protect_disagrees_with_the_listed_new_state_is_a_change(self):
        for goal_value, state_value in ((True, False), (False, True)):
            with self.subTest(goal=goal_value, state=state_value):
                preview_doc = listed_server()
                for side in ("oldState", "newState"):
                    preview_doc["steps"][1][side]["protect"] = state_value
                doc = observed_plan()
                doc["resourcePlans"][_SERVER]["goal"]["protect"] = goal_value
                self.assertAsks(self.verdict(preview_doc, doc), "plan-shape")

    def test_each_key_absent_or_empty_is_no_change(self):
        for key in ("dependencies", "ignoreChanges", "structuredAliases"):
            for value in (None, []):
                with self.subTest(key=key, value=value):
                    doc = observed_plan()
                    if value is None:
                        doc["resourcePlans"][_SERVER]["goal"].pop(key)
                    else:
                        doc["resourcePlans"][_SERVER]["goal"][key] = value
                    self.assertFalse(self.verdict(no_op_preview(), doc).has_changes)

    def test_dependencies_of_the_wrong_shape_are_a_change(self):
        other_stack = "urn:pulumi:other::branchleft-mail::hcloud:index/server:Server::x"
        other_project = "urn:pulumi:production::another::hcloud:index/server:Server::x"
        for value in (None, "x", {}, {"k": [_ROOT]}, [1], [None], [["u"]], ["not-a-urn"], [other_stack], [other_project], [f"{_ROOT}::x"]):
            for name in (_ROOT, _SERVER):
                with self.subTest(value=value, urn=name):
                    doc = observed_plan()
                    doc["resourcePlans"][name]["goal"]["dependencies"] = value
                    self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_ignore_changes_of_the_wrong_shape_are_a_change(self):
        for value in (None, "userData", {}, {"k": "v"}, [1], [None], [""], [["a"]], [True]):
            with self.subTest(value=value):
                doc = observed_plan()
                doc["resourcePlans"][_SERVER]["goal"]["ignoreChanges"] = value
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_structured_aliases_of_the_wrong_shape_are_a_change(self):
        good = {"URN": "", "Name": "old", "NoParent": False}
        values = (
            None,
            "alias",
            {},
            ["old"],
            [None],
            [{**good, "Extra": "x"}],
            [{**good, "Name": 5}],
            [{**good, "URN": None}],
            [{**good, "NoParent": "false"}],
            [{**good, "NoParent": 0}],
            [good, 3],
        )
        for value in values:
            with self.subTest(value=value):
                doc = observed_plan()
                doc["resourcePlans"][_SERVER]["goal"]["structuredAliases"] = value
                self.assertAsks(self.verdict(no_op_preview(), doc), "plan-shape")

    def test_a_listed_resource_whose_dependencies_differ_from_its_goal_is_a_change(self):
        # The "before" is the preview's stored state: a goal that names other dependencies than it holds.
        other = f"{_STACK}hcloud:index/firewall:Firewall::other"
        for state in ([], [other], [_ROOT, other], None):
            with self.subTest(state=state):
                preview_doc = listed_server()
                for side in ("oldState", "newState"):
                    if state is None:
                        preview_doc["steps"][1][side].pop("dependencies")
                    else:
                        preview_doc["steps"][1][side]["dependencies"] = state
                self.assertAsks(self.verdict(preview_doc, observed_plan()), "plan-shape")

    def test_a_listed_resource_whose_dependencies_changed_is_a_change(self):
        preview_doc = listed_server()
        preview_doc["steps"][1]["oldState"]["dependencies"] = []
        self.assertAsks(self.verdict(preview_doc, observed_plan()), "steps-changed")

    def test_dependencies_in_a_different_order_than_the_listed_state_are_no_change(self):
        extra = f"{_STACK}hcloud:index/firewall:Firewall::mail-firewall"
        preview_doc = listed_server()
        for side in ("oldState", "newState"):
            preview_doc["steps"][1][side]["dependencies"] = [_ROOT, extra]
        doc = observed_plan()
        doc["resourcePlans"][_SERVER]["goal"]["dependencies"] = [extra, _ROOT]
        self.assertFalse(self.verdict(preview_doc, doc).has_changes)

    def test_the_keys_do_not_hide_a_planned_change(self):
        # A resource that carries all four keys and plans an update, a diff or a delete still asks.
        for mutate in (
            lambda e: e.update({"steps": ["update"]}),
            lambda e: e["goal"].update({"inputDiff": {"updates": {"userData": "x"}}}),
            lambda e: e["goal"].update({"inputDiff": {"deletes": ["userData"]}}),
            lambda e: e["goal"].update({"outputDiff": {"adds": {"ip": "x"}}}),
        ):
            doc = observed_plan()
            doc["resourcePlans"][_SERVER]["goal"]["protect"] = True
            mutate(doc["resourcePlans"][_SERVER])
            self.assertTrue(self.verdict(no_op_preview(), doc).has_changes)

    def test_a_real_shaped_plan_with_a_planned_change_asks(self):
        preview_doc, plan_doc = self.real()
        sub = next(key for key in plan_doc["resourcePlans"] if key == _DEP_SUB)
        plan_doc["resourcePlans"][sub]["steps"] = ["replace"]
        self.assertAsks(self.verdict(preview_doc, plan_doc), "steps-changed")

    def test_a_real_shaped_preview_with_a_listed_create_asks(self):
        preview_doc, plan_doc = self.real()
        preview_doc["steps"].append({"op": "create", "urn": _DEP_NET})
        preview_doc["changeSummary"] = {"same": 3, "create": 1}
        self.assertAsks(self.verdict(preview_doc, plan_doc), "steps-changed")

    def test_the_other_keys_the_engine_can_write_are_still_refused(self):
        for key in ("aliases", "replaceOnChanges", "deleteBeforeReplace", "retainOnDelete", "deletedWith", "id", "additionalSecretOutputs"):
            with self.subTest(key=key):
                preview_doc, plan_doc = self.real()
                self.goal(plan_doc, "sub")[key] = []
                verdict = self.verdict(preview_doc, plan_doc)
                self.assertAsks(verdict, "plan-shape")
                self.assertEqual(verdict.detail, f"unrecognised goal key(s): {key}")

    def test_an_unknown_key_nested_inside_an_accepted_one_is_refused(self):
        preview_doc, plan_doc = self.real()
        self.goal(plan_doc, "sub")["structuredAliases"][0]["Hidden"] = "x"
        self.assertAsks(self.verdict(preview_doc, plan_doc), "plan-shape")


class StepsChangedDetailTests(VerdictCase):
    """A `steps-changed` verdict names what changed, by operation, state key and resource type: names only."""

    def test_a_listed_operation_is_named(self):
        doc = preview(("same", _ROOT), ("update", _SERVER), ("create", _FIREWALL), summary={"update": 1, "create": 1, "same": 1})
        verdict = self.verdict(doc, plan())
        self.assertAsks(verdict, "steps-changed")
        self.assertEqual(verdict.count, 2)
        self.assertEqual(verdict.detail, "step operation(s): create, update")

    def test_a_same_step_that_differs_names_its_keys_and_type_and_no_value_or_resource_name(self):
        preview_doc = listed_server()
        preview_doc["steps"][1]["oldState"]["protect"] = True
        preview_doc["steps"][1]["newState"]["inputs"] = {"k": MARKER}
        verdict = self.verdict(preview_doc, observed_plan())
        self.assertAsks(verdict, "steps-changed")
        self.assertEqual(verdict.count, 1)
        self.assertEqual(
            verdict.detail,
            "unchanged-looking step(s) differ on: inputs, protect; resource type(s): hcloud:index/server:Server",
        )
        for text in (MARKER, "mx1"):
            self.assertNotIn(text, verdict.detail)

    def test_a_diff_a_missing_provider_and_an_unfit_state_are_named(self):
        step = same_step(_SERVER, detailedDiff={"k": {"kind": "update"}})
        for side in ("oldState", "newState"):
            step[side]["custom"] = True
        verdict = self.verdict(preview_of(same_step(_ROOT), step), plan())
        self.assertAsks(verdict, "steps-changed")
        self.assertIn("detailedDiff", verdict.detail)
        self.assertIn("no-provider", verdict.detail)


_REF = f"{_STACK}pulumi:pulumi:StackReference::estate"
_PROVIDER = f"{_STACK}pulumi:providers:pulumi::default::{UNKNOWN}"


def stack_reference_read(**changes):
    """The read step of a StackReference, in the shape a real preview wrote for a throwaway
    local stack (fixtures/preview-real-shape-stack-reference-read.json): no old state,
    `external` true, `detailedDiff` null. A key given replaces the step's."""
    step = {
        "op": "read",
        "urn": _REF,
        "provider": _PROVIDER,
        "newState": {
            "urn": _REF,
            "custom": True,
            "id": "organization/estate/production",
            "type": "pulumi:pulumi:StackReference",
            "inputs": {"name": "organization/estate/production"},
            "parent": _ROOT,
            "external": True,
            "provider": _PROVIDER,
            "sourcePosition": "project:///a#5",
        },
        "detailedDiff": None,
    }
    step.update(changes)
    return step


def with_reads(*reads, summary=None):
    """A no-op preview that also lists the given read steps."""
    doc = preview(("same", _ROOT), summary=summary)
    doc["steps"].extend(reads)
    return doc


class StackReferenceReadTests(VerdictCase):
    """A StackReference is read on every run and changes nothing, so a plan whose only
    other step is that read has no changes. Any other read, or one that is not the real
    shape, still does (the sabotage test below breaks this guard and these go red)."""

    def test_the_real_shaped_fixture_is_no_change(self):
        preview_doc = json.loads((FIXTURES / "preview-real-shape-stack-reference-read.json").read_text(encoding="utf-8"))
        self.assertEqual([s["op"] for s in preview_doc["steps"]], ["same", "read"])
        plan_doc = json.loads((FIXTURES / "plan-real-shape-protected.json").read_text(encoding="utf-8"))
        verdict = self.verdict(preview_doc, plan_doc)
        self.assertFalse(verdict.has_changes, verdict)

    def test_a_stack_reference_read_is_no_change(self):
        self.assertFalse(self.verdict(with_reads(stack_reference_read()), plan()).has_changes)

    def test_two_reads_and_one_listed_in_the_summary_are_no_change(self):
        second = stack_reference_read(urn=f"{_STACK}pulumi:pulumi:StackReference::network")
        second["newState"]["urn"] = second["urn"]
        for summary in ({"same": 3}, {"same": 3, "read": 2}):
            with self.subTest(summary=summary):
                self.assertFalse(self.verdict(with_reads(stack_reference_read(), second, summary=summary), plan()).has_changes)

    def test_a_read_that_carries_an_old_state_of_its_type_is_no_change(self):
        step = stack_reference_read()
        step["oldState"] = {**step["newState"], "outputs": {"edge1": "192.0.2.1"}, "created": "t", "modified": "t"}
        self.assertFalse(self.verdict(with_reads(step), plan()).has_changes)

    def test_the_plan_may_list_the_read_resource_with_no_goal(self):
        saved = plan()
        saved["resourcePlans"][_REF] = {"steps": ["read"]}
        self.assertFalse(self.verdict(with_reads(stack_reference_read()), saved).has_changes)

    def test_a_plan_step_on_the_read_resource_that_is_not_a_read_is_a_change(self):
        saved = plan()
        saved["resourcePlans"][_REF] = {"steps": ["read", "update"]}
        self.assertAsks(self.verdict(with_reads(stack_reference_read()), saved), "steps-changed")

    def test_a_read_of_any_other_type_is_a_change_though_shaped_like_one(self):
        for urn in (_SERVER, _FIREWALL, f"{_STACK}pulumi:pulumi:Stack::estate", f"{_STACK}x:y:Component$pulumi:pulumi:StackReference::estate"):
            with self.subTest(urn=urn):
                step = stack_reference_read(urn=urn)
                step["newState"]["urn"] = urn
                verdict = self.verdict(with_reads(step), plan())
                self.assertTrue(verdict.has_changes, verdict)

    def test_a_read_that_is_not_the_real_shape_is_a_change(self):
        def mutate(change):
            step = stack_reference_read()
            change(step)
            return step

        cases = {
            "a diff": lambda s: s.update(detailedDiff={"name": {"kind": "update"}}),
            "diff reasons": lambda s: s.update(diffReasons=["name"]),
            "a replace reason": lambda s: s.update(replaceReasons=["name"]),
            "not external": lambda s: s["newState"].update(external=False),
            "not custom": lambda s: s["newState"].update(custom=False),
            "another state type": lambda s: s["newState"].update(type="hcloud:index/server:Server"),
            "an unknown state key": lambda s: s["newState"].update(price=1),
            "a taint": lambda s: s["newState"].update(taint=True),
            "a pending replacement": lambda s: s["newState"].update(pendingReplacement=True),
            "an old state of another type": lambda s: s.update(oldState={"type": "hcloud:index/server:Server"}),
            "an old state that is not an object": lambda s: s.update(oldState="x"),
            "no new state": lambda s: s.pop("newState"),
        }
        for label, change in cases.items():
            with self.subTest(label=label):
                verdict = self.verdict(with_reads(mutate(change)), plan())
                self.assertTrue(verdict.has_changes, verdict)

    def test_a_read_with_an_unknown_step_key_is_not_understood(self):
        self.assertAsks(self.verdict(with_reads(stack_reference_read(price=1)), plan()), "preview-shape")

    def test_a_summary_counting_more_reads_than_are_listed_is_a_change(self):
        doc = with_reads(stack_reference_read(), summary={"same": 3, "read": 2})
        self.assertAsks(self.verdict(doc, plan()), "summary-changed")

    def test_a_real_change_beside_a_clean_read_is_still_a_change(self):
        for op in ("create", "update", "replace", "delete"):
            with self.subTest(op=op):
                doc = with_reads(stack_reference_read(), summary={"same": 2, op: 1})
                doc["steps"].append(step_of(op, _SERVER))
                self.assertAsks(self.verdict(doc, plan()), "steps-changed")

    def test_a_listed_same_step_that_differs_beside_a_clean_read_is_still_a_change(self):
        doc = with_reads(stack_reference_read())
        doc["steps"][0]["newState"]["inputs"] = {"k": 1}
        self.assertAsks(self.verdict(doc, plan()), "steps-changed")


class QuietDiagnosticTests(VerdictCase):
    """A no-op preview that carries only warnings or notices is still a no-op; an `error`
    one (a preview that failed) and any diagnostic beside a listed change are not."""

    def test_warnings_and_notices_on_a_no_op_are_no_change(self):
        for severity in ("warning", "info", "info#err"):
            with self.subTest(severity=severity):
                doc = with_reads(stack_reference_read()) | {"diagnostics": [{"severity": severity, "message": "m"}]}
                self.assertFalse(self.verdict(doc, plan()).has_changes)

    def test_an_error_diagnostic_is_not_understood_and_names_its_severity(self):
        doc = no_op_preview() | {"diagnostics": [{"severity": "warning", "message": "m"}, {"severity": "error", "message": "boom"}]}
        verdict = self.verdict(doc, plan())
        self.assertAsks(verdict, "preview-diagnostics")
        self.assertEqual(verdict.detail, "severity: error, warning")
        self.assertNotIn("boom", verdict.detail)

    def test_a_diagnostic_that_is_not_the_shape_of_one_is_not_understood(self):
        for entry in ({"message": "m"}, {"severity": "warning"}, {"severity": "fatal", "message": "m"}, "warning", None, ["warning"]):
            with self.subTest(entry=entry):
                self.assertAsks(self.verdict(no_op_preview() | {"diagnostics": [entry]}, plan()), "preview-diagnostics")

    def test_a_warning_beside_a_listed_change_is_a_change(self):
        doc = preview(("same", _ROOT), ("update", _FIREWALL), summary={"same": 1, "update": 1}) | {
            "diagnostics": [{"severity": "warning", "message": "m"}]
        }
        self.assertAsks(self.verdict(doc, plan()), "steps-changed")


class SabotageTests(unittest.TestCase):
    """The controls are the StackReference read shape and the diagnostic severity check.
    Break each in a copy and the tests that keep a real change waiting must go red; the
    unmodified copy must be green. Skipped inside a broken copy's own run."""

    READ_CONTROL = "    if op != READ or urn.type != STACK_REFERENCE_TYPE:\n        return False\n"
    READ_BROKEN = "    if op != READ:\n        return False\n    return True\n"
    READ_MUST_FAIL = {
        "test_a_read_of_any_other_type_is_a_change_though_shaped_like_one",
        "test_a_read_that_is_not_the_real_shape_is_a_change",
        "test_a_read_with_an_unknown_step_key_is_not_understood",
    }
    # The loop that sets reads aside: every step that is not `same` is set aside.
    SKIP_CONTROL = "    reads = [(op, urn, entry) for op, urn, entry in listed if _is_clean_read(op, urn, entry)]\n"
    SKIP_BROKEN = "    reads = [(op, urn, entry) for op, urn, entry in listed if op != SAME]\n"
    SKIP_MUST_FAIL = {"test_a_real_change_beside_a_clean_read_is_still_a_change", "test_every_changing_op_is_a_change"}
    DIAGNOSTIC_CONTROL = "    if _diagnostics_are_quiet(diagnostics):\n        return\n"
    DIAGNOSTIC_BROKEN = "    return\n"
    DIAGNOSTIC_MUST_FAIL = {
        "test_an_error_diagnostic_is_not_understood_and_names_its_severity",
        "test_a_diagnostic_that_is_not_the_shape_of_one_is_not_understood",
        "test_diagnostics_that_are_not_empty",
    }

    def run_copy(self, control, broken):
        here = pathlib.Path(__file__).resolve().parent
        with tempfile.TemporaryDirectory(prefix="plan-has-changes-sabotage-") as tmp:
            copy = pathlib.Path(tmp, ".github", "actions", "plan-has-changes")
            shutil.copytree(here, copy, ignore=shutil.ignore_patterns("__pycache__"))
            script = copy / "plan-has-changes.py"
            if control:
                text = script.read_text(encoding="utf-8")
                self.assertEqual(text.count(control), 1, "the control moved: update the sabotage")
                script.write_text(text.replace(control, broken), encoding="utf-8")
            env = {**os.environ, SCRIPT_ENV: str(script)}
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(copy), "-p", "test_plan_has_changes.py", "-v"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

    def assert_red(self, control, broken, must_fail):
        red = self.run_copy(control, broken)
        self.assertNotEqual(red.returncode, 0, red.stderr)
        failed = set(re.findall(r"^(?:FAIL|ERROR): (\w+) ", red.stderr, re.MULTILINE))
        self.assertTrue(must_fail <= failed, f"expected red, got {sorted(failed)}")

    @unittest.skipIf(os.environ.get(SCRIPT_ENV), "running inside a sabotaged copy")
    def test_a_read_guard_that_passes_every_read_turns_the_change_tests_red(self):
        self.assert_red(self.READ_CONTROL, self.READ_BROKEN, self.READ_MUST_FAIL)

    @unittest.skipIf(os.environ.get(SCRIPT_ENV), "running inside a sabotaged copy")
    def test_a_parser_that_sets_every_change_aside_as_a_read_turns_the_change_tests_red(self):
        self.assert_red(self.SKIP_CONTROL, self.SKIP_BROKEN, self.SKIP_MUST_FAIL)

    @unittest.skipIf(os.environ.get(SCRIPT_ENV), "running inside a sabotaged copy")
    def test_a_diagnostic_check_that_passes_every_diagnostic_turns_the_error_tests_red(self):
        self.assert_red(self.DIAGNOSTIC_CONTROL, self.DIAGNOSTIC_BROKEN, self.DIAGNOSTIC_MUST_FAIL)

    @unittest.skipIf(os.environ.get(SCRIPT_ENV), "running inside a sabotaged copy")
    def test_the_unmodified_copy_is_green(self):
        green = self.run_copy("", "")
        self.assertEqual(green.returncode, 0, green.stderr)


if __name__ == "__main__":
    unittest.main()
