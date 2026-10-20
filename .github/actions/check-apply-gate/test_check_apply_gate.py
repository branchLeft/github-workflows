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
import time
import unittest

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SUMMARY = "Write the plan to the job summary"
PLAN_FILE = "preview-plan.json"


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
        second = second.replace("apply-gate:", "apply-gate2:")
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


def plan_findings(text: str, plan_jobs=("plan",), plan_file=PLAN_FILE):
    return gate.check_plan_file(gate.job_blocks(text), list(plan_jobs), plan_file, gate.DEFAULT_SAVE_STEP)


SAVE_STEP = "      - name: Save the update plan for the no-change check\n"
SAVE_RUN = "        run: pulumi preview --stack production --save-plan preview-plan.json > /dev/null\n"
SUMMARY_STEP = "      - name: Write the plan to the job summary\n"


def before_summary(step: str) -> tuple[str, str]:
    """The text and replacement that add a whole step to the plan job, ahead of the summary step."""
    return SUMMARY_STEP, step + SUMMARY_STEP


# (what is broken, the text it replaces, its replacement, the code that must be named)
PLAN_BROKEN = (
    ("the save step may fail the job", "        continue-on-error: true\n" + SAVE_RUN, SAVE_RUN, "GATE-PLAN-SAVE"),
    ("the save step lets stdout through", " > /dev/null\n", "\n", "GATE-PLAN-SAVE"),
    ("the save step writes another file", "--save-plan preview-plan.json", "--save-plan other.json", "GATE-PLAN-SAVE"),
    ("the save step is not the named step", SAVE_STEP, "      - name: Keep the plan\n", "GATE-PLAN-SAVE"),
    (
        "a step prints the saved plan",
        *before_summary("      - name: Show it\n        run: cat preview-plan.json\n"),
        "GATE-PLAN-READ",
    ),
    (
        "a step uploads",
        *before_summary("      - name: Keep it\n        uses: actions/upload-artifact@abc # v4\n"),
        "GATE-PLAN-READ",
    ),
    (
        "a step globs over the JSON files",
        *before_summary("      - name: Show them\n        run: cat *.json\n"),
        "GATE-PLAN-READ",
    ),
    (
        "a second step saves a plan",
        *before_summary("      - name: Save again\n        run: pulumi preview --save-plan again.json\n"),
        "GATE-PLAN-READ",
    ),
    ("the action reads another file", "plan-file: preview-plan.json", "plan-file: other.json", "GATE-PLAN-INPUT"),
    ("the action is given no plan file", "          plan-file: preview-plan.json\n", "", "GATE-PLAN-INPUT"),
    ("a job that is not a plan job names it", "        run: echo apply\n", "        run: cat preview-plan.json\n", "GATE-PLAN-READ"),
    (
        "a step names the plan file without its extension",
        *before_summary("      - name: Show it\n        run: cat preview-plan\n"),
        "GATE-PLAN-READ",
    ),
    (
        "a step names the plan file with another extension",
        *before_summary("      - name: Show it\n        run: cat preview-plan.js\n"),
        "GATE-PLAN-READ",
    ),
    (
        "a step globs on the plan file's stem",
        *before_summary("      - name: Show it\n        run: cat preview-plan*\n"),
        "GATE-PLAN-READ",
    ),
    ("the save step sends only stderr to /dev/null", " > /dev/null\n", " 2> /dev/null\n", "GATE-PLAN-SAVE"),
    ("a later command sends to /dev/null instead", "preview-plan.json > /dev/null\n", "preview-plan.json; echo done > /dev/null\n", "GATE-PLAN-SAVE"),
    ("something follows the redirect", "> /dev/null\n", "> /dev/null; cat preview-plan.json\n", "GATE-PLAN-SAVE"),
    (
        "the save step prints the file on a later line",
        SAVE_RUN,
        "        run: |\n          pulumi preview --stack production --save-plan preview-plan.json > /dev/null\n          cat preview-plan.json\n",
        "GATE-PLAN-SAVE",
    ),
    (
        "the save step saves a second plan",
        SAVE_RUN,
        "        run: |\n          pulumi preview --save-plan again.json\n          pulumi preview --stack production --save-plan preview-plan.json > /dev/null\n",
        "GATE-PLAN-SAVE",
    ),
    (
        "the check job has a step that prints the plan",
        "      - name: Check the apply gate\n",
        "      - name: Show it\n        run: cat preview-plan.json\n      - name: Check the apply gate\n",
        "GATE-PLAN-READ",
    ),
    (
        "the check job runs another repo's check",
        "uses: branchLeft/github-workflows/.github/actions/check-apply-gate@",
        "uses: someone/else/.github/actions/check-apply-gate@",
        "GATE-PLAN-READ",
    ),
    (
        "the check job sets the plan file in its own env",
        "    timeout-minutes: 5\n",
        "    timeout-minutes: 5\n    env:\n      PLAN: preview-plan.json\n",
        "GATE-PLAN-READ",
    ),
)


