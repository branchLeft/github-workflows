"""The jump host: the hop of the ssh-check-hosts action and its copy in the gated apply."""

from __future__ import annotations

from ..files import ACTION, GATED_APPLY
from ..model import Case, Edit
from ..suites import APPLY_JUMP, SSH_JUMP, SSH_OPTION, STRUCTURE
from ..text import indented

CASES: list[Case] = [
    Case(
        "jump hop stanza drops StrictHostKeyChecking",
        [Edit(ACTION, "\n" + indented(r"printf '  StrictHostKeyChecking yes\n'", 16), "")],
        [
            SSH_JUMP("test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones"),
            SSH_JUMP("test_the_config_file_is_exactly_one_stanza_for_the_hop"),
        ],
    ),
    Case(
        "jump hop stanza drops IdentitiesOnly",
        [Edit(ACTION, "\n" + indented(r"printf '  IdentitiesOnly yes\n'", 16), "")],
        [SSH_JUMP("test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones")],
    ),
    Case(
        "jump hop stanza drops BatchMode",
        [Edit(ACTION, "\n" + indented(r"printf '  BatchMode yes\n'", 16), "")],
        [SSH_JUMP("test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones")],
    ),
    Case(
        "jump hop stanza reads the global known hosts",
        [Edit(ACTION, "\n" + indented(r"printf '  GlobalKnownHostsFile /dev/null\n'", 16), "")],
        [SSH_JUMP("test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones")],
    ),
    Case(
        "jump hop stanza drops ClearAllForwardings",
        [Edit(ACTION, "\n" + indented(r"printf '  ClearAllForwardings yes\n'", 16), "")],
        [SSH_JUMP("test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones")],
    ),
    Case(
        "jump hop uses the check key",
        [
            Edit(
                ACTION,
                "printf '  IdentityFile %s\\n' \"$plan_dir/jump_id\"",
                "printf '  IdentityFile %s\\n' \"$plan_dir/id\"",
            ),
        ],
        [SSH_JUMP("test_the_check_key_is_never_offered_to_the_gateway_and_the_jump_key_never_to_a_target")],
    ),
    Case(
        "jump host without a jump key passes validation",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    [ -n "$JUMP_KEY" ] || fail "jump-host is set but jump-key is empty: a jump needs both or neither."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP("test_only_one_of_the_two_inputs_fails_the_validate_step_and_names_the_missing_one")],
    ),
    Case(
        "jump key without a jump host passes validation",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    [ -n "$JUMP_HOST" ] || fail "jump-key is set but jump-host is empty: a jump needs both or neither."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP("test_only_one_of_the_two_inputs_fails_the_validate_step_and_names_the_missing_one")],
    ),
    Case(
        "jump host with a second line passes validation",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    case "$JUMP_HOST" in *$'\n'*) fail "jump-host is not of the form user@host." ;; esac
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP("test_a_malformed_jump_host_fails_the_validate_step_and_is_never_echoed")],
    ),
    Case(
        "jump host form is not checked",
        [
            Edit(
                ACTION,
                'printf \'%s\' "$JUMP_HOST" | grep -Eq "$destination_pattern"',
                "printf '%s' \"$JUMP_HOST\" | grep -Eq '.'",
            ),
        ],
        [SSH_JUMP("test_a_malformed_jump_host_fails_the_validate_step_and_is_never_echoed")],
    ),
    Case(
        "host names may hold an underscore",
        [
            Edit(
                ACTION,
                "@[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'\n",
                "@[A-Za-z0-9]([A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$'\n",
            ),
        ],
        [SSH_JUMP("test_the_alias_of_the_hop_is_not_a_name_the_hosts_input_can_hold")],
    ),
    Case(
        "trap leaves the jump key and config behind",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    trap 'rm -f "$plan_dir/id" "$plan_dir/known_hosts" "$plan_dir"/id.* "$plan_dir/jump_id" "$plan_dir/jump_config"' EXIT
                    """,
                    12,
                ),
                "",
            ),
        ],
        [SSH_JUMP("test_the_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out")],
    ),
    Case(
        "jump key and config written with the runner umask",
        [
            Edit(
                ACTION,
                indented(
                    r'''
                    umask 077
                    printf '%s\n' "$JUMP_KEY"''',
                    14,
                ),
                indented("printf '%s\\n' \"$JUMP_KEY\"", 14),
            ),
        ],
        [SSH_JUMP("test_the_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out")],
    ),
    Case(
        "jump hop configured when no jump is set",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    if [ -n "$JUMP_HOST" ]; then
                      # ssh gives""",
                    10,
                ),
                indented(
                    r"""
                    if true; then
                      # ssh gives""",
                    10,
                ),
            ),
        ],
        [SSH_JUMP("test_with_no_jump_the_ssh_argv_the_files_and_the_removals_are_exactly_what_they_were")],
    ),
    Case(
        "jump call drops the proxy jump option",
        [
            Edit(
                ACTION,
                'ssh_args+=(-F "$plan_dir/jump_config" -o ProxyJump=jump_hop)',
                'ssh_args+=(-F "$plan_dir/jump_config")',
            ),
        ],
        [SSH_JUMP("test_a_jump_adds_the_config_file_and_the_proxy_jump_option_after_the_unchanged_options")],
    ),
    Case(
        "jump host left in the text of a host",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    if (jd != "") { s = mark(mark(s, jd, "\002"), jh, "\002") }
                    """,
                    20,
                ),
                "",
            ),
        ],
        [SSH_JUMP("test_the_jump_host_is_replaced_in_a_host_text_that_reaches_the_summary")],
    ),
    Case(
        "a failing jump is not unreachable",
        [
            Edit(
                ACTION,
                'elif [ "$rc" = 255 ]; then\n' + indented('note="the SSH connection failed (exit 255)"', 12),
                'elif [ "$rc" = 254 ]; then\n' + indented('note="the SSH connection failed (exit 255)"', 12),
            ),
        ],
        [SSH_JUMP("test_a_failing_jump_is_reported_as_unreachable_and_no_name_reaches_any_output")],
    ),
    # The jump in the gated apply and the drift check: the controls of the apply step's copy of the hop.
    Case(
        "apply jump hop stanza drops StrictHostKeyChecking",
        [Edit(GATED_APPLY, "\n" + indented(r"printf '  StrictHostKeyChecking yes\n'", 16), "")],
        [
            APPLY_JUMP("test_the_hop_has_its_own_strict_options_in_the_apply_too"),
            APPLY_JUMP("test_the_stanza_the_destination_pattern_and_the_trap_in_the_apply_step_equal_the_actions"),
            APPLY_JUMP("test_the_apply_step_writes_the_same_config_and_key_as_the_action_for_the_same_jump"),
        ],
    ),
    Case(
        "apply jump hop uses the apply key",
        [
            Edit(
                GATED_APPLY,
                "printf '  IdentityFile %s\\n' \"$work/jump_id\"",
                "printf '  IdentityFile %s\\n' \"$work/id\"",
            ),
        ],
        [
            APPLY_JUMP("test_the_apply_key_is_never_offered_to_the_gateway_and_the_jump_key_never_to_a_target"),
            APPLY_JUMP("test_the_apply_step_writes_the_same_config_and_key_as_the_action_for_the_same_jump"),
        ],
    ),
    Case(
        "apply trap leaves the jump key and config behind",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    trap 'rm -f "$work/id" "$work/known_hosts" "$work"/id.* "$work/host.err" "$work/keylines" "$work/jump_id" "$work/jump_config"' EXIT
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_JUMP("test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out")],
    ),
    Case(
        "apply jump key and config written with the runner umask",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r'''
                    umask 077
                    printf '%s\n' "$JUMP_KEY"''',
                    14,
                ),
                indented("printf '%s\\n' \"$JUMP_KEY\"", 14),
            ),
        ],
        [APPLY_JUMP("test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out")],
    ),
    Case(
        "apply jump call drops the proxy jump option",
        [
            Edit(
                GATED_APPLY,
                'ssh_args+=(-F "$work/jump_config" -o ProxyJump=jump_hop)',
                'ssh_args+=(-F "$work/jump_config")',
            ),
        ],
        [APPLY_JUMP("test_a_jump_adds_the_config_file_and_the_proxy_jump_option_after_the_unchanged_options")],
    ),
    Case(
        "apply jump host without a jump key is accepted",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ -n "$JUMP_KEY" ] || fail "ssh_jump_host is set but ssh_jump_key is empty: a jump needs both or neither."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_JUMP("test_only_one_of_the_two_secrets_fails_before_any_file_or_host_and_names_the_missing_one")],
    ),
    Case(
        "apply jump key without a jump host is accepted",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ -n "$JUMP_HOST" ] || fail "ssh_jump_key is set but ssh_jump_host is empty: a jump needs both or neither."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_JUMP("test_only_one_of_the_two_secrets_fails_before_any_file_or_host_and_names_the_missing_one")],
    ),
    Case(
        "apply jump host with a second line is accepted",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    case "$JUMP_HOST" in *$'\n'*) fail "ssh_jump_host is not of the form user@host." ;; esac
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_JUMP("test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed")],
    ),
    Case(
        "apply jump host form is not checked",
        [
            Edit(
                GATED_APPLY,
                'printf \'%s\' "$JUMP_HOST" | grep -Eq "$destination_pattern"',
                "printf '%s' \"$JUMP_HOST\" | grep -Eq '.'",
            ),
        ],
        [APPLY_JUMP("test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed")],
    ),
    Case(
        "apply jump host echoed in its refusal",
        [
            Edit(
                GATED_APPLY,
                'fail "ssh_jump_host is not of the form user@host."',
                'fail "ssh_jump_host $JUMP_HOST is not of the form user@host."',
                count=2,
            ),
        ],
        [APPLY_JUMP("test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed")],
    ),
    Case(
        "apply destination pattern allows an underscore",
        [
            Edit(
                GATED_APPLY,
                "@[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'\n",
                "@[A-Za-z0-9]([A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$'\n",
            ),
        ],
        [
            APPLY_JUMP("test_the_stanza_the_destination_pattern_and_the_trap_in_the_apply_step_equal_the_actions"),
            APPLY_JUMP("test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed"),
        ],
    ),
    Case(
        "apply jump set when no jump is given",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    if [ -n "$JUMP_HOST" ]; then
                      # ssh gives""",
                    10,
                ),
                indented(
                    r"""
                    if true; then
                      # ssh gives""",
                    10,
                ),
            ),
        ],
        [
            APPLY_JUMP("test_with_no_jump_the_ssh_argv_the_files_and_the_removals_are_exactly_what_they_were"),
            SSH_OPTION("test_the_plan_and_apply_calls_pass_the_same_exact_ssh_options"),
        ],
    ),
    Case(
        "apply jump hop stanza drops ClearAllForwardings",
        [Edit(GATED_APPLY, "\n" + indented(r"printf '  ClearAllForwardings yes\n'", 16), "")],
        [APPLY_JUMP("test_the_hop_has_its_own_strict_options_in_the_apply_too")],
    ),
    Case(
        "apply jump hop stanza drops IdentitiesOnly",
        [Edit(GATED_APPLY, "\n" + indented(r"printf '  IdentitiesOnly yes\n'", 16), "")],
        [APPLY_JUMP("test_the_hop_has_its_own_strict_options_in_the_apply_too")],
    ),
    Case(
        "apply ssh error text reaches the job log",
        [Edit(GATED_APPLY, '-- "$dest" "$SHA" < /dev/null 2> "$errfile")', '-- "$dest" "$SHA" < /dev/null)')],
        [APPLY_JUMP("test_the_jump_host_and_keys_reach_neither_the_log_nor_the_summary_of_a_successful_apply")],
    ),
    Case(
        "apply guard step reads the jump host",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    PLANNED_DIGEST: ${{ needs.plan.outputs.hosts_digest }}
                    """,
                    10,
                ),
                indented(
                    r"""
                    PLANNED_DIGEST: ${{ needs.plan.outputs.hosts_digest }}
                    JUMP_HOST: ${{ secrets.ssh_jump_host }}
                    """,
                    10,
                ),
            ),
        ],
        [
            STRUCTURE("test_the_apply_guard_step_and_the_steps_before_the_action_hold_no_jump_secret"),
            STRUCTURE("test_the_jump_secrets_are_read_by_the_plan_jobs_action_step_and_the_apply_step_only"),
            STRUCTURE("test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source"),
        ],
    ),
    Case(
        "plan job does not pass the jump host",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    jump-host: ${{ secrets.ssh_jump_host }}
                    """,
                    10,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs")],
    ),
    Case(
        "plan job passes the jump key to the check key input",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    check-key: ${{ secrets.ssh_check_key }}
                    """,
                    10,
                ),
                indented(
                    r"""
                    check-key: ${{ secrets.ssh_jump_key }}
                    """,
                    10,
                ),
            ),
        ],
        [
            STRUCTURE("test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"),
            STRUCTURE("test_each_key_is_read_by_one_job_only"),
        ],
    ),
    Case(
        "gated jump key secret not declared",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    ssh_jump_key:
                    """,
                    6,
                ),
                indented(
                    r"""
                    ssh_jump_key_unused:
                    """,
                    6,
                ),
            ),
        ],
        [STRUCTURE("test_both_workflows_declare_the_two_jump_secrets_as_optional_and_say_both_or_neither")],
    ),
]
