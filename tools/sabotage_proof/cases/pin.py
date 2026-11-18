"""The pin of the shared actions: the commit a signed tag peels to, and what the checkout must hold."""

from __future__ import annotations

from ..files import DRIFT_CHECK, GATED_APPLY
from ..model import Case, Edit
from ..suites import PIN

CASES: list[Case] = [
    Case(
        "pin accepts any non-empty value",
        [
            Edit(GATED_APPLY, '[[ "$pinned" =~ $sha_re ]] || fail', '[ -n "$pinned" ] || fail'),
            Edit(DRIFT_CHECK, '[[ "$pinned" =~ $sha_re ]] || fail', '[ -n "$pinned" ] || fail'),
        ],
        [PIN("test_no_commit_two_commits_or_a_commit_of_another_repository_is_refused_and_never_falls_back")],
    ),
    Case(
        "pin publishes the tag object instead of the commit it peels to",
        [
            Edit(GATED_APPLY, 'commit="$peeled"', 'commit="$pinned"'),
            Edit(DRIFT_CHECK, 'commit="$peeled"', 'commit="$pinned"'),
        ],
        [PIN("test_a_signed_tag_object_is_peeled_to_its_commit_which_is_published_and_both_are_logged")],
    ),
    Case(
        "pin accepts a tag that is not verified",
        [
            Edit(GATED_APPLY, '[ "$verified" = true ] || fail', '[ -n "$verified" ] || fail'),
            Edit(DRIFT_CHECK, '[ "$verified" = true ] || fail', '[ -n "$verified" ] || fail'),
        ],
        [
            PIN(
                "test_every_answer_that_is_not_a_verified_tag_of_a_commit_or_a_plain_commit_is_refused_and_publishes_nothing"
            ),
        ],
    ),
    Case(
        "pin accepts a lightweight tag",
        [
            Edit(GATED_APPLY, '[ -z "$tag_ref" ] || fail', ": || fail"),
            Edit(DRIFT_CHECK, '[ -z "$tag_ref" ] || fail', ": || fail"),
        ],
        [PIN("test_a_lightweight_tag_is_refused_and_the_commit_read_is_never_made")],
    ),
    Case(
        "pin accepts a lightweight tag (gated apply only)",
        [Edit(GATED_APPLY, '[ -z "$tag_ref" ] || fail', ": || fail")],
        [PIN("test_a_lightweight_tag_is_refused_and_the_commit_read_is_never_made")],
    ),
    Case(
        "pin accepts a lightweight tag (drift check only)",
        [Edit(DRIFT_CHECK, '[ -z "$tag_ref" ] || fail', ": || fail")],
        [PIN("test_a_lightweight_tag_is_refused_and_the_commit_read_is_never_made")],
    ),
    Case(
        "pin accepts two or more commits",
        [
            Edit(GATED_APPLY, '[ "$count" -eq 1 ] ||', '[ "$count" -ge 1 ] ||'),
            Edit(DRIFT_CHECK, '[ "$count" -eq 1 ] ||', '[ "$count" -ge 1 ] ||'),
        ],
        [PIN("test_no_commit_two_commits_or_a_commit_of_another_repository_is_refused_and_never_falls_back")],
    ),
    Case(
        "verify step checks only that HEAD is non-empty",
        [
            Edit(GATED_APPLY, '[ "$head" = "$PINNED_SHA" ] || fail', '[ -n "$head" ] || fail'),
            Edit(DRIFT_CHECK, '[ "$head" = "$PINNED_SHA" ] || fail', '[ -n "$head" ] || fail'),
        ],
        [PIN("test_the_checkout_must_be_exactly_the_pinned_commit_and_hold_the_action")],
    ),
    Case(
        "existence check pointed at another path",
        [
            Edit(GATED_APPLY, '[ -e "$GITHUB_WORKSPACE/.gw" ]', '[ -e "$GITHUB_WORKSPACE/.gwx" ]'),
            Edit(DRIFT_CHECK, '[ -e "$GITHUB_WORKSPACE/.gw" ]', '[ -e "$GITHUB_WORKSPACE/.gwx" ]'),
        ],
        [PIN("test_a_path_the_caller_already_holds_is_refused")],
    ),
]
