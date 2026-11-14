#!/usr/bin/env python3
"""Check the signed build attestation of the @branchleft npm tarballs.

Usage:
  verify-package-attestations.py scan ROOT
  verify-package-attestations.py check-inputs TARBALL --repo R --signer-workflow S --ref REF
  verify-package-attestations.py check-tarball TARBALL --repo R --signer-workflow S --ref REF
  verify-package-attestations.py prove-wrong-identity TARBALL --repo R --signer-workflow S --ref REF
  verify-package-attestations.py self-test

Needs GH_TOKEN (all but self-test and check-inputs), and `gh` and `curl` on PATH. Exit 0 only when
every check passed; 1 when a check refused; 2 on a usage error or a fault in this
program. Every unexpected state is a refusal: this program has no "skip" path.
The identity table, the rules and the reasons: verify-package-attestations.md.
"""

from __future__ import annotations

import argparse
import base64
import dataclasses
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

UNSET = "UNSET"
ISSUER = "https://token.actions.githubusercontent.com"
PREDICATE = "https://slsa.dev/provenance/v1"
GITHUB_HOSTED = "github-hosted"
MAIN_BRANCH = "main"
ON_MAIN = ("behind", "identical")
TARBALL_LIMIT = 100 * 1024 * 1024
STEP_TIMEOUT = 180

VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?")
REPO = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*")
WORKFLOW_FILE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml")
COMMIT = re.compile(r"[0-9a-f]{40}")
PACKAGE_NAME = re.compile(r"@branchleft/[a-z0-9][a-z0-9._-]*")
TARBALL_URL = re.compile(
    r"https://npm\.pkg\.github\.com/download/(@branchleft/[a-z0-9][a-z0-9._-]*)/"
    r"(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)/[0-9a-f]{40}"
)
# Faults say nothing about identity: a refusal containing one is not a refusal.
FAULT = re.compile(
    r"dial tcp|i/o timeout|no such host|connection (reset|refused)|TUF|Sigstore verif"
    r"|no valid Sigstore|token|denied|unauthorized|forbidden|HTTP \d{3}|rate limit|timed out",
    re.IGNORECASE,
)
IDENTITY_REFUSAL = re.compile(r'verifying with issuer|no matching CertificateIdentity|Subject Alternative Name')


@dataclasses.dataclass(frozen=True)
class Identity:
    """Who may have published one package. A reviewed literal, never read from the artifact."""

    package: str
    repo: str
    workflow: str
    ref_regex: str
    ref_template: str | None
    first_attested: str = UNSET
    grandfathered: tuple[str, ...] = ()


