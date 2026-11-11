"""Tests for verify-package-attestations.py, offline, against a stub gh and a stub curl.

The stubs are in fixtures/. The stub gh refuses what the real gh refused in a read-only
run against a live attestation (fixtures/real-gh-verify-output.json is that
attestation's result, without its bundle). A second mode, STUB_ENFORCE=0, is a gh that
accepts anything: the script must refuse a wrong tarball even then, because it reads the
certificate fields again itself.

The Sabotage class at the end breaks each control in a copy of this directory, requires the
named tests to go red there, and requires the unmodified copy to be green.
"""

from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
from typing import ClassVar
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
SCRIPT = HERE / "verify-package-attestations.py"
INSIDE_SABOTAGE = "VERIFY_PACKAGE_ATTESTATIONS_SABOTAGE"
STUB = "stub-not-a-credential"
EMPTY = ""
COMMIT = "7432906a8dd245b766e81dc5b157d444ed68dcd2"

spec = importlib.util.spec_from_file_location("verify_package_attestations", SCRIPT)
assert spec is not None and spec.loader is not None
M = importlib.util.module_from_spec(spec)
sys.modules["verify_package_attestations"] = M
spec.loader.exec_module(M)



def load_stub(file_name: str):  # type: ignore[no-untyped-def]
    stub_spec = importlib.util.spec_from_file_location(file_name.replace("-", "_").removesuffix(".py"), FIXTURES / file_name)
    assert stub_spec is not None and stub_spec.loader is not None
    module = importlib.util.module_from_spec(stub_spec)
    stub_spec.loader.exec_module(module)
    return module


STUB_GH = load_stub("stub-gh.py")
STUB_CURL = load_stub("stub-curl.py")

RENDER = "@branchleft/ghost-platform-render-core"
HOST = "@branchleft/hetzner-host"
COMPONENTS = "@branchleft/components"
TABLE = (
    M.Identity(
        package=RENDER,
        repo="branchLeft/ghost-platform",
        workflow="publish-render-core.yml",
        ref_regex=r"refs/tags/render-core-v\d+\.\d+\.\d+",
        ref_template="refs/tags/render-core-v{version}",
        first_attested="1.0.0",
        grandfathered=("0.9.0", "0.8.0"),
    ),
    M.Identity(
        package=HOST,
        repo="branchLeft/shared-infra",
        workflow="publish-hetzner-host.yml",
        ref_regex=r"refs/tags/hetzner-host-v\d+\.\d+\.\d+",
        ref_template="refs/tags/hetzner-host-v{version}",
    ),
    M.Identity(
        package=COMPONENTS,
        repo="branchLeft/components",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template=None,
        first_attested="2.0.0",
    ),
)
GOOD_REF = "refs/tags/render-core-v1.0.0"
GOOD_REPO = "branchLeft/ghost-platform"
GOOD_WORKFLOW = "publish-render-core.yml"


def pinned_before_attestation() -> list[tuple[str, str]]:
    """Every package@version the repositories' lockfiles pinned when the list was derived."""
    rows = (FIXTURES / "pinned-before-attestation.tsv").read_text(encoding="utf-8").splitlines()
    return [(row.split("\t")[0], row.split("\t")[1]) for row in rows if row]


def make_tgz(path: pathlib.Path, name: str, version: str, claimed: tuple[str, str] | None = None) -> None:
    """A deterministic package tarball. ``claimed`` is what its package.json says."""
    shown = claimed or (name, version)
    manifest = json.dumps({"name": shown[0], "version": shown[1]}).encode()
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(manifest)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(manifest))
        body = f"{name}@{version}".encode()
        info = tarfile.TarInfo("package/index.js")
        info.size = len(body)
        info.mtime = 0
        archive.addfile(info, io.BytesIO(body))
    path.write_bytes(gzip.compress(raw.getvalue(), mtime=0))


def integrity_of(path: pathlib.Path) -> str:
    return "sha512-" + base64.b64encode(hashlib.sha512(path.read_bytes()).digest()).decode()