class PlanFile(unittest.TestCase):
    def test_a_plan_job_that_keeps_the_plan_to_itself_passes(self):
        self.assertEqual(plan_findings(read("gate-pass.yml")), [])
        self.assertEqual(plan_findings(read("gate-pass-combined.yml")), [])

    def test_each_way_to_expose_the_plan_is_named(self):
        text = read("gate-pass.yml")
        for what, old, new, code in PLAN_BROKEN:
            with self.subTest(what=what):
                self.assertIn(old, text, "the case no longer changes the fixture")
                self.assertIn(code, codes(plan_findings(text.replace(old, new, 1))))

    def test_a_comment_that_mentions_the_plan_is_not_a_read(self):
        text = read("gate-pass.yml").replace(
            SUMMARY_STEP, "      - name: Note\n        run: |\n          # preview-plan.json is saved above\n          echo noted\n" + SUMMARY_STEP, 1
        )
        self.assertEqual(plan_findings(text), [])

    def test_the_fixture_that_prints_the_plan_fails(self):
        self.assertEqual(codes(plan_findings(read("gate-fail-plan-read.yml"))), {"GATE-PLAN-READ"})

    def test_the_job_that_runs_the_check_may_name_the_plan_file(self):
        for name in ("gate-pass.yml", "gate-pass-combined.yml"):
            with self.subTest(fixture=name):
                text = read(name)
                self.assertIn("check-apply-gate@", text)
                self.assertIn("plan-file: preview-plan.json", text.split("check-apply-gate@", 1)[1])
                self.assertEqual(plan_findings(text), [])


class Experimental(unittest.TestCase):
    def test_a_workflow_that_never_sets_it_passes(self):
        for name in ("gate-pass.yml", "gate-pass-combined.yml"):
            self.assertEqual(gate.check_experimental(read(name)), [])

    def test_a_comment_is_not_a_setting(self):
        self.assertEqual(gate.check_experimental(read("gate-pass.yml") + "# PULUMI_EXPERIMENTAL is not set\n"), [])

    def test_any_mention_in_the_workflow_is_named(self):
        text = read("gate-pass.yml").replace(
            "    timeout-minutes: 10\n", "    timeout-minutes: 10\n    env:\n      PULUMI_EXPERIMENTAL: 'true'\n", 1
        )
        self.assertIn("PULUMI_EXPERIMENTAL", text)
        self.assertEqual(codes(gate.check_experimental(text)), {"GATE-PLAN-EXPERIMENTAL"})


class ShowSecrets(unittest.TestCase):
    def test_a_workflow_that_never_uses_it_passes(self):
        for name in ("gate-pass.yml", "gate-pass-combined.yml"):
            self.assertEqual(gate.check_show_secrets(read(name)), [])

    def test_a_comment_is_not_a_use(self):
        self.assertEqual(gate.check_show_secrets(read("gate-pass.yml") + "# never --show-secrets\n"), [])

    def test_the_flag_on_the_save_command_is_named(self):
        old = "pulumi preview --stack production --save-plan"
        text = read("gate-pass.yml").replace(old, "pulumi preview --show-secrets --stack production --save-plan", 1)
        self.assertIn("--show-secrets", text)
        self.assertEqual(codes(gate.check_show_secrets(text)), {"GATE-PLAN-SHOW-SECRETS"})


