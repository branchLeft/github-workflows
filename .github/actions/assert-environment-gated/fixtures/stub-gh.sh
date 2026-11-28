#!/bin/bash
# Stands in for `gh`. Logs every call to $STUB_LOG, if set; any call that is not
# one of the two reads the action is meant to make exits 99. The two answers
# Narrative: stub-gh.comments.md, block 1.
[ -z "${STUB_LOG:-}" ] || echo "$*" >> "$STUB_LOG"
[ "$1" = "api" ] || exit 99
case "$2" in
  repos/*/environments/*/deployment_protection_rules) src="$STUB_RULES" ;;
  repos/*/environments/*) src="$STUB_ENV" ;;
  *) exit 99 ;;
esac
case "$(cat "$src")" in
  UNREADABLE) echo "gh: HTTP 500" >&2; exit 1 ;;
  GARBAGE) echo "<html>not json</html>"; exit 0 ;;
  *) cat "$src" ;;
esac
