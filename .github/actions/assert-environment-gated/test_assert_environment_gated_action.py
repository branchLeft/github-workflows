"""Tests for the action around assert-environment-gated.py.

The script's own tests are test_assert_environment_gated.py. These run the
action's `run:` body, cut out of action.yml, under bash with a stub `gh` first on
PATH, so what is asserted is the step CI runs and not a copy of it. The failure
that matters is the step passing over an environment that gates nothing.
"""

from __future__ import annotations

import hashlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
ACTION = HERE / "action.yml"
SCRIPT = HERE / "assert-environment-gated.py"
INSIDE_SABOTAGE = "ASSERT_ENVIRONMENT_GATED_SABOTAGE"

# The script is the byte-identical copy that was in two repositories. A change
# to it is a deliberate edit of the one shared home, so it shows in this test.
SCRIPT_SHA256 = "eddf46b90ed320cb340ead535b43cbbf1ba2f73318673678db1bde14ca17fe9c"

REVIEWER = FIXTURES / "environment-reviewer.json"
NO_ENV = FIXTURES / "environment-none.json"
APP = FIXTURES / "rules-app.json"
NO_RULES = FIXTURES / "rules-none.json"
DISABLED_APP = FIXTURES / "rules-disabled-app.json"
OTHER_APP = FIXTURES / "rules-other-app.json"
ERROR_BODY = FIXTURES / "error-body.json"
UNREADABLE = FIXTURES / "unreadable.txt"
GARBAGE = FIXTURES / "garbage.txt"


def _run_body() -> str:
    """The action's `run:` body, cut out of action.yml as text."""
    lines = ACTION.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line == "      run: |")
    body = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" " * 8):
            break
        body.append(line[8:])
    return "\n".join(body) + "\n"


class TheScript(unittest.TestCase):
    def test_sha256_equals_the_pin(self) -> None:
        self.assertEqual(hashlib.sha256(SCRIPT.read_bytes()).hexdigest(), SCRIPT_SHA256)


