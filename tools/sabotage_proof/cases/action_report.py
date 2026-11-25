"""The ssh-check-hosts action: how it asks the hosts, judges their answers and reports them."""

from __future__ import annotations

from ..files import ACTION, GATED_APPLY
from ..model import Case, Edit
from ..suites import APPLY, DRIFT_REPORT, PLAN, REPORT_LOG, STRUCTURE, VALIDATE_HOSTS
from ..text import indented

CASES: list[Case] = [
    Case(
        "drift exits green on one changed host",
        [
            Edit(
                ACTION,
                'if [ "$FAIL_ON_CHANGES" = true ] && [ "$changed" -gt 0 ]; then',
                'if [ "$FAIL_ON_CHANGES" = true ] && [ "$changed" -gt 1 ]; then',
            ),
        ],
        [DRIFT_REPORT("test_a_host_that_would_change_is_red_in_a_drift_check_and_green_in_a_plan")],
    ),
    Case(
        "a withheld diff counts toward the total that caps the later hosts",
        [
            Edit(
                ACTION,
                '          if [ "$show" = true ]; then\n            total_bytes=$((total_bytes + size))\n          fi\n',
                '          total_bytes=$((total_bytes + size))\n',
            ),
        ],
        [REPORT_LOG("test_a_withheld_diff_does_not_count_toward_the_total_that_caps_the_later_hosts")],
    ),
    Case(
        "the per-host plan files are numbered with two digits again, so past 99 hosts they sort out of order",
        [Edit(ACTION, "n=$(printf '%05d' \"$i\")", "n=$(printf '%02d' \"$i\")")],
        [PLAN("test_the_apply_hosts_stay_ascending_past_99_hosts")],
    ),
    Case(
        "busy exit code changed",
        [
            Edit(
                ACTION,
                'if [ "$rc" = 21 ] && [ "$f_verdict" = busy ]; then',
                'if [ "$rc" = 22 ] && [ "$f_verdict" = busy ]; then',
            ),
        ],
        [DRIFT_REPORT("test_a_refusal_is_not_drift_and_names_its_reason")],
    ),
    Case(
        "check judge regex drops the dash count of a refusal",
        [Edit(ACTION, "changed=([0-9]+|-)( floor=[0-9a-f]{40})?$'", "changed=([0-9]+)( floor=[0-9a-f]{40})?$'")],
        [DRIFT_REPORT("test_a_real_refusal_line_with_no_count_is_read_as_its_refusal")],
    ),
    Case(
        "check judge regex drops the floor of a rollback refusal",
        [Edit(ACTION, "changed=([0-9]+|-)( floor=[0-9a-f]{40})?$'", "changed=([0-9]+|-)$'")],
        [DRIFT_REPORT("test_a_real_refusal_line_that_carries_the_hosts_floor_is_read_as_its_refusal")],
    ),
    Case(
        "would-change with a dash count is read as drift",
        [Edit(ACTION, indented('&& [ "$f_changed" != - ]; then', 1), "; then")],
        [DRIFT_REPORT("test_a_dash_count_never_makes_an_unchanged_host_clean_or_a_would_change_host_drift")],
    ),
    Case(
        "host list joined with a comma in the action",
        [Edit(ACTION, 'list="${list:+$list }$dest"', 'list="${list:+$list,}$dest"')],
        [VALIDATE_HOSTS("test_the_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes")],
    ),
    Case(
        "expression inside an action run body",
        [
            Edit(
                ACTION,
                'echo "Checking commit $SHA on $count host(s)."',
                'echo "Checking commit ${{ inputs.sha }} on $count host(s)."',
            ),
        ],
        [STRUCTURE("test_no_expression_inside_a_run_body")],
    ),
    Case(
        "shell dropped from an action step",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    id: validate
                    shell: bash
                    """,
                    6,
                ),
                indented(
                    r"""
                    id: validate
                    """,
                    6,
                ),
            ),
        ],
        [STRUCTURE("test_every_run_step_of_the_action_names_bash_and_stops_on_error")],
    ),
    Case(
        "pipefail dropped from an action step",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    set -euo pipefail
                    fail() {
                      echo "::error::$1"
                      exit 1
                    }
                    sha_re=""",
                    8,
                ),
                indented(
                    r"""
                    set -eu
                    fail() {
                      echo "::error::$1"
                      exit 1
                    }
                    sha_re=""",
                    8,
                ),
            ),
        ],
        [STRUCTURE("test_every_run_step_of_the_action_names_bash_and_stops_on_error")],
    ),
    Case(
        "plan summary line reworded",
        [Edit(ACTION, 'first="Changes: $changed of $total host(s)', 'first="Change: $changed of $total host(s)')],
        [PLAN("test_one_host_with_changes_gives_has_changes_and_shows_its_diff")],
    ),
    # The stdin defect, the key copy, the time given to the hosts.
    Case(
        "ssh in the action takes the host list from its own input",
        [Edit(ACTION, '-- "$dest" "$SHA" < /dev/null >', '-- "$dest" "$SHA" >')],
        [PLAN("test_every_host_is_asked_when_the_call_reads_its_standard_input")],
    ),
    Case(
        "dry-run stand-in in the action takes the host list from its own input",
        [Edit(ACTION, '"$stub" "$dest" "$SHA" < /dev/null >', '"$stub" "$dest" "$SHA" >')],
        [PLAN("test_every_host_is_asked_when_the_dry_run_stand_in_reads_its_standard_input")],
    ),
    Case(
        "ssh in the apply takes the host list from its own input",
        [Edit(GATED_APPLY, '-- "$dest" "$SHA" < /dev/null 2> "$errfile")', '-- "$dest" "$SHA" 2> "$errfile")')],
        [APPLY("test_every_planned_host_is_applied_and_checked_again_when_the_call_reads_its_standard_input")],
    ),
    Case(
        "key copy not removed",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    trap 'rm -f "$keylines"' EXIT
                    """,
                    8,
                ),
                "",
            ),
        ],
        [PLAN("test_key_material_is_copied_private_and_removed_on_every_way_out")],
    ),
    Case(
        "key copy removed only on the way out that succeeds",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    trap 'rm -f "$keylines"' EXIT
                    """,
                    8,
                ),
                "",
            ),
            Edit(
                ACTION,
                indented(
                    r"""
                    echo "$first"
                    """,
                    8,
                ),
                indented(
                    r"""
                    echo "$first"
                    rm -f "$keylines"
                    """,
                    8,
                ),
            ),
        ],
        [PLAN("test_key_material_is_copied_private_and_removed_on_every_way_out")],
    ),
    # The key copies of the check and the apply go on every way out.
    Case(
        "check key copies not removed",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    trap 'rm -f "$plan_dir/id" "$plan_dir/known_hosts" "$plan_dir"/id.*' EXIT
                    """,
                    8,
                ),
                "",
            ),
        ],
        [PLAN("test_the_check_key_and_known_hosts_are_removed_on_every_way_out_a_failure_included")],
    ),
    Case(
        "check key copies removed only on the way out that succeeds",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    trap 'rm -f "$plan_dir/id" "$plan_dir/known_hosts" "$plan_dir"/id.*' EXIT
                    """,
                    8,
                ),
                "",
            ),
            Edit(
                ACTION,
                indented(
                    r"""
                    echo "Asked $i host(s); the answers are judged in the next step."
                    """,
                    8,
                ),
                indented(
                    r"""
                    rm -f "$plan_dir/id" "$plan_dir/known_hosts"
                    echo "Asked $i host(s); the answers are judged in the next step."
                    """,
                    8,
                ),
            ),
        ],
        [PLAN("test_the_check_key_and_known_hosts_are_removed_on_every_way_out_a_failure_included")],
    ),
    Case(
        "apply key copies not removed",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    trap 'rm -f "$work/id" "$work/known_hosts" "$work"/id.* "$work/host.err" "$work/keylines"' EXIT
                    """,
                    10,
                ),
                "",
            ),
        ],
        [APPLY("test_the_apply_key_and_known_hosts_are_removed_on_every_way_out_a_refusal_included")],
    ),
    Case(
        "apply key copies removed only on the way out that succeeds",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    trap 'rm -f "$work/id" "$work/known_hosts" "$work"/id.* "$work/host.err" "$work/keylines"' EXIT
                    """,
                    10,
                ),
                "",
            ),
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    echo "Applied commit $SHA on $applied host(s)."
                    """,
                    10,
                ),
                indented(
                    r"""
                    rm -f "$work/id" "$work/known_hosts"
                    echo "Applied commit $SHA on $applied host(s)."
                    """,
                    10,
                ),
            ),
        ],
        [APPLY("test_the_apply_key_and_known_hosts_are_removed_on_every_way_out_a_refusal_included")],
    ),
    Case(
        "key copy made with the default mask",
        [Edit(ACTION, '(umask 077 && : > "$keylines")', ': > "$keylines"')],
        [PLAN("test_key_material_is_copied_private_and_removed_on_every_way_out")],
    ),
    Case(
        "total time not applied to a call",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    limit="$left"
                    """,
                    14,
                ),
                indented(
                    r"""
                    limit="$limit"
                    """,
                    14,
                ),
            ),
        ],
        [PLAN("test_with_a_total_time_each_call_is_cut_to_what_is_left_and_a_host_reached_after_it_is_not_asked")],
    ),
    Case(
        "host reached after the total time is still asked",
        [Edit(ACTION, 'if [ "$limit" -le 0 ]; then', "if false; then")],
        [PLAN("test_with_a_total_time_each_call_is_cut_to_what_is_left_and_a_host_reached_after_it_is_not_asked")],
    ),
    Case(
        "total time longer than the host limit overrides it",
        [Edit(ACTION, 'if [ "$left" -lt "$limit" ]; then', 'if [ "$left" -ne "$limit" ]; then')],
        [PLAN("test_a_total_time_longer_than_the_host_limit_leaves_the_host_limit_alone")],
    ),
    Case(
        "host limit not validated",
        [
            Edit(
                ACTION,
                '[[ "$HOST_TIMEOUT_MINUTES" =~ $minutes_re ]] || fail "host-timeout must be a whole number of minutes, at least 1."',
                ":",
            ),
        ],
        [VALIDATE_HOSTS("test_the_timeouts_are_whole_numbers_of_minutes_and_the_total_may_be_empty")],
    ),
]
