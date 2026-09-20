#!/usr/bin/env python3
"""Move a board item to a terminal-but-not-closed status once every pull
request naming its issue has stopped being open.

A merge is not a completion. One issue routinely spans several pull requests
in several repositories, so the merge that fires this is evidence about one
edge and nothing more: the question has to be re-asked of every pull request
naming the issue, and the write happens only when none of them is still open.
Acting on the triggering pull request alone would mark work finished while a
sibling branch is still unreviewed.

## The delivery seam

Nothing in the data distinguishes a pull request that DELIVERED an issue from
one that merely NAMED it. Both spellings of the body trailer -- the closing
keywords GitHub acts on and the non-closing `Refs` used where a merge must
not close anything -- parse to the same edge, and the org convention makes
`Refs` the spelling for hand-delivered work, which is exactly the work this
status exists to describe. Measured across the whole estate: of 26 items
whose every linked pull request had landed while the board disagreed, 26 were
linked by a non-closing keyword and none by a closing one. So the trailer
kind separates nothing, and a rule keyed on it alone would either write
nothing at all or write onto every epic a pull request name-checked in
passing.

Two knobs express the rule instead, and they are the only place this question
is answered:

  --link-kinds     which trailer spellings create an edge at all
  --from-statuses  which board statuses an item may be moved OUT of

The second carries the discrimination the first cannot. On the same 26 rows,
restricting the move to items already in flight leaves 4 -- the same 4 a human
reading them judged genuine. An unstarted epic sitting in the backlog is not
completed by a merge that mentioned it, whatever keyword did the mentioning.

Both are inputs with no hidden default: an unrecognised value is refused
rather than silently narrowed, because a typo that quietly disabled the write
would look exactly like an estate with nothing to write.

## Fail closed, everywhere

Every refusal is loud and nothing is inferred from an absence. An API error
is not "no siblings found", a subject that did not merge is not a subject
with no links, and a status this tool was never told to move out of is left
alone rather than guessed at. The one thing it will never do is write on
evidence it could not gather.
"""

import argparse
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request

API = "https://api.github.com"
GRAPHQL = API + "/graphql"

# The trailer spellings, grouped by what they assert. `board_state`'s own
# pattern in the consuming repo matches the same set in one alternation and
# discards which one matched; this keeps the groups apart so the seam above
# has something to select on.
CLOSING = ("close", "closes", "closed", "fix", "fixes", "fixed",
           "resolve", "resolves", "resolved")
REFS = ("ref", "refs")
PART_OF = ("part of",)

LINK_KIND_SETS = {
    "closing": frozenset(CLOSING),
    "closing+refs": frozenset(CLOSING + REFS),
    "all": frozenset(CLOSING + REFS + PART_OF),
}

# Matches the consuming repo's trailer pattern deliberately, colon separator
# included: a spelling one side reads as a link and the other does not is a
# silent disagreement about what is linked to what.
_LINK_RE = re.compile(
    r"\b(close[sd]?|fix(?:e[sd])?|resolve[sd]?|refs?|part\s+of)"
    r"(?::\s*|\s+)"
    r"(?:([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+))?#(\d+)",
    re.IGNORECASE)

OPEN = "open"
MERGED = "merged"
CLOSED_UNMERGED = "closed_unmerged"
NONE = "none"


class Refused(Exception):
    """A condition that stops the write and is reported as such. Distinct
    from an unexpected exception so that a refusal reads as a decision in the
    log rather than as a crash."""


def normalise_kind(raw):
    """The trailer keyword folded to its canonical spelling.

    Whitespace inside `part of` is collapsed because the pattern accepts any
    run of it, and a key that varied with the author's spacing would miss the
    membership test below.
    """
    return re.sub(r"\s+", " ", raw.strip().lower())


