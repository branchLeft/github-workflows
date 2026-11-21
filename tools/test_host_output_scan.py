"""Tests of .github/actions/ssh-check-hosts/host_output_scan.py, the script alone.

The plan step and the apply job are tested through it in test_ansible_gated_apply.py.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / ".github/actions/ssh-check-hosts/host_output_scan.py"

FAILED = (
    "PLAY [all]\n"
    "TASK [Gather the facts] ****\n"
    "ok: [host]\n"
    "TASK [role : Install the package] ****\n"
    'fatal: [host]: FAILED! => {"msg": "output-that-must-not-be-printed"}\n'
)
ALLOWANCE = "hetzner/provision/branchleft-verify-image|export GH_TOKEN=placeholder-not-a-credential"


class Output:
    """What the script printed: its two lines, and its exit status."""

    def __init__(self, proc: subprocess.CompletedProcess[bytes]) -> None:
        self.code = proc.returncode
        self.stdout = proc.stdout.decode("latin-1")
        self.stderr = proc.stderr.decode("latin-1")
        lines = self.stdout.split("\n")
        self.hits = lines[0].removeprefix("hits=") if lines[0].startswith("hits=") else "?"
        self.task = lines[1].removeprefix("task=") if len(lines) > 1 and lines[1].startswith("task=") else "?"


class HostOutputScanTests(unittest.TestCase):
    def run_script(self, text: str, keylines: str | None = None, allowances: str = "") -> Output:
        directory = Path(tempfile.mkdtemp(prefix="host-output-scan-test-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(directory)], check=False)
        target = directory / "output"
        target.write_bytes(text.encode("latin-1"))
        command = [sys.executable, "-I", str(SCRIPT), str(target), "--allowances", allowances]
        if keylines is not None:
            keys = directory / "keylines"
            keys.write_bytes(keylines.encode("latin-1"))
            command += ["--keylines", str(keys)]
        return Output(subprocess.run(command, capture_output=True, check=False))

    def test_the_last_task_header_before_the_first_fatal_line_is_named_and_nothing_else_is_printed(self) -> None:
        result = self.run_script(FAILED)
        self.assertEqual(result.code, 0, result.stderr)
        self.assertEqual(result.stdout, "hits=none\ntask=role : Install the package\n")

    def test_output_with_no_fatal_line_names_no_task(self) -> None:
        self.assertEqual(self.run_script("TASK [Gather the facts] ****\nok: [host]\n").task, "")

    def test_a_fatal_line_before_any_task_header_names_no_task(self) -> None:
        self.assertEqual(self.run_script("fatal: [host]: no inventory\nTASK [Later] ****\n").task, "")

    def test_only_the_first_fatal_line_ends_the_search(self) -> None:
        text = "TASK [One] ***\nfatal: [a]: x\nTASK [Two] ***\nfatal: [b]: y\n"
        self.assertEqual(self.run_script(text).task, "One")

    def test_a_task_name_is_cut_at_a_hundred_and_sixty_characters(self) -> None:
        result = self.run_script(f"TASK [{'a' * 300}] ***\nfatal: x\n")
        self.assertEqual(result.task, "a" * 160)

    def test_a_task_header_with_no_closing_bracket_keeps_its_rest_of_line(self) -> None:
        self.assertEqual(self.run_script("TASK [Open ended\nfatal: x\n").task, "Open ended")

    def test_a_secret_in_a_task_name_gives_the_pattern_name_and_no_task_and_no_text(self) -> None:
        result = self.run_script("TASK [Write password=hunter2hunter2 to the file] ***\nfatal: x\n")
        self.assertEqual(result.stdout, "hits=credential-assignment\ntask=\n")
        self.assertNotIn("hunter2", result.stdout + result.stderr)

    def test_each_pattern_is_named_by_its_own_form(self) -> None:
        cases = {
            "private-key": "-----BEGIN OPENSSH PRIVATE KEY-----",
            "aws-access-key": "AKIA" + "A" * 16,
            "github-token": "ghp_" + "a" * 36,
            "slack-token": "xoxb-" + "1" * 12,
            "payment-key": "sk_live_" + "a" * 24,
            "jwt": "eyJ" + "a" * 12 + "." + "b" * 12 + ".sig",
            "credential-assignment": "api_key: abcd1234",
        }
        for name, line in cases.items():
            with self.subTest(pattern=name):
                result = self.run_script(f"TASK [x] ***\n{line}\nfatal: y\n")
                self.assertEqual(result.hits, name)
                self.assertEqual(result.task, "")

    def test_two_patterns_are_listed_in_the_order_of_the_list(self) -> None:
        result = self.run_script("token=abcd1234\n-----BEGIN RSA PRIVATE KEY-----\n")
        self.assertEqual(result.hits, "private-key, credential-assignment")

    def test_the_scan_ignores_case(self) -> None:
        self.assertEqual(self.run_script("PASSWORD=Hunter2Hunter2\n").hits, "credential-assignment")

    def test_a_line_that_only_looks_like_an_assignment_is_not_a_hit(self) -> None:
        lines = (
            "deploy ALL=(root) NOPASSWD: /usr/local/sbin/example-program",
            "EXAMPLE_TOKEN: ${EXAMPLE_TOKEN:?set it in /etc/example/app.env}",
            "password_file: /etc/example/db-password",
            '"example_password": true,',
        )
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(self.run_script(line + "\n").hits, "none")

    def test_a_credential_beside_a_shape_is_still_a_hit(self) -> None:
        lines = (
            "NOPASSWD: x password=hunter2hunter2",
            "token: ${A:-hunter2hunter2}",
            "password_file: /x password=hunter2hunter2",
            '"a_password": true, "b_password": "hunter2hunter2"',
        )
        for line in lines:
            with self.subTest(line=line):
                self.assertEqual(self.run_script(line + "\n").hits, "credential-assignment")

    def test_a_named_allowance_blanks_its_exact_line_in_its_own_file_only(self) -> None:
        line = "export GH_TOKEN=placeholder-not-a-credential"
        in_file = f"--- before\n+++ after: hetzner/provision/branchleft-verify-image\n+{line}\n"
        elsewhere = f"--- before\n+++ after: hetzner/provision/other\n+{line}\n"
        added = in_file + "+export GH_TOKEN=placeholder-not-a-credential # more\n"
        self.assertEqual(self.run_script(in_file, allowances=ALLOWANCE).hits, "none")
        self.assertEqual(self.run_script(elsewhere, allowances=ALLOWANCE).hits, "credential-assignment")
        self.assertEqual(self.run_script(added, allowances=ALLOWANCE).hits, "credential-assignment")
        self.assertEqual(self.run_script(in_file).hits, "credential-assignment")

    def test_a_line_of_key_material_is_a_hit_and_the_line_is_not_printed(self) -> None:
        key = "FAKE_PRIVATE_KEY_BODY_LINE_ONE_0123456789"
        result = self.run_script(f"TASK [x] ***\nsaw {key} here\nfatal: y\n", keylines=key + "\n")
        self.assertEqual(result.stdout, "hits=ci-key-material\ntask=\n")

    def test_an_empty_key_file_matches_nothing(self) -> None:
        self.assertEqual(self.run_script("anything\n", keylines="").hits, "none")

    def test_control_characters_are_dropped_before_the_scan(self) -> None:
        self.assertEqual(self.run_script("TASK [Install\x07 the package] ***\nfatal: x\n").task, "Install the package")

    def test_a_file_that_cannot_be_read_fails_with_a_fixed_message(self) -> None:
        proc = subprocess.run([sys.executable, "-I", str(SCRIPT), "/nonexistent/output"], capture_output=True, check=False)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, b"")
        self.assertNotIn(b"nonexistent", proc.stderr)


if __name__ == "__main__":
    unittest.main()
