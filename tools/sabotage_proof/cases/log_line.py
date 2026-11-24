"""The line the report step prints to the job log for each host asked."""

from __future__ import annotations

from ..files import ACTION, SCAN
from ..model import Case, Edit
from ..suites import PLAN, REPORT_LOG
from ..text import indented

CASES: list[Case] = [
    # The report step prints each non-clean host's reason to the log, and nothing of the diff.
    Case(
        "log line: the diff is printed in the host's line",
        [
            Edit(
                ACTION,
                'scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook")"',
                'scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook; diff=$(cat "$cleaned")")"',
            ),
        ],
        [REPORT_LOG("test_a_planted_secret_in_a_diff_never_reaches_the_log")],
    ),
    Case(
        "log line: the text a pattern matched is printed with the pattern name",
        [
            Edit(
                SCAN,
                "        if matches(compiled, scanned if name == ASSIGNMENT else lines):\n            hits.append(name)\n",
                "        found = next((m for line in (scanned if name == ASSIGNMENT else lines) if (m := compiled.search(line))), None)\n"
                '        if found:\n            hits.append(f"{name} {found.group(0)}")\n',
            ),
        ],
        [
            REPORT_LOG("test_a_planted_secret_in_a_diff_never_reaches_the_log"),
            REPORT_LOG("test_a_withheld_diff_prints_the_pattern_name_and_never_the_text_it_matched"),
        ],
    ),
    Case(
        "log line: the destination is printed in the host's line",
        [
            Edit(
                ACTION,
                'scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook")"',
                'scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook; dest=$dest")"',
            ),
        ],
        [REPORT_LOG("test_a_planted_secret_in_a_diff_never_reaches_the_log")],
    ),
    Case(
        "log line: what the host wrote to standard error is printed in the host's line",
        [
            Edit(
                ACTION,
                'scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook")"',
                'scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook; err=$(head -c 300 "$base.err")")"',
            ),
        ],
        [
            REPORT_LOG("test_a_planted_secret_in_a_diff_never_reaches_the_log"),
            REPORT_LOG("test_an_unreachable_host_prints_its_kind_and_not_its_name_or_its_error_text"),
        ],
    ),
    Case(
        "log line: the allow-list of characters is dropped",
        [Edit(ACTION, "printf '%s' \"$1\" | tr -c 'A-Za-z0-9 _./:;,()=-' '?'", "printf '%s' \"$1\"")],
        [REPORT_LOG("test_a_value_that_would_start_a_workflow_command_is_defanged")],
    ),
    Case(
        "log line: the error line of a non-clean host is not printed",
        [
            Edit(
                ACTION,
                'echo "::$level::$(plain "$label: kind=$kind; note=${note:--}; scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook")"',
                ":",
            ),
        ],
        [
            REPORT_LOG("test_an_unreachable_host_prints_its_kind_and_not_its_name_or_its_error_text"),
            REPORT_LOG("test_every_host_asked_prints_exactly_one_line_clean_or_not"),
        ],
    ),
    Case(
        "log line: a clean host prints no line",
        [
            Edit(
                ACTION,
                'echo "::$level::$(plain "$label: kind=$kind; note=${note:--}; scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook")"',
                '[ "$kind" = ok ] || echo "::$level::$(plain "$label: kind=$kind; note=${note:--}; scan-hits=${hits:-none}; diff-bytes=$size; running-total=$total_bytes; playbook=$playbook")"',
            ),
        ],
        [
            REPORT_LOG("test_every_host_asked_prints_exactly_one_line_clean_or_not"),
            REPORT_LOG("test_a_clean_host_is_a_notice_that_names_its_playbook"),
            REPORT_LOG("test_a_clean_host_line_is_the_shape_the_delivery_approver_reads"),
        ],
    ),
    Case(
        "log line: a clean host's line does not name its playbook",
        [
            Edit(
                ACTION,
                indented(
                    r'''
                    if [ "$matched" = yes ]; then
                      playbook="${line##* playbook=}"''',
                    10,
                ),
                indented(
                    r'''
                    if [ "$matched" = yes ] && [ "$kind" != ok ]; then
                      playbook="${line##* playbook=}"''',
                    10,
                ),
            ),
        ],
        [
            REPORT_LOG("test_a_clean_host_is_a_notice_that_names_its_playbook"),
            REPORT_LOG("test_a_clean_host_line_is_the_shape_the_delivery_approver_reads"),
        ],
    ),
    Case(
        "log line: a host that would change is an error in a plan",
        [
            Edit(
                ACTION,
                "!= true ]; then\n"
                + indented(
                    r"""
                    level=notice
                    """,
                    12,
                ),
                "!= true ]; then\n"
                + indented(
                    r"""
                    level=error
                    """,
                    12,
                ),
            ),
        ],
        [REPORT_LOG("test_a_host_that_would_change_is_a_notice_in_a_plan_and_an_error_in_a_drift_check")],
    ),
    Case(
        "log line: the failed plan headline is reverted",
        [
            Edit(
                ACTION,
                'first="Plan failed: $not_clean of $total hosts not clean ($detail), so no',
                'first="Plan failed: $failed of $total host(s) gave no usable answer, so no',
            ),
        ],
        [REPORT_LOG("test_the_failed_plan_headline_counts_the_hosts_that_are_not_clean_by_kind")],
    ),
    Case(
        "log line: the drift headline is reverted",
        [
            Edit(
                ACTION,
                'first="Not clean at commit $SHA: $not_clean of $total hosts not clean ($detail)."',
                'first="Not clean at commit $SHA: $detail."',
            ),
        ],
        [REPORT_LOG("test_the_drift_headline_counts_the_hosts_that_are_not_clean_by_kind")],
    ),
    Case(
        "log line: the playbook is not read from the answer",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    playbook="${line##* playbook=}"
                    playbook="${playbook%% *}"
                    """,
                    12,
                ),
                indented(
                    r"""
                    playbook="-"
                    """,
                    12,
                ),
            ),
        ],
        [REPORT_LOG("test_an_answer_for_another_commit_shows_the_playbook_it_named")],
    ),
    Case(
        "log line: a call stopped at its limit gets the generic note",
        [
            Edit(
                ACTION,
                'note="the call was stopped at its limit (exit 124)"',
                'note="the host did not answer with one summary line (exit $rc)"',
            ),
        ],
        [REPORT_LOG("test_a_call_stopped_at_its_limit_is_a_no_answer_that_says_so")],
    ),
    Case(
        "log line: the table is not printed to the log",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    printf '| Host | Result | Note |\n| --- | --- | --- |\n'
                    cat "$rows"

                    if [ "$failed" -gt 0 ] && [ "$partial" != true ]; then""",
                    8,
                ),
                indented('if [ "$failed" -gt 0 ] && [ "$partial" != true ]; then', 8),
            ),
        ],
        [REPORT_LOG("test_the_table_is_on_the_log_and_the_diff_is_not")],
    ),
    Case(
        "log line: the second hop's name is not redacted from a host's line",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    playbook=$(redact_jumps "$playbook")
                    """,
                    10,
                ),
                "",
            ),
        ],
        [REPORT_LOG("test_a_second_hop_name_in_a_host_line_is_redacted_as_it_is_from_the_diff")],
    ),
    Case(
        "log line: the failed task's note prints the host's output line, not the TASK header",
        [Edit(SCAN, "return task[:TASK_LIMIT]", "return line")],
        [PLAN("test_a_failed_host_log_line_names_the_task_that_failed_and_none_of_its_output")],
    ),
]
