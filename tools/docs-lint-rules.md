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
| DL012 | code | `branchLeft/repo#N` work-item references (`branchLeft/ghost-platform#139`) | Put the reference in the PR body or a doc; state the constraint in the comment |

### Why DL001 is case-sensitive

`\bRob\b` with no `-i`. This is what lets `rob@branchleft.co.uk` through, which the standard exempts as an operational value a runbook legitimately carries, and the word boundary rejects `Roboto`, `robots`, `robust`, `probably`, `problem`.

### Known limitation: `S3`

`S3` is exempt from DL009 entirely. In this codebase it is both the AWS service and a real story id, and no pattern separates them — an earlier draft without the exemption was 71% false positives, almost all `S3-compatible` and `S3Storage`. A story `S3` reference will therefore slip through; story ids from `S7` upward are still caught. Fewer, trustworthy findings beat complete ones that people learn to ignore.

### Known limitation: `Q1`-`Q4`

`Q1` through `Q4` are exempt from DL009 for the same reason as `S3`: they collide with calendar quarters (`Q3 launch`, `Q4 target`), a phrase this org's roadmap and marketing prose uses routinely, and no pattern separates a quarter reference from a low-numbered `Q`-item id. Standards-gap ids from `Q5` upward are still caught, including when a real id shares a line with an exempt low-numbered form — e.g. "the Q3 push covers the Q52 fix" still flags `Q52`. `DEP-<n>` (dependency-policy ids) is a different id shape, filed in the affected repo rather than a private tracker, and is out of scope for DL009.

### Why DL011 never fails

The standard says a long comment is *usually* a document in the wrong file. "Usually" is a review judgement, not something a regex can settle — some infrastructure comments genuinely need six lines to explain a constraint. DL011 emits a notice with the block length so the backlog stays visible, and a reviewer decides. The test to apply: if an operator would ever follow it as a procedure, it is a runbook, not a comment.

### Why DL012 is code-only, and fires on the *correct* reference form too

DL009 catches the id shape the estate retired. It has no opinion on the shape that replaced it — `branchLeft/repo#N` — so a comment built entirely from the correct, current identifier passes every gate. DL012 closes that: it matches `branchLeft/repo#N` in a source comment and fails, regardless of whether the reference itself is well-formed, because the standard's comment-style rule doesn't carve out an exception for a correctly-shaped ticket id — a comment states what the code itself cannot, never development-process context, and a work-item reference is process context by definition. The fix is never "use the other id form"; it's moving the reference to the PR body or a doc and leaving the comment to state the constraint on its own.

This is why DL012 has no `md` scope, unlike DL009. `branchLeft/repo#N` is the standard's own mandated, correct form for a reference in commit messages, PR bodies and Markdown prose — flagging it there would be flagging correct usage. The rule only has anything to say about comments in source.

### Known limitation: only the `branchLeft` owner is matched

DL012 matches `branchLeft/<repo>#<n>`, not a generic `<owner>/<repo>#<n>`. Two reasons, both found by review rather than assumed up front:

- **Upstream references are a different thing than board narration.** A comment citing a third-party issue to explain a workaround — `# workaround for actions/runner#1327, remove once upstream ships a fix` — is exactly the kind of constraint the code-comment standard asks for, not development-process narration about branchLeft's own board. A generic `owner/repo#N` match cannot tell that citation apart from a reference to branchLeft's own tracker, and the two call for opposite verdicts.
- **A generic pattern also collides with ordinary URL fragments.** `word/word#digits` matches things like a documentation link's numbered anchor (`docs.example.com/guide/setup#42`) that have nothing to do with an issue reference at all.

Restricting the owner to the literal, case-sensitive string `branchLeft` — matching this org's own naming convention (`## Naming repos and PRs`: a repo is always `org/full-name`, and every reference is `org/repo#N`, and every one of them is `branchLeft/...`) — resolves both: an upstream citation and a URL fragment never start with that literal, so neither false-matches, and every real branchLeft board reference still does.

### Known limitation: a bare `#N`

A bare `#N` is deliberately not matched, for the same reason `S3` and `Q1`-`Q4` are carved out of DL009: it collides with far too much (CSS ids, shell `$#`, port numbers, markdown footnotes) for a regex to separate a real reference from noise. The false negative is accepted; a rule people learn to ignore is worse than one with a known gap.

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
