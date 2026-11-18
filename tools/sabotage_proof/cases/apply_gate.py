"""The apply gate: the approval, the apply guard's refusals and the judging of the apply's answers."""

from __future__ import annotations

from ..files import ACTION, GATED_APPLY
from ..model import Case, Edit
from ..suites import APPLY, APPLY_GUARD, PLAN, PUSH_APPLY, STRUCTURE, VALIDATE_HOSTS
from ..text import indented

CASES: list[Case] = [
    Case(
        "apply judge regex drops the dash count of a refusal",
        [
            Edit(
                GATED_APPLY,
                "changed=([0-9]+|-)( floor=[0-9a-f]{40})?$'",
                "changed=([0-9]+)( floor=[0-9a-f]{40})?$'",
            ),
        ],
        [APPLY("test_a_real_refusal_line_with_no_count_fails_the_apply_with_its_reason_and_asks_no_second_run")],
    ),
    Case(
        "apply accepts an applied or unchanged line with a dash count",
        [Edit(GATED_APPLY, '[ "$f_changed" = - ] || { [ "$f_verdict"', '{ [ "$f_verdict"')],
        [APPLY("test_an_applied_or_unchanged_line_that_counts_no_change_as_a_dash_fails_the_job")],
    ),
    Case(
        "host list joined with a comma in the apply guard",
        [Edit(GATED_APPLY, 'list="${list:+$list }$dest"', 'list="${list:+$list,}$dest"')],
        [VALIDATE_HOSTS("test_the_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes")],
    ),
    Case(
        "apply gate: environment key deleted from the apply job",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    timeout-minutes: ${{ inputs.apply-timeout-minutes }}
                    environment: ${{ inputs.environment }}
                    """,
                    4,
                ),
                indented(
                    r"""
                    timeout-minutes: ${{ inputs.apply-timeout-minutes }}
                    """,
                    4,
                ),
            ),
        ],
        [STRUCTURE("test_the_apply_job_is_gated")],
    ),
    Case(
        "apply gate: environment hard-coded in the apply job",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    timeout-minutes: ${{ inputs.apply-timeout-minutes }}
                    environment: ${{ inputs.environment }}
                    """,
                    4,
                ),
                indented(
                    r"""
                    timeout-minutes: ${{ inputs.apply-timeout-minutes }}
                    environment: production
                    """,
                    4,
                ),
            ),
        ],
        [STRUCTURE("test_the_apply_job_is_gated")],
    ),
    Case(
        "apply gate: has_changes condition dropped from the apply if",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    && needs.plan.outputs.has_changes != 'false'
                    """,
                    6,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_apply_job_is_gated")],
    ),
    Case(
        "apply gate: non-empty environment term dropped from the apply if",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    && inputs.environment != ''
                    """,
                    6,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_apply_job_is_gated")],
    ),
    Case(
        "apply gate: empty apply key no longer refused",
        [
            Edit(
                GATED_APPLY,
                '[ -n "$SSH_KEY" ] || [ -n "$HOST_KEYS" ] || fail "The ssh_apply_key secret is empty. It is a secret of the deployment environment."',
                ":",
            ),
        ],
        [APPLY("test_the_apply_key_secret_must_be_present")],
    ),
    Case(
        "apply gate: apply guard passes a plan that found nothing",
        [Edit(GATED_APPLY, '[ "$HAS_CHANGES" != false ] || fail "The plan found nothing to change."', ":")],
        [APPLY_GUARD("test_the_apply_refuses_a_plan_with_nothing_to_change")],
    ),
    Case(
        "apply gate: no-change verdict when a host would change",
        [
            Edit(
                ACTION,
                indented(
                    'if [ "$changed" -eq 0 ] && { [ "$clean" -eq "$total" ] || [ "$partial" = true ]; } && [ "$total" -ge 1 ]; then',
                    8,
                ),
                indented('if [ "$total" -ge 1 ]; then', 8),
            ),
        ],
        [PLAN("test_one_host_with_changes_gives_has_changes_and_shows_its_diff")],
    ),
    Case(
        "apply gate: a failed host does not fail the plan",
        [
            Edit(
                ACTION,
                indented(
                    r'''
                    if [ "$failed" -gt 0 ] && [ "$partial" != true ]; then
                      echo "::error::$first"''',
                    8,
                ),
                indented(
                    r'''
                    if false; then
                      echo "::error::$first"''',
                    8,
                ),
            ),
        ],
        [PLAN("test_a_host_that_fails_fails_the_job_and_asks_for_no_approval")],
    ),
    # The refusals of the apply guard, one case each.
    Case(
        "apply gate: event check dropped from the apply guard",
        [
            Edit(
                GATED_APPLY,
                '[ "$EVENT_NAME" = push ] || fail "The apply job runs only for a push, not for $EVENT_NAME."',
                ":",
            ),
        ],
        [APPLY_GUARD("test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run")],
    ),
    Case(
        "apply gate: default-branch check dropped from the apply guard",
        [
            Edit(
                GATED_APPLY,
                '[ "$REF" = "refs/heads/$DEFAULT_BRANCH" ] || fail "The apply job runs only for the default branch."',
                ":",
            ),
        ],
        [APPLY_GUARD("test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run")],
    ),
    Case(
        "apply gate: fork check dropped from the apply guard",
        [Edit(GATED_APPLY, '[ "$IS_FORK" != true ] || fail "The apply job never runs in a fork."', ":")],
        [APPLY_GUARD("test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run")],
    ),
    Case(
        "apply gate: dry-run check dropped from the apply guard",
        [Edit(GATED_APPLY, '[ "$DRY_RUN" != true ] || fail "The apply job never runs in a dry run."', ":")],
        [APPLY_GUARD("test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run")],
    ),
    Case(
        "apply gate: empty-environment backstop dropped from the apply guard",
        [
            Edit(
                GATED_APPLY,
                '[ -n "$ENVIRONMENT" ] || fail "The environment input is empty, so there is no approval."',
                ":",
            ),
        ],
        [APPLY_GUARD("test_the_apply_refuses_without_an_environment")],
    ),
    Case(
        "apply gate: planned commit format check dropped",
        [
            Edit(
                GATED_APPLY,
                "grep -Eq '^[0-9a-f]{40}$' || fail \"The planned commit is not 40 lowercase hex digits.\"",
                "grep -Eq '^[0-9a-f]{40}$' || :",
            ),
        ],
        [APPLY_GUARD("test_the_planned_commit_must_be_40_lowercase_hex_digits")],
    ),
    Case(
        "apply gate: planned commit equality check dropped",
        [
            Edit(
                GATED_APPLY,
                '[ "$PLANNED_SHA" = "$SHA" ] || fail "The planned commit differs from this run\'s commit."',
                ":",
            ),
        ],
        [APPLY_GUARD("test_the_apply_refuses_a_commit_other_than_the_planned_one")],
    ),
    Case(
        "apply gate: planned host digest format check dropped",
        [
            Edit(
                GATED_APPLY,
                "grep -Eq '^[0-9a-f]{64}$' || fail \"The planned host digest is missing.\"",
                "grep -Eq '^[0-9a-f]{64}$' || :",
            ),
        ],
        [APPLY_GUARD("test_the_planned_host_digest_must_be_64_lowercase_hex_digits")],
    ),
    Case(
        "apply gate: planned host digest equality check dropped",
        [
            Edit(
                GATED_APPLY,
                '[ "$digest" = "$PLANNED_DIGEST" ] || fail "The hosts are not the hosts that were planned."',
                ":",
            ),
        ],
        [APPLY_GUARD("test_the_apply_refuses_hosts_that_were_not_planned")],
    ),
    Case(
        "apply gate: a host refusal of an older commit is accepted",
        [
            Edit(
                GATED_APPLY,
                indented('if [ "$ok" -ne 0 ] || [ "$f_changed" = - ] || {', 12),
                indented('if [ "$ok" -ne 0 ] && [ "$rc:$f_verdict" != "20:refused" ] || [ "$f_changed" = - ] || {', 12),
            ),
        ],
        [APPLY("test_a_host_that_already_ran_a_newer_commit_refuses_and_the_apply_fails_with_the_reason")],
    ),
    Case(
        "apply gate: a rollback refusal does not name the commits",
        [
            Edit(
                GATED_APPLY,
                indented('if [ "$f_reason" = rollback ] && [ "$f_verdict" = refused ] && [ "$f_exit" = 20 ]; then', 14),
                indented("if false; then", 14),
            ),
        ],
        [
            APPLY("test_a_rollback_refusal_names_the_planned_commit_and_the_hosts_last_applied_commit"),
            APPLY("test_a_rollback_refusal_with_no_floor_names_the_planned_commit_and_says_where_the_floor_is"),
            PUSH_APPLY("test_a_pushed_host_that_refuses_a_rollback_names_both_commits_when_its_line_carries_the_floor"),
        ],
    ),
    Case(
        "apply gate: environment term joined with or in the apply if",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    && inputs.environment != ''
                    """,
                    6,
                ),
                indented(
                    r"""
                    || inputs.environment != ''
                    """,
                    6,
                ),
            ),
        ],
        [STRUCTURE("test_the_apply_job_is_gated")],
    ),
    Case(
        "apply gate: empty known-hosts refusal no longer refused",
        [Edit(GATED_APPLY, '[ -n "$KNOWN_HOSTS" ] || fail "The ssh_known_hosts secret is empty."', ":")],
        [APPLY("test_the_known_hosts_secret_must_be_present")],
    ),
]
