"""Tests for verify-tag-signer.py, run as CI runs it: a subprocess and its real
exit code, over tags made and signed by real git and ssh-keygen.

The Sabotage class breaks each control in a copy of the script (or of the
code owners file), and the test that guards it must go red there and green on
the unmodified copy. Reasons for each case: verify-tag-signer.md.
"""
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT = os.environ.get("VERIFY_TAG_SIGNER_SCRIPT") or str(HERE / "verify-tag-signer.py")
CODEOWNERS = os.environ.get("VERIFY_TAG_SIGNER_CODEOWNERS") or str(HERE.parent.parent / "CODEOWNERS")
ALLOWED_FILE = HERE.parent.parent / "allowed_signers"
OWNER_TEAM = "@branchLeft/branchleft-admin"
EXPECTED_FINGERPRINT = "SHA256:x7pfNpY8E/43Jt6wQYCSp6xULw11iyvz6YH62H1mayk"
IN_SABOTAGE = bool(os.environ.get("VERIFY_TAG_SIGNER_SCRIPT") or os.environ.get("VERIFY_TAG_SIGNER_CODEOWNERS"))
SIG_BEGIN = "-----BEGIN SSH SIGNATURE-----"
GIT_ENV = {
    "PATH": os.environ.get("PATH", ""), "HOME": "/nonexistent", "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
}
STATE = {}


def run(argv, cwd=None, env=None, stdin=None):
    done = subprocess.run(argv, cwd=cwd, env=env, input=stdin, capture_output=True, check=False)
    if done.returncode != 0 and not env:
        raise RuntimeError("%s failed: %s" % (argv[0], done.stderr.decode()[:200]))
    return done


def keygen(directory, name, key_type="ed25519"):
    path = Path(directory, name)
    run(["ssh-keygen", "-q", "-t", key_type, "-N", "", "-C", "", "-f", str(path)])
    return path, path.with_suffix(".pub").read_text().split()


def git(repo, *args, sign_key=None):
    config = ["-c", "gpg.format=ssh", "-c", "user.signingkey=%s" % sign_key] if sign_key else []
    done = subprocess.run(["git", *config, *args], cwd=repo, env=GIT_ENV, capture_output=True, check=False)
    if done.returncode != 0:
        raise RuntimeError("git %s failed: %s" % (args[0], done.stderr.decode()[:200]))
    return done.stdout.decode()


def api_docs(repo, tag):
    """The two answers the GitHub API gives for a tag, rebuilt from the object
    git holds: the signed payload is the object text before the signature."""
    kind = git(repo, "cat-file", "-t", tag).strip()
    sha = git(repo, "rev-parse", tag).strip()
    ref = {"ref": "refs/tags/" + tag, "object": {"type": kind, "sha": sha}}
    if kind != "tag":
        return ref, None
    raw = git(repo, "cat-file", "tag", tag)
    payload, marker, rest = raw.partition("-----BEGIN ")
    signature = (marker + rest) if marker else None
    commit = git(repo, "rev-parse", tag + "^{commit}").strip()
    verification = {"signature": signature, "payload": payload if signature else None}
    return ref, {"sha": sha, "tag": tag, "object": {"type": "commit", "sha": commit}, "verification": verification}


def setUpModule():
    root = tempfile.mkdtemp(prefix="vts-")
    STATE["root"] = root
    keys = {}
    for name in ("owner", "other"):
        private, fields = keygen(root, name)
        keys[name] = (private, "%s %s" % (fields[0], fields[1]))
    repo = Path(root, "repo")
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "commit", "-q", "--allow-empty", "-m", "first")
    git(repo, "tag", "-s", "-m", "owner release", "v1.0.0", sign_key=keys["owner"][0])
    git(repo, "tag", "-s", "-m", "other release", "v2.0.0", sign_key=keys["other"][0])
    git(repo, "tag", "-a", "-m", "unsigned release", "v3.0.0")
    git(repo, "tag", "v4.0.0")
    STATE.update(keys=keys, repo=repo, docs={t: api_docs(repo, t) for t in ("v1.0.0", "v2.0.0", "v3.0.0", "v4.0.0")})


def tearDownModule():
    shutil.rmtree(STATE["root"], ignore_errors=True)


