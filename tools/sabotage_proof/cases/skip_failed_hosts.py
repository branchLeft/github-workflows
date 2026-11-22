"""skip-failed-hosts: one host failing its plan no longer blocks the apply on the others."""

from __future__ import annotations

from ..files import ACTION, GATED_APPLY
from ..model import Case, Edit
from ..suites import APPLY, APPLY_GUARD, APPLY_HOST_KEY, GATE, PLAN
from ..text import indented

CASES: list[Case] = [
    # One host failing its plan no longer blocks the apply on the others
    # (skip-failed-hosts, off by default). The rule that a skipped apply is not accepted is pinned by a gate
    # case in rerun_and_gate.py and by the concurrency cases (the apply group never cancels, no workflow-level group).
    Case(
        "skip-failed-hosts: the report step still exits 1 on a failed host when opted in",
        [Edit(ACTION, 'if [ "$failed" -gt 0 ] && [ "$partial" != true ]; then', 'if [ "$failed" -gt 0 ]; then')],
        [
            PLAN(
                "test_skip_failed_hosts_leaves_the_failed_host_out_and_a_partial_plan_with_nothing_to_change_asks_for_no_approval"
            ),
        ],
    ),
    Case(
        "skip-failed-hosts: a plan in which no host passed is treated as partial and does not fail",
        [
            Edit(
                ACTION,
                indented('&& [ "$failed" -gt 0 ] && [ "$failed" -lt "$total" ]; then', 1),
                indented('&& [ "$failed" -gt 0 ]; then', 1),
            ),
        ],
        [PLAN("test_skip_failed_hosts_still_fails_when_no_host_passed")],
    ),
    Case(
        "skip-failed-hosts: the drift check honours the opt-in and lets a failed host through",
        [
            Edit(
                ACTION,
                indented('&& [ "$FAIL_ON_CHANGES" != true ] && [ "$failed" -gt 0 ]', 1),
                indented('&& [ "$failed" -gt 0 ]', 1),
            ),
        ],
        [PLAN("test_the_drift_check_ignores_skip_failed_hosts")],
    ),
    Case(
        "skip-failed-hosts: the report step lists a failed host among the hosts to apply",
        [
            Edit(
                ACTION,
                indented("*) failed=$((failed + 1)) ;;", 12),
                indented('*) failed=$((failed + 1)); apply_hosts="${apply_hosts:+$apply_hosts }$((10#$n))" ;;', 12),
            ),
        ],
        [PLAN("test_skip_failed_hosts_never_passes_a_withheld_or_oversize_diff_or_any_other_failure_kind")],
    ),
    Case(
        "skip-failed-hosts: a partial plan with a passing host that would change asks for no approval",
        [
            Edit(
                ACTION,
                'if [ "$changed" -eq 0 ] && { [ "$clean" -eq "$total" ] || [ "$partial" = true ]; } && [ "$total" -ge 1 ]; then',
                'if [ "$failed" -gt 0 ] || { [ "$changed" -eq 0 ] && [ "$clean" -eq "$total" ] && [ "$total" -ge 1 ]; }; then',
            ),
        ],
        [
            PLAN(
                "test_skip_failed_hosts_with_a_passing_host_that_would_change_asks_for_the_approval_of_the_hosts_that_passed"
            ),
        ],
    ),
    Case(
        "skip-failed-hosts: the apply guard accepts a partial list when the caller did not opt in",
        [
            Edit(
                GATED_APPLY,
                'if [ "$SKIP_FAILED_HOSTS" != true ]; then\n' + indented('every=""', 12),
                "if false; then\n" + indented('every=""', 12),
            ),
        ],
        [APPLY_GUARD("test_partial_list_refused_when_skip_failed_hosts_is_false")],
    ),
    Case(
        "skip-failed-hosts: the apply guard drops the ascending and unique check",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ "$number" -gt "$previous" ] || fail "The planned apply hosts are not in strictly ascending order."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_GUARD("test_a_list_that_is_empty_not_numbers_not_ascending_repeated_or_out_of_range_is_refused")],
    ),
    Case(
        "skip-failed-hosts: the apply guard drops the range check",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ "$number" -le "$host_count" ] || fail "The planned apply hosts name a host beyond the end of the hosts list."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [
            APPLY_GUARD("test_a_list_that_is_empty_not_numbers_not_ascending_repeated_or_out_of_range_is_refused"),
            APPLY_GUARD("test_a_number_beyond_the_end_is_judged_against_the_hosts_of_this_run"),
        ],
    ),
    Case(
        "skip-failed-hosts: the apply guard drops the digits-only check",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [[ "$PLANNED_APPLY_HOSTS" =~ $numbers_re ]] || fail "The planned apply hosts are not a list of one or more host numbers."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [APPLY_GUARD("test_a_list_that_is_empty_not_numbers_not_ascending_repeated_or_out_of_range_is_refused")],
    ),
    Case(
        "skip-failed-hosts: the apply loop ignores the list and asks every host",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    *" $i "*) ;;
                    *)
                      printf 'Host %s: skipped""",
                    14,
                ),
                indented(
                    r"""
                    *) ;;
                    *)
                      printf 'Host %s: skipped""",
                    14,
                ),
            ),
        ],
        [APPLY("test_a_host_the_guard_did_not_list_is_not_asked_at_all_and_the_summary_says_why")],
    ),
    Case(
        "skip-failed-hosts: the apply loop renumbers the hosts it asks, so a host takes another host's key",
        [
            Edit(
                GATED_APPLY,
                'apply_host "$i" "$dest"',
                'apply_host "$applied" "$dest"',
                count=2,
            ),
        ],
        [APPLY_HOST_KEY("test_a_skipped_host_does_not_renumber_the_ones_after_it_so_each_keeps_its_own_key_file")],
    ),
    Case(
        "skip-failed-hosts: the checks job ignores the hosts that were skipped",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    "" | 0) ;;
                    *)
                      echo "$FAILED_HOSTS""",
                    16,
                ),
                indented(
                    r"""
                    *) ;;
                    *)
                      echo "$FAILED_HOSTS""",
                    16,
                ),
            ),
        ],
        [GATE("test_a_delivery_run_that_skipped_hosts_is_red_while_the_apply_of_the_others_succeeded")],
    ),
]
