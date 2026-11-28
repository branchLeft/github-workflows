# Comment narrative for `docs-lint.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 46-50)

```text
# Test-only escape hatch: `DOCS_LINT_SOURCE_ONLY=1 source docs-lint.sh` runs
# everything above (option parsing, lint-common's own function and pattern
# definitions) and returns before any file is scanned, so docs-lint.test.sh
# can call blank_spans directly against the real patterns and assert on its
# output instead of only on a rule's final verdict.
```

## Block 2 (source lines 62-67)

```text
# `S3` is deliberately exempt: in this corpus it is both an AWS service and a
# real story id, and no pattern separates them. Missing the occasional story
# S3 costs far less than 60 false positives teaching people to ignore the
# linter. Story ids S7 upward are still caught. `Q1`-`Q4` get the same
# exemption for the same reason: calendar quarters in roadmap and marketing
# prose collide with the low end of the Q-item id space.
```

## Block 3 (source lines 71-75)

```text
# DL012 catches the *replacement* id shape for branchLeft's own items:
# branchLeft/repo#N. Rationale, scope and known limitations: docs-lint-rules.md.
# Scoped to the literal `branchLeft` owner, not any owner/repo#N: an upstream
# reference (`actions/runner#1327`) is a workaround citation, not board
# narration, and a generic owner/repo#N shape false-matches doc URL fragments.
```

## Block 4 (source lines 124-128)

```text
# ---------------------------------------------------------------------------
# DL010 — links must resolve inside the repo
# ---------------------------------------------------------------------------
# Findings are collected to a file rather than piped, so that `report` runs in
# this shell and its counts reach the summary and the exit code.
```

## Block 5 (source lines 185-191)

```text
# ---------------------------------------------------------------------------
# Structural markdown hygiene. npx is this script's only runtime dependency.
# Markdown in scope means markdownlint is enabled, so a missing npx fails the
# run instead of skipping the checks. That holds in every mode: warn mode
# ratchets per-file findings, and a runner without npx is an environment
# fault, not a finding. With no markdown in scope markdownlint is off.
# ---------------------------------------------------------------------------
```
