#!/usr/bin/env python3
"""Tests for check-verify-job-hardened. Every forbidden form is a one-edit mutation of a
passing fixture and must be named; every harmless look-alike is a one-edit mutation that
must stay green; and the Sabotage class breaks each control in a copy of the program and
requires its tests to go red there. A case that stops changing its text is itself a failure."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from typing import ClassVar

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SCRIPT_NAME = "check-verify-job-hardened.py"
INSIDE_SABOTAGE = "CHECK_VERIFY_JOB_HARDENED_SABOTAGE"
ACTION = "branchLeft/github-workflows/.github/actions/verify-package-attestations"
VERIFY_STEP = f"      - name: Verify package attestations\n        uses: {ACTION}@2c34b90b367fd160bb01d311d29224d250460086 # v1.2.1\n"
GRANTS = "    permissions:\n      contents: read\n      packages: read\n"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_verify_job_hardened", HERE / SCRIPT_NAME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_module()


def read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def findings(text: str, wanted=()):
    """The findings for a workflow text; raises Unreadable when it cannot be read."""
    return checker.check_workflow(checker.Parser(text).document(), list(wanted))


def codes(found) -> set[str]:
    return {item.code for item in found}


def mutate(text: str, old: str, new: str) -> str:
    assert old in text, f"the case no longer changes the fixture: {old!r}"
    return text.replace(old, new, 1)


DEDICATED = read("verify-pass-dedicated.yml")
IN_CI = read("verify-pass-in-ci.yml")
JOB_HEAD = "    runs-on: ubuntu-latest\n    timeout-minutes: 10\n"
CHECKOUT = "      - name: Checkout\n"


def in_job(extra: str) -> tuple[str, str, str]:
    """A mutation of the dedicated fixture that adds job-level lines."""
    return DEDICATED, JOB_HEAD, JOB_HEAD + extra


def on_verify_step(extra: str) -> tuple[str, str, str]:
    """A mutation of the dedicated fixture that adds step-level lines to the verify step."""
    head = "      - name: Verify package attestations\n"
    return DEDICATED, head, head + extra


class PassingWorkflows(unittest.TestCase):
    def test_a_dedicated_workflow_that_keeps_the_contract_passes(self):
        self.assertEqual(findings(DEDICATED), [])

    def test_a_verify_job_among_other_jobs_passes_whatever_the_others_do(self):
        self.assertEqual(findings(IN_CI), [])

    def test_a_sequence_level_with_its_key_and_a_dash_with_extra_spaces_are_read(self):
        text = (
            "jobs:\n"
            "  verify:\n"
            "    permissions:\n"
            "      contents: read\n"
            "      packages: read\n"
            "    steps:\n"
            "    -   name: Checkout\n"
            "        uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1\n"
            "    - name: Verify\n"
            f"      uses: {ACTION}@2c34b90b367fd160bb01d311d29224d250460086\n"
        )
        self.assertEqual(findings(text), [])
        self.assertEqual(codes(findings(mutate(text, "      uses: " + ACTION, "      if: false\n      uses: " + ACTION))), {"VERIFY-IF-STEP"})

    def test_the_named_job_is_accepted_when_it_calls_the_action(self):
        self.assertEqual(findings(DEDICATED, ["verify"]), [])
        self.assertEqual(findings(IN_CI, ["verify-package-attestations"]), [])


# (what, fixture, old, new): harmless look-alikes that must not fail.
HARMLESS = (
    ("a literal false on the job", *in_job("    continue-on-error: false\n")),
    ("a capitalised False on the job", *in_job("    continue-on-error: False\n")),
    ("a literal false on the verify step", *on_verify_step("        continue-on-error: false\n")),
    ("an if on another step of the job", DEDICATED, CHECKOUT, CHECKOUT + "        if: github.event_name == 'push'\n"),
    ("a continue-on-error on a step of another job", IN_CI, "        run: echo deploy\n", "        run: echo deploy\n        continue-on-error: true\n"),
    ("the forbidden words in a run block", DEDICATED, VERIFY_STEP, VERIFY_STEP + "\n      - name: Say it\n        run: |\n          echo 'continue-on-error: true'\n          echo 'if: false'\n"),
    ("the forbidden words in a step name", DEDICATED, CHECKOUT, '      - name: "if: false and continue-on-error: true"\n'),
    ("a comment on the job line", *in_job("    # continue-on-error: true\n    # if: false\n")),
    ("a trailing comment after a permission", DEDICATED, "      packages: read\n", "      packages: read # the tarball download\n"),
    ("a trailing comment after a literal false", *in_job("    continue-on-error: false # never true\n")),
    ("an input named if under with", *on_verify_step("        with:\n          if: x\n          root: .\n")),
    ("an env variable named like the key", *in_job("    env:\n      continue-on-error: 'true'\n      if: 'false'\n")),
    ("the same permissions as a flow mapping", DEDICATED, GRANTS, "    permissions: { contents: read, packages: read }\n"),
    ("the permissions in the other order", DEDICATED, GRANTS, "    permissions:\n      packages: read\n      contents: read\n"),
    ("quoted permission values", DEDICATED, GRANTS, '    permissions:\n      contents: "read"\n      packages: \'read\'\n'),
    ("the owner and repo in another letter case", DEDICATED, ACTION, ACTION.replace("branchLeft/github-workflows", "BRANCHLEFT/GitHub-Workflows")),
    ("the action pinned to a tag", DEDICATED, "@2c34b90b367fd160bb01d311d29224d250460086 # v1.2.1", "@v1.2.1"),
    ("a job that needs one with no condition", DEDICATED, JOB_HEAD, JOB_HEAD + "    needs: other\n"),
    ("a document start marker", DEDICATED, "name: Fixture", "---\nname: Fixture"),
    ("a job after the verify job that is conditional", DEDICATED, VERIFY_STEP, VERIFY_STEP + "\n  later:\n    runs-on: ubuntu-latest\n    if: false\n    continue-on-error: true\n    steps:\n      - run: echo later\n"),
)


class HarmlessLookAlikes(unittest.TestCase):
    def test_each_look_alike_stays_green(self):
        for what, text, old, new in HARMLESS:
            with self.subTest(what=what):
                broken = mutate(text, old, new)
                if "needs: other" in broken:
                    broken += "\n  other:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo other\n"
                self.assertEqual(findings(broken), [])


# (what, fixture, old, new, code): each is one edit that must be named.
CONTINUE_ON_ERROR = (
    ("true on the job", *in_job("    continue-on-error: true\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("an expression on the job", *in_job("    continue-on-error: ${{ true }}\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("an expression that is sometimes true", *in_job("    continue-on-error: ${{ github.event_name == 'push' }}\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("a quoted false string on the job", *in_job('    continue-on-error: "false"\n'), "VERIFY-CONTINUE-ON-ERROR"),
    ("a yes on the job", *in_job("    continue-on-error: yes\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("an empty value on the job", *in_job("    continue-on-error:\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("a block scalar on the job", *in_job("    continue-on-error: >-\n      true\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("true with a comment that says false", *in_job("    continue-on-error: true # false\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("a quoted key on the job", *in_job('    "continue-on-error": true\n'), "VERIFY-CONTINUE-ON-ERROR"),
    ("a single-quoted key on the job", *in_job("    'continue-on-error': true\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("another letter case on the job", *in_job("    Continue-On-Error: true\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("true on the verify step", *on_verify_step("        continue-on-error: true\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("an expression on the verify step", *on_verify_step("        continue-on-error: ${{ always() }}\n"), "VERIFY-CONTINUE-ON-ERROR"),
    ("true on the checkout step", DEDICATED, CHECKOUT, CHECKOUT + "        continue-on-error: true\n", "VERIFY-CONTINUE-ON-ERROR"),
    ("true on the step in the other layout", IN_CI, "        continue-on-error: False\n", "        continue-on-error: true\n", "VERIFY-CONTINUE-ON-ERROR"),
)

IF_FORMS = (
    ("false on the job", *in_job("    if: false\n"), "VERIFY-IF-JOB"),
    ("true on the job", *in_job("    if: true\n"), "VERIFY-IF-JOB"),
    ("an expression on the job", *in_job("    if: ${{ always() }}\n"), "VERIFY-IF-JOB"),
    ("a quoted key on the job", *in_job('    "if": false\n'), "VERIFY-IF-JOB"),
    ("another letter case on the job", *in_job("    If: false\n"), "VERIFY-IF-JOB"),
    ("a folded block on the job", *in_job("    if: >-\n      github.actor != 'someone'\n"), "VERIFY-IF-JOB"),
    ("a condition on the verify step", *on_verify_step("        if: github.event_name == 'push'\n"), "VERIFY-IF-STEP"),
    ("false on the verify step", *on_verify_step("        if: false\n"), "VERIFY-IF-STEP"),
    ("a condition across two lines on the verify step", *on_verify_step("        if: github.event_name == 'push' &&\n          github.ref == 'refs/heads/main'\n"), "VERIFY-IF-STEP"),
    ("an expression on the verify step", *on_verify_step("        if: ${{ false }}\n"), "VERIFY-IF-STEP"),
    ("a quoted key on the verify step", *on_verify_step('        "if": false\n'), "VERIFY-IF-STEP"),
    ("a condition on the step in the other layout", IN_CI, "        continue-on-error: false\n        uses: " + ACTION, "        continue-on-error: false\n        if: false\n        uses: " + ACTION, "VERIFY-IF-STEP"),
    ("a condition on the job in the other layout", IN_CI, "    needs: [lint-free]\n", "    needs: [lint-free]\n    if: false\n", "VERIFY-IF-JOB"),
)

NEEDS = (
    ("a needed job with a condition", IN_CI, "needs: [lint-free]", "needs: [lint]", "VERIFY-NEEDS-IF"),
    ("a needed job named as a scalar", IN_CI, "needs: [lint-free]", "needs: lint", "VERIFY-NEEDS-IF"),
    ("a needed job in a block list", IN_CI, "    needs: [lint-free]\n", "    needs:\n      - lint-free\n      - lint\n", "VERIFY-NEEDS-IF"),
    ("a needed job that gains a condition", IN_CI, "  lint-free:\n    name: A job with no condition\n", "  lint-free:\n    name: A job with no condition\n    if: github.event_name == 'push'\n", "VERIFY-NEEDS-IF"),
    ("a job two steps up the chain with a condition", IN_CI, "  lint-free:\n    name: A job with no condition\n", "  lint-free:\n    name: A job with no condition\n    needs: lint\n", "VERIFY-NEEDS-IF"),
)

PERMISSIONS = (
    ("a third grant", DEDICATED, "      packages: read\n", "      packages: read\n      actions: read\n", "VERIFY-PERMISSIONS"),
    ("packages write", DEDICATED, "packages: read", "packages: write", "VERIFY-PERMISSIONS"),
    ("contents write", DEDICATED, "contents: read\n      packages", "contents: write\n      packages", "VERIFY-PERMISSIONS"),
    ("an id-token grant", DEDICATED, "      packages: read\n", "      packages: read\n      id-token: write\n", "VERIFY-PERMISSIONS"),
    ("no packages grant", DEDICATED, "      packages: read\n", "", "VERIFY-PERMISSIONS"),
    ("no contents grant", DEDICATED, "      contents: read\n      packages: read\n", "      packages: read\n", "VERIFY-PERMISSIONS"),
    ("no block, the workflow level is not enough", DEDICATED, GRANTS, "", "VERIFY-PERMISSIONS"),
    ("read-all", DEDICATED, GRANTS, "    permissions: read-all\n", "VERIFY-PERMISSIONS"),
    ("write-all", DEDICATED, GRANTS, "    permissions: write-all\n", "VERIFY-PERMISSIONS"),
    ("an empty flow mapping", DEDICATED, GRANTS, "    permissions: {}\n", "VERIFY-PERMISSIONS"),
    ("a flow mapping with an extra grant", DEDICATED, GRANTS, "    permissions: { contents: read, packages: read, id-token: write }\n", "VERIFY-PERMISSIONS"),
    ("a flow mapping with a write grant", DEDICATED, GRANTS, "    permissions: { contents: read, packages: write }\n", "VERIFY-PERMISSIONS"),
    ("a permissions key in another letter case", DEDICATED, GRANTS, "    Permissions:\n      contents: read\n      packages: write\n", "VERIFY-PERMISSIONS"),
    ("the same break in the other layout", IN_CI, "      packages: read\n", "      packages: write\n", "VERIFY-PERMISSIONS"),
)

SOURCE = (
    ("another organisation's action", DEDICATED, ACTION, "someone/else/.github/actions/verify-package-attestations", "VERIFY-SOURCE"),
    ("a copy of the action in the caller's own tree", DEDICATED, ACTION + "@2c34b90b367fd160bb01d311d29224d250460086 # v1.2.1", "./.github/actions/verify-package-attestations", "VERIFY-SOURCE"),
    ("a call with no ref", DEDICATED, "@2c34b90b367fd160bb01d311d29224d250460086 # v1.2.1", " # v1.2.1", "VERIFY-SOURCE"),
    ("a lookalike repository", DEDICATED, "branchLeft/github-workflows/", "branchLeft/github-workflows-fork/", "VERIFY-SOURCE"),
)

STRUCTURE = (
    ("the verify step removed", DEDICATED, VERIFY_STEP, "", "VERIFY-NONE"),
    ("the verify step replaced by a shell line", DEDICATED, VERIFY_STEP, "      - name: Verify package attestations\n        run: echo verified\n", "VERIFY-NONE"),
    ("the verify step commented out", DEDICATED, VERIFY_STEP, "".join("#" + line for line in VERIFY_STEP.splitlines(True)), "VERIFY-NONE"),
)


class BrokenWorkflows(unittest.TestCase):
    def check(self, cases, **kwargs):
        for what, text, old, new, code in cases:
            with self.subTest(what=what):
                self.assertIn(code, codes(findings(mutate(text, old, new), **kwargs)))

    def test_continue_on_error_in_every_form_is_named(self):
        self.check(CONTINUE_ON_ERROR)

    def test_an_if_on_the_job_or_the_verify_step_is_named(self):
        self.check(IF_FORMS)

    def test_a_chain_of_needs_that_can_skip_is_named(self):
        self.check(NEEDS)

    def test_a_permissions_block_other_than_the_two_reads_is_named(self):
        self.check(PERMISSIONS)

    def test_an_action_from_anywhere_else_is_named(self):
        self.check(SOURCE)

    def test_a_workflow_that_no_longer_calls_the_action_is_named(self):
        self.check(STRUCTURE)

    def test_a_missing_or_renamed_job_is_named(self):
        self.assertEqual(codes(findings(DEDICATED, ["nope"])), {"VERIFY-NO-JOB"})
        renamed = mutate(DEDICATED, "  verify:\n", "  verify-renamed:\n")
        self.assertEqual(codes(findings(renamed, ["verify"])), {"VERIFY-NO-JOB"})
        self.assertEqual(codes(findings(IN_CI, ["lint"])), {"VERIFY-NOT-CALLED"})

    def test_every_calling_job_is_checked_not_only_the_first(self):
        second = "\n  second:\n    runs-on: ubuntu-latest\n    if: false\n    permissions:\n      contents: read\n      packages: read\n    steps:\n" + VERIFY_STEP
        found = findings(DEDICATED + second)
        self.assertEqual({(item.code, item.job) for item in found}, {("VERIFY-IF-JOB", "second")})

    def test_each_finding_names_the_job_and_says_why(self):
        found = findings(mutate(*in_job("    if: false\n")))
        self.assertEqual([item.job for item in found], ["verify"])
        self.assertIn("skipped", found[0].message)

    def test_several_breaks_are_all_named_at_once(self):
        text = mutate(*in_job("    if: false\n    continue-on-error: true\n"))
        text = mutate(text, GRANTS, "    permissions: write-all\n")
        self.assertEqual(codes(findings(text)), {"VERIFY-IF-JOB", "VERIFY-CONTINUE-ON-ERROR", "VERIFY-PERMISSIONS"})


FLOW_STEP = f"      - {{uses: {ACTION}@2c34b90b367fd160bb01d311d29224d250460086, if: false}}\n"
UNREADABLE = (
    ("a job written as a flow mapping", DEDICATED, "  verify:\n", "  other: {runs-on: ubuntu-latest, if: false, steps: []}\n  verify:\n"),
    ("steps written as a flow list", DEDICATED, "    steps:\n      - name: Checkout\n", "    steps: [{name: Checkout}]\n    notes:\n      - name: Checkout\n"),
    ("a step written as a flow mapping", DEDICATED, VERIFY_STEP, FLOW_STEP),
    ("an anchor", DEDICATED, "    timeout-minutes: 10\n", "    timeout-minutes: &limit 10\n"),
    ("an alias", DEDICATED, "    timeout-minutes: 10\n", "    timeout-minutes: *limit\n"),
    ("a merge key", DEDICATED, "    timeout-minutes: 10\n", "    <<: {if: false}\n"),
    ("a tag", DEDICATED, "    timeout-minutes: 10\n", "    timeout-minutes: !!str 10\n"),
    ("a tab in the indentation", DEDICATED, "    timeout-minutes: 10\n", "\ttimeout-minutes: 10\n"),
    ("a key twice in one job", DEDICATED, "    timeout-minutes: 10\n", "    timeout-minutes: 10\n    timeout-minutes: 11\n"),
    ("if twice in one job", *in_job("    if: false\n    if: true\n")),
    ("an escape in a double-quoted key", *in_job('    "i\\u0066": false\n')),
    ("a second document", DEDICATED, VERIFY_STEP, VERIFY_STEP + "---\nname: other\n"),
    ("a line that is not a mapping entry", DEDICATED, "    timeout-minutes: 10\n", "    timeout-minutes: 10\n    garbage\n"),
    ("a nested sequence", DEDICATED, CHECKOUT, "      - - nested\n" + CHECKOUT),
    ("a needed job that is not in the workflow", DEDICATED, JOB_HEAD, JOB_HEAD + "    needs: ghost\n"),
    ("a quoted string that never closes", DEDICATED, "    timeout-minutes: 10\n", "    notes: 'never closed\n"),
    ("a flow collection that never closes", DEDICATED, "    timeout-minutes: 10\n", "    notes: [a, b\n"),
    ("steps that are not a list", DEDICATED, "    steps:\n", "    steps: nothing\n    ignored:\n"),
    ("no jobs at all", DEDICATED, "jobs:\n", "notjobs:\n"),
)


class UnreadableWorkflows(unittest.TestCase):
    def test_anything_not_read_with_certainty_fails_the_check(self):
        for what, text, old, new in UNREADABLE:
            with self.subTest(what=what), self.assertRaises(checker.Unreadable):
                findings(mutate(text, old, new))

    def test_an_empty_workflow_is_unreadable(self):
        with self.assertRaises(checker.Unreadable):
            findings("")
        with self.assertRaises(checker.Unreadable):
            findings("# only a comment\n")

    def test_a_workflow_with_a_list_at_the_top_is_unreadable(self):
        with self.assertRaises(checker.Unreadable):
            findings("- a\n- b\n")


class ParserReadsWhatWorkflowsHold(unittest.TestCase):
    def parse(self, text):
        return checker.Parser(text).document()

    def test_a_block_scalar_keeps_hashes_and_colons_as_text(self):
        doc = self.parse("a:\n  run: |\n    echo 'x: y' # not a comment\n    if: false\n  b: c\n")
        self.assertEqual(sorted(doc["a"]), ["b", "run"])
        self.assertIn("if: false", doc["a"]["run"].text)

    def test_a_comment_inside_quotes_is_text_and_after_them_is_dropped(self):
        doc = self.parse("a: 'x # y' # gone\nb: \"p # q\"\nc: don't # gone\n")
        self.assertEqual(doc["a"].text, "x # y")
        self.assertEqual(doc["b"].text, "p # q")
        self.assertEqual(doc["c"].text, "don't")

    def test_a_plain_value_may_continue_on_later_lines(self):
        doc = self.parse("a:\n  if: x &&\n    y\n  b: c\n")
        self.assertEqual(doc["a"]["if"].text, "x && y")
        self.assertEqual(doc["a"]["b"].text, "c")

    def test_a_flow_value_may_span_lines(self):
        doc = self.parse("a:\n  branches: [main,\n    dev]\n  b: c\n")
        self.assertEqual(doc["a"]["branches"].text, "[main, dev]")
        self.assertEqual(doc["a"]["b"].text, "c")

    def test_a_sequence_may_sit_level_with_its_key(self):
        doc = self.parse("a:\n- x\n- y: 1\n  z: 2\nb: c\n")
        self.assertEqual([getattr(item, "text", None) for item in doc["a"][:1]], ["x"])
        self.assertEqual(sorted(doc["a"][1]), ["y", "z"])
        self.assertEqual(doc["b"].text, "c")

    def test_a_key_whose_value_holds_a_colon_and_an_expression_is_one_value(self):
        doc = self.parse("a:\n  group: ${{ github.workflow }}-${{ x || y }}\n  url: https://example.com/a:b\n")
        self.assertEqual(doc["a"]["url"].text, "https://example.com/a:b")


class Command(unittest.TestCase):
    def run_main(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = checker.main(["check-verify-job-hardened.py", *args])
        return code, out.getvalue()

    def test_a_clean_workflow_exits_zero_with_an_ok_line(self):
        code, out = self.run_main("--workflow", str(FIXTURES / "verify-pass-dedicated.yml"))
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("ok: "))

    def test_a_broken_workflow_exits_one_with_one_annotation_per_finding(self):
        code, out = self.run_main("--workflow", str(FIXTURES / "verify-fail-continue-on-error.yml"))
        self.assertEqual(code, 1)
        self.assertEqual(len(out.splitlines()), 1)
        self.assertRegex(out, r"^::error file=\S+::VERIFY-CONTINUE-ON-ERROR verify: ")

    def test_the_skippable_fixture_is_named(self):
        code, out = self.run_main("--workflow", str(FIXTURES / "verify-fail-if.yml"))
        self.assertEqual(code, 1)
        self.assertIn("VERIFY-IF-JOB", out)

    def test_a_missing_file_fails_and_is_never_a_pass(self):
        code, out = self.run_main("--workflow", str(FIXTURES / "no-such-file.yml"))
        self.assertEqual(code, 1)
        self.assertIn("VERIFY-UNREADABLE", out)

    def test_a_binary_file_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "bad.yml")
            path.write_bytes(b"\xff\xfe\x00bad")
            code, out = self.run_main("--workflow", str(path))
        self.assertEqual(code, 1)
        self.assertIn("VERIFY-UNREADABLE", out)

    def test_an_unreadable_workflow_fails_with_the_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp, "w.yml")
            path.write_text(mutate(DEDICATED, "    timeout-minutes: 10\n", "    timeout-minutes: &a 10\n"), encoding="utf-8")
            code, out = self.run_main("--workflow", str(path))
        self.assertEqual(code, 1)
        self.assertIn("VERIFY-UNREADABLE", out)
        self.assertIn("anchor", out)

    def test_the_job_option_is_checked_and_may_repeat(self):
        path = str(FIXTURES / "verify-pass-in-ci.yml")
        self.assertEqual(self.run_main("--workflow", path, "--job", "verify-package-attestations")[0], 0)
        code, out = self.run_main("--workflow", path, "--job", "verify-package-attestations", "--job", "nope")
        self.assertEqual(code, 1)
        self.assertIn("VERIFY-NO-JOB nope", out)

    def test_the_job_option_takes_a_whitespace_list_and_an_empty_value(self):
        path = str(FIXTURES / "verify-pass-in-ci.yml")
        self.assertEqual(self.run_main("--workflow", path, "--job", "")[0], 0)
        code, out = self.run_main("--workflow", path, "--job", "verify-package-attestations\nlint")
        self.assertEqual(code, 1)
        self.assertIn("VERIFY-NOT-CALLED lint", out)

    def test_the_program_runs_as_a_command_the_way_the_action_runs_it(self):
        done = subprocess.run(
            [sys.executable, "-I", str(HERE / SCRIPT_NAME), "--workflow", str(FIXTURES / "verify-fail-if.yml")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(done.returncode, 1)
        self.assertIn("VERIFY-IF-JOB", done.stdout)


class Sabotage(unittest.TestCase):
    """Break each control in a copy of this directory and require the named tests to go red
    there; the unmodified copy must be green. Skipped inside a broken copy's own run."""

    CASES: ClassVar[dict[str, tuple[str, str, str, set[str]]]] = {
        "a continue-on-error on the job is not read": (
            SCRIPT_NAME,
            '    for node in named(job, "continue-on-error"):\n        findings += check_continue(node, "the job", job_id)\n',
            "",
            {"BrokenWorkflows.test_continue_on_error_in_every_form_is_named", "BrokenWorkflows.test_several_breaks_are_all_named_at_once"},
        ),
        "a continue-on-error on a step is not read": (
            SCRIPT_NAME,
            '            findings += check_continue(node, f"step {label}", job_id)\n',
            "            pass\n",
            {"BrokenWorkflows.test_continue_on_error_in_every_form_is_named"},
        ),
        "any continue-on-error value is accepted": (
            SCRIPT_NAME,
            '    if isinstance(node, Scalar) and node.style == "plain" and node.text.lower() == "false":',
            "    if True:",
            {"BrokenWorkflows.test_continue_on_error_in_every_form_is_named"},
        ),
        "a quoted false is accepted": (
            SCRIPT_NAME,
            'node.style == "plain" and node.text.lower() == "false"',
            'node.text.lower() == "false"',
            {"BrokenWorkflows.test_continue_on_error_in_every_form_is_named"},
        ),
        "an if on the job is not read": (
            SCRIPT_NAME,
            '    if named(job, "if"):\n        findings.append(Finding("VERIFY-IF-JOB"',
            '    if False:\n        findings.append(Finding("VERIFY-IF-JOB"',
            {"BrokenWorkflows.test_an_if_on_the_job_or_the_verify_step_is_named", "BrokenWorkflows.test_every_calling_job_is_checked_not_only_the_first"},
        ),
        "an if on the verify step is not read": (
            SCRIPT_NAME,
            '        if named(step, "if"):',
            "        if False:",
            {"BrokenWorkflows.test_an_if_on_the_job_or_the_verify_step_is_named"},
        ),
        "the needed jobs are not read": (
            SCRIPT_NAME,
            "    findings += check_needs(jobs, job_id)\n",
            "",
            {"BrokenWorkflows.test_a_chain_of_needs_that_can_skip_is_named"},
        ),
        "the chain of needs is not followed": (
            SCRIPT_NAME,
            '        todo += needs_of(jobs[name], f"job `{name}`")\n',
            "",
            {"BrokenWorkflows.test_a_chain_of_needs_that_can_skip_is_named"},
        ),
        "any permissions are accepted": (
            SCRIPT_NAME,
            "    elif grants_of(node) != GRANTS:",
            "    elif False:",
            {"BrokenWorkflows.test_a_permissions_block_other_than_the_two_reads_is_named"},
        ),
        "a missing permissions block is accepted": (
            SCRIPT_NAME,
            "    if node is None:\n        findings.append(Finding(\"VERIFY-PERMISSIONS\"",
            "    if node is None:\n        return findings\n        findings.append(Finding(\"VERIFY-PERMISSIONS\"",
            {"BrokenWorkflows.test_a_permissions_block_other_than_the_two_reads_is_named"},
        ),
        "an extra grant is allowed": (
            SCRIPT_NAME,
            'GRANTS = {"contents": "read", "packages": "read"}',
            'GRANTS = {"contents": "read", "packages": "read", "id-token": "write"}',
            {"PassingWorkflows.test_a_dedicated_workflow_that_keeps_the_contract_passes"},
        ),
        "the action's source is not checked": (
            SCRIPT_NAME,
            "        if not SOURCE.match(uses):",
            "        if False:",
            {"BrokenWorkflows.test_an_action_from_anywhere_else_is_named"},
        ),
        "a workflow with no verify job passes": (
            SCRIPT_NAME,
            "    if not callers:",
            "    if False:",
            {"BrokenWorkflows.test_a_workflow_that_no_longer_calls_the_action_is_named"},
        ),
        "a named job that is missing passes": (
            SCRIPT_NAME,
            "        if name not in jobs:\n            findings.append(Finding(\"VERIFY-NO-JOB\"",
            "        if False:\n            findings.append(Finding(\"VERIFY-NO-JOB\"",
            {"BrokenWorkflows.test_a_missing_or_renamed_job_is_named"},
        ),
        "only the first calling job is checked": (
            SCRIPT_NAME,
            "    for job_id in callers:\n        findings += check_caller(jobs, job_id)",
            "    for job_id in callers[:1]:\n        findings += check_caller(jobs, job_id)",
            {"BrokenWorkflows.test_every_calling_job_is_checked_not_only_the_first"},
        ),
        "key names are matched in one letter case only": (
            SCRIPT_NAME,
            "    return [value for key, value in mapping.items() if key.lower() == name]",
            "    return [value for key, value in mapping.items() if key == name]",
            {"BrokenWorkflows.test_continue_on_error_in_every_form_is_named", "BrokenWorkflows.test_an_if_on_the_job_or_the_verify_step_is_named"},
        ),
        "a quoted key is not unquoted": (
            SCRIPT_NAME,
            "            key = raw[1:-1]\n",
            "            key = raw\n",
            {"BrokenWorkflows.test_continue_on_error_in_every_form_is_named", "BrokenWorkflows.test_an_if_on_the_job_or_the_verify_step_is_named"},
        ),
        "an escaped key is read as text": (
            SCRIPT_NAME,
            '            if "\\\\" in raw:',
            "            if False:",
            {"UnreadableWorkflows.test_anything_not_read_with_certainty_fails_the_check"},
        ),
        "an anchor is read as a value": (
            SCRIPT_NAME,
            '        if text[0] in "&*!":',
            "        if False:",
            {"UnreadableWorkflows.test_anything_not_read_with_certainty_fails_the_check"},
        ),
        "a merge key is read as a key": (
            SCRIPT_NAME,
            '            if key == "<<":',
            "            if False:",
            {"UnreadableWorkflows.test_anything_not_read_with_certainty_fails_the_check"},
        ),
        "a duplicate key is accepted": (
            SCRIPT_NAME,
            "            if key in result:",
            "            if False:",
            {"UnreadableWorkflows.test_anything_not_read_with_certainty_fails_the_check"},
        ),
        "a tab is accepted": (
            SCRIPT_NAME,
            '                if line[indent : indent + 1] == "\\t":',
            "                if False:",
            {"UnreadableWorkflows.test_anything_not_read_with_certainty_fails_the_check"},
        ),
        "a job that is not a block mapping is skipped": (
            SCRIPT_NAME,
            "        if not isinstance(job, dict):\n            raise Unreadable",
            "        if False:\n            raise Unreadable",
            {"UnreadableWorkflows.test_anything_not_read_with_certainty_fails_the_check"},
        ),
        "a step that is not a block mapping is skipped": (
            SCRIPT_NAME,
            "        if not isinstance(item, dict):\n            raise Unreadable",
            "        if False:\n            raise Unreadable",
            {"UnreadableWorkflows.test_anything_not_read_with_certainty_fails_the_check"},
        ),
        "an unreadable workflow exits zero": (
            SCRIPT_NAME,
            '        print(f"::error file={args.workflow}::VERIFY-UNREADABLE {error}")\n        return 1',
            '        print(f"::error file={args.workflow}::VERIFY-UNREADABLE {error}")\n        return 0',
            {"Command.test_a_missing_file_fails_and_is_never_a_pass", "Command.test_an_unreadable_workflow_fails_with_the_reason"},
        ),
        "findings exit zero": (
            SCRIPT_NAME,
            "    if found:\n        return 1\n",
            "    if found:\n        return 0\n",
            {"Command.test_a_broken_workflow_exits_one_with_one_annotation_per_finding", "Command.test_the_skippable_fixture_is_named"},
        ),
    }

    def run_copy(self, edit):  # type: ignore[no-untyped-def]
        with tempfile.TemporaryDirectory(prefix="check-verify-job-hardened-sabotage-") as tmp:
            copy_dir = pathlib.Path(tmp, ".github", "actions", "check-verify-job-hardened")
            shutil.copytree(HERE, copy_dir, ignore=shutil.ignore_patterns("__pycache__"))
            if edit:
                name, old, new, _ = edit
                target = copy_dir / name
                text = target.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"the control moved, update the sabotage: {old!r}")
                target.write_text(text.replace(old, new), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(copy_dir), "-p", "test_check_verify_job_hardened.py", "-v"],
                capture_output=True,
                text=True,
                env={**os.environ, INSIDE_SABOTAGE: "1"},
                check=False,
            )

    @unittest.skipIf(os.environ.get(INSIDE_SABOTAGE), "running inside a sabotaged copy")
    def test_each_broken_control_turns_its_tests_red(self) -> None:
        for name, edit in self.CASES.items():
            with self.subTest(case=name):
                red = self.run_copy(edit)
                self.assertNotEqual(red.returncode, 0, f"{name} stayed green")
                found = re.findall(r"^(?:FAIL|ERROR): (\w+) \(\S*?\.(\w+)\.\w+\)", red.stderr, re.MULTILINE)
                failed = {f"{cls}.{meth}" for meth, cls in found}
                self.assertTrue(edit[3] <= failed, f"{name}: expected red {sorted(edit[3])}, got {sorted(failed)}")

    @unittest.skipIf(os.environ.get(INSIDE_SABOTAGE), "running inside a sabotaged copy")
    def test_the_unmodified_copy_is_green(self) -> None:
        green = self.run_copy(None)
        self.assertEqual(green.returncode, 0, green.stderr)


if __name__ == "__main__":
    unittest.main()