# One row per published @branchleft package. `first_attested` is UNSET until the
# first release published through the attesting workflow is known. `grandfathered`
# is the exact list of versions published before attestation began; only those
# versions pass without an attestation, on a floored row and on an UNSET row alike,
# and every other version fails closed. The list is derived from the versions the
# repositories' lockfiles pin, and is extended only in a reviewed change.
# `ref_template` is the exact ref when the tag maps to the package
# version; None where it does not (the check then uses `ref_regex` alone).
IDENTITIES: tuple[Identity, ...] = (
    Identity(
        package="@branchleft/ghost-platform-render-core",
        repo="branchLeft/ghost-platform",
        workflow="publish-render-core.yml",
        ref_regex=r"refs/tags/render-core-v\d+\.\d+\.\d+",
        ref_template="refs/tags/render-core-v{version}",
        grandfathered=("0.1.1", "0.2.0", "0.3.0", "0.4.0"),
    ),
    Identity(
        package="@branchleft/ghost-platform-tenant",
        repo="branchLeft/ghost-platform",
        workflow="publish-tenant-package.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template="refs/tags/v{version}",
        grandfathered=("5.0.0", "7.0.0", "8.0.0"),
    ),
    Identity(
        package="@branchleft/ghost-platform-broker-api",
        repo="branchLeft/ghost-platform",
        workflow="speckify.yml",
        ref_regex=r"refs/heads/main",
        ref_template="refs/heads/main",
    ),
    Identity(
        package="@branchleft/hetzner-host",
        repo="branchLeft/shared-infra",
        workflow="publish-hetzner-host.yml",
        ref_regex=r"refs/tags/hetzner-host-v\d+\.\d+\.\d+",
        ref_template="refs/tags/hetzner-host-v{version}",
        grandfathered=("0.2.0", "0.3.0"),
    ),
    Identity(
        package="@branchleft/components",
        repo="branchLeft/components",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template=None,
        grandfathered=("0.3.0", "0.6.0"),
    ),
    Identity(
        package="@branchleft/brand-branchleft",
        repo="branchLeft/components",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template=None,
        grandfathered=("0.3.0",),
    ),
    Identity(
        package="@branchleft/brand-publicpress",
        repo="branchLeft/components",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template=None,
    ),
    Identity(
        package="@branchleft/tsconfig",
        repo="branchLeft/standards",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template="refs/tags/v{version}",
        grandfathered=("0.3.1", "0.4.0", "0.6.0", "0.7.0", "0.8.8"),
    ),
    Identity(
        package="@branchleft/prettier-config",
        repo="branchLeft/standards",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template="refs/tags/v{version}",
        grandfathered=("0.3.1", "0.8.8"),
    ),
    Identity(
        package="@branchleft/vitest-config",
        repo="branchLeft/standards",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template="refs/tags/v{version}",
        grandfathered=("0.4.0", "0.6.0"),
    ),
    Identity(
        package="@branchleft/eslint-config",
        repo="branchLeft/standards",
        workflow="publish.yml",
        ref_regex=r"refs/tags/v\d+\.\d+\.\d+",
        ref_template="refs/tags/v{version}",
    ),
)


class Refusal(Exception):
    """A check refused. The message is what the reader acts on."""


