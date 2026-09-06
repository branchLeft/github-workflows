#!/usr/bin/env bash
# Resolves which open PR (from a `gh pr list --json ...` array) a
# `workflow_run` event is reporting about, and prints that PR's JSON object
# on stdout.
#
# `workflow_run.head_sha` is the commit that TRIGGERED the parent workflow
# run — for `graphify.yml`, an ordinary push to the default branch — never a
# commit the run goes on to create on the `graphify` branch. graphify.yml's
# `pull-request` publish mode force-rebuilds that branch from exactly that
# triggering commit on every run (see graphify.yml's "Seed the graph"/"Publish
# graph" steps), so the invariant this relies on is: the sha a workflow_run
# event reports always equals the graph PR's `baseRefOid`, never its
# `headRefOid`. Matching on `headRefOid` here would silently match nothing on
# every real graph PR, ever — which is exactly the defect this script exists
# to keep from recurring: the eligibility checks in graph-pr-check.sh only
# ever run against a PR this script actually found.
#
# Usage:
#   graph-pr-resolve.sh <sha> < pr-list.json
#
# `pr-list.json` is the array `gh pr list --json
# number,title,author,headRefName,headRefOid,baseRefName,baseRefOid,state,isCrossRepository,files`
# returns. Prints the matched PR object and exits 0, or prints nothing and
# exits 1 when no open PR's `baseRefOid` matches `<sha>`. Exit 2 means the
# input could not be read as a pull-request list at all -- a distinct outcome
# from exit 1, because "I found nothing" and "I could not look" must not be
# reported to a pull request as the same thing.

set -uo pipefail

SHA="${1:-}"
if [ -z "$SHA" ]; then
  echo "graph-pr-resolve: usage: graph-pr-resolve.sh <sha> < pr-list.json" >&2
  exit 2
fi

LIST=$(cat)
echo "$LIST" | jq -e . >/dev/null 2>&1 || {
  echo "graph-pr-resolve: input is not valid JSON" >&2
  exit 2
}

# Valid JSON is not enough. A failing `gh pr list` returns a well-formed
# object -- {"message":"Bad credentials"} -- which parses, then makes the
# select below fail at runtime with an empty result. That is indistinguishable
# from "no open PR matches", so the caller reports "superseded or already
# merged" and goes green having checked nothing. Reject anything that is not
# an array here, so the caller sees an error it can report as one.
echo "$LIST" | jq -e 'type == "array"' >/dev/null 2>&1 || {
  echo "graph-pr-resolve: input is not a JSON array of pull requests" >&2
  exit 2
}

MATCH=$(echo "$LIST" | jq -c --arg sha "$SHA" \
  '[.[] | select(.state == "OPEN" and .baseRefOid == $sha)] | .[0] // empty')

[ -n "$MATCH" ] || exit 1
printf '%s\n' "$MATCH"
exit 0
