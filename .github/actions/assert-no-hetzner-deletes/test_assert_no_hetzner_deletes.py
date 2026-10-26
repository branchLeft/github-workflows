#!/usr/bin/env python3
"""Unit tests for assert-no-hetzner-deletes.

This gate is the only automated thing standing between a merged plan and the
destruction of a production host or the platform network, so the failure that
matters is the guard reporting clean over a plan it did not actually
understand. Every test below is aimed at that shape: a URN it mis-parses, an op
it waves through, a directory whose coverage it silently skips, or an error
state that shares an exit code with success.
"""

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

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
ACTION = HERE / "action.yml"
INSIDE_SABOTAGE = "ASSERT_NO_HETZNER_DELETES_SABOTAGE"


def _load_module():
    """Import the script by path: its filename has hyphens, so it is not a
    legal module name for a plain import."""
    path = HERE / "assert-no-hetzner-deletes.py"
    spec = importlib.util.spec_from_file_location("assert_no_hetzner_deletes", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


guard = _load_module()


_STACK = "urn:pulumi:production::example-mail::"
_URN_SERVER = f"{_STACK}hcloud:index/server:Server::mail-server"
_URN_FIREWALL = f"{_STACK}hcloud:index/firewall:Firewall::mail-firewall"
_URN_ROUTE = (
    "urn:pulumi:production::example-network::"
    "hcloud:index/networkRoute:NetworkRoute::platform-egress"
)
_URN_CHILD = (
    "urn:pulumi:production::example-hosts::"
    "example:hetzner:Host$hcloud:index/server:Server::app-server"
)
_URN_CHILD_IP = (
    "urn:pulumi:production::example-hosts::"
    "example:hetzner:Host$hcloud:index/primaryIp:PrimaryIp::app-ipv4"
)
_URN_ATTACH = (
    "urn:pulumi:production::example-hosts::"
    "example:hetzner:Host$hcloud:index/serverNetwork:ServerNetwork::app-network"
)
_MAP = {"mail": {"hcloud.Server", "hcloud.Firewall"}, "hosts": {"Host"}}


def _plan(*steps):
    return {"steps": [{"op": op, "urn": urn} for op, urn in steps]}


class SplitUrnTests(unittest.TestCase):
    def test_plain_urn(self):
        self.assertEqual(
            guard._split_urn(_URN_SERVER), ("hcloud:index/server:Server", "mail-server")
        )

    def test_parent_chain_resolves_to_leaf_type(self):
        # A component's child carries `<parent>$<type>` in the type segment;
        # protection has to key on the leaf type or every Host child escapes.
        self.assertEqual(
            guard._split_urn(_URN_CHILD), ("hcloud:index/server:Server", "app-server")
        )

    def test_non_urn_raises(self):
        with self.assertRaises(ValueError):
            guard._split_urn("urn:pulumi:production")


class DestructiveStepsTests(unittest.TestCase):
    def test_clean_plan_is_empty(self):
        self.assertEqual(
            guard.destructive_steps(_plan(("same", _URN_SERVER), ("update", _URN_FIREWALL))),
            [],
        )

    def test_delete_of_protected_type_is_found(self):
        self.assertEqual(
            guard.destructive_steps(_plan(("delete", _URN_SERVER))), [("mail-server", "delete")]
        )

    def test_every_replace_flavoured_op_is_destructive(self):
        for op in (
            "replace",
            "create-replacement",
            "delete-replaced",
            "read-replacement",
            "discard-replaced",
            "remove-pending-replace",
        ):
            with self.subTest(op=op):
                self.assertEqual(
                    guard.destructive_steps(_plan((op, _URN_SERVER))), [("mail-server", op)]
                )

    def test_component_child_is_protected_through_parent_chain(self):
        self.assertEqual(
            guard.destructive_steps(_plan(("delete", _URN_CHILD))),
            [("app-server", "delete")],
        )

    def test_component_child_primary_ip_is_protected_through_parent_chain(self):
        self.assertEqual(
            guard.destructive_steps(_plan(("replace", _URN_CHILD_IP))),
            [("app-ipv4", "replace")],
        )

    def test_network_route_is_deliberately_unprotected(self):
        self.assertEqual(guard.destructive_steps(_plan(("delete", _URN_ROUTE))), [])

    def test_server_network_attachment_is_deliberately_unprotected(self):
        self.assertEqual(guard.destructive_steps(_plan(("delete", _URN_ATTACH))), [])

    def test_the_protected_types_are_exactly_these_five(self):
        # Widening or narrowing the list means editing this test: the edit is
        # the review.
        self.assertEqual(
            guard.PROTECTED_TYPES,
            {
                "hcloud:index/server:Server",
                "hcloud:index/network:Network",
                "hcloud:index/networkSubnet:NetworkSubnet",
                "hcloud:index/firewall:Firewall",
                "hcloud:index/primaryIp:PrimaryIp",
            },
        )

    def test_one_protected_delete_among_permitted_churn_is_found(self):
        plan = _plan(
            ("create", _URN_ATTACH),
            ("delete", _URN_ATTACH),
            ("update", _URN_SERVER),
            ("delete", _URN_FIREWALL),
        )
        self.assertEqual(guard.destructive_steps(plan), [("mail-firewall", "delete")])

    def test_missing_steps_raises_instead_of_passing(self):
        # "I found no steps" and "there are no destructive steps" must not
        # share an exit path.
        for bad in ({}, {"steps": "nope"}, {"steps": [{"op": "delete"}]}, [], "plan", None):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    guard.destructive_steps(bad)


class LoadCoverageMapTests(unittest.TestCase):
    def _load(self, content):
        with tempfile.TemporaryDirectory() as root:
            path = pathlib.Path(root) / "map.json"
            if content is not None:
                path.write_text(content if isinstance(content, str) else json.dumps(content))
            return guard.load_coverage_map(str(path))

    def test_a_well_formed_map_is_read(self):
        self.assertEqual(
            self._load({"mail": ["hcloud.Server", "hcloud.Firewall"], "hosts": ["Host"]}),
            {"mail": {"hcloud.Server", "hcloud.Firewall"}, "hosts": {"Host"}},
        )

    def test_the_committed_fixture_map_is_read(self):
        self.assertEqual(
            guard.load_coverage_map(str(FIXTURES / "coverage-map.json")),
            {"mail": {"hcloud.Server", "hcloud.Firewall"}},
        )

    def test_anything_else_is_refused(self):
        for name, bad in [
            ("missing file", None),
            ("not json", "{nope"),
            ("a list", ["mail"]),
            ("an empty object", {}),
            ("an empty list of constructors", {"mail": []}),
            ("constructors not a list", {"mail": "hcloud.Server"}),
            ("a non-string constructor", {"mail": ["hcloud.Server", 7]}),
            ("an empty constructor", {"mail": [""]}),
            ("an empty directory name", {"": ["Host"]}),
        ]:
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self._load(bad)


class VerifyCoverageTests(unittest.TestCase):
    def _run(self, dirname, source, coverage_map=_MAP):
        with tempfile.TemporaryDirectory() as root:
            directory = pathlib.Path(root) / dirname
            directory.mkdir()
            (directory / "program.ts").write_text(source, encoding="utf-8")
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                result = guard.verify_coverage(str(directory), coverage_map)
            return result, buffer.getvalue()

    def test_mail_with_both_constructors_passes(self):
        result, _ = self._run(
            "mail",
            "new hcloud.Server('x', {});\nnew hcloud.Firewall('y', {});\n",
        )
        self.assertEqual(result, 0)

    def test_missing_constructor_fails(self):
        result, output = self._run("mail", "new hcloud.Server('x', {});\n")
        self.assertEqual(result, 1)
        self.assertIn("hcloud.Firewall", output)

    def test_mention_in_comment_does_not_count(self):
        result, _ = self._run(
            "mail",
            "new hcloud.Server('x', {});\n// hcloud.Firewall lives elsewhere now\n",
        )
        self.assertEqual(result, 1)

    def test_new_in_a_comment_does_not_count_as_a_declaration_of_another_name(self):
        result, _ = self._run("hosts", "const h = new HostFactory();\n")
        self.assertEqual(result, 1)

    def test_unknown_directory_fails_closed(self):
        # A program dir the map has never heard of must fail, not skip: a new
        # Hetzner project added without coverage is exactly the erosion the
        # map exists to catch.
        result, output = self._run("brand-new-stack", "new hcloud.Server('x', {});\n")
        self.assertEqual(result, 1)
        self.assertIn("coverage map", output)

    def test_the_map_is_the_callers_not_the_scripts(self):
        # The same directory passes under one map and fails under another.
        source = "new hcloud.Server('x', {});\n"
        self.assertEqual(self._run("mail", source, {"mail": {"hcloud.Server"}})[0], 0)
        self.assertEqual(self._run("mail", source, {"mail": {"hcloud.Network"}})[0], 1)

    def test_empty_directory_fails(self):
        with tempfile.TemporaryDirectory() as root:
            directory = pathlib.Path(root) / "mail"
            directory.mkdir()
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                result = guard.verify_coverage(str(directory), _MAP)
        self.assertEqual(result, 1)

    def test_nonexistent_directory_fails(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = guard.verify_coverage("/nonexistent/path/mail", _MAP)
        self.assertEqual(result, 1)

    def test_the_fixture_programs_satisfy_the_fixture_map(self):
        coverage_map = guard.load_coverage_map(str(FIXTURES / "coverage-map.json"))
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            declared = guard.verify_coverage(str(FIXTURES / "programs" / "mail"), coverage_map)
            stale = guard.verify_coverage(str(FIXTURES / "programs-stale" / "mail"), coverage_map)
        self.assertEqual(declared, 0)
        self.assertEqual(stale, 1)


class MainTests(unittest.TestCase):
    def _main(self, argv):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            result = guard.main(["prog", *argv])
        return result, buffer.getvalue()

    def _plan_file(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        self.addCleanup(os.unlink, handle.name)
        with handle:
            handle.write(text if isinstance(text, str) else json.dumps(text))
        return handle.name

    def test_self_test_passes(self):
        result, output = self._main(["--self-test"])
        self.assertEqual(result, 0, output)
        self.assertNotIn("FAIL", output)

    def test_clean_plan_file_exits_zero(self):
        result, _ = self._main([self._plan_file(_plan(("same", _URN_SERVER)))])
        self.assertEqual(result, 0)

    def test_destructive_plan_file_exits_one(self):
        result, output = self._main([self._plan_file(_plan(("delete", _URN_SERVER)))])
        self.assertEqual(result, 1)
        self.assertIn("mail-server", output)
        self.assertIn("::error::", output)

    def test_unreadable_plan_exits_one_not_zero(self):
        result, _ = self._main(["/nonexistent/preview.json"])
        self.assertEqual(result, 1)

    def test_malformed_json_exits_one(self):
        result, _ = self._main([self._plan_file("{not json")])
        self.assertEqual(result, 1)

    def test_a_json_value_that_is_not_a_plan_exits_one(self):
        for text in ("[]", "null", '"plan"', "7", "{}"):
            with self.subTest(text=text):
                result, output = self._main([self._plan_file(text)])
                self.assertEqual(result, 1)
                self.assertIn("not a plan", output)

    def test_usage_error_exits_two(self):
        for argv in (
            [],
            ["one", "two"],
            ["--unknown"],
            ["--self-test", "extra"],
            ["--verify-coverage", "somewhere"],
            ["--coverage-map", "map.json"],
            ["--verify-coverage", "a", "--verify-coverage", "b"],
            ["--verify-coverage", "a", "--coverage-map", "m", "extra"],
        ):
            with self.subTest(argv=argv):
                result, _ = self._main(argv)
                self.assertEqual(result, 2)

    def test_coverage_arguments_are_read_in_either_order(self):
        for argv in (
            ["--verify-coverage", str(FIXTURES / "programs" / "mail"), "--coverage-map", str(FIXTURES / "coverage-map.json")],
            ["--coverage-map", str(FIXTURES / "coverage-map.json"), "--verify-coverage", str(FIXTURES / "programs" / "mail")],
        ):
            with self.subTest(argv=argv):
                result, output = self._main(argv)
                self.assertEqual(result, 0, output)

    def test_stale_coverage_exits_one(self):
        result, output = self._main(
            ["--verify-coverage", str(FIXTURES / "programs-stale" / "mail"), "--coverage-map", str(FIXTURES / "coverage-map.json")]
        )
        self.assertEqual(result, 1)
        self.assertIn("hcloud.Firewall", output)

    def test_an_unreadable_coverage_map_exits_one_not_zero(self):
        result, output = self._main(
            ["--verify-coverage", str(FIXTURES / "programs" / "mail"), "--coverage-map", "/nonexistent/map.json"]
        )
        self.assertEqual(result, 1)
        self.assertIn("::error::", output)


class FixtureTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(HERE / "assert-no-hetzner-deletes.py"), *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_a_plan_with_a_delete_exits_one_and_names_it(self):
        done = self.run_cli(str(FIXTURES / "preview-delete.json"))
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("mail-server (delete)", done.stdout)

    def test_a_plan_without_a_delete_exits_zero(self):
        done = self.run_cli(str(FIXTURES / "preview-no-delete.json"))
        self.assertEqual(done.returncode, 0, done.stdout)
        self.assertIn("OK:", done.stdout)

    def test_the_fixtures_differ_only_in_what_the_plan_does(self):
        # Guards the pair against drifting into two plans that both pass.
        for name, steps in (("preview-delete.json", "delete"), ("preview-no-delete.json", "update")):
            doc = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
            self.assertIn(steps, [step["op"] for step in doc["steps"]], name)


def _run_body():
    """The action's `run:` body, cut out of action.yml as text."""
    lines = ACTION.read_text(encoding="utf-8").splitlines()
    start = next(i for i, line in enumerate(lines) if line == "      run: |")
    body = []
    for line in lines[start + 1 :]:
        if line.strip() and not line.startswith(" " * 8):
            break
        body.append(line[8:])
    return "\n".join(body) + "\n"


class ActionWiringTests(unittest.TestCase):
    """Runs the action's own `run:` body under bash, as the runner does, from the fixtures directory."""

    def run_step(self, program_dir="programs/mail", coverage_map="coverage-map.json", preview=""):
        done = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", "-o", "pipefail", "-c", _run_body()],
            cwd=FIXTURES,
            capture_output=True,
            text=True,
            check=False,
            env={
                "PATH": os.environ["PATH"],
                "PROGRAM_DIR": program_dir,
                "COVERAGE_MAP": coverage_map,
                "PREVIEW_JSON": preview,
                "SCRIPT": str(HERE / "assert-no-hetzner-deletes.py"),
            },
        )
        return done.returncode, done.stdout + done.stderr

    def test_a_plan_without_a_delete_passes(self):
        code, output = self.run_step(preview="preview-no-delete.json")
        self.assertEqual(code, 0, output)

    def test_a_plan_with_a_delete_fails_and_prints_the_whole_plan(self):
        code, output = self.run_step(preview="preview-delete.json")
        self.assertNotEqual(code, 0)
        self.assertIn("mail-server (delete)", output)
        self.assertIn("--- full plan follows ---", output)
        self.assertIn('"changeSummary"', output)

    def test_a_preview_file_that_does_not_exist_fails(self):
        code, output = self.run_step(preview="no-such-preview.json")
        self.assertNotEqual(code, 0)
        self.assertIn("cannot read preview JSON", output)

    def test_no_preview_runs_the_coverage_check_alone(self):
        code, output = self.run_step(preview="")
        self.assertEqual(code, 0, output)
        self.assertIn("OK: all 2 expected constructors", output)

    def test_stale_coverage_fails_even_with_a_clean_plan(self):
        code, output = self.run_step(program_dir="programs-stale/mail", preview="preview-no-delete.json")
        self.assertNotEqual(code, 0)
        self.assertIn("hcloud.Firewall", output)

    def test_the_coverage_inputs_cannot_be_left_out(self):
        for kwargs in ({"program_dir": ""}, {"coverage_map": ""}):
            with self.subTest(kwargs=kwargs):
                code, output = self.run_step(preview="preview-no-delete.json", **kwargs)
                self.assertNotEqual(code, 0)
                self.assertIn("never skipped", output)

    def test_a_directory_the_map_does_not_know_fails(self):
        code, output = self.run_step(program_dir="programs", preview="preview-no-delete.json")
        self.assertNotEqual(code, 0)
        self.assertIn("coverage map", output)


class ActionStructureTests(unittest.TestCase):
    def setUp(self):
        self.text = ACTION.read_text(encoding="utf-8")

    def test_it_is_a_composite_action_with_the_declared_inputs_and_no_outputs(self):
        self.assertIn("using: composite", self.text)
        inputs = re.findall(r"^  ([a-z-]+):$", self.text.split("runs:")[0].split("inputs:")[1], re.MULTILINE)
        self.assertEqual(inputs, ["program-dir", "coverage-map", "preview-json", "working-directory"])
        self.assertNotIn("\noutputs:", self.text)

    def test_no_expression_reaches_the_shell(self):
        self.assertNotIn("${{", _run_body())

    def test_the_script_is_run_from_the_pinned_revision_in_isolated_mode(self):
        self.assertIn("SCRIPT: ${{ github.action_path }}/assert-no-hetzner-deletes.py", self.text)
        for line in _run_body().splitlines():
            if "$SCRIPT" in line:
                self.assertIn('python3 -I "$SCRIPT"', line)

    def test_the_step_stops_on_error(self):
        self.assertIn("set -eu", _run_body())
        self.assertIn("shell: bash", self.text)

    def test_the_action_names_no_tag_of_this_repo(self):
        self.assertNotRegex(self.text, r"github-workflows[^\n]*@v\d")


class SabotageTests(unittest.TestCase):
    """Break each control in a copy of this directory and require the named
    tests to go red there; the unmodified copy must be green. Skipped inside a
    broken copy's own run."""

    SCRIPT = "assert-no-hetzner-deletes.py"
    CASES = {
        "ops containing 'replace' no longer destructive": (
            SCRIPT,
            'DESTRUCTIVE_SUBSTRINGS = ("delete", "replace")',
            'DESTRUCTIVE_SUBSTRINGS = ("delete",)',
            {"DestructiveStepsTests.test_every_replace_flavoured_op_is_destructive"},
        ),
        "servers no longer protected": (
            SCRIPT,
            '    "hcloud:index/server:Server",\n',
            "",
            {"DestructiveStepsTests.test_delete_of_protected_type_is_found", "FixtureTests.test_a_plan_with_a_delete_exits_one_and_names_it"},
        ),
        "parent chain not split off": (
            SCRIPT,
            'return parts[1].rsplit("$", 1)[-1], parts[2]',
            "return parts[1], parts[2]",
            {"DestructiveStepsTests.test_component_child_is_protected_through_parent_chain", "SplitUrnTests.test_parent_chain_resolves_to_leaf_type"},
        ),
        "unreadable plan reported clean": (
            SCRIPT,
            '        print(f"::error::cannot read preview JSON: {error}")\n        return 1',
            '        print(f"::error::cannot read preview JSON: {error}")\n        return 0',
            {"MainTests.test_unreadable_plan_exits_one_not_zero", "MainTests.test_malformed_json_exits_one"},
        ),
        "a plan with no steps key reported clean": (
            SCRIPT,
            '        raise ValueError("no \'steps\' array in the preview JSON")',
            "        return []",
            {"DestructiveStepsTests.test_missing_steps_raises_instead_of_passing"},
        ),
        "unknown program directory waved through": (
            SCRIPT,
            "    if expected is None:\n",
            "    if expected is None:\n        return 0\n",
            {"VerifyCoverageTests.test_unknown_directory_fails_closed", "MainTests.test_self_test_passes"},
        ),
        "a mention counts as a declaration": (
            SCRIPT,
            'return re.compile(rf"\\bnew\\s+{re.escape(constructor)}\\s*\\(")',
            "return re.compile(rf\"{re.escape(constructor)}\")",
            {"VerifyCoverageTests.test_mention_in_comment_does_not_count", "MainTests.test_stale_coverage_exits_one"},
        ),
        "an empty constructor list accepted in the map": (
            SCRIPT,
            "not isinstance(constructors, list) or not constructors",
            "not isinstance(constructors, list)",
            {"LoadCoverageMapTests.test_anything_else_is_refused"},
        ),
        "the plan check dropped from the action": (
            "action.yml",
            '          if ! python3 -I "$SCRIPT" "$PREVIEW_JSON"; then',
            "          if false; then",
            {"ActionWiringTests.test_a_plan_with_a_delete_fails_and_prints_the_whole_plan"},
        ),
        "the coverage check dropped from the action": (
            "action.yml",
            '        python3 -I "$SCRIPT" --verify-coverage "$PROGRAM_DIR" --coverage-map "$COVERAGE_MAP"\n',
            "",
            {"ActionWiringTests.test_stale_coverage_fails_even_with_a_clean_plan"},
        ),
        "the coverage inputs made optional in the action": (
            "action.yml",
            'if [ -z "$PROGRAM_DIR" ] || [ -z "$COVERAGE_MAP" ]; then',
            "if false; then",
            {"ActionWiringTests.test_the_coverage_inputs_cannot_be_left_out"},
        ),
    }

    def run_copy(self, edit):
        with tempfile.TemporaryDirectory(prefix="hetzner-deletes-sabotage-") as tmp:
            copy = pathlib.Path(tmp, ".github", "actions", "assert-no-hetzner-deletes")
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__"))
            if edit:
                name, old, new, _ = edit
                target = copy / name
                text = target.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"the control moved, update the sabotage: {old!r}")
                target.write_text(text.replace(old, new), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(copy), "-p", "test_assert_no_hetzner_deletes.py", "-v"],
                capture_output=True,
                text=True,
                env={**os.environ, INSIDE_SABOTAGE: "1"},
                check=False,
            )

    @unittest.skipIf(os.environ.get(INSIDE_SABOTAGE), "running inside a sabotaged copy")
    def test_each_broken_control_turns_its_tests_red(self):
        for name, edit in self.CASES.items():
            with self.subTest(case=name):
                red = self.run_copy(edit)
                self.assertNotEqual(red.returncode, 0, f"{name} stayed green")
                failed = set(re.findall(r"^(?:FAIL|ERROR): (\w+) \(\S*?\.(\w+)\.\w+\)", red.stderr, re.MULTILINE))
                failed = {f"{cls}.{meth}" for meth, cls in failed}
                self.assertTrue(edit[3] <= failed, f"{name}: expected red {sorted(edit[3])}, got {sorted(failed)}")

    @unittest.skipIf(os.environ.get(INSIDE_SABOTAGE), "running inside a sabotaged copy")
    def test_the_unmodified_copy_is_green(self):
        green = self.run_copy(None)
        self.assertEqual(green.returncode, 0, green.stderr)


if __name__ == "__main__":
    unittest.main()
