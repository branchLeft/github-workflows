# Comment narrative for `docs-lint.test.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-7)

```text
# Unit tests for docs-lint.sh's content rules. Fixture-driven: each case is a
# snippet of prose fed to the script through a scratch git repo (docs-lint.sh
# requires one — it resolves its scan root with `git rev-parse
# --show-toplevel`) and the exit code / DL009 presence is asserted.
#
# Usage: tools/docs-lint.test.sh
```

## Block 2 (source lines 181-185)

```text
# --- Whitebox: exemption removal is by matched position, not matched text --
# The real hazard here is an exempt token that is a *prefix* of the real id
# on the same line: a text-search removal strips the first literal
# occurrence of the exempted string, which for a prefix pair lands inside
# the real id rather than on the exempted token itself.
```

## Block 3 (source lines 208-212)

```text
# --- DL012: owner/repo#N in a source comment ------------------------------
# The fixture below is a comment that DL009, DL007, DL008 and DL001 each
# return zero matches against, which is exactly the coverage gap DL012
# closes: an owner/repo#N reference is the one ticket-reference shape none
# of the existing rules were watching for.
```

## Block 4 (source lines 270-274)

```text
# Fail-closed when npx is missing. The shim holds every executable from PATH
# except npx, so npx is the only tool removed. With markdown in scope
# markdownlint is enabled, and a runner without npx must fail the run in both
# modes with the fixed message, not skip the checks and exit clean. With no
# markdown in scope markdownlint is deliberately off and the run still passes.
```
