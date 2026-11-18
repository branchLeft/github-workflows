"""The drift workflow: what it may hold, what its guard refuses, and when its alert runs."""

from __future__ import annotations

from ..files import DRIFT_CHECK
from ..model import Case, Edit
from ..suites import ALERT, DRIFT_GUARD, STRUCTURE
from ..text import indented

CASES: list[Case] = [
    Case(
        "apply key passed in the drift workflow",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    fail-on-changes: 'true'
                    """,
                    10,
                ),
                indented(
                    r"""
                    fail-on-changes: 'true'
                    apply-key: ${{ secrets.ssh_apply_key }}
                    """,
                    10,
                ),
            ),
        ],
        [
            STRUCTURE("test_the_drift_workflow_takes_the_declared_inputs_and_three_required_secrets_and_no_apply_key"),
            STRUCTURE("test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"),
        ],
    ),
    Case(
        "environment on the drift job",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    timeout-minutes: ${{ inputs.check-timeout-minutes }}
                    """,
                    4,
                ),
                indented(
                    r"""
                    timeout-minutes: ${{ inputs.check-timeout-minutes }}
                    environment: production
                    """,
                    4,
                ),
            ),
        ],
        [STRUCTURE("test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies")],
    ),
    Case(
        "drift guard accepts every event",
        [Edit(DRIFT_CHECK, indented("schedule | workflow_dispatch) ;;", 12), indented("*) ;;", 12))],
        [DRIFT_GUARD("test_every_other_event_branch_fork_commit_or_label_is_refused_and_flagged_as_a_refusal")],
    ),
    Case(
        "drift guard drops the fork check",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    [ "$IS_FORK" = false ] || fail "The drift check runs only in the repository itself, never in a fork."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [DRIFT_GUARD("test_every_other_event_branch_fork_commit_or_label_is_refused_and_flagged_as_a_refusal")],
    ),
    Case(
        "write permission on the drift job",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    permissions:
                      actions: read
                      contents: read
                    """,
                    4,
                ),
                indented(
                    r"""
                    permissions:
                      actions: read
                      contents: write
                    """,
                    4,
                ),
            ),
        ],
        [STRUCTURE("test_issues_write_is_granted_to_the_alert_job_alone_and_nothing_else_may_write")],
    ),
    Case(
        "alert condition without the guard-refusal test",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    && needs.drift.outputs.guard_refused != 'true'
                    """,
                    6,
                ),
                "",
            ),
        ],
        [
            STRUCTURE(
                "test_the_alert_runs_after_a_failed_or_timed_out_check_that_passed_the_guard_and_serialises_per_host_set"
            ),
        ],
    ),
    Case(
        "alert never comments",
        [Edit(DRIFT_CHECK, 'if [ -n "$number" ]; then', "if false; then")],
        [ALERT("test_with_an_open_issue_a_comment_is_added_to_that_issue_and_none_is_created")],
    ),
    Case(
        "issue created without its label",
        [
            Edit(
                DRIFT_CHECK,
                'gh issue create --title "$title" --label "$label" --body-file "$body"',
                'gh issue create --title "$title" --body-file "$body"',
            ),
        ],
        [ALERT("test_with_no_open_issue_one_is_created_with_the_label_and_the_title_and_no_comment_is_made")],
    ),
    Case(
        "detail not cut to the safe alphabet",
        [
            Edit(
                DRIFT_CHECK,
                "detail=$(printf '%s' \"$DETAIL\" | tr -cd 'A-Za-z0-9 :;()._-' | head -c 300)",
                "detail=$(printf '%s' \"$DETAIL\" | head -c 300)",
            ),
        ],
        [ALERT("test_a_missing_outcome_says_unknown_and_anything_outside_the_safe_alphabet_is_dropped")],
    ),
    Case(
        "host-shaped pattern added to the drift guard",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    sha_re='^[0-9a-f]{40}$'
                    label_re=""",
                    10,
                ),
                indented(
                    r"""
                    host_re='^[a-z_][a-z0-9_-]{0,31}@'
                    sha_re='^[0-9a-f]{40}$'
                    label_re=""",
                    10,
                ),
            ),
        ],
        [STRUCTURE("test_the_host_list_is_validated_in_the_action_and_nowhere_else")],
    ),
    Case(
        "check timeout range not validated in the drift guard",
        [Edit(DRIFT_CHECK, '{ [ "$CHECK_TIMEOUT" -ge 10 ] && [ "$CHECK_TIMEOUT" -le 350 ]; } \\\n', "true \\\n")],
        [DRIFT_GUARD("test_every_other_event_branch_fork_commit_or_label_is_refused_and_flagged_as_a_refusal")],
    ),
    Case(
        "hosts given the whole job bound",
        [
            Edit(
                DRIFT_CHECK,
                'echo "budget=$((CHECK_TIMEOUT - 5))" >> "$GITHUB_OUTPUT"',
                'echo "budget=$((CHECK_TIMEOUT))" >> "$GITHUB_OUTPUT"',
            ),
        ],
        [DRIFT_GUARD("test_the_hosts_get_five_minutes_less_than_the_job_bound")],
    ),
    Case(
        "drift job given no total time",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    total-timeout: ${{ steps.guard.outputs.budget }}
                    """,
                    10,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_drift_job_is_bounded_by_the_caller_input_and_gives_the_hosts_less_than_that")],
    ),
    Case(
        "alert ignores a timed-out check",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    && (needs.drift.result == 'failure' || needs.drift.result == 'cancelled')
                    """,
                    6,
                ),
                indented(
                    r"""
                    && needs.drift.result == 'failure'
                    """,
                    6,
                ),
            ),
        ],
        [
            STRUCTURE(
                "test_the_alert_runs_after_a_failed_or_timed_out_check_that_passed_the_guard_and_serialises_per_host_set"
            ),
        ],
    ),
    Case(
        "drift alert: guard-refused term joined with or",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    && needs.drift.outputs.guard_refused != 'true'
                    """,
                    6,
                ),
                indented(
                    r"""
                    || needs.drift.outputs.guard_refused != 'true'
                    """,
                    6,
                ),
            ),
        ],
        [
            STRUCTURE(
                "test_the_alert_runs_after_a_failed_or_timed_out_check_that_passed_the_guard_and_serialises_per_host_set"
            ),
        ],
    ),
    Case(
        "drift job does not pass the jump key",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    jump-key: ${{ secrets.ssh_jump_key }}
                    """,
                    10,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies")],
    ),
    Case(
        "drift jump secrets declared as required",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                      line goes in ssh_known_hosts.
                    required: false
                    """,
                    8,
                ),
                indented(
                    r"""
                      line goes in ssh_known_hosts.
                    required: true
                    """,
                    8,
                ),
            ),
        ],
        [
            STRUCTURE("test_both_workflows_declare_the_two_jump_secrets_as_optional_and_say_both_or_neither"),
            STRUCTURE("test_the_drift_workflow_takes_the_declared_inputs_and_three_required_secrets_and_no_apply_key"),
        ],
    ),
]
