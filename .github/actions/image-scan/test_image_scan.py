#!/usr/bin/env python3
"""Unit tests for the image scan gate. The failure that matters is a PASS over a critical or high
finding, so most cases plant one. Fixtures: grype-lockfile.json is a Grype 0.115 report of a
lockfile with the fields the gate does not read trimmed away; every other report is built here."""

import contextlib
import datetime
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SCRIPT_ENV = "IMAGE_SCAN_SCRIPT"


def _load_module():
    """Import the script by path (its name has a hyphen). IMAGE_SCAN_SCRIPT points the tests at
    another copy, which is how the sabotage tests break a copy and not this one."""
    path = pathlib.Path(os.environ.get(SCRIPT_ENV) or HERE / "image-scan.py").resolve()
    spec = importlib.util.spec_from_file_location("image_scan", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["image_scan"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_module()

TODAY = datetime.date(2026, 11, 9)
DIGEST = "a" * 64
IMAGE = "ghcr.io/example/app"
EXEMPTIONS = "exemptions.json"


def match(vuln, severity, package="openssl", fix_state="not-fixed", fix_versions=(), related=(), version="3.0.1", path="/usr/lib/x"):
    return {
        "vulnerability": {"id": vuln, "severity": severity, "fix": {"versions": list(fix_versions), "state": fix_state}},
        "relatedVulnerabilities": [{"id": item} for item in related],
        "artifact": {"name": package, "version": version, "type": "deb", "locations": [{"path": path}]},
    }


def exemption(vuln="CVE-2026-0001", package="openssl", **overrides):
    entry = {
        "vulnerability": vuln,
        "package": package,
        "reason": "the vulnerable function is never called by this image",
        "granted": "2026-11-01",
        "expires": "2026-11-30",
    }
    entry.update(overrides)
    return entry


class Workspace:
    """A temporary directory holding a Grype report, an SBOM and optionally an exemptions file."""

    def __init__(self, matches, exemptions=None, raw_exemptions=None, packages=1):
        self.dir = tempfile.TemporaryDirectory(prefix="image-scan-test-")
        self.root = pathlib.Path(self.dir.name)
        self.grype = self.root / "grype.json"
        self.grype.write_text(json.dumps({"matches": matches}), encoding="utf-8")
        self.sbom = self.root / "sbom.json"
        self.sbom.write_text(json.dumps({"artifacts": [{"name": f"p{i}"} for i in range(packages)]}), encoding="utf-8")
        if exemptions is not None:
            raw_exemptions = json.dumps({"exemptions": exemptions})
        if raw_exemptions is not None:
            (self.root / EXEMPTIONS).write_text(raw_exemptions, encoding="utf-8")

    def argv(self, *extra):
        return [
            "evaluate", "--grype-json", str(self.grype), "--sbom", str(self.sbom), "--image", IMAGE,
            "--root", str(self.root), "--exemptions", EXEMPTIONS, "--today", TODAY.isoformat(),
            "--findings-out", str(self.root / "findings.jsonl"), "--github-output", str(self.root / "out.txt"),
            "--github-summary", str(self.root / "summary.md"), *extra,
        ]

    def run(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = gate.main(self.argv(*extra))
        return code, out.getvalue()

    def outputs(self):
        pairs = (line.split("=", 1) for line in (self.root / "out.txt").read_text(encoding="utf-8").splitlines())
        return dict(pairs)

    def findings(self):
        return [json.loads(line) for line in (self.root / "findings.jsonl").read_text(encoding="utf-8").splitlines()]

    def close(self):
        self.dir.cleanup()


class GateTestCase(unittest.TestCase):
    def workspace(self, *args, **kwargs):
        space = Workspace(*args, **kwargs)
        self.addCleanup(space.close)
        return space

    def levels(self, space):
        return [(f["clause"], f["level"]) for f in space.findings()]


class ImageReferenceTests(unittest.TestCase):
    def test_a_digest_pinned_reference_is_accepted(self):
        for ref in (
            f"ghcr.io/branchleft/website@sha256:{DIGEST}",
            f"alpine@sha256:{DIGEST}",
            f"localhost:5000/team/app@sha256:{DIGEST}",
            f"registry.example.com/a_b/c__d/e-f.g@sha256:{DIGEST}",
        ):
            with self.subTest(ref=ref):
                self.assertEqual(gate.parse_image(ref)[1], DIGEST)

    def test_a_tag_or_a_malformed_reference_is_refused(self):
        for ref in (
            "ghcr.io/branchleft/website:latest",
            f"ghcr.io/branchleft/website:1.2@sha256:{DIGEST}",
            f"ghcr.io/branchleft/website@sha256:{DIGEST[:-1]}",
            f"ghcr.io/branchleft/website@sha256:{'A' * 64}",
            f"ghcr.io/branchleft/website@sha512:{DIGEST}",
            f"ghcr.io/Branchleft/website@sha256:{DIGEST}",
            f"registry:ghcr.io/branchleft/website@sha256:{DIGEST}",
            f"ghcr.io/x/y@sha256:{DIGEST}; echo hi",
            f"ghcr.io/x/y@sha256:{DIGEST}\nextra",
            f"ghcr.io/x/y@sha256:{DIGEST}\n",
            f" ghcr.io/x/y@sha256:{DIGEST}",
            f"@sha256:{DIGEST}",
            "",
        ):
            with self.subTest(ref=ref), self.assertRaises(gate.ScanError):
                gate.parse_image(ref)

    def test_the_registry_is_read_from_the_name(self):
        self.assertEqual(gate.registry_of("ghcr.io/a/b"), "ghcr.io")
        self.assertEqual(gate.registry_of("localhost:5000/a/b"), "localhost:5000")
        self.assertEqual(gate.registry_of("alpine"), "docker.io")
        self.assertEqual(gate.registry_of("library/alpine"), "docker.io")

    def test_check_input_writes_name_registry_digest_and_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = pathlib.Path(tmp, "out.txt")
            with contextlib.redirect_stdout(io.StringIO()):
                code = gate.main(["check-input", "--image", f"ghcr.io/a/b@sha256:{DIGEST}", "--github-output", str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(
                out.read_text(encoding="utf-8").splitlines(),
                ["name=ghcr.io/a/b", "registry=ghcr.io", f"digest={DIGEST}", f"artifact=image-scan-{DIGEST[:12]}"],
            )

    def test_a_platform_is_a_plain_specifier_or_nothing(self):
        for platform in ("", "linux", "arm64", "linux/arm64", "linux/arm64/v8"):
            with self.subTest(platform=platform):
                self.assertEqual(gate.parse_platform(platform), platform)
        for platform in ("--evil", "linux/", "/arm64", "Linux/arm64", "linux/arm64 x", "linux/arm64\n", "a/b/c/d"):
            with self.subTest(platform=platform), self.assertRaises(gate.ScanError):
                gate.parse_platform(platform)

    def test_check_input_refuses_a_bad_platform(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(["check-input", "--image", f"ghcr.io/a/b@sha256:{DIGEST}", "--platform=--evil"]), 2)

    def test_check_input_exits_non_zero_on_a_tag(self):
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(gate.main(["check-input", "--image", "ghcr.io/a/b:latest"]), 2)


class ThresholdTests(GateTestCase):
    def test_a_high_finding_blocks(self):
        space = self.workspace([match("CVE-1", "High", fix_state="fixed", fix_versions=["1.1"])])
        code, _ = space.run()
        self.assertEqual(code, 1)
        self.assertEqual(space.outputs()["verdict"], "BLOCK")
        self.assertEqual(space.outputs()["blocking"], "1")

    def test_a_critical_finding_blocks(self):
        space = self.workspace([match("CVE-1", "Critical")])
        code, _ = space.run()
        self.assertEqual(code, 1)
        self.assertEqual(space.outputs()["critical"], "1")

    def test_medium_low_negligible_and_unknown_do_not_block(self):
        space = self.workspace([match(f"CVE-{i}", sev) for i, sev in enumerate(("Medium", "Low", "Negligible", "Unknown"))])
        code, _ = space.run()
        self.assertEqual(code, 0)
        out = space.outputs()
        self.assertEqual(out["verdict"], "PASS")
        self.assertEqual((out["medium"], out["low"], out["negligible"], out["unknown"]), ("1", "1", "1", "1"))

    def test_a_severity_grype_does_not_emit_fails_closed_and_never_passes(self):
        for severity in (" High", "High ", "HIGH", "high", "Surprising", "", None, 5, ["High"]):
            with self.subTest(severity=severity):
                space = self.workspace([match("CVE-1", severity)])
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(gate.main(space.argv()), 2)

    def test_the_threshold_is_critical_and_high_and_not_an_option(self):
        self.assertEqual(gate.BLOCKING, ("critical", "high"))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            gate.main(["evaluate", "--grype-json", "a", "--sbom", "b", "--image", "c", "--fail-on", "critical"])

    def test_counts_are_per_severity_and_a_package_found_twice_counts_once(self):
        space = self.workspace([
            match("CVE-1", "High", path="/a"), match("CVE-1", "High", path="/b"),
            match("CVE-2", "Critical"), match("CVE-3", "Medium"), match("CVE-4", "Low"),
        ])
        space.run()
        out = space.outputs()
        self.assertEqual((out["critical"], out["high"], out["medium"], out["low"]), ("1", "1", "1", "1"))
        self.assertEqual(out["blocking"], "2")

    def test_a_report_of_a_real_lockfile_scan_blocks_on_its_two_highs(self):
        space = self.workspace(json.loads((FIXTURES / "grype-lockfile.json").read_text(encoding="utf-8"))["matches"])
        code, _ = space.run()
        self.assertEqual(code, 1)
        out = space.outputs()
        self.assertEqual((out["high"], out["medium"], out["blocking"]), ("2", "1", "2"))

    def test_a_clean_report_passes(self):
        space = self.workspace([])
        code, _ = space.run()
        self.assertEqual(code, 0)
        self.assertEqual(space.outputs()["verdict"], "PASS")

    def test_report_only_counts_and_reports_but_exits_zero(self):
        space = self.workspace([match("CVE-1", "Critical")])
        code, _ = space.run("--report-only")
        self.assertEqual(code, 0)
        self.assertEqual(space.outputs()["critical"], "1")
        self.assertEqual(space.outputs()["verdict"], "BLOCK")

    def test_a_blocked_finding_names_the_fix_or_the_exemption_route(self):
        space = self.workspace([match("CVE-1", "High", fix_state="fixed", fix_versions=["1.2.3"]), match("CVE-2", "High")])
        space.run()
        messages = [f["message"] for f in space.findings()]
        self.assertTrue(any("fixed in 1.2.3" in m for m in messages))
        self.assertTrue(any("record an exemption (SEC-5)" in m for m in messages))


class DuplicateMatchTests(GateTestCase):
    NO_FIX = staticmethod(lambda: match("CVE-2026-0001", "High", fix_state="not-fixed"))
    FIXABLE = staticmethod(lambda: match("CVE-2026-0001", "High", fix_state="fixed", fix_versions=["3.0.2"]))

    def test_a_vulnerability_with_any_fix_is_fixable_in_either_report_order(self):
        for order in ((self.NO_FIX, self.FIXABLE), (self.FIXABLE, self.NO_FIX)):
            with self.subTest(first=order[0] is self.NO_FIX):
                space = self.workspace([item() for item in order], [exemption()])
                code, _ = space.run()
                self.assertEqual(code, 1)
                self.assertEqual(space.outputs()["blocking"], "1")
                self.assertEqual(space.outputs()["exempted"], "0")

    def test_two_reports_of_one_vulnerability_count_once_at_the_highest_severity(self):
        space = self.workspace([match("CVE-1", "Medium"), match("CVE-1", "Critical"), match("CVE-1", "High")])
        space.run()
        out = space.outputs()
        self.assertEqual((out["critical"], out["high"], out["medium"]), ("1", "0", "0"))


class FailClosedTests(GateTestCase):
    def run_with(self, space):
        with contextlib.redirect_stdout(io.StringIO()):
            return gate.main(space.argv())

    def test_an_unreadable_report_fails(self):
        space = self.workspace([])
        space.grype.write_text("not json", encoding="utf-8")
        self.assertEqual(self.run_with(space), 2)

    def test_a_report_without_matches_fails(self):
        space = self.workspace([])
        space.grype.write_text('{"descriptor": {}}', encoding="utf-8")
        self.assertEqual(self.run_with(space), 2)

    def test_a_match_of_the_wrong_shape_fails(self):
        space = self.workspace([{"vulnerability": {"id": "CVE-1"}}])
        self.assertEqual(self.run_with(space), 2)

    def test_a_missing_report_fails(self):
        space = self.workspace([])
        space.grype.unlink()
        self.assertEqual(self.run_with(space), 2)

    def test_an_unreadable_sbom_fails(self):
        space = self.workspace([])
        space.sbom.write_text('{"nothing": 1}', encoding="utf-8")
        self.assertEqual(self.run_with(space), 2)

    def test_an_sbom_with_no_packages_fails_unless_empty_is_allowed(self):
        space = self.workspace([], packages=0)
        code, _ = space.run()
        self.assertEqual(code, 1)
        self.assertIn(("CON-11", "error"), self.levels(space))
        self.assertEqual(space.outputs()["verdict"], "BLOCK")

    def test_an_explicitly_allowed_empty_sbom_passes_with_a_warning(self):
        space = self.workspace([], packages=0)
        code, _ = space.run("--allow-empty-sbom")
        self.assertEqual(code, 0)
        self.assertIn(("CON-11", "warning"), self.levels(space))


class ExemptionTests(GateTestCase):
    def blocked_high(self, **kwargs):
        return match("CVE-2026-0001", "High", **kwargs)

    def test_a_valid_exemption_lets_a_high_with_no_fix_pass(self):
        space = self.workspace([self.blocked_high()], [exemption()])
        code, _ = space.run()
        self.assertEqual(code, 0)
        self.assertEqual(space.outputs()["exempted"], "1")
        self.assertEqual(space.outputs()["blocking"], "0")
        self.assertIn(("SEC-4", "exempt"), self.levels(space))

    def test_the_counts_still_show_an_exempt_finding(self):
        space = self.workspace([self.blocked_high()], [exemption()])
        space.run()
        self.assertEqual(space.outputs()["high"], "1")

    def test_wont_fix_and_unknown_fix_states_are_exemptable(self):
        for state in ("wont-fix", "unknown", "not-fixed"):
            with self.subTest(state=state):
                space = self.workspace([self.blocked_high(fix_state=state)], [exemption()])
                code, _ = space.run()
                self.assertEqual(code, 0)

    def test_an_exemption_does_not_cover_a_finding_that_has_a_fix(self):
        space = self.workspace([self.blocked_high(fix_state="fixed", fix_versions=["3.0.2"])], [exemption()])
        code, _ = space.run()
        self.assertEqual(code, 1)
        self.assertEqual(space.outputs()["blocking"], "1")
        self.assertIn(("SEC-5", "warning"), self.levels(space))

    def test_an_expired_exemption_fails_the_job_and_covers_nothing(self):
        space = self.workspace([self.blocked_high()], [exemption(expires="2026-11-08", granted="2026-10-20")])
        code, _ = space.run()
        self.assertEqual(code, 1)
        self.assertEqual(space.outputs()["blocking"], "1")
        self.assertIn(("SEC-5", "error"), self.levels(space))

    def test_an_exemption_expiring_today_still_holds(self):
        space = self.workspace([self.blocked_high()], [exemption(expires="2026-11-09", granted="2026-10-20")])
        code, _ = space.run()
        self.assertEqual(code, 0)

    def test_an_expired_exemption_fails_even_when_it_covers_no_finding(self):
        space = self.workspace([], [exemption(expires="2026-11-08", granted="2026-10-20")])
        code, _ = space.run()
        self.assertEqual(code, 1)

    def test_an_exemption_without_a_reason_is_refused(self):
        for reason in ("", "   "):
            with self.subTest(reason=reason):
                space = self.workspace([self.blocked_high()], [exemption(reason=reason)])
                code, _ = space.run()
                self.assertEqual(code, 1)
                self.assertEqual(space.outputs()["blocking"], "1")
        entry = exemption()
        del entry["reason"]
        self.assertEqual(self.workspace([self.blocked_high()], [entry]).run()[0], 1)

    def test_an_exemption_longer_than_thirty_days_is_refused(self):
        too_long = self.workspace([self.blocked_high()], [exemption(granted="2026-11-01", expires="2026-12-02")])
        self.assertEqual(too_long.run()[0], 1)
        exactly = self.workspace([self.blocked_high()], [exemption(granted="2026-11-01", expires="2026-12-01")])
        self.assertEqual(exactly.run()[0], 0)

    def test_a_dateless_or_future_exemption_is_refused(self):
        for overrides in ({"granted": "2026-12-01", "expires": "2026-12-20"}, {"expires": "30 November"}, {"granted": "2026-02-30"}, {"expires": 20261130}):
            with self.subTest(overrides=overrides):
                space = self.workspace([self.blocked_high()], [exemption(**overrides)])
                self.assertEqual(space.run()[0], 1)

    def test_an_unknown_key_is_refused(self):
        space = self.workspace([self.blocked_high()], [exemption(severity="low")])
        self.assertEqual(space.run()[0], 1)

    def test_a_critical_exemption_needs_the_approval_named(self):
        entry = exemption()
        bare = self.workspace([match("CVE-2026-0001", "Critical")], [entry])
        self.assertEqual(bare.run()[0], 1)
        self.assertIn(("SEC-5", "error"), self.levels(bare))
        approved = self.workspace([match("CVE-2026-0001", "Critical")], [exemption(**{"approved-by": "NAME-OR-ROLE, with where the approval is recorded"})])
        self.assertEqual(approved.run()[0], 0)

    def test_a_high_exemption_needs_no_approval(self):
        space = self.workspace([self.blocked_high()], [exemption()])
        self.assertEqual(space.run()[0], 0)

    def test_an_exemption_matches_an_alias_of_the_finding_case_insensitively(self):
        space = self.workspace([match("GHSA-xxxx-yyyy-zzzz", "High", related=["CVE-2026-0001"])], [exemption(vuln="cve-2026-0001", package="OpenSSL")])
        self.assertEqual(space.run()[0], 0)

    def test_an_exemption_for_another_package_or_finding_covers_nothing(self):
        for entry in (exemption(package="zlib"), exemption(vuln="CVE-2026-9999")):
            with self.subTest(entry=entry):
                space = self.workspace([self.blocked_high()], [entry])
                self.assertEqual(space.run()[0], 1)

    def test_an_image_scoped_exemption_covers_only_that_image(self):
        mine = self.workspace([self.blocked_high()], [exemption(image=IMAGE)])
        self.assertEqual(mine.run()[0], 0)
        other = self.workspace([self.blocked_high()], [exemption(image="ghcr.io/example/other")])
        self.assertEqual(other.run()[0], 1)

    def test_an_exemption_that_covers_nothing_is_reported_not_failed(self):
        space = self.workspace([], [exemption()])
        self.assertEqual(space.run()[0], 0)
        self.assertIn(("SEC-5", "info"), self.levels(space))

    def test_a_missing_exemptions_file_means_no_exemptions(self):
        space = self.workspace([self.blocked_high()])
        self.assertEqual(space.run()[0], 1)

    def test_a_broken_exemptions_file_fails(self):
        for raw in ("not json", "[]", '{"exemptions": {}}', '{"exemptions": [], "extra": 1}', '{"exemptions": ["x"]}'):
            with self.subTest(raw=raw):
                space = self.workspace([], raw_exemptions=raw)
                self.assertEqual(space.run()[0], 1)

    def test_a_path_leaving_the_repository_is_refused(self):
        space = self.workspace([])
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (pathlib.Path(outside.name) / "e.json").write_text('{"exemptions": []}', encoding="utf-8")
        (space.root / "link.json").symlink_to(pathlib.Path(outside.name) / "e.json")
        for path in ("../e.json", "/etc/passwd", "a/../../e.json", "link.json", ""):
            with self.subTest(path=path), self.assertRaises(gate.ScanError):
                gate.safe_relative(path, space.root)


class OutputTests(GateTestCase):
    FINDING_PATTERN = re.compile(r'^\{"clause":"([^"]*)","file":"([^"]*)","line":(\d+),"level":"([^"]*)","message":"(.*)"\}$')

    def test_every_finding_is_in_the_one_finding_format(self):
        space = self.workspace([match("CVE-1", "High"), match("CVE-2", "Critical")], [exemption(), exemption(expires="2026-01-01")])
        space.run()
        lines = (space.root / "findings.jsonl").read_text(encoding="utf-8").splitlines()
        self.assertGreaterEqual(len(lines), 3)
        for line in lines:
            with self.subTest(line=line):
                self.assertRegex(line, self.FINDING_PATTERN)
                self.assertEqual(list(json.loads(line)), ["clause", "file", "line", "level", "message"])

    def test_hostile_text_from_a_report_cannot_start_a_workflow_command_or_break_a_line(self):
        hostile = 'x"\n::error::pwned %0A \\'
        space = self.workspace([match("CVE-1", "High", package=hostile, version=hostile, path=hostile)])
        _, printed = space.run()
        for line in printed.splitlines():
            self.assertTrue(line.startswith(("{", "::error title=", "::notice title=image-scan counts::", "counts image=", "###", "|", "-", "Image", "Packages")) or not line.strip(), line)
        for finding in space.findings():
            self.assertNotRegex(finding["message"] + finding["file"], r'["\n%\\]')

    def test_the_summary_has_a_count_per_severity_and_the_verdict(self):
        space = self.workspace([match("CVE-1", "High"), match("CVE-2", "Medium")])
        space.run()
        summary = (space.root / "summary.md").read_text(encoding="utf-8")
        self.assertIn("Image scan: BLOCK", summary)
        for severity in gate.SEVERITIES:
            self.assertIn(f"| {severity} |", summary)
        self.assertIn("| high | 1 |", summary)

    def test_the_run_prints_one_counts_line_and_notice(self):
        space = self.workspace([match("CVE-1", "High"), match("CVE-2", "Critical")])
        _, printed = space.run()
        want = "image=ghcr.io/example/app critical=1 high=1 medium=0 low=0 negligible=0 unknown=0 blocking=2 exempted=0 packages=1"
        self.assertIn(f"counts {want}\n", printed)
        self.assertIn(f"::notice title=image-scan counts::{want}\n", printed)

    def test_a_blocked_run_prints_an_error_annotation(self):
        space = self.workspace([match("CVE-1", "High")])
        _, printed = space.run()
        self.assertIn("::error title=SEC-4::", printed)

    def test_the_command_line_runs_end_to_end_in_a_fresh_interpreter(self):
        space = self.workspace([match("CVE-1", "High")], [exemption(vuln="CVE-1")])
        done = subprocess.run([sys.executable, "-I", str(gate_path()), *space.argv()], capture_output=True, text=True, check=False)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual(space.outputs()["verdict"], "PASS")


def gate_path():
    return pathlib.Path(os.environ.get(SCRIPT_ENV) or HERE / "image-scan.py")


def tarball(name="grype", content=b"binary"):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


class InstallTests(unittest.TestCase):
    def pins(self, tmp, data, url="https://github.com/anchore/grype/releases/download/v1/grype.tar.gz", sha=None):
        import hashlib

        path = pathlib.Path(tmp, "pins.json")
        sha = sha or hashlib.sha256(data).hexdigest()
        path.write_text(json.dumps({"grype": {"linux-amd64": {"url": url, "sha256": sha}}}), encoding="utf-8")
        return path

    def install(self, tmp, data, **kwargs):
        pins = self.pins(tmp, data, **kwargs)
        with mock.patch.object(gate, "fetch", return_value=data):
            return gate.install(pins, "grype", pathlib.Path(tmp, "bin"), "linux", "amd64")

    def test_a_download_matching_its_pin_is_installed_executable(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = self.install(tmp, tarball())
            self.assertEqual(target.read_bytes(), b"binary")
            self.assertTrue(os.access(target, os.X_OK))

    def test_a_download_not_matching_its_pin_is_refused_and_nothing_is_written(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(gate.ScanError):
                self.install(tmp, tarball(), sha="0" * 64)
            self.assertFalse(pathlib.Path(tmp, "bin").exists())

    def test_an_archive_without_the_tool_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(gate.ScanError):
            self.install(tmp, tarball(name="../grype"))

    def test_a_pin_off_https_or_off_github_is_refused(self):
        for url in ("http://github.com/x.tar.gz", "https://example.com/x.tar.gz", "file:///etc/passwd"):
            with self.subTest(url=url), tempfile.TemporaryDirectory() as tmp, self.assertRaises(gate.ScanError):
                self.install(tmp, tarball(), url=url)

    def test_a_platform_with_no_pin_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            pins = self.pins(tmp, tarball())
            with self.assertRaises(gate.ScanError):
                gate.install(pins, "grype", pathlib.Path(tmp, "bin"), "darwin", "arm64")

    def test_the_real_pins_are_https_github_releases_with_a_checksum(self):
        pins = json.loads((HERE / "tool-pins.json").read_text(encoding="utf-8"))
        self.assertEqual(sorted(pins), ["grype", "syft"])
        for tool, platforms in pins.items():
            for key, pin in platforms.items():
                with self.subTest(tool=tool, platform=key):
                    self.assertRegex(pin["sha256"], r"^[0-9a-f]{64}$")
                    self.assertTrue(pin["url"].startswith(f"https://github.com/anchore/{tool}/releases/download/v{pin['version']}/"))
                    self.assertIn(pin["version"], pin["url"].rsplit("/", 1)[1])


class ImageListTests(unittest.TestCase):
    def test_a_list_keeps_order_drops_comments_and_duplicates(self):
        text = f"# live images\nghcr.io/a/b@sha256:{DIGEST}  # first\n\nalpine@sha256:{DIGEST}\nghcr.io/a/b@sha256:{DIGEST}\n"
        self.assertEqual(gate.read_image_list(text), [f"ghcr.io/a/b@sha256:{DIGEST}", f"alpine@sha256:{DIGEST}"])

    def test_a_list_with_a_tag_or_nothing_is_refused(self):
        for text in ("ghcr.io/a/b:latest\n", "# nothing\n", ""):
            with self.subTest(text=text), self.assertRaises(gate.ScanError):
                gate.read_image_list(text)

    def test_list_images_writes_a_json_array_for_a_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            listing = pathlib.Path(tmp, "images.txt")
            listing.write_text(f"ghcr.io/a/b@sha256:{DIGEST}\n", encoding="utf-8")
            out = pathlib.Path(tmp, "out.txt")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(gate.main(["list-images", "--file", str(listing), "--github-output", str(out)]), 0)
            self.assertEqual(json.loads(out.read_text(encoding="utf-8").split("=", 1)[1]), [f"ghcr.io/a/b@sha256:{DIGEST}"])

    def test_the_committed_report_list_is_valid(self):
        listing = HERE / "report-images.txt"
        if listing.exists():
            self.assertGreaterEqual(len(gate.read_image_list(listing.read_text(encoding="utf-8"))), 1)


class ConfigTests(unittest.TestCase):
    def test_both_tools_run_from_a_config_file_beside_the_action(self):
        text = (HERE / "action.yml").read_text(encoding="utf-8")
        self.assertIn('-c "$ACTION_PATH/syft-config.yaml"', text)
        self.assertIn('-c "$ACTION_PATH/grype-config.yaml"', text)
        for name in ("syft-config.yaml", "grype-config.yaml"):
            keys = [line for line in (HERE / name).read_text(encoding="utf-8").splitlines() if line.strip() and not line.startswith("#")]
            self.assertEqual(keys, ["check-for-app-update: false"])

    def test_the_action_has_no_input_for_the_threshold(self):
        block = (HERE / "action.yml").read_text(encoding="utf-8").split("\ninputs:\n")[1].split("\noutputs:\n")[0]
        names = re.findall(r"^  ([a-z-]+):$", block, re.MULTILINE)
        self.assertEqual(
            names,
            [
                "source", "image-name", "exemptions-path", "report-only", "allow-empty-sbom", "platform",
                "registry-authority", "registry-username", "registry-password",
            ],
        )


WORKFLOWS = HERE.parent.parent / "workflows"


@unittest.skipUnless(WORKFLOWS.is_dir(), "the workflows are not beside the action in this copy")
class WorkflowTests(unittest.TestCase):
    """Text checks on exact lines: this repo's CI carries no YAML parser."""

    NAMES = ("image-scan.yml", "image-scan-self.yml", "image-scan-report.yml")

    def text(self, name):
        return (WORKFLOWS / name).read_text(encoding="utf-8")

    def test_every_action_is_local_or_pinned_by_a_full_commit_id(self):
        for name in self.NAMES:
            for line in self.text(name).splitlines():
                found = re.match(r"\s+(?:- )?uses: (\S+)", line)
                if found:
                    with self.subTest(workflow=name, uses=found.group(1)):
                        self.assertRegex(found.group(1), r"^(\./\S+|[\w.-]+/[\w./-]+@[0-9a-f]{40})$")

    def test_the_gate_workflow_takes_exactly_four_inputs_and_no_way_to_lower_the_threshold(self):
        text = self.text("image-scan.yml")
        block = text.split("    inputs:\n")[1].split("    outputs:\n")[0]
        self.assertEqual(re.findall(r"^      ([a-z-]+):$", block, re.MULTILINE), ["image", "exemptions-path", "platform", "allow-empty-sbom"])
        self.assertNotIn("report-only", text.replace("report-only mode", "").split("jobs:")[1])
        self.assertNotIn("secrets:", text)

    def test_the_gate_workflow_holds_read_permissions_only(self):
        text = self.text("image-scan.yml")
        self.assertEqual(text.count("permissions:\n  contents: read\n  packages: read\n"), 1)
        self.assertEqual(text.count("    permissions:\n      contents: read\n      packages: read\n"), 1)
        self.assertNotRegex(text, r"(?m)^\s+(?:id-token|actions|attestations|checks|pull-requests|issues|packages): write")

    def test_no_input_is_interpolated_into_a_script(self):
        for name in self.NAMES[:1] + ("../actions/image-scan/action.yml",):
            in_run = False
            for line in self.text(name).splitlines():
                stripped = line.strip()
                if re.match(r"run:\s*\|?$", stripped) or stripped.startswith("run: "):
                    in_run = True
                    if stripped.startswith("run: ") and "${{" in stripped:
                        self.fail(f"{name}: expression in a run line: {stripped}")
                    continue
                if in_run and re.match(r"[a-z-]+:", stripped) and not line.startswith(" " * 10):
                    in_run = False
                if in_run and "${{" in line:
                    self.fail(f"{name}: expression in a script: {stripped}")

    def test_the_registry_token_goes_to_ghcr_only(self):
        text = self.text("image-scan.yml")
        self.assertEqual(text.count("steps.ref.outputs.registry == 'ghcr.io' &&"), 3)

    def test_the_gate_scans_after_the_digest_check_and_uploads_even_on_failure(self):
        text = self.text("image-scan.yml")
        self.assertLess(text.index("check-input"), text.index("uses: ./.image-scan-tools/.github/actions/image-scan"))
        self.assertIn("        if: always()\n        uses: actions/upload-artifact@", text)

    def test_the_report_workflow_cannot_be_called_and_holds_no_package_permission(self):
        text = self.text("image-scan-report.yml")
        self.assertNotRegex(text, r"(?m)^  workflow_call:")
        self.assertNotIn("packages:", text)
        self.assertNotIn("registry-password", text)


class SabotageTests(unittest.TestCase):
    """Each control is broken in a copy; the tests that guard it must go red there, and the
    unmodified copy must be green. Skipped inside a broken copy's own run."""

    CASES = {
        "threshold lowered to critical only": (
            'BLOCKING = ("critical", "high")\n',
            'BLOCKING = ("critical",)\n',
            {"test_a_high_finding_blocks", "test_the_threshold_is_critical_and_high_and_not_an_option",
             "test_a_report_of_a_real_lockfile_scan_blocks_on_its_two_highs", "test_counts_are_per_severity_and_a_package_found_twice_counts_once"},
        ),
        "expiry not checked": (
            "    if expires < today:\n",
            "    if False:\n",
            {"test_an_expired_exemption_fails_the_job_and_covers_nothing",
             "test_an_expired_exemption_fails_even_when_it_covers_no_finding"},
        ),
        "thirty-day limit not checked": (
            "    if (expires - granted).days > MAX_EXEMPTION_DAYS:\n",
            "    if False:\n",
            {"test_an_exemption_longer_than_thirty_days_is_refused"},
        ),
        "an exemption covers a finding that has a fix": (
            "            if match.has_fix:  # control: an exemption is for a finding with no fix\n",
            "            if False:\n",
            {"test_an_exemption_does_not_cover_a_finding_that_has_a_fix"},
        ),
        "a critical exemption needs no approval": (
            "            if match.severity == \"critical\" and not exemption.approved_by:  # control: a critical exemption needs the approval named\n",
            "            if False:\n",
            {"test_a_critical_exemption_needs_the_approval_named"},
        ),
        "checksum not compared": (
            "    if actual != sha256:\n",
            "    if False:\n",
            {"test_a_download_not_matching_its_pin_is_refused_and_nothing_is_written"},
        ),
        "a trailing newline is accepted": (
            "    found = IMAGE_RE.fullmatch(reference)  # control: fullmatch, so a trailing newline is not accepted by `$`\n",
            "    found = IMAGE_RE.match(reference)\n",
            {"test_a_tag_or_a_malformed_reference_is_refused"},
        ),
        "an unknown severity is judged as unknown": (
            "            if not isinstance(raw_severity, str) or raw_severity not in GRYPE_SEVERITIES:  # control: only a severity Grype emits is judged\n"
            '                raise ScanError("a match in the Grype report has a severity Grype does not emit")\n'
            "            match = Match(\n"
            "                ids=tuple(str(item).lower() for item in ids),\n"
            "                severity=GRYPE_SEVERITIES[raw_severity],\n",
            "            match = Match(\n"
            "                ids=tuple(str(item).lower() for item in ids),\n"
            '                severity=GRYPE_SEVERITIES.get(raw_severity, "unknown"),\n',
            {"test_a_severity_grype_does_not_emit_fails_closed_and_never_passes"},
        ),
        "the verdict depends on report order": (
            "        fix_versions=tuple(dict.fromkeys(better.fix_versions + other.fix_versions)),\n"
            '        fix_state="fixed" if "fixed" in (first.fix_state, second.fix_state) else better.fix_state,  # control: any fix makes it fixable\n',
            "        fix_versions=better.fix_versions,\n        fix_state=better.fix_state,\n",
            {"test_a_vulnerability_with_any_fix_is_fixable_in_either_report_order"},
        ),
        "an empty SBOM passes": (
            '        level = "warning" if allow_empty else "error"  # control: an empty catalogue is nothing judged, not a pass\n',
            '        level = "warning"\n',
            {"test_an_sbom_with_no_packages_fails_unless_empty_is_allowed"},
        ),
        "a tag is accepted": (
            "    if not found:\n",
            "    if False:\n",
            {"test_a_tag_or_a_malformed_reference_is_refused"},
        ),
    }

    def run_copy(self, control, broken):
        with tempfile.TemporaryDirectory(prefix="image-scan-sabotage-") as tmp:
            copy = pathlib.Path(tmp, "image-scan")
            shutil.copytree(HERE, copy, ignore=shutil.ignore_patterns("__pycache__"))
            script = copy / "image-scan.py"
            if broken:
                text = script.read_text(encoding="utf-8")
                self.assertEqual(text.count(control[0]), 1, "the control moved: update the sabotage")
                script.write_text(text.replace(control[0], control[1]), encoding="utf-8")
            env = {**os.environ, SCRIPT_ENV: str(script)}
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(copy), "-p", "test_image_scan.py", "-v"],
                capture_output=True, text=True, env=env, check=False,
            )

    @unittest.skipIf(os.environ.get(SCRIPT_ENV), "running inside a sabotaged copy")
    def test_each_broken_control_turns_its_tests_red_and_the_unbroken_copy_is_green(self):
        for name, (before, after, must_fail) in self.CASES.items():
            with self.subTest(sabotage=name):
                red = self.run_copy((before, after), broken=True)
                self.assertNotEqual(red.returncode, 0, red.stderr)
                failed = set(re.findall(r"^(?:FAIL|ERROR): (\w+) ", red.stderr, re.MULTILINE))
                self.assertTrue(must_fail <= failed, f"expected red for {sorted(must_fail - failed)}, got {sorted(failed)}")
        green = self.run_copy(("", ""), broken=False)
        self.assertEqual(green.returncode, 0, green.stderr[-2000:])


if __name__ == "__main__":
    unittest.main()
