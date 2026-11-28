# Comment narrative for `opv-lint.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 55-63)

```text
# ---------------------------------------------------------------------------
# Shared: the placeholder token shape, defined once because it is used both
# as OPV002's own match (below) and as OPV001's except (immediately below):
# a hostname substring sitting inside a placeholder token (`<edge1-ipv4>`)
# is a placeholder standing in for a value, not a committed one, so OPV001
# must not treat it as a hit -- see OPV001's own comment for the collision
# this prevents. Kept as one definition so the two rules can never silently
# disagree about what counts as "inside a placeholder."
# ---------------------------------------------------------------------------
```

## Block 2 (source lines 68-80)

```text
# ---------------------------------------------------------------------------
# OPV001 — a committed concrete operational value: an IPv4 literal/private
# subnet (outside loopback/documentation/link-local/four netmask literals),
# or one of the estate's fixed operational hostnames (edge1/app1/db1/mx1/
# mon1, word-bounded). Two shapes, reported under one rule id via two
# run_rule calls below. Private ranges, public constants and CIDR prose are
# deliberately NOT excluded. The hostname half excepts a placeholder token
# (PLACEHOLDER_HYPHEN_TOKEN/PLACEHOLDER_BARE_TOKEN): `edge1` inside
# `<edge1-ipv4>` is textually a placeholder, not a committed value, and
# without this except OPV001 and OPV002 co-fire on the same span, which
# contradicts the ruling's own framing of the two rules as normally
# independent. Full rationale and known limitations: tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
```

## Block 3 (source lines 87-97)

```text
# ---------------------------------------------------------------------------
# OPV002 — an unsubstituted placeholder in a copy-pasteable command. Scoped
# to bash/sh/shell/console fences ONLY (command_fence_scannable) -- the
# opposite scope from OPV001/OPV003/OPV004. A placeholder is a lowercase
# token with a hyphen or dot (`<edge1-ipv4>`), or a bare word from a fixed
# list (`<host>`, `<secret>`, ...). The angle brackets must run right up
# against the token with no space inside, which is what excludes shell
# redirection/comparison without a shell parser; a heredoc opener has no
# closing `>` so it can never match either shape. Full rationale and known
# limitations: tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
```

## Block 4 (source lines 101-105)

```text
# ---------------------------------------------------------------------------
# OPV003 — an AWS-style access key id literal. `AKIA`/`ASIA` + 16 more chars
# is a reserved, unambiguous shape, so this needs no except pattern. Known
# limitations (Hetzner has no comparable shape): tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
```

## Block 5 (source lines 108-118)

```text
# ---------------------------------------------------------------------------
# OPV004 — a credential-shaped variable assigned a literal via `=` instead of
# the mandated `read -rs VAR; export VAR`. Case-insensitive (camelCase/
# snake_case are the idiom in .py/.js/.ts). `export VAR="$VAR"` and
# `export VAR="$(cmd)"` are excluded as pure re-exports, and a fixed set of
# non-secret-shaped literals (true/false/yes/no/on/off/enabled/disabled) is
# excluded too -- but `${VAR:-a-literal-default}` is NOT excluded: a default
# expansion can still carry a real fallback secret. Known limitations
# (assignment-shaped only, no YAML `key:` form, the boolean list is fixed
# and small): tools/opv-lint-rules.md.
# ---------------------------------------------------------------------------
```

## Block 6 (source lines 121-126)

```text
# The except pattern mirrors MATCH's own prefix (same credential name, same
# `=`) so it only ever blanks a safe VALUE, never an unrelated quoted token
# elsewhere on the line. A pure re-export's value is exactly `$IDENT`,
# `${IDENT}` or `$(...)` inside double quotes and nothing else -- `${VAR:-x}`
# does not fit this shape (no `:`/`-`/`=`/`+` operator is allowed between the
# identifier and the closing brace), so it falls through to MATCH unblanked.
```