def entry(key, options='namespaces="git"', principal="release-signer"):
    return "%s %s %s\n" % (principal, options, key) if options else "%s %s\n" % (principal, key)


class Controls(unittest.TestCase):
    def cli(self, tag, ref, tag_doc, allowed):
        with tempfile.TemporaryDirectory() as workdir:
            paths = []
            for name, content in (("ref.json", ref), ("tag.json", tag_doc), ("allowed", allowed)):
                path = os.path.join(workdir, name)
                if content is not None:
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(content if isinstance(content, str) else json.dumps(content))
                paths.append(path)  # a None content leaves the path absent
            done = subprocess.run([sys.executable, "-I", SCRIPT, tag, *paths], capture_output=True, check=False)
        return done.returncode, done.stderr.decode() + done.stdout.decode()

    def owner_allowed(self):
        return entry(STATE["keys"]["owner"][1])

    def check(self, tag, allowed, expect_ok=False, reason=""):
        ref, tag_doc = STATE["docs"][tag]
        code, text = self.cli(tag, ref, {} if tag_doc is None else tag_doc, allowed)
        self.assertEqual(code, 0 if expect_ok else 1, text)
        self.assertIn(reason, text)

    def test_owner_signed_tag_accepted(self):
        self.check("v1.0.0", self.owner_allowed(), True, "signed by an allowed signer")

    def test_other_key_refused(self):
        self.check("v2.0.0", self.owner_allowed(), False, "not signed by any signer")

    def test_other_key_accepted_only_when_listed(self):
        both = self.owner_allowed() + entry(STATE["keys"]["other"][1], principal="second-signer")
        self.check("v2.0.0", both, True, "signed by an allowed signer")

    def test_unsigned_annotated_tag_refused(self):
        self.check("v3.0.0", self.owner_allowed(), False, "is unsigned")

    def test_lightweight_tag_refused(self):
        self.check("v4.0.0", self.owner_allowed(), False, "is lightweight")

    def test_empty_allowed_signers_refuses_everything(self):
        for tag in ("v1.0.0", "v2.0.0"):
            for content in ("", "\n", "# only a comment\n"):
                self.check(tag, content, False, "holds no entry")

    def test_signature_in_another_namespace_refused(self):
        private = STATE["keys"]["owner"][0]
        ref, tag_doc = STATE["docs"]["v1.0.0"]
        payload = tag_doc["verification"]["payload"]
        signed = run(["ssh-keygen", "-Y", "sign", "-f", str(private), "-n", "file"], stdin=payload.encode())
        forged = json.loads(json.dumps(tag_doc))
        forged["verification"]["signature"] = signed.stdout.decode()
        code, text = self.cli("v1.0.0", ref, forged, self.owner_allowed())
        self.assertEqual(code, 1, text)
        self.assertIn("not signed by any signer", text)

    def test_changed_payload_refused(self):
        ref, tag_doc = STATE["docs"]["v1.0.0"]
        forged = json.loads(json.dumps(tag_doc))
        forged["verification"]["payload"] = forged["verification"]["payload"].replace("owner release", "other release")
        code, text = self.cli("v1.0.0", ref, forged, self.owner_allowed())
        self.assertEqual(code, 1, text)
        self.assertIn("not signed by any signer", text)

    def test_valid_signed_object_under_another_tag_name_refused(self):
        ref, tag_doc = STATE["docs"]["v1.0.0"]
        renamed = dict(ref, ref="refs/tags/v9.9.9")
        code, text = self.cli("v9.9.9", renamed, tag_doc, self.owner_allowed())
        self.assertEqual(code, 1, text)
        self.assertIn("does not name the tag v9.9.9", text)

    def test_signed_text_for_another_commit_refused(self):
        ref, tag_doc = STATE["docs"]["v1.0.0"]
        forged = json.loads(json.dumps(tag_doc))
        forged["object"]["sha"] = "0" * 40
        code, text = self.cli("v1.0.0", ref, forged, self.owner_allowed())
        self.assertEqual(code, 1, text)
        self.assertIn("does not name the commit", text)

    def test_gpg_signature_refused(self):
        ref, tag_doc = STATE["docs"]["v1.0.0"]
        forged = json.loads(json.dumps(tag_doc))
        forged["verification"]["signature"] = "-----BEGIN PGP SIGNATURE-----\nAAAA\n-----END PGP SIGNATURE-----\n"
        code, text = self.cli("v1.0.0", ref, forged, self.owner_allowed())
        self.assertEqual(code, 1, text)
        self.assertIn("not signed with an SSH signature", text)

    def test_error_bodies_refused(self):
        body = {"message": "Not Found", "status": "404"}
        code, text = self.cli("v1.0.0", body, body, self.owner_allowed())
        self.assertEqual(code, 1, text)
        self.assertIn("unreadable", text)

    def test_bad_tag_names_refused(self):
        ref, tag_doc = STATE["docs"]["v1.0.0"]
        for name in ("main", "v1.0", "v1.0.0-rc1", "../v1.0.0", ""):
            code, text = self.cli(name, ref, tag_doc, self.owner_allowed())
            self.assertEqual(code, 1, text)
            self.assertIn("tag name must be", text)

    def test_missing_input_files_refused(self):
        ref, tag_doc = STATE["docs"]["v1.0.0"]
        for args in ((None, tag_doc, self.owner_allowed()), (ref, None, self.owner_allowed()), (ref, tag_doc, None)):
            code, text = self.cli("v1.0.0", *args)
            self.assertEqual(code, 1, text)
            self.assertIn("could not be read", text)

    def test_certificate_authority_entry_refused(self):
        ca = entry(STATE["keys"]["owner"][1], options='cert-authority,namespaces="git"')
        self.check("v1.0.0", ca, False, "certificate authority")

    def test_entry_without_namespace_restriction_refused(self):
        self.check("v1.0.0", entry(STATE["keys"]["owner"][1], options=""), False, "lacks the option")
        self.check("v1.0.0", entry(STATE["keys"]["owner"][1], options='namespaces="file"'), False, "lacks the option")

    def test_private_key_material_refused(self):
        private_text = Path(STATE["keys"]["owner"][0]).read_text()
        self.check("v1.0.0", self.owner_allowed() + "# " + private_text.replace("\n", " # "), False, "private key material")

    def test_other_key_types_refused(self):
        private, fields = keygen(STATE["root"], "rsa", "rsa")
        rsa = entry("%s %s" % (fields[0], fields[1]))
        self.check("v1.0.0", rsa, False, "key type")


