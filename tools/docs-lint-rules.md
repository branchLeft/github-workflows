# docs-lint rules

Enforces the mechanical parts of the [org documentation standard](https://github.com/branchLeft/.github/blob/main/docs/DOCUMENTATION-STANDARD.md). The parts a regex cannot see — whether a document actually describes the current state, whether a comment earns its place — remain review judgements.

Run it locally from a repo root:

```bash
path/to/docs-lint.sh --explain          # whole tree
path/to/docs-lint.sh --explain FILE...  # specific files
```

`--explain` prints the matched text for each finding, which is what you want when a rule fires somewhere surprising.

## Modes

`.docs-lint.mode` at the repo root controls how findings are treated.

| File | Behaviour |
|---|---|
| absent | **enforce** — any finding anywhere in the tree fails. This is the default so a new repo is protected without opting in. |
| contains `warn` | **ratchet** — the full-tree scan is advisory and never fails, but files changed on the branch are enforced and do fail. |

Warn mode exists so the gate can be wired into a repo that isn't clean yet: CI is green on day one, no new violation can land from that moment, and each cleanup PR is checked against the rules it is implementing. Flip to enforce by deleting the file.

Scope is `md` (markdown, fenced code blocks excluded), `code` (comment lines only, in `.ts/.tsx/.js/.jsx/.mjs/.cjs/.py/.sh/.bash/.yml/.yaml/.tf`), or both. Comment extraction never reads string literals, so prose inside JSX or marketing copy is structurally out of scope.

## Rules

| ID | Scope | Catches | Fix |
|---|---|---|---|
| DL000 | both | A suppression comment with no rule id or no reason | Write `docs-lint-disable-next-line DL006 <why>` |
| DL001 | both | `Rob`, `Rob's`, `Robert` | State the decision itself, or use a role noun |
| DL002 | both | `Rob-only`, `Rob-gated` | Use the role: platform owner, repo admin, operator |
| DL004 | md | `**Status:**` headers | Delete. A committed document is the current description of the thing |
| DL005 | md | `~~`, `superseded/supersedes/superseding`, `struck through` | Delete the old thing, describe the new one |
| DL006 | md | Bolded or parenthesised ISO dates, `verified live <date>`, `As of <date>`, `Applied <date>` | Delete. History lives in `git log` and the PR |
| DL007 | code | `verified live`, `verified against the live`, `confirmed in production on` | State the constraint, not when it was checked |
| DL008 | code | `adversarial review`, `round N`, `per X's … decision`, `as discussed/agreed/we decided` | State what the code must satisfy, not how it got there |
| DL009 | both | Story, backlog and standards-gap ids (`S10`, `B22`, `Q52`) | Describe the change, not the ticket |
| DL010 | md | Links that escape the repo root, or point at a file that doesn't exist | Use an absolute GitHub URL for cross-repo references |
| DL011 | code | Comment blocks of 7+ lines | **Advisory only, never fails** — see below |

### Why DL001 is case-sensitive

`\bRob\b` with no `-i`. This is what lets `rob@branchleft.co.uk` through, which the standard exempts as an operational value a runbook legitimately carries, and the word boundary rejects `Roboto`, `robots`, `robust`, `probably`, `problem`.

### Known limitation: `S3`

`S3` is exempt from DL009 entirely. In this codebase it is both the AWS service and a real story id, and no pattern separates them — an earlier draft without the exemption was 71% false positives, almost all `S3-compatible` and `S3Storage`. A story `S3` reference will therefore slip through; story ids from `S7` upward are still caught. Fewer, trustworthy findings beat complete ones that people learn to ignore.

### Known limitation: `Q1`-`Q4`

`Q1` through `Q4` are exempt from DL009 for the same reason as `S3`: they collide with calendar quarters (`Q3 launch`, `Q4 target`), a phrase this org's roadmap and marketing prose uses routinely, and no pattern separates a quarter reference from a low-numbered `Q`-item id. Standards-gap ids from `Q5` upward are still caught. `DEP-<n>` (dependency-policy ids) is a different id shape, filed in the affected repo rather than a private tracker, and is out of scope for DL009.

### Why DL011 never fails

The standard says a long comment is *usually* a document in the wrong file. "Usually" is a review judgement, not something a regex can settle — some infrastructure comments genuinely need six lines to explain a constraint. DL011 emits a notice with the block length so the backlog stays visible, and a reviewer decides. The test to apply: if an operator would ever follow it as a procedure, it is a runbook, not a comment.

## Suppressing a finding

Three tiers, all requiring a reason. Every suppression in the fleet is greppable in one command, which is the point.

**One line**, on the line before the finding:

```markdown
<!-- docs-lint-disable-next-line DL006 upstream release date, part of the fact -->
```

```ts
// docs-lint-disable-next-line DL009 matches an upstream issue number, not ours
```

The rule id and a reason are both mandatory; a bare disable is reported as DL000.

**A path**, via `.docs-lintignore` at the repo root — tab-separated `glob`, rule ids, reason:

```text
templates/*	DL006,DL010	# contract scaffolds: [DATE] tokens are the mechanism
```

`*` matches across directory separators, so `templates/*` covers nested paths. `ALL` exempts every rule for that path.

**A repo**, via `.docs-lint.mode` — see Modes above.

## Structural markdown checks

`markdownlint-cli2` runs six rules on top of the above: MD001 (heading increment), MD012 (consecutive blanks), MD024 (duplicate sibling headings), MD034 (bare URLs), MD040 (fenced blocks declare a language), MD047 (trailing newline). Everything else is off so it never competes with Prettier, which already owns markdown formatting where it runs.

Config lives here, in `tools/docs.markdownlint-cli2.jsonc`, and is passed by path — no per-repo config file exists. This is the only part of the check with a runtime dependency, and it is skipped with a notice where `npx` is unavailable.
