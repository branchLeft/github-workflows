"""The merge queue's confirmation, the skipped rerun and the gate that reads the jobs."""

from __future__ import annotations

from ..files import GATED_APPLY
from ..model import Case, Edit
from ..suites import CONFIRM_MERGE_GROUP, GATE, RERUN_DECISION, SKIP_RERUN_DECLARATION
from ..text import indented

CASES: list[Case] = [
    Case(
        "confirm accepts a check run of any app",
        [Edit(GATED_APPLY, indented('and .app.slug == "github-actions")', 1), ")")],
        [CONFIRM_MERGE_GROUP("test_a_check_run_made_by_another_app_is_refused")],
    ),
    Case(
        "confirm accepts a passing check from a suite that is not a merge queue run",
        [
            Edit(
                GATED_APPLY,
                '(.head_branch // "" | startswith("gh-readonly-queue/")) and .head_sha == $sha',
                ".head_sha == $sha",
            ),
        ],
        [CONFIRM_MERGE_GROUP("test_a_passing_check_that_is_not_from_a_merge_queue_run_is_refused")],
    ),
    Case(
        "confirm accepts a queue suite of another commit",
        [Edit(GATED_APPLY, indented("and .head_sha == $sha' > /dev/null; then", 1), "' > /dev/null; then")],
        [CONFIRM_MERGE_GROUP("test_a_passing_check_that_is_not_from_a_merge_queue_run_is_refused")],
    ),
    Case(
        "confirm accepts a check that did not succeed",
        [Edit(GATED_APPLY, '.conclusion == "success" and .head_sha', ".head_sha")],
        [CONFIRM_MERGE_GROUP("test_a_check_that_failed_or_is_unfinished_or_was_skipped_is_refused")],
    ),
    Case(
        "confirm accepts a check of another name",
        [Edit(GATED_APPLY, "select(.name == $name and .status", "select(.status")],
        [CONFIRM_MERGE_GROUP("test_a_check_of_another_name_is_refused")],
    ),
    Case(
        "confirm accepts a check run of another commit",
        [Edit(GATED_APPLY, indented("and .head_sha == $sha and .app.slug", 1), indented("and .app.slug", 1))],
        [CONFIRM_MERGE_GROUP("test_a_check_run_for_another_commit_is_refused")],
    ),
    Case(
        "confirm passes when the checks cannot be read",
        [
            Edit(
                GATED_APPLY,
                'fail "The check runs of the commit could not be read, so nothing shows the merge queue passed it. Refusing."',
                "runs='{\"check_runs\": []}'",
            ),
        ],
        [CONFIRM_MERGE_GROUP("test_when_the_checks_cannot_be_read_it_cannot_tell_and_refuses")],
    ),
    Case(
        "confirm falls through to success when nothing matched",
        [
            Edit(
                GATED_APPLY,
                indented("fail \"No passing '$CHECK_NAME'", 10),
                indented("exit 0; fail \"No passing '$CHECK_NAME'", 10),
            ),
        ],
        [CONFIRM_MERGE_GROUP("test_a_commit_with_no_check_runs_is_refused")],
    ),
    Case(
        "confirm step never runs",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    if: steps.context.outputs.rerun == 'false'
                    """,
                    8,
                ),
                indented(
                    r"""
                    if: steps.context.outputs.rerun == 'never'
                    """,
                    8,
                ),
            ),
        ],
        [SKIP_RERUN_DECLARATION("test_the_confirmation_is_a_discover_step_that_runs_only_when_the_rerun_is_skipped")],
    ),
    Case(
        "rerun skipped on a run that does not deliver",
        [
            Edit(
                GATED_APPLY,
                'if [ "$delivery" = true ] && [ "$SKIP_RERUN" = true ]; then',
                'if [ "$SKIP_RERUN" = true ]; then',
            ),
        ],
        [RERUN_DECISION("test_a_run_that_does_not_deliver_always_reruns_whatever_it_asks")],
    ),
    Case(
        "skip input defaults to on",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                      type: boolean
                      required: false
                      default: false
                    merge-group-check-name:""",
                    6,
                ),
                indented(
                    r"""
                      type: boolean
                      required: false
                      default: true
                    merge-group-check-name:""",
                    6,
                ),
            ),
        ],
        [SKIP_RERUN_DECLARATION("test_the_inputs_default_to_todays_behaviour")],
    ),
    Case(
        "lint runs on a delivery run that asked to skip it",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    needs: [discover]
                    if: needs.discover.outputs.recheck == 'true'
                    runs-on: ubuntu-24.04
                    timeout-minutes: 20""",
                    4,
                ),
                indented(
                    r"""
                    needs: [discover]
                    runs-on: ubuntu-24.04
                    timeout-minutes: 20""",
                    4,
                ),
            ),
        ],
        [SKIP_RERUN_DECLARATION("test_lint_and_the_shards_run_unless_discover_says_the_merge_queue_already_did")],
    ),
    Case(
        "gate accepts lint that ran when it was to be skipped",
        [Edit(GATED_APPLY, 'expect lint "$LINT" skipped', 'expect lint "$LINT" "$LINT"')],
        [GATE("test_a_delivery_run_that_skipped_the_rerun_needs_lint_and_the_shards_skipped")],
    ),
    Case(
        "gate accepts skipped lint when it was to run",
        [Edit(GATED_APPLY, 'expect lint "$LINT" success', 'expect lint "$LINT" "$LINT"')],
        [GATE("test_a_run_that_reruns_still_needs_lint_and_the_shards_to_have_passed")],
    ),
    Case(
        "gate accepts a skipped or cancelled apply when the plan found changes",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    else
                      expect apply "$APPLY" success
                    fi""",
                    14,
                ),
                indented(
                    r"""
                    else
                      :
                    fi""",
                    14,
                ),
            ),
        ],
        [GATE("test_a_delivery_run_whose_apply_was_skipped_or_cancelled_is_red_even_though_the_plan_found_changes")],
    ),
]