class CheckedInFile(unittest.TestCase):
    def test_holds_exactly_the_owner_key(self):
        text = ALLOWED_FILE.read_text(encoding="utf-8")
        spec = importlib.util.spec_from_file_location("vts", str(HERE / "verify-tag-signer.py"))
        loaded = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(loaded)
        entries = loaded.parse_allowed_signers(text)
        self.assertEqual(len(entries), 1)
        with tempfile.TemporaryDirectory() as workdir:
            pub = Path(workdir, "key.pub")
            pub.write_text("%s %s\n" % (entries[0][1], entries[0][2]))
            listing = run(["ssh-keygen", "-lf", str(pub)]).stdout.decode()
        self.assertIn(EXPECTED_FINGERPRINT, listing)


def pattern_regex(pattern):
    body = pattern.strip("/")
    anchored = pattern.startswith("/")
    if body.startswith("**/"):
        body, anchored = body[3:], False
    return re.compile("^" + ("" if anchored else "(?:.*/)?") + re.escape(body).replace(r"\*", "[^/]*") + "(?:/.*)?$")


def owner_of(path, lines):
    """Who owns a path under a code owners file: the last matching line wins.
    Handles the pattern shapes this repo uses: anchored, directory and ** forms."""
    owners = None
    for raw in lines:
        line = raw.split("#", 1)[0].split()
        if len(line) >= 2 and pattern_regex(line[0]).match(path):
            owners = line[1:]
    return owners


class CodeOwners(unittest.TestCase):
    def test_only_the_owner_team_owns_the_trust_files(self):
        lines = Path(CODEOWNERS).read_text(encoding="utf-8").splitlines()
        for path in (".github/allowed_signers",
                     ".github/actions/verify-tag-signer/verify-tag-signer.py",
                     ".github/actions/verify-tag-signer/action.yml",
                     ".github/CODEOWNERS"):
            self.assertEqual(owner_of(path, lines), [OWNER_TEAM], path)

    def test_the_matcher_does_not_own_everything(self):
        lines = Path(CODEOWNERS).read_text(encoding="utf-8").splitlines()
        self.assertIsNone(owner_of("README.md", lines))


