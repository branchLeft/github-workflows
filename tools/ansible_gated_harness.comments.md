# Comment narrative for `ansible_gated_harness.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-8)

```text
"""The harness the ansible-gated test modules share: a run's result and a scratch directory.

test_ansible_gated_apply.py and test_ansible_gated_step.py each keep their own stand-in
commands and their own environment for a run, because those differ between the two gates.
What is the same lives here: the result of a run, the scratch directory with the stand-in
commands on PATH, and the subprocess call that runs a step's script with the runner's
default umask.
"""
```

## Block 2 (source lines 38-42)

```text
    """A scratch directory with stand-in commands on PATH and the files the runner provides.

    `stub_commands` are (name, text) pairs written as executables into bin/. Subclasses build
    the environment for `run_script` themselves, because each gate's environment differs.
    """
```
