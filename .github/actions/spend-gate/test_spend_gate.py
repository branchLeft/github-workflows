#!/usr/bin/env python3
"""Unit tests for spend-gate: the failure that matters is a FREE over a change that costs money.
Fixture shapes are those `pulumi preview --json` wrote for a throwaway local stack (the
create and error ones); update, replace and delete steps follow the engine source and are
labelled so where they are built."""

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
SCRIPT_ENV = "SPEND_GATE_SCRIPT"


def _load_module():
    """Import the script by path: its filename has hyphens. SPEND_GATE_SCRIPT points the
    tests at another copy of it, which is how the sabotage test breaks a copy and not this one."""
    path = pathlib.Path(os.environ.get(SCRIPT_ENV) or HERE / "spend-gate.py").resolve()
    spec = importlib.util.spec_from_file_location("spend_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gate = _load_module()

_STACK = "urn:pulumi:production::example::"
_ROOT = f"{_STACK}pulumi:pulumi:Stack::example-production"
_SERVER = "hcloud:index/server:Server"
_FIREWALL = "hcloud:index/firewall:Firewall"
_NETWORK = "hcloud:index/network:Network"
_STATE_KEYS = {"urn", "custom", "type", "inputs", "parent", "provider", "sourcePosition"}


def state_of(type_name, name):
    return {
        "urn": f"{_STACK}{type_name}::{name}",
        "custom": True,
        "type": type_name,
        "inputs": {"name": name},
        "provider": f"{_STACK}pulumi:providers:hcloud::default::ID",
    }


def same_root():
    base = {"urn": _ROOT, "custom": False, "type": "pulumi:pulumi:Stack"}
    return {
        "op": "same",
        "urn": _ROOT,
        "oldState": {**base, "id": "ID", "outputs": {"k": "v"}, "sourcePosition": "a#1"},
        "newState": {**base, "sourcePosition": "a#9"},
    }


def step(op, type_name, name="one"):
    """A resource step in the shape of the engine source for ops that were not observed.
    The type is the full type string, which for a nested resource contains `$`."""
    urn = f"{_STACK}{type_name}::{name}"
    entry = {"op": op, "urn": urn, "provider": f"{_STACK}pulumi:providers:hcloud::default::ID"}
    leaf = type_name.rsplit("$", 1)[-1]
    if op != "delete":
        entry["newState"] = state_of(leaf, name)
    if op in ("update", "replace", "delete", "delete-replaced"):
        entry["oldState"] = {**state_of(leaf, name), "id": "1"}
    if op == "update":
        entry["detailedDiff"] = {"name": {"kind": "update", "inputDiff": True}}
    return entry


def preview(*steps, summary=None, **extra):
    doc = {"steps": [same_root(), *steps], "duration": 1}
    doc["changeSummary"] = summary if summary is not None else {"same": 2, "update": 1}
    doc.update(extra)
    return doc


class GateCase(unittest.TestCase):
    def setUp(self):
        self.dir = pathlib.Path(tempfile.mkdtemp(prefix="spend-gate-"))
        self.addCleanup(shutil.rmtree, self.dir, True)

    def write(self, name, content):
        path = self.dir / name
        path.write_text(content if isinstance(content, str) else json.dumps(content), encoding="utf-8")
        return str(path)

    def judge(self, doc):
        return gate.judge(self.write("preview.json", doc))

    def assertPaid(self, result):
        self.assertEqual(result.verdict, "PAID", result)

    def assertFree(self, result):
        self.assertEqual(result.verdict, "FREE", result)


class FixtureTests(GateCase):
    def check(self, name, verdict):
        result = gate.judge(str(FIXTURES / name))
        self.assertEqual(result.verdict, verdict, result)
        return result

    def test_no_changes_is_free(self):
        self.check("preview-no-changes.json", "FREE")

    def test_firewall_rule_change_is_free(self):
        self.check("preview-firewall-rule-change.json", "FREE")

    def test_server_create_is_paid(self):
        result = self.check("preview-server-create.json", "PAID")
        self.assertIn("hcloud:index/server:Server", result.reason)

    def test_server_type_change_is_paid(self):
        result = self.check("preview-server-type-change.json", "PAID")
        self.assertIn("hcloud:index/server:Server", result.reason)

    def test_unknown_type_is_paid(self):
        result = self.check("preview-unknown-type.json", "PAID")
        self.assertIn("example:index/thing:Thing", result.reason)

    def test_malformed_json_is_paid(self):
        self.check("preview-malformed.json", "PAID")

    def test_error_diagnostic_is_paid_though_only_the_free_stack_step_is_listed(self):
        # A preview that errors lists only the stack step, a free type: only the
        # diagnostic stops it being FREE.
        doc = json.loads((FIXTURES / "preview-error-diagnostic.json").read_text(encoding="utf-8"))
        self.assertEqual([s["urn"].split("::")[2] for s in doc["steps"]], ["pulumi:pulumi:Stack"])
        result = self.check("preview-error-diagnostic.json", "PAID")
        self.assertIn("preview-diagnostics", result.reason)


class OpTests(GateCase):
    def test_delete_of_a_server_is_paid(self):
        self.assertPaid(self.judge(preview(step("delete", _SERVER), summary={"same": 1, "delete": 1})))

    def test_delete_of_a_firewall_is_free(self):
        self.assertFree(self.judge(preview(step("delete", _FIREWALL), summary={"same": 1, "delete": 1})))

    def test_create_of_a_network_is_free(self):
        self.assertFree(self.judge(preview(step("create", _NETWORK), summary={"same": 1, "create": 1})))

    def test_replace_of_a_paid_type_is_paid(self):
        steps = [step("create-replacement", _SERVER), step("replace", _SERVER), step("delete-replaced", _SERVER)]
        self.assertPaid(self.judge(preview(*steps, summary={"same": 1, "replace": 1})))

    def test_replace_of_a_free_type_is_free(self):
        steps = [step("create-replacement", _FIREWALL), step("replace", _FIREWALL), step("delete-replaced", _FIREWALL)]
        self.assertFree(self.judge(preview(*steps, summary={"same": 1, "replace": 1})))

    def test_an_unknown_op_is_paid(self):
        for op in ("refresh", "read", "discard", "remove-pending-replace", "teleport"):
            with self.subTest(op=op):
                self.assertPaid(self.judge(preview(step(op, _FIREWALL), summary={"same": 1, op: 1})))

    def test_a_resource_under_an_unknown_component_is_paid(self):
        nested = "example:index/component:Component$hcloud:index/firewall:Firewall"
        self.assertPaid(self.judge(preview(step("create", nested), summary={"same": 1, "create": 1})))

    def test_a_provider_step_is_free(self):
        provider = "pulumi:providers:hcloud"
        self.assertFree(self.judge(preview(step("update", provider), summary={"same": 1, "update": 1})))

    def test_a_provider_with_an_extra_segment_is_paid(self):
        self.assertPaid(self.judge(preview(step("create", "pulumi:providers:hcloud:extra"), summary={"same": 1, "create": 1})))

    def test_a_same_step_that_carries_a_difference_is_judged_by_its_type(self):
        server = step("same", _SERVER)
        server["oldState"] = {**state_of(_SERVER, "one"), "id": "1"}
        server["newState"] = state_of(_SERVER, "one")
        server["detailedDiff"] = {"serverType": {"kind": "update", "inputDiff": True}}
        self.assertPaid(self.judge(preview(server, summary={"same": 1, "update": 1})))

    def test_an_unchanged_paid_resource_listed_as_same_is_free(self):
        server = step("same", _SERVER)
        server["oldState"] = {**state_of(_SERVER, "one"), "id": "1"}
        server["newState"] = state_of(_SERVER, "one")
        self.assertFree(self.judge(preview(server, summary={"same": 2})))


class FailClosedTests(GateCase):
    def test_an_empty_file_is_paid(self):
        self.assertPaid(self.judge(""))

    def test_a_missing_file_is_paid(self):
        self.assertPaid(gate.judge(str(self.dir / "absent.json")))

    def test_an_empty_steps_list_is_paid(self):
        self.assertPaid(self.judge({"steps": [], "duration": 1, "changeSummary": {"same": 1}}))

    def test_a_missing_steps_list_is_paid(self):
        self.assertPaid(self.judge({"duration": 1, "changeSummary": {"same": 1}}))

    def test_a_non_object_top_level_is_paid(self):
        for text in ("[]", "null", '"FREE"', "7", "true"):
            with self.subTest(text=text):
                self.assertPaid(self.judge(text))

    def test_a_summary_counting_more_than_is_listed_is_paid(self):
        # The one listed step is a firewall; the summary counts a second change that is not listed.
        self.assertPaid(self.judge(preview(step("update", _FIREWALL), summary={"same": 1, "update": 2})))

    def test_a_summary_counting_nothing_beside_a_listed_change_is_paid(self):
        self.assertPaid(self.judge(preview(step("update", _FIREWALL), summary={"same": 2})))

    def test_a_missing_or_odd_summary_is_paid(self):
        doc = preview(step("update", _FIREWALL))
        for summary in (None, [], {"update": -1}, {"update": True}, {"update": "1"}, {"update": 1.5}):
            with self.subTest(summary=summary):
                changed = {**doc, "changeSummary": summary}
                self.assertPaid(self.judge(changed))
        del doc["changeSummary"]
        self.assertPaid(self.judge(doc))

    def test_a_state_type_that_disagrees_with_the_urn_is_paid(self):
        for key in ("newState", "oldState"):
            with self.subTest(key=key):
                entry = step("update", _FIREWALL)
                entry[key]["type"] = _SERVER
                self.assertPaid(self.judge(preview(entry)))

    def test_a_state_that_is_not_an_object_is_paid(self):
        entry = step("update", _FIREWALL)
        entry["newState"] = "hcloud:index/firewall:Firewall"
        self.assertPaid(self.judge(preview(entry)))

    def test_an_unknown_top_level_or_step_key_is_paid(self):
        self.assertPaid(self.judge(preview(step("update", _FIREWALL), costEstimate=0)))
        entry = step("update", _FIREWALL)
        entry["price"] = 0
        self.assertPaid(self.judge(preview(entry)))

    def test_diagnostics_of_any_kind_are_paid(self):
        for diagnostics in ([{"severity": "warning", "message": "m"}], [{"severity": "info"}], "none", {}, 0):
            with self.subTest(diagnostics=diagnostics):
                self.assertPaid(self.judge(preview(step("update", _FIREWALL), diagnostics=diagnostics)))

    def test_empty_or_null_diagnostics_are_free(self):
        for diagnostics in (None, []):
            with self.subTest(diagnostics=diagnostics):
                self.assertFree(self.judge(preview(step("update", _FIREWALL), diagnostics=diagnostics)))

    def test_maybe_corrupt_is_paid(self):
        self.assertPaid(self.judge(preview(step("update", _FIREWALL), maybeCorrupt=True)))
        self.assertFree(self.judge(preview(step("update", _FIREWALL), maybeCorrupt=False)))

    def test_a_malformed_urn_is_paid(self):
        for urn in ("not-a-urn", 7, None, f"{_STACK}hcloud:index/firewall:Firewall::a::b"):
            with self.subTest(urn=urn):
                entry = step("update", _FIREWALL)
                entry["urn"] = urn
                self.assertPaid(self.judge(preview(entry)))

    def test_json_hazards_are_paid(self):
        good = json.dumps(preview(step("update", _FIREWALL)))
        duplicate = good.replace('"duration": 1', '"duration": 1, "duration": 2')
        nan = good.replace('"duration": 1', '"duration": NaN')
        deep = "[" * 2000 + "]" * 2000
        for name, text in (("duplicate", duplicate), ("nan", nan), ("deep", deep)):
            with self.subTest(name=name):
                self.assertPaid(self.judge(text))

    def test_any_exception_is_paid(self):
        original = gate.shared._load
        gate.shared._load = lambda *_: (_ for _ in ()).throw(RuntimeError("boom"))
        self.addCleanup(setattr, gate.shared, "_load", original)
        result = self.judge(preview(step("update", _FIREWALL)))
        self.assertPaid(result)
        self.assertIn("internal-error", result.reason)


class ReasonTests(GateCase):
    def test_a_type_name_cannot_forge_an_output_line(self):
        forged = f"{_FIREWALL}\nverdict=FREE"
        result = self.judge(preview(step("create", forged), summary={"same": 1, "create": 1}))
        self.assertPaid(result)
        self.assertNotIn("\n", result.reason)
        self.assertNotIn("verdict=FREE", result.reason)

    def test_a_control_character_in_a_reason_is_flattened(self):
        self.assertEqual(gate._one_line("a\nverdict=FREE\r\x00b"), "a verdict=FREE  b")

    def test_a_long_run_of_paid_types_is_capped(self):
        steps = [step("create", f"example:index/t{n}:T{n}") for n in range(60)]
        result = self.judge(preview(*steps, summary={"same": 1, "create": 60}))
        self.assertPaid(result)
        self.assertIn("more", result.reason)


class AllowlistTests(unittest.TestCase):
    def test_the_free_list_is_exactly_the_ruled_one(self):
        # Widening the list means editing this test: the edit is the review.
        self.assertEqual(
            gate.FREE_TYPES,
            {
                "pulumi:pulumi:Stack",
                "hcloud:index/network:Network",
                "hcloud:index/networkSubnet:NetworkSubnet",
                "hcloud:index/networkRoute:NetworkRoute",
                "hcloud:index/firewall:Firewall",
                "hcloud:index/firewallAttachment:FirewallAttachment",
                "hcloud:index/sshKey:SshKey",
                "hcloud:index/placementGroup:PlacementGroup",
            },
        )

    def test_the_only_pattern_beside_the_list_is_the_provider_type(self):
        free = [t for t in ("pulumi:providers:hcloud", "pulumi:providers:random") if gate._is_free_type(t)]
        self.assertEqual(len(free), 2)
        for token in ("pulumi:providers:", "pulumi:providers:a:b", "pulumi:pulumi:Provider", "pulumi:providers", ""):
            with self.subTest(token=token):
                self.assertFalse(gate._is_free_type(token))

    def test_the_paid_types_are_not_free(self):
        paid = (
            "server serverNetwork volume volumeAttachment loadBalancer loadBalancerNetwork loadBalancerService"
            " loadBalancerTarget primaryIp floatingIp floatingIpAssignment snapshot storageBox zone zoneRecord"
            " zoneRrset certificate managedCertificate uploadedCertificate rdns"
        ).split()
        for name in paid:
            with self.subTest(name=name):
                self.assertFalse(gate._is_free_type(f"hcloud:index/{name}:{name[0].upper()}{name[1:]}"))
        self.assertFalse(gate._is_free_type("aws:s3/bucket:Bucket"))

    def test_a_free_name_in_another_package_is_not_free(self):
        self.assertFalse(gate._is_free_type("other:index/firewall:Firewall"))
        self.assertFalse(gate._is_free_type("hcloud:index/firewall:Firewall$hcloud:index/server:Server"))


class MainTests(GateCase):
    def run_main(self, doc, outputs=True):
        argv = ["spend-gate.py", self.write("preview.json", doc)]
        if outputs:
            argv += ["--github-output", str(self.dir / "output"), "--github-summary", str(self.dir / "summary")]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = gate.main(argv)
        read = lambda name: (self.dir / name).read_text(encoding="utf-8") if (self.dir / name).is_file() else ""  # noqa: E731
        return code, buffer.getvalue(), read("output"), read("summary")

    def test_a_free_preview_writes_the_reason_then_the_verdict_and_the_summary(self):
        code, stdout, out, summary = self.run_main(preview(step("update", _FIREWALL)))
        self.assertEqual(code, 0)
        lines = out.splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].startswith("reason="))
        self.assertEqual(lines[1], "verdict=FREE")
        self.assertEqual(summary.splitlines()[0], f"Spend gate: FREE: {lines[0][len('reason='):]}")
        self.assertIn("verdict=FREE", stdout)

    def test_a_paid_preview_writes_paid(self):
        code, _, out, summary = self.run_main(preview(step("update", _SERVER)))
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1], "verdict=PAID")
        self.assertTrue(summary.startswith("Spend gate: PAID: "))

    def test_an_unreadable_preview_still_exits_zero_with_paid(self):
        code, _, out, _ = self.run_main("{not json")
        self.assertEqual(code, 0)
        self.assertEqual(out.splitlines()[-1], "verdict=PAID")

    def test_output_is_appended_to_what_is_there(self):
        (self.dir / "output").write_text("earlier=1\n", encoding="utf-8")
        _, _, out, _ = self.run_main(preview(step("update", _FIREWALL)))
        self.assertTrue(out.startswith("earlier=1\nreason="))

    def test_a_bad_output_path_is_refused_before_anything_is_written(self):
        (self.dir / "output").mkdir()
        code, stdout, _, summary = self.run_main(preview(step("update", _FIREWALL)))
        self.assertEqual(code, 1)
        self.assertIn("::error::", stdout)
        self.assertEqual(summary, "")

    def test_the_same_file_for_both_outputs_is_refused(self):
        argv = ["spend-gate.py", self.write("preview.json", preview()), "--github-output", str(self.dir / "o"), "--github-summary", str(self.dir / "o")]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(argv), 1)
        self.assertFalse((self.dir / "o").exists())

    def test_a_blank_output_path_is_refused(self):
        argv = ["spend-gate.py", self.write("preview.json", preview()), "--github-output", " ", "--github-summary", str(self.dir / "s")]
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(argv), 1)

    def test_a_failed_write_leaves_the_verdict_unset(self):
        for unwritable in ("output", "summary"):
            with self.subTest(unwritable=unwritable):
                paths = {"output": str(self.dir / "o"), "summary": str(self.dir / "s")}
                paths[unwritable] = str(self.dir / "no-such-dir" / unwritable)
                argv = ["spend-gate.py", self.write("preview.json", preview()), "--github-output", paths["output"], "--github-summary", paths["summary"]]
                with contextlib.redirect_stdout(io.StringIO()) as buffer:
                    code = gate.main(argv)
                self.assertEqual(code, 1)
                self.assertIn("cannot write", buffer.getvalue())
                output = pathlib.Path(paths["output"])
                self.assertFalse(output.exists() and "verdict=" in output.read_text(encoding="utf-8"))
                for name in ("o", "s"):
                    (self.dir / name).unlink(missing_ok=True)


