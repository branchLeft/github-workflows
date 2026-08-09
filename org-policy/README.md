# org-policy

Branch and tag ruleset payloads for the `branchLeft`-owned repos that are not
covered by a live, hand-configured ruleset, held here so applying the org
standard is a script run rather than a click-through per repo.

## The standard

Every payload here is the same shape as the rulesets already live on `website`,
`components`, `github-workflows` and `.github`: on the default branch, no
deletion, no force-push, linear history, signed commits, and a pull request with
one approving review, code-owner review, stale-review dismissal, last-push
approval, resolved threads and squash-only merge. The single bypass actor is
`OrganizationAdmin` in `pull_request` mode, so a repo admin can land a merge the
rules would otherwise block without holding a standing write exemption.

Release-tag payloads block `deletion`, `update` and `non_fast_forward` on
`refs/tags/v*.*.*` and require signatures, with no bypass actor at all. Tags are
immutable org-wide: every change ships as a new tag.

## Coverage

| Repo | Payloads | Live? |
|---|---|---|
| `ghost-platform` | `main`, `release-tags` | yes |
| `ghost-platform-docs` | `main` | no — private, endpoint 403s |
| `ghost-platform-tenant-template` | `main` | no — private, endpoint 403s |
| `shared-infra` | `main` | no — private, endpoint 403s |

GitHub Free returns `403 Upgrade to GitHub Pro` for the rulesets endpoint on a
private repo, so the three private payloads cannot be applied until a repo goes
public or the org upgrades. They are committed and reviewed as code so that
moment is a script run, not a redesign.

`ghost-platform` requires four status checks, one per gate that runs on every
pull request to `main`: `docker build`, `Platform type check`,
`Type check (provisioning)` and `Tenant type check`. `docs-lint` is deliberately
not required there — that repo runs it in `warn` mode, and requiring a check the
repo has opted to treat as advisory would contradict the mode. CodeQL is not
required either, matching `website`.

The three private payloads carry no status checks. Their contexts cannot be read
back or verified while the endpoint 403s, and a required context that never
reports blocks every merge in the repo — so those are added per repo at the point
its rules can actually be applied.

The four repos whose rulesets were configured by hand are not represented here.
Bringing them under the same audit is worthwhile and is its own change: each
needs its live payload captured, including its own status-check set.

## Usage

```bash
./org-policy/audit.sh                  # compare live state to these payloads
./org-policy/apply.sh ghost-platform   # apply every payload for one repo
./org-policy/apply.sh                  # apply for all four
```

`apply.sh` matches a live ruleset by name and updates it in place, creating one
only when no ruleset of that name exists, so re-running it cannot produce
duplicates.

`audit.sh` reports each payload as `ok`, `MISSING` or `DRIFT` (with a diff), and
exits non-zero if any is either. A private repo whose endpoint 403s is reported
as blocked and does not fail the run. Both scripts need only `gh` and `python3`.

## Layout

`rulesets/<repo>/<name>.json` — one directory per repo. The directory is not
cosmetic: repo names here share prefixes (`ghost-platform` is a prefix of
`ghost-platform-docs`), so a flat `<repo>-<name>.json` glob selects payloads
belonging to other repos.