@unittest.skipIf(IN_SABOTAGE, "a sabotaged copy is being tested; it does not test itself")
class Sabotage(unittest.TestCase):
    """Break one control in a copy; the guarding test must fail there and pass
    on the unmodified copy."""

    SCRIPT_CASES = [
        ("other key accepted", "            if code == 0:", "            if True:", "Controls.test_other_key_refused"),
        ("unsigned tag accepted", "    if not isinstance(signature, str) or not signature.strip() or not isinstance(payload, str) or not payload:",
         "    if False:", "Controls.test_unsigned_annotated_tag_refused"),
        ("lightweight tag accepted", '    if ref_object.get("type") != "tag":', "    if False:",
         "Controls.test_lightweight_tag_refused"),
        ("empty file allowed", "    if not entries:", "    if False:",
         "Controls.test_empty_allowed_signers_refuses_everything"),
        ("namespace not required", '        if "namespaces=\\"git\\"" not in options:', "        if False:",
         "Controls.test_entry_without_namespace_restriction_refused"),
        ("certificate authority allowed", '== "cert-authority"', '== "never-matches"',
         "Controls.test_certificate_authority_entry_refused"),
        ("tag name not checked", '    if "tag " + tag not in headers:', "    if False:",
         "Controls.test_valid_signed_object_under_another_tag_name_refused"),
        ("commit not checked", '    if "object " + commit_sha not in headers or "type commit" not in headers:',
         "    if False:", "Controls.test_signed_text_for_another_commit_refused"),
        ("private material allowed", '    if "PRIVATE KEY" in text:', "    if False:",
         "Controls.test_private_key_material_refused"),
        ("signature format not checked", "    if not (stripped.startswith(SIG_BEGIN) and stripped.endswith(SIG_END)):",
         "    if False:", "Controls.test_gpg_signature_refused"),
        ("key type not checked", "        if key_type not in KEY_TYPES:", "        if False:",
         "Controls.test_other_key_types_refused"),
    ]

    def run_named(self, test, env):
        done = subprocess.run(
            [sys.executable, "-m", "unittest", "test_verify_tag_signer." + test], cwd=str(HERE),
            env=dict(os.environ, **env), capture_output=True, check=False)
        return done.returncode

    def sabotage_script(self, label, old, new, test):
        original = Path(HERE, "verify-tag-signer.py").read_text(encoding="utf-8")
        self.assertEqual(original.count(old), 1, "the line to break must exist once: %s" % label)
        with tempfile.TemporaryDirectory() as workdir:
            good, bad = Path(workdir, "good.py"), Path(workdir, "bad.py")
            good.write_text(original, encoding="utf-8")
            bad.write_text(original.replace(old, new), encoding="utf-8")
            self.assertEqual(self.run_named(test, {"VERIFY_TAG_SIGNER_SCRIPT": str(good)}), 0, "green on the original: " + label)
            self.assertNotEqual(self.run_named(test, {"VERIFY_TAG_SIGNER_SCRIPT": str(bad)}), 0, "red when broken: " + label)

    def test_every_script_control_is_guarded(self):
        for label, old, new, test in self.SCRIPT_CASES:
            with self.subTest(label):
                self.sabotage_script(label, old, new, test)

    def test_code_owners_coverage_is_guarded(self):
        original = Path(HERE.parent.parent, "CODEOWNERS").read_text(encoding="utf-8").splitlines()
        no_github = [line for line in original if not re.match(r"\s*(/|\*\*/)\.github/", line)]
        other_team = [line.replace(OWNER_TEAM, "@branchLeft/someone-else") if re.match(r"\s*/\.github/", line) else line
                      for line in original]
        for label, lines in (("unmodified", original), ("github lines removed", no_github), ("another team", other_team)):
            with tempfile.TemporaryDirectory() as workdir:
                path = Path(workdir, "CODEOWNERS")
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                code = self.run_named("CodeOwners", {"VERIFY_TAG_SIGNER_CODEOWNERS": str(path)})
            if label == "unmodified":
                self.assertEqual(code, 0, label)
            else:
                self.assertNotEqual(code, 0, label)


if __name__ == "__main__":
    unittest.main()
