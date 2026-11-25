"""The scan of a diff for secrets before it is shown, and the one named allowance.

The scan is host_output_scan.py, which the plan step reads each diff through and the apply job reads
the output of a failed host through. Each case breaks one line of it.
"""

from __future__ import annotations

from ..files import SCAN
from ..model import Case, Edit
from ..suites import PLAN, REPORT_LOG

NOPASSWD = r'(r"(^|[\s)])nopasswd:", r"\1"),'
REFERENCE = r'(r"\$\{[a-z_][a-z0-9_]*(:?\?[^}]*)?\}", ""),'
COMMAND = r'(r"\$\(cat [^\s)]+\)", ""),'
FILE_KEY = r'''(r'[a-z0-9_.-]*[_.-](file|filename|path|dir)"?\s*[:=](\s*"?[/~])', r"\2"),'''
FLAG = r'''(r'[:=]\s*"?(true|false|null|none)"?\s*([\],}]|$)', r" \2"),'''
ASSIGNMENT_LINES = (
    '        r"(passw(or)?d|passphrase|secret|token|api[_-]?key|private[_-]?key|credential)"\n'
    "        r'[A-Za-z0-9_.-]*\"?\\s*[:=]\\s*\"?[^\\s\"]{4,}',\n"
)
READS = "scanned if name == ASSIGNMENT else lines"
BLANKED = "blanked = blank_allowed(lines, allowances)"
LINE_TEST = "text == a.line and applies(file, a)"


def drop(shape: str) -> Edit:
    """The line of one shape taken out of the list, so the shape is no longer removed."""
    return Edit(SCAN, f"        {shape}\n", "")


CASES: list[Case] = [
    # The credential-assignment pattern reads a copy of the diff with four non-credential shapes taken out.
    # First the fix reverted and the pattern broken to match nothing, then each rule dropped (a harmless line
    # is withheld again) and each rule widened (a credential beside the shape is let through).
    Case(
        "scan: the credential-assignment pattern reads the diff whole again",
        [Edit(SCAN, READS, "lines")],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: the credential-assignment pattern matches nothing",
        [Edit(SCAN, ASSIGNMENT_LINES, '        r"x^x",\n')],
        [
            PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line"),
            PLAN("test_a_secret_looking_string_in_the_diff_is_withheld_and_fails_the_job"),
            REPORT_LOG("test_a_withheld_diff_prints_the_pattern_name_and_never_the_text_it_matched"),
        ],
    ),
    Case(
        "scan: the block-scalar join is no longer read by the assignment pattern",
        [Edit(SCAN, "scrub(line) for line in blanked + block_scalar_lines(blanked)", "scrub(line) for line in blanked")],
        [PLAN("test_a_block_scalar_secret_in_the_diff_is_withheld_and_never_shown")],
    ),
    Case(
        "scan: every pattern reads the copy with the shapes taken out",
        [Edit(SCAN, READS, "scanned")],
        [PLAN("test_the_other_patterns_read_the_diff_whole_even_beside_a_shape_the_assignment_pattern_lets_through")],
    ),
    Case(
        "scan: the sudoers tag is no longer taken out",
        [drop(NOPASSWD)],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a ${NAME} reference is no longer taken out",
        [drop(REFERENCE)],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a $(cat PATH) reference is no longer taken out",
        [drop(COMMAND)],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a key that names a file or a path is no longer taken out",
        [drop(FILE_KEY)],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a true, false, null or none flag is no longer taken out",
        [drop(FLAG)],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: the sudoers tag takes the rest of its line with it",
        [Edit(SCAN, r'(^|[\s)])nopasswd:", r"\1"', r'(^|[\s)])nopasswd:.*", r"\1"')],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a ${NAME} reference takes the rest of its line with it",
        [
            Edit(
                SCAN,
                r'(:?\?[^}]*)?\}", ""',
                r'(:?\?[^}]*)?\}.*", ""',
            ),
        ],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a key that names a file takes the rest of its line with it",
        [Edit(SCAN, r'''[:=](\s*"?[/~])', r"\2"''', r'''[:=].*', r"\1"''')],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a key that only ends like a file, with any value, is taken out",
        [
            Edit(
                SCAN,
                r'''[a-z0-9_.-]*[_.-](file|filename|path|dir)"?\s*[:=](\s*"?[/~])', r"\2"''',
                r'''[a-z0-9_.-]*(file|filename|path|dir)"?\s*[:=]', r"\1"''',
            ),
        ],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a value that only starts like a flag is taken out",
        [Edit(SCAN, r'''"?\s*([\],}]|$)', r" \2"''', r'''"?()', r" \2"''')],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: the ${...} rule takes out a default again (the old broad rule)",
        [Edit(SCAN, r'(r"\$\{[a-z_][a-z0-9_]*(:?\?[^}]*)?\}", ""),', r'(r"\$\{[^}]*\}", ""),')],
        [PLAN("test_a_secret_in_a_default_or_a_command_is_withheld_and_never_printed")],
    ),
    Case(
        "scan: the $(...) rule takes out a command again (the old broad rule)",
        [Edit(SCAN, r'(r"\$\(cat [^\s)]+\)", ""),', r'(r"\$\([^)]*\)", ""),')],
        [PLAN("test_a_secret_in_a_default_or_a_command_is_withheld_and_never_printed")],
    ),
    Case(
        "scan: the flag rule accepts any punctuation after the flag word",
        [Edit(SCAN, r'''"?\s*([\],}]|$)', r" \2"''', r'''"?([^a-z0-9_]|$)', r" \2"''')],
        [PLAN("test_a_secret_beside_a_flag_word_a_file_key_or_a_sudoers_tag_is_withheld_and_never_printed")],
    ),
    # The one named allowance: the exact line, in the file whose header names it, and nothing else.
    # First the allowance not read at all (the named line is withheld again), then each way it could
    # be widened: any file, a line that only starts like the entry, or a path that only ends alike.
    Case(
        "scan: the named allowance is not read by the credential-assignment pattern",
        [Edit(SCAN, BLANKED, "blanked = lines")],
        [PLAN("test_the_named_placeholder_in_its_own_file_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: the named allowance applies in any file",
        [
            Edit(SCAN, 'elif file != "" and line[:1] in ("-", "+", " "):', 'elif line[:1] in ("-", "+", " "):'),
            Edit(SCAN, LINE_TEST, "text == a.line"),
        ],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: the named allowance applies to a path that only ends alike",
        [Edit(SCAN, 'file.endswith("/" + allowance.path)', "file.endswith(allowance.path)")],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: the named allowance applies to a line that only starts with the entry",
        [Edit(SCAN, "text == a.line and", "text.startswith(a.line) and")],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: the named allowance takes the rest of the line with it",
        [
            Edit(SCAN, "line = line[:1]", 'line = ""'),
            Edit(SCAN, "text == a.line and", "text.startswith(a.line) and"),
        ],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: a file header carries over into the next file of the same diff",
        [Edit(SCAN, "if line.startswith(HEADER_BEFORE):\n            file = \"\"", "if line.startswith(HEADER_BEFORE):\n            pass")],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
]
