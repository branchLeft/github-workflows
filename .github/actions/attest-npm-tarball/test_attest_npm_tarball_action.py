"""Tests for the attest-npm-tarball composite action: its run: bodies under a stub gh.
Sabotage class and the attest-step boundary: test_attest_npm_tarball_action.md beside this file.
"""

from __future__ import annotations

import importlib.util
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
SIBLING = HERE.parent / "verify-package-attestations"
ACTION = HERE / "action.yml"
INSIDE_SABOTAGE = "ATTEST_NPM_TARBALL_SABOTAGE"
GOOD_REF = "refs/tags/render-core-v1.0.0"
REPO = "branchLeft/ghost-platform"
SIGNER = f"{REPO}/.github/workflows/publish-render-core.yml"
EMPTY = ""


def load(path: pathlib.Path, name: str):  # type: ignore[no-untyped-def]
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PROGRAM_TESTS = load(SIBLING / "test_verify_package_attestations.py", "program_tests")
text_of_action = ACTION.read_text(encoding="utf-8")


def steps() -> dict[str, dict[str, str]]:
    """Each step of action.yml by name: its `uses:` line, its `env:` pairs and its `run:` body."""
    found: dict[str, dict[str, str]] = {}
    for block in re.split(r"^    - ", text_of_action.split("  steps:\n", 1)[1], flags=re.MULTILINE)[1:]:
        name = re.match(r"name: (.+)", block).group(1)  # type: ignore[union-attr]
        run = re.search(r"^      run: \|\n((?:        .*\n|\n)+)", block, re.MULTILINE)
        found[name] = {
            "block": "    - " + block,
            "uses": (re.search(r"^      uses: (.+)$", block, re.MULTILINE) or [None, ""])[1],  # type: ignore[index]
            "run": "".join(line[8:] if line.strip() else line for line in run.group(0).splitlines(True)[1:]) if run else "",
        }
    return found


