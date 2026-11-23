"""The push route: hosts that are sent a bundle and a command, not asked by a trigger they run."""

from __future__ import annotations

from ..files import ACTION, DRIFT_CHECK, GATED_APPLY
from ..model import Case, Edit
from ..suites import PUSH_APPLY, PUSH_APPLY_GUARD, PUSH_MAIN_TIP, PUSH_OFF, PUSH_PARITY, PUSH_PLAN, PUSH_RELEASE_TAG, PUSH_VALIDATE, STRUCTURE
from ..text import indented

CASES: list[Case] = [
    # The push route (README, "Pushed hosts"): each control of the sender, broken once.
    Case(
        "push: the route is ignored and a pulled host is judged as a pushed one",
        [Edit(ACTION, 'route=$(cat "$base.route")', "route=push")],
        [
            PUSH_PLAN("test_a_pulled_host_that_answers_with_the_line_of_the_push_receiver_is_refused"),
            PUSH_PLAN("test_the_real_stand_ins_refuse_the_wrong_line_in_both_directions"),
        ],
    ),
    Case(
        "push: the route is ignored and a pushed host is judged as a pulled one",
        [Edit(ACTION, 'route=$(cat "$base.route")', "route=pull")],
        [
            PUSH_PLAN("test_a_pushed_host_that_answers_with_the_line_of_the_pull_trigger_is_refused"),
            PUSH_PLAN("test_the_real_stand_ins_refuse_the_wrong_line_in_both_directions"),
        ],
    ),
    Case(
        "push: the route file is never written",
        [Edit(ACTION, 'printf \'%s\\n\' "$route" > "$plan_dir/$n.route"', ":")],
        [
            PUSH_PLAN("test_a_clean_pushed_host_and_a_clean_pulled_host_give_no_changes"),
            PUSH_OFF(
                "test_with_no_push_list_the_plan_and_apply_calls_are_byte_for_byte_the_pull_calls_with_nothing_on_stdin"
            ),
        ],
    ),
    Case(
        "push: the action accepts a push line for another mode",
        [Edit(ACTION, indented('&& [ "${BASH_REMATCH[4]}" = check ]; then', 1), "; then")],
        [PUSH_PLAN("test_a_push_line_for_another_mode_commit_or_exit_status_or_with_more_lines_is_refused")],
    ),
    Case(
        "push: the apply job accepts a push line for another mode",
        [Edit(GATED_APPLY, indented('&& [ "${BASH_REMATCH[4]}" = apply ]; then', 1), "; then")],
        [PUSH_APPLY("test_a_push_line_for_the_check_mode_another_commit_or_exit_or_a_second_change_fails_the_job")],
    ),
    Case(
        "push: the apply job judges a pushed host by the pull trigger's line",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    if [ "$route" = push ]; then
                      if [[ "$line" =~ $push_re ]]""",
                    14,
                ),
                indented(
                    r"""
                    if [ "$route" = never ]; then
                      if [[ "$line" =~ $push_re ]]""",
                    14,
                ),
            ),
        ],
        [PUSH_APPLY("test_a_pushed_host_that_answers_with_the_line_of_the_pull_trigger_is_refused")],
    ),
    Case(
        "push: the apply job judges a pulled host by the push receiver's line",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    if [ "$route" = push ]; then
                      if [[ "$line" =~ $push_re ]]""",
                    14,
                ),
                indented(
                    r"""
                    if true; then
                      if [[ "$line" =~ $push_re ]]""",
                    14,
                ),
            ),
        ],
        [PUSH_APPLY("test_a_pulled_host_that_answers_with_the_line_of_the_push_receiver_is_refused")],
    ),
    Case(
        "push: the plan sends the pull form to a pushed host",
        [
            Edit(
                ACTION,
                '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
                '-- "$dest" "$SHA" < "$push_bundle"',
            ),
        ],
        [
            PUSH_PLAN(
                "test_a_pushed_host_gets_check_the_commit_the_playbook_and_the_bundle_and_a_pulled_host_gets_the_commit_alone"
            ),
        ],
    ),
    Case(
        "push: the plan sends apply to a pushed host",
        [
            Edit(
                ACTION,
                '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
                '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
            ),
        ],
        [PUSH_PLAN("test_the_plan_never_sends_apply_and_every_pushed_host_reads_the_same_bundle_from_its_start")],
    ),
    Case(
        "push: the plan sends no bundle to a pushed host",
        [
            Edit(
                ACTION,
                '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
                '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < /dev/null',
            ),
        ],
        [
            PUSH_PLAN(
                "test_a_pushed_host_gets_check_the_commit_the_playbook_and_the_bundle_and_a_pulled_host_gets_the_commit_alone"
            ),
        ],
    ),
    Case(
        "push: the apply job sends the pull form to a pushed host",
        [
            Edit(
                GATED_APPLY,
                '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
                '-- "$dest" "$SHA" < "$push_bundle"',
            ),
        ],
        [PUSH_APPLY("test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run")],
    ),
    Case(
        "push: the apply job sends check to a pushed host",
        [
            Edit(
                GATED_APPLY,
                '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
                '-- "$dest" "check $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
            ),
        ],
        [PUSH_APPLY("test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run")],
    ),
    Case(
        "push: the apply job sends no bundle to a pushed host",
        [
            Edit(
                GATED_APPLY,
                '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < "$push_bundle"',
                '-- "$dest" "apply $SHA $PUSH_PLAYBOOK" < /dev/null',
            ),
        ],
        [PUSH_APPLY("test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run")],
    ),
    Case(
        "push: the bundle drops the history",
        [Edit(ACTION, 'bundle create "$file" "${refs[@]}"', 'bundle create "$file" HEAD~1..HEAD')],
        [
            PUSH_PLAN(
                "test_a_pushed_host_gets_check_the_commit_the_playbook_and_the_bundle_and_a_pulled_host_gets_the_commit_alone"
            ),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the apply job's bundle drops the history",
        [Edit(GATED_APPLY, 'bundle create "$file" "${refs[@]}"', 'bundle create "$file" HEAD~1..HEAD')],
        [
            PUSH_APPLY("test_a_pushed_host_gets_apply_the_commit_the_playbook_and_a_fresh_whole_bundle_for_each_run"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the action bundles a shallow checkout",
        [
            Edit(
                ACTION,
                '|| fail "The checkout of the repository is shallow, so its bundle would not carry the history the host needs."',
                "|| true",
            ),
        ],
        [
            PUSH_PLAN(
                "test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked"
            ),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the apply job bundles a shallow checkout",
        [
            Edit(
                GATED_APPLY,
                '|| fail "The checkout of the repository is shallow, so its bundle would not carry the history the host needs."',
                "|| true",
            ),
        ],
        [
            PUSH_APPLY(
                "test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked"
            ),
        ],
    ),
    Case(
        "push: the action bundles a checkout that is not at the commit",
        [
            Edit(
                ACTION,
                '[ "$head" = "$sha" ] || fail "The checkout of the repository is not at the commit being delivered."',
                ":",
            ),
        ],
        [
            PUSH_PLAN(
                "test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked"
            ),
        ],
    ),
    Case(
        "push: the apply job bundles a checkout that is not at the commit",
        [
            Edit(
                GATED_APPLY,
                '[ "$head" = "$sha" ] || fail "The checkout of the repository is not at the commit being delivered."',
                ":",
            ),
        ],
        [
            PUSH_APPLY(
                "test_a_shallow_checkout_a_head_that_is_not_the_commit_and_a_directory_that_is_no_repository_are_refused_before_any_host_is_asked"
            ),
        ],
    ),
    Case(
        "push: the action pushes from a repository that is not public",
        [
            Edit(
                ACTION,
                '[ "$PUSH_REPO_PRIVATE" = false ] || fail "Pushing refuses unless the repository is public: a bundle carries its whole history to the host."',
                ":",
            ),
        ],
        [PUSH_VALIDATE("test_a_repository_that_is_not_known_to_be_public_is_refused")],
    ),
    Case(
        "push: the apply guard pushes from a repository that is not public",
        [
            Edit(
                GATED_APPLY,
                '[ "$PUSH_REPO_PRIVATE" = false ] || fail "Pushing refuses unless the repository is public: a bundle carries its whole history to the host."',
                ":",
            ),
        ],
        [PUSH_APPLY_GUARD("test_the_push_playbook_and_the_public_repository_are_checked_again_in_the_apply_job")],
    ),
    Case(
        "push: the action takes a push host that is not a listed host",
        [Edit(ACTION, 'grep -qxF -- "$dest" <<< "$HOSTS" || fail "A push host is not in the hosts list."', ":")],
        [PUSH_VALIDATE("test_a_push_host_that_is_not_a_listed_host_a_duplicate_or_a_malformed_line_is_refused")],
    ),
    Case(
        "push: the action takes a push host listed twice",
        [Edit(ACTION, 'case " $push_list " in *" $dest "*) fail "A push host is listed twice." ;; esac', ":")],
        [PUSH_VALIDATE("test_a_push_host_that_is_not_a_listed_host_a_duplicate_or_a_malformed_line_is_refused")],
    ),
    Case(
        "push: the apply guard does not compare the push list with the plan",
        [
            Edit(
                GATED_APPLY,
                '[ "$push_digest" = "$PLANNED_PUSH_DIGEST" ] || fail "The pushed hosts are not the pushed hosts that were planned."',
                ":",
            ),
        ],
        [
            PUSH_APPLY_GUARD(
                "test_a_push_list_the_plan_did_not_have_a_list_the_plan_had_and_a_different_list_are_refused"
            ),
        ],
    ),
    Case(
        "push: the apply guard takes an empty planned push digest for a push list",
        [
            Edit(
                GATED_APPLY,
                "printf '%s' \"$PLANNED_PUSH_DIGEST\" | grep -Eq '^[0-9a-f]{64}$' || fail \"The planned push digest is missing.\"",
                ":",
            ),
        ],
        [PUSH_APPLY_GUARD("test_a_push_list_with_no_host_in_it_is_refused_whatever_the_plan_said")],
    ),
    Case(
        "push: the push digest is joined with a comma in the action",
        [Edit(ACTION, 'push_list="${push_list:+$push_list }$dest"', 'push_list="${push_list:+$push_list,}$dest"')],
        [PUSH_APPLY_GUARD("test_the_push_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes")],
    ),
    Case(
        "push: the push digest is joined with a comma in the apply guard",
        [
            Edit(
                GATED_APPLY,
                'push_list="${push_list:+$push_list }$dest"',
                'push_list="${push_list:+$push_list,}$dest"',
            ),
        ],
        [PUSH_APPLY_GUARD("test_the_push_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes")],
    ),
    Case(
        "push: the action does not require the jump host for a push",
        [
            Edit(
                ACTION,
                '{ [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "push-hosts needs jump-host and jump-key: a pushed host is reached only through the jump host."',
                ":",
            ),
        ],
        [PUSH_VALIDATE("test_a_push_needs_the_jump_host_and_its_key")],
    ),
    Case(
        "push: the apply job does not require the jump host for a push",
        [
            Edit(
                GATED_APPLY,
                '{ [ -n "$JUMP_HOST" ] && [ -n "$JUMP_KEY" ]; } || fail "ssh_push_hosts needs ssh_jump_host and ssh_jump_key: a pushed host is reached only through the jump host."',
                ":",
            ),
        ],
        [PUSH_APPLY("test_a_push_needs_the_jump_host_and_its_key_and_asks_no_host_without_them")],
    ),
    Case(
        "push: the action takes a playbook with a parent directory in it",
        [Edit(ACTION, 'case "$PUSH_PLAYBOOK" in *..*) fail "push-playbook must not contain \'..\'." ;; esac', ":")],
        [PUSH_VALIDATE("test_the_push_playbook_must_be_a_relative_path_of_plain_characters_with_no_dotdot")],
    ),
    Case(
        "push: the action takes any playbook text",
        [
            Edit(
                ACTION,
                '[[ "$PUSH_PLAYBOOK" =~ $playbook_re ]] || fail "push-playbook must be a relative path of letters, digits, dot, underscore, dash and slash."',
                ":",
            ),
        ],
        [PUSH_VALIDATE("test_the_push_playbook_must_be_a_relative_path_of_plain_characters_with_no_dotdot")],
    ),
    Case(
        "push: the apply guard takes a playbook with a parent directory in it",
        [
            Edit(
                GATED_APPLY,
                'case "$PUSH_PLAYBOOK" in *..*) fail "push-playbook must not contain \'..\'." ;; esac',
                ":",
            ),
        ],
        [PUSH_APPLY_GUARD("test_the_push_playbook_and_the_public_repository_are_checked_again_in_the_apply_job")],
    ),
    Case(
        "push: the apply guard takes any playbook text",
        [
            Edit(
                GATED_APPLY,
                '[[ "$PUSH_PLAYBOOK" =~ $playbook_re ]] || fail "push-playbook must be a relative path of letters, digits, dot, underscore, dash and slash."',
                ":",
            ),
        ],
        [PUSH_APPLY_GUARD("test_the_push_playbook_and_the_public_repository_are_checked_again_in_the_apply_job")],
    ),
    Case(
        "push: the push decision of the plan job reads no push list",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    env:
                      PUSH_HOSTS: ${{ secrets.ssh_push_hosts }}
                    run: |""",
                    8,
                ),
                indented(
                    r"""
                    env:
                      PUSH_HOSTS: ''
                    run: |""",
                    8,
                ),
            ),
        ],
        [STRUCTURE("test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source")],
    ),
    Case(
        "push: the plan job checks out the whole history on every run",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    if: steps.push.outputs.push == 'true'
                    """,
                    8,
                ),
                indented(
                    r"""
                    if: always()
                    """,
                    8,
                ),
            ),
        ],
        [
            STRUCTURE(
                "test_the_drift_job_checks_out_the_caller_only_for_a_push_and_the_plan_job_only_in_a_dry_run_or_for_a_push"
            ),
            PUSH_OFF("test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it"),
        ],
    ),
    Case(
        "push: the apply job checks out the whole history on every run",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    if: steps.guard.outputs.push == 'true'
                    """,
                    8,
                ),
                indented(
                    r"""
                    if: always()
                    """,
                    8,
                ),
            ),
        ],
        [
            STRUCTURE("test_the_apply_job_steps_are_the_guard_then_the_apply_with_no_if_but_the_push_checkout"),
            PUSH_OFF("test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it"),
        ],
    ),
    Case(
        "push: the drift job checks out the whole history on every run",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    if: steps.push.outputs.push == 'true'
                    """,
                    8,
                ),
                indented(
                    r"""
                    if: always()
                    """,
                    8,
                ),
            ),
        ],
        [
            STRUCTURE(
                "test_the_drift_job_checks_out_the_caller_only_for_a_push_and_the_plan_job_only_in_a_dry_run_or_for_a_push"
            ),
            PUSH_OFF("test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it"),
        ],
    ),
    Case(
        "push: the push checkout is shallow",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    fetch-depth: 0
                    """,
                    10,
                ),
                indented(
                    r"""
                    fetch-depth: 1
                    """,
                    10,
                ),
            ),
        ],
        [
            STRUCTURE(
                "test_the_drift_job_checks_out_the_caller_only_for_a_push_and_the_plan_job_only_in_a_dry_run_or_for_a_push"
            ),
        ],
    ),
    Case(
        "push: the drift job does not pass the push list",
        [
            Edit(
                DRIFT_CHECK,
                indented(
                    r"""
                    push-hosts: ${{ secrets.ssh_push_hosts }}
                    """,
                    10,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies")],
    ),
    Case(
        "push: the plan job does not pass the public-repository value",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    push-repo-private: ${{ github.event.repository.private }}
                    """,
                    10,
                ),
                "",
            ),
        ],
        [STRUCTURE("test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs")],
    ),
    Case(
        "push: the plan job's push digest is read from the wrong step",
        [
            Edit(
                GATED_APPLY,
                "push_hosts_digest: ${{ steps.ssh.outputs.push-hosts-digest }}",
                "push_hosts_digest: ${{ steps.guard.outputs.push-hosts-digest }}",
            ),
        ],
        [STRUCTURE("test_the_plan_job_outputs_come_from_the_steps_that_make_them")],
    ),
    Case(
        "push: the push receiver's line differs in the apply job",
        [
            Edit(
                GATED_APPLY,
                "push_re='^push-receiver verdict=([a-z-]+) exit=([0-9]+) reason=([a-z-]+) mode=(check|apply)",
                "push_re='^push-receiver verdict=([a-z-]*) exit=([0-9]+) reason=([a-z-]+) mode=(check|apply)",
            ),
        ],
        [PUSH_PARITY("test_the_push_line_is_the_contract_and_the_same_in_the_action_and_in_the_apply_job")],
    ),
    Case(
        "push: the old pull path gains a step that runs without a push list",
        [
            Edit(
                GATED_APPLY,
                indented(
                    r"""
                    - name: Decide whether any host is pushed to
                      id: push
                    """,
                    6,
                ),
                indented(
                    r"""
                    - name: Decide whether any host is pushed to
                      id: push
                      if: always()
                    """,
                    6,
                ),
            ),
        ],
        [PUSH_OFF("test_every_step_the_push_route_adds_is_conditional_on_the_push_decision_or_only_makes_it")],
    ),
    Case(
        "push: the action leaves the release tags out of the bundle",
        [Edit(ACTION, 'bundle create "$file" "${refs[@]}"', 'bundle create "$file" HEAD')],
        [
            PUSH_RELEASE_TAG("test_a_signed_release_tag_that_names_the_commit_is_in_the_bundle_and_the_receiver_admits_it"),
            PUSH_RELEASE_TAG("test_a_tag_that_is_there_but_is_not_put_in_the_bundle_is_refused_by_the_receiver"),
            PUSH_RELEASE_TAG("test_every_release_tag_that_names_the_commit_is_sent_and_only_those"),
            PUSH_RELEASE_TAG("test_the_plan_step_sends_the_release_tag_in_the_bundle_to_a_pushed_host"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the action sends a release tag that names another commit",
        [Edit(ACTION, ' && [ "$target" = "$sha" ]', "")],
        [
            PUSH_RELEASE_TAG("test_every_release_tag_that_names_the_commit_is_sent_and_only_those"),
            PUSH_RELEASE_TAG("test_a_tag_for_another_commit_does_not_admit_this_one"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the action sends every tag, not only those under release/",
        [Edit(ACTION, "%(refname)' refs/tags/release/)", "%(refname)' refs/tags/)")],
        [
            PUSH_RELEASE_TAG("test_every_release_tag_that_names_the_commit_is_sent_and_only_those"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the apply job leaves the release tags out of the bundle",
        [Edit(GATED_APPLY, 'bundle create "$file" "${refs[@]}"', 'bundle create "$file" HEAD')],
        [
            PUSH_RELEASE_TAG("test_a_signed_release_tag_that_names_the_commit_is_in_the_bundle_and_the_receiver_admits_it"),
            PUSH_RELEASE_TAG("test_a_tag_that_is_there_but_is_not_put_in_the_bundle_is_refused_by_the_receiver"),
            PUSH_RELEASE_TAG("test_every_release_tag_that_names_the_commit_is_sent_and_only_those"),
            PUSH_RELEASE_TAG("test_the_apply_step_sends_the_release_tag_in_the_bundle_to_a_pushed_host_on_both_runs"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the apply job sends a release tag that names another commit",
        [Edit(GATED_APPLY, ' && [ "$target" = "$sha" ]', "")],
        [
            PUSH_RELEASE_TAG("test_every_release_tag_that_names_the_commit_is_sent_and_only_those"),
            PUSH_RELEASE_TAG("test_a_tag_for_another_commit_does_not_admit_this_one"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the apply job sends every tag, not only those under release/",
        [Edit(GATED_APPLY, "%(refname)' refs/tags/release/)", "%(refname)' refs/tags/)")],
        [
            PUSH_RELEASE_TAG("test_every_release_tag_that_names_the_commit_is_sent_and_only_those"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the action leaves the tip of main out of the bundle",
        [Edit(ACTION, "refs+=(refs/remotes/origin/main)", "true")],
        [
            PUSH_MAIN_TIP("test_the_bundle_carries_the_tip_of_main_under_its_own_name_and_the_receiver_finds_the_commit_on_it"),
            PUSH_MAIN_TIP("test_a_bundle_of_the_commit_alone_is_refused_by_the_receiver_which_is_what_the_sender_did_before"),
            PUSH_MAIN_TIP("test_when_main_has_moved_on_the_tip_sent_is_the_tip_the_checkout_holds_and_the_commit_is_behind_it"),
            PUSH_MAIN_TIP("test_the_plan_step_sends_the_tip_of_main_in_the_bundle_to_a_pushed_host"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the apply job leaves the tip of main out of the bundle",
        [Edit(GATED_APPLY, "refs+=(refs/remotes/origin/main)", "true")],
        [
            PUSH_MAIN_TIP("test_the_bundle_carries_the_tip_of_main_under_its_own_name_and_the_receiver_finds_the_commit_on_it"),
            PUSH_MAIN_TIP("test_the_apply_step_sends_the_tip_of_main_in_the_bundle_to_a_pushed_host_on_both_runs"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the action makes a bundle when the checkout holds no tip of main",
        [Edit(ACTION, 'rev-parse --verify --quiet "refs/remotes/origin/main^{commit}"', "rev-parse --verify --quiet HEAD")],
        [
            PUSH_MAIN_TIP("test_a_checkout_that_holds_no_tip_of_main_is_refused_before_a_bundle_is_made"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
    Case(
        "push: the apply job makes a bundle when the checkout holds no tip of main",
        [Edit(GATED_APPLY, 'rev-parse --verify --quiet "refs/remotes/origin/main^{commit}"', "rev-parse --verify --quiet HEAD")],
        [
            PUSH_MAIN_TIP("test_a_checkout_that_holds_no_tip_of_main_is_refused_before_a_bundle_is_made"),
            PUSH_PARITY("test_the_bundle_snippet_is_the_same_text_in_the_action_and_in_the_apply_job"),
        ],
    ),
]
