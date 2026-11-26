"""Tests of .github/actions/ssh-check-hosts/host_output_scan.py, the script alone.

The plan step and the apply job are tested through it in test_ansible_gated_apply.py.
"""

from __future__ import annotations

import importlib.util
import re
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


    def test_a_file_key_is_taken_out_and_its_value_keeps_its_slash(self) -> None:
        # The scrub is loaded alone: the copy it leaves is what the patterns read.
        spec = importlib.util.spec_from_file_location("host_output_scan", SCRIPT)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        self.assertEqual(module.scrub("password_file: /etc/example/db-password"), " /etc/example/db-password")

    def test_a_secret_on_the_line_after_a_block_scalar_indicator_is_withheld(self) -> None:
        lines = (
            "password: >-\n  hunter2hunter2\n",
            "token: |\n    abcd1234efgh\n",
            "- api_key: >-\n    abcd1234efgh\n",
        )
        for text in lines:
            with self.subTest(text=text):
                self.assertEqual(self.run_script(text).hits, "credential-assignment")

    def test_a_block_scalar_that_is_not_a_credential_is_not_a_hit(self) -> None:
        self.assertEqual(self.run_script("description: >-\n  just some words here\n").hits, "none")

    def test_a_block_scalar_ends_at_the_first_line_with_no_more_indent(self) -> None:
        self.assertEqual(self.run_script("password: >-\nhunter2hunter2\n").hits, "none")

    def test_a_block_scalar_secret_is_withheld_in_added_removed_and_context_diff_lines(self) -> None:
        diff = "--- before\n+++ after: /x\n"
        cases = {
            "added": "+password: >-\n+  hunter2hunter2\n",
            "removed": "-token: |\n-    abcd1234efgh\n",
            "context": " password: >-\n   hunter2hunter2\n",
            "added, indented under a list item": "+- api_key: >-\n+    abcd1234efgh\n",
        }
        for name, body in cases.items():
            with self.subTest(line=name):
                self.assertEqual(self.run_script(diff + body).hits, "credential-assignment")

    def test_a_block_scalar_that_is_not_a_credential_in_a_diff_is_not_a_hit(self) -> None:
        self.assertEqual(
            self.run_script("--- before\n+++ after: /x\n+description: >-\n+  just some words here\n").hits, "none"
        )

    def test_a_diff_block_scalar_ends_at_the_first_line_with_no_more_indent(self) -> None:
        self.assertEqual(
            self.run_script("--- before\n+++ after: /x\n+password: >-\n+next: 1\n+  hunter2hunter2\n").hits, "none"
        )

    # The report of a withheld host: where its bytes are, and which task each hit fell under.

    def report(self, text: str, keylines: str | None = None) -> str:
        directory = Path(tempfile.mkdtemp(prefix="host-output-scan-report-"))
        self.addCleanup(subprocess.run, ["rm", "-rf", str(directory)], check=False)
        target = directory / "output"
        target.write_bytes(text.encode("latin-1"))
        command = [sys.executable, "-I", str(SCRIPT), str(target), "--report"]
        if keylines is not None:
            keys = directory / "keylines"
            keys.write_bytes(keylines.encode("latin-1"))
            command += ["--keylines", str(keys)]
        proc = subprocess.run(command, capture_output=True, check=False)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(proc.stderr, b"")
        return proc.stdout.decode("latin-1")

    def test_the_report_gives_task_sizes_and_the_task_of_a_hit_and_never_the_matched_line(self) -> None:
        # The synthetic secret, its path and every other content line carry a marker that must never be printed.
        header_a = "TASK [role : Write the env file] ****\n"
        body_a = "--- before\n+++ after: /etc/leakpath/app.env\n+api_token = leakvalue0123456789\n+leakbody unrelated\n"
        header_b = "TASK [role : Install the unit] ****\n"
        body_b = "+leakbody " + "x" * 90 + "\n"
        text = "PLAY [all] ****\n" + header_a + body_a + header_b + body_b + "PLAY RECAP ****\nleakrecap\n"
        out = self.report(text)
        self.assertEqual(
            out,
            "output bytes by task, largest first (2 of 2 tasks):\n"
            f"{len(header_b + body_b)} TASK [role : Install the unit]\n"
            f"{len(header_a + body_a)} TASK [role : Write the env file]\n"
            f"{len('PLAY [all] ****\n' + 'PLAY RECAP ****' + chr(10) + 'leakrecap' + chr(10))} outside any TASK\n"
            "scan hits by task:\n"
            "credential-assignment in TASK [role : Write the env file]\n",
        )
        for text_that_must_not_appear in ("leakvalue", "leakpath", "api_token", "leakbody", "leakrecap", "--- before", "+++", "/etc"):
            self.assertNotIn(text_that_must_not_appear, out)

    def test_every_report_line_is_built_from_a_number_a_pattern_name_or_a_task_header_name(self) -> None:
        text = (
            "TASK [r : one] ***\n--- before\n+password: leakvalue0123456789\n"
            "TASK [r : two] ***\n+-----BEGIN OPENSSH PRIVATE KEY-----\n+leakbody\n"
            "TASK [r : three] ***\n+x: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.\n"
        )
        line = re.compile(
            r"^(output bytes by task, largest first \([0-9]+ of [0-9]+ tasks\):"
            r"|[0-9]+ (TASK \[[A-Za-z0-9 _./:;,()=?-]{0,120}\]|outside any TASK)"
            r"|scan hits by task:"
            r"|(private-key|aws-access-key|github-token|slack-token|payment-key|jwt|credential-assignment|ci-key-material)"
            r" in TASK \[[A-Za-z0-9 _./:;,()=?-]{0,120}\])$"
        )
        out = self.report(text)
        for each in out.splitlines():
            self.assertRegex(each, line)
        self.assertIn("credential-assignment in TASK [r : one]", out)
        self.assertIn("private-key in TASK [r : two]", out)
        self.assertIn("jwt in TASK [r : three]", out)
        self.assertNotIn("leak", out)

    def test_a_block_scalar_secret_and_key_material_are_placed_under_their_own_task(self) -> None:
        text = (
            "TASK [r : plain] ***\n+nothing here\n"
            "TASK [r : scalar] ***\n--- before\n+++ after: /x\n+password: >-\n+  leakvalue0123456789\n"
            "TASK [r : keyed] ***\n+FAKE_KEY_BODY_LINE_0123456789 leaktail\n"
        )
        out = self.report(text, keylines="FAKE_KEY_BODY_LINE_0123456789\n")
        self.assertIn("credential-assignment in TASK [r : scalar]", out)
        self.assertIn("ci-key-material in TASK [r : keyed]", out)
        self.assertNotIn("in TASK [r : plain]", out)
        self.assertNotIn("leak", out)
        self.assertNotIn("FAKE_KEY", out)

    def test_a_size_withhold_has_no_hits_and_the_rows_are_the_biggest_fifteen_largest_first(self) -> None:
        sizes = {f"t{index:02d}": 10 * (index + 1) for index in range(20)}
        text = "".join(f"TASK [r : {name}] ***\n+{'y' * size}\n" for name, size in sizes.items())
        rows = self.report(text).splitlines()
        self.assertEqual(rows[0], "output bytes by task, largest first (15 of 20 tasks):")
        self.assertEqual(rows[-1], "scan hits by task: none")
        shown = rows[1:16]
        self.assertEqual([row.split(" ", 2)[2] for row in shown], [f"[r : t{index:02d}]" for index in range(19, 4, -1)])
        numbers = [int(row.split(" ", 1)[0]) for row in shown]
        self.assertEqual(numbers, sorted(numbers, reverse=True))
        self.assertEqual(numbers[0], len(f"TASK [r : t19] ***\n+{'y' * 200}\n"))
        self.assertEqual(rows[16], "0 outside any TASK")

    def test_the_shown_rows_and_the_outside_line_add_up_to_the_whole_output(self) -> None:
        text = "PLAY [all] ***\nTASK [a : x] ***\n+one\nTASK [a : y] ***\n+two two\nRUNNING HANDLER [a : z] ***\n+three\nPLAY RECAP ***\n"
        out = self.report(text).splitlines()
        counted = sum(int(row.split(" ", 1)[0]) for row in out[1:-1])
        self.assertEqual(counted, len(text.encode("latin-1")))

    def test_a_task_name_is_cut_at_a_hundred_and_twenty_characters(self) -> None:
        out = self.report(f"TASK [r : {'n' * 300}] ***\n+x\n")
        self.assertIn(f"TASK [r : {'n' * 116}]", out)
        self.assertNotIn("n" * 117, out)

    def test_characters_outside_the_safe_set_in_a_task_name_become_question_marks(self) -> None:
        out = self.report("TASK [r : a`b\"c$d{{ e }}\\] ***\n+x\n")
        self.assertIn("TASK [r : a?b?c?d?? e ???]", out)
        for character in '`"$\\{}':
            self.assertNotIn(character, out)

    def test_a_task_name_that_reads_as_a_secret_is_replaced_by_its_number(self) -> None:
        out = self.report("TASK [r : fine] ***\n+x\nTASK [r : Set password=leakvalue0123456789 now] ***\n+y\n")
        self.assertIn("TASK number 2 (its name reads as a secret and is not shown)", out)
        self.assertIn("credential-assignment in TASK number 2", out)
        self.assertNotIn("leak", out)

    def test_a_line_that_only_looks_like_a_task_header_is_content_and_is_never_printed(self) -> None:
        text = "TASK [r : real] ***\nTASK [leakname] with no stars\n  TASK [leakname] ***\n+TASK [leakname] ***\n"
        out = self.report(text)
        self.assertNotIn("leakname", out)
        self.assertIn(f"{len(text)} TASK [r : real]", out)

    def test_the_same_task_name_twice_is_one_row_with_both_sizes(self) -> None:
        out = self.report("TASK [r : again] ***\n+aa\nTASK [r : again] ***\n+bbbb\n")
        self.assertIn(f"{len('TASK [r : again] ***' + chr(10) + '+aa' + chr(10)) + len('TASK [r : again] ***' + chr(10) + '+bbbb' + chr(10))} TASK [r : again]", out)
        self.assertIn("(1 of 1 tasks)", out)

    def test_at_most_thirty_hit_rows_are_printed_and_the_rest_are_counted(self) -> None:
        text = "".join(f"TASK [r : t{index}] ***\n+password: leakvalue0123456789\n" for index in range(40))
        out = self.report(text).splitlines()
        self.assertEqual(sum(row.startswith("credential-assignment in TASK") for row in out), 30)
        self.assertEqual(out[-1], "10 more hit rows not shown")

    def test_the_report_does_not_change_the_scan_of_the_same_output(self) -> None:
        text = "TASK [r : t] ***\n+password: leakvalue0123456789\nfatal: x\n"
        self.assertEqual(self.run_script(text).stdout, "hits=credential-assignment\ntask=\n")


if __name__ == "__main__":
    unittest.main()
