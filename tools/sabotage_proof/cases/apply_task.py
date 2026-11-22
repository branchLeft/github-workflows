"""The failed task the apply job names when a host's playbook fails, and the output it never prints."""

from __future__ import annotations

from ..files import GATED_APPLY, SCAN
from ..model import Case, Edit
from ..suites import APPLY, STRUCTURE

PULL_CALL = '"$SHA" < /dev/null 2> "$errfile") || rc=$?'
HOP_NAMES = '"$JUMP2_HOST" "${JUMP2_HOST#*@}" "$JUMP_HOST" "${JUMP_HOST#*@}" "$dest" "${dest#*@}"'
SCAN_CALL = 'scan_out=$(python3 "$scan_script" "$errfile" --keylines "$keylines" 2> /dev/null) || scan_out=""'
TASK_READ = "scan_task=$(printf '%s\\n' \"$scan_out\" | sed -n 's/^task=//p')"
NAMES_THE_TASK = "test_a_playbook_failed_apply_names_the_playbook_and_the_failed_task"
WITHHOLDS = "test_a_credential_looking_line_in_the_output_withholds_the_task_and_prints_no_text"

CASES: list[Case] = [
    Case(
        "apply task: the host's standard error goes to /dev/null again",
        [Edit(GATED_APPLY, PULL_CALL, '"$SHA" < /dev/null 2> /dev/null) || rc=$?')],
        [APPLY(NAMES_THE_TASK)],
    ),
    Case(
        "apply task: the apply prints the fatal line instead of the TASK header",
        [Edit(SCAN, "return task[:TASK_LIMIT]", "return line")],
        [APPLY(NAMES_THE_TASK)],
    ),
    Case(
        "apply task: the apply names the task without the scan",
        [
            Edit(
                GATED_APPLY,
                SCAN_CALL,
                "scan_out=$(printf 'hits=none\\ntask=%s\\n' \"$(awk '/^TASK \\[/ { t = $0 } /^fatal: / { print t; exit }' \"$errfile\")\") || scan_out=\"\"",
            ),
        ],
        [APPLY(WITHHOLDS)],
    ),
    Case(
        "apply task: a scan hit still names the task",
        [Edit(GATED_APPLY, 'if [ -n "$scan_hits" ] && [ "$scan_hits" != none ]; then', "if false; then")],
        [APPLY(WITHHOLDS)],
    ),
    Case(
        "apply task: the key lines are not given to the scan",
        [Edit(GATED_APPLY, ' "$errfile" --keylines "$keylines" 2> /dev/null)', ' "$errfile" 2> /dev/null)')],
        [APPLY("test_a_line_that_quotes_the_apply_key_withholds_the_task")],
    ),
    Case(
        "apply task: the error file is not removed on the way out",
        [
            Edit(
                GATED_APPLY,
                '"$work"/id.* "$work/host.err" "$work/keylines"\' EXIT\n          (umask',
                '"$work"/id.* "$work/keylines"\' EXIT\n          (umask',
            ),
        ],
        [APPLY("test_the_raw_output_is_never_printed_uploaded_or_kept")],
    ),
    Case(
        "apply task: every failure reads the output, not only a failed playbook",
        [Edit(GATED_APPLY, 'if [ "$f_reason" = playbook-failed ]; then\n                scan_out=""', 'if true; then\n                scan_out=""')],
        [APPLY("test_a_summary_line_that_is_not_playbook_failed_adds_no_task")],
    ),
    Case(
        "apply task: a host name in the task is not replaced",
        [
            Edit(
                GATED_APPLY,
                f'detail=", task $(redact "$scan_task" {HOP_NAMES})"',
                'detail=", task $scan_task"',
            ),
        ],
        [APPLY("test_the_host_and_jump_names_are_replaced_in_the_task_and_the_line_is_held_to_plain_characters")],
    ),
    Case(
        "apply task: the allow-list of characters is dropped from the apply's line",
        [Edit(GATED_APPLY, "printf '%s' \"$1\" | tr -c 'A-Za-z0-9 _./:;,()=-' '?'\n          }\n          # A host name is a secret", "printf '%s' \"$1\"\n          }\n          # A host name is a secret")],
        [APPLY("test_the_host_and_jump_names_are_replaced_in_the_task_and_the_line_is_held_to_plain_characters")],
    ),
    Case(
        "apply task: the apply carries its own copy of the task reader",
        [Edit(GATED_APPLY, TASK_READ, "scan_task=$(awk '/^TASK \\[/ { t = $0 } /^fatal: / { print t; exit }' \"$errfile\")")],
        [STRUCTURE("test_the_failed_task_is_read_by_one_script_and_no_step_text_carries_a_copy_of_its_reader")],
    ),
    Case(
        "apply task: the shared commit may be empty",
        [Edit(GATED_APPLY, '[[ "$PLANNED_SHARED_SHA" =~ $sha_re ]] || fail "The plan resolved', 'true || fail "The plan resolved')],
        [APPLY("test_the_shared_actions_are_taken_at_the_commit_the_plan_resolved_and_only_when_it_is_one")],
    ),
    Case(
        "apply task: the shared actions are checked out at the default branch",
        [Edit(GATED_APPLY, "ref: ${{ steps.shared.outputs.sha }}", "ref: main")],
        [STRUCTURE("test_the_apply_job_checks_out_the_shared_actions_at_the_plans_commit_after_the_callers_own_checkout")],
    ),
]
