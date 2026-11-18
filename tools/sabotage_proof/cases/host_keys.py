"""Per-host keys: the same break is made in the action (the plan side) and in the apply step, each with its own red test.

A table of near-identical cases is built here rather than written out twice. Each edit
is a fragment found exactly once in its file, so a change to the text makes the case fail
to apply, not pass.
"""

from __future__ import annotations

from ..files import ACTION, DRIFT_CHECK, GATED_APPLY
from ..model import Case, Edit
from ..suites import (
    APPLY_HOST_KEY,
    HOST_KEY_SECRET_SCOPE,
    HOST_KEY_VALIDATE,
    HOST_KEYS_PARITY,
    JUMP_PARITY,
    PER_HOST_KEY,
    STRUCTURE,
)
from ..text import indented


def host_key_cases() -> list[Case]:
    cases: list[Case] = []

    def both(name: str, action: Edit | None, apply: Edit | None, plan_red: list[str], apply_red: list[str]) -> None:
        if action is not None:
            cases.append(Case(f"host keys, plan side: {name}", [action], plan_red))
        if apply is not None:
            cases.append(Case(f"host keys, apply side: {name}", [apply], apply_red))

    one_key_each = PER_HOST_KEY("test_a_host_with_an_entry_gets_exactly_its_own_key_and_the_other_host_the_shared_key")
    wrong_key_fails_one = PER_HOST_KEY("test_a_wrong_key_for_demo1_fails_the_plan_for_demo1_alone")
    apply_one_key_each = APPLY_HOST_KEY(
        "test_a_host_with_an_entry_gets_its_own_key_on_both_runs_and_the_other_host_the_shared_key"
    )

    # The selection of a host's key.
    both(
        "the selection ignores the map, so every host gets the shared key",
        Edit(ACTION, 'if [ -e "$plan_dir/id.$key_name" ]; then', "if false; then"),
        Edit(GATED_APPLY, 'if [ -e "$work/id.$(printf \'%02d\' "$num")" ]; then', "if false; then"),
        [one_key_each, wrong_key_fails_one],
        [apply_one_key_each],
    )
    both(
        "the file of a host is numbered one too high (an off-by-one on the host index)",
        Edit(
            ACTION,
            'host_key_file="$plan_dir/id.$(printf \'%02d\' "$host_key_index")"',
            'host_key_file="$plan_dir/id.$(printf \'%02d\' "$((host_key_index + 1))")"',
        ),
        Edit(
            GATED_APPLY,
            'host_key_file="$work/id.$(printf \'%02d\' "$host_key_index")"',
            'host_key_file="$work/id.$(printf \'%02d\' "$((host_key_index + 1))")"',
        ),
        [one_key_each, wrong_key_fails_one],
        [apply_one_key_each],
    )
    has_entry = "'($ENV.HOST_KEYS | fromjson) | has($dest)'"
    has_entry_always = "'($ENV.HOST_KEYS | fromjson) | true'"
    both(
        "a host with no entry no longer falls back to the shared key",
        Edit(ACTION, has_entry, has_entry_always),
        Edit(GATED_APPLY, has_entry, has_entry_always),
        [one_key_each, wrong_key_fails_one],
        [apply_one_key_each],
    )
    both(
        "the call adds a second -i beside the shared one instead of replacing its value",
        Edit(ACTION, 'args[$((index + 1))]="$identity"', 'args+=(-i "$identity")'),
        Edit(GATED_APPLY, 'args[$((index + 1))]="$identity"', 'args+=(-i "$identity")'),
        [PER_HOST_KEY("test_every_call_has_one_identity_option_and_otherwise_the_unchanged_argv")],
        [apply_one_key_each],
    )

    # A host with no key is refused in the validate block, before any file is written.
    no_key = (
        'elif ($ENV.DRY_RUN != "true") and ($shared | length) == 0'
        ' and any($hosts[]; . as $h | ($map | has($h)) | not) then "no-key"\n'
    )
    both(
        "a host with no entry and no shared key is accepted (the validate block no longer refuses it)",
        Edit(ACTION, no_key, ""),
        Edit(GATED_APPLY, no_key, ""),
        [
            PER_HOST_KEY(
                "test_a_host_with_no_entry_and_no_shared_key_fails_in_the_validate_step_before_any_file_or_host"
            ),
            HOST_KEY_VALIDATE("test_each_refusal_fails_the_step_with_its_own_message_and_echoes_no_value"),
        ],
        [
            APPLY_HOST_KEY("test_a_host_with_no_entry_and_no_shared_key_fails_before_any_host_is_asked"),
            APPLY_HOST_KEY("test_each_refusal_comes_before_any_file_or_host_and_echoes_no_value"),
        ],
    )

    # The compare drops CR and the whitespace round a key, so CRLF or a leading blank cannot dodge it.
    full_trim = r'def trim: gsub("\r"; "") | sub("\\A\\s+"; "") | sub("\\s+\\z"; "");'
    old_trim = r'def trim: sub("\\s+\\z"; "");'
    refusal_before_anything = HOST_KEY_VALIDATE(
        "test_each_refusal_fails_the_step_with_its_own_message_and_echoes_no_value"
    )
    apply_refusal_before_anything = APPLY_HOST_KEY(
        "test_each_refusal_comes_before_any_file_or_host_and_echoes_no_value"
    )
    both(
        "keys are compared without dropping CR or leading whitespace",
        Edit(ACTION, full_trim, old_trim),
        Edit(GATED_APPLY, full_trim, old_trim),
        [refusal_before_anything],
        [apply_refusal_before_anything],
    )
    full_write = r'[$dest] | gsub("\r"; "") | sub("\\A\\s+"; "") | sub("\\s+\\z"; "")'
    old_write = r'[$dest] | sub("\\s+\\z"; "")'
    both(
        "a key is written with its CR and leading whitespace",
        Edit(ACTION, full_write, old_write),
        Edit(GATED_APPLY, full_write, old_write),
        [PER_HOST_KEY("test_a_key_with_crlf_line_ends_and_leading_whitespace_is_written_as_plain_lines")],
        [APPLY_HOST_KEY("test_a_key_with_crlf_line_ends_and_leading_whitespace_is_written_as_plain_lines")],
    )

    # What is refused before any file is written or host asked.
    refusals = (
        (
            "the check that a key's host is in the hosts list is removed",
            'elif any($map | keys[]; . as $k | all($hosts[]; . != $k)) then "unknown-host"\n',
        ),
        (
            "the check that no two hosts share a key is removed",
            'elif ([$map[] | trim] | length) != ([$map[] | trim] | unique | length) then "duplicate"\n',
        ),
        (
            "the check that no host key is the shared key is removed",
            'elif any($map[]; . as $v | any($shared[]; . == ($v | trim))) then "shared"\n',
        ),
        (
            "the check that no host key is a jump key is removed",
            'elif any($map[]; . as $v | any($jump[]; . == ($v | trim))) then "jump"\n',
        ),
        (
            "the check that a key is a non-empty string is removed",
            'elif any($map[]; (type != "string") or (trim == "")) then "bad-value"\n',
        ),
    )
    for name, line in refusals:
        both(
            name,
            Edit(ACTION, line, ""),
            Edit(GATED_APPLY, line, ""),
            [refusal_before_anything],
            [apply_refusal_before_anything],
        )
    untrimmed = "[$map[] | trim] | length) != ([$map[] | trim] | unique"
    trimmed = "[$map[]] | length) != ([$map[]] | unique"
    both(
        "keys are compared without trimming the newline a key file ends with",
        Edit(ACTION, untrimmed, trimmed),
        Edit(GATED_APPLY, untrimmed, trimmed),
        [refusal_before_anything],
        [apply_refusal_before_anything],
    )

    # Removal, privacy, masking and silence.
    mask = 'echo "::add-mask::$host_key_line"'
    never_printed_each_line_masked = "test_a_key_is_never_printed_but_each_of_its_lines_is_masked"
    both(
        "the lines of a key are not masked",
        Edit(ACTION, mask, ":"),
        Edit(GATED_APPLY, mask, ":"),
        [PER_HOST_KEY(never_printed_each_line_masked)],
        [APPLY_HOST_KEY(never_printed_each_line_masked)],
    )
    key_written = "printf '\\n' >> \"$host_key_file\"\n"
    key_printed = key_written + indented('cat "$host_key_file"\n', 20)
    both(
        "the step prints the key it has written",
        Edit(ACTION, key_written, key_printed),
        Edit(GATED_APPLY, key_written, key_printed),
        [PER_HOST_KEY(never_printed_each_line_masked)],
        [APPLY_HOST_KEY(never_printed_each_line_masked)],
    )
    private_write = "umask 077\n" + indented("jq -n -j", 20)
    private_while_ssh_runs = "test_the_key_files_are_private_while_ssh_runs_and_removed_on_every_way_out"
    both(
        "a host key file is written with the runner umask",
        Edit(ACTION, private_write, "jq -n -j"),
        Edit(GATED_APPLY, private_write, "jq -n -j"),
        [PER_HOST_KEY(private_while_ssh_runs)],
        [APPLY_HOST_KEY(private_while_ssh_runs)],
    )
    trailing_newline_trimmed = '| sub("\\\\s+\\\\z"; "")\' > "$host_key_file"'
    trailing_newline_kept = '\' > "$host_key_file"'
    exactly_one_newline = "test_a_key_pasted_with_trailing_newlines_is_written_with_exactly_one"
    both(
        "the newline a key file ends with is written through",
        Edit(ACTION, trailing_newline_trimmed, trailing_newline_kept),
        Edit(GATED_APPLY, trailing_newline_trimmed, trailing_newline_kept),
        [PER_HOST_KEY(exactly_one_newline)],
        [APPLY_HOST_KEY(exactly_one_newline)],
    )

    # The traps that remove the shared key remove the host key files too: each is taken out of one trap in turn.
    plan_trap = 'trap \'rm -f "$plan_dir/id" "$plan_dir/known_hosts" "$plan_dir"/id.*'
    plan_trap_without = 'trap \'rm -f "$plan_dir/id" "$plan_dir/known_hosts"'
    apply_trap = 'trap \'rm -f "$work/id" "$work/known_hosts" "$work"/id.*'
    apply_trap_without = 'trap \'rm -f "$work/id" "$work/known_hosts"'
    removed_after_hops = "test_the_key_files_are_removed_after_a_jump_and_after_a_second_hop"
    traps = (
        (
            "the first trap leaves the host key files behind",
            indented(plan_trap + "' EXIT\n", 8),
            indented(plan_trap_without + "' EXIT\n", 8),
            ACTION,
            PER_HOST_KEY,
            private_while_ssh_runs,
        ),
        (
            "the jump trap leaves the host key files behind",
            plan_trap + ' "$plan_dir/jump_id" "$plan_dir/jump_config"\' EXIT\n',
            plan_trap_without + ' "$plan_dir/jump_id" "$plan_dir/jump_config"\' EXIT\n',
            ACTION,
            PER_HOST_KEY,
            removed_after_hops,
        ),
        (
            "the second hop trap leaves the host key files behind",
            plan_trap + ' "$plan_dir/jump_id" "$plan_dir/jump_config" "$plan_dir/jump2_id"\' EXIT\n',
            plan_trap_without + ' "$plan_dir/jump_id" "$plan_dir/jump_config" "$plan_dir/jump2_id"\' EXIT\n',
            ACTION,
            PER_HOST_KEY,
            removed_after_hops,
        ),
        (
            "the first trap leaves the host key files behind",
            indented(apply_trap + "' EXIT\n", 10),
            indented(apply_trap_without + "' EXIT\n", 10),
            GATED_APPLY,
            APPLY_HOST_KEY,
            private_while_ssh_runs,
        ),
        (
            "the jump trap leaves the host key files behind",
            apply_trap + ' "$work/jump_id" "$work/jump_config"\' EXIT\n',
            apply_trap_without + ' "$work/jump_id" "$work/jump_config"\' EXIT\n',
            GATED_APPLY,
            APPLY_HOST_KEY,
            removed_after_hops,
        ),
        (
            "the second hop trap leaves the host key files behind",
            apply_trap + ' "$work/jump_id" "$work/jump_config" "$work/jump2_id"\' EXIT\n',
            apply_trap_without + ' "$work/jump_id" "$work/jump_config" "$work/jump2_id"\' EXIT\n',
            GATED_APPLY,
            APPLY_HOST_KEY,
            removed_after_hops,
        ),
    )
    for name, old, new, path, suite, test in traps:
        side = "plan side" if path == ACTION else "apply side"
        cases.append(
            Case(
                f"host keys, {side}: {name}",
                [Edit(path, old, new)],
                [
                    suite(test),
                    HOST_KEYS_PARITY("test_every_trap_that_removes_the_shared_key_removes_the_per_host_key_files_too"),
                ],
            )
        )

    # The scan of a diff, the secrets each job reads, and the places the map is passed.
    scan = indented(
        r"""
        # The key of each host that has one of its own is scanned for the same way.
        if [ -n "$HOST_KEYS" ]; then
          jq -n -r '($ENV.HOST_KEYS | fromjson) | .[] | gsub("\r"; "") | split("\n")[]' | awk 'length($0) >= 16 && $0 !~ /^-----/' >> "$keylines"
        fi
        """,
        8,
    )
    cases.append(
        Case(
            "host keys, plan side: the report step leaves the host keys out of the scan of the diff",
            [Edit(ACTION, scan, "")],
            [PER_HOST_KEY("test_a_diff_that_quotes_a_host_key_is_withheld_and_never_shown")],
        )
    )
    only_the_actions_step = (
        "test_the_host_apply_keys_are_read_by_the_apply_step_only_and_never_by_the_plan_or_the_drift_check"
    )
    check_keys_only = "test_the_host_check_keys_are_read_by_the_action_step_of_the_plan_and_of_the_drift_check_only"
    check_keys = "host-check-keys: ${{ secrets.ssh_host_check_keys }}"
    cases.append(
        Case(
            "host keys, plan side: the plan job also reads ssh_host_apply_keys",
            [
                Edit(
                    GATED_APPLY,
                    check_keys,
                    "host-check-keys: ${{ secrets.ssh_host_check_keys || secrets.ssh_host_apply_keys }}",
                )
            ],
            [
                HOST_KEY_SECRET_SCOPE(only_the_actions_step),
                STRUCTURE("test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"),
            ],
        )
    )
    cases.append(
        Case(
            "host keys, drift check: the drift check also reads ssh_host_apply_keys",
            [
                Edit(
                    DRIFT_CHECK,
                    check_keys,
                    "host-check-keys: ${{ secrets.ssh_host_check_keys || secrets.ssh_host_apply_keys }}",
                )
            ],
            [HOST_KEY_SECRET_SCOPE(only_the_actions_step)],
        )
    )
    cases.append(
        Case(
            "host keys, apply side: the apply step reads the check keys",
            [
                Edit(
                    GATED_APPLY,
                    "HOST_KEYS: ${{ secrets.ssh_host_apply_keys }}",
                    "HOST_KEYS: ${{ secrets.ssh_host_check_keys }}",
                )
            ],
            [
                HOST_KEY_SECRET_SCOPE(check_keys_only),
                STRUCTURE("test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source"),
            ],
        )
    )
    cases.append(
        Case(
            "host keys, plan side: the plan job does not pass the host check keys",
            [Edit(GATED_APPLY, indented(check_keys + "\n", 10), "")],
            [
                HOST_KEY_SECRET_SCOPE(check_keys_only),
                STRUCTURE("test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"),
            ],
        )
    )
    cases.append(
        Case(
            "host keys, drift check: the drift check does not pass the host check keys, so a host with its own key fails the nightly check",
            [Edit(DRIFT_CHECK, indented(check_keys + "\n", 10), "")],
            [
                HOST_KEY_SECRET_SCOPE(check_keys_only),
                STRUCTURE("test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"),
            ],
        )
    )

    # The two copies of the text drift apart, and a key is handed to a command.
    cases.append(
        Case(
            "host keys: the validation block of the action drifts from the apply step's",
            [
                Edit(
                    ACTION,
                    'duplicate) fail "Two hosts have the same key: each host has a key of its own."',
                    'duplicate) fail "Two hosts have the same key."',
                )
            ],
            [HOST_KEYS_PARITY("test_the_validation_block_is_the_same_text_in_the_action_and_in_the_apply_step")],
        )
    )
    cases.append(
        Case(
            "host keys: the write block of the apply step drifts from the action's",
            [
                Edit(
                    GATED_APPLY,
                    "awk 'length($0) >= 16 && $0 !~ /^-----/' \"$host_key_file\"",
                    "awk 'length($0) >= 20 && $0 !~ /^-----/' \"$host_key_file\"",
                )
            ],
            [
                HOST_KEYS_PARITY(
                    "test_the_write_block_is_the_same_text_in_the_action_and_in_the_apply_step_the_directory_aside"
                )
            ],
        )
    )
    cases.append(
        Case(
            "host keys: a key is handed to jq on its argument list",
            [
                Edit(
                    ACTION,
                    'jq -n -e --arg dest "$host_key_dest" \'($ENV.HOST_KEYS',
                    'jq -n -e --arg dest "$host_key_dest" --arg keys "$HOST_KEYS" \'($ENV.HOST_KEYS',
                )
            ],
            [HOST_KEYS_PARITY("test_a_key_is_read_from_the_environment_inside_jq_and_never_given_to_a_command")],
        )
    )
    cases.append(
        Case(
            "host keys: a call of the one-call function leaves out the identity file",
            [
                Edit(
                    ACTION,
                    'ssh_via "$chain" "${limit}s" "$identity" -- "$dest" "$SHA" < /dev/null',
                    'ssh_via "$chain" "${limit}s" "$workaround" -- "$dest" "$SHA" < /dev/null',
                )
            ],
            [JUMP_PARITY("test_every_call_of_that_function_names_the_identity_file_of_its_host")],
        )
    )
    return cases


CASES: list[Case] = host_key_cases()