class VerdictSource(unittest.TestCase):
    ENV = "          PLAN_HAS_CHANGES: ${{ steps.plan_verdict.outputs.has_changes }}\n"

    def broken(self, old, new):
        text = read("gate-pass-combined.yml")
        self.assertIn(old, text)
        return codes(findings(text.replace(old, new, 1), apply="deploy"))

    def test_a_comment_that_mentions_the_output_is_not_a_source(self):
        self.assertEqual(self.broken(self.ENV, "          # " + self.ENV.strip() + "\n"), {"GATE-VERDICT-SOURCE"})

    def test_an_env_binding_that_is_never_read_is_not_a_source(self):
        self.assertEqual(self.broken('"$PLAN_HAS_CHANGES"', '"$OTHER"'), {"GATE-VERDICT-SOURCE"})

    def test_a_variable_that_is_not_bound_from_the_output_is_not_a_source(self):
        self.assertEqual(self.broken(self.ENV, ""), {"GATE-VERDICT-SOURCE"})

    def test_a_binding_outside_env_is_not_a_source(self):
        # The same line under `with:` or in another step's env is not this step's env.
        self.assertEqual(self.broken("        env:\n" + self.ENV, "        with:\n" + self.ENV), {"GATE-VERDICT-SOURCE"})
        self.assertEqual(
            self.broken("        env:\n" + self.ENV, "        env:\n          OTHER: x\n        with:\n" + self.ENV),
            {"GATE-VERDICT-SOURCE"},
        )

    def test_the_variable_may_be_read_with_braces(self):
        text = read("gate-pass-combined.yml").replace('"$PLAN_HAS_CHANGES"', '"${PLAN_HAS_CHANGES}"')
        self.assertEqual(findings(text, apply="deploy"), [])


class LongRunsOfSpaces(unittest.TestCase):
    def test_a_comment_is_stripped_and_a_hash_without_a_space_is_not_a_comment(self):
        for value, want in (("a # b", "a"), ("a\t# b", "a"), ("a#b", "a#b"), ("a#b # c", "a#b"), ("  a  ", "a"), ("# c", "# c"), ("", "")):
            with self.subTest(value=value):
                self.assertEqual(gate._strip_comment(value), want)

    def test_a_very_long_run_of_spaces_costs_one_pass(self):
        started = time.monotonic()
        self.assertEqual(gate._strip_comment("a" + " " * 100_000 + "# c"), "a")
        self.assertEqual(gate._strip_comment("a" + " " * 100_000 + "b"), "a" + " " * 100_000 + "b")
        padded = read("gate-pass.yml").replace("needs: [plan]", "needs: [plan]" + " " * 100_000 + "# c", 1)
        self.assertEqual(findings(padded), [])
        self.assertLess(time.monotonic() - started, 2)


class Command(unittest.TestCase):
    def run_main(self, text, *args):
        with tempfile.NamedTemporaryFile("w", suffix=".yml", delete=False, encoding="utf-8") as handle:
            handle.write(text)
        path = pathlib.Path(handle.name)
        self.addCleanup(path.unlink)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            status = gate.main(
                ["check-apply-gate.py", "--workflow", str(path), "--verdict-step", "noop", "--plan-file", PLAN_FILE, *args]
            )
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
                [
                    "check-apply-gate.py",
                    "--workflow",
                    "/nonexistent/x.yml",
                    "--pairs",
                    "plan:apply",
                    "--verdict-step",
                    "noop",
                    "--plan-file",
                    PLAN_FILE,
                ]
            )
        self.assertEqual(status, 1)
        self.assertIn("GATE-UNREADABLE", out.getvalue())

    def test_the_script_imports_on_python_3_9(self):
        # `X | None` annotations fail at import on 3.9 without this line.
        source = (HERE / "check-apply-gate.py").read_text(encoding="utf-8")
        self.assertIn("from __future__ import annotations", source)


if __name__ == "__main__":
    unittest.main()
