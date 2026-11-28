# Comment narrative for `look_results.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-9)

```text
"""Derive, per kind, every result the look of shared-infra's pull_trigger.py can print.

The look (pull_trigger README, "The look") prints `look issue=... kind=K result=R`. R is one of a closed set of
words, a digest, a status or a key, and ssh-check-hosts judges a copy of that line by a regex. This reads the
program with ast and lists every word its result functions can return, so the regex is held to the program and
not to a hand-typed list.

Usage: look_results.py PULL_TRIGGER_PY SOURCE_SHA > tools/look-results.json
"""
```
