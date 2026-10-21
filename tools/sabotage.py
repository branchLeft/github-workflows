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
HM = "HeadMovedTests"

CASES: list[Case] = [
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
        [Edit(ACTION, "        trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\"' EXIT\n", "")],
        [f"{P}.test_the_check_key_and_known_hosts_are_removed_on_every_way_out_a_failure_included"],
    ),
    Case(
        "check key copies removed only on the way out that succeeds",
        [
            Edit(ACTION, "        trap 'rm -f \"$plan_dir/id\" \"$plan_dir/known_hosts\"' EXIT\n", ""),
            Edit(ACTION, '        echo "Asked $i host(s); the answers are judged in the next step."\n', '        rm -f "$plan_dir/id" "$plan_dir/known_hosts"\n        echo "Asked $i host(s); the answers are judged in the next step."\n'),
        ],
        [f"{P}.test_the_check_key_and_known_hosts_are_removed_on_every_way_out_a_failure_included"],
    ),
    Case(
        "apply key copies not removed",
        [Edit(GATED, "          trap 'rm -f \"$work/id\" \"$work/known_hosts\"' EXIT\n", "")],
        [f"{A}.test_the_apply_key_and_known_hosts_are_removed_on_every_way_out_a_refusal_included"],
    ),
    Case(
        "apply key copies removed only on the way out that succeeds",
        [
            Edit(GATED, "          trap 'rm -f \"$work/id\" \"$work/known_hosts\"' EXIT\n", ""),
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
        [Edit(GATED, '[ -n "$SSH_KEY" ] || fail "The ssh_apply_key secret is empty. It is a secret of the deployment environment."', ":")],
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
    # The refusals of the apply guard and the head-moved stop, one case each.
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
        "apply gate: moved default branch does not stop the apply",
        [Edit(GATED, 'if [ "$head" != "$PLANNED_SHA" ]; then', "if false; then")],
        [f"{HM}.test_a_branch_head_that_moved_stops_the_apply"],
    ),
    Case(
        "apply gate: environment term joined with or in the apply if",
        [Edit(GATED, "      && inputs.environment != ''\n", "      || inputs.environment != ''\n")],
        [f"{S}.test_the_apply_job_is_gated"],
    ),
]


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
