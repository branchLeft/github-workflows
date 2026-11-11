"""Sabotage proof for tools/test_ansible_gated_step.py: break each control, require its test red.

Each case edits a copy of the repository so that one control no longer holds and
runs the named tests there; every named test must go red. Before any case, the
unedited tree must pass the whole suite. The controls are listed in
.github/workflows/ansible-gated-step.md. Run one case with a part of its name as
an argument, or list them with --list.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = "tools/test_ansible_gated_step.py"
STEP = ".github/workflows/ansible-gated-step.yml"
APPLY = ".github/workflows/ansible-gated-apply.yml"


@dataclass
class Edit:
    path: str
    old: str
    new: str
    count: int = 1
    # Replace only this occurrence (1-based) of the text, and leave the others.
    nth: int | None = None


@dataclass
class Case:
    name: str
    edits: list[Edit]
    # Tests, as Class.method, that must go red. At least one.
    red: list[str] = field(default_factory=list)


STRUCT = "StructureTests"
RG = "RequestGuardTests"
SG = "SendGuardTests"
CO = "CheckoutTests"
INV = "InventoryTests"
PL = "PlanTests"
SE = "SendTests"
CH = "ChecksTests"
UN = "ApplyWorkflowUntouchedTests"

KEY_TEST = f"{SE}.test_the_key_is_private_on_disk_never_an_argument_and_never_echoed"
HALF_HOP = f"{SE}.test_a_half_configured_or_reused_hop_is_refused_before_any_call"


def step(
    old: str, new: str, red: list[str], name: str, count: int = 1, nth: int | None = None
) -> Case:
    return Case(name, [Edit(STEP, old, new, count, nth)], red)


def both_sites(check: str, label: str, guard_red: list[str], send_red: list[str]) -> list[Case]:
    """A check that the guard job and the send job both make: break each site on its own."""
    return [
        step(check, "true ||", guard_red, f"{label}, in the guard job", count=2, nth=1),
        step(check, "true ||", send_red, f"{label}, in the send job", count=2, nth=2),
    ]


def build_cases() -> list[Case]:
    cases: list[Case] = [
        step(
            "    environment: ${{ inputs.environment }}\n",
            "",
            [f"{STRUCT}.test_send_job_runs_in_the_environment_input"],
            "W1 the send job has no environment",
        ),
        step(
            "default: control-plane-step",
            "default: production",
            [f"{STRUCT}.test_the_environment_defaults_to_control_plane_step"],
            "W1 the default environment is another one",
        ),
        step(
            "    name: Guard the step request\n",
            "    name: Guard the step request\n    environment: ${{ inputs.environment }}\n",
            [f"{STRUCT}.test_guard_job_holds_no_environment_and_no_secret"],
            "the guard job gets an environment",
        ),
        step(
            "          ENVIRONMENT: ${{ inputs.environment }}\n",
            "          ENVIRONMENT: ${{ inputs.environment }}\n          LEAK: ${{ secrets.ssh_step_key }}\n",
            [
                f"{STRUCT}.test_guard_job_holds_no_environment_and_no_secret",
                f"{STRUCT}.test_no_job_but_send_reads_a_secret",
            ],
            "the guard job reads a secret",
        ),
        step(
            "cancel-in-progress: false",
            "cancel-in-progress: true",
            [f"{STRUCT}.test_send_waits_for_the_guard_and_is_never_cancelled"],
            "the send job can be cancelled",
        ),
        step(
            'echo "The owner dispatched step $STEP at commit $SHA."',
            'echo "The owner dispatched step ${{ inputs.step }} at commit $SHA."',
            [f"{STRUCT}.test_no_run_body_holds_an_expression"],
            "a run body holds an expression",
        ),
        step(
            "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1",
            "actions/checkout@v7",
            [f"{STRUCT}.test_every_action_is_pinned_to_a_commit_with_a_version_comment"],
            "an action is not pinned to a commit",
        ),
        step(
            "on:\n  workflow_call:",
            "on:\n  push:\n  workflow_call:",
            [f"{STRUCT}.test_the_workflow_is_called_never_started_by_an_event_of_its_own"],
            "the workflow starts on a push of its own",
        ),
        # W2 only a dispatch sends.
        *both_sites(
            '[ "$EVENT_NAME" = workflow_dispatch ] ||',
            "W2 a push event is allowed",
            [f"{RG}.test_only_a_dispatch_passes"],
            [f"{SG}.test_only_a_dispatch_on_the_default_branch_of_a_repository_passes"],
        ),
        step(
            'expect send "$SEND" skipped',
            'expect send "$SEND" success',
            [f"{CH}.test_any_other_event_must_never_send"],
            "W2 the checks job lets another event send",
        ),
        step(
            'expect guard "$GUARD" success',
            'expect guard "$GUARD" "$GUARD"',
            [f"{CH}.test_a_dispatch_must_pass_the_guard_and_the_send"],
            "the checks job ignores the guard",
        ),
        step(
            'expect send "$SEND" success\n          else',
            'expect send "$SEND" "$SEND"\n          else',
            [f"{CH}.test_a_dispatch_must_pass_the_guard_and_the_send"],
            "the checks job ignores the send",
        ),
        # W3 only the default branch.
        *both_sites(
            '[ "$REF" = "refs/heads/$DEFAULT_BRANCH" ] ||',
            "W3 the ref test is dropped",
            [f"{RG}.test_only_the_default_branch_passes"],
            [f"{SG}.test_only_a_dispatch_on_the_default_branch_of_a_repository_passes"],
        ),
        *both_sites(
            '[ "$IS_FORK" != true ] ||',
            "a fork is allowed",
            [f"{RG}.test_a_fork_is_refused"],
            [f"{SG}.test_only_a_dispatch_on_the_default_branch_of_a_repository_passes"],
        ),
        *both_sites(
            '[[ "$SHA" =~ $sha_re ]] ||',
            "the commit shape test is dropped",
            [f"{RG}.test_a_commit_that_is_not_40_lowercase_hex_digits_is_refused"],
            [f"{SG}.test_a_commit_or_step_the_guard_did_not_judge_is_refused"],
        ),
        # W4 the step and the word are shaped before any secret is read.
        *both_sites(
            '[[ "$STEP" =~ $step_re ]] ||',
            "W4 the step pattern is dropped",
            [f"{RG}.test_a_malformed_step_is_refused"],
            [f"{SG}.test_a_malformed_step_or_word_is_refused"],
        ),
        *both_sites(
            '[[ "$CONFIRM" =~ $confirm_re ]] ||',
            "W4 the word pattern is dropped",
            [f"{RG}.test_a_malformed_word_is_refused"],
            [f"{SG}.test_a_malformed_step_or_word_is_refused"],
        ),
        step(
            '[[ "$ENVIRONMENT" =~ $environment_re ]] ||',
            "true ||",
            [f"{RG}.test_a_malformed_environment_is_refused"],
            "W4 the environment pattern is dropped",
        ),
        step(
            '[[ "$INVENTORY" =~ $path_re ]] ||',
            "true ||",
            [f"{RG}.test_an_inventory_path_that_is_not_a_relative_path_is_refused"],
            "the inventory path pattern is dropped",
        ),
        step(
            'case "$INVENTORY" in *..*) fail "step-inventory must not contain \'..\'." ;; esac',
            ":",
            [f"{RG}.test_an_inventory_path_that_is_not_a_relative_path_is_refused"],
            "the inventory path may hold dot-dot",
        ),
        step(
            '[[ "$line" =~ $line_re ]] ||',
            "true ||",
            [f"{RG}.test_a_step_inputs_line_that_is_not_an_id_and_a_relative_path_is_refused"],
            "the step-inputs line pattern is dropped",
        ),
        step(
            'case "$line" in *..*) fail "A path in step-inputs must not contain \'..\'." ;; esac',
            ":",
            [f"{RG}.test_a_step_inputs_line_that_is_not_an_id_and_a_relative_path_is_refused"],
            "a step-inputs path may hold dot-dot",
        ),
        # W5 the dispatcher is the owner.
        *both_sites(
            '[ "$actor" = "$owner" ] ||',
            "W5 the actor test is dropped",
            [f"{RG}.test_another_actor_is_refused"],
            [f"{SG}.test_another_actor_or_a_rerun_by_another_actor_is_refused"],
        ),
        *both_sites(
            '[ "$triggering" = "$owner" ] ||',
            "W5 the rerun actor test is dropped",
            [f"{RG}.test_a_rerun_by_another_actor_is_refused"],
            [f"{SG}.test_another_actor_or_a_rerun_by_another_actor_is_refused"],
        ),
        *both_sites(
            '[[ "$OWNER" =~ $owner_re ]] ||',
            "the owner-login pattern is dropped",
            [f"{RG}.test_an_owner_input_that_is_not_an_account_name_is_refused"],
            [f"{SG}.test_another_actor_or_a_rerun_by_another_actor_is_refused"],
        ),
        step(
            "owner=$(printf '%s' \"$OWNER\" | tr '[:upper:]' '[:lower:]')",
            "owner=$OWNER",
            [f"{RG}.test_the_owner_account_is_compared_without_case"],
            "the owner account is compared with case",
            count=2,
        ),
        step(
            '[ "$GUARDED_SHA" = "$SHA" ] ||',
            "true ||",
            [f"{SG}.test_a_commit_or_step_the_guard_did_not_judge_is_refused"],
            "the send job accepts a commit the guard did not judge",
        ),
        step(
            '[ "$GUARDED_STEP" = "$STEP" ] ||',
            "true ||",
            [f"{SG}.test_a_commit_or_step_the_guard_did_not_judge_is_refused"],
            "the send job accepts a step the guard did not judge",
        ),
        step(
            '[ "$head" = "$SHA" ] || {',
            "true || {",
            [f"{CO}.test_a_checkout_at_another_commit_is_refused"],
            "the checkout may be another commit",
        ),
        # The step is on the host's allow-list and the word is its word.
        step(
            "[ \"$(printf '%s' \"$entries\" | jq 'length')\" = 1 ] ||",
            "true ||",
            [
                f"{INV}.test_a_step_that_is_not_on_the_allow_list_is_refused",
                f"{INV}.test_a_step_listed_twice_is_refused",
            ],
            "a step that is not on the allow-list, or is listed twice, passes",
        ),
        step(
            '[ "$word" = "$CONFIRM" ] ||',
            "true ||",
            [
                f"{INV}.test_a_wrong_word_is_refused_and_never_printed",
                f"{INV}.test_the_word_of_another_step_is_refused",
            ],
            "a wrong word passes",
        ),
        step(
            '*) fail "step-inventory leaves the checkout." ;;\n',
            "*) ;;\n",
            [f"{INV}.test_an_inventory_that_is_a_link_or_leaves_the_checkout_is_refused"],
            "the inventory may leave the checkout",
        ),
        # The plan the approver reads.
        step(
            '[ "$named" -le 1 ] ||',
            "true ||",
            [f"{PL}.test_a_step_named_twice_is_refused"],
            "a step named twice in step-inputs passes",
        ),
        step(
            '*) fail "The input file named for the step leaves the checkout." ;;\n',
            "*) ;;\n",
            [f"{PL}.test_an_input_file_that_leaves_the_checkout_is_refused"],
            "the input file may leave the checkout",
        ),
        step(
            'shasum -a 256 "$file"',
            'shasum -a 1 "$file"',
            [f"{PL}.test_the_plan_is_three_lines_in_the_log_and_the_summary"],
            "the plan hashes with another algorithm",
        ),
        step(
            '            echo "step $STEP"\n',
            "",
            [f"{PL}.test_the_plan_is_three_lines_in_the_log_and_the_summary"],
            "the plan omits the step",
        ),
        # W6 the answer is a mode=step line for this step.
        step(
            'if [ "$f_step" != "$STEP" ]; then',
            "if false; then",
            [
                f"{SE}.test_an_answer_that_names_another_step_is_refused",
                f"{SE}.test_a_second_answer_for_another_step_is_refused",
            ],
            "W6 an answer for another step is accepted",
        ),
        step(
            "mode=step commit=",
            "mode=[a-z]+ commit=",
            [f"{SE}.test_an_answer_for_apply_mode_is_not_read_as_a_step"],
            "W6 an answer for another mode is accepted",
        ),
        Case(
            "W6 an answer with no step field is accepted",
            [
                Edit(
                    STEP, " step=([a-z][a-z0-9_]{0,31}|-)$'", "( step=)?([a-z][a-z0-9_]{0,31}|-)?$'"
                ),
                Edit(STEP, 'f_step="${BASH_REMATCH[6]}"', 'f_step="$STEP"'),
            ],
            [f"{SE}.test_an_answer_with_no_step_field_is_refused"],
        ),
        step(
            'if [ "$f_commit" != "$SHA" ] || [ "$f_exit" != "$rc" ]; then',
            "if false; then",
            [f"{SE}.test_an_answer_for_another_commit_or_exit_status_is_refused"],
            "an answer for another commit or exit status is accepted",
        ),
        step(
            'if [ "$rc" = 255 ]; then',
            "if false; then",
            [f"{SE}.test_a_failed_connection_stops_the_run"],
            "a failed connection is not named",
        ),
        step(
            "[ \"$(printf '%s\\n' \"$out\" | wc -l | tr -d ' ')\" = 1 ] && ",
            "",
            [f"{SE}.test_an_answer_that_is_not_exactly_one_line_is_refused"],
            "an answer of several lines is accepted",
        ),
        # W7 one host.
        step(
            '[ "$count" -eq 1 ] ||',
            '[ "$count" -ge 1 ] ||',
            [f"{SE}.test_more_than_one_host_is_refused_and_none_is_called"],
            "W7 a second host is sent the request",
        ),
        step(
            '[[ "$dest" =~ $destination_pattern ]] ||',
            "true ||",
            [f"{SE}.test_a_host_that_is_not_user_at_host_is_refused"],
            "a host that is not user@host is called",
        ),
        step(
            '[ -n "$SSH_KEY" ] || fail',
            "true || fail",
            [f"{SE}.test_an_empty_key_or_known_hosts_or_host_is_refused_before_any_call"],
            "an empty key is sent with",
        ),
        step(
            '[ -n "$KNOWN_HOSTS" ] || fail',
            "true || fail",
            [f"{SE}.test_an_empty_key_or_known_hosts_or_host_is_refused_before_any_call"],
            "empty known hosts are sent with",
        ),
        step(
            '[[ "$HOST_TIMEOUT_MINUTES" =~ $timeout_re ]] ||',
            "true ||",
            [f"{SE}.test_a_timeout_that_is_not_a_whole_number_of_minutes_is_refused"],
            "the timeout is not shaped",
        ),
        step(
            'out=$(timeout "${HOST_TIMEOUT_MINUTES}m" ssh',
            "out=$(ssh",
            [f"{SE}.test_each_call_is_limited_by_the_timeout_input"],
            "the call has no timeout",
        ),
        # W8 the key is removed on every exit.
        step(
            "trap 'rm -rf \"$work\"' EXIT",
            "true",
            [
                f"{SE}.test_the_key_is_removed_when_the_send_fails",
                f"{SE}.test_the_key_is_removed_when_the_send_succeeds",
            ],
            "W8 the key is not removed on exit",
        ),
        # W9 the second run changes nothing.
        step(
            'ask "The second run, which must change nothing" "0:unchanged"',
            'ask "The second run, which must change nothing" "0:unchanged" "10:applied"',
            [f"{SE}.test_a_second_run_that_changes_something_is_refused"],
            "W9 a change on the second answer is accepted",
        ),
        step(
            ' || { [ "$f_verdict" = unchanged ] && [ "$f_changed" != 0 ]; }',
            "",
            [
                f"{SE}.test_an_unchanged_answer_that_counts_a_change_is_refused",
                f"{SE}.test_a_second_run_that_changes_something_is_refused",
            ],
            "W9 an unchanged answer may count a change",
        ),
        step(
            '[ "$ok" -ne 0 ] || [ "$f_changed" = - ] ||',
            '[ "$ok" -ne 0 ] ||',
            [f"{SE}.test_an_applied_answer_with_no_count_is_refused"],
            "an applied answer with no count is accepted",
        ),
        step(
            '          ask "The second run, which must change nothing" "0:unchanged"\n',
            "",
            [f"{SE}.test_a_step_that_applies_and_then_changes_nothing_succeeds"],
            "W9 the second run is not made",
        ),
        Case(
            "a refusal of the host is accepted",
            [
                Edit(
                    STEP,
                    'ask "The step" "10:applied" "0:unchanged"',
                    'ask "The step" "10:applied" "0:unchanged" "20:refused"',
                ),
                Edit(STEP, '[ "$ok" -ne 0 ] || [ "$f_changed" = - ] ||', '[ "$ok" -ne 0 ] ||'),
            ],
            [f"{SE}.test_a_refusal_or_a_failure_of_the_host_stops_the_run_with_its_reason"],
        ),
        # The key is never echoed, and the connection is strict.
        step(
            "          destination_pattern=",
            "          set -x\n          destination_pattern=",
            [KEY_TEST],
            "the shell traces the key",
            count=1,
        ),
        step(
            'echo "::add-mask::$masked"',
            'echo "$masked"',
            [KEY_TEST],
            "the key lines are printed, not masked",
        ),
        step(
            "(umask 077 && printf '%s\\n' \"$SSH_KEY\"",
            "(umask 022 && printf '%s\\n' \"$SSH_KEY\"",
            [KEY_TEST],
            "the key file is readable by others",
        ),
        step(
            "2> /dev/null) || rc=$?",
            "2>&1) || rc=$?",
            [f"{SE}.test_the_playbook_output_on_standard_error_is_not_in_the_log"],
            "the playbook output reaches the log",
        ),
        step(
            "-o StrictHostKeyChecking=yes",
            "-o StrictHostKeyChecking=no",
            [f"{SE}.test_the_connection_is_strict_and_forwards_nothing"],
            "the host key is not checked",
        ),
        step(
            "-o ClearAllForwardings=yes)",
            "-o ClearAllForwardings=no)",
            [f"{SE}.test_the_connection_is_strict_and_forwards_nothing"],
            "forwarding is not cleared",
        ),
        step(
            "-o IdentitiesOnly=yes",
            "-o IdentitiesOnly=no",
            [f"{SE}.test_the_connection_is_strict_and_forwards_nothing"],
            "other identities are offered",
        ),
        step(
            "printf '  StrictHostKeyChecking yes\\n'",
            "printf '  StrictHostKeyChecking no\\n'",
            [f"{SE}.test_one_jump_hop_has_a_stanza_and_a_key_of_its_own"],
            "a jump hop does not check the host key",
        ),
        step(
            "proxy=jump_hop,jump_hop2",
            "proxy=jump_hop2,jump_hop",
            [f"{SE}.test_two_jump_hops_chain_in_order"],
            "the hops are chained in the wrong order",
        ),
        step(
            '[ "$JUMP_KEY" != "$SSH_KEY" ] ||',
            "true ||",
            [HALF_HOP],
            "the first jump may use the step key",
        ),
        step(
            '[ "$JUMP2_KEY" != "$SSH_KEY" ] ||',
            "true ||",
            [HALF_HOP],
            "the second jump may use the step key",
        ),
        step(
            '[ "$JUMP2_KEY" != "$JUMP_KEY" ] ||',
            "true ||",
            [HALF_HOP],
            "the second jump may use the first jump's key",
        ),
        step(
            '[ -n "$JUMP_HOST" ] || fail "ssh_jump_key is set but ssh_jump_host is empty: a jump needs both or neither."',
            ":",
            [HALF_HOP],
            "a jump key with no jump host passes",
        ),
        step(
            '[ -n "$JUMP_KEY" ] || fail "ssh_jump_host is set but ssh_jump_key is empty: a jump needs both or neither."',
            ":",
            [HALF_HOP],
            "a jump host with no jump key passes",
        ),
        step(
            '{ [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail',
            "true || fail",
            [HALF_HOP],
            "a second hop needs no first hop",
        ),
        step(
            '[ -n "$JUMP2_HOST" ] || fail "ssh_jump2_key is set but ssh_jump2_host is empty: a second hop needs both or neither."',
            ":",
            [HALF_HOP],
            "a second jump key with no second jump host passes",
        ),
        step(
            '[ -n "$JUMP2_KEY" ] || fail "ssh_jump2_host is set but ssh_jump2_key is empty: a second hop needs both or neither."',
            ":",
            [HALF_HOP],
            "a second jump host with no second jump key passes",
        ),
        step(
            '[[ "$JUMP_HOST" =~ $destination_pattern ]] ||',
            "true ||",
            [HALF_HOP],
            "a jump host that is not user@host passes",
        ),
        step(
            '[[ "$JUMP2_HOST" =~ $destination_pattern ]] ||',
            "true ||",
            [HALF_HOP],
            "a second jump host that is not user@host passes",
        ),
        # W10 the apply workflow is untouched: break it and the pinning test goes red.
        Case(
            "W10 the apply answer pattern takes a step line",
            [
                Edit(
                    APPLY,
                    "mode=apply commit=([0-9a-f]{40})",
                    "mode=(apply|step) commit=([0-9a-f]{40})",
                )
            ],
            [f"{UN}.test_the_apply_answer_pattern_still_pins_apply_mode"],
        ),
        Case(
            "W10 the plan job runs for another event",
            [
                Edit(
                    APPLY,
                    '[ "$EVENT_NAME" = push ] || fail "The plan job runs only for a push',
                    '[ "$EVENT_NAME" != "" ] || fail "The plan job runs only for a push',
                )
            ],
            [f"{UN}.test_the_plan_and_apply_jobs_still_run_only_for_a_push"],
        ),
        Case(
            "W10 the apply job runs for another event",
            [
                Edit(
                    APPLY,
                    '[ "$EVENT_NAME" = push ] || fail "The apply job runs only for a push',
                    '[ "$EVENT_NAME" != "" ] || fail "The apply job runs only for a push',
                )
            ],
            [f"{UN}.test_the_plan_and_apply_jobs_still_run_only_for_a_push"],
        ),
        Case(
            "W10 the apply job no longer waits for a change",
            [
                Edit(
                    APPLY,
                    "needs.plan.outputs.has_changes != 'false'",
                    "needs.plan.outputs.has_changes != 'never'",
                )
            ],
            [f"{UN}.test_the_apply_still_waits_for_a_plan_that_found_a_change"],
        ),
        Case(
            "W10 the apply workflow learns a step",
            [
                Edit(
                    APPLY, "name: Ansible gated apply\n", "name: Ansible gated apply\n# mode=step\n"
                )
            ],
            [f"{UN}.test_the_apply_workflow_knows_nothing_of_a_step"],
        ),
    ]
    return cases


CASES = build_cases()


def apply_edits(root: Path, edits: list[Edit]) -> str | None:
    """Apply the edits under root. Returns a reason if one does not match, else None."""
    for edit in edits:
        target = root / edit.path
        text = target.read_text()
        found = text.count(edit.old)
        if found != edit.count:
            return f"{edit.path}: the text to break was found {found} time(s), expected {edit.count}: {edit.old[:70]!r}"
        if edit.new == edit.old:
            return f"{edit.path}: the edit changes nothing"
        if edit.nth is None:
            target.write_text(text.replace(edit.old, edit.new))
            continue
        position = -1
        for _ in range(edit.nth):
            position = text.index(edit.old, position + 1)
        target.write_text(text[:position] + edit.new + text[position + len(edit.old) :])
    return None


def run_tests(root: Path, names: list[str]) -> tuple[int, set[str], str]:
    """Run the test module in root (all of it, or only names). Returns code, red tests as Class.method, log."""
    proc = subprocess.run(
        [sys.executable, str(root / TESTS), *names],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    log = proc.stdout + proc.stderr
    red = {
        f"{cls}.{meth}"
        for meth, cls in re.findall(r"(?m)^(?:FAIL|ERROR): (\w+) \(__main__\.(\w+)\.\w+\)", log)
    }
    return proc.returncode, red, log


def copy_repo(destination: Path) -> None:
    shutil.copytree(
        REPO, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules")
    )


def run_case(case: Case) -> tuple[Case, bool, str]:
    with tempfile.TemporaryDirectory(prefix="sabotage-step-") as scratch:
        root = Path(scratch) / "repo"
        copy_repo(root)
        problem = apply_edits(root, case.edits)
        if problem:
            return case, False, f"CANNOT BREAK  {problem}"
        code, red, log = run_tests(root, case.red)
        missing = [name for name in case.red if name not in red]
        if code == 0 or missing:
            return case, False, f"NOT RED  expected red: {missing or case.red}\n{log[-1500:]}"
        return case, True, "RED  " + ", ".join(sorted(red))


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("names", nargs="*", help="run the cases whose name contains one of these")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()

    cases = [c for c in CASES if not args.names or any(n in c.name for n in args.names)]
    if args.list:
        for case in cases:
            print(case.name)
        return 0
    empty = [c.name for c in CASES if not c.red]
    names = [c.name for c in CASES]
    if empty or len(set(names)) != len(names):
        print(
            f"every case needs a red test and a name of its own: {empty or 'a name is used twice'}"
        )
        return 1

    code, _, log = run_tests(REPO, [])
    if code != 0:
        print("The unedited tree is not green, so no red can be trusted:\n" + log[-3000:])
        return 1
    print(
        f"GREEN  the unedited tree passes the whole suite; {len(cases)} case(s) follow", flush=True
    )

    failed = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for case, ok, line in pool.map(run_case, cases):
            print(f"{'ok  ' if ok else 'FAIL'}  {case.name}: {line}", flush=True)
            failed += 0 if ok else 1
    print(f"{len(cases) - failed} of {len(cases)} case(s) went red as expected")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