class ScriptTests(unittest.TestCase):
    def run_script(self, *args):
        return subprocess.run(
            [sys.executable, "-I", str(pathlib.Path(gate.__file__).resolve()), *args], capture_output=True, text=True, check=False
        )

    def test_the_script_runs_in_isolated_mode_and_finds_the_shared_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            out, summary = pathlib.Path(tmp, "o"), pathlib.Path(tmp, "s")
            done = self.run_script(str(FIXTURES / "preview-no-changes.json"), "--github-output", str(out), "--github-summary", str(summary))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertEqual(out.read_text(encoding="utf-8").splitlines()[-1], "verdict=FREE")

    def test_a_missing_shared_parser_fails_the_step_and_writes_no_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            lonely = pathlib.Path(tmp, "spend-gate")
            lonely.mkdir()
            shutil.copy(pathlib.Path(gate.__file__), lonely / "spend-gate.py")
            out = pathlib.Path(tmp, "o")
            done = subprocess.run(
                [sys.executable, "-I", str(lonely / "spend-gate.py"), str(FIXTURES / "preview-no-changes.json"), "--github-output", str(out), "--github-summary", str(pathlib.Path(tmp, "s"))],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(done.returncode, 0)
            self.assertFalse(out.exists())


class SabotageTests(unittest.TestCase):
    """The control is the allowlist check. Break it in a copy and the PAID tests must go
    red; the unmodified copy must be green. Skipped inside the broken copy's own run."""

    CONTROL = "    return type_name in FREE_TYPES or shared._is_provider_type(type_name)\n"
    BROKEN = "    return True\n"
    MUST_FAIL = {
        "test_server_create_is_paid",
        "test_server_type_change_is_paid",
        "test_unknown_type_is_paid",
        "test_delete_of_a_server_is_paid",
        "test_replace_of_a_paid_type_is_paid",
        "test_a_resource_under_an_unknown_component_is_paid",
        "test_the_paid_types_are_not_free",
    }

    def run_copy(self, broken):
        with tempfile.TemporaryDirectory(prefix="spend-gate-sabotage-") as tmp:
            actions = pathlib.Path(tmp, ".github", "actions")
            for name in ("spend-gate", "plan-has-changes"):
                shutil.copytree(HERE.parent / name, actions / name, ignore=shutil.ignore_patterns("__pycache__"))
            script = actions / "spend-gate" / "spend-gate.py"
            if broken:
                text = script.read_text(encoding="utf-8")
                self.assertEqual(text.count(self.CONTROL), 1, "the allowlist check moved: update the sabotage")
                script.write_text(text.replace(self.CONTROL, self.BROKEN), encoding="utf-8")
            env = {**os.environ, SCRIPT_ENV: str(script)}
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(actions / "spend-gate"), "-p", "test_spend_gate.py", "-v"],
                capture_output=True,
                text=True,
                env=env,
                check=False,
            )

    @unittest.skipIf(os.environ.get(SCRIPT_ENV), "running inside a sabotaged copy")
    def test_an_allowlist_that_always_passes_turns_the_paid_tests_red(self):
        red = self.run_copy(broken=True)
        self.assertNotEqual(red.returncode, 0, red.stderr)
        failed = set(re.findall(r"^(?:FAIL|ERROR): (\w+) ", red.stderr, re.MULTILINE))
        self.assertTrue(self.MUST_FAIL <= failed, f"expected red, got {sorted(failed)}")

    @unittest.skipIf(os.environ.get(SCRIPT_ENV), "running inside a sabotaged copy")
    def test_the_unmodified_copy_is_green(self):
        green = self.run_copy(broken=False)
        self.assertEqual(green.returncode, 0, green.stderr)


if __name__ == "__main__":
    unittest.main()
