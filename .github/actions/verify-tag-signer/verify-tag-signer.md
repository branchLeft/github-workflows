# verify-tag-signer

`verify-tag-signer.py` exits 0 only if a release tag of this repository is an
annotated tag signed by a signer listed in `.github/allowed_signers`. It is the
one home of that check: a repository that runs a production apply from a shared
workflow calls the composite action beside this file and holds no copy.

## Why

Before this, a pin check accepted any tag whose `verification.verified` flag was
true. That flag means the signature matches a key registered to some GitHub
account, not that the expected signer made the tag. The owner ruled that
production applies and the drift check accept only the owner's key on the tags
they run from.

## What it checks

Given a tag name, the answer of `GET git/ref/tags/{tag}`, the answer of
`GET git/tags/{sha}` and the allowed signers file, it refuses unless all hold:

- the name is `v` then three dot-separated numbers;
- the reference is `refs/tags/{tag}` and points at a **tag object**. A
  lightweight tag points straight at a commit and carries no signature;
- the tag object points at a commit, holds a signature and the text that was
  signed, and the signature is an SSH signature (a GPG one is refused);
- the signed text names the same tag and the same commit the answer names, so a
  valid signature on another tag's object, replayed under a new name, is refused;
- `ssh-keygen -Y verify` accepts the signature in the `git` namespace against
  one principal of the allowed signers file. The key is the owner's alone; a
  verified tag from any other key is refused.

The signed text and signature are the ones GitHub returns in `verification`;
the check recomputes the signature itself and does not read the `verified` flag.

## The allowed signers file

`.github/allowed_signers` is in OpenSSH `allowed_signers` format. Each line is
`principal namespaces="git" ssh-ed25519 KEY`. The script refuses the file, and so
every tag, if it:

- holds no entry (an empty file refuses everything);
- holds an entry without `namespaces="git"`, so a key cannot be trusted for
  anything but git signatures;
- holds a `cert-authority` entry, which would trust every key the authority signs;
- holds a key type other than `ssh-ed25519` or `sk-ssh-ed25519@openssh.com`, a
  malformed key, or a principal outside letters, digits, dot, at, underscore, hyphen;
- holds private key material, or is larger than 8192 bytes.

The file holds public keys only and names nobody: the principal is a label. The
test file pins the one expected fingerprint, so a key change needs the file and
that test changed together.

## Who may change it

Only the code owner team. `.github/CODEOWNERS` owns the whole `.github/`
directory, so this file, the script, the action and the self-test all need the
owner's approval, and the branch ruleset requires code owner review. The test
`CodeOwners` reads `.github/CODEOWNERS` and fails if any of those paths stops
having exactly that team as owner.

To rotate a key: one pull request adds the new line and the test's expected
fingerprint; a second removes the old line once no pinned tag needs it.

## Where the list is read from

The action reads `.github/allowed_signers` from `main`, never from the tag being
checked. A tag cannot vouch for itself, and removing a key on `main` takes effect
on the next run without a caller changing its pin.

## Calling it

Pin the action to a commit of this repository, not to a tag or a branch, and run
it as its own job that the apply job `needs`, so the apply is never admitted if it
fails:

```yaml
jobs:
  verify-pin:
    runs-on: ubuntu-latest
    permissions:
      contents: read
    steps:
      - uses: branchLeft/github-workflows/.github/actions/verify-tag-signer@FULL_COMMIT_ID_OF_MAIN
        with:
          tag: v1.1.0
```

`FULL_COMMIT_ID_OF_MAIN` stands for the 40-character commit id of this
repository's `main` that holds the action; the caller's own pull request writes it
in, and the owner reads it there. The tag given must be the tag the caller's `uses:` line pins. The check belongs
in the caller, before the apply, because the pin step inside a reusable workflow
runs the code of the tag being pinned: a tag made by someone else could carry a
workflow with that step removed.

## Limits

- It proves who signed the tag, not that the tagged code is what was reviewed.
- It does not look at the caller's `uses:` line; the caller passes the tag.
- It needs `ssh-keygen` (OpenSSH 8.2 or newer) and `jq`, both on the hosted runners.