class TheStepRefusesUngated(unittest.TestCase):
    def run_step(self, env, rules, environment="production", repository="example/repo"):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = pathlib.Path(tmp)
            stub = tmp_path / "gh"
            shutil.copy(FIXTURES / "stub-gh.sh", stub)
            stub.chmod(0o755)
            log = tmp_path / "calls.log"
            log.write_text("", encoding="utf-8")
            done = subprocess.run(
                ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _run_body()],
                capture_output=True,
                text=True,
                check=False,
                env={
                    "PATH": f"{tmp}{os.pathsep}{os.environ['PATH']}",
                    "RUNNER_TEMP": tmp,
                    "ENVIRONMENT": environment,
                    "REPOSITORY": repository,
                    "GH_TOKEN": "stub",
                    "SCRIPT": str(SCRIPT),
                    "STUB_ENV": str(env),
                    "STUB_RULES": str(rules),
                    "STUB_LOG": str(log),
                },
            )
            calls = log.read_text(encoding="utf-8").splitlines()
            left = sorted(p.name for p in tmp_path.iterdir() if p.name not in {"gh", "calls.log"})
            return done.returncode, done.stdout, done.stderr, calls, left

    def test_a_required_reviewer_passes(self) -> None:
        code, out, _, calls, _ = self.run_step(REVIEWER, NO_RULES)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("gated: "), out)
        self.assertEqual(len(calls), 2)

    def test_an_enabled_custom_rule_passes(self) -> None:
        code, out, _, _, _ = self.run_step(NO_ENV, APP)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("gated: "), out)

    def test_no_rule_refuses(self) -> None:
        code, out, _, calls, _ = self.run_step(NO_ENV, NO_RULES)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)
        self.assertIn("::error::the production environment has neither", out)
        self.assertEqual(len(calls), 2)

    def test_a_disabled_custom_rule_refuses(self) -> None:
        code, out, _, _, _ = self.run_step(NO_ENV, DISABLED_APP)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)

    def test_another_apps_rule_refuses(self) -> None:
        code, out, _, _, _ = self.run_step(NO_ENV, OTHER_APP)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)

    def test_an_unreadable_environment_refuses_even_with_a_rule_present(self) -> None:
        code, out, _, _, _ = self.run_step(UNREADABLE, APP)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read the production environment", out)

    def test_unreadable_rules_refuse_even_with_a_reviewer_present(self) -> None:
        code, out, _, _, _ = self.run_step(REVIEWER, UNREADABLE)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read the production deployment protection rules", out)

    def test_a_malformed_environment_refuses(self) -> None:
        code, out, err, _, _ = self.run_step(GARBAGE, APP)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", out + err)

    def test_malformed_rules_refuse(self) -> None:
        code, out, err, _, _ = self.run_step(REVIEWER, GARBAGE)
        self.assertNotEqual(code, 0)
        self.assertIn("could not read", out + err)

    def test_an_error_body_refuses(self) -> None:
        code, out, _, _, _ = self.run_step(ERROR_BODY, ERROR_BODY)
        self.assertNotEqual(code, 0)
        self.assertIn("NOT gated", out)

    def test_the_environment_the_caller_names_is_the_one_read(self) -> None:
        code, _, _, calls, _ = self.run_step(REVIEWER, NO_RULES, environment="production-demo", repository="example/other")
        self.assertEqual(code, 0)
        self.assertEqual(
            calls,
            [
                "api repos/example/other/environments/production-demo",
                "api repos/example/other/environments/production-demo/deployment_protection_rules",
            ],
        )

    def test_a_name_that_could_change_the_api_path_is_refused_before_any_call(self) -> None:
        for environment in ("", "production/../x", "a b", "production?x=1", "pro$duction", "../..", "..", ".", ".x"):
            with self.subTest(environment=environment):
                code, out, _, calls, _ = self.run_step(REVIEWER, APP, environment=environment)
                self.assertNotEqual(code, 0)
                self.assertIn("environment must be a name", out)
                self.assertEqual(calls, [])

    def test_a_repository_that_is_not_owner_slash_name_is_refused_before_any_call(self) -> None:
        for repository in ("", "repo", "a/b/c", "a/b?x", "a b/c", "/c", "a/..", "a/.", "../b", "../.."):
            with self.subTest(repository=repository):
                code, out, _, calls, _ = self.run_step(REVIEWER, APP, repository=repository)
                self.assertNotEqual(code, 0)
                self.assertIn("repository must be owner/name", out)
                self.assertEqual(calls, [])

    def test_the_temporary_files_are_removed_on_every_way_out(self) -> None:
        for env, rules in ((REVIEWER, NO_RULES), (NO_ENV, NO_RULES), (UNREADABLE, APP), (REVIEWER, UNREADABLE)):
            with self.subTest(env=env.name, rules=rules.name):
                _, _, _, _, left = self.run_step(env, rules)
                self.assertEqual(left, [])


class TheActionStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.text = ACTION.read_text(encoding="utf-8")

    def test_it_is_a_composite_action_with_the_declared_inputs(self) -> None:
        self.assertIn("using: composite", self.text)
        declared = self.text.split("runs:")[0].split("inputs:")[1]
        self.assertEqual(re.findall(r"^  ([a-z-]+):$", declared, re.MULTILINE), ["environment", "github-token", "repository"])

    def test_no_expression_reaches_the_shell(self) -> None:
        self.assertNotIn("${{", _run_body())

    def test_the_script_is_run_from_the_pinned_revision_in_isolated_mode(self) -> None:
        self.assertIn("SCRIPT: ${{ github.action_path }}/assert-environment-gated.py", self.text)
        for line in _run_body().splitlines():
            if "$SCRIPT" in line:
                self.assertIn('python3 -I "$SCRIPT"', line)

    def test_the_token_defaults_to_the_runs_own_and_nothing_wider(self) -> None:
        self.assertIn("default: ${{ github.token }}", self.text)

    def test_the_step_stops_on_error(self) -> None:
        self.assertIn("set -eu", _run_body())
        self.assertIn("shell: bash", self.text)

    def test_the_action_names_no_tag_of_this_repo(self) -> None:
        self.assertNotRegex(self.text, r"github-workflows[^\n]*@v\d")


