"""The proof of the jump on a real sshd (tools/ssh_jump_proof.py) and the workflow that runs it."""

from __future__ import annotations

from ..files import JUMP_PROOF, JUMP_SELF_TEST
from ..model import Case, Edit
from ..suites import JUMP_PROOF_FIXTURE, JUMP_PROOF_LINE, JUMP_PROOF_LOG
from ..text import indented

CASES: list[Case] = [
    Case(
        "jump proof line permits any destination",
        [
            Edit(
                JUMP_PROOF,
                "*(f'permitopen=\"{d.host}:{d.port}\"' for d in destinations)",
                "*('permitopen=\"*:*\"' for d in destinations)",
            ),
        ],
        [
            JUMP_PROOF_LINE("test_the_line_is_restrict_then_port_forwarding_one_permitopen_and_a_forced_command"),
            JUMP_PROOF_LINE("test_permitopen_is_exactly_one_destination_with_no_wildcard_and_no_list"),
        ],
    ),
    Case(
        "jump proof line drops the forced command",
        [
            Edit(
                JUMP_PROOF,
                indented("if forced_command is not None:", 4),
                indented("if forced_command is None:", 4),
            ),
        ],
        [JUMP_PROOF_LINE("test_the_line_carries_a_forced_command_unless_the_caller_asks_for_none")],
    ),
    Case(
        "jump proof line accepts a wildcard or a list as the host",
        [
            Edit(
                JUMP_PROOF,
                'ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")',
                'ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9.*,-]*$")',
            ),
        ],
        [JUMP_PROOF_LINE("test_a_wildcard_a_list_or_an_injected_option_is_refused_before_it_reaches_the_line")],
    ),
    Case(
        "jump proof line accepts a port out of range",
        [
            Edit(
                JUMP_PROOF,
                indented("if not 1 <= destination.port <= 65535:", 8),
                indented("if not 0 <= destination.port <= 70000:", 8),
            ),
        ],
        [JUMP_PROOF_LINE("test_a_wildcard_a_list_or_an_injected_option_is_refused_before_it_reaches_the_line")],
    ),
    Case(
        "jump proof counts a denied forward as permitted",
        [
            Edit(
                JUMP_PROOF,
                indented(
                    r"""
                    remaining.subtract(Counter(denied_forwards(log)))
                    """,
                    4,
                ),
                "",
            ),
        ],
        [JUMP_PROOF_LOG("test_a_forward_is_permitted_only_when_it_was_requested_and_not_denied")],
    ),
    Case(
        "jump proof gives the documented gateway account a login shell and no forced command",
        [
            Edit(
                JUMP_PROOF,
                "GatewayAccount(JUMP_USER, NO_SHELL, True, False,",
                "GatewayAccount(JUMP_USER, LOGIN_SHELL, False, False,",
            ),
        ],
        [
            JUMP_PROOF_LINE(
                "test_the_documented_account_has_no_shell_and_a_forced_command_and_only_the_control_runs_commands"
            ),
        ],
    ),
    Case(
        "jump proof target line is not restricted",
        [
            Edit(
                JUMP_PROOF,
                "return f'restrict,command=\"{command_path}\" {public_key}'",
                "return f'command=\"{command_path}\" {public_key}'",
            ),
        ],
        [JUMP_PROOF_LINE("test_the_target_line_forces_the_one_command_and_restricts_the_key")],
    ),
    Case(
        "jump proof stand-in target answers in a form the report step rejects",
        [Edit(JUMP_PROOF, "playbook=site.yml changed=0", "playbook=site.yml changed=x")],
        [JUMP_PROOF_FIXTURE("test_the_stand_in_target_prints_a_summary_line_the_report_step_reads_as_clean")],
    ),
    Case(
        "jump self-test workflow stops running on a change to the proof",
        [
            Edit(
                JUMP_SELF_TEST,
                indented(
                    r"""
                    - tools/ssh_jump_proof.py
                    """,
                    6,
                ),
                "",
                count=2,
            ),
        ],
        [JUMP_PROOF_FIXTURE("test_the_self_test_workflow_runs_on_a_change_to_what_it_proves_and_every_week")],
    ),
    Case(
        "jump self-test workflow loses its weekly run",
        [
            Edit(
                JUMP_SELF_TEST,
                indented(
                    r"""
                    schedule:
                      - cron: '17 4 * * 1'
                    """,
                    2,
                ),
                "",
            ),
        ],
        [JUMP_PROOF_FIXTURE("test_the_self_test_workflow_runs_on_a_change_to_what_it_proves_and_every_week")],
    ),
    Case(
        "jump self-test workflow job loses its timeout",
        [
            Edit(
                JUMP_SELF_TEST,
                indented(
                    r"""
                    timeout-minutes: 10
                    """,
                    4,
                ),
                "",
            ),
        ],
        [JUMP_PROOF_FIXTURE("test_the_self_test_workflow_runs_on_a_change_to_what_it_proves_and_every_week")],
    ),
    Case(
        "jump self-test workflow does not run the proof",
        [
            Edit(
                JUMP_SELF_TEST,
                "run: python3 tools/ssh_jump_proof.py",
                "run: python3 tools/ssh_jump_proof.py --help",
            ),
        ],
        [JUMP_PROOF_FIXTURE("test_the_self_test_workflow_runs_on_a_change_to_what_it_proves_and_every_week")],
    ),
]