class TheSteps(unittest.TestCase):
    CHECK = "Check the inputs"
    ATTEST = "Attest the tarball"
    VERIFY = "Verify the attestation through the GitHub API"
    PROVE = "Prove a wrong identity is rejected"

    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="attest-npm-tarball-test-")
        self.addCleanup(holder.cleanup)
        self.world = PROGRAM_TESTS.World(pathlib.Path(holder.name))
        self.path, self.sha256 = self.world.package(PROGRAM_TESTS.RENDER, "1.0.0")
        self.steps = steps()

    def attest(self, **changes: object) -> None:
        self.world.attestation(self.sha256, **changes)

    def run_step(self, name: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = {
            **self.world.environment(),
            "HOME": str(self.world.tmp),
            "TARBALL": str(self.path),
            "REPO": REPO,
            "SIGNER_WORKFLOW": SIGNER,
            "REF": GOOD_REF,
            "ATTEMPTS": "2",
            "PAUSE_SECONDS": "0",
            "VERIFIER": str(SIBLING / "verify-package-attestations.py"),
            **overrides,
        }
        return subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", self.steps[name]["run"]],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    # the steps run, with the right identity
    def test_the_right_identity_passes_every_step(self) -> None:
        self.attest()
        for name in (self.CHECK, self.VERIFY, self.PROVE):
            with self.subTest(step=name):
                done = self.run_step(name)
                self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_the_verify_step_checks_the_table_before_it_verifies(self) -> None:
        self.assertLess(self.steps[self.VERIFY]["run"].index("self-test"), self.steps[self.VERIFY]["run"].index("check-tarball"))

    # the verify step refuses
    def test_no_attestation_fails_the_verify_step_after_every_attempt(self) -> None:
        done = self.run_step(self.VERIFY)
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("attempt 2 of 2", done.stdout)

    def test_a_wrong_identity_fails_the_verify_step_whether_or_not_gh_enforces_it(self) -> None:
        for enforce in ("1", "0"):
            for changes in ({"repo": "branchLeft/shared-infra"}, {"workflow": "publish-tenant-package.yml"}, {"ref": "refs/tags/render-core-v1.0.1"}):
                with self.subTest(gh_enforces=enforce, changes=changes):
                    self.setUp()
                    self.attest(**changes)
                    done = self.run_step(self.VERIFY, STUB_ENFORCE=enforce)
                    self.assertNotEqual(done.returncode, 0, done.stdout)

    def test_a_commit_that_is_not_on_main_fails_the_verify_step(self) -> None:
        self.attest()
        done = self.run_step(self.VERIFY, STUB_COMPARE_STATUS="diverged")
        self.assertNotEqual(done.returncode, 0)

    def test_the_identity_the_caller_wrote_is_the_one_sent_to_gh(self) -> None:
        self.attest()
        self.run_step(self.VERIFY)
        verify = next(c for c in self.world.calls() if c.startswith("attestation verify"))
        for expected in (f"--repo {REPO}", f"--signer-workflow {SIGNER}", f"--source-ref {GOOD_REF}"):
            self.assertIn(expected, verify)

    # the wrong-identity step
    def test_a_gh_that_accepts_a_wrong_identity_fails_the_prove_step(self) -> None:
        self.attest()
        done = self.run_step(self.PROVE, STUB_ENFORCE="0")
        self.assertNotEqual(done.returncode, 0)
        self.assertIn("proves nothing", done.stdout)

    def test_a_fault_fails_the_prove_step(self) -> None:
        self.attest()
        for fault in ("network", "tuf"):
            with self.subTest(fault=fault):
                self.assertNotEqual(self.run_step(self.PROVE, STUB_FAULT=fault).returncode, 0)

    # the input check
    def test_inputs_that_are_not_literal_are_refused_before_anything_is_attested(self) -> None:
        for overrides in (
            {"TARBALL": str(self.path.with_suffix(".tar"))},
            {"TARBALL": str(self.world.tmp / "missing.tgz")},
            {"REPO": "no-slash"},
            {"SIGNER_WORKFLOW": "other/repo/.github/workflows/x.yml"},
            {"SIGNER_WORKFLOW": f"{REPO}/.github/workflows/../x.yml"},
            {"REF": "main"},
            {"REF": "refs/tags/a b"},
            {"REF": ""},
        ):
            with self.subTest(overrides=overrides):
                self.assertNotEqual(self.run_step(self.CHECK, **overrides).returncode, 0)

    def test_the_input_check_needs_no_token(self) -> None:
        self.assertEqual(self.run_step(self.CHECK, GH_TOKEN=EMPTY).returncode, 0)


class TheStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.text = text_of_action
        self.steps = steps()

    def test_the_inputs_are_the_declared_ones_and_the_identity_has_no_default(self) -> None:
        declared = self.text.split("runs:")[0].split("inputs:")[1]
        self.assertEqual(
            re.findall(r"^  ([a-z-]+):$", declared, re.MULTILINE),
            ["tarball", "repo", "signer-workflow", "ref", "github-token", "attempts", "pause-seconds"],
        )
        for name in ("tarball", "repo", "signer-workflow", "ref"):
            block = re.search(rf"^  {name}:\n((?:    .*\n)+)", declared, re.MULTILINE).group(1)  # type: ignore[union-attr]
            self.assertIn("required: true", block, name)
            self.assertNotIn("default:", block, name)

    def test_the_steps_are_these_in_this_order(self) -> None:
        self.assertEqual(
            list(self.steps),
            ["Check the inputs", "Attest the tarball", "Verify the attestation through the GitHub API", "Prove a wrong identity is rejected"],
        )

    def test_the_attest_action_is_pinned_by_sha_with_its_tag_in_a_comment(self) -> None:
        self.assertRegex(self.steps["Attest the tarball"]["uses"], r"^actions/attest-build-provenance@[0-9a-f]{40} # v\d+\.\d+\.\d+$")

    def test_the_attest_step_attests_the_input_tarball_and_nothing_else(self) -> None:
        block = self.steps["Attest the tarball"]["block"]
        self.assertIn("subject-path: ${{ inputs.tarball }}", block)
        self.assertNotIn("push-to-registry", block)

    def test_no_step_can_be_skipped_or_forgiven(self) -> None:
        for forbidden in (r"^\s+if:", "continue-on-error", r"\|\| true", "set \\+e"):
            self.assertIsNone(re.search(forbidden, self.text, re.MULTILINE), forbidden)

    def test_no_expression_reaches_a_shell(self) -> None:
        for name, step in self.steps.items():
            self.assertNotIn("${{", step["run"], name)

    def test_the_identity_is_never_read_from_the_run_being_checked(self) -> None:
        allowed = re.compile(r"inputs\.[a-z-]+|github\.action_path|github\.token")
        expressions = re.findall(r"\$\{\{\s*([^}]*?)\s*\}\}", self.text)
        self.assertTrue(expressions)
        for expression in expressions:
            self.assertRegex(expression, allowed.pattern)
            self.assertIsNotNone(allowed.fullmatch(expression), expression)

    def test_the_program_is_run_from_the_pinned_revision_in_isolated_mode(self) -> None:
        self.assertIn("${{ github.action_path }}/../verify-package-attestations/verify-package-attestations.py", self.text)
        for name in ("Check the inputs", "Verify the attestation through the GitHub API", "Prove a wrong identity is rejected"):
            for line in self.steps[name]["run"].splitlines():
                if "$VERIFIER" in line:
                    self.assertIn('python3 -I "$VERIFIER"', line)

    def test_every_step_that_runs_shell_stops_on_error(self) -> None:
        for name, step in self.steps.items():
            if step["run"]:
                self.assertTrue(step["run"].startswith("set -eu\n"), name)
                self.assertIn("shell: bash", step["block"], name)

    def test_the_action_asks_for_no_permission_and_no_secret(self) -> None:
        self.assertNotIn("permissions:", self.text)
        self.assertNotIn("secrets.", self.text)
        self.assertNotIn("packages: write", self.text.replace("# ", ""))

    def test_the_action_names_no_tag_of_this_repo(self) -> None:
        self.assertNotRegex(self.text, r"github-workflows[^\n]*@v\d")


class Sabotage(unittest.TestCase):
    """Break each control in a copy of the two directories and require the named tests to go
    red there; the unmodified copy must be green. Skipped inside a broken copy's own run."""

    ACTION_FILE = "attest-npm-tarball/action.yml"
    PROGRAM = "verify-package-attestations/verify-package-attestations.py"
    CASES: ClassVar[dict[str, tuple[str, str, str, set[str]]]] = {
        "the wrong-identity step removed": (
            ACTION_FILE,
            "    - name: Prove a wrong identity is rejected\n",
            "    - name: Prove a wrong identity is rejected, not\n",
            {"TheSteps.test_a_gh_that_accepts_a_wrong_identity_fails_the_prove_step", "TheStructure.test_the_steps_are_these_in_this_order"},
        ),
        "the verify failure forgiven": (
            ACTION_FILE,
            '--attempts "$ATTEMPTS" --pause "$PAUSE_SECONDS"',
            '--attempts "$ATTEMPTS" --pause "$PAUSE_SECONDS" || true',
            {"TheSteps.test_no_attestation_fails_the_verify_step_after_every_attempt", "TheStructure.test_no_step_can_be_skipped_or_forgiven"},
        ),
        "the step made conditional": (
            ACTION_FILE,
            "    - name: Prove a wrong identity is rejected\n      shell: bash\n",
            "    - name: Prove a wrong identity is rejected\n      if: always()\n      shell: bash\n",
            {"TheStructure.test_no_step_can_be_skipped_or_forgiven"},
        ),
        "the identity read from the run": (
            ACTION_FILE,
            '        REF: ${{ inputs.ref }}\n        ATTEMPTS:',
            '        REF: ${{ github.ref }}\n        ATTEMPTS:',
            {"TheStructure.test_the_identity_is_never_read_from_the_run_being_checked"},
        ),
        "a default given to the identity": (
            ACTION_FILE,
            "  ref:\n    description: The exact git ref the attested run must have, refs/tags/... or refs/heads/...\n    required: true\n",
            "  ref:\n    description: The exact git ref the attested run must have, refs/tags/... or refs/heads/...\n    required: true\n    default: refs/heads/main\n",
            {"TheStructure.test_the_inputs_are_the_declared_ones_and_the_identity_has_no_default"},
        ),
        "the input check not run": (
            ACTION_FILE,
            'python3 -I "$VERIFIER" check-inputs',
            "true",
            {"TheSteps.test_inputs_that_are_not_literal_are_refused_before_anything_is_attested"},
        ),
        "the table check not run": (
            ACTION_FILE,
            '        python3 -I "$VERIFIER" self-test\n        python3 -I "$VERIFIER" check-tarball',
            '        python3 -I "$VERIFIER" check-tarball',
            {"TheSteps.test_the_verify_step_checks_the_table_before_it_verifies"},
        ),
        "the attest action unpinned": (
            ACTION_FILE,
            "actions/attest-build-provenance@4d101475d8b20a2381f78447822ac1eab6504dd8 # v4.2.2",
            "actions/attest-build-provenance@v4",
            {"TheStructure.test_the_attest_action_is_pinned_by_sha_with_its_tag_in_a_comment"},
        ),
        "the program always passes": (
            PROGRAM,
            '    """Every reason this tarball is not attested by exactly this identity; empty when it is."""\n',
            '    """Every reason this tarball is not attested by exactly this identity; empty when it is."""\n    return []\n',
            {
                "TheSteps.test_no_attestation_fails_the_verify_step_after_every_attempt",
                "TheSteps.test_a_wrong_identity_fails_the_verify_step_whether_or_not_gh_enforces_it",
                "TheSteps.test_a_commit_that_is_not_on_main_fails_the_verify_step",
            },
        ),
    }

    def run_copy(self, edit):  # type: ignore[no-untyped-def]
        with tempfile.TemporaryDirectory(prefix="attest-npm-tarball-sabotage-") as tmp:
            actions = pathlib.Path(tmp, ".github", "actions")
            for directory in (HERE, SIBLING):
                shutil.copytree(directory, actions / directory.name, ignore=shutil.ignore_patterns("__pycache__"))
            if edit:
                name, old, new, _ = edit
                target = actions / name
                text = target.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"the control moved, update the sabotage: {old!r}")
                target.write_text(text.replace(old, new), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(actions / HERE.name), "-p", "test_attest_npm_tarball_action.py", "-v"],
                capture_output=True,
                text=True,
                env={**os.environ, INSIDE_SABOTAGE: "1", PROGRAM_TESTS.INSIDE_SABOTAGE: "1"},
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
