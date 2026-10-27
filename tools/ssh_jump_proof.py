"""Proof, on real sshd, that the ssh-check-hosts jump hop works and its lock holds.
Gateway sshd on 127.0.0.2, target on 127.0.0.3, both port 22 (the action's inputs carry no port).
Runs the action's validate, check and report steps as written, with real ssh, and asserts both logs.
Needs root: re-runs itself under sudo, and removes every process, user and file it made on any exit.
Exit: 0 every assertion held, 1 one failed, 2 setup failed. Run: python3 tools/ssh_jump_proof.py
"""

from __future__ import annotations

import os
import pwd
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

import yaml

import run_workflow_steps

REPO = Path(__file__).resolve().parent.parent
ACTION = REPO / ".github/actions/ssh-check-hosts/action.yml"

GATEWAY_ADDRESS = "127.0.0.2"
TARGET_ADDRESS = "127.0.0.3"
OTHER_ADDRESS = "127.0.0.4"
SSH_PORT = 22
OTHER_PORT = 2222
PROOF_SHA = "0123456789abcdef0123456789abcdef01234567"

JUMP_USER = "proofjump"
FORCED_ONLY_USER = "proofforced"
SHELL_ONLY_USER = "proofnologin"
CONTROL_USER = "proofopen"
DEPLOY_USER = "proofdeploy"
PRIVILEGE_SEPARATION_DIRECTORY = Path("/run/sshd")
NO_SHELL = "/usr/sbin/nologin"
LOGIN_SHELL = "/bin/sh"
FORCED_COMMAND = NO_SHELL

STEP_IDS = ("validate", "check", "report")
LOCKED_PLAN_FILES = ("id", "known_hosts", "jump_id", "jump_config")
REFUSAL = "administratively prohibited"

TARGET_COMMAND = (
    "#!/bin/sh\n"
    "printf 'pull-trigger verdict=unchanged exit=0 reason=none mode=check "
    'commit=%s playbook=site.yml changed=0\\n\' "$SSH_ORIGINAL_COMMAND"\n'
)
CALL_END = "=== end of call ==="
SSH_SHIM = (
    "#!/bin/sh\n"
    'printf \'%s\\n\' "$@" >> "$SSH_ARGV_LOG"\n'
    f"printf '%s\\n' '{CALL_END}' >> \"$SSH_ARGV_LOG\"\n"
    'exec {real} "$@"\n'
)

ACCEPTED_LINE = re.compile(
    r"Accepted publickey for (?P<user>\S+) from (?P<source>\S+) port \d+ ssh2: \S+ (?P<fingerprint>SHA256:\S+)"
)
OFFERED_LINE = re.compile(r"userauth_pubkey: publickey test pkalg \S+ pkblob \S+ (?P<fingerprint>SHA256:\S+)")
REQUESTED_LINE = re.compile(
    r"server_request_direct_tcpip: originator \S+ port \d+, target (?P<host>\S+) port (?P<port>\d+)"
)
DENIED_LINE = re.compile(r"to connect to host (?P<host>\S+) port (?P<port>\d+), but the request was denied")
CONNECTION_LINE = re.compile(r"^Connection from ", re.MULTILINE)
ADDRESS_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.-]*$")
PUBLIC_KEY_PATTERN = re.compile(r"^[a-z0-9@.-]+ [A-Za-z0-9+/=]+$")


class ProofError(Exception):
    """Base of every error this script raises."""


class SetupError(ProofError):
    """The proof could not be set up: a tool, a port, a user or a server is not as it must be."""


class InvalidLineInput(ProofError):
    """A value for an authorized_keys line is not the plain host, port or key it must be."""


class ActionFormatError(ProofError):
    """The action file is not shaped as this proof reads it."""


class Endpoint(NamedTuple):
    host: str
    port: int


class Accepted(NamedTuple):
    user: str
    source: str
    fingerprint: str


class CommandResult(NamedTuple):
    exit_status: int
    stdout: str
    stderr: str


class ActionStep(NamedTuple):
    step_id: str
    script: str
    environment: dict[str, str]


@dataclass(frozen=True)
class KeyPair:
    private_path: Path
    public_key: str
    fingerprint: str

    def private_text(self) -> str:
        return self.private_path.read_text(encoding="utf-8").rstrip("\n")

    def known_hosts_line(self, address: str) -> str:
        return f"{address} {self.public_key}"


@dataclass(frozen=True)
class Server:
    address: str
    log_path: Path


@dataclass(frozen=True)
class GatewayAccount:
    name: str
    shell: str
    has_forced_command: bool
    runs_commands: bool
    description: str


@dataclass(frozen=True)
class Fixture:
    directory: Path
    shim_directory: Path
    argv_log: Path
    marker_directory: Path
    gateway: Server
    target: Server
    jump_key: KeyPair
    check_key: KeyPair
    gateway_host_key: KeyPair
    target_host_key: KeyPair
    wrong_host_key: KeyPair


