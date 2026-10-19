#!/usr/bin/env bash
# Print how many earlier steps of this job wrote a line starting with the given
# text to their job summary, read from the runner's per-step summary files. That
# layout is the runner's, not a documented interface, so a missing directory
# fails loudly here rather than counting zero.
set -eu

dir="$RUNNER_TEMP/_runner_file_commands"
files=$(find "$dir" -name 'step_summary_*' 2> /dev/null)
if [ -z "$files" ]; then
  echo "::error::no step summary files under $dir: the runner layout changed, so the job summary cannot be observed" >&2
  exit 1
fi
count=0
for file in $files; do
  if grep -q -- "^$1" "$file"; then
    count=$((count + 1))
  fi
done
echo "$count"
