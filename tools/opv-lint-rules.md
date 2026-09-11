# opv-lint rules

Blocks a committed **operational value** in place of the reference the org
convention (`## Replying` in the workspace root's `CLAUDE.md`) mandates:
`read -rs VAR; export VAR` for a secret, a resolved lookup or an env var for
an address or an id, and a resolved value (never an unsubstituted token) in
a command meant to be pasted and run.

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

**Scope is split by rule, on purpose, and it runs in the opposite direction
per rule family.** OPV001/OPV003/OPV004 (concrete values) read the **whole
file** — unlike docs-lint's md/code split, a leaked key or address is
exactly as real inside a pasted command, a YAML value or a table cell as
inside a comment. OPV002 (an unsubstituted placeholder) is the deliberate
exception: it reads **only** `bash`/`sh`/`shell`/`console` fenced code
blocks in markdown, because a placeholder is a defect in an instruction
meant to be pasted into a shell — prose describing a value, and every other
fence language, are structurally out of scope for that one rule.

## Rules

| ID | Scope | Catches | Fix |
|---|---|---|---|
| OPV000 | whole file | A suppression comment with no rule id or no reason | Write `opv-disable-next-line OPV001 <why>` |
| OPV001 | whole file | A committed concrete operational value: an IPv4 literal or private subnet, or one of the estate's fixed operational hostnames (`edge1`/`app1`/`db1`/`mx1`/`mon1`) | Resolve it with a lookup (`hcloud server describe`, `pulumi stack output`, …) into an env var, don't commit the literal |
| OPV002 | `bash`/`sh`/`shell`/`console` fences only | An unsubstituted placeholder in a copy-pasteable command (`<edge1-ipv4>`, `<host>`, …) | Resolve it before committing, or restructure the step as a lookup |
| OPV003 | whole file | A committed AWS-style access key id (`AKIA`/`ASIA` + 16 more characters) | `read -rs VAR; export VAR`, never the literal value |
| OPV004 | whole file | A credential-shaped variable (`SECRET`, `PASSWORD`, `PASSPHRASE`, `ACCESS_KEY`, `PRIVATE_KEY`, `TOKEN`, `CREDENTIAL`, `API_KEY`, `ENCRYPTIONSALT` — matched case-insensitively, so `db_password`, `apiToken` and `DB_PASSWORD` all fire) assigned a literal instead of a variable reference | `read -rs VAR; export VAR`, then reference `$VAR` |

### Why these are the ids, in this order

The numbering follows the org's own ruling on this rule family, not the
order the checks were built in: `OPV001`/`OPV002` are the two halves of the
underlying principle — a concrete value committed where a reference belongs,
and an unresolved placeholder in an instruction meant to be pasted and run —
and they are deliberately two separate ids because they fail for opposite
reasons and a file can legitimately need one exempted and not the other.
`OPV003`/`OPV004` are additional, narrower concrete-value shapes (a
specific credential format, a specific assignment pattern) that don't
change that structure.

### OPV001 has two shapes, one rule id

An address (IPv4 literal or private subnet) and a fixed operational
hostname fail for the *same* reason — a lookup belongs where this literal
is — so they share `OPV001` rather than getting a rule id each, unlike the
OPV002-vs-OPV001 split above.

- **Address form**: octets validated `<=255`, so a version-shaped string
  with an out-of-range component (`999.1.1.1`) is rejected rather than
  merely filtered. Private ranges (`10.x`, `172.16-31.x`, `192.168.x`) are
  **not** excluded — they are real internal topology, and the ruling behind
  this check (Reading B: the principle governs everything committed, not
  only what is pasted into a shell) ranks that the same as a public
  address. Neither are well-known public constants (`8.8.8.8`) or a CIDR
  block named in architecture prose (`10.0.0.0/8` in a topology table) —
  the rule has no concept of "sensitive," only "a literal where a reference
  belongs." **This is expected, frequent friction, not an oversight**: a
  doc that legitimately discusses a public constant or a CIDR range in
  prose needs an inline suppression per occurrence, or a `.opv-lintignore`
  entry if the whole file is about describing addresses rather than pasting
  commands. Only ranges that can never be a real host are excluded outright:
  loopback, unspecified, broadcast, the three IANA documentation ranges,
  link-local/metadata, and exactly four netmask literals (`255.255.255.255`,
  `255.255.255.0`, `255.255.0.0`, `255.0.0.0`) — **not a general
  netmask-shape check**: `255.255.255.128` and other valid but less common
  netmasks still fire and need the same inline suppression as any other
  address.
- **Hostname form**: `edge1`, `app1`, `db1`, `mx1`, `mon1` — this estate's
  own fixed, small set of operational hostnames, word-bounded
  (`hetzner-edge1` matches, `edge10` does not) and case-sensitive (these
  names are always written lowercase). No FQDN form (`*.branchleft.co.uk`)
  is matched — the runbooks that motivated this rule reference the estate
  exclusively by bare short name, both in prose and as an `hcloud server
  describe <name>` resource argument, never as a dotted domain. **Naming
  which host to look up is itself a literal**: `hcloud server describe
  edge1` still fires even though the *address* it returns is resolved at
  runtime, because the *hostname* is still hardcoded rather than threaded
  through a variable. **A hostname substring inside a placeholder token is
  not a hit**: `edge1` inside `<edge1-ipv4>` is excepted, because it is
  textually part of an OPV002 placeholder standing in for a value, not a
  committed value itself — without this exception the two rules would
  co-fire on the same span, which contradicts their own reason for being
  separate ids. Extend the list as the estate's fixed inventory grows; it
  is deliberately not a general hostname-shape detector, the same tradeoff
  `docs-lint` makes for its own fixed id-shape exemptions.

### Known limitation: the hostname list is a snapshot, not a promise

`edge1`/`app1`/`db1`/`mx1`/`mon1` is the estate's fixed-hostname inventory as
measured when this rule shipped, not a self-maintaining list. `mon1` is the
concrete case worth naming: it currently shares a machine with another host,
but it is a real, separately-addressable name already referenced in
architecture documentation, and it is exactly the shape of host this list
has to keep pace with — a service that starts out co-located and later gets
its own box. A host that gets provisioned, renamed, or split out after this
rule ships is invisible to OPV001 until someone adds it here; nothing
detects the gap automatically. Add a name to the list as soon as a host it
should cover exists, the same discipline `docs-lint`'s own hand-maintained
id-shape exemptions ask of whoever touches them.

### OPV002: an unsubstituted placeholder in a copy-pasteable command

A placeholder is either:

- **a lowercase token containing a hyphen or dot** — `<edge1-ipv4>`,
  `<generated-repo>`, `<mail-host>`, `<tenant.slug>` — the shape this
  estate's own measurement found dominates real runbooks (`<mail-host>`
  alone: 19 occurrences in one file's bash fences), or
- **a bare single word from a fixed list** — `host`, `domain`, `secret`,
  `password`, `passphrase`, `token`, `key`, `value`, `ip`, `uuid`, `id`,
  `salt`, `region`, `hostname`, `address`, `url`, `email`, `port`,
  `username`, `repo`, `slug`. Extend this list as real cases turn up; a word
  outside it (`<widget>`) is not caught, the same tradeoff `docs-lint`'s own
  curated exemption lists make.

Both shapes require the token to run right up against the angle brackets
with **no space inside them**. That single constraint is what separates a
placeholder from shell redirection and comparison without needing a shell
parser: `sort < input.txt`, `[ "$a" -lt "$b" ]` and `node_load1 < 1` all put
a space immediately after `<` or before `>`, which neither shape allows. A
heredoc opener (`<<EOF`) has no closing `>` at all, so it can never satisfy
either shape regardless of case. An HTML tag or a generic (`<div>`,
`Map<string, number>`) fails on the character class itself — commas, spaces
and uppercase letters aren't part of either shape.

**Scope is the opposite of every other rule here.** OPV001/OPV003/OPV004
read the whole file because a leaked credential or address is real wherever
it sits. A placeholder is only a defect *inside an instruction meant to be
pasted into a shell* — the same token sitting in prose, a topology table, or
a `yaml`/`json`/`ts` fence is reference material or configuration, not a
paste-and-run failure, so OPV002 scans **only** `bash`/`sh`/`shell`/
`console` fenced code blocks in markdown files. An untagged fence is **not**
treated as a command fence here, unlike a reply-time placeholder guard's own
convention — this estate's own runbooks consistently tag their
copy-pasteable blocks, so the narrower rule is the correct default. This is
a settled scope decision, not an open question this rule is still deciding:
widening it (untagged fences, other extensions, other fence languages) is
separately tracked follow-up, taken up on its own schedule rather than as
part of shipping this check.

### The two documented exemption cases

These are the two cases the org's ruling named by name when it decided this
rule family needed an exemption mechanism at all — both are exercised as
positive test fixtures in `opv-lint.test.sh`, verbatim:

- **A test fixture where the value under test is the address or hostname
  itself** (a promtool alert-rule fixture, an address-plan-drift test, a
  `render.test.ts`-shaped unit test): `opv-disable-next-line OPV001 this
  hostname is the subject of the assertion; threading it would make the
  test assert on the environment`. Threading the value through an env var
  would make the test assert on whatever the environment happens to hold,
  which inverts what a fixture is for.
- **A template repository's placeholder for a thing that does not exist
  yet** (`ghost-platform-tenant-template`'s `<generated-repo>`):
  `opv-disable-next-line OPV002 no command can resolve this; the repository
  does not exist until the template is instantiated`. The pattern this rule
  enforces assumes the value exists somewhere to be looked up; a template's
  own placeholder slot has nothing to look up by construction.

### Known limitation: OPV003 is AWS-shaped only

A Hetzner Object Storage access key id has no comparable fixed prefix to key
on, so it is not caught by shape. It is still caught by OPV004 when it is
committed the way this estate's runbooks actually commit one: as a direct
assignment (`export AWS_ACCESS_KEY_ID='...'`) rather than a `read`.

### OPV004 is case-insensitive, and a fixed set of non-secret literals is excluded

The name match (`SECRET`, `PASSWORD`, `PASSPHRASE`, `ACCESS_KEY`,
`PRIVATE_KEY`, `TOKEN`, `CREDENTIAL`, `API_KEY`, `ENCRYPTIONSALT`) is
case-insensitive: `db_password`, `apiToken`, `SSH_PRIVATE_KEY` and
`EncryptionSalt` all fire identically. camelCase and snake_case are the
idiomatic naming convention in two of the six extensions this check covers
(`.py`, `.js`/`.ts`), so a case-sensitive match would have missed most of
its real target in exactly those files.

A small fixed set of non-secret-shaped literal values is excluded so a
feature flag doesn't fire identically to a leak: `true`, `false`, `yes`,
`no`, `on`, `off`, `enabled`, `disabled` (case-insensitively). This is
**minimal shape discrimination, not general value-shape analysis** — a short
enum-like value outside that fixed list (`FEATURE_TOKEN="beta"`,
`LOG_LEVEL_TOKEN="prod"`) still fires, because nothing distinguishes a
genuinely short secret from a genuinely short non-secret word without a much
larger, harder-to-maintain list. Extend the list, or suppress inline, as
real cases turn up — the exemption's own reasoning (an inline reason beats a
silent carve-out) applies here too.

A default-value expansion is **not** treated as a safe re-export, even
though it starts with `$`: `export DB_PASSWORD="${DB_PASSWORD:-a-literal-fallback}"`
still fires, because the fallback itself can be a real secret. Only a bare
`$VAR`, `${VAR}` or `$(...)` — nothing else inside the quotes — is recognised
as a pure re-export.

### Known limitation: OPV004 is `=`-assignment-shaped only

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

### Known limitation: file types

The same markdown/code extension list as docs-lint
(`.md`/`.mdx`, `.ts`/`.tsx`/`.js`/`.jsx`/`.mjs`/`.cjs`/`.py`/`.sh`/`.bash`/`.yml`/`.yaml`/`.tf`).
A `.json`, `.env` or other extension outside that list is not scanned.
Widening it is a smaller change than adding a rule and can follow once the
current set has proven itself.

### Private repos: no carve-out, by construction

An earlier draft of the org's ruling floated skipping this check for private
repos, then withdrew that carve-out — Reading B applies everywhere. There is
therefore nothing to build here: adoption is opt-in per repo exactly the way
`docs-lint` already works, and `docs-lint` itself has no repo-visibility
check anywhere in its workflow or its script — a repo participates only by
adding the caller stanza to its own `.github/workflows/`. A private repo
that wants out simply never adds `opv-lint.yml`'s caller; nothing here reads
or reacts to a repo's visibility setting, so there is no mechanism that
could silently do the wrong thing if a repo's visibility changes later.

## Suppressing a finding

Same two tiers as docs-lint, and the identical mechanism — only the
directive token and the rule-id namespace differ, so a suppression is never
honoured by a second, subtly different parser.

**One line**, on the line before the finding:

```yaml
# opv-disable-next-line OPV001 promtool fixture: this hostname is the subject of the assertion
```

```markdown
<!-- opv-disable-next-line OPV003 example key id, not a real credential -->
```

The rule id and a reason are both mandatory; a bare disable is reported as
OPV000.

**A path**, via `.opv-lintignore` at the repo root — tab-separated `glob`,
rule ids, reason:

```text
fixtures/*	OPV001	# fixture hostnames are the subject of the assertion, not a leak
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

The **clause** — the rule and its id, recorded in the org's standards
documentation — belongs in `standards/docs/index.md`, a different repo. It
is not part of this PR.