def parse_links(body, default_repo, org, kinds):
    """`[(repo, number), ...]` for every trailer in `body` whose keyword is in
    `kinds`, in first-seen order and de-duplicated.

    A trailer naming an owner other than `org` is dropped: this tool writes to
    one organisation's boards, and an edge pointing outside it can only ever
    resolve to something it must not touch.
    """
    out = []
    for match in _LINK_RE.finditer(body or ""):
        keyword, owner, repo, number = match.groups()
        if normalise_kind(keyword) not in kinds:
            continue
        if owner and owner.lower() != org.lower():
            continue
        key = (repo or default_repo, int(number))
        if key not in out:
            out.append(key)
    return out


def classify(states):
    """The single most informative state across every pull request linked to
    one issue.

    Open outranks everything: one unlanded sibling is the whole reason this
    tool re-asks the question. Merged outranks closed-unmerged, or an issue
    that ever had an abandoned branch could never reach the status again.
    """
    states = set(states)
    if OPEN in states:
        return OPEN
    if MERGED in states:
        return MERGED
    if CLOSED_UNMERGED in states:
        return CLOSED_UNMERGED
    return NONE


def pull_state(node):
    """A pull request's lifecycle state from an issues-API node.

    `merged_at` rather than `merged`: the issues endpoint exposes the former
    on every pull request and the latter on none of them, so reading `merged`
    here would classify every landed pull request as closed-unmerged and
    every issue as never-delivered.
    """
    if node.get("state") == "open":
        return OPEN
    if (node.get("pull_request") or {}).get("merged_at"):
        return MERGED
    return CLOSED_UNMERGED


def decide(linked_states, current_status, from_statuses):
    """`(write, reason)` for one issue on one board placement."""
    verdict = classify(linked_states)
    if verdict == OPEN:
        return False, "a pull request naming it is still open"
    if verdict != MERGED:
        return False, "no pull request naming it has merged (%s)" % verdict
    if current_status is None:
        return False, "the item carries no status value"
    if current_status not in from_statuses:
        return False, ("its status is %r, which is not one this run may move "
                       "out of" % current_status)
    return True, "every pull request naming it has landed"


