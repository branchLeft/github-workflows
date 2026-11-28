# Comment narrative for `script_import.py`

The source keeps the first lines of each long comment block and points here.

## Block 1 (source lines 1-7)

```text
"""Import an action's hyphenated script by path, for that action's tests.

Shared by the action tests under .github/actions/. A script's filename has hyphens, so
it is not a legal module name for a plain import. When the named environment variable is
set, the tests load the script at that path instead: that is how a sabotage test breaks a
copy of the script and not the original.
"""
```

## Block 2 (source lines 26-30)

```text
    """Load `script` from the directory `here` as module `module_name`.

    `env_var`, when set and present in the environment, names the path to load instead.
    `register` also puts the module in sys.modules, for tests that patch through it.
    """
```
