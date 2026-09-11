# opv-lint rules

Blocks a committed **operational value** in place of the reference the org
convention (`## Replying` in the workspace root's `CLAUDE.md`) mandates:
`read -rs VAR; export VAR` for a secret, a resolved lookup or an env var for
an address or an id. This is the complementary check to a placeholder guard:
that class catches an unsubstituted `<placeholder>` token in a reply; this
one catches the opposite failure — a real value sitting where a reference
belongs, already committed.

Run it locally from a repo root:

```bash
path/to/opv-lint.sh --explain          # whole tree
path/to/opv-lint.sh --explain FILE...  # specific files
```

`--explain` prints the matched text for each finding.

## Modes

`.opv-lint.mode` at the repo root controls how findings are treated, exactly
as `.docs-lint.mode` does for docs-lint (own file, own scope — the two
checks ratchet independently).

| File | Behaviour |
|---|---|
| absent | **enforce** — any finding anywhere in the tree fails. This is the default so a new repo is protected without opting in. |
| contains `warn` | **ratchet** — the full-tree scan is advisory and never fails, but files changed on the branch are enforced and do fail. |

Warn mode is how a repo carrying existing violations adopts the gate without
an immediate red build: CI is green on day one, no new violation can land,
and each cleanup lands checked against the rule it fixes. Flip to enforce by
deleting the file.

Every rule reads the **whole file**, not comments-only or fences-blanked —
unlike docs-lint's md/code split, an operational value is exactly as real
inside a pasted command, a YAML value or a table cell as inside a comment.

## Rules

| ID | Catches | Fix |
|---|---|---|
| OPV000 | A suppression comment with no rule id or no reason | Write `opv-disable-next-line OPV003 <why>` |
| OPV001 | A committed AWS-style access key id (`AKIA`/`ASIA` + 16 more characters) | `read -rs VAR; export VAR`, never the literal value |
| OPV002 | A credential-shaped variable (`SECRET`, `PASSWORD`, `PASSPHRASE`, `ACCESS_KEY`, `PRIVATE_KEY`, `TOKEN`, `CREDENTIAL`, `API_KEY`, `ENCRYPTIONSALT`) assigned a literal instead of a variable reference | `read -rs VAR; export VAR`, then reference `$VAR` |
| OPV003 | A bare IPv4 address outside loopback/documentation/link-local ranges | Resolve it with a lookup (`hcloud server describe`, `pulumi stack output`, …) into an env var, don't commit the literal |

### Why OPV001 and OPV002 are separate rules

They fail for opposite reasons and a file can need one exempted and not the
other. OPV001 keys on *shape* — the reserved `AKIA`/`ASIA` prefix is
unambiguous, so it needs no except pattern and no assignment context; it
fires on the value wherever it appears. OPV002 keys on *context* — there is
no shape that marks a Hetzner-issued key or a database password as a secret,
so it only fires when a credential-named variable is assigned a literal
directly.

### Known limitation: OPV001 is AWS-shaped only

A Hetzner Object Storage access key id has no comparable fixed prefix to key
on, so it is not caught by shape. It is still caught by OPV002 when it is
committed the way this estate's runbooks actually commit one: as a direct
assignment (`export AWS_ACCESS_KEY_ID='...'`) rather than a `read`.

### Known limitation: OPV002 is `=`-assignment-shaped only

`NAME=value` and `export NAME=value` are covered. Two shapes are
deliberately not:

- A value handed to a command positionally — `printf '...%s...' 'literal
  secret'` — has no `=` to anchor on. A token-by-token scan of a whole line
  to catch that shape was tried and rejected: an embedded apostrophe in the
  quoted value closes a naive quoted-token match early and lets most of a
  file's offending lines through silently, which is worse than a narrower
  rule that is right about what it does cover.
- A YAML `key: value` credential (a Pulumi config secret written in place
  rather than through `pulumi config set --secret`) uses `:`, not `=`. `:`
  was not added alongside it because a credential-name substring appears in
  plenty of non-secret YAML keys (`passwordHashAlgo: bcrypt`,
  `tokenTtlSeconds: 3600`), and catching the real case without those would
  need a narrower name list than this rule currently carries.

### Known limitation: OPV003 catches private ranges on purpose

`10.x`, `172.16-31.x` and `192.168.x` are **not** excluded. They are real
internal topology — the address a host actually has — and the owner ruling
behind this check ranks that the same as a public address: both are a
concrete operational value where a lookup belongs. Only ranges that can
never be a real host are excluded: loopback, unspecified, broadcast, the
three IANA documentation ranges, link-local/metadata, and a short fixed list
of netmask literals.

### Known limitation: file types

The same markdown/code extension list as docs-lint
(`.md`/`.mdx`, `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs`/`.py`/`.sh`/`.bash`/`.yml`/`.yaml`/`.tf`).
A `.json`, `.env` or other extension outside that list is not scanned.
Widening it is a smaller change than adding a rule and can follow once the
current set has proven itself.

## Suppressing a finding

Same two tiers as docs-lint, and the identical mechanism — only the
directive token and the rule-id namespace differ, so a suppression is never
honoured by a second, subtly different parser.

**One line**, on the line before the finding:

```yaml
# opv-disable-next-line OPV003 promtool fixture: this hostname is the subject of the assertion
```

```markdown
<!-- opv-disable-next-line OPV001 example key id, not a real credential -->
```

The rule id and a reason are both mandatory; a bare disable is reported as
OPV000.

**A path**, via `.opv-lintignore` at the repo root — tab-separated `glob`,
rule ids, reason:

```text
fixtures/*	OPV003	# fixture hostnames are the subject of the assertion, not a leak
```

`*` matches across directory separators. `ALL` exempts every rule for that
path — use it for a file whose entire purpose is to document or test the
pattern this check would otherwise flag, the same way `.docs-lintignore`
exempts `tools/docs-lint.sh` and `tools/docs-lint-rules.md` from `docs-lint`
itself.

**A repo**, via `.opv-lint.mode` — see Modes above.

### Why an inline exemption, not a path carve-out, is the expected shape here

A path pattern silently exempts every future file underneath it forever. An
inline comment forces a stated reason on the specific line, and the running
exemption count stays visible to review rather than becoming a hole nobody
decided to open — the same reasoning `docs-lint-rules.md`'s suppression
section and `standards/docs/ratchet.md` already state for docs-lint. Reach
for `.opv-lintignore` only for a file that is *entirely* about documenting or
testing the pattern (this rules doc, the check script itself, its test
fixtures) — not for a directory of files that merely tend to trip a rule.

## What this check does not do

It is the guard, not the sweep. Adopting it in a repo that already carries
violations is expected to turn CI red (or, in warn mode, advisory) on the
existing instances; fixing those is separate work, done caller by caller, not
a bulk pass run alongside landing this check.