class Client(object):
    """Two credentials, because no single one can do both halves.

    The App installation token is the only thing that can touch an
    organisation project at all -- `GITHUB_TOKEN` answers NOT_FOUND on
    `projectV2` -- and it is also the only one that reaches an issue in a
    sibling repository. It cannot read a pull request: an App granted
    `issues: read` is refused `/repos/.../issues/<n>` for a pull request with
    "Resource not accessible by integration", measured on this runner.

    The subject pull request is always in the repository the workflow is
    running in, which is exactly what the job's own `GITHUB_TOKEN` can read.
    So the split is not a convenience: it is what lets this run on the two
    permissions the App already holds, rather than on a third one nobody has
    granted.
    """

    def __init__(self, project_token, repo_token, org):
        self.project_token = project_token
        self.repo_token = repo_token or project_token
        self.org = org

    def _request(self, url, data=None, accept="application/vnd.github+json",
                 token=None):
        headers = {
            "Authorization": "Bearer " + (token or self.project_token),
            "Accept": accept,
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "branchleft-merged-status",
        }
        body = None
        if data is not None:
            body = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8")), resp.headers
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            # Raised, never returned as an empty result. An HTTP error that
            # became "nothing found" would make a revoked credential
            # indistinguishable from an issue with no siblings, and the write
            # that followed would be made on no evidence at all.
            raise Refused("%s %s -> HTTP %s: %s"
                          % ("POST" if data else "GET", url, exc.code, detail))

    def pull(self, repo, number):
        """The subject pull request, read with the repository token."""
        return self._request(
            "%s/repos/%s/%s/pulls/%d" % (API, self.org, repo, number),
            token=self.repo_token)[0]

    def referencing_pulls(self, repo, number):
        """Every pull request in the org whose timeline entry cross-references
        this issue, as `(repo, number, node)` where `node` carries the body
        and lifecycle state the timeline already embeds.

        This is a candidate list, not the answer: the timeline counts a
        mention made anywhere, including one in a comment written long after
        the fact, while the edge this tool acts on is the one a pull request
        declares in its own body. The body is filtered on below, from the copy
        the timeline supplies -- reading each candidate back individually
        would need a permission the App does not hold and would buy nothing.
        """
        found = []
        url = ("%s/repos/%s/%s/issues/%d/timeline?per_page=100"
               % (API, self.org, repo, number))
        while url:
            page, headers = self._request(
                url, accept="application/vnd.github+json")
            for event in page:
                if event.get("event") != "cross-referenced":
                    continue
                source = (event.get("source") or {}).get("issue") or {}
                if not source.get("pull_request"):
                    continue
                src_repo = ((source.get("repository") or {}).get("name"))
                owner = (((source.get("repository") or {}).get("owner")
                          or {}).get("login"))
                if owner and owner.lower() != self.org.lower():
                    continue
                if not src_repo or not isinstance(source.get("number"), int):
                    continue
                key = (src_repo, source["number"])
                if key not in [(r, n) for r, n, _ in found]:
                    found.append((src_repo, source["number"], source))
            url = _next_link(headers.get("Link"))
        return found

    def graphql(self, query, variables):
        payload, _ = self._request(
            GRAPHQL, data={"query": query, "variables": variables})
        if payload.get("errors"):
            raise Refused("GraphQL: %s"
                          % json.dumps(payload["errors"])[:600])
        return payload["data"]

    def project_items(self, repo, number, status_field):
        data = self.graphql(_ITEMS_QUERY, {
            "owner": self.org, "repo": repo, "number": number,
            "field": status_field})
        issue = (data.get("repository") or {}).get("issue")
        if issue is None:
            return None
        nodes = (issue.get("projectItems") or {}).get("nodes") or []
        page = (issue.get("projectItems") or {}).get("pageInfo") or {}
        if page.get("hasNextPage"):
            # One issue on more boards than the query asks for would leave a
            # placement unexamined, and an unexamined placement reads exactly
            # like a correct one.
            raise Refused("issue %s/%s#%d sits on more project items than "
                          "this query reads" % (self.org, repo, number))
        return nodes

    def set_status(self, project_id, item_id, field_id, option_id):
        return self.graphql(_WRITE_MUTATION, {
            "project": project_id, "item": item_id,
            "field": field_id, "option": option_id})


_NEXT_RE = re.compile(r'<([^>]+)>\s*;\s*rel="?next"?')


def _next_link(header):
    """The `rel="next"` URL from a Link header, or None.

    Paging is followed rather than assumed away: an issue with more than one
    page of timeline entries would otherwise have its later cross-references
    silently dropped, and a dropped open sibling is the exact reading this
    tool exists to prevent.
    """
    for part in (header or "").split(","):
        match = _NEXT_RE.search(part)
        if match:
            return match.group(1).strip()
    return None


_ITEMS_QUERY = """
query($owner:String!,$repo:String!,$number:Int!,$field:String!){
  repository(owner:$owner,name:$repo){
    issue(number:$number){
      projectItems(first:20){
        pageInfo{ hasNextPage }
        nodes{
          id
          project{ id number title }
          fieldValueByName(name:$field){
            ... on ProjectV2ItemFieldSingleSelectValue{
              name
              field{ ... on ProjectV2SingleSelectField{
                id options{ id name } } }
            }
          }
        }
      }
    }
  }
}
"""

_WRITE_MUTATION = """
mutation($project:ID!,$item:ID!,$field:ID!,$option:String!){
  updateProjectV2ItemFieldValue(input:{projectId:$project,itemId:$item,
    fieldId:$field,value:{singleSelectOptionId:$option}}){
    projectV2Item{ id }
  }
}
"""


def resolve_kinds(spec):
    try:
        return LINK_KIND_SETS[spec]
    except KeyError:
        raise Refused(
            "--link-kinds %r is not one of %s. Refusing to run: a value this "
            "tool does not recognise would silently select no trailers at "
            "all, which is indistinguishable from an estate with nothing to "
            "write." % (spec, ", ".join(sorted(LINK_KIND_SETS))))