@dataclass(frozen=True)
class ActionRun:
    steps: list[tuple[str, int]]
    log: str
    outputs: dict[str, str]
    summary: str
    plan_directory: Path
    ssh_calls: list[list[str]]

    def status_of(self, step_id: str) -> int | None:
        return dict(self.steps).get(step_id)

    def every_step_succeeded(self) -> bool:
        return [status for _, status in self.steps] == [0] * len(STEP_IDS)


class Report:
    """Prints each observation and each assertion, and remembers the assertions that failed."""

    def __init__(self) -> None:
        self.passed = 0
        self.failures: list[str] = []

    def section(self, title: str) -> None:
        print(f"\n== {title}", flush=True)

    def observe(self, text: str) -> None:
        first, *rest = text.splitlines() or [""]
        print(f"OBSERVED: {first}", flush=True)
        for line in rest:
            print(f"    {line}", flush=True)

    def assert_that(self, condition: bool, description: str) -> None:
        if condition:
            self.passed += 1
            print(f"ASSERT PASS: {description}", flush=True)
        else:
            self.failures.append(description)
            print(f"ASSERT FAIL: {description}", flush=True)


GATEWAY_ACCOUNTS = (
    GatewayAccount(JUMP_USER, NO_SHELL, True, False, "documented account: no shell and a forced command"),
    GatewayAccount(FORCED_ONLY_USER, LOGIN_SHELL, True, False, "login shell, forced command only"),
    GatewayAccount(SHELL_ONLY_USER, NO_SHELL, False, False, "no shell, no forced command"),
    GatewayAccount(CONTROL_USER, LOGIN_SHELL, False, True, "control: login shell, no forced command"),
)


def gateway_authorized_keys_line(
    target_address: str, port: int, public_key: str, forced_command: str | None = FORCED_COMMAND
) -> str:
    """Return the authorized_keys line of the jump key on the gateway: one allowed destination, no command.

    Raises:
        InvalidLineInput: the address is not a plain host name or address, the port is not 1 to 65535,
            or the public key is not one "type base64" pair.
    """
    if not ADDRESS_PATTERN.fullmatch(target_address):
        raise InvalidLineInput("the permitted address must be one plain host name or address, with no wildcard")
    if not 1 <= port <= 65535:
        raise InvalidLineInput("the permitted port must be from 1 to 65535")
    if not PUBLIC_KEY_PATTERN.fullmatch(public_key):
        raise InvalidLineInput("the public key must be one 'type base64' pair")
    options = ["restrict", "port-forwarding", f'permitopen="{target_address}:{port}"']
    if forced_command is not None:
        options.append(f'command="{forced_command}"')
    return f"{','.join(options)} {public_key}"


def target_authorized_keys_line(command_path: Path, public_key: str) -> str:
    """Return the authorized_keys line of the check key on the target: the one forced command."""
    return f'restrict,command="{command_path}" {public_key}'


def accepted_logins(log: str) -> list[Accepted]:
    """Return every public-key login the sshd log records, in order."""
    return [Accepted(m["user"], m["source"], m["fingerprint"]) for m in ACCEPTED_LINE.finditer(log)]


def offered_fingerprints(log: str) -> frozenset[str]:
    """Return the fingerprint of every key the client offered, whether or not it was accepted."""
    return frozenset(m["fingerprint"] for m in OFFERED_LINE.finditer(log))


def requested_forwards(log: str) -> list[Endpoint]:
    """Return every destination a client asked the gateway to open, in order."""
    return [Endpoint(m["host"], int(m["port"])) for m in REQUESTED_LINE.finditer(log)]


def denied_forwards(log: str) -> list[Endpoint]:
    """Return every destination the gateway refused to open."""
    return [Endpoint(m["host"], int(m["port"])) for m in DENIED_LINE.finditer(log)]


def permitted_forwards(log: str) -> list[Endpoint]:
    """Return every destination the gateway was asked for and did not refuse."""
    remaining = Counter(requested_forwards(log))
    remaining.subtract(Counter(denied_forwards(log)))
    return sorted(remaining.elements())


def count_connections(log: str) -> int:
    """Return how many connections the sshd log records."""
    return len(CONNECTION_LINE.findall(log))


def parse_outputs(text: str) -> dict[str, str]:
    """Return the name=value lines of a GITHUB_OUTPUT file."""
    pairs = (line.partition("=") for line in text.splitlines())
    return {name: value for name, separator, value in pairs if separator}


def parse_ssh_calls(text: str) -> list[list[str]]:
    """Return each ssh call the shim recorded: one argument per line, a marker line ending the call."""
    calls: list[list[str]] = [[]]
    for line in text.splitlines():
        if line == CALL_END:
            calls.append([])
        else:
            calls[-1].append(line)
    return [call for call in calls if call]


