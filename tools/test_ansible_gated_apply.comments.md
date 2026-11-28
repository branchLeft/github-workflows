# Comment narrative for `test_ansible_gated_apply.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 5266-5271)

```text
        """Run a pin step against a run record that holds `record` as its referenced workflows.

        `tag` and `commit` are the answers of the tag read and the commit read. Unless a test sets
        another, the id is no tag object and is a commit, whatever sha the run record names.
        A `commit` of None answers as a commit of the id asked for.
        """
```

## Block 2 (source lines 5851-5859)

```text
    """The report step prints, for each host it asked, clean or not, one line with the reason (issue 73).

    A clean host's line says `kind=ok` and names the playbook it answered for, because the apply
    reaches a clean host too and the delivery's approver reads the hosts from those playbook lines.

    The log is public and the diff is not: a line carries the label, the kind, a fixed note, the
    NAMES of the scan hits and the playbook path, and never a host name or address, a diff line,
    the text a pattern matched or anything a host wrote to standard error.
    """
```

## Block 3 (source lines 7219-7224)

```text
# ---------------------------------------------------------------------------
# The push route: a host that never opens a connection is sent a git bundle of
# the commit by the runner, through the jump host, and answers with the line of
# the push receiver. The receiver is built in shared-infra; these tests are the
# sender's half of the one contract. README, "Pushed hosts".
# ---------------------------------------------------------------------------
```

## Block 4 (source lines 7800-7805)

```text
# ---------------------------------------------------------------------------
# The release tags in the bundle (T8 of shared-infra docs/pull-signer-trust.md).
# A host told to require a release tag applies a commit only if an annotated tag of the shape release/YYYYMMDD.N, whose own
# text names exactly that commit, is signed by a release key; the receiver reads
# the tags from the bundle. The sender's half is that the bundle carries them.
# ---------------------------------------------------------------------------
```

## Block 5 (source lines 7847-7855)

```text
    """What the receiver does with the release tags of a bundle, written out from shared-infra's pull_trigger.

    The receiver (`ansible/roles/pull_trigger/files/pull_trigger.py`, `release_tags_naming` and
    `ReleaseVerifier`) fetches `+refs/tags/release/*` from the bundle into a private
    namespace, keeps an annotated tag whose own `object` and `type` lines name exactly the commit and whose
    own `tag` line has the release shape, and requires its SSH signature, made under the namespace `git`,
    to be by a release key. This is that, so the sender is held to what the receiver reads. It is a mirror,
    not the receiver: the receiver's own tests are the proof of its half.
    """
```

## Block 6 (source lines 8028-8033)

```text
# ---------------------------------------------------------------------------
# The tip of main in the bundle (shared-infra pull_trigger.py `require_ancestor_of_main`).
# A host with no route to GitHub has no main of its own. Its receiver unpacks `refs/remotes/origin/main` from the
# bundle and refuses a commit that is not an ancestor of it, and a bundle that carries none, as `not-on-main`.
# The sender's half is that the bundle carries the tip.
# ---------------------------------------------------------------------------
```

## Block 7 (source lines 8054-8061)

```text
    """What the receiver asks of the tip of main in a bundle, written out from shared-infra's pull_trigger.

    The receiver (`ansible/roles/pull_trigger/files/pull_trigger.py`, `Quarantine.carries_main`, `Quarantine.admit`
    and `Repository.require_ancestor_of_main`) lists the bundle's heads for `refs/remotes/origin/main`, fetches it
    into a private ref when it is there, and then refuses the commit if there is no such ref or the commit is not an
    ancestor of it. This is that, so the sender is held to what the receiver reads. It is a mirror, not the
    receiver: the receiver's own tests are the proof of its half.
    """
```

## Block 8 (source lines 8356-8363)

```text
# --- What GitHub does with a job's concurrency group, evaluated rather than read as text -------------------
#
# GitHub keeps at most one running job and one pending job per group. A job that arrives while another runs
# in its group is pending, and replaces (cancels) a job that was already pending. With cancel-in-progress
# true it also cancels the running job. The tests below evaluate the group and cancel-in-progress of the
# real jobs for a given run (in the expression language GitHub uses, for the few operators the workflow
# writes) and feed them to a model of that rule, so "a push to main does not cancel a delivery in flight"
# is shown by running the rule, not by pinning a spelling.
```
