#!/usr/bin/env python3
"""Tests for check-apply-gate: every way a caller can break the contract must be
named, and the callers that keep it must pass. Each failing case is a one-edit
mutation of a passing fixture, so a case that stops changing its text is itself
a failure."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SUMMARY = "Write the plan to the job summary"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_apply_gate", HERE / "check-apply-gate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_module()


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def findings(text: str, plan="plan", apply="apply", verdict="noop", summary=None):
    return gate.check_pair(gate.job_blocks(text), plan, apply, verdict, summary)


def codes(found) -> set[str]:
    return {item.code for item in found}


class PassingCallers(unittest.TestCase):
    def test_a_caller_that_keeps_the_contract_passes(self):
        self.assertEqual(findings(read("gate-pass.yml"), summary=SUMMARY), [])

    def test_a_caller_that_combines_the_verdict_with_another_check_passes(self):
        self.assertEqual(findings(read("gate-pass-combined.yml"), apply="deploy"), [])

    def test_every_spelling_of_needs_and_if_is_read(self):
        text = read("gate-pass.yml")
        for old, new in (
            ("needs: [plan]", "needs: plan"),
            ("needs: [plan]", "needs:\n      - plan"),
            ("needs: [plan]", "needs: [setup, plan]"),
        ):
            with self.subTest(new=new):
                self.assertIn(old, text)
                self.assertEqual(findings(text.replace(old, new)), [])
        wrapped = text.replace(
            "if: >-\n      github.event_name", "if: ${{ github.event_name"
        ).replace("!= 'false'\n    steps:\n      - name: Apply", "!= 'false' }}\n    steps:\n      - name: Apply")
        self.assertIn("${{ github.event_name", wrapped)
        self.assertEqual(findings(wrapped), [])

    def test_two_pairs_in_one_workflow_are_each_checked(self):
        text = read("gate-pass.yml")
        second = text.split("jobs:\n", 1)[1].replace("plan:", "plan2:").replace("apply:", "apply2:")
        second = second.replace("needs: [plan]", "needs: [plan2]").replace("needs.plan.outputs", "needs.plan2.outputs")
        second = second.replace("id: noop", "id: noop2").replace("steps.noop.", "steps.noop2.")
        jobs = gate.job_blocks(text + second)
        self.assertEqual(gate.check_pair(jobs, "plan2", "apply2", "noop2", None), [])
        self.assertEqual(gate.check_pair(jobs, "plan", "apply", "noop", None), [])


# (what is broken, the text it replaces, its replacement, the code that must be named)
BROKEN = (
    ("the apply condition tests == 'true'", "has_changes != 'false'", "has_changes == 'true'", "GATE-IF-OPERAND"),
    ("an or starts the job on its own", "&&\n      needs.plan", "||\n      needs.plan", "GATE-IF-OR"),
    ("the operand is negated", "needs.plan.outputs.has_changes != 'false'", "!(needs.plan.outputs.has_changes != 'false')", "GATE-IF-OPERAND"),
    ("the operand names another job", "needs.plan.outputs", "needs.other.outputs", "GATE-IF-OPERAND"),
    ("the operand is ORed in a group", "github.ref == 'refs/heads/main' &&", "(github.ref == 'refs/heads/main' || true) &&", "GATE-IF-OR"),
    ("always() lets a failed plan start the apply", "github.event_name == 'push' &&", "always() && github.event_name == 'push' &&", "GATE-IF-STATUS"),
    ("failure() is a status function too", "github.event_name == 'push' &&", "failure() && github.event_name == 'push' &&", "GATE-IF-STATUS"),
    ("the apply job does not need the plan", "needs: [plan]", "needs: [setup]", "GATE-NEEDS"),
    ("the apply job needs nothing", "    needs: [plan]\n", "", "GATE-NEEDS"),
    (
        "the apply condition is gone",
        "    if: >-\n      github.event_name == 'push' && github.ref == 'refs/heads/main' &&\n      needs.plan.outputs.has_changes != 'false'\n",
        "",
        "GATE-IF-MISSING",
    ),
    ("the plan job publishes another step", "steps.noop.outputs.has_changes", "steps.other.outputs.has_changes", "GATE-OUTPUT"),
    ("the plan job publishes a constant", "${{ steps.noop.outputs.has_changes }}", "'false'", "GATE-OUTPUT"),
    ("the plan job publishes nothing", "    outputs:\n      has_changes: ${{ steps.noop.outputs.has_changes }}\n", "", "GATE-OUTPUT"),
    ("the verdict step can be skipped", "        id: noop\n        if: always()\n", "        id: noop\n", "GATE-VERDICT-ALWAYS"),
    ("the verdict step skips on a condition", "        id: noop\n        if: always()\n", "        id: noop\n        if: success()\n", "GATE-VERDICT-ALWAYS"),
    ("the verdict step has another id", "        id: noop\n", "        id: renamed\n", "GATE-VERDICT-STEP"),
    (
        "the plan job runs another repo's action",
        "uses: branchLeft/github-workflows/.github/actions/plan-has-changes@",
        "uses: someone/else/.github/actions/plan-has-changes@",
        "GATE-ACTION",
    ),
    (
        "the plan job calls the action twice",
        "      - name: Write the plan to the job summary\n",
        "      - name: Again\n        uses: branchLeft/github-workflows/.github/actions/plan-has-changes@0123456789abcdef0123456789abcdef01234567 # v1.0.10\n      - name: Write the plan to the job summary\n",
        "GATE-ACTION",
    ),
)


class BrokenCallers(unittest.TestCase):
    def test_each_break_is_named(self):
        text = read("gate-pass.yml")
        for what, old, new, code in BROKEN:
            with self.subTest(what=what):
                self.assertIn(old, text, "the case no longer changes the fixture")
                self.assertIn(code, codes(findings(text.replace(old, new, 1))))

    def test_the_summary_written_before_the_verdict_is_named(self):
        text = read("gate-pass.yml")
        self.assertEqual(codes(findings(text, summary="Checkout")), {"GATE-ORDER"})
        self.assertEqual(codes(findings(text, summary="No such step")), {"GATE-ORDER"})

    def test_a_missing_job_is_named(self):
        text = read("gate-pass.yml")
        self.assertEqual(codes(findings(text, plan="nope")), {"GATE-NO-JOB"})
        self.assertEqual(codes(findings(text, apply="nope")), {"GATE-NO-JOB"})

    def test_a_combining_step_that_ignores_the_action_is_named(self):
        text = read("gate-pass-combined.yml")
        old = "steps.plan_verdict.outputs.has_changes"
        self.assertIn(old, text)
        self.assertEqual(codes(findings(text.replace(old, "steps.other.outputs.has_changes"), apply="deploy")), {"GATE-VERDICT-SOURCE"})

    def test_a_workflow_with_two_jobs_of_one_name_is_unreadable(self):
        text = read("gate-pass.yml") + "  plan:\n    runs-on: ubuntu-latest\n"
        with self.assertRaises(gate.Unreadable):
            gate.job_blocks(text)

    def test_a_workflow_with_no_jobs_is_unreadable(self):
        with self.assertRaises(gate.Unreadable):
            gate.job_blocks("name: nothing\n")

    def test_a_quoted_or_is_not_an_or(self):
        text = read("gate-pass.yml").replace("refs/heads/main'", "refs/heads/a||b'", 1)
        self.assertEqual(findings(text), [])


class Command(unittest.TestCase):
    def run_main(self, text, *args):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as handle:
            handle.write(text)
        path = pathlib.Path(handle.name)
        self.addCleanup(path.unlink)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = gate.main(["check-apply-gate.py", "--workflow", str(path), "--verdict-step", "noop", *args])
        return status, out.getvalue()

    def test_a_passing_workflow_exits_zero(self):
        status, out = self.run_main(read("gate-pass.yml"), "--pairs", "plan:apply", "--summary-step", SUMMARY)
        self.assertEqual(status, 0, out)
        self.assertIn("ok:", out)

    def test_a_broken_workflow_exits_one_and_names_the_code_as_an_annotation(self):
        status, out = self.run_main(read("gate-fail-or.yml"), "--pairs", "plan:apply")
        self.assertEqual(status, 1)
        self.assertIn("::error file=", out)
        self.assertIn("GATE-IF-OR apply:", out)

    def test_every_pair_is_reported_not_just_the_first(self):
        status, out = self.run_main(read("gate-fail-or.yml"), "--pairs", "plan:apply nope:apply")
        self.assertEqual(status, 1)
        self.assertIn("GATE-IF-OR", out)
        self.assertIn("GATE-NO-JOB nope", out)

    def test_a_pair_that_is_not_plan_colon_apply_fails(self):
        for pairs in ("plan", "plan:", ":apply", "a:b:c", " "):
            with self.subTest(pairs=pairs):
                status, out = self.run_main(read("gate-pass.yml"), "--pairs", pairs)
                self.assertEqual(status, 1)
                self.assertIn("GATE-UNREADABLE", out)

    def test_a_missing_workflow_file_fails(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = gate.main(
                ["check-apply-gate.py", "--workflow", "/nonexistent/x.yml", "--pairs", "plan:apply", "--verdict-step", "noop"]
            )
        self.assertEqual(status, 1)
        self.assertIn("GATE-UNREADABLE", out.getvalue())

    def test_the_script_imports_on_python_3_9(self):
        # `X | None` annotations fail at import on 3.9 without this line.
        source = (HERE / "check-apply-gate.py").read_text(encoding="utf-8")
        self.assertIn("from __future__ import annotations", source)


if __name__ == "__main__":
    unittest.main()
