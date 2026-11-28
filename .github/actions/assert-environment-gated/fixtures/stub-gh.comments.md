# Comment narrative for `stub-gh.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-6)

```text
# Stands in for `gh`. Logs every call to $STUB_LOG, if set; any call that is not
# one of the two reads the action is meant to make exits 99. The two answers
# come from the files $STUB_ENV and $STUB_RULES. A file holding UNREADABLE makes
# the call fail as an API error would; one holding GARBAGE makes it print
# something that is not JSON and succeed.
```