class Sabotage(unittest.TestCase):
    """Break each control in a copy of this directory and require the named
    tests to go red there; the unmodified copy must be green. Skipped inside a
    broken copy's own run."""

    SCRIPT_NAME = "assert-environment-gated.py"
    CASES = {
        "the app id no longer compared": (
            SCRIPT_NAME,
            "                and app.get(\"id\") == APP_ID\n",
            "",
            {"Cli.test_self_test_exits_zero", "TheStepRefusesUngated.test_a_required_reviewer_passes"},
        ),
        "a disabled rule accepted": (
            SCRIPT_NAME,
            "rule.get(\"enabled\") is True",
            "rule.get(\"enabled\") is not None",
            {"Cli.test_disabled_app_rule_exits_nonzero", "TheStepRefusesUngated.test_a_disabled_custom_rule_refuses"},
        ),
        "the second API read dropped": (
            "action.yml",
            "        gh api \"repos/${REPOSITORY}/environments/${ENVIRONMENT}/deployment_protection_rules\" > \"$rules_json\" \\\n          || { echo \"::error::could not read the ${ENVIRONMENT} deployment protection rules\"; exit 1; }\n",
            "        echo '{\"custom_deployment_protection_rules\": []}' > \"$rules_json\"\n",
            {"TheStepRefusesUngated.test_an_enabled_custom_rule_passes", "TheStepRefusesUngated.test_unreadable_rules_refuse_even_with_a_reviewer_present"},
        ),
        "an unreadable environment read ignored": (
            "action.yml",
            "          || { echo \"::error::could not read the ${ENVIRONMENT} environment\"; exit 1; }\n",
            "          || true\n",
            {"TheStepRefusesUngated.test_an_unreadable_environment_refuses_even_with_a_rule_present"},
        ),
        "a refusal no longer fails the step": (
            "action.yml",
            "          echo \"::error::the ${ENVIRONMENT} environment has neither",
            "          exit 0\n          echo \"::error::the ${ENVIRONMENT} environment has neither",
            {"TheStepRefusesUngated.test_no_rule_refuses", "TheStepRefusesUngated.test_a_disabled_custom_rule_refuses"},
        ),
        "the environment name not validated": (
            "action.yml",
            "        [[ \"$ENVIRONMENT\" =~ $name_re ]] || ",
            "        true || ",
            {"TheStepRefusesUngated.test_a_name_that_could_change_the_api_path_is_refused_before_any_call"},
        ),
        "the repository not validated": (
            "action.yml",
            "        [[ \"$REPOSITORY\" =~ $repository_re ]] || ",
            "        true || ",
            {"TheStepRefusesUngated.test_a_repository_that_is_not_owner_slash_name_is_refused_before_any_call"},
        ),
        "the temporary files left behind": (
            "action.yml",
            "        trap 'rm -f \"$env_json\" \"$rules_json\"' EXIT\n",
            "",
            {"TheStepRefusesUngated.test_the_temporary_files_are_removed_on_every_way_out"},
        ),
        "the environment name hard-coded": (
            "action.yml",
            "gh api \"repos/${REPOSITORY}/environments/${ENVIRONMENT}\" >",
            "gh api \"repos/${REPOSITORY}/environments/production\" >",
            {"TheStepRefusesUngated.test_the_environment_the_caller_names_is_the_one_read"},
        ),
    }

    def run_copy(self, edit):
        with tempfile.TemporaryDirectory(prefix="environment-gated-sabotage-") as tmp:
            copy = pathlib.Path(tmp, ".github", "actions", "assert-environment-gated")
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__"))
            if edit:
                name, old, new, _ = edit
                target = copy / name
                text = target.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"the control moved, update the sabotage: {old!r}")
                target.write_text(text.replace(old, new), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(copy), "-p", "test_*.py", "-v"],
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
