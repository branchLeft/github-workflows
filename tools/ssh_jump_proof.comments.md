# Comment narrative for `ssh_jump_proof.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-8)

```text
"""Proof, on real sshd, that the ssh-check-hosts jump hop works and its lock holds.
Gateway sshd on 127.0.0.2, target on 127.0.0.3, both port 22 (the action's inputs carry no port).
The second hop adds a second gateway on 127.0.0.5 and a target behind it on 127.0.0.6, each with
authorized_keys files of its own, as separate hosts have.
Runs the action's validate, check and report steps as written, with real ssh, and asserts the logs.
Needs root: re-runs itself under sudo, and removes every process, user and file it made on any exit.
Exit: 0 every assertion held, 1 one failed, 2 setup failed. Run: python3 tools/ssh_jump_proof.py
"""
```

## Block 2 (source lines 308-312)

```text
    """Return the default of every input and the run steps of the action, as written in the file.

    Raises:
        ActionFormatError: the file is not the composite action shape this proof reads.
    """
```

## Block 3 (source lines 335-339)

```text
    """Run one command to its end and return what it printed.

    Raises:
        SetupError: the command did not end within the time.
    """
```

## Block 4 (source lines 350-354)

```text
    """Run a setup command and return its standard output.

    Raises:
        SetupError: the command ended with a non-zero status.
    """
```

## Block 5 (source lines 428-432)

```text
    """Start one sshd in the foreground of a child process and wait until it accepts connections.

    keys_directory holds the authorized_keys.<account> files this server reads; a server of its own host has
    its own, so a key installed on one host is not accepted by another.
    """
```

## Block 6 (source lines 470-474)

```text
    """Build the whole proof environment, and tear it down on every way out.

    Raises:
        SetupError: a tool, a port, an account or a server is not as the proof needs it.
    """
```

## Block 7 (source lines 570-574)

```text
    """Run the validate, check and report steps of the action, as written, against the servers.

    hosts defaults to the one target. jump2 sets the second hop's host and key; jump2_hosts is the list sent
    through it.
    """
```
