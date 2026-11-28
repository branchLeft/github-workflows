"""The sabotage proof of tools/test_ansible_gated_apply.py, as a package.

tools/sabotage.py is the one entry point. The pieces:

- model.py: Edit, Case and Suite, the three types a case is written in.
- text.py: indented(), which puts a block of text at a column, so no case types the
  indentation of the file it breaks.
- files.py and suites.py: the names of the files that are broken and of the test classes.
- cases/: the cases, one module per family of controls.
- runner.py: breaks a copy of the repository and runs the tests in it.
- cli.py: the command line.
"""