class World:
    """A temporary workspace with the stubs first on PATH."""

    def __init__(self, tmp: pathlib.Path) -> None:
        self.tmp = tmp
        self.repo = tmp / "repo"
        self.bin = tmp / "bin"
        self.attest = tmp / "attestations"
        self.tarballs = tmp / "tarballs"
        self.log = tmp / "calls.log"
        for directory in (self.repo, self.bin, self.attest, self.tarballs):
            directory.mkdir()
        self.log.write_text("", encoding="utf-8")
        for stub, name in (("stub-gh.py", "gh"), ("stub-curl.py", "curl")):
            target = self.bin / name
            shutil.copy(FIXTURES / stub, target)
            target.chmod(target.stat().st_mode | stat.S_IXUSR)
        self.extra: dict[str, str] = {}

    def environment(self, **extra: str) -> dict[str, str]:
        return {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "GH_TOKEN": STUB,
            "STUB_ATTEST_DIR": str(self.attest),
            "STUB_TARBALLS": str(self.tarballs),
            "STUB_LOG": str(self.log),
            **self.extra,
            **extra,
        }

    def calls(self) -> list[str]:
        return self.log.read_text(encoding="utf-8").splitlines()

    def package(self, name: str, version: str, claimed: tuple[str, str] | None = None) -> tuple[pathlib.Path, str]:
        """Make the tarball the registry would serve; return it and its sha256."""
        path = self.tarballs / f"{name.split('/')[1]}-{version}.tgz"
        make_tgz(path, name, version, claimed)
        return path, hashlib.sha256(path.read_bytes()).hexdigest()

    def attestation(
        self,
        sha256: str,
        *,
        repo: str = GOOD_REPO,
        workflow: str = GOOD_WORKFLOW,
        ref: str = GOOD_REF,
        commit: str = COMMIT,
        **changes: object,
    ) -> pathlib.Path:
        """The attestation API's answer for one digest, built on the real result's shape."""
        results = json.loads((FIXTURES / "real-gh-verify-output.json").read_text(encoding="utf-8"))
        verification = results[0]["verificationResult"]
        certificate = verification["signature"]["certificate"]
        identity = f"https://github.com/{repo}/.github/workflows/{workflow}@{ref}"
        certificate.update(
            githubWorkflowRepository=repo,
            sourceRepositoryURI=f"https://github.com/{repo}",
            subjectAlternativeName=identity,
            buildSignerURI=identity,
            buildConfigURI=identity,
            githubWorkflowRef=ref,
            sourceRepositoryRef=ref,
            sourceRepositoryDigest=commit,
            buildSignerDigest=commit,
            githubWorkflowSHA=commit,
            buildConfigDigest=commit,
        )
        verification["statement"]["subject"][0]["digest"]["sha256"] = sha256
        for key, value in changes.items():
            if key in certificate:
                certificate[key] = value
            elif key == "subject":
                verification["statement"]["subject"][0]["digest"]["sha256"] = value
            elif key == "predicateType":
                verification["statement"]["predicateType"] = value
            else:
                raise KeyError(key)
        path = self.attest / f"{sha256}.json"
        path.write_text(json.dumps(results), encoding="utf-8")
        return path

    def lock(self, entries: list[tuple[str, str, str | None, str | None]], name: str = "pnpm-lock.yaml") -> pathlib.Path:
        """A pnpm lockfile in the shape pnpm writes; each entry is name, version, integrity, tarball."""
        lines = ["lockfileVersion: '9.0'", "", "importers:", "", "  .: {}", "", "packages:", ""]
        for package, version, integrity, tarball in entries:
            parts = []
            if integrity:
                parts.append(f"integrity: {integrity}")
            if tarball:
                parts.append(f"tarball: {tarball}")
            lines += [f"  '{package}@{version}':", "    resolution: {" + ", ".join(parts) + "}", ""]
        lines += ["  '@jridgewell/sourcemap-codec@1.6.0':", "    resolution: {integrity: sha512-AAAA}", "", "snapshots:", ""]
        for package, version, _, _ in entries:
            lines += [f"  '{package}@{version}': {{}}", ""]
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def entry(self, package: str, version: str, **changes: object) -> tuple[str, str, str | None, str | None]:
        """A lockfile entry for a package tarball that exists in this world."""
        path = self.tarballs / f"{package.split('/')[1]}-{version}.tgz"
        url = f"https://npm.pkg.github.com/download/{package}/{version}/{'ab' * 20}"
        integrity = integrity_of(path) if path.exists() else "sha512-AAAA"
        values = {"integrity": integrity, "tarball": url, **changes}
        return package, version, values["integrity"], values["tarball"]  # type: ignore[return-value]

    def fake_run(self, environ: dict[str, str]):  # type: ignore[no-untyped-def]
        """The verifier's tool runner, answered in-process by the stubs: same code, no process start."""

        def fake(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
            if argv[0] == "gh":
                code, out, err = STUB_GH.run(argv[1:], environ)
            elif argv[0] == "curl":
                code, out, err = STUB_CURL.run(argv[1:], environ, stdin)
            else:
                raise AssertionError(f"the verifier ran {argv[0]}")
            return subprocess.CompletedProcess(argv, code, out, err)

        return fake

    def scan(self, table: object = TABLE, token: str = STUB, **extra: str) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(M, "run", self.fake_run(self.environment(**extra))), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = M.scan(self.repo, table, token)
        return code, out.getvalue(), err.getvalue()

    def cli(self, *args: str, real: bool = False, **extra: str) -> subprocess.CompletedProcess[str]:
        """The command line. In-process by default; ``real`` starts the program and the stubs as processes."""
        if real:
            return subprocess.run(
                [sys.executable, "-I", str(SCRIPT), *args],
                capture_output=True,
                text=True,
                check=False,
                env={**self.environment(**extra), "HOME": str(self.tmp)},
            )
        environ = self.environment(**extra)
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.dict(os.environ, environ),
            mock.patch.object(M, "run", self.fake_run(environ)),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = M.main(list(args))
            except SystemExit as stop:
                code = int(stop.code or 0)
        return subprocess.CompletedProcess(list(args), code, out.getvalue(), err.getvalue())


class WorldCase(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory(prefix="verify-package-attestations-test-")
        self.addCleanup(holder.cleanup)
        self.world = World(pathlib.Path(holder.name))

    def good(self, package: str = RENDER, version: str = "1.0.0", **attest: object) -> tuple[str, str, str | None, str | None]:
        """A package that is published, attested by the right identity, and locked."""
        _, sha256 = self.world.package(package, version)
        self.world.attestation(sha256, **attest)  # type: ignore[arg-type]
        return self.world.entry(package, version)


class ScanPasses(WorldCase):
    def test_the_right_tarball_with_the_right_identity_passes(self) -> None:
        self.world.lock([self.good()])
        code, out, err = self.world.scan()
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("1 attested, 0 grandfathered, 0 refused", out)

    def test_every_identity_flag_is_sent_to_gh(self) -> None:
        self.world.lock([self.good()])
        self.world.scan()
        verify = next(c for c in self.world.calls() if c.startswith("attestation verify"))
        for flag in (
            f"--repo {GOOD_REPO}",
            f"--signer-workflow {GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}",
            f"--cert-oidc-issuer {M.ISSUER}",
            f"--predicate-type {M.PREDICATE}",
            "--deny-self-hosted-runners",
            "--format json",
            f"--source-ref {GOOD_REF}",
        ):
            self.assertIn(flag, verify)

    def test_a_row_with_no_ref_template_checks_the_tag_pattern_only(self) -> None:
        self.world.lock([self.good(COMPONENTS, "2.0.0", ref="refs/tags/v7.7.7", workflow="publish.yml", repo="branchLeft/components")])
        code, out, err = self.world.scan()
        self.assertEqual((code, err), (0, ""), out)
        verify = next(c for c in self.world.calls() if c.startswith("attestation verify"))
        self.assertNotIn("--source-ref", verify)

    def test_a_grandfathered_version_passes_and_nothing_is_fetched_or_asked(self) -> None:
        self.world.lock([self.world.entry(RENDER, "0.9.0")])
        code, out, err = self.world.scan()
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("0 attested, 1 grandfathered, 0 refused", out)
        self.assertEqual(self.world.calls(), [])

    def test_a_version_below_the_floor_that_is_attested_also_passes(self) -> None:
        self.world.lock([self.good(RENDER, "1.0.0")])
        self.assertEqual(self.world.scan()[0], 0)

    def test_an_unset_row_passes_a_version_that_is_attested(self) -> None:
        self.world.lock([self.good(HOST, "3.1.0", repo="branchLeft/shared-infra", workflow="publish-hetzner-host.yml", ref="refs/tags/hetzner-host-v3.1.0")])
        code, out, err = self.world.scan()
        self.assertEqual((code, err), (0, ""), out)

    def test_a_lockfile_with_no_branchleft_package_passes(self) -> None:
        self.world.lock([])
        code, out, _ = self.world.scan()
        self.assertEqual(code, 0)
        self.assertIn("0 @branchleft package(s)", out)

    def test_the_same_tarball_in_two_lockfiles_is_fetched_once(self) -> None:
        entry = self.good()
        self.world.lock([entry])
        self.world.lock([entry], name="services/a/pnpm-lock.yaml")
        code, out, err = self.world.scan()
        self.assertEqual((code, err), (0, ""), out)
        self.assertEqual(len([c for c in self.world.calls() if c.startswith("curl")]), 1)
        self.assertIn("2 lockfile(s), 2 @branchleft package(s)", out)

    def test_a_package_lock_json_is_read_too(self) -> None:
        entry = self.good()
        lock = {
            "lockfileVersion": 3,
            "packages": {
                "": {"name": "x"},
                f"node_modules/{RENDER}": {"version": "1.0.0", "resolved": entry[3], "integrity": entry[2]},
                "node_modules/@branchleft/linked": {"resolved": "../linked", "link": True},
                "node_modules/left-pad": {"version": "1.0.0"},
            },
        }
        (self.world.repo / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")
        code, out, err = self.world.scan()
        self.assertEqual((code, err), (0, ""), out)
        self.assertIn("1 attested", out)

    def test_node_modules_and_git_are_not_scanned(self) -> None:
        self.world.lock([self.good()])
        for hidden in ("node_modules/x", ".git/y"):
            (self.world.repo / hidden).mkdir(parents=True)
            (self.world.repo / hidden / "pnpm-lock.yaml").write_text("packages:\n  '@branchleft/unknown@1.0.0':\n    resolution: {}\n", encoding="utf-8")
        self.assertEqual(self.world.scan()[0], 0)

    def test_a_scan_runs_the_stubs_as_processes_and_the_real_table_accepts_an_attested_package(self) -> None:
        self.world.lock([self.good()])
        done = self.world.cli("scan", str(self.world.repo), real=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertEqual([c.split()[0] for c in self.world.calls()].count("curl"), 1)

    def test_the_token_goes_to_curl_on_standard_input_only(self) -> None:
        self.world.lock([self.good()])
        self.assertEqual(self.world.scan()[0], 0)
        self.assertFalse([c for c in self.world.calls() if STUB in c])


class ScanRefuses(WorldCase):
    def refused(self, *needles: str) -> str:
        code, out, err = self.world.scan()
        self.assertEqual(code, 1, out + err)
        for needle in needles:
            self.assertIn(needle, err)
        self.assertNotIn("attested, 0 grandfathered, 0 refused", out)
        return err

    def test_no_attestation_for_the_digest_is_refused(self) -> None:
        self.world.package(RENDER, "1.0.0")
        self.world.lock([self.world.entry(RENDER, "1.0.0")])
        self.refused("no attestation for sha256", "at or above the floor 1.0.0")

    def test_a_version_above_the_floor_without_attestation_fails(self) -> None:
        self.world.package(RENDER, "1.4.2")
        self.world.lock([self.world.entry(RENDER, "1.4.2")])
        self.refused("at or above the floor 1.0.0")

    def test_a_version_below_the_floor_that_is_not_listed_fails(self) -> None:
        self.world.package(RENDER, "0.7.0")
        self.world.lock([self.world.entry(RENDER, "0.7.0")])
        self.refused("below the floor 1.0.0", "not in the grandfathered list")

    def test_an_unset_row_without_attestation_fails_closed_and_names_the_row(self) -> None:
        self.world.package(HOST, "3.1.0")
        self.world.lock([self.world.entry(HOST, "3.1.0")])
        self.refused(f"row {HOST} has first_attested UNSET", "none passes without a verified attestation")

    def test_a_wrong_repo_is_refused_whether_or_not_gh_enforces_it(self) -> None:
        for enforce in ("1", "0"):
            with self.subTest(gh_enforces=enforce):
                self.setUp()
                self.world.extra["STUB_ENFORCE"] = enforce
                self.world.lock([self.good(repo="branchLeft/shared-infra")])
                self.refused()

    def test_a_wrong_workflow_is_refused_whether_or_not_gh_enforces_it(self) -> None:
        for workflow in ("publish-tenant-package.yml", "publish-render-core.yml.evil", "other.yml"):
            for enforce in ("1", "0"):
                with self.subTest(workflow=workflow, gh_enforces=enforce):
                    self.setUp()
                    self.world.extra["STUB_ENFORCE"] = enforce
                    self.world.lock([self.good(workflow=workflow)])
                    self.refused()

    def test_a_wrong_ref_is_refused_whether_or_not_gh_enforces_it(self) -> None:
        # The first is a tag of the right shape for another version; the rest are no tag of the shape.
        for ref in ("refs/tags/render-core-v9.9.9", "refs/heads/main", "refs/tags/v1.0.0", "refs/tags/render-core-v1.0.0-evil", "refs/heads/render-core-v1.0.0"):
            for enforce in ("1", "0"):
                with self.subTest(ref=ref, gh_enforces=enforce):
                    self.setUp()
                    self.world.extra["STUB_ENFORCE"] = enforce
                    self.world.lock([self.good(ref=ref)])
                    self.refused()

    def test_a_ref_that_matches_the_pattern_but_not_the_version_is_refused_by_the_script_alone(self) -> None:
        self.world.extra["STUB_ENFORCE"] = "0"
        self.world.lock([self.good(ref="refs/tags/render-core-v9.9.9")])
        self.refused("not refs/tags/render-core-v1.0.0")

    def test_an_attestation_of_other_bytes_is_refused_by_the_script_alone(self) -> None:
        self.world.extra["STUB_ENFORCE"] = "0"
        _, sha256 = self.world.package(RENDER, "1.0.0")
        self.world.attestation(sha256, subject="f" * 64)
        self.world.lock([self.world.entry(RENDER, "1.0.0")])
        self.refused("do not include this tarball's sha256")

    def test_a_self_hosted_runner_is_refused_whether_or_not_gh_enforces_it(self) -> None:
        for enforce in ("1", "0"):
            with self.subTest(gh_enforces=enforce):
                self.setUp()
                self.world.extra["STUB_ENFORCE"] = enforce
                self.world.lock([self.good(runnerEnvironment="self-hosted")])
                self.refused()

    def test_another_issuer_a_wrong_predicate_and_a_split_signer_commit_are_refused_by_the_script_alone(self) -> None:
        self.world.extra["STUB_ENFORCE"] = "0"
        for change, message in (
            ({"issuer": "https://accounts.example.com"}, "issuer is"),
            ({"predicateType": "https://example.com/other"}, "predicate type is"),
            ({"buildSignerDigest": "1" * 40}, "signer commit differs"),
            ({"sourceRepositoryDigest": "not-a-sha"}, "not a 40-hex sha"),
            ({"githubWorkflowRef": "refs/tags/render-core-v1.0.1"}, "workflow ref is"),
            ({"sourceRepositoryURI": "https://github.com/branchLeft/other"}, "source repository is"),
            ({"subjectAlternativeName": "https://github.com/branchLeft/ghost-platform/.github/workflows/publish-render-core.yml@refs/tags/render-core-v0.0.1"}, "signer identity is"),
        ):
            with self.subTest(change=change):
                self.setUp()
                self.world.lock([self.good(**change)])
                self.refused(message)

    def test_a_wrong_sha512_is_refused_before_gh_is_asked(self) -> None:
        self.good()
        wrong = "sha512-" + base64.b64encode(hashlib.sha512(b"other bytes").digest()).decode()
        self.world.lock([self.world.entry(RENDER, "1.0.0", integrity=wrong)])
        self.refused("do not match the lockfile integrity")
        self.assertEqual([c for c in self.world.calls() if c.startswith("attestation")], [])

    def test_an_integrity_that_is_not_sha512_or_not_base64_is_refused(self) -> None:
        self.good()
        for integrity in ("sha1-" + "A" * 27 + "=", "sha256-" + "A" * 43 + "=", "sha512-!!!not base64!!!", None):
            with self.subTest(integrity=integrity):
                self.world.lock([self.world.entry(RENDER, "1.0.0", integrity=integrity)])
                self.refused()

    def test_a_tarball_that_is_not_the_package_the_lockfile_names_is_refused(self) -> None:
        _, sha256 = self.world.package(RENDER, "1.0.0", claimed=(HOST, "1.0.0"))
        self.world.attestation(sha256)
        self.world.lock([self.world.entry(RENDER, "1.0.0")])
        self.refused(f"the tarball is {HOST}@1.0.0")
        self.assertEqual([c for c in self.world.calls() if c.startswith("attestation")], [])

    def test_a_tarball_with_no_readable_manifest_is_refused(self) -> None:
        path, _ = self.world.package(RENDER, "1.0.0")
        path.write_bytes(b"not a gzip tarball")
        self.world.lock([self.world.entry(RENDER, "1.0.0")])
        self.refused("cannot read package/package.json")

    def test_a_tarball_url_that_is_not_the_packages_download_url_is_refused_before_any_call(self) -> None:
        self.good()
        urls = (
            "https://registry.npmjs.org/@branchleft/ghost-platform-render-core/-/x-1.0.0.tgz",
            "https://npm.pkg.github.com.evil.example/download/@branchleft/ghost-platform-render-core/1.0.0/" + "ab" * 20,
            "https://npm.pkg.github.com/download/@branchleft/ghost-platform-render-core/1.0.1/" + "ab" * 20,
            "https://npm.pkg.github.com/download/@branchleft/hetzner-host/1.0.0/" + "ab" * 20,
            "http://npm.pkg.github.com/download/@branchleft/ghost-platform-render-core/1.0.0/" + "ab" * 20,
            None,
        )
        for url in urls:
            with self.subTest(url=url):
                self.world.lock([self.world.entry(RENDER, "1.0.0", tarball=url)])
                self.refused()
                self.assertEqual(self.world.calls(), [])

    def test_a_package_that_is_not_in_the_table_is_refused(self) -> None:
        self.world.package("@branchleft/new-thing", "1.0.0")
        self.world.lock([self.world.entry("@branchleft/new-thing", "1.0.0")])
        self.refused("is not in the identity table")
        self.assertEqual(self.world.calls(), [])

    def test_a_download_that_fails_is_refused(self) -> None:
        self.world.lock([self.world.entry(RENDER, "1.0.0")])
        self.refused("could not download")

    def test_a_gh_fault_is_never_a_pass(self) -> None:
        self.world.lock([self.good()])
        for fault in ("network", "tuf"):
            with self.subTest(fault=fault):
                code, out, err = self.world.scan(STUB_FAULT=fault)
                self.assertEqual(code, 1, out)
                self.assertIn("gh attestation verify refused", err)

    def test_gh_exiting_zero_with_no_usable_answer_is_refused(self) -> None:
        for body in ("", "not json", "[]", "{}", "[{}]", '[{"verificationResult": 5}]'):
            with self.subTest(body=body):
                self.setUp()
                self.world.extra["STUB_ENFORCE"] = "0"
                entry = self.good()
                sha256 = next(self.world.attest.glob("*.json"))
                sha256.write_text(body, encoding="utf-8")
                self.world.lock([entry])
                code, out, err = self.world.scan()
                self.assertEqual(code, 1, out + err)

    def test_a_commit_that_is_not_on_main_is_refused(self) -> None:
        for status in ("ahead", "diverged", "", "behind-ish"):
            with self.subTest(status=status):
                self.setUp()
                self.world.lock([self.good()])
                code, out, err = self.world.scan(STUB_COMPARE_STATUS=status)
                self.assertEqual(code, 1, out)
                self.assertIn("is not on main", err)

    def test_a_compare_call_that_fails_is_refused(self) -> None:
        self.world.lock([self.good()])
        code, _, err = self.world.scan(STUB_COMPARE_FAIL="1")
        self.assertEqual(code, 1)
        self.assertIn("could not ask whether", err)

    def test_a_commit_on_main_may_be_identical_or_behind(self) -> None:
        for status in ("identical", "behind"):
            with self.subTest(status=status):
                self.setUp()
                self.world.lock([self.good()])
                self.assertEqual(self.world.scan(STUB_COMPARE_STATUS=status)[0], 0)

    def test_one_bad_package_fails_the_run_though_the_others_pass(self) -> None:
        good = self.good()
        self.world.package(HOST, "3.1.0")
        self.world.lock([good, self.world.entry(HOST, "3.1.0")])
        code, out, err = self.world.scan()
        self.assertEqual(code, 1)
        self.assertIn("1 attested, 0 grandfathered, 1 refused", out)
        self.assertIn(HOST, err)

    def test_no_lockfile_is_refused_not_passed(self) -> None:
        code, _, err = self.world.scan()
        self.assertEqual(code, 1)
        self.assertIn("no pnpm-lock.yaml or package-lock.json", err)

    def test_an_empty_token_is_refused_before_any_call(self) -> None:
        self.world.lock([self.good()])
        code, _, err = self.world.scan(token=EMPTY)
        self.assertEqual(code, 1)
        self.assertIn("GH_TOKEN is empty", err)
        self.assertEqual(self.world.calls(), [])

    def test_a_branchleft_entry_the_parser_cannot_read_is_refused(self) -> None:
        (self.world.repo / "pnpm-lock.yaml").write_text("packages:\n  '@branchleft/odd name@1.0.0':\n    resolution: {}\n", encoding="utf-8")
        code, _, err = self.world.scan()
        self.assertEqual(code, 1)
        self.assertIn("cannot read the @branchleft entry", err)

    def test_a_tarball_url_outside_the_section_read_is_refused(self) -> None:
        entry = self.good()
        self.world.lock([entry])
        with (self.world.repo / "pnpm-lock.yaml").open("a", encoding="utf-8") as handle:
            handle.write(f"\nextra:\n  resolution: {{tarball: {entry[3]}}}\n")
        code, _, err = self.world.scan()
        self.assertEqual(code, 1)
        self.assertIn("tarball URL(s) but", err)

    def test_a_package_lock_without_a_packages_map_is_refused(self) -> None:
        (self.world.repo / "package-lock.json").write_text('{"lockfileVersion": 1, "dependencies": {"@branchleft/x": {}}}', encoding="utf-8")
        code, _, err = self.world.scan()
        self.assertEqual(code, 1)
        self.assertIn("lockfileVersion 1", err)

    def test_a_table_that_is_not_usable_refuses_everything(self) -> None:
        broken = (M.Identity(RENDER, "branchLeft/ghost-platform", "publish-render-core.yml", r"refs/tags/v\d+", None, "1.0.0", ("1.0.0",)),)
        self.world.lock([self.good()])
        code, _, err = self.world.scan(table=broken)
        self.assertEqual(code, 1)
        self.assertIn("is not below first_attested", err)


class TheTable(unittest.TestCase):
    def test_the_real_table_is_usable(self) -> None:
        self.assertEqual(M.validate_table(M.IDENTITIES), [])

    def test_the_real_table_covers_every_published_package_once(self) -> None:
        names = sorted(row.package for row in M.IDENTITIES)
        self.assertEqual(names, sorted(set(names)))
        self.assertEqual(
            names,
            sorted(
                [
                    "@branchleft/brand-branchleft",
                    "@branchleft/brand-publicpress",
                    "@branchleft/components",
                    "@branchleft/eslint-config",
                    "@branchleft/ghost-platform-broker-api",
                    "@branchleft/ghost-platform-render-core",
                    "@branchleft/ghost-platform-tenant",
                    "@branchleft/hetzner-host",
                    "@branchleft/prettier-config",
                    "@branchleft/tsconfig",
                    "@branchleft/vitest-config",
                ]
            ),
        )

    def test_the_real_table_lists_exactly_the_versions_pinned_before_attestation(self) -> None:
        listed = sorted((row.package, version) for row in M.IDENTITIES for version in row.grandfathered)
        self.assertEqual(listed, sorted(pinned_before_attestation()))

    def test_every_real_grandfathered_list_is_in_ascending_order_and_every_row_is_unset(self) -> None:
        for row in M.IDENTITIES:
            keys = [M.version_key(v) for v in row.grandfathered]
            self.assertEqual(keys, sorted(keys), row.package)  # type: ignore[type-var]
            self.assertEqual(row.first_attested, M.UNSET, row.package)

    def test_every_way_the_table_can_be_wrong_is_found(self) -> None:
        good = TABLE[0]

        def one(**changes: object) -> list[str]:
            import dataclasses

            return M.validate_table([dataclasses.replace(good, **changes)])  # type: ignore[arg-type]

        self.assertTrue(one(package="@other/x"))
        self.assertTrue(one(repo="no-slash"))
        self.assertTrue(one(workflow="../x.yml"))
        self.assertTrue(one(ref_regex="(unclosed"))
        self.assertTrue(one(ref_regex="tags/v1"))
        self.assertTrue(one(ref_template="refs/tags/other-v{version}"))
        self.assertTrue(one(first_attested="soon"))
        self.assertEqual(one(first_attested="UNSET", grandfathered=("0.1.0",)), [])
        self.assertTrue(one(first_attested="UNSET", grandfathered=("0.1.0", "0.1.0")))
        self.assertTrue(one(first_attested="UNSET", grandfathered=("soon",)))
        self.assertTrue(one(grandfathered=("1.0.0",)))
        self.assertTrue(one(grandfathered=("2.0.0",)))
        self.assertTrue(one(grandfathered=("old",)))
        self.assertTrue(M.validate_table([good, good]))
        self.assertEqual(one(), [])

    def test_versions_order_as_semver(self) -> None:
        order = ["0.9.9", "1.0.0-alpha", "1.0.0-alpha.1", "1.0.0-beta", "1.0.0-beta.2", "1.0.0-beta.11", "1.0.0-rc.1", "1.0.0", "1.0.1", "1.1.0", "2.0.0"]
        keys = [M.version_key(v) for v in order]
        self.assertEqual(keys, sorted(keys))  # type: ignore[type-var]
        self.assertIsNone(M.version_key("1.0"))


class TheRealTree(WorldCase):
    """The real table against a lockfile that pins every version the repositories pin today."""

    def lock_pinned(self) -> None:
        self.world.lock([self.world.entry(name, version) for name, version in pinned_before_attestation()])

    def test_every_pinned_version_passes_with_no_download_and_no_lookup(self) -> None:
        self.lock_pinned()
        done = self.world.cli("scan", str(self.world.repo), real=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn(f"0 attested, {len(pinned_before_attestation())} grandfathered, 0 refused", done.stdout)
        self.assertEqual(self.world.calls(), [])

    def test_a_version_that_is_not_listed_fails_closed_on_every_package_with_a_list(self) -> None:
        listed = {name for name, _ in pinned_before_attestation()}
        self.assertTrue(listed)
        for name in sorted(listed):
            for version in ("0.0.1", "0.3.2", "9.9.9"):
                with self.subTest(package=name, version=version):
                    self.setUp()
                    self.world.package(name, version)
                    self.world.lock([self.world.entry(name, version)])
                    done = self.world.cli("scan", str(self.world.repo), real=True)
                    self.assertEqual(done.returncode, 1, done.stdout)
                    self.assertIn(f"row {name} has first_attested UNSET", done.stderr)
                    self.assertIn(f"{version} is not one of them", done.stderr)

    def test_a_listed_version_of_one_package_does_not_pass_for_another(self) -> None:
        self.world.package("@branchleft/eslint-config", "0.8.8")
        self.world.lock([self.world.entry("@branchleft/eslint-config", "0.8.8")])
        done = self.world.cli("scan", str(self.world.repo), real=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("row @branchleft/eslint-config has first_attested UNSET", done.stderr)
        self.assertIn("no version of it is grandfathered", done.stderr)

    def test_one_unlisted_version_among_listed_ones_fails_the_whole_scan(self) -> None:
        self.world.package("@branchleft/tsconfig", "0.9.0")
        self.world.lock(
            [self.world.entry(name, version) for name, version in pinned_before_attestation()]
            + [self.world.entry("@branchleft/tsconfig", "0.9.0")]
        )
        done = self.world.cli("scan", str(self.world.repo), real=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"{len(pinned_before_attestation())} grandfathered, 1 refused", done.stdout)


class TheRealResult(unittest.TestCase):
    """The parser against a result printed by the real gh for a real attestation."""

    def result(self) -> object:
        return json.loads((FIXTURES / "real-gh-verify-output.json").read_text(encoding="utf-8"))[0]

    def judged(self, **changes: str) -> list[str]:
        args = {
            "sha256_hex": "6410ecb830f6dd01ff46a459aa3e352e613b241378a4dc24f17f4bd50c4fc864",
            "repo": "branchLeft/ghost-platform",
            "signer": "branchLeft/ghost-platform/.github/workflows/broker-release.yml",
            "ref_regex": r"refs/tags/broker-v\d+\.\d+\.\d+",
            "ref": "refs/tags/broker-v0.1.0",
            **changes,
        }
        return M.judge(self.result(), **args)

    def test_the_real_result_is_accepted_for_its_own_identity(self) -> None:
        self.assertEqual(self.judged(), [])

    def test_the_real_result_is_refused_for_any_other_identity(self) -> None:
        for change in (
            {"repo": "branchLeft/shared-infra"},
            {"signer": "branchLeft/ghost-platform/.github/workflows/other.yml"},
            {"ref": "refs/tags/broker-v0.1.1"},
            {"ref_regex": r"refs/tags/render-core-v\d+\.\d+\.\d+", "ref": None},
            {"sha256_hex": "0" * 64},
        ):
            with self.subTest(change=change):
                self.assertTrue(self.judged(**change))


class CheckTarballCommand(WorldCase):
    def run_check(self, *extra: str, repo: str = GOOD_REPO, workflow: str = GOOD_WORKFLOW, ref: str = GOOD_REF, **env: str) -> subprocess.CompletedProcess[str]:
        path, _ = self.world.package(RENDER, "1.0.0")
        return self.world.cli(
            "check-tarball", str(path), "--repo", repo, "--signer-workflow", f"{repo}/.github/workflows/{workflow}", "--ref", ref, "--pause", "0", *extra, **env
        )

    def attest(self, **changes: object) -> None:
        _, sha256 = self.world.package(RENDER, "1.0.0")
        self.world.attestation(sha256, **changes)  # type: ignore[arg-type]

    def test_the_right_identity_verifies(self) -> None:
        self.attest()
        done = self.run_check()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("verified:", done.stdout)

    def test_the_right_identity_verifies_through_real_processes(self) -> None:
        self.attest()
        path, _ = self.world.package(RENDER, "1.0.0")
        done = self.world.cli(
            "check-tarball", str(path), "--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}", "--ref", GOOD_REF, real=True
        )
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("verified:", done.stdout)

    def test_no_attestation_fails_after_every_attempt(self) -> None:
        self.world.package(RENDER, "1.0.0")
        done = self.run_check("--attempts", "3")
        self.assertEqual(done.returncode, 1)
        self.assertIn("attempt 3 of 3", done.stdout)
        self.assertEqual(len([c for c in self.world.calls() if c.startswith("attestation verify")]), 3)
        self.assertIn("::error::", done.stdout)

    def test_a_wrong_identity_fails_whether_or_not_gh_enforces_it(self) -> None:
        for enforce in ("1", "0"):
            for changes in ({"repo": "branchLeft/shared-infra"}, {"workflow": "publish-tenant-package.yml"}, {"ref": "refs/tags/render-core-v1.0.1"}):
                with self.subTest(gh_enforces=enforce, changes=changes):
                    self.setUp()
                    self.attest(**changes)
                    done = self.run_check("--attempts", "1", STUB_ENFORCE=enforce)
                    self.assertEqual(done.returncode, 1, done.stdout)

    def test_a_commit_that_is_not_on_main_fails(self) -> None:
        self.attest()
        done = self.run_check("--attempts", "1", STUB_COMPARE_STATUS="diverged")
        self.assertEqual(done.returncode, 1)
        self.assertIn("is not on main", done.stdout)

    def test_a_retry_that_finds_the_attestation_later_passes(self) -> None:
        self.world.package(RENDER, "1.0.0")
        first = self.run_check("--attempts", "1")
        self.assertEqual(first.returncode, 1)
        self.attest()
        self.assertEqual(self.run_check("--attempts", "2").returncode, 0)

    def test_an_identity_that_is_not_literal_is_refused_before_any_call(self) -> None:
        self.attest()
        tarball = str(self.world.tarballs / "ghost-platform-render-core-1.0.0.tgz")
        for args in (
            ["--repo", "no-slash", "--signer-workflow", "no-slash/.github/workflows/x.yml", "--ref", GOOD_REF],
            ["--repo", GOOD_REPO, "--signer-workflow", "other/repo/.github/workflows/x.yml", "--ref", GOOD_REF],
            ["--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/../x.yml", "--ref", GOOD_REF],
            ["--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}", "--ref", "main"],
            ["--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}", "--ref", "refs/tags/a b"],
            ["--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}", "--ref", "refs/tags/../x"],
        ):
            with self.subTest(args=args):
                done = self.world.cli("check-tarball", tarball, *args)
                self.assertEqual(done.returncode, 1, done.stdout)
                self.assertEqual(self.world.calls(), [])

    def test_a_missing_tarball_and_a_missing_token_are_refused(self) -> None:
        done = self.world.cli("check-tarball", str(self.world.tarballs / "none.tgz"), "--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}", "--ref", GOOD_REF)
        self.assertEqual(done.returncode, 1)
        self.attest()
        done = self.run_check(GH_TOKEN=EMPTY)
        self.assertEqual(done.returncode, 1)
        self.assertIn("GH_TOKEN is empty", done.stderr)

    def test_a_consumer_shape_that_no_table_row_claims_is_refused_before_any_call(self) -> None:
        shapes = (
            ("the ref does not match the row's pattern", GOOD_REPO, GOOD_WORKFLOW, "refs/tags/v1.0.0"),
            ("the workflow is not the row's signer", GOOD_REPO, "publish-tenant-package.yml", GOOD_REF),
            ("the repo has no row", "branchLeft/shared-infra", GOOD_WORKFLOW, GOOD_REF),
        )
        for label, repo, workflow, ref in shapes:
            with self.subTest(shape=label):
                self.world.log.write_text("", encoding="utf-8")
                self.attest(repo=repo, workflow=workflow, ref=ref)
                done = self.run_check("--attempts", "1", repo=repo, workflow=workflow, ref=ref)
                self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
                self.assertEqual(self.world.calls(), [])

    def test_check_inputs_refuses_a_ref_that_no_table_row_accepts(self) -> None:
        path, _ = self.world.package(RENDER, "1.0.0")
        done = self.world.cli(
            "check-inputs", str(path), "--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}", "--ref", "refs/tags/v1.0.0"
        )
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn("matches no ref_regex", done.stderr)


class ProveWrongIdentityCommand(WorldCase):
    def prove(self, real: bool = False, **env: str) -> subprocess.CompletedProcess[str]:
        path, _ = self.world.package(RENDER, "1.0.0")
        return self.world.cli(
            "prove-wrong-identity", str(path), "--repo", GOOD_REPO, "--signer-workflow", f"{GOOD_REPO}/.github/workflows/{GOOD_WORKFLOW}", "--ref", GOOD_REF, real=real, **env
        )

    def attest(self) -> None:
        _, sha256 = self.world.package(RENDER, "1.0.0")
        self.world.attestation(sha256)

    def test_a_gh_that_refuses_a_wrong_workflow_and_a_wrong_ref_for_their_identity_passes(self) -> None:
        self.attest()
        done = self.prove()
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertNotIn("::error::", done.stdout)

    def test_a_gh_that_accepts_a_wrong_identity_fails_the_check(self) -> None:
        self.attest()
        done = self.prove(STUB_ENFORCE="0")
        self.assertEqual(done.returncode, 1)
        self.assertIn("was accepted: the verify command proves nothing", done.stdout)

    def test_a_fault_is_not_a_refusal_for_the_identity(self) -> None:
        self.attest()
        for fault in ("network", "tuf"):
            with self.subTest(fault=fault):
                done = self.prove(STUB_FAULT=fault)
                self.assertEqual(done.returncode, 1)
                self.assertIn("refused for a fault", done.stdout)

    def test_an_unknown_digest_is_not_a_refusal_for_the_identity(self) -> None:
        done = self.prove()
        self.assertEqual(done.returncode, 1)
        self.assertIn("refused for a fault", done.stdout)

    def test_a_refusal_that_does_not_name_the_identity_fails_the_check(self) -> None:
        stub = self.world.bin / "gh"
        stub.write_text("#!/bin/sh\necho 'Error: something else went wrong' >&2\nexit 1\n", encoding="utf-8")
        done = self.prove(real=True)
        self.assertEqual(done.returncode, 1)
        self.assertIn("did not name the identity", done.stdout)


class TheCommandLine(WorldCase):
    def test_self_test_passes_on_the_real_table_with_no_token(self) -> None:
        done = self.world.cli("self-test", real=True, GH_TOKEN=EMPTY)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_the_command_line_has_no_way_to_name_another_table(self) -> None:
        self.world.lock([])
        for flag in ("--table", "--identities", "--grandfather"):
            done = self.world.cli("scan", str(self.world.repo), flag, "x")
            self.assertEqual(done.returncode, 2)

    def test_a_real_table_scan_refuses_an_unset_package_and_names_the_row(self) -> None:
        self.world.package(RENDER, "0.4.0")
        self.world.lock([self.world.entry(RENDER, "0.4.0")])
        done = self.world.cli("scan", str(self.world.repo), real=True)
        self.assertEqual(done.returncode, 1, done.stdout)
        self.assertIn(f"row {RENDER} has first_attested UNSET", done.stderr)
        self.assertIn("no attestation for sha256", done.stderr)

    def test_a_real_table_scan_of_a_lockfile_with_no_first_party_package_passes(self) -> None:
        self.world.lock([])
        done = self.world.cli("scan", str(self.world.repo))
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_fault_inside_the_program_is_a_nonzero_exit(self) -> None:
        self.world.lock([self.world.entry(RENDER, "0.4.0")])
        done = self.world.cli("scan", str(self.world.repo), real=True, PATH="/nonexistent")
        self.assertNotEqual(done.returncode, 0)


class Sabotage(unittest.TestCase):
    """Break each control in a copy of this directory and require the named tests to go red
    there; the unmodified copy must be green. Skipped inside a broken copy's own run."""

    SCRIPT_NAME = "verify-package-attestations.py"
    CASES: ClassVar[dict[str, tuple[str, str, str, set[str]]]] = {
        "the verifier always passes": (
            SCRIPT_NAME,
            '    """Every reason this tarball is not attested by exactly this identity; empty when it is."""\n',
            '    """Every reason this tarball is not attested by exactly this identity; empty when it is."""\n    return []\n',
            {
                "ScanRefuses.test_no_attestation_for_the_digest_is_refused",
                "ScanRefuses.test_a_wrong_repo_is_refused_whether_or_not_gh_enforces_it",
                "ScanRefuses.test_a_wrong_workflow_is_refused_whether_or_not_gh_enforces_it",
                "ScanRefuses.test_a_wrong_ref_is_refused_whether_or_not_gh_enforces_it",
                "ScanRefuses.test_a_gh_fault_is_never_a_pass",
                "ScanRefuses.test_a_version_above_the_floor_without_attestation_fails",
                "ScanRefuses.test_an_unset_row_without_attestation_fails_closed_and_names_the_row",
                "CheckTarballCommand.test_no_attestation_fails_after_every_attempt",
                "CheckTarballCommand.test_a_wrong_identity_fails_whether_or_not_gh_enforces_it",
            },
        ),
        "the certificate is not read again": (
            SCRIPT_NAME,
            '    problems: list[str] = []\n    verification = result.get("verificationResult")',
            '    return []\n    problems: list[str] = []\n    verification = result.get("verificationResult")',
            {
                "ScanRefuses.test_a_ref_that_matches_the_pattern_but_not_the_version_is_refused_by_the_script_alone",
                "ScanRefuses.test_an_attestation_of_other_bytes_is_refused_by_the_script_alone",
                "ScanRefuses.test_a_wrong_repo_is_refused_whether_or_not_gh_enforces_it",
                "TheRealResult.test_the_real_result_is_refused_for_any_other_identity",
            },
        ),
        "the sha512 is not compared": (
            SCRIPT_NAME,
            "    if not hmac.compare_digest(pinned, sha512):",
            "    if False:",
            {"ScanRefuses.test_a_wrong_sha512_is_refused_before_gh_is_asked"},
        ),
        "the manifest inside the tarball is not compared": (
            SCRIPT_NAME,
            "    if inside != (entry.name, entry.version):",
            "    if False:",
            {"ScanRefuses.test_a_tarball_that_is_not_the_package_the_lockfile_names_is_refused"},
        ),
        "a version below the floor is grandfathered without being listed": (
            SCRIPT_NAME,
            "    if entry.version in row.grandfathered:",
            "    if True:",
            {
                "ScanRefuses.test_a_version_above_the_floor_without_attestation_fails",
                "ScanRefuses.test_a_version_below_the_floor_that_is_not_listed_fails",
                "ScanRefuses.test_an_unset_row_without_attestation_fails_closed_and_names_the_row",
            },
        ),
        "one pinned version is removed from the grandfathered list": (
            SCRIPT_NAME,
            'grandfathered=("0.3.1", "0.4.0", "0.6.0", "0.7.0", "0.8.8"),',
            'grandfathered=("0.3.1", "0.4.0", "0.6.0", "0.8.8"),',
            {
                "TheRealTree.test_every_pinned_version_passes_with_no_download_and_no_lookup",
                "TheTable.test_the_real_table_lists_exactly_the_versions_pinned_before_attestation",
            },
        ),
        "an unpinned version is added to the grandfathered list": (
            SCRIPT_NAME,
            'grandfathered=("0.2.0", "0.3.0"),',
            'grandfathered=("0.2.0", "0.3.0", "9.9.9"),',
            {
                "TheRealTree.test_a_version_that_is_not_listed_fails_closed_on_every_package_with_a_list",
                "TheTable.test_the_real_table_lists_exactly_the_versions_pinned_before_attestation",
            },
        ),
        "an unset row grandfathers every version": (
            SCRIPT_NAME,
            "    if entry.version in row.grandfathered:",
            '    if entry.version in row.grandfathered or row.first_attested == UNSET:',
            {
                "TheRealTree.test_a_version_that_is_not_listed_fails_closed_on_every_package_with_a_list",
                "TheRealTree.test_one_unlisted_version_among_listed_ones_fails_the_whole_scan",
                "ScanRefuses.test_an_unset_row_without_attestation_fails_closed_and_names_the_row",
            },
        ),
        "a failed verification no longer refuses": (
            SCRIPT_NAME,
            "    if problems:\n        return Outcome(\"refuse\", f\"{label}: {'; '.join(problems)}.",
            "    if False:\n        return Outcome(\"refuse\", f\"{label}: {'; '.join(problems)}.",
            {
                "ScanRefuses.test_no_attestation_for_the_digest_is_refused",
                "ScanRefuses.test_an_unset_row_without_attestation_fails_closed_and_names_the_row",
            },
        ),
        "a commit off main is accepted": (
            SCRIPT_NAME,
            "    if status not in ON_MAIN:",
            "    if False:",
            {"ScanRefuses.test_a_commit_that_is_not_on_main_is_refused", "CheckTarballCommand.test_a_commit_that_is_not_on_main_fails"},
        ),
        "the tarball URL is not checked": (
            SCRIPT_NAME,
            "    if match is None or match.group(1) != entry.name or match.group(2) != entry.version:",
            "    if False:",
            {"ScanRefuses.test_a_tarball_url_that_is_not_the_packages_download_url_is_refused_before_any_call"},
        ),
        "an unknown package is not refused": (
            SCRIPT_NAME,
            "    if row is None:\n        return \"refuse\", (",
            "    if False:\n        return \"refuse\", (",
            {"ScanRefuses.test_a_package_that_is_not_in_the_table_is_refused"},
        ),
        "no lockfile passes": (
            SCRIPT_NAME,
            "    if not lockfiles:",
            "    if False:",
            {"ScanRefuses.test_no_lockfile_is_refused_not_passed"},
        ),
        "the wrong-identity proof accepts an acceptance": (
            SCRIPT_NAME,
            "        if done.returncode == 0:\n            problems.append(f\"{label} was accepted",
            "        if False:\n            problems.append(f\"{label} was accepted",
            {"ProveWrongIdentityCommand.test_a_gh_that_accepts_a_wrong_identity_fails_the_check"},
        ),
        "the wrong-identity proof accepts a fault": (
            SCRIPT_NAME,
            "        elif FAULT.search(output):",
            "        elif False:",
            {"ProveWrongIdentityCommand.test_a_fault_is_not_a_refusal_for_the_identity", "ProveWrongIdentityCommand.test_an_unknown_digest_is_not_a_refusal_for_the_identity"},
        ),
        "the self-hosted runner is not refused": (
            SCRIPT_NAME,
            "    if field(\"runnerEnvironment\") != GITHUB_HOSTED:",
            "    if False:",
            {"ScanRefuses.test_a_self_hosted_runner_is_refused_whether_or_not_gh_enforces_it"},
        ),
        "the token is not sent to curl": (
            SCRIPT_NAME,
            "        stdin=f'header = \"Authorization: Bearer {token}\"\\n',",
            "        stdin=None,",
            {"ScanPasses.test_every_identity_flag_is_sent_to_gh", "ScanPasses.test_the_right_tarball_with_the_right_identity_passes"},
        ),
        "the consumer ref is not cross-checked against the table": (
            SCRIPT_NAME,
            "    table_row_for(args.repo, args.signer_workflow[len(prefix):], args.ref, IDENTITIES)\n",
            "    _unchecked = (args.signer_workflow, IDENTITIES)\n",
            {
                "CheckTarballCommand.test_a_consumer_shape_that_no_table_row_claims_is_refused_before_any_call",
                "CheckTarballCommand.test_check_inputs_refuses_a_ref_that_no_table_row_accepts",
            },
        ),
    }

    def run_copy(self, edit):  # type: ignore[no-untyped-def]
        with tempfile.TemporaryDirectory(prefix="verify-package-attestations-sabotage-") as tmp:
            copy_dir = pathlib.Path(tmp, ".github", "actions", "verify-package-attestations")
            shutil.copytree(HERE, copy_dir, ignore=shutil.ignore_patterns("__pycache__"))
            if edit:
                name, old, new, _ = edit
                target = copy_dir / name
                text = target.read_text(encoding="utf-8")
                self.assertEqual(text.count(old), 1, f"the control moved, update the sabotage: {old!r}")
                target.write_text(text.replace(old, new), encoding="utf-8")
            return subprocess.run(
                [sys.executable, "-m", "unittest", "discover", "-s", str(copy_dir), "-p", "test_verify_package_attestations.py", "-v"],
                capture_output=True,
                text=True,
                env={**os.environ, INSIDE_SABOTAGE: "1"},
                check=False,
            )

    @unittest.skipIf(os.environ.get(INSIDE_SABOTAGE), "running inside a sabotaged copy")
    def test_each_broken_control_turns_its_tests_red(self) -> None:
        for name, edit in self.CASES.items():
            with self.subTest(case=name):
                red = self.run_copy(edit)
                self.assertNotEqual(red.returncode, 0, f"{name} stayed green")
                found = re.findall(r"^(?:FAIL|ERROR): (\w+) \(\S*?\.(\w+)\.\w+\)", red.stderr, re.MULTILINE)
                failed = {f"{cls}.{meth}" for meth, cls in found}
                self.assertTrue(edit[3] <= failed, f"{name}: expected red {sorted(edit[3])}, got {sorted(failed)}")

    @unittest.skipIf(os.environ.get(INSIDE_SABOTAGE), "running inside a sabotaged copy")
    def test_the_unmodified_copy_is_green(self) -> None:
        green = self.run_copy(None)
        self.assertEqual(green.returncode, 0, green.stderr)


if __name__ == "__main__":
    unittest.main()
