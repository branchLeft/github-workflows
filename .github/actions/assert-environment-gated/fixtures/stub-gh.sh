#!/bin/bash
# Stands in for `gh`. Logs every call to $STUB_LOG, if set; any call that is not
# one of the two reads the action is meant to make exits 99. The two answers
# come from the files $STUB_ENV and $STUB_RULES. A file holding UNREADABLE makes
# the call fail as an API error would; one holding GARBAGE makes it print
# something that is not JSON and succeed.
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
