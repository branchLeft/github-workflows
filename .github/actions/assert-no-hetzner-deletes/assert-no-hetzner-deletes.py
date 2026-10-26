#!/usr/bin/env python3
"""Fail if a `pulumi preview --json` plan would destroy a protected Hetzner resource.
Usage:
    assert-no-hetzner-deletes.py <preview-json-file>
    assert-no-hetzner-deletes.py --self-test
    assert-no-hetzner-deletes.py --verify-coverage <program-dir> --coverage-map <json-file>
Exit 0 if clean, 1 if a protected resource would be destroyed or any input could
not be read or understood, 2 on usage error. Rules, limits and the coverage map
format: assert-no-hetzner-deletes.md beside this file.
"""

import contextlib
import io
import json
import pathlib
import re
import sys

# Every instance of these types is protected, in every Hetzner stack. The
# comment on each is what deleting one costs.
PROTECTED_TYPES = {
    # A host's loss is an outage: a mail host's sending reputation rebuilds in
    # months, an edge host terminates public traffic, a database host holds
    # every tenant's data.
    "hcloud:index/server:Server",
    # Replacing the network detaches every attached server in one operation,
    # and a network cannot be moved or resized in place.
    "hcloud:index/network:Network",
    # The subnet type exposes no `deleteProtection` input, so Pulumi's
    # `protect: true` and this gate are the only things standing in the way.
    "hcloud:index/networkSubnet:NetworkSubnet",
    # Deleting a firewall leaves its host open to the internet with nothing
    # else noticing.
    "hcloud:index/firewall:Firewall",
    # A released primary IP is a DNS change at the registrar before anything
    # serves (or delivers mail) again. A program may not declare one directly
    # today, but an import or a refactor can surface them as first-class
    # resources at any time, and that is not the day anyone re-reads this list.
    "hcloud:index/primaryIp:PrimaryIp",
}

# Any Pulumi step op containing either word destroys, or schedules the
# destruction of, the resource it names. Covers `delete`, `delete-replaced`,
# `replace`, `create-replacement`, `read-replacement`, `import-replacement`,
# `discard-replaced` and `remove-pending-replace` without enumerating them, so
# a future op name has to actively avoid both words to slip through.
DESTRUCTIVE_SUBSTRINGS = ("delete", "replace")


def _split_urn(urn: str) -> tuple[str, str]:
    """Return (type token, logical name) from a Pulumi URN.

    A URN is `urn:pulumi:<stack>::<project>::<type>::<name>`, and `<type>` is
    itself `::`-free while a parent chain would appear as `<parent>$<type>`.
    Splitting from the right is therefore correct for both shapes -- and the
    parent-chain shape is the normal one where a `Host` component parents its
    server, firewall and primary IPs.
    """
    parts = urn.rsplit("::", 2)
    if len(parts) != 3:
        raise ValueError(f"not a resource URN: {urn!r}")
    return parts[1].rsplit("$", 1)[-1], parts[2]


def destructive_steps(plan: dict) -> list[tuple[str, str]]:
    """Return (name, op) for every protected resource this plan would destroy.

    Raises ValueError if `plan` is not a preview plan, rather than returning
    an empty list -- "I could not find any steps" and "there are no
    destructive steps" must not share an exit code.
    """
    if not isinstance(plan, dict):
        raise ValueError("the preview JSON is not an object")
    steps = plan.get("steps")
    if not isinstance(steps, list):
        raise ValueError("no 'steps' array in the preview JSON")

    found: list[tuple[str, str]] = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"malformed step entry: {step!r}")
        op = step.get("op")
        urn = step.get("urn")
        if not isinstance(op, str) or not isinstance(urn, str):
            raise ValueError(f"step missing op/urn: {step!r}")
        if not any(word in op for word in DESTRUCTIVE_SUBSTRINGS):
            continue
        type_token, name = _split_urn(urn)
        if type_token in PROTECTED_TYPES:
            found.append((name, op))
    return sorted(found)


