# opv-lint.sh

Blocks inline operational values committed in place of a reference.
Rule reference and rationale: opv-lint-rules.md (this folder).

Scan/report/exemption machinery shared with docs-lint.sh lives in
lint-common.sh; this file owns only its own rule ids, patterns and
messages.

Unlike docs-lint's comment-only / fence-blanked scan, OPV001/OPV003/OPV004
read the whole file (raw_scannable): a leaked key, a literal secret
assignment or a bare host address is exactly as real inside a pasted
command, a YAML value or a table cell as it is inside a comment, and
blanking any of those out would blank out the thing this check exists to
catch. OPV002 is the deliberate exception -- an unsubstituted placeholder
is only a defect inside a copy-pasteable command, so it reads ONLY
bash/sh/shell/console fences in markdown (command_fence_scannable).

No runtime dependencies beyond bash + grep + awk, matching docs-lint.sh's
own constraint.

Usage:
  opv-lint.sh [--explain] [--mode warn|enforce] [FILE...]

With no FILE arguments it scans every tracked file. With FILE arguments it
scans only those (this is how pre-commit invokes it).
