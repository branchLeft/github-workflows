# Comment narrative for `opv-lint.test.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-8)

```text
# Unit tests for opv-lint.sh's rules. Fixture-driven, same harness shape as
# docs-lint.test.sh: each case is a snippet fed to the script through a
# scratch git repo (opv-lint.sh requires one -- it resolves its scan root
# with `git rev-parse --show-toplevel`) and the exit code / rule presence is
# asserted.
#
# Usage: tools/opv-lint.test.sh
```

## Block 2 (source lines 74-79)

```text
# Runs the tool ONCE on one fixture and asserts on TWO rules' flag state
# from the SAME output. This is the only way to catch a cross-rule
# collision -- one rule over-firing on a span that is genuinely another
# rule's territory -- because case_file/case_sh/case_md_fence each assert
# only on the rule under test and are structurally blind to what every
# OTHER rule did on the same line.
```

## Block 3 (source lines 200-206)

```text
# ==== OPV002: an unsubstituted placeholder in a copy-pasteable command ======
# ---- Cross-rule regression: OPV001 and OPV002 must not co-fire on the same
# span. `<EXAMPLE-ipv4>` is the ruling's own canonical placeholder example,
# and it textually contains the hostname `EXAMPLE` -- a per-rule assertion
# (case_md_fence checking OPV002 alone) cannot see OPV001 firing on the same
# fixture, which is exactly how this collision shipped unnoticed. case_dual
# runs the tool once and checks both rules against the one output.
```

## Block 4 (source lines 401-406)

```text
# ==== The two exemption cases named verbatim in the ruling ===================
# 1. A test fixture where the hostname/address IS the subject of the
#    assertion (a promtool alert-rule fixture, an address-plan-drift test, a
#    render.ts unit test). Threading it through a lookup would make the test
#    assert on whatever the environment happens to hold, which inverts what
#    a fixture is for.
```

## Block 5 (source lines 476-480)

```text
# Mode resolution (.opv-lint.mode, warn-vs-enforce, the ENFORCED file set) is
# shared, unmodified logic sourced from lint-common.sh; every case above
# forces --mode enforce so it isn't exercised here, the same choice
# docs-lint.test.sh makes for the identical mechanism under its own mode
# file.
```