def load_coverage_map(path: str) -> dict[str, set[str]]:
    """Read the caller's coverage map: program dir basename -> constructors.

    Raises ValueError for anything but an object of non-empty lists of
    non-empty strings. An empty list is refused, not read as "nothing to
    check": it would let a directory pass `--verify-coverage` having been
    checked for nothing.
    """
    try:
        with open(path, encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read the coverage map {path!r}: {error}") from error
    if not isinstance(raw, dict) or not raw:
        raise ValueError("the coverage map must be a non-empty JSON object")
    coverage: dict[str, set[str]] = {}
    for directory, constructors in raw.items():
        if not directory or not isinstance(constructors, list) or not constructors:
            raise ValueError(f"{directory!r}: expected a non-empty list of constructors")
        if not all(isinstance(ctor, str) and ctor for ctor in constructors):
            raise ValueError(f"{directory!r}: every constructor must be a non-empty string")
        coverage[directory] = set(constructors)
    return coverage


def _declaration_pattern(constructor: str) -> re.Pattern[str]:
    """Match a resource *declaration*, not a mention of the same text.

    A bare substring search reports a false pass: a constructor that has been
    refactored away usually survives elsewhere as a comment or an import.
    """
    return re.compile(rf"\bnew\s+{re.escape(constructor)}\s*\(")


def verify_coverage(program_dir: str, coverage_map: dict[str, set[str]]) -> int:
    """Fail if a directory's expected constructors are no longer declared.

    The quiet way this gate dies: a resource migrates out of the program (into
    a package, into another project) while the plan check keeps passing on
    whatever is left. Nothing errors, and the type the map promised this
    directory declares is no longer anything the repository reviews. Failing
    here forces the map -- and therefore the guard's actual coverage -- to be
    updated in the same change that moves the resource.
    """
    directory = pathlib.Path(program_dir)
    if not directory.is_dir():
        print(f"::error::not a directory: {program_dir}")
        return 1

    expected = coverage_map.get(directory.resolve().name)
    if expected is None:
        print(
            f"::error::no coverage expectations recorded for {program_dir!r} "
            f"(known: {', '.join(sorted(coverage_map))}). A program dir the "
            "coverage map has never heard of must be added to it, not waved "
            "through."
        )
        return 1

    sources = sorted(directory.glob("*.ts"))
    if not sources:
        print(f"::error::no .ts files found in {program_dir}; nothing was checked")
        return 1

    blob = "\n".join(path.read_text(encoding="utf-8") for path in sources)

    missing = [
        ctor for ctor in sorted(expected) if not _declaration_pattern(ctor).search(blob)
    ]
    if missing:
        print(
            f"::error::these constructors are expected in {program_dir}/*.ts but "
            f"no longer appear: {', '.join(missing)}. Either the resource moved "
            "and the coverage map was not updated -- in which case the guard's "
            "coverage is stale -- or it was removed on purpose and belongs "
            "out of the map in the same change. A mention in a comment or an "
            "import does not count."
        )
        return 1

    print(
        f"OK: all {len(expected)} expected constructors still declared across "
        f"{len(sources)} program files in {program_dir}."
    )
    return 0


_STACK = "urn:pulumi:production::example-mail::"
_URN_MAIL = f"{_STACK}hcloud:index/server:Server::mail-server"
_URN_FIREWALL = f"{_STACK}hcloud:index/firewall:Firewall::mail-firewall"
_NET_STACK = "urn:pulumi:production::example-network::"
_URN_NETWORK = f"{_NET_STACK}hcloud:index/network:Network::platform"
_URN_SUBNET = f"{_NET_STACK}hcloud:index/networkSubnet:NetworkSubnet::platform-subnet"
_URN_ROUTE = f"{_NET_STACK}hcloud:index/networkRoute:NetworkRoute::platform-egress"
# A component's children carry the parent chain in their type segment, which
# the URN splitter has to see through.
_HOSTS_STACK = "urn:pulumi:production::example-hosts::"
_HOST = f"{_HOSTS_STACK}example:hetzner:Host$hcloud:index/"
_URN_APP = f"{_HOST}server:Server::app-server"
_URN_DB = f"{_HOST}server:Server::db-server"
_URN_APP_FW = f"{_HOST}firewall:Firewall::app-firewall"
_URN_APP_IPV4 = f"{_HOST}primaryIp:PrimaryIp::app-ipv4"
_URN_ATTACH = f"{_HOST}serverNetwork:ServerNetwork::app-network"


def _plan(*steps: tuple[str, str]) -> dict:
    return {"steps": [{"op": op, "urn": urn} for op, urn in steps]}


def _quarantine(fn, *args):
    """Run fn with stdout captured, returning its result and what it printed.

    `verify_coverage` emits `::error::`-prefixed lines on the failure fixtures
    by design, and that prefix is a GitHub Actions annotation trigger. Letting
    it through would flag a fully passing self-test run as an error in the
    Actions UI, indistinguishable from a real one.
    """
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        result = fn(*args)
    return result, buffer.getvalue()


def _print_quarantined(output: str, ok: bool) -> None:
    label = (
        "fixture output, expected -- not a gate failure"
        if ok
        else "fixture output -- this case FAILED, inspect"
    )
    lines = output.rstrip("\n").splitlines()
    if not lines:
        if not ok:
            print(f"    ({label}, but nothing was captured -- inspect verify_coverage)")
        return
    for line in lines:
        print(f"    {label}: {line}")


_SAMPLE_COVERAGE_MAP = {
    "mail": {"hcloud.Server", "hcloud.Firewall"},
    "hetzner": {"hcloud.Network", "hcloud.NetworkSubnet", "Host"},
    "hosts": {"Host"},
}
_MAIL_SOURCE = (
    "export const mail = new hcloud.Server('mail-server', {});\n"
    "export const fw = new hcloud.Firewall('mail-firewall', {});\n"
)
_HETZNER_SOURCE = (
    "export const network = new hcloud.Network('platform', {});\n"
    "export const subnet = new hcloud.NetworkSubnet('platform-subnet', {});\n"
    "export const edge = new Host({ name: 'edge' });\n"
)
_HOSTS_SOURCE = (
    "export const app = new Host({ name: 'app' });\n"
    "export const db = new Host({ name: 'db' });\n"
)


def _coverage_self_test() -> int:
    """Prove `--verify-coverage` still tells a declaration from a mention."""
    import tempfile

    refactored = _MAIL_SOURCE.replace("new hcloud.Server(", "new somethingElse(", 1) + (
        # The old constructor survives as a comment and an import -- the exact
        # shape that made a substring check report a clean pass.
        "\n// was new hcloud.Server before the refactor\n"
        "import * as hcloud from '@pulumi/hcloud';\n"
    )
    hosts_refactored = _HOSTS_SOURCE.replace("new Host(", "new somethingElse(", 2) + (
        "\n// was new Host before the refactor\n"
        "import { Host } from '@example/hetzner-host';\n"
    )

    cases = [
        ("mail", "mail, everything declared", _MAIL_SOURCE, 0),
        ("hetzner", "hetzner, everything declared", _HETZNER_SOURCE, 0),
        ("hosts", "hosts, everything declared", _HOSTS_SOURCE, 0),
        ("mail", "mail, server refactored away but still mentioned", refactored, 1),
        ("hetzner", "hetzner, Host no longer constructed", _HETZNER_SOURCE.replace("new Host(", "new Ghost(", 1), 1),
        ("hosts", "hosts, Host no longer constructed", hosts_refactored, 1),
    ]

    failed = False
    with tempfile.TemporaryDirectory() as root:
        for dirname, name, source, expected in cases:
            directory = pathlib.Path(root) / name.replace(" ", "_").replace(",", "") / dirname
            directory.mkdir(parents=True)
            (directory / "program.ts").write_text(source, encoding="utf-8")
            actual, output = _quarantine(verify_coverage, str(directory), _SAMPLE_COVERAGE_MAP)
            ok = actual == expected
            failed |= not ok
            print(f"{'PASS' if ok else 'FAIL'}: coverage, {name} -> exit {actual} (expected {expected})")
            _print_quarantined(output, ok)

        unknown = pathlib.Path(root) / "some-new-project"
        unknown.mkdir()
        (unknown / "program.ts").write_text(_MAIL_SOURCE, encoding="utf-8")
        actual, output = _quarantine(verify_coverage, str(unknown), _SAMPLE_COVERAGE_MAP)
        ok = actual == 1
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: coverage, unknown program dir -> exit {actual} (expected 1)")
        _print_quarantined(output, ok)

        empty = pathlib.Path(root) / "mail"
        empty.mkdir()
        actual, output = _quarantine(verify_coverage, str(empty), _SAMPLE_COVERAGE_MAP)
        ok = actual == 1
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: coverage, no .ts files -> exit {actual} (expected 1)")
        _print_quarantined(output, ok)

    return 1 if failed else 0


def self_test() -> int:
    cases: list[tuple[str, dict, list[tuple[str, str]]]] = [
        ("clean create-only plan", _plan(("create", _URN_MAIL), ("create", _URN_DB)), []),
        ("no-op plan", _plan(("same", _URN_MAIL), ("same", _URN_FIREWALL)), []),
        ("updating the firewall's rules is not a destroy", _plan(("update", _URN_FIREWALL)), []),
        ("outright delete", _plan(("delete", _URN_MAIL)), [("mail-server", "delete")]),
        (
            "replacement, which a regex gate keyed on '(delete)' would miss",
            _plan(("replace", _URN_NETWORK)),
            [("platform", "replace")],
        ),
        (
            "the create half of a replacement still counts",
            _plan(("create-replacement", _URN_SUBNET)),
            [("platform-subnet", "create-replacement")],
        ),
        (
            "a component's child server is caught through the parent chain",
            _plan(("delete", _URN_APP)),
            [("app-server", "delete")],
        ),
        (
            "a component's child primary IP is caught through the parent chain",
            _plan(("create-replacement", _URN_APP_IPV4)),
            [("app-ipv4", "create-replacement")],
        ),
        (
            "the egress route is deliberately deletable",
            _plan(("delete", _URN_ROUTE)),
            [],
        ),
        (
            "a network attachment is deliberately deletable",
            _plan(("delete", _URN_ATTACH)),
            [],
        ),
        (
            "one protected delete hidden among permitted churn",
            _plan(
                ("create", _URN_ATTACH),
                ("delete", _URN_ATTACH),
                ("update", _URN_APP),
                ("delete", _URN_APP_FW),
            ),
            [("app-firewall", "delete")],
        ),
        ("empty plan", _plan(), []),
    ]

    failed = False
    for name, plan, expected in cases:
        actual = destructive_steps(plan)
        ok = actual == expected
        failed |= not ok
        print(f"{'PASS' if ok else 'FAIL'}: {name} -> {actual!r} (expected {expected!r})")

    for name, bad in [
        ("plan with no steps key", {}),
        ("steps is not a list", {"steps": "nope"}),
        ("step missing urn", {"steps": [{"op": "delete"}]}),
        ("urn is not a resource urn", {"steps": [{"op": "delete", "urn": "urn:pulumi:x"}]}),
    ]:
        try:
            destructive_steps(bad)
        except ValueError:
            print(f"PASS: {name} raises rather than reporting clean")
        else:
            failed = True
            print(f"FAIL: {name} returned clean instead of raising")

    failed |= _coverage_self_test() != 0

    if failed:
        print("\nThis gate no longer behaves as written. It would report success")
        print("against a plan that destroys a Hetzner production resource.")
    return 1 if failed else 0


def _coverage_arguments(args: list[str]) -> tuple[str, str] | None:
    """Return (program dir, map file) for `--verify-coverage D --coverage-map F`
    in either order, or None if the arguments are anything else."""
    if len(args) != 4:
        return None
    flags = {args[0]: args[1], args[2]: args[3]}
    if set(flags) != {"--verify-coverage", "--coverage-map"}:
        return None
    return flags["--verify-coverage"], flags["--coverage-map"]


def main(argv: list[str]) -> int:
    args = argv[1:]
    if args == ["--self-test"]:
        return self_test()
    if args and args[0].startswith("--"):
        arguments = _coverage_arguments(args)
        if arguments is None:
            print(__doc__)
            return 2
        try:
            coverage_map = load_coverage_map(arguments[1])
        except ValueError as error:
            print(f"::error::{error}")
            return 1
        return verify_coverage(arguments[0], coverage_map)
    if len(args) != 1:
        print(__doc__)
        return 2

    try:
        with open(args[0], encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        # Must fail. Treating an unreadable plan as an empty one would report
        # "nothing will be destroyed" without having looked at anything.
        print(f"::error::cannot read preview JSON: {error}")
        return 1

    try:
        found = destructive_steps(plan)
    except ValueError as error:
        print(f"::error::preview JSON is not a plan this gate understands: {error}")
        return 1

    if found:
        detail = ", ".join(f"{name} ({op})" for name, op in found)
        print(
            f"::error::this apply would DESTROY Hetzner production infrastructure: {detail}. "
            "Nothing has been applied. If the destruction is genuinely intended it "
            "is a gated migration run by the platform owner, not a merge."
        )
        return 1

    steps = plan.get("steps", [])
    summary = plan.get("changeSummary", {})
    print(
        "OK: no protected Hetzner resource is destroyed by this plan "
        f"({len(steps)} steps checked, changeSummary={summary})."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
