"""continue-past-failed-apply: one host's failed apply no longer hides the hosts after it, and the run stays red."""

from __future__ import annotations

from ..files import GATED_APPLY
from ..model import Case, Edit
from ..suites import APPLY, STRUCTURE
from ..text import indented

CONTINUING = "test_with_the_input_on_a_failed_apply_goes_on_to_the_remaining_hosts_names_the_failed_host_and_ends_red"

CASES: list[Case] = [
    Case(
        "continue-past-failed-apply: the input is ignored and the first failed host still ends the step",
        [
            Edit(
                GATED_APPLY,
                indented('if [ "$CONTINUE_PAST_FAILED_APPLY" != true ]; then', 1),
                indented('if true; then', 1),
            ),
        ],
        [APPLY(CONTINUING)],
    ),
    Case(
        "continue-past-failed-apply: a failed host is not counted and the job ends green",
        [Edit(GATED_APPLY, "failed=$((failed + 1))", "failed=$((failed + 0))")],
        [APPLY(CONTINUING)],
    ),
    Case(
        "continue-past-failed-apply: the job does not end red after the last host",
        [
            Edit(
                GATED_APPLY,
                indented('fail "$failed host(s) failed their apply: $failed_hosts.', 1),
                indented('echo "$failed host(s) failed their apply: $failed_hosts.', 1),
            ),
        ],
        [APPLY(CONTINUING)],
    ),
    Case(
        "continue-past-failed-apply: a failed host is not written to the step summary",
        [Edit(GATED_APPLY, "'Host %s: FAILED, %s\\n'", "'Host %s: %s\\n'")],
        [APPLY(CONTINUING)],
    ),
    Case(
        "continue-past-failed-apply: the failed host is not named in the closing error",
        [Edit(GATED_APPLY, 'failed_hosts="${failed_hosts:+$failed_hosts, }$i"', 'failed_hosts="${failed_hosts:+$failed_hosts, }"')],
        [APPLY("test_with_the_input_on_two_failed_hosts_are_both_named")],
    ),
    Case(
        "continue-past-failed-apply: the host's message is not printed to the log",
        [Edit(GATED_APPLY, """[ -z "$host_out" ] || printf '%s\\n' "$host_out\"""", "true")],
        [APPLY(CONTINUING)],
    ),
    Case(
        "continue-past-failed-apply: a host the guard did not list is applied",
        [
            Edit(
                GATED_APPLY,
                indented("*\" $i \"*) ;;", 1),
                indented("*) ;;", 1),
            ),
        ],
        [APPLY("test_with_the_input_on_a_host_whose_plan_failed_is_still_never_asked")],
    ),
    Case(
        "continue-past-failed-apply: the input is accepted whatever it says",
        [
            Edit(
                GATED_APPLY,
                'case "$CONTINUE_PAST_FAILED_APPLY" in true | false) ;; *) fail "continue-past-failed-apply must be true or false." ;; esac',
                'true',
            ),
        ],
        [APPLY("test_the_continue_input_must_be_true_or_false")],
    ),
    Case(
        "continue-past-failed-apply: the input defaults to true",
        [
            Edit(
                GATED_APPLY,
                "across hosts, because a host after a failed one then runs without it.\n        type: boolean\n        required: false\n        default: false",
                "across hosts, because a host after a failed one then runs without it.\n        type: boolean\n        required: false\n        default: true",
            ),
        ],
        [APPLY("test_the_continue_input_is_declared_boolean_and_off_by_default")],
    ),
    Case(
        "continue-past-failed-apply: the step is not given the input",
        [Edit(GATED_APPLY, "CONTINUE_PAST_FAILED_APPLY: ${{ inputs.continue-past-failed-apply }}", "CONTINUE_PAST_FAILED_APPLY: 'false'")],
        [STRUCTURE("test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source")],
    ),
]
