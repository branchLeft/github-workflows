# Comment narrative for `lint-common.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 16-21)

```text
  # The EXIT trap is deliberately NOT set here: a trap on EXIT registered
  # inside a function is torn down the moment that function returns in at
  # least one shell this runs under, deleting TMPDIR_LINT before a single
  # file has been scanned. The caller sets it, at its own top level,
  # immediately after this call returns -- `trap ... EXIT` at a script's own
  # top level is unambiguous everywhere.
```

## Block 2 (source lines 30-34)

```text
  # Bash 3.2 (macOS's shipped /bin/bash) treats "${arr[@]}" on a zero-element
  # array as an unbound-variable reference under `set -u` -- the callers of
  # this function guard the expansion with ${#FILES_FROM_ARGS[@]} before
  # ever passing it here, so "$#" being 0 is the normal whole-tree case, not
  # a broken call.
```

## Block 3 (source lines 97-102)

```text
# ---------------------------------------------------------------------------
# Exemption tier 1: a path glob in an ignorefile.
#   glob<TAB>RULE_IDS<TAB># reason
# `*` matches across directory separators, so `templates/*` covers nested
# paths. `ALL` exempts every rule for that path.
# ---------------------------------------------------------------------------
```

## Block 4 (source lines 124-128)

```text
# ---------------------------------------------------------------------------
# Exemption tier 2: inline suppression on the preceding line. The rule id and
# a reason are both mandatory; a bare disable is itself reported by the
# caller's own <PREFIX>000 rule (see check_malformed_suppression below).
# ---------------------------------------------------------------------------
```

## Block 5 (source lines 155-159)

```text
# ---------------------------------------------------------------------------
# Scannable line extraction. Every extractor preserves the file's own line
# numbers (blanking rather than deleting), so a report's line number always
# points at the real source line.
# ---------------------------------------------------------------------------
```

## Block 6 (source lines 185-193)

```text
# The inverse of md_scannable: everything BLANKED except the content of a
# fenced code block whose language is a shell/command dialect
# (bash/sh/shell/console). For a rule about a copy-pasteable command -- an
# unsubstituted placeholder -- prose and non-command fences are structurally
# out of scope, the opposite of a concrete-value rule that must read
# everything. Language detection takes the fence's first info-string word,
# lowercased; an untagged fence is NOT a command fence here (unlike a reply
# guard's own convention) because a runbook's copy-pasteable blocks are
# consistently tagged in this estate's own measured corpus.
```

## Block 7 (source lines 243-250)

```text
# Blank every span an ERE ($2) matches out of text ($1), replacing each with
# spaces of the same length so match offsets earlier in the same line stay
# valid. Blanking by position (not by searching for the matched text again)
# matters: a text-search removal can strip the wrong occurrence of that text
# (e.g. blanking "S3" must never eat the "S3" inside "S30"). LC_ALL=C is
# forced so grep's byte offsets and bash's substring indexing agree on
# multibyte content -- under a UTF-8 locale they diverge (grep counts bytes,
# bash counts characters) and offsets silently misalign.
```

## Block 8 (source lines 264-268)

```text
# rule, file-list, match-ere, except-ere (or ""), extractor, message,
# advisory ("1" = never fails, only a notice: mirrors docs-lint's DL011),
# ci ("1" = case-insensitive match and except -- docs-lint's own rules stay
# case-sensitive by design (see docs-lint.sh's DL001 comment), so this
# defaults off rather than changing existing behavior).
```

## Block 9 (source lines 332-336)

```text
# Test-only escape hatch, mirroring docs-lint.sh's own: a caller can
# `CHECK_SOURCE_ONLY=1 source <check>.sh` to load every function and pattern
# constant without scanning a file, so a test file can call blank_spans (or
# any other shared function) directly against the real production code
# instead of a reimplementation that could drift from it.
```
