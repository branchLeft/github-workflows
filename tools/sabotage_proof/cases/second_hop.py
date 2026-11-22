"""The second hop: listed hosts are reached through two jump hosts, every other host through one."""

from __future__ import annotations

from ..files import ACTION, DRIFT_CHECK, GATED_APPLY
from ..model import Case, Edit
from ..suites import APPLY_JUMP, APPLY_JUMP_2, JUMP_2_APPLY_GUARD, JUMP_PARITY, SSH_JUMP_2, STRUCTURE
from ..text import indented

CASES: list[Case] = [
    # The second hop. The control is that the second hop applies to the
    # listed hosts and only to them; the rest hold its validation, its keys and its
    # silence.
    Case(
        "second hop: the chain is applied to every host",
        [
            Edit(
                ACTION,
                indented('if [ -n "$JUMP2_HOSTS" ] && grep -qxF -- "$dest" <<< "$JUMP2_HOSTS"; then', 10),
                indented('if [ -n "$JUMP2_HOSTS" ]; then', 10),
            ),
        ],
        [SSH_JUMP_2("test_only_listed_hosts_get_the_two_hop_chain")],
    ),
    Case(
        "second hop: the chain is dropped for the listed hosts",
        [Edit(ACTION, "=ProxyJump=jump_hop,jump_hop2", "=ProxyJump=jump_hop")],
        [SSH_JUMP_2("test_a_listed_host_is_called_through_both_hops")],
    ),
    Case(
        "second hop: a listed host is called without the second hop when none is set",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    if [ "$DRY_RUN" != true ] && [ -n "$JUMP2_HOSTS" ] && [ "${#ssh_args_two[@]}" -eq 0 ]; then
                      fail "jump2-hosts is set but no second hop is configured."
                    fi
                    """,
                    8,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_a_second_hop_listed_with_no_hop_set_fails_closed_even_if_validation_were_skipped")],
    ),
    Case(
        "second hop: a host with no key is accepted",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    [ -n "$JUMP2_KEY" ] || fail "jump2-host is set but jump2-key is empty: a second hop needs both or neither."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value")],
    ),
    Case(
        "second hop: a key with no host is accepted",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    [ -n "$JUMP2_HOST" ] || fail "jump2-key is set but jump2-host is empty: a second hop needs both or neither."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value")],
    ),
    Case(
        "second hop: a second hop without the first hop is accepted",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    { [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "A second hop needs jump-host and jump-key: it is reached through the first hop."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value")],
    ),
    Case(
        "second hop: a list with no second hop is accepted",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    if [ -z "$JUMP2_HOST" ] && [ -z "$JUMP2_KEY" ]; then
                      fail "jump2-hosts is set but there is no second hop (jump2-host and jump2-key are empty): a listed host would fall back to one hop."
                    fi
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value")],
    ),
    Case(
        "second hop: the second hop's own address is accepted as a target",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    [ "$(printf '%s' "${dest#*@}" | tr '[:upper:]' '[:lower:]')" != "$jump2_gateway" ] \
                      || fail "A line of jump2-hosts is the address of the second hop itself: that host is reached through the first hop alone."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value")],
    ),
    Case(
        "second hop: the first hop's key is accepted as the second hop's",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    [ "$JUMP2_KEY" != "$JUMP_KEY" ] || fail "jump2-key is the same as jump-key: each hop has its own key."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value")],
    ),
    Case(
        "second hop: the check key is accepted as the second hop's",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    [ "$JUMP2_KEY" != "$SSH_KEY" ] || fail "jump2-key is the same as this job's own key: the second hop has a key of its own."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_the_check_key_is_refused_as_the_second_hop_key_before_any_file_or_host")],
    ),
    Case(
        "second hop: the apply key is accepted as the second hop's",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ "$JUMP2_KEY" != "$SSH_KEY" ] || fail "ssh_jump2_key is the same as this job's own key: the second hop has a key of its own."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_JUMP("test_the_apply_key_is_refused_as_the_second_hop_key_before_any_file_or_host")],
    ),
    Case(
        "second hop: a listed host outside the hosts list is accepted",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    grep -qxF -- "$dest" <<< "$HOSTS" || fail "A line of jump2-hosts is not in the hosts list."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value")],
    ),
    Case(
        "second hop: the second key and the config are written with the runner umask",
        [
            Edit(
                ACTION,
                indented(
                    r'''
                    umask 077
                    printf '%s\n' "$JUMP2_KEY"''',
                    16,
                ),
                indented("printf '%s\\n' \"$JUMP2_KEY\"", 16),
            ),
        ],
        [SSH_JUMP_2("test_the_second_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out")],
    ),
    Case(
        "second hop: the trap leaves the second key behind",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    trap 'rm -f "$plan_dir/id" "$plan_dir/known_hosts" "$plan_dir"/id.* "$plan_dir/jump_id" "$plan_dir/jump_config" "$plan_dir/jump2_id"' EXIT
                    """,
                    14,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_the_second_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out")],
    ),
    Case(
        "second hop: the second hop's name is left in the text of a host",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    if (jd2 != "") { s = mark(mark(s, jd2, "\003"), jh2, "\003") }
                    """,
                    20,
                ),
                "",
            ),
        ],
        [SSH_JUMP_2("test_the_second_hops_name_is_replaced_in_a_host_text_that_reaches_the_summary")],
    ),
    Case(
        "second hop: the second stanza drops IdentitiesOnly",
        [
            Edit(
                ACTION,
                '"$plan_dir/jump2_id"\n'
                + indented(
                    r"""
                    printf '  IdentitiesOnly yes\n'
                    """,
                    18,
                ),
                '"$plan_dir/jump2_id"\n',
            ),
        ],
        [SSH_JUMP_2("test_the_second_hop_has_its_own_strict_options_and_its_own_key_file")],
    ),
    Case(
        "second hop: the second stanza uses the first hop's key file",
        [
            Edit(
                ACTION,
                "printf '  IdentityFile %s\\n' \"$plan_dir/jump2_id\"",
                "printf '  IdentityFile %s\\n' \"$plan_dir/jump_id\"",
            ),
        ],
        [SSH_JUMP_2("test_the_second_hop_has_its_own_strict_options_and_its_own_key_file")],
    ),
    Case(
        "second hop: the second stanza reads the global known hosts",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                      printf '  GlobalKnownHostsFile /dev/null\n'
                      printf '  ConnectTimeout 30\n'
                      printf '  ServerAliveInterval 30\n'
                      printf '  ForwardAgent no\n'
                      printf '  ClearAllForwardings yes\n'
                    } >> """,
                    16,
                ),
                indented(
                    r"""
                      printf '  ConnectTimeout 30\n'
                      printf '  ServerAliveInterval 30\n'
                      printf '  ForwardAgent no\n'
                      printf '  ClearAllForwardings yes\n'
                    } >> """,
                    16,
                ),
            ),
        ],
        [SSH_JUMP_2("test_the_second_hop_has_its_own_strict_options_and_its_own_key_file")],
    ),
    Case(
        "second hop: the drift check passes no second-hop list",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    jump2-hosts: ${{ secrets.ssh_jump2_hosts }}
                    """,
                    10,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies")],
    ),
    Case(
        "second hop: the apply digest check is removed",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ "$jump2_digest" = "$PLANNED_JUMP2_DIGEST" ] || fail "The second-hop hosts are not the second-hop hosts that were planned."
                    """,
                    10,
                ),
                "",
            ),
        ],
        [
            JUMP_2_APPLY_GUARD(
                "test_a_list_the_plan_did_not_have_a_list_the_plan_had_and_a_different_list_are_refused"
            ),
        ],
    ),
    Case(
        "second hop: the apply guard accepts a missing planned digest",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    printf '%s' "$PLANNED_JUMP2_DIGEST" | grep -Eq '^[0-9a-f]{64}$' || fail "The planned second-hop digest is missing."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [JUMP_2_APPLY_GUARD("test_a_list_with_no_host_in_it_is_refused_whatever_the_plan_said")],
    ),
    Case(
        "second hop: the plan job drops the second-hop digest",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    jump2_hosts_digest: ${{ steps.ssh.outputs.jump2-hosts-digest }}
                    """,
                    6,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_plan_job_outputs_come_from_the_steps_that_make_them")],
    ),
    Case(
        "second hop: the apply chain is applied to every host",
        [
            Edit(
                GATED_APPLY,
                indented('if [ -n "$JUMP2_HOSTS" ] && grep -qxF -- "$dest" <<< "$JUMP2_HOSTS"; then', 12),
                indented('if [ -n "$JUMP2_HOSTS" ]; then', 12),
            ),
        ],
        [APPLY_JUMP_2("test_a_listed_host_is_called_through_both_hops_and_every_other_host_through_the_first_alone")],
    ),
    Case(
        "second hop: the action's one-call function drifts from the apply step's",
        [
            Edit(
                ACTION,
                indented(
                    r"""
                    args=("${ssh_args_two[@]}")
                    """,
                    12,
                ),
                indented(
                    r"""
                    args=("${ssh_args[@]}")
                    """,
                    12,
                ),
            ),
        ],
        [JUMP_PARITY("test_the_one_call_function_is_the_same_text_in_the_action_and_in_the_apply_step")],
    ),
    Case(
        "second hop: the apply chain is dropped for the listed hosts",
        [Edit(GATED_APPLY, "=ProxyJump=jump_hop,jump_hop2", "=ProxyJump=jump_hop")],
        [APPLY_JUMP_2("test_a_listed_host_is_called_through_both_hops_and_every_other_host_through_the_first_alone")],
    ),
    Case(
        "second hop: the apply accepts a host with no key",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ -n "$JUMP2_KEY" ] || fail "ssh_jump2_host is set but ssh_jump2_key is empty: a second hop needs both or neither."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_JUMP_2("test_each_refusal_of_the_second_hop_comes_before_any_file_or_host_and_echoes_no_value")],
    ),
    Case(
        "second hop: the apply accepts a second hop without the first hop",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    { [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "A second hop needs ssh_jump_host and ssh_jump_key: it is reached through the first hop."
                    """,
                    12,
                ),
                "",
            ),
        ],
        [APPLY_JUMP_2("test_each_refusal_of_the_second_hop_comes_before_any_file_or_host_and_echoes_no_value")],
    ),
    Case(
        "second hop: the apply accepts a self-jump",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    [ "$(printf '%s' "${dest#*@}" | tr '[:upper:]' '[:lower:]')" != "$jump2_gateway" ] \
                      || fail "A line of ssh_jump2_hosts is the address of the second hop itself: that host is reached through the first hop alone."
                    """,
                    14,
                ),
                "",
            ),
        ],
        [APPLY_JUMP_2("test_each_refusal_of_the_second_hop_comes_before_any_file_or_host_and_echoes_no_value")],
    ),
    Case(
        "second hop: the apply writes the second key with the runner umask",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r'''
                    umask 077
                    printf '%s\n' "$JUMP2_KEY"''',
                    16,
                ),
                indented("printf '%s\\n' \"$JUMP2_KEY\"", 16),
            ),
        ],
        [APPLY_JUMP_2("test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out")],
    ),
    Case(
        "second hop: the apply trap leaves the second key behind",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    trap 'rm -f "$work/id" "$work/known_hosts" "$work"/id.* "$work/host.err" "$work/keylines" "$work/jump_id" "$work/jump_config" "$work/jump2_id"' EXIT
                    """,
                    14,
                ),
                "",
            ),
        ],
        [APPLY_JUMP_2("test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out")],
    ),
    Case(
        "second hop: the apply second stanza drops IdentitiesOnly",
        [
            Edit(
                GATED_APPLY,
                '"$work/jump2_id"\n'
                + indented(
                    r"""
                    printf '  IdentitiesOnly yes\n'
                    """,
                    18,
                ),
                '"$work/jump2_id"\n',
            ),
        ],
        [APPLY_JUMP_2("test_the_config_file_is_the_first_stanza_then_one_stanza_for_the_second_hop")],
    ),
    Case(
        "second hop: the apply second stanza uses the first hop's key file",
        [
            Edit(
                GATED_APPLY,
                "printf '  IdentityFile %s\\n' \"$work/jump2_id\"",
                "printf '  IdentityFile %s\\n' \"$work/jump_id\"",
            ),
        ],
        [APPLY_JUMP_2("test_each_key_is_offered_to_its_own_hop_only_and_none_reaches_an_argv")],
    ),
]
