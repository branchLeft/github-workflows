#!/usr/bin/env python3
"""Exit 0 only if a release tag is an annotated tag signed by an allowed signer.

Usage: verify-tag-signer.py TAG REF_JSON TAG_JSON ALLOWED_SIGNERS
Rules, shapes and reasons: verify-tag-signer.md
"""
import base64
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

TAG_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")
KEY_TYPES = ("ssh-ed25519", "sk-ssh-ed25519@openssh.com")
SIG_BEGIN = "-----BEGIN SSH SIGNATURE-----"
SIG_END = "-----END SSH SIGNATURE-----"
MAX_ALLOWED_BYTES = 8192
KEYGEN_SECONDS = 20


class Refusal(Exception):
    """A reason the tag is not accepted. Every path out of here exits non-zero."""


def split_options(text):
    """Split an allowed-signers option string on commas outside double quotes."""
    parts, current, quoted = [], "", False
    for char in text:
        if char == '"':
            quoted = not quoted
        if char == "," and not quoted:
            parts.append(current)
            current = ""
        else:
            current += char
    if quoted:
        raise Refusal("the allowed signers file has an option with an unclosed quote")
    parts.append(current)
    return parts


def parse_allowed_signers(text):
    """Return the entries as (principal, key_type, key_blob). Anything outside
    the one accepted shape is refused, so a file cannot widen the trust by a
    certificate authority, a key for every namespace or private material."""
    if len(text.encode("utf-8", "replace")) > MAX_ALLOWED_BYTES:
        raise Refusal("the allowed signers file is larger than %d bytes" % MAX_ALLOWED_BYTES)
    if "PRIVATE KEY" in text:
        raise Refusal("the allowed signers file holds private key material")
    entries = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        where = "line %d of the allowed signers file" % number
        fields = line.split()
        if len(fields) < 3:
            raise Refusal("%s is not principal, options, key type and key" % where)
        principal = fields[0]
        if not PRINCIPAL_RE.match(principal):
            raise Refusal("%s names a principal outside letters, digits, dot, at, underscore, hyphen" % where)
        rest = fields[1:]
        options = []
        if rest[0] not in KEY_TYPES:
            options = split_options(rest[0])
            rest = rest[1:]
            if len(rest) < 2:
                raise Refusal("%s has options but no key" % where)
        if "namespaces=\"git\"" not in options:
            raise Refusal('%s lacks the option namespaces="git"' % where)
        if any(option.split("=", 1)[0].strip().lower() == "cert-authority" for option in options):
            raise Refusal("%s is a certificate authority, not a key" % where)
        key_type, key_body = rest[0], rest[1]
        if key_type not in KEY_TYPES:
            raise Refusal("%s has the key type %s, not one of %s" % (where, key_type[:40], ", ".join(KEY_TYPES)))
        try:
            blob = base64.b64decode(key_body, validate=True)
        except ValueError:
            raise Refusal("%s has a key that is not base64" % where) from None
        if not blob.startswith(len(key_type).to_bytes(4, "big") + key_type.encode()):
            raise Refusal("%s has a key whose body is not of the type it names" % where)
        entries.append((principal, key_type, key_body))
    if not entries:
        raise Refusal("the allowed signers file holds no entry, so no signer is allowed")
    return entries


