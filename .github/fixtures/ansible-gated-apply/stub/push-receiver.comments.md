# Comment narrative for `push-receiver.sh`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 2-8)

```text
# The stand-in for the receiver of a pushed host, in the dry run of the plan job
# and in the self-test of the action. It receives the SSH destination, the mode,
# the commit and the playbook as arguments and a git bundle on standard input, as
# the real receiver does. It verifies the bundle with git into an empty scratch
# repository, requires the bundle's HEAD to be the commit it was asked about, and
# answers in the format of the receiver's summary line. It applies nothing and
# reaches nothing.
```
