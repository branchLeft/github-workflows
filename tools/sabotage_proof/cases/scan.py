"""The scan of a diff for secrets before it is shown, and the one named allowance."""

from __future__ import annotations

from ..files import ACTION
from ..model import Case, Edit
from ..suites import PLAN, REPORT_LOG
from ..text import indented

CASES: list[Case] = [
    # The credential-assignment pattern reads a copy of the diff with four non-credential shapes taken out.
    # First the fix reverted and the pattern broken to match nothing, then each rule dropped (a harmless line
    # is withheld again) and each rule widened (a credential beside the shape is let through).
    Case(
        "scan: the credential-assignment pattern reads the diff whole again",
        [Edit(ACTION, 'target="$scanned"', 'target="$cleaned"')],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: the credential-assignment pattern matches nothing",
        [
            Edit(
                ACTION,
                '\'credential-assignment|(passw(or)?d|passphrase|secret|token|api[_-]?key|private[_-]?key|credential)[A-Za-z0-9_.-]*"?[[:space:]]*[:=][[:space:]]*"?[^[:space:]"]{4,}\'',
                "'credential-assignment|x^x'",
            ),
        ],
        [
            PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line"),
            PLAN("test_a_secret_looking_string_in_the_diff_is_withheld_and_fails_the_job"),
            REPORT_LOG("test_a_withheld_diff_prints_the_pattern_name_and_never_the_text_it_matched"),
        ],
    ),
    Case(
        "scan: every pattern reads the copy with the shapes taken out",
        [Edit(ACTION, 'if [ "$name" = credential-assignment ]; then', "if true; then")],
        [PLAN("test_the_other_patterns_read_the_diff_whole_even_beside_a_shape_the_assignment_pattern_lets_through")],
    ),
    Case(
        "scan: the sudoers tag is no longer taken out",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    -e 's/(^|[[:space:])])nopasswd:/\1/g' \
                    """,
                    14,
                ),
                "",
            ),
        ],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a ${NAME} reference is no longer taken out",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    -e 's/\$\{[a-z_][a-z0-9_]*(:?\?[^}]*)?\}//g' \
                    """,
                    14,
                ),
                "",
            ),
        ],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a $(cat PATH) reference is no longer taken out",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    -e 's/\$\(cat [^[:space:])]+\)//g' \
                    """,
                    14,
                ),
                "",
            ),
        ],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a key that names a file or a path is no longer taken out",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    -e 's/[a-z0-9_.-]*[_.-](file|filename|path|dir)"?[[:space:]]*[:=]([[:space:]]*"?[/~])/\1/g' \
                    """,
                    14,
                ),
                "",
            ),
        ],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: a true, false, null or none flag is no longer taken out",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    -e 's/[:=][[:space:]]*"?(true|false|null|none)"?[[:space:]]*([],}]|$)/ \2/g' \
                    """,
                    14,
                ),
                "",
            ),
        ],
        [PLAN("test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: the sudoers tag takes the rest of its line with it",
        [Edit(ACTION, r"-e 's/(^|[[:space:])])nopasswd:/\1/g'", r"-e 's/(^|[[:space:])])nopasswd:.*/\1/g'")],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a ${NAME} reference takes the rest of its line with it",
        [
            Edit(
                ACTION,
                r"-e 's/\$\{[a-z_][a-z0-9_]*(:?\?[^}]*)?\}//g'",
                r"-e 's/\$\{[a-z_][a-z0-9_]*(:?\?[^}]*)?\}.*//g'",
            ),
        ],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a key that names a file takes the rest of its line with it",
        [Edit(ACTION, "[:=]([[:space:]]*\"?[/~])/\\1/g'", r"[:=].*/\1/g'")],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a key that only ends like a file, with any value, is taken out",
        [
            Edit(
                ACTION,
                '[a-z0-9_.-]*[_.-](file|filename|path|dir)"?[[:space:]]*[:=]([[:space:]]*"?[/~])/\\1/g\'',
                "[a-z0-9_.-]*(file|filename|path|dir)\"?[[:space:]]*[:=]/\\1/g'",
            ),
        ],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: a value that only starts like a flag is taken out",
        [Edit(ACTION, "\"?[[:space:]]*([],}]|$)/ \\2/g'", "\"?()/ \\2/g'")],
        [PLAN("test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line")],
    ),
    Case(
        "scan: the ${...} rule takes out a default again (the old broad rule)",
        [Edit(ACTION, r"-e 's/\$\{[a-z_][a-z0-9_]*(:?\?[^}]*)?\}//g'", r"-e 's/\$\{[^}]*\}//g'")],
        [PLAN("test_a_secret_in_a_default_or_a_command_is_withheld_and_never_printed")],
    ),
    Case(
        "scan: the $(...) rule takes out a command again (the old broad rule)",
        [Edit(ACTION, r"-e 's/\$\(cat [^[:space:])]+\)//g'", r"-e 's/\$\([^)]*\)//g'")],
        [PLAN("test_a_secret_in_a_default_or_a_command_is_withheld_and_never_printed")],
    ),
    Case(
        "scan: the flag rule accepts any punctuation after the flag word",
        [
            Edit(
                ACTION,
                "-e 's/[:=][[:space:]]*\"?(true|false|null|none)\"?[[:space:]]*([],}]|$)/ \\2/g'",
                "-e 's/[:=][[:space:]]*\"?(true|false|null|none)\"?([^a-z0-9_]|$)/ \\2/g'",
            ),
        ],
        [PLAN("test_a_secret_beside_a_flag_word_a_file_key_or_a_sudoers_tag_is_withheld_and_never_printed")],
    ),
    # The one named allowance: the exact line, in the file whose header names it, and nothing else.
    # First the allowance not read at all (the named line is withheld again), then each way it could
    # be widened: any file, a line that only starts like the entry, or a path that only ends alike.
    Case(
        "scan: the named allowance is not read by the credential-assignment pattern",
        [
            Edit(
                ACTION,
                "tr '[:upper:]' '[:lower:]' < \"$allowed\"",
                "tr '[:upper:]' '[:lower:]' < \"$cleaned\"",
            ),
        ],
        [PLAN("test_the_named_placeholder_in_its_own_file_is_shown_and_does_not_fail_the_job")],
    ),
    Case(
        "scan: the named allowance applies in any file",
        [
            Edit(ACTION, 'if (file != "" && $0 ~ /^[-+ ]/) {', "if ($0 ~ /^[-+ ]/) {"),
            Edit(
                ACTION,
                'text == aline[k] && (file == apath[k] || (length(file) > length(apath[k]) && substr(file, length(file) - length(apath[k])) == "/" apath[k]))',
                "text == aline[k]",
            ),
        ],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: the named allowance applies to a path that only ends alike",
        [
            Edit(
                ACTION,
                'substr(file, length(file) - length(apath[k])) == "/" apath[k]',
                "substr(file, length(file) - length(apath[k]) + 1) == apath[k]",
            ),
        ],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: the named allowance applies to a line that only starts with the entry",
        [Edit(ACTION, "text == aline[k] &&", "index(text, aline[k]) == 1 &&")],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: the named allowance takes the rest of the line with it",
        [
            Edit(ACTION, "{ $0 = substr($0, 1, 1) }", '{ $0 = "" }'),
            Edit(ACTION, "text == aline[k] &&", "index(text, aline[k]) == 1 &&"),
        ],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
    Case(
        "scan: a file header carries over into the next file of the same diff",
        [Edit(ACTION, '/^--- before/ { file = ""; print; next }', "/^--- before/ { print; next }")],
        [PLAN("test_the_named_placeholder_is_withheld_in_any_other_place_or_with_anything_added")],
    ),
]