def resolve_statuses(spec):
    values = [part.strip() for part in (spec or "").split(",")]
    values = [value for value in values if value]
    if not values:
        raise Refused(
            "--from-statuses is empty. Refusing to run: an empty set moves "
            "nothing, and a run that writes nothing for that reason reads in "
            "the log exactly like a run with nothing to do.")
    return frozenset(values)


def run(client, subject_repo, subject_number, kinds, from_statuses,
        target_status, status_field, dry_run, log):
    subject = client.pull(subject_repo, subject_number)
    if not subject.get("merged"):
        raise Refused(
            "%s/%s#%d did not merge. Refusing to run: this tool records that "
            "work landed, and a closed-unmerged subject is the case where it "
            "did not." % (client.org, subject_repo, subject_number))

    candidates = parse_links(subject.get("body"), subject_repo, client.org,
                             kinds)
    log("subject %s/%s#%d links %d issue(s): %s"
        % (client.org, subject_repo, subject_number, len(candidates),
           ", ".join("%s#%d" % key for key in candidates) or "none"))

    written = []
    for repo, number in candidates:
        items = client.project_items(repo, number, status_field)
        if items is None:
            # A trailer can name a pull request, or an issue this token
            # cannot see. Neither is written to, and neither is treated as a
            # reading about whether the work landed.
            log("  %s#%d is not an issue visible to this token -- skipped"
                % (repo, number))
            continue
        if not items:
            log("  %s#%d is on no board -- nothing to write" % (repo, number))
            continue

        states = [MERGED]
        log("  %s#%d <- %s#%d (%s)"
            % (repo, number, subject_repo, subject_number, MERGED))
        for pull_repo, pull_number, node in client.referencing_pulls(
                repo, number):
            if (pull_repo, pull_number) == (subject_repo, subject_number):
                continue
            if (repo, number) not in parse_links(
                    node.get("body"), pull_repo, client.org, kinds):
                continue
            state = pull_state(node)
            states.append(state)
            log("  %s#%d <- %s#%d (%s)"
                % (repo, number, pull_repo, pull_number, state))

        for item in items:
            value = item.get("fieldValueByName") or {}
            current = value.get("name")
            write, reason = decide(states, current, from_statuses)
            where = "board %s" % ((item.get("project") or {}).get("number"))
            if not write:
                log("  %s#%d %s: left at %r -- %s"
                    % (repo, number, where, current, reason))
                continue
            field = value.get("field") or {}
            option = None
            for candidate in field.get("options") or []:
                if candidate.get("name") == target_status:
                    option = candidate.get("id")
            if option is None:
                raise Refused(
                    "%s has no %r option on its status field -- refusing to "
                    "write a status that board cannot hold"
                    % (where, target_status))
            if dry_run:
                log("  %s#%d %s: WOULD set %r (from %r) -- %s"
                    % (repo, number, where, target_status, current, reason))
                continue
            client.set_status((item.get("project") or {}).get("id"),
                              item.get("id"), field.get("id"), option)
            log("  %s#%d %s: set %r (from %r) -- %s"
                % (repo, number, where, target_status, current, reason))
            written.append((repo, number, where))
    return written


