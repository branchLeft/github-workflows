#!/usr/bin/env python3
"""Stands in for `curl` in the tests of verify-package-attestations.py.

Behaviour and environment switches: README.md in this folder. `run` is the whole stub, so tests can call it in-process.
"""

import os
import re
import shutil
import sys


def run(argv, environ, stdin):
    """Answer one call: return (exit status, standard output, standard error)."""
    if environ.get("STUB_LOG"):
        with open(environ["STUB_LOG"], "a", encoding="utf-8") as log:
            log.write("curl " + " ".join(argv) + "\n")
    if not re.fullmatch(r'header = "Authorization: Bearer \S+"\n', stdin or ""):
        return 97, "", "stub curl: no authorization header on standard input\n"
    token = environ.get("GH_TOKEN", "")
    if token and token in " ".join(argv):
        return 98, "", "stub curl: the token is on the command line\n"
    url = argv[-1]
    match = re.fullmatch(r"https://npm\.pkg\.github\.com/download/@branchleft/([^/]+)/([^/]+)/[0-9a-f]{40}", url)
    if not match or "--fail" not in argv or "--proto" not in argv or "--output" not in argv:
        return 99, "", f"stub curl: unexpected call: {argv}\n"
    source = os.path.join(environ["STUB_TARBALLS"], f"{match.group(1)}-{match.group(2)}.tgz")
    if not os.path.exists(source):
        return 22, "", "curl: (22) The requested URL returned error: 404\n"
    shutil.copy(source, argv[argv.index("--output") + 1])
    return 0, "", ""


if __name__ == "__main__":
    status, out, err = run(sys.argv[1:], os.environ, sys.stdin.read())
    sys.stdout.write(out)
    sys.stderr.write(err)
    sys.exit(status)
