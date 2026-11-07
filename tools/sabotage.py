"""Sabotage proof for tools/test_ansible_gated_apply.py: break each control, require its test red.

What it does, how to run one case and how to add one: README, section
"ansible-drift-check.yml", paragraph on tools/sabotage.py.
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
TESTS = "tools/test_ansible_gated_apply.py"

ACTION = ".github/actions/ssh-check-hosts/action.yml"
GATED = ".github/workflows/ansible-gated-apply.yml"
DRIFT = ".github/workflows/ansible-drift-check.yml"
PROOF = "tools/ssh_jump_proof.py"
JUMP_SELF = ".github/workflows/ssh-jump-self.yml"


@dataclass
class Edit:
    path: str
    old: str
    new: str
    count: int = 1


@dataclass
class Case:
    name: str
    edits: list[Edit]
    # Tests, as Class.method, that must go red. At least one.
    red: list[str] = field(default_factory=list)


S = "StructureTests"
V = "ValidateHostsTests"
P = "PlanTests"
A = "ApplyTests"
PIN = "PinTests"
DG = "DriftGuardTests"
DR = "DriftReportTests"
AL = "AlertTests"
PG = "PlanGuardTests"
AG = "ApplyGuardTests"
CF = "ConfirmMergeGroupTests"
SJ = "SshJumpTests"
AJ = "ApplyJumpTests"
SO = "SshOptionTests"
SP = "SshJumpProofLineTests"
SL = "SshJumpProofLogTests"
SF = "SshJumpProofFixtureTests"
RT = "RoleTestEntryPointTests"
PV = "PushValidateTests"
PP = "PushPlanTests"
PAG = "PushApplyGuardTests"
PA = "PushApplyTests"
PPar = "PushParityTests"
PO = "PushOffTests"
RL = "ReportLogTests"
SJ2 = "SshJump2Tests"
AJ2 = "ApplyJump2Tests"
JAG = "Jump2ApplyGuardTests"
JPar = "Jump2ParityTests"
CC = "ConcurrencyTests"

CASES: list[Case] = [
    Case(
        "delivery runs a Molecule sequence of its own beside the role test",
        [
            Edit(
                GATED,
                "  # The plan job. It runs only for a push",
                "      - name: Molecule check action\n        working-directory: ${{ inputs.working-directory }}/${{ inputs.ansible-directory }}/${{ matrix.path }}\n        run: '\"$RUNNER_TEMP/ansible-venv/bin/molecule\" check'\n\n  # The plan job. It runs only for a push",
            )
        ],
        [f"{RT}.test_test_the_role_is_the_only_step_of_the_shard_job_that_runs_molecule_or_a_role_test"],
    ),
    Case(
        "delivery ignores the caller's role test command",
        [Edit(GATED, 'if [ -n "$ROLE_TEST_COMMAND" ]; then', "if false; then")],
        [f"{RT}.test_a_set_command_is_run_from_the_ansible_directory_with_the_role_and_molecule_is_never_called"],
    ),
    Case(
        "delivery lets the caller's role test command through unchecked",
        [Edit(GATED, "if ! [[ \"$ROLE_TEST_COMMAND\" =~ $command_re ]] || [[ \"$ROLE_TEST_COMMAND\" == *..* ]]; then", "if false; then")],
        [f"{RT}.test_a_command_that_is_not_a_plain_relative_python_file_is_refused_before_anything_runs"],
    ),
    Case(
        "apply key passed in the drift workflow",
        [Edit(DRIFT, "          fail-on-changes: 'true'\n", "          fail-on-changes: 'true'\n          apply-key: ${{ secrets.ssh_apply_key }}\n")],
        [f"{S}.test_the_drift_workflow_takes_the_declared_inputs_and_three_required_secrets_and_no_apply_key", f"{S}.test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"],
    ),
    Case(
        "environment on the drift job",
        [Edit(DRIFT, "    timeout-minutes: ${{ inputs.check-timeout-minutes }}\n", "    timeout-minutes: ${{ inputs.check-timeout-minutes }}\n    environment: production\n")],
        [f"{S}.test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"],
    ),
    Case(
        "drift guard accepts every event",
        [Edit(DRIFT, "            schedule | workflow_dispatch) ;;", "            *) ;;")],
        [f"{DG}.test_every_other_event_branch_fork_commit_or_label_is_refused_and_flagged_as_a_refusal"],
    ),
    Case(
        "drift guard drops the fork check",
        [Edit(DRIFT, '          [ "$IS_FORK" = false ] || fail "The drift check runs only in the repository itself, never in a fork."\n', "")],
        [f"{DG}.test_every_other_event_branch_fork_commit_or_label_is_refused_and_flagged_as_a_refusal"],
    ),
    Case(
        "plan guard accepts schedule instead of push",
        [Edit(GATED, '[ "$EVENT_NAME" = push ] || fail "The plan job runs only for a push, not for $EVENT_NAME."', '[ "$EVENT_NAME" = schedule ] || fail "The plan job runs only for a push, not for $EVENT_NAME."')],
        [f"{PG}.test_a_push_to_the_default_branch_passes_and_publishes_the_commit"],
    ),
    Case(
        "drift exits green on one changed host",
        [Edit(ACTION, 'if [ "$FAIL_ON_CHANGES" = true ] && [ "$changed" -gt 0 ]; then', 'if [ "$FAIL_ON_CHANGES" = true ] && [ "$changed" -gt 1 ]; then')],
        [f"{DR}.test_a_host_that_would_change_is_red_in_a_drift_check_and_green_in_a_plan"],
    ),
    Case(
        "busy exit code changed",
        [Edit(ACTION, 'if [ "$rc" = 21 ] && [ "$f_verdict" = busy ]; then', 'if [ "$rc" = 22 ] && [ "$f_verdict" = busy ]; then')],
        [f"{DR}.test_a_refusal_is_not_drift_and_names_its_reason"],
    ),
    Case(
        "check judge regex drops the dash count of a refusal",
        [Edit(ACTION, "changed=([0-9]+|-)$'", "changed=([0-9]+)$'")],
        [f"{DR}.test_a_real_refusal_line_with_no_count_is_read_as_its_refusal"],
    ),
    Case(
        "apply judge regex drops the dash count of a refusal",
        [Edit(GATED, "changed=([0-9]+|-)$'", "changed=([0-9]+)$'")],
        [f"{A}.test_a_real_refusal_line_with_no_count_fails_the_apply_with_its_reason_and_asks_no_second_run"],
    ),
    Case(
        "would-change with a dash count is read as drift",
        [Edit(ACTION, ' && [ "$f_changed" != - ]; then', "; then")],
        [f"{DR}.test_a_dash_count_never_makes_an_unchanged_host_clean_or_a_would_change_host_drift"],
    ),
    Case(
        "apply accepts an applied or unchanged line with a dash count",
        [Edit(GATED, '[ "$f_changed" = - ] || { [ "$f_verdict"', '{ [ "$f_verdict"')],
        [f"{A}.test_an_applied_or_unchanged_line_that_counts_no_change_as_a_dash_fails_the_job"],
    ),
    Case(
        "plan has_changes read from the wrong step",
        [Edit(GATED, "has_changes: ${{ steps.ssh.outputs.has-changes }}", "has_changes: ${{ steps.guard.outputs.has-changes }}")],
        [f"{S}.test_the_plan_job_outputs_come_from_the_steps_that_make_them"],
    ),
    Case(
        "plan hosts_digest read from the wrong step",
        [Edit(GATED, "hosts_digest: ${{ steps.ssh.outputs.hosts-digest }}", "hosts_digest: ${{ steps.guard.outputs.hosts-digest }}")],
        [f"{S}.test_the_plan_job_outputs_come_from_the_steps_that_make_them"],
    ),
    Case(
        "host list joined with a comma in the apply guard",
        [Edit(GATED, 'list="${list:+$list }$dest"', 'list="${list:+$list,}$dest"')],
        [f"{V}.test_the_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes"],
    ),
    Case(
        "host list joined with a comma in the action",
        [Edit(ACTION, 'list="${list:+$list }$dest"', 'list="${list:+$list,}$dest"')],
        [f"{V}.test_the_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes"],
    ),
    Case(
        "shared actions checked out at a literal tag",
        [Edit(GATED, "ref: ${{ steps.pin.outputs.sha }}", "ref: v1.0.0")],
        [f"{S}.test_the_shared_actions_are_checked_out_after_a_pin_that_cannot_be_empty_and_before_they_run"],
    ),
    Case(
        "a second local action in the plan job",
        [
            Edit(
                GATED,
                "      - name: Check every host in check mode and write the plan\n        id: ssh\n",
                "      - name: A second local action\n        uses: ./.github/actions/plan-has-changes\n\n      - name: Check every host in check mode and write the plan\n        id: ssh\n",
            )
        ],
        [f"{S}.test_every_action_is_pinned_to_a_commit_with_a_version_comment_or_is_the_one_checked_out_action"],
    ),
    Case(
        "pin accepts any non-empty value",
        [Edit(GATED, '[[ "$pinned" =~ $sha_re ]] || fail', '[ -n "$pinned" ] || fail'), Edit(DRIFT, '[[ "$pinned" =~ $sha_re ]] || fail', '[ -n "$pinned" ] || fail')],
        [f"{PIN}.test_no_commit_two_commits_or_a_commit_of_another_repository_is_refused_and_never_falls_back"],
    ),
    Case(
        "pin publishes the tag object instead of the commit it peels to",
        [Edit(GATED, 'commit="$peeled"', 'commit="$pinned"'), Edit(DRIFT, 'commit="$peeled"', 'commit="$pinned"')],
        [f"{PIN}.test_a_signed_tag_object_is_peeled_to_its_commit_which_is_published_and_both_are_logged"],
    ),
    Case(
        "pin accepts a tag that is not verified",
        [Edit(GATED, '[ "$verified" = true ] || fail', '[ -n "$verified" ] || fail'), Edit(DRIFT, '[ "$verified" = true ] || fail', '[ -n "$verified" ] || fail')],
        [f"{PIN}.test_every_answer_that_is_not_a_verified_tag_of_a_commit_or_a_plain_commit_is_refused_and_publishes_nothing"],
    ),
    Case(
        "pin accepts two or more commits",
        [Edit(GATED, '[ "$count" -eq 1 ] ||', '[ "$count" -ge 1 ] ||'), Edit(DRIFT, '[ "$count" -eq 1 ] ||', '[ "$count" -ge 1 ] ||')],
        [f"{PIN}.test_no_commit_two_commits_or_a_commit_of_another_repository_is_refused_and_never_falls_back"],
    ),
    Case(
        "verify step checks only that HEAD is non-empty",
        [Edit(GATED, '[ "$head" = "$PINNED_SHA" ] || fail', '[ -n "$head" ] || fail'), Edit(DRIFT, '[ "$head" = "$PINNED_SHA" ] || fail', '[ -n "$head" ] || fail')],
        [f"{PIN}.test_the_checkout_must_be_exactly_the_pinned_commit_and_hold_the_action"],
    ),
    Case(
        "existence check pointed at another path",
        [Edit(GATED, '[ -e "$GITHUB_WORKSPACE/.gw" ]', '[ -e "$GITHUB_WORKSPACE/.gwx" ]'), Edit(DRIFT, '[ -e "$GITHUB_WORKSPACE/.gw" ]', '[ -e "$GITHUB_WORKSPACE/.gwx" ]')],
        [f"{PIN}.test_a_path_the_caller_already_holds_is_refused"],
    ),
    Case(
        "expression inside an action run body",
        [Edit(ACTION, 'echo "Checking commit $SHA on $count host(s)."', 'echo "Checking commit ${{ inputs.sha }} on $count host(s)."')],
        [f"{S}.test_no_expression_inside_a_run_body"],
    ),
    Case(
        "shell dropped from an action step",
        [Edit(ACTION, "      id: validate\n      shell: bash\n", "      id: validate\n")],
        [f"{S}.test_every_run_step_of_the_action_names_bash_and_stops_on_error"],
    ),
    Case(
        "pipefail dropped from an action step",
        [Edit(ACTION, '        set -euo pipefail\n        fail() {\n          echo "::error::$1"\n          exit 1\n        }\n        sha_re=', '        set -eu\n        fail() {\n          echo "::error::$1"\n          exit 1\n        }\n        sha_re=')],
        [f"{S}.test_every_run_step_of_the_action_names_bash_and_stops_on_error"],
    ),
    Case(
        "write permission on the drift job",
        [Edit(DRIFT, "    permissions:\n      actions: read\n      contents: read\n", "    permissions:\n      actions: read\n      contents: write\n")],
        [f"{S}.test_issues_write_is_granted_to_the_alert_job_alone_and_nothing_else_may_write"],
    ),
    Case(
        "alert condition without the guard-refusal test",
        [Edit(DRIFT, "      && needs.drift.outputs.guard_refused != 'true'\n", "")],
        [f"{S}.test_the_alert_runs_after_a_failed_or_timed_out_check_that_passed_the_guard_and_serialises_per_host_set"],
    ),
    Case(
        "alert never comments",
        [Edit(DRIFT, 'if [ -n "$number" ]; then', "if false; then")],
        [f"{AL}.test_with_an_open_issue_a_comment_is_added_to_that_issue_and_none_is_created"],
    ),
    Case(
        "issue created without its label",
        [Edit(DRIFT, 'gh issue create --title "$title" --label "$label" --body-file "$body"', 'gh issue create --title "$title" --body-file "$body"')],
        [f"{AL}.test_with_no_open_issue_one_is_created_with_the_label_and_the_title_and_no_comment_is_made"],
    ),
    Case(
        "detail not cut to the safe alphabet",
        [Edit(DRIFT, "detail=$(printf '%s' \"$DETAIL\" | tr -cd 'A-Za-z0-9 :;()._-' | head -c 300)", "detail=$(printf '%s' \"$DETAIL\" | head -c 300)")],
        [f"{AL}.test_a_missing_outcome_says_unknown_and_anything_outside_the_safe_alphabet_is_dropped"],
    ),
    Case(
        "host-shaped pattern added to the drift guard",
        [Edit(DRIFT, "          sha_re='^[0-9a-f]{40}$'\n          label_re=", "          host_re='^[a-z_][a-z0-9_-]{0,31}@'\n          sha_re='^[0-9a-f]{40}$'\n          label_re=")],
        [f"{S}.test_the_host_list_is_validated_in_the_action_and_nowhere_else"],
    ),
    Case(
        "plan job calls the action by a caller-relative path",
        [Edit(GATED, "uses: ./.gw/.github/actions/ssh-check-hosts", "uses: ./.github/actions/ssh-check-hosts")],
        [f"{S}.test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"],
    ),
    Case(
        "plan job passes fail-on-changes true",
        [Edit(GATED, "          host-timeout: ${{ inputs.host-timeout-minutes }}\n          environment: ${{ inputs.environment }}", "          host-timeout: ${{ inputs.host-timeout-minutes }}\n          fail-on-changes: 'true'\n          environment: ${{ inputs.environment }}")],
        [f"{S}.test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"],
    ),
    Case(
        "plan summary line reworded",
        [Edit(ACTION, 'first="Changes: $changed of $total host(s)', 'first="Change: $changed of $total host(s)')],
        [f"{P}.test_one_host_with_changes_gives_has_changes_and_shows_its_diff"],
    ),
    # The second round of review (ws#2342): the stdin defect, the key copy, the time given to the hosts.
    Case(
        "ssh in the action takes the host list from its own input",
        [Edit(ACTION, '-- "$dest" "$SHA" < /dev/null >', '-- "$dest" "$SHA" >')],
        [f"{P}.test_every_host_is_asked_when_the_call_reads_its_standard_input"],
    ),
    Case(
        "dry-run stand-in in the action takes the host list from its own input",
        [Edit(ACTION, '"$stub" "$dest" "$SHA" < /dev/null >', '"$stub" "$dest" "$SHA" >')],
        [f"{P}.test_every_host_is_asked_when_the_dry_run_stand_in_reads_its_standard_input"],
    ),
    Case(
        "ssh in the apply takes the host list from its own input",
        [Edit(GATED, '-- "$dest" "$SHA" < /dev/null 2> /dev/null)', '-- "$dest" "$SHA" 2> /dev/null)')],
        [f"{A}.test_every_planned_host_is_applied_and_checked_again_when_the_call_reads_its_standard_input"],
    ),
    Case(
        "key copy not removed",
        [Edit(ACTION, "        trap 'rm -f \"$keylines\"' EXIT\n", "")],
        [f"{P}.test_key_material_is_copied_private_and_removed_on_every_way_out"],
    ),
    Case(
        "key copy removed only on the way out that succeeds",
        [Edit(ACTION, "        trap 'rm -f \"$keylines\"' EXIT\n", ""), Edit(ACTION, '        echo "$first"\n', '        echo "$first"\n        rm -f "$keylines"\n')],
        [f"{P}.test_key_material_is_copied_private_and_removed_on_every_way_out"],
    ),
    # The fifth round of review (ws#2342): the key copies of the check and the apply go on every way out.
    Case(
        "check key copies not removed",
        [Edit(ACTION, "        trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir\"/id.*' EXIT\n", "")],
        [f"{P}.test_the_check_key_and_known_hosts_are_removed_on_every_way_out_a_failure_included"],
    ),
    Case(
        "check key copies removed only on the way out that succeeds",
        [
            Edit(ACTION, "        trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir\"/id.*' EXIT\n", ""),
            Edit(ACTION, '        echo "Asked $i host(s); the answers are judged in the next step."\n', '        rm -f "$plan_dir/id" "$plan_dir/known_hosts"\n        echo "Asked $i host(s); the answers are judged in the next step."\n'),
        ],
        [f"{P}.test_the_check_key_and_known_hosts_are_removed_on_every_way_out_a_failure_included"],
    ),
    Case(
        "apply key copies not removed",
        [Edit(GATED, "          trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work\"/id.*' EXIT\n", "")],
        [f"{A}.test_the_apply_key_and_known_hosts_are_removed_on_every_way_out_a_refusal_included"],
    ),
    Case(
        "apply key copies removed only on the way out that succeeds",
        [
            Edit(GATED, "          trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work\"/id.*' EXIT\n", ""),
            Edit(GATED, '          echo "Applied commit $SHA on $i host(s)."\n', '          rm -f "$work/id" "$work/known_hosts"\n          echo "Applied commit $SHA on $i host(s)."\n'),
        ],
        [f"{A}.test_the_apply_key_and_known_hosts_are_removed_on_every_way_out_a_refusal_included"],
    ),
    Case(
        "key copy made with the default mask",
        [Edit(ACTION, '(umask 077 && : > "$keylines")', ': > "$keylines"')],
        [f"{P}.test_key_material_is_copied_private_and_removed_on_every_way_out"],
    ),
    Case(
        "total time not applied to a call",
        [Edit(ACTION, '              limit="$left"\n', '              limit="$limit"\n')],
        [f"{P}.test_with_a_total_time_each_call_is_cut_to_what_is_left_and_a_host_reached_after_it_is_not_asked"],
    ),
    Case(
        "host reached after the total time is still asked",
        [Edit(ACTION, 'if [ "$limit" -le 0 ]; then', "if false; then")],
        [f"{P}.test_with_a_total_time_each_call_is_cut_to_what_is_left_and_a_host_reached_after_it_is_not_asked"],
    ),
    Case(
        "total time longer than the host limit overrides it",
        [Edit(ACTION, 'if [ "$left" -lt "$limit" ]; then', 'if [ "$left" -ne "$limit" ]; then')],
        [f"{P}.test_a_total_time_longer_than_the_host_limit_leaves_the_host_limit_alone"],
    ),
    Case(
        "host limit not validated",
        [Edit(ACTION, '[[ "$HOST_TIMEOUT_MINUTES" =~ $minutes_re ]] || fail "host-timeout must be a whole number of minutes, at least 1."', ":")],
        [f"{V}.test_the_timeouts_are_whole_numbers_of_minutes_and_the_total_may_be_empty"],
    ),
    Case(
        "check timeout range not validated in the drift guard",
        [Edit(DRIFT, '{ [ "$CHECK_TIMEOUT" -ge 10 ] && [ "$CHECK_TIMEOUT" -le 350 ]; } \\\n', "true \\\n")],
        [f"{DG}.test_every_other_event_branch_fork_commit_or_label_is_refused_and_flagged_as_a_refusal"],
    ),
    Case(
        "hosts given the whole job bound",
        [Edit(DRIFT, 'echo "budget=$((CHECK_TIMEOUT - 5))" >> "$GITHUB_OUTPUT"', 'echo "budget=$((CHECK_TIMEOUT))" >> "$GITHUB_OUTPUT"')],
        [f"{DG}.test_the_hosts_get_five_minutes_less_than_the_job_bound"],
    ),
    Case(
        "drift job given no total time",
        [Edit(DRIFT, "          total-timeout: ${{ steps.guard.outputs.budget }}\n", "")],
        [f"{S}.test_the_drift_job_is_bounded_by_the_caller_input_and_gives_the_hosts_less_than_that"],
    ),
    Case(
        "alert ignores a timed-out check",
        [Edit(DRIFT, "      && (needs.drift.result == 'failure' || needs.drift.result == 'cancelled')\n", "      && needs.drift.result == 'failure'\n")],
        [f"{S}.test_the_alert_runs_after_a_failed_or_timed_out_check_that_passed_the_guard_and_serialises_per_host_set"],
    ),
    Case(
        "apply gate: environment key deleted from the apply job",
        [Edit(GATED, "    timeout-minutes: ${{ inputs.apply-timeout-minutes }}\n    environment: ${{ inputs.environment }}\n", "    timeout-minutes: ${{ inputs.apply-timeout-minutes }}\n")],
        [f"{S}.test_the_apply_job_is_gated"],
    ),
    Case(
        "apply gate: environment hard-coded in the apply job",
        [Edit(GATED, "    timeout-minutes: ${{ inputs.apply-timeout-minutes }}\n    environment: ${{ inputs.environment }}\n", "    timeout-minutes: ${{ inputs.apply-timeout-minutes }}\n    environment: production\n")],
        [f"{S}.test_the_apply_job_is_gated"],
    ),
    Case(
        "apply gate: has_changes condition dropped from the apply if",
        [Edit(GATED, "      && needs.plan.outputs.has_changes != 'false'\n", "")],
        [f"{S}.test_the_apply_job_is_gated"],
    ),
    Case(
        "apply gate: non-empty environment term dropped from the apply if",
        [Edit(GATED, "      && inputs.environment != ''\n", "")],
        [f"{S}.test_the_apply_job_is_gated"],
    ),
    Case(
        "apply gate: empty apply key no longer refused",
        [Edit(GATED, '[ -n "$SSH_KEY" ] || [ -n "$HOST_KEYS" ] || fail "The ssh_apply_key secret is empty. It is a secret of the deployment environment."', ":")],
        [f"{A}.test_the_apply_key_secret_must_be_present"],
    ),
    Case(
        "apply gate: apply guard passes a plan that found nothing",
        [Edit(GATED, '[ "$HAS_CHANGES" != false ] || fail "The plan found nothing to change."', ":")],
        ["ApplyGuardTests.test_the_apply_refuses_a_plan_with_nothing_to_change"],
    ),
    Case(
        "apply gate: no-change verdict when a host would change",
        [Edit(ACTION, '        if [ "$changed" -eq 0 ] && [ "$clean" -eq "$total" ] && [ "$total" -ge 1 ]; then', '        if [ "$total" -ge 1 ]; then')],
        [f"{P}.test_one_host_with_changes_gives_has_changes_and_shows_its_diff"],
    ),
    Case(
        "apply gate: a failed host does not fail the plan",
        [Edit(ACTION, '        if [ "$failed" -gt 0 ]; then\n          echo "::error::$first"', '        if false; then\n          echo "::error::$first"')],
        [f"{P}.test_a_host_that_fails_fails_the_job_and_asks_for_no_approval"],
    ),
    # The refusals of the apply guard, one case each.
    Case(
        "apply gate: event check dropped from the apply guard",
        [Edit(GATED, '[ "$EVENT_NAME" = push ] || fail "The apply job runs only for a push, not for $EVENT_NAME."', ":")],
        [f"{AG}.test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run"],
    ),
    Case(
        "apply gate: default-branch check dropped from the apply guard",
        [Edit(GATED, '[ "$REF" = "refs/heads/$DEFAULT_BRANCH" ] || fail "The apply job runs only for the default branch."', ":")],
        [f"{AG}.test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run"],
    ),
    Case(
        "apply gate: fork check dropped from the apply guard",
        [Edit(GATED, '[ "$IS_FORK" != true ] || fail "The apply job never runs in a fork."', ":")],
        [f"{AG}.test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run"],
    ),
    Case(
        "apply gate: dry-run check dropped from the apply guard",
        [Edit(GATED, '[ "$DRY_RUN" != true ] || fail "The apply job never runs in a dry run."', ":")],
        [f"{AG}.test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run"],
    ),
    Case(
        "apply gate: empty-environment backstop dropped from the apply guard",
        [Edit(GATED, '[ -n "$ENVIRONMENT" ] || fail "The environment input is empty, so there is no approval."', ":")],
        [f"{AG}.test_the_apply_refuses_without_an_environment"],
    ),
    Case(
        "apply gate: planned commit format check dropped",
        [Edit(GATED, "grep -Eq '^[0-9a-f]{40}$' || fail \"The planned commit is not 40 lowercase hex digits.\"", "grep -Eq '^[0-9a-f]{40}$' || :")],
        [f"{AG}.test_the_planned_commit_must_be_40_lowercase_hex_digits"],
    ),
    Case(
        "apply gate: planned commit equality check dropped",
        [Edit(GATED, '[ "$PLANNED_SHA" = "$SHA" ] || fail "The planned commit differs from this run\'s commit."', ":")],
        [f"{AG}.test_the_apply_refuses_a_commit_other_than_the_planned_one"],
    ),
    Case(
        "apply gate: planned host digest format check dropped",
        [Edit(GATED, "grep -Eq '^[0-9a-f]{64}$' || fail \"The planned host digest is missing.\"", "grep -Eq '^[0-9a-f]{64}$' || :")],
        [f"{AG}.test_the_planned_host_digest_must_be_64_lowercase_hex_digits"],
    ),
    Case(
        "apply gate: planned host digest equality check dropped",
        [Edit(GATED, '[ "$digest" = "$PLANNED_DIGEST" ] || fail "The hosts are not the hosts that were planned."', ":")],
        [f"{AG}.test_the_apply_refuses_hosts_that_were_not_planned"],
    ),
    Case(
        "apply gate: a host refusal of an older commit is accepted",
        [Edit(GATED, '            if [ "$ok" -ne 0 ] || [ "$f_changed" = - ] || {', '            if [ "$ok" -ne 0 ] && [ "$rc:$f_verdict" != "20:refused" ] || [ "$f_changed" = - ] || {')],
        [f"{A}.test_a_host_that_already_ran_a_newer_commit_refuses_and_the_apply_fails_with_the_reason"],
    ),
    Case(
        "apply gate: a rollback refusal does not name the commits",
        [Edit(GATED, '              if [ "$f_reason" = rollback ] && [ "$f_verdict" = refused ] && [ "$f_exit" = 20 ]; then', '              if false; then')],
        [
            f"{A}.test_a_rollback_refusal_names_the_planned_commit_and_the_hosts_last_applied_commit",
            f"{A}.test_a_rollback_refusal_with_no_floor_names_the_planned_commit_and_says_where_the_floor_is",
            f"{PA}.test_a_pushed_host_that_refuses_a_rollback_names_both_commits_when_its_line_carries_the_floor",
        ],
    ),
    Case(
        "apply gate: environment term joined with or in the apply if",
        [Edit(GATED, "      && inputs.environment != ''\n", "      || inputs.environment != ''\n")],
        [f"{S}.test_the_apply_job_is_gated"],
    ),
    Case(
        "drift alert: guard-refused term joined with or",
        [Edit(DRIFT, "      && needs.drift.outputs.guard_refused != 'true'\n", "      || needs.drift.outputs.guard_refused != 'true'\n")],
        [f"{S}.test_the_alert_runs_after_a_failed_or_timed_out_check_that_passed_the_guard_and_serialises_per_host_set"],
    ),
    Case(
        "apply gate: empty known-hosts refusal no longer refused",
        [Edit(GATED, '[ -n "$KNOWN_HOSTS" ] || fail "The ssh_known_hosts secret is empty."', ":")],
        [f"{A}.test_the_known_hosts_secret_must_be_present"],
    ),
    Case(
        "confirm accepts a check run of any app",
        [Edit(GATED, ' and .app.slug == "github-actions")', ')')],
        [f"{CF}.test_a_check_run_made_by_another_app_is_refused"],
    ),
    Case(
        "confirm accepts a passing check from a suite that is not a merge queue run",
        [Edit(GATED, '(.head_branch // "" | startswith("gh-readonly-queue/")) and .head_sha == $sha', '.head_sha == $sha')],
        [f"{CF}.test_a_passing_check_that_is_not_from_a_merge_queue_run_is_refused"],
    ),
    Case(
        "confirm accepts a queue suite of another commit",
        [Edit(GATED, ' and .head_sha == $sha\' > /dev/null; then', '\' > /dev/null; then')],
        [f"{CF}.test_a_passing_check_that_is_not_from_a_merge_queue_run_is_refused"],
    ),
    Case(
        "confirm accepts a check that did not succeed",
        [Edit(GATED, '.conclusion == "success" and .head_sha', '.head_sha')],
        [f"{CF}.test_a_check_that_failed_or_is_unfinished_or_was_skipped_is_refused"],
    ),
    Case(
        "confirm accepts a check of another name",
        [Edit(GATED, 'select(.name == $name and .status', 'select(.status')],
        [f"{CF}.test_a_check_of_another_name_is_refused"],
    ),
    Case(
        "confirm accepts a check run of another commit",
        [Edit(GATED, ' and .head_sha == $sha and .app.slug', ' and .app.slug')],
        [f"{CF}.test_a_check_run_for_another_commit_is_refused"],
    ),
    Case(
        "confirm passes when the checks cannot be read",
        [Edit(GATED, 'fail "The check runs of the commit could not be read, so nothing shows the merge queue passed it. Refusing."', 'runs=\'{"check_runs": []}\'')],
        [f"{CF}.test_when_the_checks_cannot_be_read_it_cannot_tell_and_refuses"],
    ),
    Case(
        "confirm falls through to success when nothing matched",
        [Edit(GATED, '          fail "No passing \'$CHECK_NAME\'', '          exit 0; fail "No passing \'$CHECK_NAME\'')],
        [f"{CF}.test_a_commit_with_no_check_runs_is_refused"],
    ),
    Case(
        "confirm step never runs",
        [Edit(GATED, "        if: steps.context.outputs.rerun == 'false'\n", "        if: steps.context.outputs.rerun == 'never'\n")],
        ["SkipRerunDeclarationTests.test_the_confirmation_is_a_discover_step_that_runs_only_when_the_rerun_is_skipped"],
    ),
    Case(
        "rerun skipped on a run that does not deliver",
        [Edit(GATED, 'if [ "$delivery" = true ] && [ "$SKIP_RERUN" = true ]; then', 'if [ "$SKIP_RERUN" = true ]; then')],
        ["RerunDecisionTests.test_a_run_that_does_not_deliver_always_reruns_whatever_it_asks"],
    ),
    Case(
        "skip input defaults to on",
        [Edit(GATED, "        type: boolean\n        required: false\n        default: false\n      merge-group-check-name:", "        type: boolean\n        required: false\n        default: true\n      merge-group-check-name:")],
        ["SkipRerunDeclarationTests.test_the_inputs_default_to_todays_behaviour"],
    ),
    Case(
        "lint runs on a delivery run that asked to skip it",
        [Edit(GATED, "    needs: [discover]\n    if: needs.discover.outputs.recheck == 'true'\n    runs-on: ubuntu-24.04\n    timeout-minutes: 20", "    needs: [discover]\n    runs-on: ubuntu-24.04\n    timeout-minutes: 20")],
        ["SkipRerunDeclarationTests.test_lint_and_the_shards_run_unless_discover_says_the_merge_queue_already_did"],
    ),
    Case(
        "gate accepts lint that ran when it was to be skipped",
        [Edit(GATED, 'expect lint "$LINT" skipped', 'expect lint "$LINT" "$LINT"')],
        ["GateTests.test_a_delivery_run_that_skipped_the_rerun_needs_lint_and_the_shards_skipped"],
    ),
    Case(
        "gate accepts skipped lint when it was to run",
        [Edit(GATED, 'expect lint "$LINT" success', 'expect lint "$LINT" "$LINT"')],
        ["GateTests.test_a_run_that_reruns_still_needs_lint_and_the_shards_to_have_passed"],
    ),
    Case(
        "plan runs after a failed lint",
        [Edit(GATED, "      && (needs.lint.result == 'success' || needs.lint.result == 'skipped')\n", "")],
        [f"{S}.test_the_plan_job_runs_only_for_delivery_and_only_after_every_need_succeeded_or_was_skipped"],
    ),
    Case(
        "apply has no leading status function, so a skipped lint skips it",
        [Edit(GATED, "      !cancelled() && !failure()\n      && needs.discover.outputs.delivery", "      needs.discover.outputs.delivery")],
        [f"{S}.test_the_apply_job_leads_with_a_status_function_so_a_skipped_lint_does_not_skip_it"],
    ),
    Case(
        "jump hop stanza drops StrictHostKeyChecking",
        [Edit(ACTION, "\n                printf '  StrictHostKeyChecking yes\\n'", "")],
        [f"{SJ}.test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones", f"{SJ}.test_the_config_file_is_exactly_one_stanza_for_the_hop"],
    ),
    Case(
        "jump hop stanza drops IdentitiesOnly",
        [Edit(ACTION, "\n                printf '  IdentitiesOnly yes\\n'", "")],
        [f"{SJ}.test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones"],
    ),
    Case(
        "jump hop stanza drops BatchMode",
        [Edit(ACTION, "\n                printf '  BatchMode yes\\n'", "")],
        [f"{SJ}.test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones"],
    ),
    Case(
        "jump hop stanza reads the global known hosts",
        [Edit(ACTION, "\n                printf '  GlobalKnownHostsFile /dev/null\\n'", "")],
        [f"{SJ}.test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones"],
    ),
    Case(
        "jump hop stanza drops ClearAllForwardings",
        [Edit(ACTION, "\n                printf '  ClearAllForwardings yes\\n'", "")],
        [f"{SJ}.test_the_hop_has_its_own_strict_options_because_ssh_passes_none_of_the_outer_ones"],
    ),
    Case(
        "jump hop uses the check key",
        [Edit(ACTION, "printf '  IdentityFile %s\\n' \"$plan_dir/jump_id\"", "printf '  IdentityFile %s\\n' \"$plan_dir/id\"")],
        [f"{SJ}.test_the_check_key_is_never_offered_to_the_gateway_and_the_jump_key_never_to_a_target"],
    ),
    Case(
        "jump host without a jump key passes validation",
        [Edit(ACTION, "          [ -n \"$JUMP_KEY\" ] || fail \"jump-host is set but jump-key is empty: a jump needs both or neither.\"\n", "")],
        [f"{SJ}.test_only_one_of_the_two_inputs_fails_the_validate_step_and_names_the_missing_one"],
    ),
    Case(
        "jump key without a jump host passes validation",
        [Edit(ACTION, "          [ -n \"$JUMP_HOST\" ] || fail \"jump-key is set but jump-host is empty: a jump needs both or neither.\"\n", "")],
        [f"{SJ}.test_only_one_of_the_two_inputs_fails_the_validate_step_and_names_the_missing_one"],
    ),
    Case(
        "jump host with a second line passes validation",
        [Edit(ACTION, "          case \"$JUMP_HOST\" in *$'\\n'*) fail \"jump-host is not of the form user@host.\" ;; esac\n", "")],
        [f"{SJ}.test_a_malformed_jump_host_fails_the_validate_step_and_is_never_echoed"],
    ),
    Case(
        "jump host form is not checked",
        [Edit(ACTION, "printf '%s' \"$JUMP_HOST\" | grep -Eq \"$destination_pattern\"", "printf '%s' \"$JUMP_HOST\" | grep -Eq '.'")],
        [f"{SJ}.test_a_malformed_jump_host_fails_the_validate_step_and_is_never_echoed"],
    ),
    Case(
        "host names may hold an underscore",
        [Edit(ACTION, "@[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'\n", "@[A-Za-z0-9]([A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$'\n")],
        [f"{SJ}.test_the_alias_of_the_hop_is_not_a_name_the_hosts_input_can_hold"],
    ),
    Case(
        "trap leaves the jump key and config behind",
        [Edit(ACTION, "            trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir\"/id.* \"$plan_dir/jump_id\" \"$plan_dir/jump_config\"' EXIT\n", "")],
        [f"{SJ}.test_the_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out"],
    ),
    Case(
        "jump key and config written with the runner umask",
        [Edit(ACTION, "              umask 077\n              printf '%s\\n' \"$JUMP_KEY\"", "              printf '%s\\n' \"$JUMP_KEY\"")],
        [f"{SJ}.test_the_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out"],
    ),
    Case(
        "jump hop configured when no jump is set",
        [Edit(ACTION, "          if [ -n \"$JUMP_HOST\" ]; then\n            # ssh gives", "          if true; then\n            # ssh gives")],
        [f"{SJ}.test_with_no_jump_the_ssh_argv_the_files_and_the_removals_are_exactly_what_they_were"],
    ),
    Case(
        "jump call drops the proxy jump option",
        [Edit(ACTION, "ssh_args+=(-F \"$plan_dir/jump_config\" -o ProxyJump=jump_hop)", "ssh_args+=(-F \"$plan_dir/jump_config\")")],
        [f"{SJ}.test_a_jump_adds_the_config_file_and_the_proxy_jump_option_after_the_unchanged_options"],
    ),
    Case(
        "jump host left in the text of a host",
        [Edit(ACTION, "                    if (jd != \"\") { s = mark(mark(s, jd, \"\\002\"), jh, \"\\002\") }\n", "")],
        [f"{SJ}.test_the_jump_host_is_replaced_in_a_host_text_that_reaches_the_summary"],
    ),
    Case(
        "a failing jump is not unreachable",
        [Edit(ACTION, "elif [ \"$rc\" = 255 ]; then\n            note=\"the SSH connection failed (exit 255)\"", "elif [ \"$rc\" = 254 ]; then\n            note=\"the SSH connection failed (exit 255)\"")],
        [f"{SJ}.test_a_failing_jump_is_reported_as_unreachable_and_no_name_reaches_any_output"],
    ),
    Case(
        "jump proof line permits any destination",
        [Edit(PROOF, "*(f'permitopen=\"{d.host}:{d.port}\"' for d in destinations)", "*('permitopen=\"*:*\"' for d in destinations)")],
        [f"{SP}.test_the_line_is_restrict_then_port_forwarding_one_permitopen_and_a_forced_command", f"{SP}.test_permitopen_is_exactly_one_destination_with_no_wildcard_and_no_list"],
    ),
    Case(
        "jump proof line drops the forced command",
        [Edit(PROOF, "    if forced_command is not None:", "    if forced_command is None:")],
        [f"{SP}.test_the_line_carries_a_forced_command_unless_the_caller_asks_for_none"],
    ),
    Case(
        "jump proof line accepts a wildcard or a list as the host",
        [Edit(PROOF, "ADDRESS_PATTERN = re.compile(r\"^[A-Za-z0-9][A-Za-z0-9.-]*$\")", "ADDRESS_PATTERN = re.compile(r\"^[A-Za-z0-9*][A-Za-z0-9.*,-]*$\")")],
        [f"{SP}.test_a_wildcard_a_list_or_an_injected_option_is_refused_before_it_reaches_the_line"],
    ),
    Case(
        "jump proof line accepts a port out of range",
        [Edit(PROOF, "        if not 1 <= destination.port <= 65535:", "        if not 0 <= destination.port <= 70000:")],
        [f"{SP}.test_a_wildcard_a_list_or_an_injected_option_is_refused_before_it_reaches_the_line"],
    ),
    Case(
        "jump proof counts a denied forward as permitted",
        [Edit(PROOF, "    remaining.subtract(Counter(denied_forwards(log)))\n", "")],
        [f"{SL}.test_a_forward_is_permitted_only_when_it_was_requested_and_not_denied"],
    ),
    Case(
        "jump proof gives the documented gateway account a login shell and no forced command",
        [Edit(PROOF, "GatewayAccount(JUMP_USER, NO_SHELL, True, False,", "GatewayAccount(JUMP_USER, LOGIN_SHELL, False, False,")],
        [f"{SP}.test_the_documented_account_has_no_shell_and_a_forced_command_and_only_the_control_runs_commands"],
    ),
    Case(
        "jump proof target line is not restricted",
        [Edit(PROOF, "return f'restrict,command=\"{command_path}\" {public_key}'", "return f'command=\"{command_path}\" {public_key}'")],
        [f"{SP}.test_the_target_line_forces_the_one_command_and_restricts_the_key"],
    ),
    Case(
        "jump proof stand-in target answers in a form the report step rejects",
        [Edit(PROOF, "playbook=site.yml changed=0", "playbook=site.yml changed=x")],
        [f"{SF}.test_the_stand_in_target_prints_a_summary_line_the_report_step_reads_as_clean"],
    ),
    Case(
        "jump self-test workflow gains a path filter",
        [Edit(JUMP_SELF, "  pull_request:\n  push:", "  pull_request:\n    paths: ['tools/**']\n  push:")],
        [f"{SF}.test_the_self_test_workflow_runs_on_every_pull_request_and_main_with_no_path_filter"],
    ),
    Case(
        "jump self-test workflow job loses its timeout",
        [Edit(JUMP_SELF, "    timeout-minutes: 10\n", "")],
        [f"{SF}.test_the_self_test_workflow_runs_on_every_pull_request_and_main_with_no_path_filter"],
    ),
    Case(
        "jump self-test workflow does not run the proof",
        [Edit(JUMP_SELF, "run: python3 tools/ssh_jump_proof.py", "run: python3 tools/ssh_jump_proof.py --help")],
        [f"{SF}.test_the_self_test_workflow_runs_on_every_pull_request_and_main_with_no_path_filter"],
    ),
    # The jump in the gated apply and the drift check (ws#2625). Cases a, b and c
    # are the three the brief names; the rest are the other controls of the
    # apply step's copy of the hop.
    Case(
        "apply jump hop stanza drops StrictHostKeyChecking",
        [Edit(GATED, "\n                printf '  StrictHostKeyChecking yes\\n'", "")],
        [
            f"{AJ}.test_the_hop_has_its_own_strict_options_in_the_apply_too",
            f"{AJ}.test_the_stanza_the_destination_pattern_and_the_trap_in_the_apply_step_equal_the_actions",
            f"{AJ}.test_the_apply_step_writes_the_same_config_and_key_as_the_action_for_the_same_jump",
        ],
    ),
    Case(
        "apply jump hop uses the apply key",
        [Edit(GATED, "printf '  IdentityFile %s\\n' \"$work/jump_id\"", "printf '  IdentityFile %s\\n' \"$work/id\"")],
        [
            f"{AJ}.test_the_apply_key_is_never_offered_to_the_gateway_and_the_jump_key_never_to_a_target",
            f"{AJ}.test_the_apply_step_writes_the_same_config_and_key_as_the_action_for_the_same_jump",
        ],
    ),
    Case(
        "apply trap leaves the jump key and config behind",
        [Edit(GATED, "            trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work\"/id.* \"$work/jump_id\" \"$work/jump_config\"' EXIT\n", "")],
        [f"{AJ}.test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out"],
    ),
    Case(
        "apply jump key and config written with the runner umask",
        [Edit(GATED, "              umask 077\n              printf '%s\\n' \"$JUMP_KEY\"", "              printf '%s\\n' \"$JUMP_KEY\"")],
        [f"{AJ}.test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out"],
    ),
    Case(
        "apply jump call drops the proxy jump option",
        [Edit(GATED, "ssh_args+=(-F \"$work/jump_config\" -o ProxyJump=jump_hop)", "ssh_args+=(-F \"$work/jump_config\")")],
        [f"{AJ}.test_a_jump_adds_the_config_file_and_the_proxy_jump_option_after_the_unchanged_options"],
    ),
    Case(
        "apply jump host without a jump key is accepted",
        [Edit(GATED, "            [ -n \"$JUMP_KEY\" ] || fail \"ssh_jump_host is set but ssh_jump_key is empty: a jump needs both or neither.\"\n", "")],
        [f"{AJ}.test_only_one_of_the_two_secrets_fails_before_any_file_or_host_and_names_the_missing_one"],
    ),
    Case(
        "apply jump key without a jump host is accepted",
        [Edit(GATED, "            [ -n \"$JUMP_HOST\" ] || fail \"ssh_jump_key is set but ssh_jump_host is empty: a jump needs both or neither.\"\n", "")],
        [f"{AJ}.test_only_one_of_the_two_secrets_fails_before_any_file_or_host_and_names_the_missing_one"],
    ),
    Case(
        "apply jump host with a second line is accepted",
        [Edit(GATED, "            case \"$JUMP_HOST\" in *$'\\n'*) fail \"ssh_jump_host is not of the form user@host.\" ;; esac\n", "")],
        [f"{AJ}.test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed"],
    ),
    Case(
        "apply jump host form is not checked",
        [Edit(GATED, "printf '%s' \"$JUMP_HOST\" | grep -Eq \"$destination_pattern\"", "printf '%s' \"$JUMP_HOST\" | grep -Eq '.'")],
        [f"{AJ}.test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed"],
    ),
    Case(
        "apply jump host echoed in its refusal",
        [Edit(GATED, "fail \"ssh_jump_host is not of the form user@host.\"", "fail \"ssh_jump_host $JUMP_HOST is not of the form user@host.\"", 2)],
        [f"{AJ}.test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed"],
    ),
    Case(
        "apply destination pattern allows an underscore",
        [Edit(GATED, "@[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$'\n", "@[A-Za-z0-9]([A-Za-z0-9._-]{0,251}[A-Za-z0-9])?$'\n")],
        [
            f"{AJ}.test_the_stanza_the_destination_pattern_and_the_trap_in_the_apply_step_equal_the_actions",
            f"{AJ}.test_a_malformed_jump_host_fails_before_any_file_or_host_and_is_never_echoed",
        ],
    ),
    Case(
        "apply jump set when no jump is given",
        [Edit(GATED, "          if [ -n \"$JUMP_HOST\" ]; then\n            # ssh gives", "          if true; then\n            # ssh gives")],
        [
            f"{AJ}.test_with_no_jump_the_ssh_argv_the_files_and_the_removals_are_exactly_what_they_were",
            f"{SO}.test_the_plan_and_apply_calls_pass_the_same_exact_ssh_options",
        ],
    ),
    Case(
        "apply jump hop stanza drops ClearAllForwardings",
        [Edit(GATED, "\n                printf '  ClearAllForwardings yes\\n'", "")],
        [f"{AJ}.test_the_hop_has_its_own_strict_options_in_the_apply_too"],
    ),
    Case(
        "apply jump hop stanza drops IdentitiesOnly",
        [Edit(GATED, "\n                printf '  IdentitiesOnly yes\\n'", "")],
        [f"{AJ}.test_the_hop_has_its_own_strict_options_in_the_apply_too"],
    ),
    Case(
        "apply ssh error text reaches the job log",
        [Edit(GATED, '-- "$dest" "$SHA" < /dev/null 2> /dev/null)', '-- "$dest" "$SHA" < /dev/null)')],
        [f"{AJ}.test_the_jump_host_and_keys_reach_neither_the_log_nor_the_summary_of_a_successful_apply"],
    ),
    Case(
        "apply guard step reads the jump host",
        [Edit(GATED, "          PLANNED_DIGEST: ${{ needs.plan.outputs.hosts_digest }}\n", "          PLANNED_DIGEST: ${{ needs.plan.outputs.hosts_digest }}\n          JUMP_HOST: ${{ secrets.ssh_jump_host }}\n")],
        [
            f"{S}.test_the_apply_guard_step_and_the_steps_before_the_action_hold_no_jump_secret",
            f"{S}.test_the_jump_secrets_are_read_by_the_plan_jobs_action_step_and_the_apply_step_only",
            f"{S}.test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source",
        ],
    ),
    Case(
        "plan job does not pass the jump host",
        [Edit(GATED, "          jump-host: ${{ secrets.ssh_jump_host }}\n", "")],
        [f"{S}.test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"],
    ),
    Case(
        "plan job passes the jump key to the check key input",
        [Edit(GATED, "          check-key: ${{ secrets.ssh_check_key }}\n", "          check-key: ${{ secrets.ssh_jump_key }}\n")],
        [
            f"{S}.test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs",
            f"{S}.test_each_key_is_read_by_one_job_only",
        ],
    ),
    Case(
        "drift job does not pass the jump key",
        [Edit(DRIFT, "          jump-key: ${{ secrets.ssh_jump_key }}\n", "")],
        [f"{S}.test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"],
    ),
    Case(
        "drift jump secrets declared as required",
        [Edit(DRIFT, "          line goes in ssh_known_hosts.\n        required: false\n", "          line goes in ssh_known_hosts.\n        required: true\n")],
        [
            f"{S}.test_both_workflows_declare_the_two_jump_secrets_as_optional_and_say_both_or_neither",
            f"{S}.test_the_drift_workflow_takes_the_declared_inputs_and_three_required_secrets_and_no_apply_key",
        ],
    ),
    Case(
        "gated jump key secret not declared",
        [Edit(GATED, "      ssh_jump_key:\n", "      ssh_jump_key_unused:\n")],
        [f"{S}.test_both_workflows_declare_the_two_jump_secrets_as_optional_and_say_both_or_neither"],
    ),
    # The push route (README, "Pushed hosts"): each control of the sender, broken once.
    Case(
        "push: the route is ignored and a pulled host is judged as a pushed one",
        [Edit(ACTION, 'route=$(cat "$base.route")', "route=push")],
        [f"{PP}.test_a_pulled_host_that_answers_with_the_line_of_the_push_receiver_is_refused", f"{PP}.test_the_real_stand_ins_refuse_the_wrong_line_in_both_directions"],
    ),
    Case(
        "push: the route is ignored and a pushed host is judged as a pulled one",
        [Edit(ACTION, 'route=$(cat "$base.route")', "route=pull")],
        [f"{PP}.test_a_pushed_host_that_answers_with_the_line_of_the_pull_trigger_is_refused", f"{PP}.test_the_real_stand_ins_refuse_the_wrong_line_in_both_directions"],
    ),
    Case(
        "push: the route file is never written",
        [Edit(ACTION, 'printf \'%s\\n\' "$route" > "$plan_dir/$n.route"', ":")],
        [f"{PP}.test_a_clean_pushed_host_and_a_clean_pulled_host_give_no_changes", f"{PO}.test_with_no_push_list_the_plan_and_apply_calls_are_byte_for_byte_the_pull_calls_with_nothing_on_stdin"],
    ),
    Case(
        "push: the action accepts a push line for another mode",
        [Edit(ACTION, ' && [ "${BASH_REMATCH[4]}" = check ]; then', "; then")],
        [f"{PP}.test_a_push_line_for_another_mode_commit_or_exit_status_or_with_more_lines_is_refused"],
    ),
    Case(
        "push: the apply job accepts a push line for another mode",
        [Edit(GATED, ' && [ "${BASH_REMATCH[4]}" = apply ]; then', "; then")],
        [f"{PA}.test_a_push_line_for_the_check_mode_another_commit_or_exit_or_a_second_change_fails_the_job"],
    ),
    Case(
        "push: the apply job judges a pushed host by the pull trigger's line",
        [Edit(GATED, '              if [ "$route" = push ]; then\n                if [[ "$line" =~ $push_re ]]', '              if [ "$route" = never ]; then\n                if [[ "$line" =~ $push_re ]]')],
        [f"{PA}.test_a_pushed_host_that_answers_with_the_line_of_the_pull_trigger_is_refused"],
    ),
    Case(
        "push: the apply job judges a pulled host by the push receiver's line",
        [Edit(GATED, '              if [ "$route" = push ]; then\n                if [[ "$line" =~ $push_re ]]', '              if true; then\n                if [[ "$line" =~ $push_re ]]')],
        [f"{PA}.test_a_pulled_host_that_answers_with_the_line_of_the_push_receiver_is_refused"],
    ),
    Case(
        "push: the plan sends the pull form to a pushed host",
        [Edit(ACTION, '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"', '-- "$dest" "$SHA" < "$push_bundle"')],
        [f"{PP}.test_a_pushed_host_gets_check_the_commit_the_playbook_and_the_bundle_and_a_pulled_host_gets_the_commit_alone"],
    ),
    Case(
        "push: the plan sends apply to a pushed host",
        [Edit(ACTION, '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"', '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"')],
        [f"{PP}.test_the_plan_never_sends_apply_and_every_pushed_host_reads_the_same_bundle_from_its_start"],
    ),
    Case(
        "push: the plan sends no bundle to a pushed host",
        [Edit(ACTION, '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"', '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < /dev/null')],
        [f"{PP}.test_a_pushed_host_gets_check_the_commit_the_playbook_and_the_bundle_and_a_pulled_host_gets_the_commit_alone"],
    ),
    Case(
        "push: the apply job sends the pull form to a pushed host",
        [Edit(GATED, '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"', '-- "$dest" "$SHA" < "$push_bundle"')],
        [f"{PA}.test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run"],
    ),
    Case(
        "push: the apply job sends check to a pushed host",
        [Edit(GATED, '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"', '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"')],
        [f"{PA}.test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run"],
    ),
    Case(
        "push: the apply job sends no bundle to a pushed host",
        [Edit(GATED, '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"', '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < /dev/null')],
        [f"{PA}.test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run"],
    ),
    Case(
        "push: the bundle drops the history",
        [Edit(ACTION, 'bundle create "$file" HEAD', 'bundle create "$file" HEAD~1..HEAD')],
        [f"{PP}.test_a_pushed_host_gets_check_the_commit_the_playbook_and_the_bundle_and_a_pulled_host_gets_the_commit_alone", f"{PPar}.test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"],
    ),
    Case(
        "push: the apply job's bundle drops the history",
        [Edit(GATED, 'bundle create "$file" HEAD', 'bundle create "$file" HEAD~1..HEAD')],
        [f"{PA}.test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run", f"{PPar}.test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"],
    ),
    Case(
        "push: the action bundles a shallow checkout",
        [Edit(ACTION, '|| fail "The checkout of the repository is shallow, so its bundle would not carry the history the host needs."', "|| true")],
        [f"{PP}.test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked", f"{PPar}.test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"],
    ),
    Case(
        "push: the apply job bundles a shallow checkout",
        [Edit(GATED, '|| fail "The checkout of the repository is shallow, so its bundle would not carry the history the host needs."', "|| true")],
        [f"{PA}.test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked"],
    ),
    Case(
        "push: the action bundles a checkout that is not at the commit",
        [Edit(ACTION, '[ "$head" = "$sha" ] || fail "The checkout of the repository is not at the commit being delivered."', ":")],
        [f"{PP}.test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked"],
    ),
    Case(
        "push: the apply job bundles a checkout that is not at the commit",
        [Edit(GATED, '[ "$head" = "$sha" ] || fail "The checkout of the repository is not at the commit being delivered."', ":")],
        [f"{PA}.test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked"],
    ),
    Case(
        "push: the action pushes from a repository that is not public",
        [Edit(ACTION, '[ "$PUSH_REPO_PRIVATE" = false ] || fail "Pushing refuses unless the repository is public: a bundle carries its whole history to the host."', ":")],
        [f"{PV}.test_a_repository_that_is_not_known_to_be_public_is_refused"],
    ),
    Case(
        "push: the apply guard pushes from a repository that is not public",
        [Edit(GATED, '[ "$PUSH_REPO_PRIVATE" = false ] || fail "Pushing refuses unless the repository is public: a bundle carries its whole history to the host."', ":")],
        [f"{PAG}.test_the_push_playbook_and_the_public_repository_are_checked_again_in_the_apply_job"],
    ),
    Case(
        "push: the action takes a push host that is not a listed host",
        [Edit(ACTION, 'grep -qxF -- "$dest" <<< "$HOSTS" || fail "A push host is not in the hosts list."', ":")],
        [f"{PV}.test_a_push_host_that_is_not_a_listed_host_a_duplicate_or_a_malformed_line_is_refused"],
    ),
    Case(
        "push: the action takes a push host listed twice",
        [Edit(ACTION, 'case " $push_list " in *" $dest "*) fail "A push host is listed twice." ;; esac', ":")],
        [f"{PV}.test_a_push_host_that_is_not_a_listed_host_a_duplicate_or_a_malformed_line_is_refused"],
    ),
    Case(
        "push: the apply guard does not compare the push list with the plan",
        [Edit(GATED, '[ "$push_digest" = "$PLANNED_PUSH_DIGEST" ] || fail "The pushed hosts are not the pushed hosts that were planned."', ":")],
        [f"{PAG}.test_a_push_list_the_plan_did_not_have_a_list_the_plan_had_and_a_different_list_are_refused"],
    ),
    Case(
        "push: the apply guard takes an empty planned push digest for a push list",
        [Edit(GATED, "printf '%s' \"$PLANNED_PUSH_DIGEST\" | grep -Eq '^[0-9a-f]{64}$' || fail \"The planned push digest is missing.\"", ":")],
        [f"{PAG}.test_a_push_list_with_no_host_in_it_is_refused_whatever_the_plan_said"],
    ),
    Case(
        "push: the push digest is joined with a comma in the action",
        [Edit(ACTION, 'push_list="${push_list:+$push_list }$dest"', 'push_list="${push_list:+$push_list,}$dest"')],
        [f"{PAG}.test_the_push_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes"],
    ),
    Case(
        "push: the push digest is joined with a comma in the apply guard",
        [Edit(GATED, 'push_list="${push_list:+$push_list }$dest"', 'push_list="${push_list:+$push_list,}$dest"')],
        [f"{PAG}.test_the_push_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes"],
    ),
    Case(
        "push: the action does not require the jump host for a push",
        [Edit(ACTION, '{ [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "push-hosts needs jump-host and jump-key: a pushed host is reached only through the jump host."', ":")],
        [f"{PV}.test_a_push_needs_the_jump_host_and_its_key"],
    ),
    Case(
        "push: the apply job does not require the jump host for a push",
        [Edit(GATED, '{ [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "ssh_push_hosts needs ssh_jump_host and ssh_jump_key: a pushed host is reached only through the jump host."', ":")],
        [f"{PA}.test_a_push_needs_the_jump_host_and_its_key_and_asks_no_host_without_them"],
    ),
    Case(
        "push: the action takes a playbook with a parent directory in it",
        [Edit(ACTION, "case \"$PUSH_PLAYBOOK\" in *..*) fail \"push-playbook must not contain '..'.\" ;; esac", ":")],
        [f"{PV}.test_the_push_playbook_must_be_a_relative_path_of_plain_characters_with_no_dotdot"],
    ),
    Case(
        "push: the action takes any playbook text",
        [Edit(ACTION, '[[ "$PUSH_PLAYBOOK" =~ $playbook_re ]] || fail "push-playbook must be a relative path of letters, digits, dot, underscore, dash and slash."', ":")],
        [f"{PV}.test_the_push_playbook_must_be_a_relative_path_of_plain_characters_with_no_dotdot"],
    ),
    Case(
        "push: the apply guard takes a playbook with a parent directory in it",
        [Edit(GATED, "case \"$PUSH_PLAYBOOK\" in *..*) fail \"push-playbook must not contain '..'.\" ;; esac", ":")],
        [f"{PAG}.test_the_push_playbook_and_the_public_repository_are_checked_again_in_the_apply_job"],
    ),
    Case(
        "push: the apply guard takes any playbook text",
        [Edit(GATED, '[[ "$PUSH_PLAYBOOK" =~ $playbook_re ]] || fail "push-playbook must be a relative path of letters, digits, dot, underscore, dash and slash."', ":")],
        [f"{PAG}.test_the_push_playbook_and_the_public_repository_are_checked_again_in_the_apply_job"],
    ),
    Case(
        "push: the push decision of the plan job reads no push list",
        [Edit(GATED, "        env:\n          PUSH_HOSTS: ${{ secrets.ssh_push_hosts }}\n        run: |", "        env:\n          PUSH_HOSTS: ''\n        run: |")],
        [f"{S}.test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source"],
    ),
    Case(
        "push: the plan job checks out the whole history on every run",
        [Edit(GATED, "        if: steps.push.outputs.push == 'true'\n", "        if: always()\n")],
        [f"{S}.test_the_drift_job_checks_out_the_caller_only_for_a_push_and_the_plan_job_only_in_a_dry_run_or_for_a_push", f"{PO}.test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it"],
    ),
    Case(
        "push: the apply job checks out the whole history on every run",
        [Edit(GATED, "        if: steps.guard.outputs.push == 'true'\n", "        if: always()\n")],
        [f"{S}.test_the_apply_job_steps_are_the_guard_then_the_apply_with_no_if_but_the_push_checkout", f"{PO}.test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it"],
    ),
    Case(
        "push: the drift job checks out the whole history on every run",
        [Edit(DRIFT, "        if: steps.push.outputs.push == 'true'\n", "        if: always()\n")],
        [f"{S}.test_the_drift_job_checks_out_the_caller_only_for_a_push_and_the_plan_job_only_in_a_dry_run_or_for_a_push", f"{PO}.test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it"],
    ),
    Case(
        "push: the push checkout is shallow",
        [Edit(DRIFT, "          fetch-depth: 0\n", "          fetch-depth: 1\n")],
        [f"{S}.test_the_drift_job_checks_out_the_caller_only_for_a_push_and_the_plan_job_only_in_a_dry_run_or_for_a_push"],
    ),
    Case(
        "push: the drift job does not pass the push list",
        [Edit(DRIFT, "          push-hosts: ${{ secrets.ssh_push_hosts }}\n", "")],
        [f"{S}.test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"],
    ),
    Case(
        "push: the plan job does not pass the public-repository value",
        [Edit(GATED, "          push-repo-private: ${{ github.event.repository.private }}\n", "")],
        [f"{S}.test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"],
    ),
    Case(
        "push: the plan job's push digest is read from the wrong step",
        [Edit(GATED, "push_hosts_digest: ${{ steps.ssh.outputs.push-hosts-digest }}", "push_hosts_digest: ${{ steps.guard.outputs.push-hosts-digest }}")],
        [f"{S}.test_the_plan_job_outputs_come_from_the_steps_that_make_them"],
    ),
    Case(
        "push: the push receiver's line differs in the apply job",
        [Edit(GATED, "push_re='^push-receiver verdict=([a-z-]+) exit=([0-9]+) reason=([a-z-]+) mode=(check|apply)", "push_re='^push-receiver verdict=([a-z-]*) exit=([0-9]+) reason=([a-z-]+) mode=(check|apply)")],
        [f"{PPar}.test_the_push_line_is_the_contract_and_the_same_in_the_action_and_in_the_apply_job"],
    ),
    Case(
        "push: the old pull path gains a step that runs without a push list",
        [Edit(GATED, "      - name: Decide whether any host is pushed to\n        id: push\n", "      - name: Decide whether any host is pushed to\n        id: push\n        if: always()\n")],
        [f"{PO}.test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it"],
    ),
    # Issue 73: the report step prints each non-clean host's reason to the log, and nothing of the diff.
    Case(
        "log line: the diff is printed in the host's line",
        [Edit(ACTION, 'scan-hits=${hits:-none}; playbook=$playbook")"', 'scan-hits=${hits:-none}; playbook=$playbook; diff=$(cat "$cleaned")")"')],
        [f"{RL}.test_a_planted_secret_in_a_diff_never_reaches_the_log"],
    ),
    Case(
        "log line: the text a pattern matched is printed with the pattern name",
        [Edit(ACTION, 'hits="${hits:+$hits, }$name"\n', 'hits="${hits:+$hits, }$name $(grep -Eio -e "$regex" "$cleaned" | head -n 1)"\n')],
        [f"{RL}.test_a_planted_secret_in_a_diff_never_reaches_the_log", f"{RL}.test_a_withheld_diff_prints_the_pattern_name_and_never_the_text_it_matched"],
    ),
    Case(
        "log line: the destination is printed in the host's line",
        [Edit(ACTION, 'scan-hits=${hits:-none}; playbook=$playbook")"', 'scan-hits=${hits:-none}; playbook=$playbook; dest=$dest")"')],
        [f"{RL}.test_a_planted_secret_in_a_diff_never_reaches_the_log"],
    ),
    Case(
        "log line: what the host wrote to standard error is printed in the host's line",
        [Edit(ACTION, 'scan-hits=${hits:-none}; playbook=$playbook")"', 'scan-hits=${hits:-none}; playbook=$playbook; err=$(head -c 300 "$base.err")")"')],
        [f"{RL}.test_a_planted_secret_in_a_diff_never_reaches_the_log", f"{RL}.test_an_unreachable_host_prints_its_kind_and_not_its_name_or_its_error_text"],
    ),
    Case(
        "log line: the allow-list of characters is dropped",
        [Edit(ACTION, "printf '%s' \"$1\" | tr -c 'A-Za-z0-9 _./:;,()=-' '?'", "printf '%s' \"$1\"")],
        [f"{RL}.test_a_value_that_would_start_a_workflow_command_is_defanged"],
    ),
    Case(
        "log line: the error line of a non-clean host is not printed",
        [Edit(ACTION, 'echo "::$level::$(plain "$label: kind=$kind; note=${note:--}; scan-hits=${hits:-none}; playbook=$playbook")"', ":")],
        [f"{RL}.test_an_unreachable_host_prints_its_kind_and_not_its_name_or_its_error_text", f"{RL}.test_a_clean_host_prints_no_line_and_each_other_host_prints_exactly_one"],
    ),
    Case(
        "log line: a host that would change is an error in a plan",
        [Edit(ACTION, "              level=notice\n", "              level=error\n")],
        [f"{RL}.test_a_host_that_would_change_is_a_notice_in_a_plan_and_an_error_in_a_drift_check"],
    ),
    Case(
        "log line: the failed plan headline is reverted",
        [Edit(ACTION, 'first="Plan failed: $not_clean of $total hosts not clean ($detail), so no', 'first="Plan failed: $failed of $total host(s) gave no usable answer, so no')],
        [f"{RL}.test_the_failed_plan_headline_counts_the_hosts_that_are_not_clean_by_kind"],
    ),
    Case(
        "log line: the drift headline is reverted",
        [Edit(ACTION, 'first="Not clean at commit $SHA: $not_clean of $total hosts not clean ($detail)."', 'first="Not clean at commit $SHA: $detail."')],
        [f"{RL}.test_the_drift_headline_counts_the_hosts_that_are_not_clean_by_kind"],
    ),
    Case(
        "log line: the playbook is not read from the answer",
        [Edit(ACTION, '              playbook="${line##* playbook=}"\n              playbook="${playbook%% *}"\n', '              playbook="-"\n')],
        [f"{RL}.test_an_answer_for_another_commit_shows_the_playbook_it_named"],
    ),
    Case(
        "log line: a call stopped at its limit gets the generic note",
        [Edit(ACTION, 'note="the call was stopped at its limit (exit 124)"', 'note="the host did not answer with one summary line (exit $rc)"')],
        [f"{RL}.test_a_call_stopped_at_its_limit_is_a_no_answer_that_says_so"],
    ),
    Case(
        "log line: the table is not printed to the log",
        [Edit(ACTION, "        printf '| Host | Result | Note |\\n| --- | --- | --- |\\n'\n        cat \"$rows\"\n\n        if [ \"$failed\" -gt 0 ]; then", "        if [ \"$failed\" -gt 0 ]; then")],
        [f"{RL}.test_the_table_is_on_the_log_and_the_diff_is_not"],
    ),
    Case(
        "log line: the second hop's name is not redacted from a host's line",
        [Edit(ACTION, '            playbook=$(redact_jumps "$playbook")\n', "")],
        [f"{RL}.test_a_second_hop_name_in_a_host_line_is_redacted_as_it_is_from_the_diff"],
    ),
    # The second hop. The control is that the second hop applies to the
    # listed hosts and only to them; the rest hold its validation, its keys and its
    # silence. The widened and dropped cases are the pair the ticket names.
    Case(
        "second hop: the chain is applied to every host",
        [Edit(ACTION, '          if [ -n "$JUMP2_HOSTS" ] && grep -qxF -- "$dest" <<< "$JUMP2_HOSTS"; then', '          if [ -n "$JUMP2_HOSTS" ]; then')],
        [f"{SJ2}.test_only_listed_hosts_get_the_two_hop_chain"],
    ),
    Case(
        "second hop: the chain is dropped for the listed hosts",
        [Edit(ACTION, "=ProxyJump=jump_hop,jump_hop2", "=ProxyJump=jump_hop")],
        [f"{SJ2}.test_a_listed_host_is_called_through_both_hops"],
    ),
    Case(
        "second hop: a listed host is called without the second hop when none is set",
        [Edit(ACTION, '        if [ "$DRY_RUN" != true ] && [ -n "$JUMP2_HOSTS" ] && [ "${#ssh_args_two[@]}" -eq 0 ]; then\n          fail "jump2-hosts is set but no second hop is configured."\n        fi\n', "")],
        [f"{SJ2}.test_a_second_hop_listed_with_no_hop_set_fails_closed_even_if_validation_were_skipped"],
    ),
    Case(
        "second hop: a host with no key is accepted",
        [Edit(ACTION, '          [ -n "$JUMP2_KEY" ] || fail "jump2-host is set but jump2-key is empty: a second hop needs both or neither."\n', "")],
        [f"{SJ2}.test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value"],
    ),
    Case(
        "second hop: a key with no host is accepted",
        [Edit(ACTION, '          [ -n "$JUMP2_HOST" ] || fail "jump2-key is set but jump2-host is empty: a second hop needs both or neither."\n', "")],
        [f"{SJ2}.test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value"],
    ),
    Case(
        "second hop: a second hop without the first hop is accepted",
        [Edit(ACTION, '          { [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "A second hop needs jump-host and jump-key: it is reached through the first hop."\n', "")],
        [f"{SJ2}.test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value"],
    ),
    Case(
        "second hop: a list with no second hop is accepted",
        [
            Edit(
                ACTION,
                '          if [ -z "$JUMP2_HOST" ] && [ -z "$JUMP2_KEY" ]; then\n            fail "jump2-hosts is set but there is no second hop (jump2-host and jump2-key are empty): a listed host would fall back to one hop."\n          fi\n',
                "",
            )
        ],
        [f"{SJ2}.test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value"],
    ),
    Case(
        "second hop: the second hop's own address is accepted as a target",
        [
            Edit(
                ACTION,
                '            [ "$(printf \'%s\' "${dest#*@}" | tr \'[:upper:]\' \'[:lower:]\')" != "$jump2_gateway" ] \\\n              || fail "A line of jump2-hosts is the address of the second hop itself: that host is reached through the first hop alone."\n',
                "",
            )
        ],
        [f"{SJ2}.test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value"],
    ),
    Case(
        "second hop: the first hop's key is accepted as the second hop's",
        [Edit(ACTION, '          [ "$JUMP2_KEY" != "$JUMP_KEY" ] || fail "jump2-key is the same as jump-key: each hop has its own key."\n', "")],
        [f"{SJ2}.test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value"],
    ),
    Case(
        "second hop: the check key is accepted as the second hop's",
        [Edit(ACTION, '          [ "$JUMP2_KEY" != "$SSH_KEY" ] || fail "jump2-key is the same as this job\'s own key: the second hop has a key of its own."\n', "")],
        [f"{SJ2}.test_the_check_key_is_refused_as_the_second_hop_key_before_any_file_or_host"],
    ),
    Case(
        "second hop: the apply key is accepted as the second hop's",
        [Edit(GATED, '            [ "$JUMP2_KEY" != "$SSH_KEY" ] || fail "ssh_jump2_key is the same as this job\'s own key: the second hop has a key of its own."\n', "")],
        [f"{AJ}.test_the_apply_key_is_refused_as_the_second_hop_key_before_any_file_or_host"],
    ),
    Case(
        "second hop: a listed host outside the hosts list is accepted",
        [Edit(ACTION, '            grep -qxF -- "$dest" <<< "$HOSTS" || fail "A line of jump2-hosts is not in the hosts list."\n', "")],
        [f"{SJ2}.test_each_refusal_of_the_second_hop_names_what_is_wrong_and_echoes_no_value"],
    ),
    Case(
        "second hop: the second key and the config are written with the runner umask",
        [Edit(ACTION, "                umask 077\n                printf '%s\\n' \"$JUMP2_KEY\"", "                printf '%s\\n' \"$JUMP2_KEY\"")],
        [f"{SJ2}.test_the_second_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out"],
    ),
    Case(
        "second hop: the trap leaves the second key behind",
        [Edit(ACTION, '              trap \'rm -f "$plan_dir/id" "$plan_dir/known_hosts" "$plan_dir"/id.* "$plan_dir/jump_id" "$plan_dir/jump_config" "$plan_dir/jump2_id"\' EXIT\n', "")],
        [f"{SJ2}.test_the_second_key_and_the_config_are_private_while_ssh_runs_and_removed_after_every_way_out"],
    ),
    Case(
        "second hop: the second hop's name is left in the text of a host",
        [Edit(ACTION, '                    if (jd2 != "") { s = mark(mark(s, jd2, "\\003"), jh2, "\\003") }\n', "")],
        [f"{SJ2}.test_the_second_hops_name_is_replaced_in_a_host_text_that_reaches_the_summary"],
    ),
    Case(
        "second hop: the second stanza drops IdentitiesOnly",
        [Edit(ACTION, "\"$plan_dir/jump2_id\"\n                  printf '  IdentitiesOnly yes\\n'\n", "\"$plan_dir/jump2_id\"\n")],
        [f"{SJ2}.test_the_second_hop_has_its_own_strict_options_and_its_own_key_file"],
    ),
    Case(
        "second hop: the second stanza uses the first hop's key file",
        [Edit(ACTION, "printf '  IdentityFile %s\\n' \"$plan_dir/jump2_id\"", "printf '  IdentityFile %s\\n' \"$plan_dir/jump_id\"")],
        [f"{SJ2}.test_the_second_hop_has_its_own_strict_options_and_its_own_key_file"],
    ),
    Case(
        "second hop: the second stanza reads the global known hosts",
        [Edit(ACTION, "                  printf '  GlobalKnownHostsFile /dev/null\\n'\n                  printf '  ConnectTimeout 30\\n'\n                  printf '  ServerAliveInterval 30\\n'\n                  printf '  ForwardAgent no\\n'\n                  printf '  ClearAllForwardings yes\\n'\n                } >> ", "                  printf '  ConnectTimeout 30\\n'\n                  printf '  ServerAliveInterval 30\\n'\n                  printf '  ForwardAgent no\\n'\n                  printf '  ClearAllForwardings yes\\n'\n                } >> ")],
        [f"{SJ2}.test_the_second_hop_has_its_own_strict_options_and_its_own_key_file"],
    ),
    Case(
        "second hop: the drift check passes no second-hop list",
        [Edit(DRIFT, "          jump2-hosts: ${{ secrets.ssh_jump2_hosts }}\n", "")],
        [f"{S}.test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"],
    ),
    Case(
        "second hop: the apply digest check is removed",
        [Edit(GATED, '          [ "$jump2_digest" = "$PLANNED_JUMP2_DIGEST" ] || fail "The second-hop hosts are not the second-hop hosts that were planned."\n', "")],
        [f"{JAG}.test_a_list_the_plan_did_not_have_a_list_the_plan_had_and_a_different_list_are_refused"],
    ),
    Case(
        "second hop: the apply guard accepts a missing planned digest",
        [Edit(GATED, "            printf '%s' \"$PLANNED_JUMP2_DIGEST\" | grep -Eq '^[0-9a-f]{64}$' || fail \"The planned second-hop digest is missing.\"\n", "")],
        [f"{JAG}.test_a_list_with_no_host_in_it_is_refused_whatever_the_plan_said"],
    ),
    Case(
        "second hop: the plan job drops the second-hop digest",
        [Edit(GATED, "      jump2_hosts_digest: ${{ steps.ssh.outputs.jump2-hosts-digest }}\n", "")],
        [f"{S}.test_the_plan_job_outputs_come_from_the_steps_that_make_them"],
    ),
    Case(
        "second hop: the apply chain is applied to every host",
        [Edit(GATED, '            if [ -n "$JUMP2_HOSTS" ] && grep -qxF -- "$dest" <<< "$JUMP2_HOSTS"; then', '            if [ -n "$JUMP2_HOSTS" ]; then')],
        [f"{AJ2}.test_a_listed_host_is_called_through_both_hops_and_every_other_host_through_the_first_alone"],
    ),
    Case(
        "second hop: the action's one-call function drifts from the apply step's",
        [Edit(ACTION, '            args=("${ssh_args_two[@]}")\n', '            args=("${ssh_args[@]}")\n')],
        [f"{JPar}.test_the_one_call_function_is_the_same_text_in_the_action_and_in_the_apply_step"],
    ),
    Case(
        "second hop: the apply chain is dropped for the listed hosts",
        [Edit(GATED, "=ProxyJump=jump_hop,jump_hop2", "=ProxyJump=jump_hop")],
        [f"{AJ2}.test_a_listed_host_is_called_through_both_hops_and_every_other_host_through_the_first_alone"],
    ),
    Case(
        "second hop: the apply accepts a host with no key",
        [Edit(GATED, '            [ -n "$JUMP2_KEY" ] || fail "ssh_jump2_host is set but ssh_jump2_key is empty: a second hop needs both or neither."\n', "")],
        [f"{AJ2}.test_each_refusal_of_the_second_hop_comes_before_any_file_or_host_and_echoes_no_value"],
    ),
    Case(
        "second hop: the apply accepts a second hop without the first hop",
        [Edit(GATED, '            { [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "A second hop needs ssh_jump_host and ssh_jump_key: it is reached through the first hop."\n', "")],
        [f"{AJ2}.test_each_refusal_of_the_second_hop_comes_before_any_file_or_host_and_echoes_no_value"],
    ),
    Case(
        "second hop: the apply accepts a self-jump",
        [
            Edit(
                GATED,
                '              [ "$(printf \'%s\' "${dest#*@}" | tr \'[:upper:]\' \'[:lower:]\')" != "$jump2_gateway" ] \\\n                || fail "A line of ssh_jump2_hosts is the address of the second hop itself: that host is reached through the first hop alone."\n',
                "",
            )
        ],
        [f"{AJ2}.test_each_refusal_of_the_second_hop_comes_before_any_file_or_host_and_echoes_no_value"],
    ),
    Case(
        "second hop: the apply writes the second key with the runner umask",
        [Edit(GATED, "                umask 077\n                printf '%s\\n' \"$JUMP2_KEY\"", "                printf '%s\\n' \"$JUMP2_KEY\"")],
        [f"{AJ2}.test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out"],
    ),
    Case(
        "second hop: the apply trap leaves the second key behind",
        [Edit(GATED, '              trap \'rm -f "$work/id" "$work/known_hosts" "$work"/id.* "$work/jump_id" "$work/jump_config" "$work/jump2_id"\' EXIT\n', "")],
        [f"{AJ2}.test_the_keys_and_the_config_are_private_while_ssh_runs_and_removed_on_every_way_out"],
    ),
    Case(
        "second hop: the apply second stanza drops IdentitiesOnly",
        [Edit(GATED, "\"$work/jump2_id\"\n                  printf '  IdentitiesOnly yes\\n'\n", "\"$work/jump2_id\"\n")],
        [f"{AJ2}.test_the_config_file_is_the_first_stanza_then_one_stanza_for_the_second_hop"],
    ),
    Case(
        "second hop: the apply second stanza uses the first hop's key file",
        [Edit(GATED, "printf '  IdentityFile %s\\n' \"$work/jump2_id\"", "printf '  IdentityFile %s\\n' \"$work/jump_id\"")],
        [f"{AJ2}.test_each_key_is_offered_to_its_own_hop_only_and_none_reaches_an_argv"],
    ),
    # The credential-assignment pattern reads a copy of the diff with four non-credential shapes taken out.
    # First the fix reverted and the pattern broken to match nothing, then each rule dropped (a harmless line
    # is withheld again) and each rule widened (a credential beside the shape is let through).
    Case(
        "scan: the credential-assignment pattern reads the diff whole again",
        [Edit(ACTION, 'target="$scanned"', 'target="$cleaned"')],
        [f"{P}.test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job"],
    ),
    Case(
        "scan: the credential-assignment pattern matches nothing",
        [
            Edit(
                ACTION,
                "'credential-assignment|(passw(or)?d|passphrase|secret|token|api[_-]?key|private[_-]?key|credential)"
                '[A-Za-z0-9_.-]*"?[[:space:]]*[:=][[:space:]]*"?[^[:space:]"]{4,}\'',
                "'credential-assignment|x^x'",
            )
        ],
        [
            f"{P}.test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line",
            f"{P}.test_a_secret_looking_string_in_the_diff_is_withheld_and_fails_the_job",
            f"{RL}.test_a_withheld_diff_prints_the_pattern_name_and_never_the_text_it_matched",
        ],
    ),
    Case(
        "scan: every pattern reads the copy with the shapes taken out",
        [Edit(ACTION, 'if [ "$name" = credential-assignment ]; then', "if true; then")],
        [f"{P}.test_the_other_patterns_read_the_diff_whole_even_beside_a_shape_the_assignment_pattern_lets_through"],
    ),
    Case(
        "scan: the sudoers tag is no longer taken out",
        [Edit(ACTION, "              -e 's/(^|[[:space:])])nopasswd:/\\1/g' \\\n", "")],
        [f"{P}.test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job"],
    ),
    Case(
        "scan: a ${NAME} reference is no longer taken out",
        [Edit(ACTION, "              -e 's/\\$\\{[a-z_][a-z0-9_]*(:?\\?[^}]*)?\\}//g' \\\n", "")],
        [f"{P}.test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job"],
    ),
    Case(
        "scan: a $(cat PATH) reference is no longer taken out",
        [Edit(ACTION, "              -e 's/\\$\\(cat [^[:space:])]+\\)//g' \\\n", "")],
        [f"{P}.test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job"],
    ),
    Case(
        "scan: a key that names a file or a path is no longer taken out",
        [
            Edit(
                ACTION,
                "              -e 's/[a-z0-9_.-]*[_.-](file|filename|path|dir)\"?[[:space:]]*[:=]([[:space:]]*\"?[/~])/\\1/g' \\\n",
                "",
            )
        ],
        [f"{P}.test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job"],
    ),
    Case(
        "scan: a true, false, null or none flag is no longer taken out",
        [
            Edit(
                ACTION,
                "              -e 's/[:=][[:space:]]*\"?(true|false|null|none)\"?[[:space:]]*([],}]|$)/ \\2/g' \\\n",
                "",
            )
        ],
        [f"{P}.test_a_line_that_only_looks_like_a_credential_assignment_is_shown_and_does_not_fail_the_job"],
    ),
    Case(
        "scan: the sudoers tag takes the rest of its line with it",
        [Edit(ACTION, "-e 's/(^|[[:space:])])nopasswd:/\\1/g'", "-e 's/(^|[[:space:])])nopasswd:.*/\\1/g'")],
        [f"{P}.test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line"],
    ),
    Case(
        "scan: a ${NAME} reference takes the rest of its line with it",
        [
            Edit(
                ACTION,
                "-e 's/\\$\\{[a-z_][a-z0-9_]*(:?\\?[^}]*)?\\}//g'",
                "-e 's/\\$\\{[a-z_][a-z0-9_]*(:?\\?[^}]*)?\\}.*//g'",
            )
        ],
        [f"{P}.test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line"],
    ),
    Case(
        "scan: a key that names a file takes the rest of its line with it",
        [Edit(ACTION, "[:=]([[:space:]]*\"?[/~])/\\1/g'", "[:=].*/\\1/g'")],
        [f"{P}.test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line"],
    ),
    Case(
        "scan: a key that only ends like a file, with any value, is taken out",
        [
            Edit(
                ACTION,
                "[a-z0-9_.-]*[_.-](file|filename|path|dir)\"?[[:space:]]*[:=]([[:space:]]*\"?[/~])/\\1/g'",
                "[a-z0-9_.-]*(file|filename|path|dir)\"?[[:space:]]*[:=]/\\1/g'",
            )
        ],
        [f"{P}.test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line"],
    ),
    Case(
        "scan: a value that only starts like a flag is taken out",
        [Edit(ACTION, '"?[[:space:]]*([],}]|$)/ \\2/g\'', '"?()/ \\2/g\'')],
        [f"{P}.test_a_credential_assignment_is_withheld_whatever_else_is_on_its_line"],
    ),
    Case(
        "scan: the ${...} rule takes out a default again (the old broad rule)",
        [Edit(ACTION, "-e 's/\\$\\{[a-z_][a-z0-9_]*(:?\\?[^}]*)?\\}//g'", "-e 's/\\$\\{[^}]*\\}//g'")],
        [f"{P}.test_a_secret_in_a_default_or_a_command_is_withheld_and_never_printed"],
    ),
    Case(
        "scan: the $(...) rule takes out a command again (the old broad rule)",
        [Edit(ACTION, "-e 's/\\$\\(cat [^[:space:])]+\\)//g'", "-e 's/\\$\\([^)]*\\)//g'")],
        [f"{P}.test_a_secret_in_a_default_or_a_command_is_withheld_and_never_printed"],
    ),
    Case(
        "scan: the flag rule accepts any punctuation after the flag word",
        [Edit(ACTION, "-e 's/[:=][[:space:]]*\"?(true|false|null|none)\"?[[:space:]]*([],}]|$)/ \\2/g'", "-e 's/[:=][[:space:]]*\"?(true|false|null|none)\"?([^a-z0-9_]|$)/ \\2/g'")],
        [f"{P}.test_a_secret_beside_a_flag_word_a_file_key_or_a_sudoers_tag_is_withheld_and_never_printed"],
    ),
    Case(
        "apply job group cancels a running apply",
        [Edit(GATED, "      group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}\n      cancel-in-progress: false", "      group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}\n      cancel-in-progress: true")],
        [f"{CC}.test_the_apply_job_has_its_own_group_that_never_cancels", f"{CC}.test_no_job_that_holds_a_key_or_an_environment_or_applies_can_be_cancelled_by_a_group"],
    ),
    Case(
        "apply job group is shared with the plan job",
        [Edit(GATED, "      group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}\n", "      group: ansible-plan-${{ github.repository }}\n")],
        [f"{CC}.test_the_apply_job_has_its_own_group_that_never_cancels", f"{CC}.test_no_other_job_shares_the_apply_group"],
    ),
    Case(
        "apply job group dropped",
        [Edit(GATED, "    concurrency:\n      group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}\n      cancel-in-progress: false\n", "")],
        [f"{CC}.test_the_apply_job_has_its_own_group_that_never_cancels"],
    ),
    Case(
        "Molecule legs share one group",
        [Edit(GATED, "-${{ inputs.environment }}-${{ matrix.name }}-${{ github.event_name", "-${{ inputs.environment }}-${{ github.event_name")],
        [f"{CC}.test_a_molecule_leg_has_a_group_of_its_own_so_the_legs_do_not_cancel_each_other"],
    ),
    Case(
        "plan job cancels a running plan",
        [Edit(GATED, "      group: ansible-plan-${{ github.repository }}\n      cancel-in-progress: false", "      group: ansible-plan-${{ github.repository }}\n      cancel-in-progress: true")],
        [f"{CC}.test_plan_and_the_drift_check_share_one_group_that_never_cancels", f"{CC}.test_no_job_that_holds_a_key_or_an_environment_or_applies_can_be_cancelled_by_a_group"],
    ),
    Case(
        "drift job leaves the plan group",
        [Edit(DRIFT, "      group: ansible-plan-${{ github.repository }}\n", "      group: ansible-drift-${{ github.repository }}\n")],
        [f"{CC}.test_plan_and_the_drift_check_share_one_group_that_never_cancels"],
    ),
    Case(
        "a workflow-level group is added",
        [Edit(GATED, "permissions:\n  contents: read\n\njobs:", "permissions:\n  contents: read\n\nconcurrency:\n  group: ansible-${{ github.repository }}\n  cancel-in-progress: true\n\njobs:")],
        [f"{CC}.test_neither_workflow_has_a_workflow_level_group"],
    ),
    Case(
        "lint stops cancelling a superseded run",
        [Edit(GATED, "-${{ inputs.environment }}-${{ github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork != true && 'delivery' || format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n      cancel-in-progress: true\n    defaults:", "-${{ inputs.environment }}-${{ github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork != true && 'delivery' || format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n      cancel-in-progress: false\n    defaults:")],
        [f"{CC}.test_only_discover_lint_and_shard_cancel_and_each_has_the_group_pinned_here"],
    ),
    Case(
        "a pull request run shares the delivery group of discover",
        [
            Edit(
                GATED,
                "ansible-ci-discover-${{ github.repository }}-${{ inputs.environment }}-${{ github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork != true && 'delivery' || format('run-{0}-{1}', github.run_id, github.run_attempt) }}",
                "ansible-ci-discover-${{ github.repository }}-${{ inputs.environment }}-delivery",
            )
        ],
        [f"{CC}.test_only_discover_lint_and_shard_cancel_and_each_has_the_group_pinned_here"],
    ),
    Case(
        "plan runs for a superseded run (the old condition)",
        [Edit(GATED, "      ${{ !cancelled()\n      && needs.discover.result == 'success'\n      && (needs.lint.result == 'success' || needs.lint.result == 'skipped')\n      && (needs.shard.result == 'success' || needs.shard.result == 'skipped')\n      && needs.discover.outputs.delivery == 'true' }}", "      ${{ !cancelled() && !failure() && needs.discover.outputs.delivery == 'true' }}")],
        [f"{S}.test_the_plan_job_runs_only_for_delivery_and_only_after_every_need_succeeded_or_was_skipped"],
    ),
]

PHK = "PerHostKeyTests"
HKV = "HostKeyValidateTests"
AHK = "ApplyHostKeyTests"
HKP = "HostKeysParityTests"
HKS = "HostKeySecretScopeTests"


def host_key_cases() -> list[Case]:
    """Per-host keys: the same break is made in the action (the plan side) and in the apply step, each with its own red test.

    A table of near-identical cases is built here rather than written out twice. Each edit
    is a fragment found exactly once in its file, so a change to the text makes the case fail
    to apply, not pass.
    """
    cases: list[Case] = []

    def both(name: str, action: Edit | None, apply: Edit | None, plan_red: list[str], apply_red: list[str]) -> None:
        if action is not None:
            cases.append(Case(f"host keys, plan side: {name}", [action], plan_red))
        if apply is not None:
            cases.append(Case(f"host keys, apply side: {name}", [apply], apply_red))

    ONE = f"{PHK}.test_a_host_with_an_entry_gets_exactly_its_own_key_and_the_other_host_the_shared_key"
    HEAD = f"{PHK}.test_a_wrong_key_for_demo1_fails_the_plan_for_demo1_alone"
    A_ONE = f"{AHK}.test_a_host_with_an_entry_gets_its_own_key_on_both_runs_and_the_other_host_the_shared_key"

    # The selection of a host's key.
    both(
        "the selection ignores the map, so every host gets the shared key",
        Edit(ACTION, 'if [ -e "$plan_dir/id.$n" ]; then', "if false; then"),
        Edit(GATED, 'if [ -e "$work/id.$(printf \'%02d\' "$num")" ]; then', "if false; then"),
        [ONE, HEAD],
        [A_ONE],
    )
    both(
        "the file of a host is numbered one too high (an off-by-one on the host index)",
        Edit(ACTION, 'host_key_file="$plan_dir/id.$(printf \'%02d\' "$host_key_index")"', 'host_key_file="$plan_dir/id.$(printf \'%02d\' "$((host_key_index + 1))")"'),
        Edit(GATED, 'host_key_file="$work/id.$(printf \'%02d\' "$host_key_index")"', 'host_key_file="$work/id.$(printf \'%02d\' "$((host_key_index + 1))")"'),
        [ONE, HEAD],
        [A_ONE],
    )
    both(
        "a host with no entry no longer falls back to the shared key",
        Edit(ACTION, "'($ENV.HOST_KEYS | fromjson) | has($dest)'", "'($ENV.HOST_KEYS | fromjson) | true'"),
        Edit(GATED, "'($ENV.HOST_KEYS | fromjson) | has($dest)'", "'($ENV.HOST_KEYS | fromjson) | true'"),
        [ONE, HEAD],
        [A_ONE],
    )
    both(
        "the call adds a second -i beside the shared one instead of replacing its value",
        Edit(ACTION, 'args[$((index + 1))]="$identity"', 'args+=(-i "$identity")'),
        Edit(GATED, 'args[$((index + 1))]="$identity"', 'args+=(-i "$identity")'),
        [f"{PHK}.test_every_call_has_one_identity_option_and_otherwise_the_unchanged_argv"],
        [A_ONE],
    )
    # A host with no key is refused in the validate block, before any file is written.
    no_key = 'elif ($ENV.DRY_RUN != "true") and ($shared | length) == 0 and any($hosts[]; . as $h | ($map | has($h)) | not) then "no-key"\n'
    both(
        "a host with no entry and no shared key is accepted (the validate block no longer refuses it)",
        Edit(ACTION, no_key, ""),
        Edit(GATED, no_key, ""),
        [f"{PHK}.test_a_host_with_no_entry_and_no_shared_key_fails_in_the_validate_step_before_any_file_or_host", f"{HKV}.test_each_refusal_fails_the_step_with_its_own_message_and_echoes_no_value"],
        [f"{AHK}.test_a_host_with_no_entry_and_no_shared_key_fails_before_any_host_is_asked", f"{AHK}.test_each_refusal_comes_before_any_file_or_host_and_echoes_no_value"],
    )
    # The compare drops CR and the whitespace round a key, so CRLF or a leading blank cannot dodge it.
    full_trim = r'def trim: gsub("\r"; "") | sub("\\A\\s+"; "") | sub("\\s+\\z"; "");'
    old_trim = r'def trim: sub("\\s+\\z"; "");'
    both(
        "keys are compared without dropping CR or leading whitespace",
        Edit(ACTION, full_trim, old_trim),
        Edit(GATED, full_trim, old_trim),
        [f"{HKV}.test_each_refusal_fails_the_step_with_its_own_message_and_echoes_no_value"],
        [f"{AHK}.test_each_refusal_comes_before_any_file_or_host_and_echoes_no_value"],
    )
    full_write = r'[$dest] | gsub("\r"; "") | sub("\\A\\s+"; "") | sub("\\s+\\z"; "")'
    old_write = r'[$dest] | sub("\\s+\\z"; "")'
    both(
        "a key is written with its CR and leading whitespace",
        Edit(ACTION, full_write, old_write),
        Edit(GATED, full_write, old_write),
        [f"{PHK}.test_a_key_with_crlf_line_ends_and_leading_whitespace_is_written_as_plain_lines"],
        [f"{AHK}.test_a_key_with_crlf_line_ends_and_leading_whitespace_is_written_as_plain_lines"],
    )

    # What is refused before any file is written or host asked.
    refusals = (
        ("the check that a key's host is in the hosts list is removed", 'elif any($map | keys[]; . as $k | all($hosts[]; . != $k)) then "unknown-host"\n'),
        ("the check that no two hosts share a key is removed", 'elif ([$map[] | trim] | length) != ([$map[] | trim] | unique | length) then "duplicate"\n'),
        ("the check that no host key is the shared key is removed", 'elif any($map[]; . as $v | any($shared[]; . == ($v | trim))) then "shared"\n'),
        ("the check that no host key is a jump key is removed", 'elif any($map[]; . as $v | any($jump[]; . == ($v | trim))) then "jump"\n'),
        ("the check that a key is a non-empty string is removed", 'elif any($map[]; (type != "string") or (trim == "")) then "bad-value"\n'),
    )
    for name, line in refusals:
        both(
            name,
            Edit(ACTION, line, ""),
            Edit(GATED, line, ""),
            [f"{HKV}.test_each_refusal_fails_the_step_with_its_own_message_and_echoes_no_value"],
            [f"{AHK}.test_each_refusal_comes_before_any_file_or_host_and_echoes_no_value"],
        )
    both(
        "keys are compared without trimming the newline a key file ends with",
        Edit(ACTION, "[$map[] | trim] | length) != ([$map[] | trim] | unique", "[$map[]] | length) != ([$map[]] | unique"),
        Edit(GATED, "[$map[] | trim] | length) != ([$map[] | trim] | unique", "[$map[]] | length) != ([$map[]] | unique"),
        [f"{HKV}.test_each_refusal_fails_the_step_with_its_own_message_and_echoes_no_value"],
        [f"{AHK}.test_each_refusal_comes_before_any_file_or_host_and_echoes_no_value"],
    )

    # Removal, privacy, masking and silence.
    mask = 'echo "::add-mask::$host_key_line"'
    both(
        "the lines of a key are not masked",
        Edit(ACTION, mask, ":"),
        Edit(GATED, mask, ":"),
        [f"{PHK}.test_a_key_is_never_printed_but_each_of_its_lines_is_masked"],
        [f"{AHK}.test_a_key_is_never_printed_but_each_of_its_lines_is_masked"],
    )
    echo_key = "printf '\\n' >> \"$host_key_file\"\n"
    both(
        "the step prints the key it has written",
        Edit(ACTION, echo_key, echo_key + '                    cat "$host_key_file"\n'),
        Edit(GATED, echo_key, echo_key + '                    cat "$host_key_file"\n'),
        [f"{PHK}.test_a_key_is_never_printed_but_each_of_its_lines_is_masked"],
        [f"{AHK}.test_a_key_is_never_printed_but_each_of_its_lines_is_masked"],
    )
    umask = "umask 077\n                    jq -n -j"
    both(
        "a host key file is written with the runner umask",
        Edit(ACTION, umask, "jq -n -j"),
        Edit(GATED, umask, "jq -n -j"),
        [f"{PHK}.test_the_key_files_are_private_while_ssh_runs_and_removed_on_every_way_out"],
        [f"{AHK}.test_the_key_files_are_private_while_ssh_runs_and_removed_on_every_way_out"],
    )
    both(
        "the newline a key file ends with is written through",
        Edit(ACTION, '| sub("\\\\s+\\\\z"; "")\' > "$host_key_file"', '\' > "$host_key_file"'),
        Edit(GATED, '| sub("\\\\s+\\\\z"; "")\' > "$host_key_file"', '\' > "$host_key_file"'),
        [f"{PHK}.test_a_key_pasted_with_trailing_newlines_is_written_with_exactly_one"],
        [f"{AHK}.test_a_key_pasted_with_trailing_newlines_is_written_with_exactly_one"],
    )
    traps = (
        ("the first trap leaves the host key files behind", "        trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir\"/id.*' EXIT\n", "        trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\"' EXIT\n", ACTION, PHK, "test_the_key_files_are_private_while_ssh_runs_and_removed_on_every_way_out"),
        ("the jump trap leaves the host key files behind", "trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir\"/id.* \"$plan_dir/jump_id\" \"$plan_dir/jump_config\"' EXIT\n", "trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir/jump_id\" \"$plan_dir/jump_config\"' EXIT\n", ACTION, PHK, "test_the_key_files_are_removed_after_a_jump_and_after_a_second_hop"),
        ("the second hop trap leaves the host key files behind", "trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir\"/id.* \"$plan_dir/jump_id\" \"$plan_dir/jump_config\" \"$plan_dir/jump2_id\"' EXIT\n", "trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\" \"$plan_dir/jump_id\" \"$plan_dir/jump_config\" \"$plan_dir/jump2_id\"' EXIT\n", ACTION, PHK, "test_the_key_files_are_removed_after_a_jump_and_after_a_second_hop"),
        ("the first trap leaves the host key files behind", "          trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work\"/id.*' EXIT\n", "          trap 'rm -f \"$work/id\" \"$work/known_hosts\"' EXIT\n", GATED, AHK, "test_the_key_files_are_private_while_ssh_runs_and_removed_on_every_way_out"),
        ("the jump trap leaves the host key files behind", "trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work\"/id.* \"$work/jump_id\" \"$work/jump_config\"' EXIT\n", "trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work/jump_id\" \"$work/jump_config\"' EXIT\n", GATED, AHK, "test_the_key_files_are_removed_after_a_jump_and_after_a_second_hop"),
        ("the second hop trap leaves the host key files behind", "trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work\"/id.* \"$work/jump_id\" \"$work/jump_config\" \"$work/jump2_id\"' EXIT\n", "trap 'rm -f \"$work/id\" \"$work/known_hosts\" \"$work/jump_id\" \"$work/jump_config\" \"$work/jump2_id\"' EXIT\n", GATED, AHK, "test_the_key_files_are_removed_after_a_jump_and_after_a_second_hop"),
    )
    for name, old, new, path, cls, test in traps:
        side = "plan side" if path == ACTION else "apply side"
        cases.append(Case(f"host keys, {side}: {name}", [Edit(path, old, new)], [f"{cls}.{test}", f"{HKP}.test_every_trap_that_removes_the_shared_key_removes_the_per_host_key_files_too"]))

    # The scan of a diff, the secrets each job reads, and the places the map is passed.
    scan = (
        "        # The key of each host that has one of its own is scanned for the same way.\n"
        "        if [ -n \"$HOST_KEYS\" ]; then\n"
        "          jq -n -r '($ENV.HOST_KEYS | fromjson) | .[] | gsub(\"\\r\"; \"\") | split(\"\\n\")[]' | awk 'length($0) >= 16 && $0 !~ /^-----/' >> \"$keylines\"\n"
        "        fi\n"
    )
    cases.append(Case("host keys, plan side: the report step leaves the host keys out of the scan of the diff", [Edit(ACTION, scan, "")], [f"{PHK}.test_a_diff_that_quotes_a_host_key_is_withheld_and_never_shown"]))
    cases.append(Case(
        "host keys, plan side: the plan job also reads ssh_host_apply_keys",
        [Edit(GATED, "host-check-keys: ${{ secrets.ssh_host_check_keys }}", "host-check-keys: ${{ secrets.ssh_host_check_keys || secrets.ssh_host_apply_keys }}")],
        [f"{HKS}.test_the_host_apply_keys_are_read_by_the_apply_step_only_and_never_by_the_plan_or_the_drift_check", f"{S}.test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"],
    ))
    cases.append(Case(
        "host keys, drift check: the drift check also reads ssh_host_apply_keys",
        [Edit(DRIFT, "host-check-keys: ${{ secrets.ssh_host_check_keys }}", "host-check-keys: ${{ secrets.ssh_host_check_keys || secrets.ssh_host_apply_keys }}")],
        [f"{HKS}.test_the_host_apply_keys_are_read_by_the_apply_step_only_and_never_by_the_plan_or_the_drift_check"],
    ))
    cases.append(Case(
        "host keys, apply side: the apply step reads the check keys",
        [Edit(GATED, "HOST_KEYS: ${{ secrets.ssh_host_apply_keys }}", "HOST_KEYS: ${{ secrets.ssh_host_check_keys }}")],
        [f"{HKS}.test_the_host_check_keys_are_read_by_the_action_step_of_the_plan_and_of_the_drift_check_only", f"{S}.test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source"],
    ))
    cases.append(Case(
        "host keys, plan side: the plan job does not pass the host check keys",
        [Edit(GATED, "          host-check-keys: ${{ secrets.ssh_host_check_keys }}\n", "")],
        [f"{HKS}.test_the_host_check_keys_are_read_by_the_action_step_of_the_plan_and_of_the_drift_check_only", f"{S}.test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs"],
    ))
    cases.append(Case(
        "host keys, drift check: the drift check does not pass the host check keys, so a host with its own key fails the nightly check",
        [Edit(DRIFT, "          host-check-keys: ${{ secrets.ssh_host_check_keys }}\n", "")],
        [f"{HKS}.test_the_host_check_keys_are_read_by_the_action_step_of_the_plan_and_of_the_drift_check_only", f"{S}.test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies"],
    ))

    # The two copies of the text drift apart, and a key is handed to a command.
    cases.append(Case(
        "host keys: the validation block of the action drifts from the apply step's",
        [Edit(ACTION, 'duplicate) fail "Two hosts have the same key: each host has a key of its own."', 'duplicate) fail "Two hosts have the same key."')],
        [f"{HKP}.test_the_validation_block_is_the_same_text_in_the_action_and_in_the_apply_step"],
    ))
    cases.append(Case(
        "host keys: the write block of the apply step drifts from the action's",
        [Edit(GATED, "awk 'length($0) >= 16 && $0 !~ /^-----/' \"$host_key_file\"", "awk 'length($0) >= 20 && $0 !~ /^-----/' \"$host_key_file\"")],
        [f"{HKP}.test_the_write_block_is_the_same_text_in_the_action_and_in_the_apply_step_the_directory_aside"],
    ))
    cases.append(Case(
        "host keys: a key is handed to jq on its argument list",
        [Edit(ACTION, "jq -n -e --arg dest \"$host_key_dest\" '($ENV.HOST_KEYS", "jq -n -e --arg dest \"$host_key_dest\" --arg keys \"$HOST_KEYS\" '($ENV.HOST_KEYS")],
        [f"{HKP}.test_a_key_is_read_from_the_environment_inside_jq_and_never_given_to_a_command"],
    ))
    cases.append(Case(
        "host keys: a call of the one-call function leaves out the identity file",
        [Edit(ACTION, 'ssh_via "$chain" "${limit}s" "$identity" -- "$dest" "$SHA" < /dev/null', 'ssh_via "$chain" "${limit}s" "$workaround" -- "$dest" "$SHA" < /dev/null')],
        [f"{JPar}.test_every_call_of_that_function_names_the_identity_file_of_its_host"],
    ))
    return cases


CASES.extend(host_key_cases())


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
        target.write_text(text.replace(edit.old, edit.new))
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
    red = {f"{cls}.{meth}" for meth, cls in re.findall(r"(?m)^(?:FAIL|ERROR): (\w+) \(__main__\.(\w+)\.\w+\)", log)}
    return proc.returncode, red, log


def copy_repo(destination: Path) -> None:
    shutil.copytree(REPO, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))


def run_case(case: Case, discover: bool) -> tuple[Case, bool, str]:
    with tempfile.TemporaryDirectory(prefix="sabotage-") as scratch:
        root = Path(scratch) / "repo"
        copy_repo(root)
        problem = apply_edits(root, case.edits)
        if problem:
            return case, False, f"CANNOT BREAK  {problem}"
        subprocess.run([sys.executable, str(root / TESTS), "--write-snapshot"], cwd=root, check=True)
        if discover:
            code, red, _ = run_tests(root, [])
            return case, True, "red: " + ", ".join(sorted(red)) if red else "NOT RED"
        code, red, log = run_tests(root, case.red)
        missing = [name for name in case.red if name not in red]
        if code == 0 or missing:
            return case, False, f"NOT RED  expected red: {missing or case.red}\n{log[-1500:]}"
        return case, True, "RED  " + ", ".join(sorted(red))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("names", nargs="*", help="run the cases whose name contains one of these")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    args = parser.parse_args()

    cases = [c for c in CASES if not args.names or any(n in c.name for n in args.names)]
    if args.list:
        for case in cases:
            print(case.name)
        return 0
    if not args.discover:
        empty = [c.name for c in CASES if not c.red]
        if empty:
            print(f"cases with no red test named: {empty}")
            return 1
        names = [c.name for c in CASES]
        if len(set(names)) != len(names):
            print("two cases share a name")
            return 1

    code, _, log = run_tests(REPO, [])
    if code != 0:
        print("The unedited tree is not green, so no red can be trusted:\n" + log[-3000:])
        return 1
    print(f"GREEN  the unedited tree passes the whole suite; {len(cases)} case(s) follow", flush=True)

    failed = 0
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for case, ok, line in pool.map(lambda c: run_case(c, args.discover), cases):
            print(f"{'ok  ' if ok else 'FAIL'}  {case.name}: {line}", flush=True)
            failed += 0 if ok else 1
    print(f"{len(cases) - failed} of {len(cases)} case(s) went red as expected")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