def _mapping(value: object, what: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ActionFormatError(f"{what} is not a mapping")
    return {str(key): item for key, item in value.items()}


def load_action(path: Path) -> tuple[dict[str, str], list[ActionStep]]:
    """Return the default of every input and the run steps of the action, as written in the file.

    Raises:
        ActionFormatError: the file is not the composite action shape this proof reads.
    """
    document = _mapping(yaml.safe_load(path.read_text(encoding="utf-8")), "the action")
    defaults = {
        name: str(_mapping(spec, f"input {name}").get("default", ""))
        for name, spec in _mapping(document.get("inputs"), "inputs").items()
    }
    raw_steps = _mapping(document.get("runs"), "runs").get("steps")
    if not isinstance(raw_steps, list):
        raise ActionFormatError("runs.steps is not a list")
    steps: list[ActionStep] = []
    for raw in raw_steps:
        step = _mapping(raw, "a step")
        environment = {key: str(value) for key, value in _mapping(step.get("env", {}), "env").items()}
        steps.append(ActionStep(str(step.get("id")), str(step.get("run")), environment))
    if [step.step_id for step in steps] != list(STEP_IDS):
        raise ActionFormatError(f"the steps are not {STEP_IDS}")
    return defaults, steps


def run_command(argv: Sequence[str], stdin_text: str = "", timeout_seconds: int = 120) -> CommandResult:
    """Run one command to its end and return what it printed.

    Raises:
        SetupError: the command did not end within the time.
    """
    try:
        completed = subprocess.run(
            list(argv), input=stdin_text, capture_output=True, text=True, timeout=timeout_seconds, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise SetupError(f"{argv[0]} did not end within {timeout_seconds} seconds") from error
    return CommandResult(completed.returncode, completed.stdout, completed.stderr)


def run_checked(argv: Sequence[str]) -> str:
    """Run a setup command and return its standard output.

    Raises:
        SetupError: the command ended with a non-zero status.
    """
    result = run_command(argv)
    if result.exit_status != 0:
        raise SetupError(f"{' '.join(argv[:2])} failed with status {result.exit_status}: {result.stderr.strip()}")
    return result.stdout


def generate_key(directory: Path, name: str) -> KeyPair:
    """Create an unencrypted ed25519 key pair and return it with its fingerprint."""
    private_path = directory / name
    run_checked(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", name, "-f", str(private_path)])
    public_key = " ".join((directory / f"{name}.pub").read_text(encoding="utf-8").split()[:2])
    listing = run_checked(["ssh-keygen", "-l", "-f", str(private_path)])
    return KeyPair(private_path, public_key, listing.split()[1])


def find_sshd() -> str:
    found = shutil.which("sshd") or ("/usr/sbin/sshd" if Path("/usr/sbin/sshd").exists() else None)
    if found is None:
        raise SetupError("sshd is not installed: install openssh-server")
    return found


def ensure_port_free(address: str) -> None:
    """Raise SetupError when something already listens on the address, so a server cannot start there."""
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((address, SSH_PORT))
        except OSError as error:
            raise SetupError(
                f"port {SSH_PORT} on {address} is in use ({error.strerror}): stop the system sshd first"
            ) from error


def add_user(stack: ExitStack, name: str, shell: str, home: Path) -> None:
    """Create one throwaway account, and register its removal before anything else can fail."""
    try:
        pwd.getpwnam(name)
    except KeyError:
        pass
    else:
        raise SetupError(f"the account {name} exists already and is not this proof's to remove")
    run_checked(["useradd", "--no-create-home", "--home-dir", str(home), "--shell", shell, "--password", "*", name])
    stack.callback(run_command, ["userdel", name])


def write_private(path: Path, text: str, mode: int) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(mode)


def sshd_configuration(fixture_directory: Path, address: str, host_key: KeyPair) -> str:
    return "\n".join(
        [
            f"ListenAddress {address}",
            f"Port {SSH_PORT}",
            f"HostKey {host_key.private_path}",
            f"AuthorizedKeysFile {fixture_directory}/authorized_keys.%u",
            "PasswordAuthentication no",
            "KbdInteractiveAuthentication no",
            "PubkeyAuthentication yes",
            "UsePAM no",
            "StrictModes no",
            "PermitRootLogin no",
            "LogLevel DEBUG1",
            "",
        ]
    )


def start_server(stack: ExitStack, directory: Path, name: str, address: str, host_key: KeyPair) -> Server:
    """Start one sshd in the foreground of a child process and wait until it accepts connections."""
    ensure_port_free(address)
    config_path = directory / f"sshd_{name}.conf"
    log_path = directory / f"sshd_{name}.log"
    write_private(config_path, sshd_configuration(directory, address, host_key), 0o600)
    process = subprocess.Popen([find_sshd(), "-D", "-f", str(config_path), "-E", str(log_path)])
    stack.callback(stop_process, process)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise SetupError(f"the {name} sshd exited with status {process.returncode}: {log_path.read_text()[-400:]}")
        with socket.socket() as probe:
            if probe.connect_ex((address, SSH_PORT)) == 0:
                return Server(address, log_path)
        time.sleep(0.1)
    raise SetupError(f"the {name} sshd does not accept connections on {address}")


def stop_process(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def write_gateway_accounts(directory: Path, fixture_keys: KeyPair) -> None:
    for account in GATEWAY_ACCOUNTS:
        forced = FORCED_COMMAND if account.has_forced_command else None
        line = gateway_authorized_keys_line(TARGET_ADDRESS, SSH_PORT, fixture_keys.public_key, forced)
        write_private(directory / f"authorized_keys.{account.name}", line + "\n", 0o644)


@contextmanager
def proof_fixture() -> Iterator[Fixture]:
    """Build the whole proof environment, and tear it down on every way out.

    Raises:
        SetupError: a tool, a port, an account or a server is not as the proof needs it.
    """
    try:
        pwd.getpwnam("sshd")
    except KeyError as error:
        raise SetupError("sshd needs the sshd account: install openssh-server") from error
    with ExitStack() as stack:
        if not PRIVILEGE_SEPARATION_DIRECTORY.is_dir():
            PRIVILEGE_SEPARATION_DIRECTORY.mkdir(mode=0o755)
            stack.callback(PRIVILEGE_SEPARATION_DIRECTORY.rmdir)
        directory = Path(tempfile.mkdtemp(prefix="ssh-jump-proof-"))
        stack.callback(shutil.rmtree, directory, True)
        directory.chmod(0o755)
        key_directory = directory / "keys"
        key_directory.mkdir(mode=0o700)
        marker_directory = directory / "markers"
        marker_directory.mkdir()
        marker_directory.chmod(0o777)
        shim_directory = directory / "bin"
        shim_directory.mkdir()
        argv_log = directory / "ssh_argv.log"
        real_ssh = shutil.which("ssh")
        if real_ssh is None:
            raise SetupError("ssh is not installed: install openssh-client")
        write_private(shim_directory / "ssh", SSH_SHIM.format(real=real_ssh), 0o755)

        jump_key = generate_key(key_directory, "jump_key")
        check_key = generate_key(key_directory, "check_key")
        gateway_host_key = generate_key(key_directory, "gateway_host_key")
        target_host_key = generate_key(key_directory, "target_host_key")
        wrong_host_key = generate_key(key_directory, "wrong_host_key")

        for account in GATEWAY_ACCOUNTS:
            add_user(stack, account.name, account.shell, directory)
        add_user(stack, DEPLOY_USER, LOGIN_SHELL, directory)
        write_gateway_accounts(directory, jump_key)
        command_path = directory / "target_command.sh"
        write_private(command_path, TARGET_COMMAND, 0o755)
        write_private(
            directory / f"authorized_keys.{DEPLOY_USER}",
            target_authorized_keys_line(command_path, check_key.public_key) + "\n",
            0o644,
        )

        gateway = start_server(stack, directory, "gateway", GATEWAY_ADDRESS, gateway_host_key)
        target = start_server(stack, directory, "target", TARGET_ADDRESS, target_host_key)
        yield Fixture(
            directory,
            shim_directory,
            argv_log,
            marker_directory,
            gateway,
            target,
            jump_key,
            check_key,
            gateway_host_key,
            target_host_key,
            wrong_host_key,
        )


def run_action(fixture: Fixture, name: str, known_hosts: str, jump: bool) -> ActionRun:
    """Run the validate, check and report steps of the action, as written, against the two servers."""
    defaults, steps = load_action(ACTION)
    runner_temp = fixture.directory / "runs" / name
    runner_temp.mkdir(parents=True)
    home = runner_temp / "home"
    home.mkdir()
    output_file = runner_temp / "github_output"
    summary_file = runner_temp / "github_summary"
    output_file.write_text("")
    summary_file.write_text("")
    inputs = {
        **defaults,
        "hosts": f"{DEPLOY_USER}@{TARGET_ADDRESS}",
        "known-hosts": known_hosts,
        "check-key": fixture.check_key.private_text(),
        "sha": PROOF_SHA,
    }
    if jump:
        inputs["jump-host"] = f"{JUMP_USER}@{GATEWAY_ADDRESS}"
        inputs["jump-key"] = fixture.jump_key.private_text()
    base_environment = {
        "PATH": f"{fixture.shim_directory}:{os.environ['PATH']}",
        "HOME": str(home),
        "LANG": "C.UTF-8",
        "RUNNER_TEMP": str(runner_temp),
        "GITHUB_OUTPUT": str(output_file),
        "GITHUB_STEP_SUMMARY": str(summary_file),
        "GITHUB_WORKSPACE": str(REPO),
        "SSH_ARGV_LOG": str(fixture.argv_log),
    }
    fixture.argv_log.write_text("")
    results: list[tuple[str, int]] = []
    log = ""
    for step in steps:
        script_path = runner_temp / f"{step.step_id}.sh"
        script_path.write_text(step.script, encoding="utf-8")
        environment = {key: run_workflow_steps.substitute(value, inputs) for key, value in step.environment.items()}
        completed = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", str(script_path)],
            env={**base_environment, **environment},
            cwd=REPO,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        log += completed.stdout + completed.stderr
        results.append((step.step_id, completed.returncode))
        if completed.returncode != 0:
            break
    return ActionRun(
        results,
        log,
        parse_outputs(output_file.read_text()),
        summary_file.read_text(),
        runner_temp / "ansible-plan",
        parse_ssh_calls(fixture.argv_log.read_text()),
    )


def jump_key_ssh(
    fixture: Fixture, known_hosts: Path, account: str, remote: Sequence[str], extra: Sequence[str] = ()
) -> list[str]:
    """Return the argv of a direct ssh to the gateway with the jump key and the strict options of the hop."""
    return [
        "ssh",
        "-F",
        "none",
        "-i",
        str(fixture.jump_key.private_path),
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
        *extra,
        f"{account}@{GATEWAY_ADDRESS}",
        *remote,
    ]


class ForwardResult(NamedTuple):
    exit_status: int
    banner: str
    stderr: str


def open_forward(argv: Sequence[str]) -> ForwardResult:
    """Run ssh -W and return the first line the far end sent, so an opened channel is seen to carry data."""
    process = subprocess.Popen(
        list(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    watchdog = threading.Timer(30, process.kill)
    watchdog.start()
    try:
        if process.stdout is None or process.stdin is None or process.stderr is None:
            raise SetupError("ssh -W has no pipes")
        banner = process.stdout.readline()
        process.stdin.close()
        stderr = process.stderr.read()
        return ForwardResult(process.wait(), banner.strip(), stderr)
    finally:
        watchdog.cancel()
        process.kill()
        process.wait()
        for stream in (process.stdout, process.stderr):
            if stream is not None:
                stream.close()


def log_text(server: Server) -> str:
    return server.log_path.read_text(encoding="utf-8", errors="replace")


def log_since(server: Server, offset: int) -> str:
    time.sleep(0.5)
    return log_text(server)[offset:]


def known_hosts_text(gateway_key: KeyPair | None, target_key: KeyPair | None) -> str:
    lines = []
    if gateway_key is not None:
        lines.append(gateway_key.known_hosts_line(GATEWAY_ADDRESS))
    if target_key is not None:
        lines.append(target_key.known_hosts_line(TARGET_ADDRESS))
    return "\n".join(lines)


def names_in(text: str) -> list[str]:
    return [name for name in (GATEWAY_ADDRESS, TARGET_ADDRESS, JUMP_USER, DEPLOY_USER) if name in text]


def plan_files_left(run: ActionRun) -> list[str]:
    return [name for name in LOCKED_PLAN_FILES if (run.plan_directory / name).exists()]


def prove_jump_path(fixture: Fixture, report: Report) -> None:
    report.section("(1) the action's jump path reaches the target through the gateway")
    gateway_mark, target_mark = len(log_text(fixture.gateway)), len(log_text(fixture.target))
    known_hosts = known_hosts_text(fixture.gateway_host_key, fixture.target_host_key)
    run = run_action(fixture, "jump", known_hosts, jump=True)
    gateway_log, target_log = log_since(fixture.gateway, gateway_mark), log_since(fixture.target, target_mark)
    report.observe(f"steps: {run.steps}; outputs: {run.outputs}")
    report.assert_that(run.every_step_succeeded(), "(1) validate, check and report all succeed with the jump set")
    report.assert_that(
        run.outputs.get("outcome") == "ok", "(1) the report step classifies the answer as a normal result (outcome ok)"
    )
    outer = run.ssh_calls[0] if run.ssh_calls else []
    report.observe("outer ssh argv, as the shim recorded it:\n" + " ".join(outer))
    report.assert_that("ClearAllForwardings=yes" in outer, "(1) the outer ssh ran with -o ClearAllForwardings=yes")
    report.assert_that("ProxyJump=jump_hop" in outer, "(1) the outer ssh ran with -o ProxyJump=jump_hop")
    permitted = permitted_forwards(gateway_log)
    report.observe(
        f"gateway log, forward requests: {requested_forwards(gateway_log)}; refused: {denied_forwards(gateway_log)}"
    )
    report.assert_that(
        permitted == [Endpoint(TARGET_ADDRESS, SSH_PORT)],
        "(1) the gateway carried exactly one forward, to the target on port 22",
    )
    report.assert_that(
        [login.user for login in accepted_logins(target_log)] == [DEPLOY_USER],
        "(1) the target logged one login, of the deploy account",
    )
    report.assert_that(
        not names_in(run.log + run.summary), "(1) neither the job log nor the summary holds a host or account name"
    )
    report.assert_that(
        not plan_files_left(run), "(1) the key, known_hosts, jump key and jump config files are gone after the steps"
    )
    prove_key_separation(fixture, report, gateway_log, target_log)


def prove_key_separation(fixture: Fixture, report: Report, gateway_log: str, target_log: str) -> None:
    report.section("(1b) key separation, from what each server logged")
    jump, check = fixture.jump_key.fingerprint, fixture.check_key.fingerprint
    report.observe(
        f"gateway offered: {sorted(offered_fingerprints(gateway_log))}; accepted: {accepted_logins(gateway_log)}\n"
        f"target offered: {sorted(offered_fingerprints(target_log))}; accepted: {accepted_logins(target_log)}"
    )
    report.assert_that(
        offered_fingerprints(gateway_log) == {jump}, "(1b) the gateway was offered the jump key and nothing else"
    )
    report.assert_that(
        offered_fingerprints(target_log) == {check}, "(1b) the target was offered the check key and nothing else"
    )
    report.assert_that(
        [a.fingerprint for a in accepted_logins(gateway_log)] == [jump], "(1b) the gateway accepted the jump key only"
    )
    report.assert_that(
        [a.fingerprint for a in accepted_logins(target_log)] == [check], "(1b) the target accepted the check key only"
    )
    report.assert_that(
        check not in gateway_log and jump not in target_log, "(1b) neither server's log names the other key at all"
    )
    prove_cross_use(fixture, report)


def prove_cross_use(fixture: Fixture, report: Report) -> None:
    known_hosts = fixture.directory / "known_hosts_both"
    known_hosts.write_text(known_hosts_text(fixture.gateway_host_key, fixture.target_host_key) + "\n")
    options = [
        "-F",
        "none",
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=yes",
        "-o",
        f"UserKnownHostsFile={known_hosts}",
        "-o",
        "GlobalKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=10",
    ]
    check_to_gateway = run_command(
        ["ssh", *options, "-i", str(fixture.check_key.private_path), f"{JUMP_USER}@{GATEWAY_ADDRESS}", "true"]
    )
    jump_to_target = run_command(
        ["ssh", *options, "-i", str(fixture.jump_key.private_path), f"{DEPLOY_USER}@{TARGET_ADDRESS}", "true"]
    )
    report.observe(
        f"check key to the gateway: status {check_to_gateway.exit_status}: {check_to_gateway.stderr.strip()}"
    )
    report.observe(f"jump key to the target: status {jump_to_target.exit_status}: {jump_to_target.stderr.strip()}")
    report.assert_that(
        "Permission denied (publickey)" in check_to_gateway.stderr, "(1b) the check key is refused by the gateway"
    )
    report.assert_that(
        "Permission denied (publickey)" in jump_to_target.stderr, "(1b) the jump key is refused by the target"
    )


def prove_direct_path_control(fixture: Fixture, report: Report) -> None:
    report.section("(1c) control: with no jump the gateway is not touched, so its log can show a jump")
    gateway_mark, target_mark = len(log_text(fixture.gateway)), len(log_text(fixture.target))
    run = run_action(fixture, "direct", known_hosts_text(None, fixture.target_host_key), jump=False)
    gateway_log, target_log = log_since(fixture.gateway, gateway_mark), log_since(fixture.target, target_mark)
    outer = run.ssh_calls[0] if run.ssh_calls else []
    report.observe(
        f"steps: {run.steps}; gateway connections: {count_connections(gateway_log)}; target logins: {accepted_logins(target_log)}"
    )
    report.assert_that(
        run.every_step_succeeded() and run.outputs.get("outcome") == "ok", "(1c) the run without a jump succeeds"
    )
    report.assert_that(count_connections(gateway_log) == 0, "(1c) the gateway saw no connection when no jump was set")
    report.assert_that(
        "-F" not in outer and not any("ProxyJump" in argument for argument in outer),
        "(1c) the ssh call has no -F and no ProxyJump without a jump",
    )
    report.assert_that(len(accepted_logins(target_log)) == 1, "(1c) the target logged the one direct login")


def prove_forwarding_lock(fixture: Fixture, report: Report) -> None:
    report.section("(2) the jump key opens the allowed destination and no other")
    known_hosts = fixture.directory / "known_hosts_gateway"
    known_hosts.write_text(fixture.gateway_host_key.known_hosts_line(GATEWAY_ADDRESS) + "\n")
    allowed = Endpoint(TARGET_ADDRESS, SSH_PORT)
    refused = [
        (Endpoint(TARGET_ADDRESS, OTHER_PORT), "the same host, another port"),
        (Endpoint(OTHER_ADDRESS, SSH_PORT), "another host, the same port"),
        (Endpoint(GATEWAY_ADDRESS, SSH_PORT), "the gateway itself"),
        (Endpoint("0x7f.0.0.3", SSH_PORT), "another spelling of the target's address"),
        (Endpoint("[::ffff:127.0.0.3]", SSH_PORT), "the target's address as an IPv4-mapped IPv6 address"),
    ]
    mark = len(log_text(fixture.gateway))
    result = open_forward(jump_key_ssh(fixture, known_hosts, JUMP_USER, [], ["-W", f"{allowed.host}:{allowed.port}"]))
    gateway_log = log_since(fixture.gateway, mark)
    report.observe(
        f"allowed -W {allowed.host}:{allowed.port}: status {result.exit_status}, the far end sent {result.banner!r}"
    )
    report.assert_that(
        result.banner.startswith("SSH-2.0-"),
        "(2) positive control: the allowed destination is reached, the target's banner arrives",
    )
    report.assert_that(
        permitted_forwards(gateway_log) == [allowed],
        "(2) positive control: the gateway log records the forward as permitted",
    )
    for endpoint, why in refused:
        prove_refusal(fixture, report, known_hosts, endpoint, why)


def prove_refusal(fixture: Fixture, report: Report, known_hosts: Path, endpoint: Endpoint, why: str) -> None:
    mark = len(log_text(fixture.gateway))
    result = open_forward(jump_key_ssh(fixture, known_hosts, JUMP_USER, [], ["-W", f"{endpoint.host}:{endpoint.port}"]))
    gateway_log = log_since(fixture.gateway, mark)
    bare_host = endpoint.host.strip("[]")
    report.observe(
        f"refused -W {endpoint.host}:{endpoint.port} ({why}): status {result.exit_status}, "
        f"client: {result.stderr.strip().splitlines()[:1]}, gateway: {denied_forwards(gateway_log)}"
    )
    case = f"(2) {why}"
    report.assert_that(result.exit_status != 0 and not result.banner, f"{case}: ssh -W fails and nothing is carried")
    report.assert_that(REFUSAL in result.stderr, f"{case}: the client reports '{REFUSAL}'")
    report.assert_that(
        Endpoint(bare_host, endpoint.port) in denied_forwards(gateway_log),
        f"{case}: the gateway log records the request as denied",
    )
    report.assert_that(not permitted_forwards(gateway_log), f"{case}: the gateway permitted no forward")


def marker_for(fixture: Fixture, account: str, label: str) -> Path:
    return fixture.marker_directory / f"{account}-{label}"


def prove_no_command(fixture: Fixture, report: Report) -> None:
    report.section("(3) the jump key cannot run a command or get a shell on the gateway")
    known_hosts = fixture.directory / "known_hosts_gateway"
    for account in GATEWAY_ACCOUNTS:
        prove_command_attempts(fixture, report, known_hosts, account)
    prove_marker_directory_is_writable(fixture, report)


def prove_command_attempts(fixture: Fixture, report: Report, known_hosts: Path, account: GatewayAccount) -> None:
    attempts = {
        "command": (["touch", str(marker_for(fixture, account.name, "command"))], "", []),
        "id": (["id"], "", []),
        "shell": ([], f"touch {marker_for(fixture, account.name, 'shell')}\nexit\n", []),
        "tty": ([], "", ["-tt"]),
    }
    for label, (remote, stdin_text, extra) in attempts.items():
        result = run_command(jump_key_ssh(fixture, known_hosts, account.name, remote, extra), stdin_text)
        ran = result.exit_status == 0 and label != "tty"
        report.observe(
            f"{account.name} ({account.description}), {label}: status {result.exit_status}: {(result.stdout + result.stderr).strip().splitlines()[:1]}"
        )
        if label in ("command", "shell"):
            made = marker_for(fixture, account.name, label).exists()
            report.assert_that(
                made == account.runs_commands,
                f"(3) {account.name}, {label}: the marker file is {'made' if account.runs_commands else 'not made'}",
            )
        if not account.runs_commands:
            report.assert_that(
                not ran and result.exit_status != 0, f"(3) {account.name}, {label}: refused, non-zero status"
            )
        elif label == "id":
            report.assert_that(
                ran, f"(3) {account.name}, {label}: control runs the command, so a refusal above is not a blind test"
            )


def prove_marker_directory_is_writable(fixture: Fixture, report: Report) -> None:
    probe = marker_for(fixture, JUMP_USER, "writable-probe")
    result = run_command(["runuser", "-u", JUMP_USER, "--", "touch", str(probe)])
    report.assert_that(
        result.exit_status == 0 and probe.exists(),
        "(3) the marker directory is writable by the jump account, so an absent marker means no command ran",
    )


def prove_host_key_checks(fixture: Fixture, report: Report) -> None:
    report.section("(4) a wrong or missing gateway host key stops the run")
    cases = [
        (
            "a different key for the gateway address",
            known_hosts_text(fixture.wrong_host_key, fixture.target_host_key),
            "wronggateway",
        ),
        ("no line for the gateway", known_hosts_text(None, fixture.target_host_key), "nogateway"),
    ]
    for why, known_hosts, name in cases:
        prove_failed_run(fixture, report, why, known_hosts, name, expect_forward=False)
    report.section("(5) a wrong target host key stops the run, even though the gateway carried the connection")
    wrong_target = known_hosts_text(fixture.gateway_host_key, fixture.wrong_host_key)
    prove_failed_run(
        fixture, report, "a different key for the target address", wrong_target, "wrongtarget", expect_forward=True
    )


def prove_failed_run(
    fixture: Fixture, report: Report, why: str, known_hosts: str, name: str, expect_forward: bool
) -> None:
    gateway_mark, target_mark = len(log_text(fixture.gateway)), len(log_text(fixture.target))
    run = run_action(fixture, name, known_hosts, jump=True)
    gateway_log, target_log = log_since(fixture.gateway, gateway_mark), log_since(fixture.target, target_mark)
    error_text = (run.plan_directory / "01.err").read_text(errors="replace")
    stage = "(5)" if expect_forward else "(4)"
    said = [line for line in error_text.splitlines() if "Host key verification failed" in line or "WARNING" in line]
    report.observe(f"{why}: steps {run.steps}, outcome {run.outputs.get('outcome')!r}; ssh said: {said}")
    report.assert_that(
        run.status_of("report") == 1 and run.outputs.get("outcome") == "unreachable",
        f"{stage} {why}: the host is reported unreachable and the report step fails",
    )
    report.assert_that(
        "Host key verification failed" in error_text, f"{stage} {why}: ssh stopped on 'Host key verification failed'"
    )
    report.assert_that(not accepted_logins(target_log), f"{stage} {why}: the target accepted no key")
    gateway_accepted = [login.fingerprint for login in accepted_logins(gateway_log)]
    report.assert_that(
        gateway_accepted == ([fixture.jump_key.fingerprint] if expect_forward else []),
        f"{stage} {why}: the gateway accepted {'only the jump key' if expect_forward else 'no key'}",
    )
    report.assert_that(
        not names_in(run.log + run.summary), f"{stage} {why}: no host or account name reaches the job log or summary"
    )
    report.assert_that(not plan_files_left(run), f"{stage} {why}: the key and config files are gone")
    if expect_forward:
        report.assert_that(
            permitted_forwards(gateway_log) == [Endpoint(TARGET_ADDRESS, SSH_PORT)],
            f"{stage} {why}: the gateway did carry the connection, so the target's own check is what stopped it",
        )
    else:
        report.assert_that(
            not requested_forwards(gateway_log) and count_connections(target_log) == 0,
            f"{stage} {why}: the gateway was asked for no forward and the target saw no connection",
        )


def ensure_root() -> None:
    """Run this script again under sudo when it is not root: the servers need port 22 and system accounts."""
    if os.geteuid() != 0:
        os.execvp("sudo", ["sudo", "-n", sys.executable, os.path.abspath(sys.argv[0]), *sys.argv[1:]])


def main() -> int:
    ensure_root()
    report = Report()
    try:
        with proof_fixture() as fixture:
            report.observe(
                f"gateway sshd on {GATEWAY_ADDRESS}:{SSH_PORT}, target sshd on {TARGET_ADDRESS}:{SSH_PORT}, throwaway keys and accounts"
            )
            report.observe(f"jump key {fixture.jump_key.fingerprint}, check key {fixture.check_key.fingerprint}")
            prove_jump_path(fixture, report)
            prove_direct_path_control(fixture, report)
            prove_forwarding_lock(fixture, report)
            prove_no_command(fixture, report)
            prove_host_key_checks(fixture, report)
    except ProofError as error:
        print(f"ERROR: {error}", file=sys.stderr, flush=True)
        return 2
    print(f"\n{report.passed} assertions passed, {len(report.failures)} failed", flush=True)
    for description in report.failures:
        print(f"FAILED: {description}", flush=True)
    return 1 if report.failures else 0


if __name__ == "__main__":
    sys.exit(main())
