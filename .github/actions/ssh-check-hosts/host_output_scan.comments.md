# Comment narrative for `host_output_scan.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-23)

```text
"""Read what a host wrote for secrets, and name the Ansible task that failed.

One home for the scan of a host's output. The plan step of the ssh-check-hosts action reads each
host's diff through it, and the apply job of ansible-gated-apply.yml reads the standard error of a
host whose playbook failed through it. The text is read as bytes (Latin-1, so one character is one
byte, as a grep in the C locale reads it) and is never printed: the two lines on standard output
carry a list of pattern names and, when nothing matched, the name of one task.

    host_output_scan.py FILE [--keylines FILE] [--allowances TEXT]

    hits=<pattern names, comma and space separated, or none>
    task=<the text between the brackets of the last TASK header before the first fatal line, at most
          160 characters, or empty; always empty when hits is not none>

With --report the script prints instead, for a host whose diff is withheld, where its bytes are:
up to 15 lines `<bytes> TASK [<role> : <task name>]`, largest first, and for each pattern that
matched the task it fell under. A line of that report is a size, a pattern name or a TASK header
name (cut at 120 characters, any character outside letters, digits, space and `_ . / : ; , ( ) = -`
turned into `?`, and replaced by its place in the output when it reads as a secret itself); it is
never the text of any other line.

What the scan reads, and the named allowances: README, section "What the scan reads".
"""
```

## Block 2 (source lines 130-134)

```text
    """Blank each line that is exactly an allowance, under the diff header of its file.

    The whole line, after its first character and with its outer whitespace trimmed, must equal the
    entry's text. Only the text is blanked: its first character stays.
    """
```

## Block 3 (source lines 162-168)

```text
    """Each `key: >-` or `key: |` line joined with the more-indented lines under it.

    The assignment pattern reads one line at a time, so a secret written on the line after a block
    scalar indicator is read here, beside its key. In a diff each line starts with one character
    (`+`, `-` or a space) that is not part of the text, so it is taken off before the indent is
    measured. Blank lines are skipped; a line at the key's indent or less ends the scalar.
    """
```

## Block 4 (source lines 264-268)

```text
    """Where the bytes of a withheld output are, and which task each matched pattern fell under.

    Every line is built from a byte count, a pattern name, a fixed phrase or a label(), and from
    nothing else: no line of the output, and no value, path or diff text, is ever copied.
    """
```