def _self_test():
    org = "branchLeft"
    failures = []

    def check(name, got, want):
        if got != want:
            failures.append("%s: got %r, want %r" % (name, got, want))

    all_kinds = LINK_KIND_SETS["all"]
    check("plain refs",
          parse_links("Refs branchLeft/workspace#12", "x", org, all_kinds),
          [("workspace", 12)])
    check("bare number uses the subject repo",
          parse_links("Closes #7", "website", org, all_kinds),
          [("website", 7)])
    check("colon separator",
          parse_links("Closes: #7", "website", org, all_kinds),
          [("website", 7)])
    check("part of, multi-space",
          parse_links("part  of  branchLeft/standards#3", "x", org,
                      all_kinds),
          [("standards", 3)])
    check("foreign owner dropped",
          parse_links("Closes otherorg/thing#9", "x", org, all_kinds), [])
    check("duplicates collapse",
          parse_links("Refs #1 and refs #1", "r", org, all_kinds),
          [("r", 1)])
    check("closing-only rejects refs",
          parse_links("Refs #1\nCloses #2", "r", org,
                      LINK_KIND_SETS["closing"]),
          [("r", 2)])
    check("closing+refs rejects part of",
          parse_links("Refs #1\npart of #2", "r", org,
                      LINK_KIND_SETS["closing+refs"]),
          [("r", 1)])
    check("no trailer", parse_links("mentions #4 in passing", "r", org,
                                    all_kinds), [])

    check("open outranks merged", classify([MERGED, OPEN]), OPEN)
    check("merged outranks closed", classify([CLOSED_UNMERGED, MERGED]),
          MERGED)
    check("closed alone", classify([CLOSED_UNMERGED]), CLOSED_UNMERGED)
    check("nothing linked", classify([]), NONE)

    check("open state", pull_state({"state": "open", "pull_request": {}}),
          OPEN)
    check("merged state",
          pull_state({"state": "closed",
                      "pull_request": {"merged_at": "2026-01-01T00:00:00Z"}}),
          MERGED)
    check("closed unmerged",
          pull_state({"state": "closed",
                      "pull_request": {"merged_at": None}}),
          CLOSED_UNMERGED)

    flight = frozenset(("In progress", "In review"))
    check("sibling open blocks the write",
          decide([MERGED, OPEN], "In review", flight)[0], False)
    check("all landed writes", decide([MERGED], "In review", flight)[0], True)
    check("backlog is not moved",
          decide([MERGED], "Backlog", flight)[0], False)
    check("no status value", decide([MERGED], None, flight)[0], False)
    check("nothing merged", decide([CLOSED_UNMERGED], "In review", flight)[0],
          False)

    check("kind set", resolve_kinds("closing+refs"),
          LINK_KIND_SETS["closing+refs"])
    try:
        resolve_kinds("closing+part-of")
        failures.append("resolve_kinds accepted an unknown value")
    except Refused:
        pass
    for bad in ("", "  ", ",,"):
        try:
            resolve_statuses(bad)
            failures.append("resolve_statuses accepted %r" % bad)
        except Refused:
            pass
    check("statuses parse", resolve_statuses(" In progress , In review "),
          flight)

    # An HTTP error must stop the run, never become an empty result: a
    # revoked credential answering 403 would otherwise be indistinguishable
    # from an issue with no siblings, and the write that followed would be
    # made on evidence that was never gathered.
    real_urlopen = urllib.request.urlopen

    def _boom(*args, **kwargs):
        raise urllib.error.HTTPError(
            "https://api.github.com/x", 403, "Forbidden", {},
            io.BytesIO(b'{"message":"Resource not accessible by integration"}'))

    urllib.request.urlopen = _boom
    try:
        try:
            Client("t", "t", "branchLeft").pull("workspace", 1)
            failures.append("an HTTP error did not stop the run")
        except Refused as exc:
            if "403" not in str(exc):
                failures.append("the refusal does not name the status code")
    finally:
        urllib.request.urlopen = real_urlopen

    logged = []

    class _Fake(object):
        """Stands in for the API so `run`'s own refusals can be exercised.

        Deliberately not a stand-in for GitHub: it answers the four calls
        `run` makes and nothing more. What it proves is that the decision
        logic refuses where it should, never that the requests resemble the
        real ones -- see the live run recorded with this change for that.
        """

        org = "branchLeft"

        def __init__(self, merged=True, siblings=(), status="In review"):
            self._merged = merged
            self._siblings = list(siblings)
            self._status = status
            self.writes = []

        def pull(self, repo, number):
            return {"merged": self._merged,
                    "body": "Refs branchLeft/workspace#7"}

        def project_items(self, repo, number, field):
            return [{"id": "item", "project": {"id": "proj", "number": 4},
                     "fieldValueByName": {
                         "name": self._status,
                         "field": {"id": "field",
                                   "options": [{"id": "opt",
                                                "name": "Merged"}]}}}]

        def referencing_pulls(self, repo, number):
            return self._siblings

        def set_status(self, *args):
            self.writes.append(args)

    def _run(client):
        del logged[:]
        return run(client, "workspace", 1, LINK_KIND_SETS["closing+refs"],
                   flight, "Merged", "Status", False, logged.append)

    try:
        _run(_Fake(merged=False))
        failures.append("run accepted a subject that did not merge")
    except Refused:
        pass

    open_sibling = [("workspace", 9,
                     {"state": "open", "pull_request": {"merged_at": None},
                      "body": "Refs branchLeft/workspace#7"})]
    client = _Fake(siblings=open_sibling)
    check("an open sibling blocks the write", _run(client), [])
    check("and nothing was written", client.writes, [])

    client = _Fake()
    check("no open sibling writes", len(_run(client)), 1)
    check("the write reached the board", len(client.writes), 1)

    client = _Fake(status="Backlog")
    check("a backlog item is left alone", _run(client), [])

    merged_sibling = [("workspace", 9,
                       {"state": "closed",
                        "pull_request": {"merged_at": "2026-01-01T00:00:00Z"},
                        "body": "Refs branchLeft/workspace#7"})]
    check("a landed sibling does not block", len(_run(_Fake(
        siblings=merged_sibling))), 1)

    unrelated = [("workspace", 9,
                  {"state": "open", "pull_request": {"merged_at": None},
                   "body": "mentions #7 with no trailer"})]
    check("an open pull request with no trailer does not block",
          len(_run(_Fake(siblings=unrelated))), 1)

    check("link header",
          _next_link('<https://a/2>; rel="next", <https://a/9>; rel="last"'),
          "https://a/2")
    check("no next link", _next_link('<https://a/1>; rel="prev"'), None)
    check("absent link header", _next_link(None), None)

    for failure in failures:
        sys.stderr.write("FAIL %s\n" % failure)
    if failures:
        return 1
    print("merged-status self-test: OK")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--org")
    parser.add_argument("--repo")
    parser.add_argument("--pr", type=int)
    parser.add_argument("--link-kinds")
    parser.add_argument("--from-statuses")
    parser.add_argument("--target-status", default="Merged")
    parser.add_argument("--status-field", default="Status")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    lines = []

    def log(line):
        print(line, flush=True)
        lines.append(line)

    try:
        missing = [name for name, value in
                   (("--org", args.org), ("--repo", args.repo),
                    ("--pr", args.pr), ("--link-kinds", args.link_kinds),
                    ("--from-statuses", args.from_statuses))
                   if value in (None, "")]
        if missing:
            raise Refused("missing required argument(s): %s"
                          % ", ".join(missing))
        token = os.environ.get("MERGED_STATUS_TOKEN") or ""
        if not token:
            raise Refused("MERGED_STATUS_TOKEN is empty")
        if not os.environ.get("MERGED_STATUS_REPO_TOKEN"):
            # Falling back to the project token is right for a workstation
            # run, where one credential covers both, and wrong to do
            # silently on a runner -- the App cannot read a pull request, so
            # the fallback would fail several calls later with an error that
            # named the wrong cause.
            log("no MERGED_STATUS_REPO_TOKEN: reading the subject pull "
                "request with the project token")
        written = run(
            Client(token, os.environ.get("MERGED_STATUS_REPO_TOKEN"),
                   args.org),
            args.repo, args.pr,
            resolve_kinds(args.link_kinds),
            resolve_statuses(args.from_statuses),
            args.target_status, args.status_field, args.dry_run, log)
        log("wrote %d placement(s)" % len(written))
        status = 0
    except Refused as exc:
        log("REFUSED: %s" % exc)
        status = 1
    _summary(lines)
    return status


def _summary(lines):
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("### merged-status\n\n```\n%s\n```\n"
                         % "\n".join(lines))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())
