#!/usr/bin/env bash
# Resolves the open graph PR a completed `graphify` run belongs to, evaluates
# it with graph-pr-check.sh, and posts the verdict as a commit status.
#
# The two shas involved are different commits, and conflating them is the
# defect this script exists to prevent:
#
#   $SHA  — `workflow_run.head_sha`, the commit on the default branch that
#           TRIGGERED the parent graphify run. It is the PR's `baseRefOid`,
#           which is why graph-pr-resolve.sh matches on that field. It is
#           never a commit on the `graphify` branch.
#   head  — the resolved PR's `headRefOid`. GitHub evaluates a PR's checks
#           against its head commit only, so this is the sole sha a status
#           can be posted to and be seen on the PR.
#
# Posting to $SHA put a green status on the default-branch merge commit and
# left every graph PR with zero statuses and zero check-runs, which is how
# they went on being merged with `--admin` while appearing to be checked.
#
# Environment:
#   REPO     owner/name of the repo being checked (required)
#   SHA      the completed run's head_sha (required)
#   CONTEXT  commit status context name (default: graph-pr-check)
#
# Exits 0 when a status was posted, and also when no open graph PR resolves
# at $SHA — a superseded or already-merged PR is not a failure. Exits
# non-zero only when it could not do its job at all. The check's own verdict
# is carried by the status, not by this script's exit code, because the job
# running it is a `workflow_run` job whose conclusion appears on no PR — so
# a red job here means "no verdict was reached", never "the PR failed".

set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"

: "${REPO:?graph-pr-report: REPO is required}"
: "${SHA:?graph-pr-report: SHA is required}"
CONTEXT="${CONTEXT:-graph-pr-check}"

PR_LIST=$(gh pr list --repo "$REPO" --head graphify --state open \
  --json number,title,author,headRefName,headRefOid,baseRefName,baseRefOid,state,isCrossRepository,files) || {
  echo "graph-pr-report: could not list open PRs on ${REPO}" >&2
  exit 1
}

PR_JSON=$(printf '%s' "$PR_LIST" | "$HERE/graph-pr-resolve.sh" "$SHA")
RESOLVE_RC=$?

# Exit 1 is the resolver's "no open PR matches this sha", which is an ordinary
# outcome — the PR was superseded by a newer force-rebuild, or already merged.
# Every other non-zero means it could not answer at all: exit 2 for JSON it
# could not parse (a `gh pr list` that returned an error object rather than an
# array), or 126/127 for a missing script or a runner without `jq`. Folding
# those into "nothing to report" would print a sentence that is false about
# what happened and leave the job green — the same observable state as the
# defect this file exists to fix, reached by a different route.
if [ "$RESOLVE_RC" -eq 1 ]; then
  echo "No open graph PR at ${SHA} — superseded or already merged. Nothing to report."
  exit 0
fi

if [ "$RESOLVE_RC" -ne 0 ] || [ -z "$PR_JSON" ]; then
  echo "graph-pr-report: graph-pr-resolve.sh could not answer (exit ${RESOLVE_RC}) — no verdict was reached for ${SHA}" >&2
  exit 1
fi

RESULT=$(printf '%s' "$PR_JSON" | "$HERE/graph-pr-check.sh")
RC=$?

echo "$RESULT"

# A missing or non-executable graph-pr-check.sh exits 127 with no output. That
# is already a failure, but posting it with an empty description puts a red on
# the PR that says nothing about why — and "the check could not run" is a
# different thing from "the PR is ineligible", which is what a bare red reads
# as. An empty result is a failure whatever the exit code said.
if [ "$RC" -eq 0 ] && [ -n "$RESULT" ]; then
  STATE="success"
else
  STATE="failure"
fi
if [ -z "$RESULT" ]; then
  RESULT="FAIL: graph-pr-check.sh produced no output (exit ${RC}) — the check could not be run"
fi
DESC=$(printf '%s' "$RESULT" | cut -c1-140)

# The status goes on the PR's head commit, never on $SHA. graph-pr-check.sh
# already fails a PR that reports no headRefOid, so an empty value here means
# the input shape changed rather than that the PR is ineligible — report it
# as a job failure rather than posting a status to nowhere.
HEAD_OID=$(printf '%s' "$PR_JSON" | jq -r '.headRefOid // empty')
if [ -z "$HEAD_OID" ]; then
  echo "graph-pr-report: resolved PR reports no headRefOid — no commit to pin the status to" >&2
  exit 1
fi

gh api "repos/${REPO}/statuses/${HEAD_OID}" \
  -f state="$STATE" \
  -f context="$CONTEXT" \
  -f description="$DESC"
