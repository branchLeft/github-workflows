# test_codeowners.py

Checks `.github/CODEOWNERS` against a fixed table of sample paths and a few structural rules. It reads the file only and edits nothing on disk. It uses the standard library only.

```bash
python3 tools/test_codeowners.py                      # .github/CODEOWNERS
python3 tools/test_codeowners.py --codeowners PATH    # another copy of the file
```

It exits 0 when every check passes and 1 otherwise, printing each wrong path or broken rule.

## What it holds

- A last-match-wins matcher with GitHub's semantics. A pattern whose last segment has a wildcard does not reach into subdirectories unless it ends in `/`. A line that names no owner removes ownership.
- One table of sample paths, each with its class and whether the owner must hold it. Every kept class has a sample: workflows and actions, ruleset and branch-protection names, the gate tools and their tests, trust and key files, mail, and the licence text. Every released class has one too: the three gate-prose documents, unowned prose and files, and the paths the dropped name-only lines used to own.

## Structural checks

- No catch-all line.
- Every line names exactly the owner team, except the three release lines, which name no one.
- No work-item reference (`#` followed by digits) anywhere in the file.
- The last five lines are the shared set from the standards template, in template order, with `/.github/` last.
- The three release lines are the block just before the shared set, so no release line sits before an owner line.

## Sabotage

Every default run also breaks a copy of the file in memory and requires the checks to turn red:

- delete each line in turn, which must make a sample or a structural check fail
- append a catch-all
- append a line that names no owner for `/.github/actions/`
- move a release line below the shared set
- move the `/.github/` line to the top
- move the `/tools/` line below the release lines

One deletion is allowed to stay green, listed in `REDUNDANT_BY_DESIGN` with its reason: `/tools/floors.tsv`, which the `/tools/` line above it already owns. The sabotage is skipped when the file itself is red.

## Changing the table

A path that moves from owned to released, or back, is an ownership change: edit the table and `.github/CODEOWNERS` in the same pull request and say why in its description. The check runs as the `codeowners-self` workflow on every pull request.
