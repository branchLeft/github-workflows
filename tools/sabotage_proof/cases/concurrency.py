"""The concurrency groups: what may cancel what, and what never cancels a delivery."""

from __future__ import annotations

from ..files import DRIFT_CHECK, GATED_APPLY
from ..model import Case, Edit
from ..suites import CONCURRENCY, DELIVERY_DOES_NOT_STARVE
from ..text import indented

CASES: list[Case] = [
    Case(
        "apply job group cancels a running apply",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}
                    cancel-in-progress: false""",
                    6,
                ),
                indented(
                    r"""
                    group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}
                    cancel-in-progress: true""",
                    6,
                ),
            ),
        ],
        [
            CONCURRENCY("test_the_apply_job_has_its_own_group_that_never_cancels"),
            CONCURRENCY("test_no_job_that_holds_a_key_or_an_environment_or_applies_can_be_cancelled_by_a_group"),
        ],
    ),
    Case(
        "apply job group is shared with the plan job",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}
                    """,
                    6,
                ),
                indented(
                    r"""
                    group: ansible-plan-${{ github.repository }}
                    """,
                    6,
                ),
            ),
        ],
        [
            CONCURRENCY("test_the_apply_job_has_its_own_group_that_never_cancels"),
            CONCURRENCY("test_no_other_job_shares_the_apply_group"),
        ],
    ),
    Case(
        "apply job group dropped",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    concurrency:
                      group: ansible-apply-${{ github.repository }}-${{ inputs.environment }}
                      cancel-in-progress: false
                    """,
                    4,
                ),
                "",
            ),
        ],
        [CONCURRENCY("test_the_apply_job_has_its_own_group_that_never_cancels")],
    ),
    Case(
        "Molecule legs share one group",
        [
            Edit(
                GATED_APPLY,
                "-${{ inputs.environment }}-${{ matrix.name }}-${{ github.event_name",
                "-${{ inputs.environment }}-${{ github.event_name",
            ),
        ],
        [CONCURRENCY("test_a_molecule_leg_has_a_group_of_its_own_so_the_legs_do_not_cancel_each_other")],
    ),
    Case(
        "plan job cancels a running plan",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    group: ansible-plan-${{ github.repository }}
                    cancel-in-progress: false""",
                    6,
                ),
                indented(
                    r"""
                    group: ansible-plan-${{ github.repository }}
                    cancel-in-progress: true""",
                    6,
                ),
            ),
        ],
        [
            CONCURRENCY("test_plan_and_the_drift_check_share_one_group_that_never_cancels"),
            CONCURRENCY("test_no_job_that_holds_a_key_or_an_environment_or_applies_can_be_cancelled_by_a_group"),
        ],
    ),
    Case(
        "drift job leaves the plan group",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    group: ansible-plan-${{ github.repository }}
                    """,
                    6,
                ),
                indented(
                    r"""
                    group: ansible-drift-${{ github.repository }}
                    """,
                    6,
                ),
            ),
        ],
        [CONCURRENCY("test_plan_and_the_drift_check_share_one_group_that_never_cancels")],
    ),
    Case(
        "a workflow-level group is added",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    permissions:
                      contents: read

                    jobs:""",
                    0,
                ),
                indented(
                    r"""
                    permissions:
                      contents: read

                    concurrency:
                      group: ansible-${{ github.repository }}
                      cancel-in-progress: true

                    jobs:""",
                    0,
                ),
            ),
        ],
        [CONCURRENCY("test_neither_workflow_has_a_workflow_level_group")],
    ),
    Case(
        "lint cancels the delivery in flight (the setting before v1.2.4)",
        [
            Edit(
                GATED_APPLY,
                "-${{ inputs.environment }}-${{ github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork != true && 'delivery' || format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: false
                    defaults:
                      run:
                        working-directory: ${{ inputs.working-directory }}
                    steps:
                      - name: Checkout""",
                    4,
                ),
                "-${{ inputs.environment }}-${{ github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork != true && 'delivery' || format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: true
                    defaults:
                      run:
                        working-directory: ${{ inputs.working-directory }}
                    steps:
                      - name: Checkout""",
                    4,
                ),
            ),
        ],
        [
            CONCURRENCY(
                "test_no_job_of_the_gated_workflow_cancels_in_progress_and_each_check_has_the_group_pinned_here"
            ),
            DELIVERY_DOES_NOT_STARVE("test_a_second_push_to_main_cancels_nothing_that_has_started"),
        ],
    ),
    Case(
        "discover cancels the delivery in flight (the setting before v1.2.4)",
        [
            Edit(
                GATED_APPLY,
                "format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: false
                    outputs:""",
                    4,
                ),
                "format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: true
                    outputs:""",
                    4,
                ),
            ),
        ],
        [
            CONCURRENCY(
                "test_no_job_of_the_gated_workflow_cancels_in_progress_and_each_check_has_the_group_pinned_here"
            ),
            DELIVERY_DOES_NOT_STARVE("test_a_second_push_to_main_cancels_nothing_that_has_started"),
        ],
    ),
    Case(
        "a Molecule leg cancels the delivery in flight (the setting before v1.2.4)",
        [
            Edit(
                GATED_APPLY,
                "format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: false
                    defaults:
                      run:
                        working-directory: ${{ inputs.working-directory }}
                    env:""",
                    4,
                ),
                "format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: true
                    defaults:
                      run:
                        working-directory: ${{ inputs.working-directory }}
                    env:""",
                    4,
                ),
            ),
        ],
        [
            CONCURRENCY(
                "test_no_job_of_the_gated_workflow_cancels_in_progress_and_each_check_has_the_group_pinned_here"
            ),
            DELIVERY_DOES_NOT_STARVE("test_a_second_push_to_main_cancels_nothing_that_has_started"),
        ],
    ),
    Case(
        "a cancel-in-progress written as a string is not read as false",
        [
            Edit(
                GATED_APPLY,
                "format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: false
                    outputs:""",
                    4,
                ),
                "format('run-{0}-{1}', github.run_id, github.run_attempt) }}\n"
                + indented(
                    r"""
                      cancel-in-progress: 'true'
                    outputs:""",
                    4,
                ),
            ),
        ],
        [
            CONCURRENCY(
                "test_no_job_of_the_gated_workflow_cancels_in_progress_and_each_check_has_the_group_pinned_here"
            ),
            DELIVERY_DOES_NOT_STARVE("test_a_second_push_to_main_cancels_nothing_that_has_started"),
        ],
    ),
    Case(
        "a pull request run shares the delivery group of discover",
        [
            Edit(
                GATED_APPLY,
                "ansible-ci-discover-${{ github.repository }}-${{ inputs.environment }}-${{ github.event_name == 'push' && github.ref == format('refs/heads/{0}', github.event.repository.default_branch) && github.event.repository.fork != true && 'delivery' || format('run-{0}-{1}', github.run_id, github.run_attempt) }}",
                "ansible-ci-discover-${{ github.repository }}-${{ inputs.environment }}-delivery",
            ),
        ],
        [
            CONCURRENCY(
                "test_no_job_of_the_gated_workflow_cancels_in_progress_and_each_check_has_the_group_pinned_here"
            ),
            DELIVERY_DOES_NOT_STARVE(
                "test_a_run_that_is_not_a_delivery_is_in_a_group_of_its_own_so_the_checks_never_cancel_it"
            ),
        ],
    ),
]
