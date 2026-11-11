#!/usr/bin/env python3
"""Stands in for `gh` in the tests of verify-package-attestations.py.

Two calls are answered and every other call exits 99:
  gh attestation verify FILE --repo R --signer-workflow S [--source-ref X] ...
  gh api repos/R/compare/main...SHA --jq .status

The verify call looks up $STUB_ATTEST_DIR/<sha256 of FILE>.json, the way the real
call looks the digest up in the attestation API. No file is the real wording for an
unknown digest (HTTP 404). With STUB_ENFORCE unset it then refuses what the real gh
refuses, in the words the real gh used against a live attestation; with
STUB_ENFORCE=0 it is a gh that accepts anything and prints the file as it is.
STUB_FAULT=network|tuf makes the verify call fail the way a fault does.
STUB_COMPARE_STATUS answers the compare call (default behind); STUB_COMPARE_FAIL=1
makes it fail. Every call is appended to $STUB_LOG when that is set.

`run` is the whole stub, so the tests can call it in-process; run as a script it
is the program the tests put on PATH.
"""

import hashlib
import json
import os
import re
import sys


def run(argv, environ):
    """Answer one call: return (exit status, standard output, standard error)."""
    if environ.get("STUB_LOG"):
        with open(environ["STUB_LOG"], "a", encoding="utf-8") as log:
            log.write(" ".join(argv) + "\n")
    if not environ.get("GH_TOKEN"):
        return 4, "", "gh: To use GitHub CLI in automation, set the GH_TOKEN environment variable.\n"
    if argv[:2] == ["attestation", "verify"]:
        return verify(argv[2], argv[3:], environ)
    if argv[:1] == ["api"] and re.fullmatch(r"repos/[^/]+/[^/]+/compare/main\.\.\.[0-9a-f]{40}", argv[1]):
        if environ.get("STUB_COMPARE_FAIL") == "1":
            return 1, "", "gh: HTTP 500\n"
        return 0, environ.get("STUB_COMPARE_STATUS", "behind") + "\n", ""
    return 99, "", "stub gh: unexpected call: " + " ".join(argv) + "\n"


def verify(target, rest, environ):
    flags = {}
    rest = list(rest)
    while rest:
        flag = rest.pop(0)
        flags[flag] = "true" if flag == "--deny-self-hosted-runners" else rest.pop(0)
    fault = environ.get("STUB_FAULT")
    if fault == "network":
        return 1, "", 'Error: Get "https://api.github.com/repos/x/y/attestations/sha256:aaaa": dial tcp: lookup api.github.com: no such host\n'
    if fault == "tuf":
        return 1, "", "Error: Sigstore verification failed: failed to fetch the TUF root: connection reset by peer\n"
    with open(target, "rb") as handle:
        digest = hashlib.sha256(handle.read()).hexdigest()
    path = os.path.join(environ["STUB_ATTEST_DIR"], digest + ".json")
    not_found = f"Error: HTTP 404: Not Found (https://api.github.com/repos/{flags.get('--repo')}/attestations/sha256:{digest}?per_page=30)\n"
    if not os.path.exists(path):
        return 1, "", not_found
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    if environ.get("STUB_ENFORCE") == "0":
        return 0, text + "\n", ""
    certificate = json.loads(text)[0]["verificationResult"]["signature"]["certificate"]
    if flags.get("--repo") != certificate["githubWorkflowRepository"]:
        return 1, "", not_found
    if not certificate["subjectAlternativeName"].startswith("https://github.com/" + flags["--signer-workflow"]):
        return 1, "", 'Error: verifying with issuer "sigstore.dev"\n'
    if "--source-ref" in flags and flags["--source-ref"] != certificate["sourceRepositoryRef"]:
        return 1, "", f"Error: expected SourceRepositoryRef to be {flags['--source-ref']}, got {certificate['sourceRepositoryRef']}\n"
    if "--deny-self-hosted-runners" in flags and certificate["runnerEnvironment"] != "github-hosted":
        return 1, "", 'Error: verifying with issuer "sigstore.dev"\n'
    return 0, text + "\n", ""


if __name__ == "__main__":
    status, out, err = run(sys.argv[1:], os.environ)
    sys.stdout.write(out)
    sys.stderr.write(err)
    sys.exit(status)
