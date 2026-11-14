# lint-common.sh

Shared scan/report/exemption machinery for this repo's lint checks
(docs-lint.sh, opv-lint.sh). Sourced, never executed directly.

Each caller owns its own rule ids, patterns and messages; what lives here
is the mechanism every check needs identically: resolving which files are
enforced under warn-vs-enforce mode, the two exemption tiers (a path glob
in an ignorefile, an inline disable-next-line comment), position-preserving
span blanking so an except-pattern only removes the text that matched it,
and the scan-report-summarise loop itself.

A caller sources this, then sets two variables before calling run_rule or
check_malformed_suppression:

  LINT_IGNOREFILE  - e.g. .docs-lintignore or .opv-lintignore
  LINT_PREFIX      - the disable-next-line token; each caller sets its own spelling

No runtime dependencies beyond bash + grep + awk, matching docs-lint.sh's
own constraint: several callers of these checks have no package.json and
no Node.