def version_key(version: str) -> tuple[int, int, int, int, tuple[tuple[int, int, str], ...]] | None:
    """Semver precedence key, or None when ``version`` is not a semver."""
    match = VERSION.fullmatch(version)
    if match is None:
        return None
    major, minor, patch, pre = match.groups()
    if pre is None:
        return (int(major), int(minor), int(patch), 1, ())
    ids = tuple((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split("."))
    return (int(major), int(minor), int(patch), 0, ids)


def validate_table(table: Sequence[Identity]) -> list[str]:
    """Every way the table can be wrong. An empty list means it is usable."""
    problems: list[str] = []
    seen: set[str] = set()
    for row in table:
        where = f"identity table row {row.package}"
        if not PACKAGE_NAME.fullmatch(row.package):
            problems.append(f"{where}: the package is not an @branchleft name")
        if row.package in seen:
            problems.append(f"{where}: listed twice")
        seen.add(row.package)
        if not REPO.fullmatch(row.repo):
            problems.append(f"{where}: repo is not owner/name")
        if not WORKFLOW_FILE.fullmatch(row.workflow):
            problems.append(f"{where}: workflow is not a file name")
        try:
            ref_pattern = re.compile(row.ref_regex)
        except re.error:
            problems.append(f"{where}: ref_regex does not compile")
            continue
        if not row.ref_regex.startswith("refs/"):
            problems.append(f"{where}: ref_regex does not start with refs/")
        if row.ref_template is not None:
            sample = row.ref_template.format(version="1.2.3")
            if not ref_pattern.fullmatch(sample):
                problems.append(f"{where}: ref_template gives {sample}, which ref_regex does not match")
        floor = None if row.first_attested == UNSET else version_key(row.first_attested)
        if row.first_attested != UNSET and floor is None:
            problems.append(f"{where}: first_attested is neither {UNSET} nor a version")
        if len(set(row.grandfathered)) != len(row.grandfathered):
            problems.append(f"{where}: grandfathered lists a version twice")
        for old in row.grandfathered:
            key = version_key(old)
            if key is None:
                problems.append(f"{where}: grandfathered {old!r} is not a version")
            elif floor is not None and key >= floor:
                problems.append(f"{where}: grandfathered {old} is not below first_attested {row.first_attested}")
    return problems


# ---------------------------------------------------------------- lockfiles


@dataclasses.dataclass(frozen=True)
class LockEntry:
    lockfile: str
    name: str
    version: str
    integrity: str | None
    tarball: str | None


PNPM_KEY = re.compile(r"  '?/?(@branchleft/[A-Za-z0-9._-]+)@([^'\s:(]+)'?:")
PNPM_FIRST_PARTY = re.compile(r"""  ["']?/?@branchleft/""")


def parse_pnpm_lock(text: str, source: str) -> list[LockEntry]:
    """Every @branchleft entry of the `packages:` section; a line it cannot read is a refusal."""
    entries: list[LockEntry] = []
    lines = text.splitlines()
    in_packages = False
    index = 0
    while index < len(lines):
        line = lines[index]
        if line and not line.startswith(" "):
            in_packages = line.rstrip() == "packages:"
            index += 1
            continue
        if in_packages and PNPM_FIRST_PARTY.match(line):
            key = PNPM_KEY.fullmatch(line.rstrip())
            if key is None:
                raise Refusal(f"{source}: cannot read the @branchleft entry {line.strip()!r}")
            body: list[str] = []
            index += 1
            while index < len(lines) and (not lines[index].strip() or lines[index].startswith("    ")):
                body.append(lines[index])
                index += 1
            resolution = next((b for b in body if b.startswith("    resolution:")), "")
            integrity = re.search(r"integrity:\s*([^,}\s]+)", resolution)
            tarball = re.search(r"tarball:\s*([^,}\s]+)", resolution)
            entries.append(
                LockEntry(
                    source,
                    key.group(1),
                    key.group(2),
                    integrity.group(1) if integrity else None,
                    tarball.group(1) if tarball else None,
                )
            )
            continue
        index += 1
    downloads = text.count("npm.pkg.github.com/download/@branchleft/")
    parsed = sum(1 for e in entries if "npm.pkg.github.com/download/@branchleft/" in (e.tarball or ""))
    if downloads != parsed:
        raise Refusal(f"{source}: {downloads} GitHub Packages tarball URL(s) but {parsed} read from the packages: section")
    return entries


def parse_package_lock(text: str, source: str) -> list[LockEntry]:
    try:
        data = json.loads(text)
    except ValueError as error:
        raise Refusal(f"{source}: not JSON: {error}") from error
    if not isinstance(data, dict):
        raise Refusal(f"{source}: not a lockfile object")
    packages = data.get("packages")
    if not isinstance(packages, dict):
        if "@branchleft/" in text:
            raise Refusal(f"{source}: lockfileVersion 1 has no packages map; this program cannot read it")
        return []
    entries: list[LockEntry] = []
    for path, value in packages.items():
        if "node_modules/@branchleft/" not in path:
            continue
        if not isinstance(value, dict):
            raise Refusal(f"{source}: cannot read the entry {path!r}")
        if value.get("link") is True:
            continue
        name = "@branchleft/" + path.rsplit("node_modules/@branchleft/", 1)[1]
        entries.append(
            LockEntry(
                source,
                name,
                str(value.get("version", "")),
                value.get("integrity") if isinstance(value.get("integrity"), str) else None,
                value.get("resolved") if isinstance(value.get("resolved"), str) else None,
            )
        )
    return entries


def find_lockfiles(root: Path) -> list[Path]:
    found: list[Path] = []
    for directory, subdirectories, files in os.walk(root):
        subdirectories[:] = sorted(d for d in subdirectories if d not in {"node_modules", ".git"})
        for name in sorted(files):
            if name in {"pnpm-lock.yaml", "package-lock.json"}:
                found.append(Path(directory, name))
    return found


# ---------------------------------------------------------------- tools


def run(argv: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run one tool. It never inherits standard input, so it can never wait on a terminal."""
    return subprocess.run(
        argv,
        input=stdin,
        stdin=subprocess.DEVNULL if stdin is None else None,
        capture_output=True,
        text=True,
        check=False,
        timeout=STEP_TIMEOUT,
    )


def digests(path: Path) -> tuple[str, bytes]:
    """The sha256 (hex) and the sha512 (raw) of a file."""
    sha256, sha512 = hashlib.sha256(), hashlib.sha512()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            sha256.update(block)
            sha512.update(block)
    return sha256.hexdigest(), sha512.digest()


def download(url: str, destination: Path, token: str) -> None:
    """Fetch a tarball. The token goes on standard input, never on the command line."""
    done = run(
        [
            "curl", "--fail", "--silent", "--show-error", "--location",
            "--proto", "=https", "--proto-redir", "=https",
            "--max-time", "120", "--max-filesize", str(TARBALL_LIMIT),
            "--output", str(destination), "--config", "-", url,
        ],
        stdin=f'header = "Authorization: Bearer {token}"\n',
    )  # fmt: skip
    if done.returncode != 0:
        raise Refusal(f"could not download {url}: {done.stderr.strip()[:300]}")


def tarball_manifest(path: Path) -> tuple[object, object]:
    """The name and version inside the tarball, as `pnpm pack` writes them."""
    try:
        with tarfile.open(path, "r:gz") as archive:
            member = archive.getmember("package/package.json")
            if not member.isfile() or member.size > 1 << 20:
                raise Refusal("package/package.json is not a small regular file")
            handle = archive.extractfile(member)
            if handle is None:
                raise Refusal("package/package.json cannot be read")
            manifest = json.loads(handle.read())
    except (tarfile.TarError, KeyError, OSError, ValueError, EOFError) as error:
        raise Refusal(f"cannot read package/package.json from the tarball: {error}") from error
    if not isinstance(manifest, dict):
        raise Refusal("package/package.json is not an object")
    return manifest.get("name"), manifest.get("version")


# ---------------------------------------------------------------- attestation


def gh_verify(
    tarball: Path, repo: str, signer: str, ref: str | None
) -> subprocess.CompletedProcess[str]:
    argv = [
        "gh", "attestation", "verify", str(tarball),
        "--repo", repo,
        "--signer-workflow", signer,
        "--cert-oidc-issuer", ISSUER,
        "--predicate-type", PREDICATE,
        "--deny-self-hosted-runners",
        "--format", "json",
    ]  # fmt: skip
    if ref is not None:
        argv += ["--source-ref", ref]
    return run(argv)


def judge(result: Any, sha256_hex: str, repo: str, signer: str, ref_regex: str, ref: str | None) -> list[str]:
    """What is wrong with one verified result, read from the certificate fields.

    gh already enforces the flags it was given. These are the same facts read again
    from the certificate, which the workflow that was attested cannot alter, so a gh
    that is wrong, old or replaced still cannot make a bad tarball pass.
    """
    problems: list[str] = []
    verification = result.get("verificationResult") if isinstance(result, dict) else None
    if not isinstance(verification, dict):
        return ["the result has no verificationResult"]
    signature = verification.get("signature")
    certificate = signature.get("certificate") if isinstance(signature, dict) else None
    statement = verification.get("statement")
    if not isinstance(certificate, dict) or not isinstance(statement, dict):
        return ["the result has no certificate or no statement"]

    def field(name: str) -> object:
        return certificate.get(name)

    ref_seen = field("sourceRepositoryRef")
    if statement.get("predicateType") != PREDICATE:
        problems.append(f"predicate type is {statement.get('predicateType')!r}, not {PREDICATE}")
    subjects = statement.get("subject")
    attested = [
        s.get("digest", {}).get("sha256")
        for s in (subjects if isinstance(subjects, list) else [])
        if isinstance(s, dict) and isinstance(s.get("digest"), dict)
    ]
    if sha256_hex not in attested:
        problems.append(f"the attested subject digests {attested} do not include this tarball's sha256 {sha256_hex}")
    if field("issuer") != ISSUER:
        problems.append(f"issuer is {field('issuer')!r}, not {ISSUER}")
    if field("githubWorkflowRepository") != repo:
        problems.append(f"workflow repository is {field('githubWorkflowRepository')!r}, not {repo}")
    if field("sourceRepositoryURI") != f"https://github.com/{repo}":
        problems.append(f"source repository is {field('sourceRepositoryURI')!r}, not https://github.com/{repo}")
    if not isinstance(ref_seen, str) or not re.fullmatch(ref_regex, ref_seen):
        problems.append(f"source ref is {ref_seen!r}, which does not match {ref_regex}")
    if ref is not None and ref_seen != ref:
        problems.append(f"source ref is {ref_seen!r}, not {ref}")
    if field("subjectAlternativeName") != f"https://github.com/{signer}@{ref_seen}":
        problems.append(f"signer identity is {field('subjectAlternativeName')!r}, not https://github.com/{signer}@{ref_seen}")
    if field("githubWorkflowRef") != ref_seen:
        problems.append(f"workflow ref is {field('githubWorkflowRef')!r}, not the source ref")
    if field("runnerEnvironment") != GITHUB_HOSTED:
        problems.append(f"runner is {field('runnerEnvironment')!r}, not {GITHUB_HOSTED}")
    commit = field("sourceRepositoryDigest")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        problems.append(f"source commit is {commit!r}, not a 40-hex sha")
    elif field("buildSignerDigest") != commit:
        problems.append("signer commit differs from the source commit")
    return problems


def commit_on_main(repo: str, commit: str) -> str | None:
    """None when the commit is on main, otherwise what is wrong."""
    done = run(["gh", "api", f"repos/{repo}/compare/{MAIN_BRANCH}...{commit}", "--jq", ".status"])
    status = done.stdout.strip()
    if done.returncode != 0:
        return f"could not ask whether {commit} is on {MAIN_BRANCH}: {done.stderr.strip()[:200]}"
    if status not in ON_MAIN:
        return f"commit {commit} is not on {MAIN_BRANCH} (compare status {status!r})"
    return None


def verify_tarball(
    tarball: Path, repo: str, signer: str, ref_regex: str, ref: str | None, main_cache: dict[tuple[str, str], str | None]
) -> list[str]:
    """Every reason this tarball is not attested by exactly this identity; empty when it is."""
    sha256_hex, _ = digests(tarball)
    done = gh_verify(tarball, repo, signer, ref)
    if done.returncode != 0:
        detail = (done.stderr.strip() or done.stdout.strip())[:300]
        if "HTTP 404" in detail:
            return [f"no attestation for sha256 {sha256_hex} in {repo}: {detail}"]
        return [f"gh attestation verify refused: {detail}"]
    try:
        results = json.loads(done.stdout)
    except ValueError:
        return ["gh attestation verify exited 0 but printed no JSON"]
    if not isinstance(results, list) or not results:
        return ["gh attestation verify exited 0 but printed no result"]
    first_problems: list[str] | None = None
    for result in results:
        problems = judge(result, sha256_hex, repo, signer, ref_regex, ref)
        if not problems:
            commit = result["verificationResult"]["signature"]["certificate"]["sourceRepositoryDigest"]
            key = (repo, commit)
            if key not in main_cache:
                main_cache[key] = commit_on_main(repo, commit)
            main_problem = main_cache[key]
            return [main_problem] if main_problem else []
        if first_problems is None:
            first_problems = problems
    return first_problems or ["no result"]


def prove_wrong_identity(tarball: Path, repo: str, signer: str, ref: str) -> list[str]:
    """Each wrong identity must be refused, and refused for the identity."""
    problems: list[str] = []
    cases = (
        ("a wrong signer workflow", signer + ".sabotage", ref, IDENTITY_REFUSAL),
        ("a wrong source ref", signer, ref + ".sabotage", re.compile(r"expected SourceRepositoryRef to be " + re.escape(ref + ".sabotage"))),
    )  # fmt: skip
    for label, wrong_signer, wrong_ref, marker in cases:
        done = gh_verify(tarball, repo, wrong_signer, wrong_ref)
        output = done.stdout + done.stderr
        if done.returncode == 0:
            problems.append(f"{label} was accepted: the verify command proves nothing")
        elif FAULT.search(output):
            problems.append(f"{label} was refused for a fault, not for the identity: {output.strip()[:300]}")
        elif not marker.search(output):
            problems.append(f"{label} was refused, but gh did not name the identity: {output.strip()[:300]}")
    return problems


# ---------------------------------------------------------------- scan


@dataclasses.dataclass
class Outcome:
    status: str
    message: str


def classify(entry: LockEntry, row: Identity | None) -> tuple[str, str]:
    if row is None:
        return "refuse", (
            f"{entry.name}@{entry.version} ({entry.lockfile}) is not in the identity table: "
            "add a reviewed row for it before it can be installed"
        )
    if entry.version in row.grandfathered:
        return "grandfathered", (
            f"{entry.name}@{entry.version} is listed as published before attestation began "
            f"(first_attested {row.first_attested})"
        )
    return "verify", ""


def failure_context(entry: LockEntry, row: Identity) -> str:
    if row.first_attested == UNSET:
        if not row.grandfathered:
            return (
                f"identity table row {row.package} has first_attested UNSET: no version of it is grandfathered "
                "and none passes without a verified attestation until the floor is set"
            )
        return (
            f"identity table row {row.package} has first_attested UNSET: only its {len(row.grandfathered)} "
            f"grandfathered version(s) {', '.join(row.grandfathered)} pass without a verified attestation, "
            f"and {entry.version} is not one of them"
        )
    key, floor = version_key(entry.version), version_key(row.first_attested)
    if key is None:
        return f"{entry.version} is not a version, so it cannot be placed against the floor {row.first_attested}"
    if floor is not None and key < floor:
        return (
            f"{entry.version} is below the floor {row.first_attested} but is not in the grandfathered list of "
            f"row {row.package}: list it there in a reviewed change, or install an attested version"
        )
    return f"{entry.version} is at or above the floor {row.first_attested} of row {row.package}, so an attestation is required"


def check_entry(
    entry: LockEntry,
    table: Sequence[Identity],
    token: str,
    workdir: Path,
    fetched: dict[str, Path],
    main_cache: dict[tuple[str, str], str | None],
) -> Outcome:
    row = next((r for r in table if r.package == entry.name), None)
    status, message = classify(entry, row)
    if status == "refuse":
        return Outcome("refuse", message)
    if status == "grandfathered":
        return Outcome("grandfathered", message)
    assert row is not None
    label = f"{entry.name}@{entry.version} ({entry.lockfile})"
    if entry.integrity is None or not entry.integrity.startswith("sha512-"):
        return Outcome("refuse", f"{label}: the lockfile gives no sha512 integrity ({entry.integrity!r}), so the bytes cannot be pinned")
    match = TARBALL_URL.fullmatch(entry.tarball or "")
    if match is None or match.group(1) != entry.name or match.group(2) != entry.version:
        return Outcome("refuse", f"{label}: the tarball URL {entry.tarball!r} is not the GitHub Packages download URL of this package and version")
    assert entry.tarball is not None
    if entry.tarball not in fetched:
        destination = workdir / f"tarball-{len(fetched)}.tgz"
        try:
            download(entry.tarball, destination, token)
        except Refusal as refusal:
            return Outcome("refuse", f"{label}: {refusal}")
        fetched[entry.tarball] = destination
    path = fetched[entry.tarball]
    _, sha512 = digests(path)
    try:
        pinned = base64.b64decode(entry.integrity[len("sha512-"):], validate=True)
    except ValueError:
        return Outcome("refuse", f"{label}: the lockfile integrity is not base64")
    if not hmac.compare_digest(pinned, sha512):
        return Outcome("refuse", f"{label}: the downloaded bytes do not match the lockfile integrity")
    try:
        inside = tarball_manifest(path)
    except Refusal as refusal:
        return Outcome("refuse", f"{label}: {refusal}")
    if inside != (entry.name, entry.version):
        return Outcome("refuse", f"{label}: the tarball is {inside[0]}@{inside[1]}, not what the lockfile names")
    ref = row.ref_template.format(version=entry.version) if row.ref_template else None
    signer = f"{row.repo}/.github/workflows/{row.workflow}"
    problems = verify_tarball(path, row.repo, signer, row.ref_regex, ref, main_cache)
    if problems:
        return Outcome("refuse", f"{label}: {'; '.join(problems)}. {failure_context(entry, row)}")
    return Outcome("ok", f"{label} was attested by {signer}")


def scan(root: Path, table: Sequence[Identity], token: str) -> int:
    table_problems = validate_table(table)
    if table_problems:
        for problem in table_problems:
            print(f"error: {problem}", file=sys.stderr)
        return 1
    if not token:
        print("error: GH_TOKEN is empty: the downloads and the attestation lookups need a token", file=sys.stderr)
        return 1
    lockfiles = find_lockfiles(root)
    if not lockfiles:
        print(f"error: no pnpm-lock.yaml or package-lock.json under {root}: a check over nothing would pass for the wrong reason", file=sys.stderr)
        return 1
    entries: list[LockEntry] = []
    failures: list[str] = []
    for lockfile in lockfiles:
        name = str(lockfile.relative_to(root))
        try:
            text = lockfile.read_text(encoding="utf-8")
            entries += parse_pnpm_lock(text, name) if lockfile.name == "pnpm-lock.yaml" else parse_package_lock(text, name)
        except (Refusal, OSError, UnicodeError) as error:
            failures.append(str(error))
    counts = {"ok": 0, "grandfathered": 0}
    fetched: dict[str, Path] = {}
    main_cache: dict[tuple[str, str], str | None] = {}
    with tempfile.TemporaryDirectory(prefix="verify-package-attestations-") as scratch:
        for entry in entries:
            outcome = check_entry(entry, table, token, Path(scratch), fetched, main_cache)
            if outcome.status == "refuse":
                failures.append(outcome.message)
            else:
                counts[outcome.status] += 1
                print(f"{outcome.status}: {outcome.message}")
    for failure in failures:
        print(f"refused: {failure}", file=sys.stderr)
    print(
        f"{len(lockfiles)} lockfile(s), {len(entries)} @branchleft package(s): "
        f"{counts['ok']} attested, {counts['grandfathered']} grandfathered, {len(failures)} refused"
    )
    if failures and os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error::{len(failures)} @branchleft package(s) were refused; the lines above name each one")
    return 1 if failures else 0


# ---------------------------------------------------------------- command line


def table_row_for(repo: str, file_name: str, ref: str, table: Sequence[Identity]) -> Identity:
    """The row a consumer claims: same repo and signer workflow file, and a ref its pattern accepts.

    A consumer that no row claims is refused here, before anything is attested, not at the first publish.
    """
    named = [row for row in table if row.repo == repo and row.workflow == file_name]
    if not named:
        raise Refusal(f"no identity table row names {repo}/.github/workflows/{file_name}")
    for row in named:
        if re.fullmatch(row.ref_regex, ref):
            return row
    raise Refusal(f"ref {ref!r} matches no ref_regex of the identity table rows for {repo}/.github/workflows/{file_name}")


def literal_identity(args: argparse.Namespace) -> tuple[str, str, str]:
    if not REPO.fullmatch(args.repo):
        raise Refusal(f"--repo {args.repo!r} is not owner/name")
    prefix = f"{args.repo}/.github/workflows/"
    if not args.signer_workflow.startswith(prefix) or not WORKFLOW_FILE.fullmatch(args.signer_workflow[len(prefix):]):
        raise Refusal(f"--signer-workflow {args.signer_workflow!r} is not a workflow file of {args.repo}")
    if not re.fullmatch(r"refs/(tags|heads)/[A-Za-z0-9._/-]+", args.ref) or ".." in args.ref:
        raise Refusal(f"--ref {args.ref!r} is not an exact refs/tags/... or refs/heads/... ref")
    table_row_for(args.repo, args.signer_workflow[len(prefix):], args.ref, IDENTITIES)
    return args.repo, args.signer_workflow, args.ref


def command_check_tarball(args: argparse.Namespace) -> int:
    repo, signer, ref = literal_identity(args)
    tarball = Path(args.tarball)
    if not tarball.is_file():
        raise Refusal(f"{tarball} is not a file")
    problems: list[str] = []
    for attempt in range(1, args.attempts + 1):
        problems = verify_tarball(tarball, repo, signer, re.escape(ref), ref, {})
        if not problems:
            print(f"verified: {tarball} was attested by {signer} at {ref}")
            return 0
        print(f"attempt {attempt} of {args.attempts}: {'; '.join(problems)}")
        if attempt < args.attempts:
            time.sleep(attempt * args.pause)
    print(f"::error::{tarball} did not verify as attested by {signer} at {ref}: {'; '.join(problems)}")
    return 1


def command_prove(args: argparse.Namespace) -> int:
    repo, signer, ref = literal_identity(args)
    tarball = Path(args.tarball)
    if not tarball.is_file():
        raise Refusal(f"{tarball} is not a file")
    problems = prove_wrong_identity(tarball, repo, signer, ref)
    for problem in problems:
        print(f"::error::{problem}")
    if problems:
        return 1
    print("A wrong signer workflow and a wrong source ref were each refused for their identity, as required.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("self-test", help="check the identity table and exit")
    scanner = commands.add_parser("scan", help="check every @branchleft tarball in the lockfiles under ROOT")
    scanner.add_argument("root")
    for name in ("check-inputs", "check-tarball", "prove-wrong-identity"):
        sub = commands.add_parser(name)
        sub.add_argument("tarball")
        sub.add_argument("--repo", required=True)
        sub.add_argument("--signer-workflow", required=True)
        sub.add_argument("--ref", required=True)
        if name == "check-tarball":
            sub.add_argument("--attempts", type=int, default=5)
            sub.add_argument("--pause", type=int, default=12)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "self-test":
            problems = validate_table(IDENTITIES)
            for problem in problems:
                print(f"error: {problem}", file=sys.stderr)
            print("the identity table is usable" if not problems else "the identity table is not usable")
            return 1 if problems else 0
        if args.command == "check-inputs":
            literal_identity(args)
            if not Path(args.tarball).is_file():
                raise Refusal(f"{args.tarball} is not a file")
            print("the tarball and the identity are well formed")
            return 0
        token = os.environ.get("GH_TOKEN", "")
        if not token:
            print("error: GH_TOKEN is empty", file=sys.stderr)
            return 1
        if args.command == "scan":
            return scan(Path(args.root), IDENTITIES, token)
        if args.command == "check-tarball":
            if args.attempts < 1 or args.pause < 0:
                raise Refusal("--attempts must be at least 1 and --pause at least 0")
            return command_check_tarball(args)
        return command_prove(args)
    except Refusal as refusal:
        print(f"error: {refusal}", file=sys.stderr)
        return 1
    except Exception as error:  # noqa: BLE001 - any fault is a refusal, never a pass
        print(f"error: unexpected fault: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