def check_documents(tag, ref_doc, tag_doc):
    """Return (signature, payload) after the structural checks on the two API
    answers. The checks tie the signed bytes to the tag the caller named and the
    commit it points at, so a valid signature on another object is not enough."""
    if not isinstance(tag, str) or not TAG_RE.match(tag):
        raise Refusal("the tag name must be v then three dot-separated numbers")
    if not isinstance(ref_doc, dict) or not isinstance(ref_doc.get("object"), dict):
        raise Refusal("the tag reference answer is unreadable (an error body?)")
    ref_object = ref_doc["object"]
    if ref_doc.get("ref") != "refs/tags/" + tag:
        raise Refusal("the reference answer is not for refs/tags/%s" % tag)
    if ref_object.get("type") != "tag":
        raise Refusal("the tag %s is lightweight (it points straight at a %s), so it carries no signature" % (tag, str(ref_object.get("type"))[:20]))
    tag_sha = ref_object.get("sha")
    if not isinstance(tag_sha, str) or not SHA_RE.match(tag_sha):
        raise Refusal("the tag object id is not 40 lowercase hex digits")
    if not isinstance(tag_doc, dict) or tag_doc.get("sha") != tag_sha:
        raise Refusal("the tag object answer is not for the object the reference names")
    target = tag_doc.get("object")
    if not isinstance(target, dict) or target.get("type") != "commit":
        raise Refusal("the tag does not point at a commit")
    commit_sha = target.get("sha")
    if not isinstance(commit_sha, str) or not SHA_RE.match(commit_sha):
        raise Refusal("the commit id is not 40 lowercase hex digits")
    verification = tag_doc.get("verification")
    verification = verification if isinstance(verification, dict) else {}
    signature, payload = verification.get("signature"), verification.get("payload")
    if not isinstance(signature, str) or not signature.strip() or not isinstance(payload, str) or not payload:
        raise Refusal("the tag %s is unsigned: the answer holds no signature to check" % tag)
    stripped = signature.strip()
    if not (stripped.startswith(SIG_BEGIN) and stripped.endswith(SIG_END)):
        raise Refusal("the tag %s is not signed with an SSH signature" % tag)
    headers = payload.split("\n\n", 1)[0].split("\n")
    if "tag " + tag not in headers:
        raise Refusal("the signed text does not name the tag %s" % tag)
    if "object " + commit_sha not in headers or "type commit" not in headers:
        raise Refusal("the signed text does not name the commit the tag points at")
    return signature, payload


def run_keygen(args, payload):
    """Run ssh-keygen with an argv list. Returns (exit code, combined output)."""
    try:
        done = subprocess.run(
            ["ssh-keygen", *args], input=payload, capture_output=True, timeout=KEYGEN_SECONDS, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refusal("ssh-keygen could not be run: %s" % type(exc).__name__) from None
    return done.returncode, (done.stdout + done.stderr).decode("utf-8", "replace")


def verify(tag, ref_doc, tag_doc, allowed_text):
    """Return the line to print when the tag is signed by an allowed signer."""
    signature, payload = check_documents(tag, ref_doc, tag_doc)
    entries = parse_allowed_signers(allowed_text)
    with tempfile.TemporaryDirectory() as workdir:
        allowed = Path(workdir, "allowed_signers")
        sig = Path(workdir, "tag.sig")
        allowed.write_text(allowed_text, encoding="utf-8")
        sig.write_text(signature.strip() + "\n", encoding="utf-8")
        for principal in sorted({entry[0] for entry in entries}):
            code, output = run_keygen(
                ["-Y", "verify", "-f", str(allowed), "-I", principal, "-n", "git", "-s", str(sig)],
                payload.encode("utf-8"))
            if code == 0:
                return "the tag %s is signed by an allowed signer (%s)" % (tag, output.strip().splitlines()[0][:120])
    raise Refusal("the tag %s is not signed by any signer in the allowed signers file" % tag)


def read_text(path, label):
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        raise Refusal("%s could not be read: %s" % (label, type(exc).__name__)) from None


def read_json(path, label):
    try:
        return json.loads(read_text(path, label))
    except ValueError:
        raise Refusal("%s is not JSON" % label) from None


def main(argv):
    if len(argv) != 4:
        print(__doc__, file=sys.stderr)
        return 2
    tag, ref_path, tag_path, allowed_path = argv
    try:
        line = verify(tag, read_json(ref_path, "the tag reference answer"),
                      read_json(tag_path, "the tag object answer"),
                      read_text(allowed_path, "the allowed signers file"))
    except Refusal as exc:
        print("::error::refusing: %s" % exc, file=sys.stderr)
        return 1
    print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
