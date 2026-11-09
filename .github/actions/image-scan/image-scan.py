#!/usr/bin/env python3
"""Image scan gate: judges a Grype report of a container image.

Commands: `check-input` refuses an image reference that is not pinned by digest, `install`
fetches a pinned, checksum-verified Syft or Grype, `evaluate` turns the Grype JSON into
findings, severity counts and a verdict, and `list-images` reads the manual report's list. The rules and their reasons are in
image-scan.md beside this file.

The blocking threshold is a constant, not an argument: no caller can lower it. A finding at
or above it blocks unless a valid, unexpired exemption covers a finding that has no fix.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import io
import json
import pathlib
import platform
import re
import sys
import tarfile
import urllib.parse
import urllib.request

BLOCKING = ("critical", "high")
SEVERITIES = ("critical", "high", "medium", "low", "negligible", "unknown")
MAX_EXEMPTION_DAYS = 30
MAX_REASON = 500
MAX_ANNOTATIONS = 20
MAX_LISTED = 30

CLAUSE_VULN = "SEC-4"
CLAUSE_EXEMPTION = "SEC-5"
CLAUSE_SCAN = "SEC-7"
CLAUSE_SBOM = "CON-11"

EXEMPTION_KEYS = {"vulnerability", "package", "image", "reason", "granted", "expires", "approved-by"}
REQUIRED_KEYS = ("vulnerability", "package", "reason", "granted", "expires")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UNSAFE_RE = re.compile(r"[^A-Za-z0-9 ._:@/+~(),=<>-]")
COMPONENT = r"[a-z0-9]+(?:(?:\.|_|__|-+)[a-z0-9]+)*"
IMAGE_RE = re.compile(
    rf"^(?P<name>(?:(?:{COMPONENT}(?:\.{COMPONENT})*(?::[0-9]+)?)/)?{COMPONENT}(?:/{COMPONENT})*)@sha256:(?P<digest>[0-9a-f]{{64}})$"
)
PLATFORM_RE = re.compile(r"[a-z0-9]+(?:/[a-z0-9]+){0,2}")
GRYPE_SEVERITIES = {
    "Critical": "critical",
    "High": "high",
    "Medium": "medium",
    "Low": "low",
    "Negligible": "negligible",
    "Unknown": "unknown",
}
INSTALL_HOSTS = ("github.com",)


class ScanError(Exception):
    """Something the gate cannot judge. It always fails the step: an unreadable report is never a pass."""


@dataclasses.dataclass(frozen=True)
class Finding:
    clause: str
    file: str
    line: int
    level: str
    message: str

    def as_json(self) -> str:
        return json.dumps(
            {"clause": self.clause, "file": self.file, "line": self.line, "level": self.level, "message": self.message},
            separators=(",", ":"),
        )


@dataclasses.dataclass(frozen=True)
class Exemption:
    index: int
    vulnerability: str
    package: str
    image: str
    reason: str
    granted: datetime.date
    expires: datetime.date
    approved_by: str


@dataclasses.dataclass(frozen=True)
class Match:
    ids: tuple[str, ...]
    severity: str
    package: str
    version: str
    kind: str
    path: str
    fix_versions: tuple[str, ...]
    fix_state: str

    @property
    def vulnerability(self) -> str:
        return self.ids[0]

    @property
    def has_fix(self) -> bool:
        return self.fix_state == "fixed" or bool(self.fix_versions)


@dataclasses.dataclass
class Result:
    counts: dict[str, int]
    blocking: int
    exempted: int
    packages: int
    findings: list[Finding]

    @property
    def failed(self) -> bool:
        return self.blocking > 0 or any(f.level == "error" for f in self.findings)

    @property
    def verdict(self) -> str:
        return "BLOCK" if self.failed else "PASS"


def clean(text: object, limit: int = 160) -> str:
    """Text safe to print in a log line, an annotation or a table cell: nothing that can start a workflow command."""
    return UNSAFE_RE.sub("?", str(text))[:limit]


def parse_image(reference: str) -> tuple[str, str]:
    """Return (name, digest) of `name@sha256:<digest>`, or raise. A tag alone is refused: it can move."""
    found = IMAGE_RE.fullmatch(reference)  # control: fullmatch, so a trailing newline is not accepted by `$`
    if not found:
        raise ScanError("the image must be a reference pinned by digest, name@sha256:<64 lowercase hex digits>")
    return found.group("name"), found.group("digest")


def parse_platform(platform: str) -> str:
    """An optional `os`, `arch` or `os/arch[/variant]` for Syft's --platform; empty means the runner's own."""
    if platform and not PLATFORM_RE.fullmatch(platform):
        raise ScanError("the platform must look like linux/arm64, arm64 or linux/arm64/v8")
    return platform


def registry_of(name: str) -> str:
    """The registry host of an image name; a first component without a dot, a colon or `localhost` is a Docker Hub namespace."""
    first = name.split("/", 1)[0]
    if "/" in name and ("." in first or ":" in first or first == "localhost"):
        return first
    return "docker.io"


def parse_date(value: object, field: str) -> datetime.date:
    if not isinstance(value, str) or not DATE_RE.match(value):
        raise ValueError(f"{field} must be a date written YYYY-MM-DD")
    try:
        return datetime.date.fromisoformat(value)
    except ValueError as error:
        raise ValueError(f"{field} is not a real date") from error


def parse_exemption(index: int, raw: object, today: datetime.date) -> Exemption:
    """One entry, or ValueError saying what is wrong. Order of checks matters for the message only."""
    if not isinstance(raw, dict):
        raise ValueError("an entry must be an object")
    unknown = sorted(set(raw) - EXEMPTION_KEYS)
    if unknown:
        raise ValueError(f"unknown key {clean(unknown[0])}")
    for key in REQUIRED_KEYS:
        value = raw.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} is missing or empty")
    for key in ("image", "approved-by"):
        if key in raw and not isinstance(raw[key], str):
            raise ValueError(f"{key} must be text")
    if len(raw["reason"]) > MAX_REASON:
        raise ValueError(f"reason is longer than {MAX_REASON} characters")
    granted = parse_date(raw["granted"], "granted")
    expires = parse_date(raw["expires"], "expires")
    if expires < today:
        raise ValueError(f"expired on {expires.isoformat()}")
    if granted > today:
        raise ValueError("granted is in the future")
    if (expires - granted).days > MAX_EXEMPTION_DAYS:
        raise ValueError(f"lasts more than {MAX_EXEMPTION_DAYS} days")
    if expires < granted:
        raise ValueError("expires before it is granted")
    return Exemption(
        index=index,
        vulnerability=raw["vulnerability"].strip().lower(),
        package=raw["package"].strip().lower(),
        image=str(raw.get("image", "")).strip(),
        reason=raw["reason"].strip(),
        granted=granted,
        expires=expires,
        approved_by=str(raw.get("approved-by", "")).strip(),
    )


def load_exemptions(path: pathlib.Path | None, today: datetime.date) -> tuple[list[Exemption], list[Finding]]:
    """The valid entries and a finding per fault. A missing file is no exemptions; a broken file or entry is a fault."""
    if path is None or not path.exists():
        return [], []
    shown = clean(path.as_posix())
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        return [], [Finding(CLAUSE_EXEMPTION, shown, 1, "error", f"the exemptions file cannot be read as JSON: {clean(error)}")]
    if not isinstance(document, dict) or set(document) != {"exemptions"} or not isinstance(document["exemptions"], list):
        return [], [Finding(CLAUSE_EXEMPTION, shown, 1, "error", 'the exemptions file must be an object holding only "exemptions", a list')]
    valid: list[Exemption] = []
    faults: list[Finding] = []
    for index, raw in enumerate(document["exemptions"], start=1):
        try:
            valid.append(parse_exemption(index, raw, today))
        except ValueError as error:
            faults.append(Finding(CLAUSE_EXEMPTION, shown, index, "error", f"exemption {index} is refused: {clean(error)}"))
    return valid, faults


def merge(first: Match, second: Match) -> Match:
    """Two reports of one finding become one that does not depend on their order: the higher
    severity, and fixable if either report has a fix, so an exemption cannot ride on the other."""
    better, other = (first, second) if SEVERITIES.index(first.severity) <= SEVERITIES.index(second.severity) else (second, first)
    return dataclasses.replace(
        better,
        ids=tuple(dict.fromkeys(better.ids + other.ids)),
        fix_versions=tuple(dict.fromkeys(better.fix_versions + other.fix_versions)),
        fix_state="fixed" if "fixed" in (first.fix_state, second.fix_state) else better.fix_state,  # control: any fix makes it fixable
    )


def load_matches(path: pathlib.Path) -> list[Match]:
    """Read the Grype JSON strictly. One match per (vulnerability, package, version, type), at its highest severity."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ScanError(f"the Grype report cannot be read: {clean(error)}") from error
    if not isinstance(document, dict) or not isinstance(document.get("matches"), list):
        raise ScanError("the Grype report has no matches list")
    seen: dict[tuple[str, str, str, str], Match] = {}
    for raw in document["matches"]:
        try:
            vulnerability = raw["vulnerability"]
            artifact = raw["artifact"]
            ids = [vulnerability["id"]]
            ids += [related["id"] for related in raw.get("relatedVulnerabilities") or []]
            fix = vulnerability.get("fix") or {}
            locations = artifact.get("locations") or []
            raw_severity = vulnerability["severity"]
            if not isinstance(raw_severity, str) or raw_severity not in GRYPE_SEVERITIES:  # control: only a severity Grype emits is judged
                raise ScanError("a match in the Grype report has a severity Grype does not emit")
            match = Match(
                ids=tuple(str(item).lower() for item in ids),
                severity=GRYPE_SEVERITIES[raw_severity],
                package=str(artifact["name"]).lower(),
                version=str(artifact.get("version", "")),
                kind=str(artifact.get("type", "")),
                path=str(locations[0].get("path", "")) if locations and isinstance(locations[0], dict) else "",
                fix_versions=tuple(str(item) for item in fix.get("versions") or []),
                fix_state=str(fix.get("state", "unknown")).lower(),
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ScanError("a match in the Grype report does not have the expected shape") from error
        key = (match.vulnerability, match.package, match.version, match.kind)
        kept = seen.get(key)
        seen[key] = match if kept is None else merge(kept, match)
    return list(seen.values())


def count_packages(path: pathlib.Path) -> int:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
        artifacts = document["artifacts"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ScanError(f"the SBOM cannot be read: {clean(error)}") from error
    if not isinstance(artifacts, list):
        raise ScanError("the SBOM has no artifacts list")
    return len(artifacts)


def covers(exemption: Exemption, match: Match, image: str) -> bool:
    if exemption.package != match.package or exemption.vulnerability not in match.ids:
        return False
    return not exemption.image or exemption.image == image


def evaluate(matches: list[Match], exemptions: list[Exemption], faults: list[Finding], image: str, exemptions_file: str, packages: int, allow_empty: bool = False) -> Result:
    counts = dict.fromkeys(SEVERITIES, 0)
    findings = list(faults)
    blocking = 0
    exempted = 0
    ignored: set[int] = set()
    used: set[int] = set()
    for match in sorted(matches, key=lambda m: (SEVERITIES.index(m.severity), m.vulnerability, m.package)):
        counts[match.severity] += 1
        if match.severity not in BLOCKING:
            continue
        subject = f"{clean(match.vulnerability)} ({match.severity}) in {clean(match.package)} {clean(match.version)}"
        where = clean(match.path) or clean(image)
        granted_by = None
        for exemption in exemptions:
            if not covers(exemption, match, image):
                continue
            if match.has_fix:  # control: an exemption is for a finding with no fix
                if exemption.index not in ignored:
                    ignored.add(exemption.index)
                    findings.append(Finding(CLAUSE_EXEMPTION, clean(exemptions_file), exemption.index, "warning", f"exemption {exemption.index} for {clean(match.vulnerability)} is ignored: a fix exists, so update instead"))
                continue
            if match.severity == "critical" and not exemption.approved_by:  # control: a critical exemption needs the approval named
                if exemption.index not in ignored:
                    ignored.add(exemption.index)
                    findings.append(Finding(CLAUSE_EXEMPTION, clean(exemptions_file), exemption.index, "error", f"exemption {exemption.index} for critical {clean(match.vulnerability)} names no approved-by"))
                continue
            granted_by = exemption
            break
        if granted_by is not None:
            used.add(granted_by.index)
            exempted += 1
            findings.append(Finding(CLAUSE_VULN, where, 1, "exempt", f"{subject} is exempt until {granted_by.expires.isoformat()}"))
            continue
        blocking += 1
        remedy = f"fixed in {clean(', '.join(match.fix_versions))}" if match.fix_versions else (
            "a fix exists" if match.has_fix else "no fix exists: record an exemption (SEC-5) or remove the package"
        )
        findings.append(Finding(CLAUSE_VULN, where, 1, "error", f"{subject}: {remedy}"))
    for exemption in exemptions:
        if exemption.index not in used and exemption.index not in ignored:
            findings.append(Finding(CLAUSE_EXEMPTION, clean(exemptions_file), exemption.index, "info", f"exemption {exemption.index} for {clean(exemption.vulnerability)} covers no finding in this image"))
    if packages == 0:
        level = "warning" if allow_empty else "error"  # control: an empty catalogue is nothing judged, not a pass
        findings.append(Finding(CLAUSE_SBOM, clean(image), 1, level, "the SBOM lists no packages, so the scan had nothing to judge"))
    return Result(counts=counts, blocking=blocking, exempted=exempted, packages=packages, findings=findings)


def render_summary(result: Result, image: str, report_only: bool) -> str:
    mode = " (report only: the job does not fail on findings)" if report_only else ""
    lines = [f"### Image scan: {result.verdict}{mode}", "", f"Image: `{clean(image)}`", "", "| Severity | Findings |", "| --- | --- |"]
    lines += [f"| {name} | {result.counts[name]} |" for name in SEVERITIES]
    lines += ["", f"Packages in the SBOM: {result.packages}. Blocking (critical or high, not exempt): {result.blocking}. Exempt: {result.exempted}.", ""]
    listed = [f for f in result.findings if f.level in ("error", "warning")]
    for finding in listed[:MAX_LISTED]:
        lines.append(f"- {finding.level} {finding.clause}: {finding.message}")
    if len(listed) > MAX_LISTED:
        lines.append(f"- and {len(listed) - MAX_LISTED} more in the findings file")
    return "\n".join(lines) + "\n"


def write_outputs(path: str, result: Result) -> None:
    pairs = {"verdict": result.verdict, "blocking": result.blocking, "exempted": result.exempted, "packages": result.packages}
    pairs.update(result.counts)
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            handle.write(f"{key}={value}\n")


def safe_relative(raw: str, root: pathlib.Path) -> pathlib.Path:
    """A path inside the workspace: not absolute, no `..`, and not a link that leaves it."""
    candidate = pathlib.PurePosixPath(raw)
    if not raw or candidate.is_absolute() or ".." in candidate.parts:
        raise ScanError("the exemptions path must be relative and stay inside the repository")
    resolved = (root / raw).resolve()
    if root.resolve() not in resolved.parents:
        raise ScanError("the exemptions path leaves the repository")
    return resolved


def cmd_check_input(args: argparse.Namespace) -> int:
    name, digest = parse_image(args.image)
    parse_platform(args.platform)
    registry = registry_of(name)
    print(f"image={name}@sha256:{digest}")
    print(f"registry={registry}")
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"name={name}\nregistry={registry}\ndigest={digest}\nartifact=image-scan-{digest[:12]}\n")
    return 0


def read_image_list(text: str) -> list[str]:
    """Image references one per line; `#` starts a comment. Each must be pinned by digest."""
    images: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        try:
            parse_image(entry)
        except ScanError as error:
            raise ScanError(f"line {number}: {error}") from error
        if entry not in images:
            images.append(entry)
    if not images:
        raise ScanError("the image list names no image")
    return images


def cmd_list_images(args: argparse.Namespace) -> int:
    try:
        text = pathlib.Path(args.file).read_text(encoding="utf-8") if args.file else args.text
    except OSError as error:
        raise ScanError(f"the image list cannot be read: {clean(error)}") from error
    images = read_image_list(text)
    print("\n".join(images))
    if args.github_output:
        with open(args.github_output, "a", encoding="utf-8") as handle:
            handle.write(f"images={json.dumps(images, separators=(',', ':'))}\n")
    return 0


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:  # scheme and host are checked by the caller
        return response.read()


def install(pins_path: pathlib.Path, tool: str, bin_dir: pathlib.Path, system: str, machine: str) -> pathlib.Path:
    try:
        pins = json.loads(pins_path.read_text(encoding="utf-8"))
        pin = pins[tool][f"{system}-{machine}"]
        url, sha256 = pin["url"], pin["sha256"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise ScanError(f"no pin for {clean(tool)} on {clean(system)}-{clean(machine)}") from error
    parts = urllib.parse.urlparse(url)
    if parts.scheme != "https" or parts.hostname not in INSTALL_HOSTS:
        raise ScanError("a pinned download must be an https URL on an allowed host")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ScanError("a pinned checksum must be 64 lowercase hex digits")
    data = fetch(url)
    actual = hashlib.sha256(data).hexdigest()
    if actual != sha256:
        raise ScanError(f"the {clean(tool)} download does not match its pinned checksum (got {actual})")
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        member = next((m for m in archive.getmembers() if m.name == tool and m.isfile()), None)
        if member is None:
            raise ScanError(f"the {clean(tool)} archive holds no file named {clean(tool)}")
        content = archive.extractfile(member)
        if content is None:
            raise ScanError(f"the {clean(tool)} archive member cannot be read")
        bin_dir.mkdir(parents=True, exist_ok=True)
        target = bin_dir / tool
        target.write_bytes(content.read())
    target.chmod(0o755)
    return target


def cmd_install(args: argparse.Namespace) -> int:
    machine = {"x86_64": "amd64", "amd64": "amd64"}.get(platform.machine().lower(), platform.machine().lower())
    target = install(pathlib.Path(args.pins), args.tool, pathlib.Path(args.bin_dir), platform.system().lower(), machine)
    print(f"installed {args.tool} at {target}")
    if args.github_path:
        with open(args.github_path, "a", encoding="utf-8") as handle:
            handle.write(f"{pathlib.Path(args.bin_dir).resolve()}\n")
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    today = datetime.date.fromisoformat(args.today) if args.today else datetime.datetime.now(datetime.UTC).date()
    root = pathlib.Path(args.root)
    exemptions_path = safe_relative(args.exemptions, root) if args.exemptions else None
    exemptions, faults = load_exemptions(exemptions_path, today)
    result = evaluate(
        load_matches(pathlib.Path(args.grype_json)),
        exemptions,
        faults,
        args.image,
        args.exemptions or "",
        count_packages(pathlib.Path(args.sbom)),
        args.allow_empty_sbom,
    )
    for finding in result.findings:
        print(finding.as_json())
    errors = [f for f in result.findings if f.level == "error"]
    for finding in errors[:MAX_ANNOTATIONS]:
        print(f"::error title={finding.clause}::{finding.message}")
    if args.findings_out:
        pathlib.Path(args.findings_out).write_text("".join(f.as_json() + "\n" for f in result.findings), encoding="utf-8")
    counted = " ".join(f"{name}={result.counts[name]}" for name in SEVERITIES)
    totals = f"image={clean(args.image)} {counted} blocking={result.blocking} exempted={result.exempted} packages={result.packages}"
    print(f"counts {totals}")
    print(f"::notice title=image-scan counts::{totals}")
    summary = render_summary(result, args.image, args.report_only)
    if args.github_summary:
        with open(args.github_summary, "a", encoding="utf-8") as handle:
            handle.write(summary)
    else:
        print(summary)
    if args.github_output:
        write_outputs(args.github_output, result)
    if result.failed and not args.report_only:
        print(f"::error title={CLAUSE_VULN}::{result.blocking} critical or high finding(s) block this image")
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-input")
    check.add_argument("--image", required=True)
    check.add_argument("--platform", default="")
    check.add_argument("--github-output")
    check.set_defaults(run=cmd_check_input)
    fetch_tool = commands.add_parser("install")
    fetch_tool.add_argument("--tool", required=True, choices=("grype", "syft"))
    fetch_tool.add_argument("--pins", required=True)
    fetch_tool.add_argument("--bin-dir", required=True)
    fetch_tool.add_argument("--github-path")
    fetch_tool.set_defaults(run=cmd_install)
    listing = commands.add_parser("list-images", help="read the image list of the manual report")
    source = listing.add_mutually_exclusive_group(required=True)
    source.add_argument("--file")
    source.add_argument("--text")
    listing.add_argument("--github-output")
    listing.set_defaults(run=cmd_list_images)
    judge = commands.add_parser("evaluate")
    judge.add_argument("--grype-json", required=True)
    judge.add_argument("--sbom", required=True)
    judge.add_argument("--image", required=True, help="the name shown in findings and matched by an exemption's image key")
    judge.add_argument("--exemptions", help="path inside --root; absent file means no exemptions")
    judge.add_argument("--root", default=".")
    judge.add_argument("--report-only", action="store_true", help="count and report but exit 0 on findings; used by the manual report only")
    judge.add_argument("--allow-empty-sbom", action="store_true", help="an SBOM with no packages warns instead of failing")
    judge.add_argument("--today", help="YYYY-MM-DD; tests only")
    judge.add_argument("--findings-out")
    judge.add_argument("--github-output")
    judge.add_argument("--github-summary")
    judge.set_defaults(run=cmd_evaluate)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except ScanError as error:
        print(f"::error title={CLAUSE_SCAN}::{error}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
