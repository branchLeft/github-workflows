"""How a delivery run is put together: the role test, which jobs run for which events, and what the plan reads."""

from __future__ import annotations

from ..files import GATED_APPLY
from ..model import Case, Edit
from ..suites import PLAN_GUARD, ROLE_TEST_ENTRY_POINT, STRUCTURE
from ..text import indented

CASES: list[Case] = [
    Case(
        "delivery runs a Molecule sequence of its own beside the role test",
        [
            Edit(
                GATED_APPLY,
                indented("# The plan job. It runs only for a push", 2),
                indented(
                    r"""
                        - name: Molecule check action
                          working-directory: ${{ inputs.working-directory }}/${{ inputs.ansible-directory }}/${{ matrix.path }}
                          run: '"$RUNNER_TEMP/ansible-venv/bin/molecule" check'

                    # The plan job. It runs only for a push""",
                    2,
                ),
            ),
        ],
        [
            ROLE_TEST_ENTRY_POINT(
                "test_test_the_role_is_the_only_step_of_the_shard_job_that_runs_molecule_or_a_role_test"
            ),
        ],
    ),
    Case(
        "delivery ignores the caller's role test command",
        [Edit(GATED_APPLY, 'if [ -n "$ROLE_TEST_COMMAND" ]; then', "if false; then")],
        [
            ROLE_TEST_ENTRY_POINT(
                "test_a_set_command_is_run_from_the_ansible_directory_with_the_role_and_molecule_is_never_called"
            ),
        ],
    ),
    Case(
        "delivery lets the caller's role test command through unchecked",
        [
            Edit(
                GATED_APPLY,
                'if ! [[ "$ROLE_TEST_COMMAND" =~ $command_re ]] || [[ "$ROLE_TEST_COMMAND" == *..* ]]; then',
                "if false; then",
            ),
        ],
        [
            ROLE_TEST_ENTRY_POINT(
                "test_a_command_that_is_not_a_plain_relative_python_file_is_refused_before_anything_runs"
            ),
        ],
    ),
    Case(
        "plan guard accepts schedule instead of push",
        [
            Edit(
                GATED_APPLY,
                '[ "$EVENT_NAME" = push ] || fail "The plan job runs only for a push, not for $EVENT_NAME."',
                '[ "$EVENT_NAME" = schedule ] || fail "The plan job runs only for a push, not for $EVENT_NAME."',
            ),
        ],
        [PLAN_GUARD("test_a_push_to_the_default_branch_passes_and_publishes_the_commit")],
    ),
    Case(
        "plan has_changes read from the wrong step",
        [
            Edit(
                GATED_APPLY,
                "has_changes: ${{ steps.ssh.outputs.has-changes }}",
                "has_changes: ${{ steps.guard.outputs.has-changes }}",
            ),
        ],
        [STRUCTURE("test_the_plan_job_outputs_come_from_the_steps_that_make_them")],
    ),
    Case(
        "plan hosts_digest read from the wrong step",
        [
            Edit(
                GATED_APPLY,
                "hosts_digest: ${{ steps.ssh.outputs.hosts-digest }}",
                "hosts_digest: ${{ steps.guard.outputs.hosts-digest }}",
            ),
        ],
        [STRUCTURE("test_the_plan_job_outputs_come_from_the_steps_that_make_them")],
    ),
    Case(
        "shared actions checked out at a literal tag",
        [Edit(GATED_APPLY, "ref: ${{ steps.pin.outputs.sha }}", "ref: v1.0.0")],
        [STRUCTURE("test_the_shared_actions_are_checked_out_after_a_pin_that_cannot_be_empty_and_before_they_run")],
    ),
    Case(
        "a second local action in the plan job",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    - name: Check every host in check mode and write the plan
                      id: ssh
                    """,
                    6,
                ),
                indented(
                    r"""
                    - name: A second local action
                      uses: ./.github/actions/plan-has-changes

                    - name: Check every host in check mode and write the plan
                      id: ssh
                    """,
                    6,
                ),
            ),
        ],
        [
            STRUCTURE(
                "test_every_action_is_pinned_to_a_commit_with_a_version_comment_or_is_the_one_checked_out_action"
            ),
        ],
    ),
    Case(
        "plan job calls the action by a caller-relative path",
        [
            Edit(
                GATED_APPLY,
                "uses: ./.gw/.github/actions/ssh-check-hosts",
                "uses: ./.github/actions/ssh-check-hosts",
            ),
        ],
        [STRUCTURE("test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs")],
    ),
    Case(
        "plan job passes fail-on-changes true",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    host-timeout: ${{ inputs.host-timeout-minutes }}
                    environment: ${{ inputs.environment }}""",
                    10,
                ),
                indented(
                    r"""
                    host-timeout: ${{ inputs.host-timeout-minutes }}
                    fail-on-changes: 'true'
                    environment: ${{ inputs.environment }}""",
                    10,
                ),
            ),
        ],
        [STRUCTURE("test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs")],
    ),
    Case(
        "plan runs after a failed lint",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    && (needs.lint.result == 'success' || needs.lint.result == 'skipped')
                    """,
                    6,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_plan_job_runs_only_for_delivery_and_only_after_every_need_succeeded_or_was_skipped")],
    ),
    Case(
        "apply has no leading status function, so a skipped lint skips it",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    !cancelled() && !failure()
                    && needs.discover.outputs.delivery""",
                    6,
                ),
                indented("needs.discover.outputs.delivery", 6),
            ),
        ],
        [STRUCTURE("test_the_apply_job_leads_with_a_status_function_so_a_skipped_lint_does_not_skip_it")],
    ),
    Case(
        "plan runs for a superseded run (the old condition)",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    ${{ !cancelled()
                    && needs.discover.result == 'success'
                    && (needs.lint.result == 'success' || needs.lint.result == 'skipped')
                    && (needs.shard.result == 'success' || needs.shard.result == 'skipped')
                    && needs.discover.outputs.delivery == 'true' }}""",
                    6,
                ),
                indented("${{ !cancelled() && !failure() && needs.discover.outputs.delivery == 'true' }}", 6),
            ),
        ],
        [STRUCTURE("test_the_plan_job_runs_only_for_delivery_and_only_after_every_need_succeeded_or_was_skipped")],
    ),
]
