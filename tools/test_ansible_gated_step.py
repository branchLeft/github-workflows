"""Tests of ansible-gated-step.yml, run against the text of each step.

Each step's `run:` text runs under bash with `ssh`, `timeout` and `yq` stubbed on
PATH, and the `${{ }}` expressions of its `env:` are bound from a context the test
builds: an expression the context lacks fails the test, so no step passes on a
value nobody set. tools/sabotage_step.py breaks each control and requires its
test here to go red. The workflow's own page is ansible-gated-step.md.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = REPO / ".github/workflows/ansible-gated-step.yml"
DOC_PAGE = REPO / ".github/workflows/ansible-gated-step.md"
SELF_WORKFLOW = REPO / ".github/workflows/ansible-gated-step-self.yml"
APPLY_WORKFLOW = REPO / ".github/workflows/ansible-gated-apply.yml"

DOC: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
JOBS: dict[str, dict[str, Any]] = DOC["jobs"]
TRIGGERS: dict[str, Any] = DOC.get("on") or DOC[True]

SHA = "a" * 40
OTHER_SHA = "b" * 40
OWNER = "test-owner"
STEP = "migrate"
WORD = "MIGRATE-OPS1"
DEST = "stepper@ops1.example.test"
JUMP = "jumper@jump.example.test"
JUMP2 = "jumper2@jump2.example.test"
KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nSTEPKEYLINEONE0123456789abcdef\nSTEPKEYLINETWO0123456789abcdef\n-----END OPENSSH PRIVATE KEY-----"
JUMP_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nJUMPKEYLINEONE0123456789abcdef\n-----END OPENSSH PRIVATE KEY-----"
JUMP2_KEY = "-----BEGIN OPENSSH PRIVATE KEY-----\nJUMP2KEYLINEONE123456789abcdef\n-----END OPENSSH PRIVATE KEY-----"
KNOWN_HOSTS = "ops1.example.test ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKNOWNHOSTLINE"
INVENTORY = "ansible/owner_vars/ops1.yml"
INVENTORY_TEXT = """---
pull_trigger_steps:
  - id: migrate
    tag: control_plane_db_migrate
    word: MIGRATE-OPS1
  - id: identity
    tag: control_plane_db_identity
    word: IDENTITY-OPS1
control_plane_db_apps_image: 'registry.example.test/apps@sha256:0000000000000000000000000000000000000000000000000000000000000000'
"""

EXPRESSION = re.compile(r"\$\{\{\s*(.*?)\s*\}\}")


def step_named(job: str, fragment: str) -> dict[str, Any]:
    steps: list[dict[str, Any]] = JOBS[job]["steps"]
    for step in steps:
        if fragment in step.get("name", ""):
            return step
    raise KeyError(f"{job}/{fragment}")


def base_context() -> dict[str, str]:
    return {
        "github.event_name": "workflow_dispatch",
        "github.ref": "refs/heads/main",
        "github.event.repository.default_branch": "main",
        "github.event.repository.fork": "false",
        "github.sha": SHA,
        "github.actor": OWNER,
        "github.triggering_actor": OWNER,
        "inputs.owner-login": OWNER,
        "inputs.step": STEP,
        "inputs.confirm": WORD,
        "inputs.environment": "control-plane-step",
        "inputs.step-inventory": INVENTORY,
        "inputs.step-inputs": f"{STEP}={INVENTORY}\n",
        "inputs.host-timeout-minutes": "65",
        "steps.request.outputs.sha": SHA,
        "steps.request.outputs.step": STEP,
        "needs.guard.outputs.sha": SHA,
        "needs.guard.outputs.step": STEP,
        "needs.guard.result": "success",
        "needs.send.result": "success",
        "secrets.ssh_step_key": KEY,
        "secrets.ssh_step_host": DEST,
        "secrets.ssh_known_hosts": KNOWN_HOSTS,
        "secrets.ssh_jump_host": "",
        "secrets.ssh_jump_key": "",
        "secrets.ssh_jump2_host": "",
        "secrets.ssh_jump2_key": "",
    }


def bind(step: dict[str, Any], context: dict[str, str]) -> dict[str, str]:
    """Return the step's env with every expression resolved from ``context``."""
    bound: dict[str, str] = {}
    for name, text in step.get("env", {}).items():
        bound[name] = EXPRESSION.sub(lambda match: context[match.group(1)], str(text))
    return bound


# Reads its standard input to the end, as ssh does, records what the step passed
# and the state of the key directory at the moment of the call, then answers from
# out.N, err.N and rc.N for the Nth call. A call with no answer prepared exits 97.
SSH_STUB = """#!/usr/bin/env bash
set -u
cat > /dev/null || true
n=$(( $(cat "$STUB_DIR/ssh.count" 2> /dev/null || echo 0) + 1 ))
echo "$n" > "$STUB_DIR/ssh.count"
printf '%s\\n' "$*" >> "$STUB_DIR/ssh.log"
identity=""
config=""
previous=""
for arg in "$@"; do
  [ "$previous" = -i ] && identity="$arg"
  [ "$previous" = -F ] && config="$arg"
  previous="$arg"
done
ls -l "$(dirname "$identity")" > "$STUB_DIR/ssh.workdir.$n"
[ -z "$config" ] || cp "$config" "$STUB_DIR/ssh.config.$n"
[ -f "$STUB_DIR/rc.$n" ] || exit 97
cat "$STUB_DIR/out.$n"
cat "$STUB_DIR/err.$n" >&2
exit "$(cat "$STUB_DIR/rc.$n")"
"""

TIMEOUT_STUB = """#!/usr/bin/env bash
echo "$1" >> "$STUB_DIR/timeout.log"
shift
exec "$@"
"""

# Answers only `yq -o=json . FILE`, by reading the file with PyYAML. Any other
# call, a missing file or a file that is not YAML exits non-zero, as yq does. The
# real yq runs on the runner.
YQ_STUB = f"""#!{sys.executable}
import json
import sys

import yaml

if sys.argv[1:3] != ["-o=json", "."] or len(sys.argv) != 4:
    sys.exit(64)
try:
    with open(sys.argv[3], encoding="utf-8") as handle:
        print(json.dumps(yaml.safe_load(handle)))
except (OSError, yaml.YAMLError):
    sys.exit(1)
"""


def host_line(
    verdict: str,
    code: int,
    commit: str = SHA,
    changed: int | str = 1,
    step: str = STEP,
    mode: str = "step",
    reason: str = "ok",
) -> str:
    line = f"pull-trigger verdict={verdict} exit={code} reason={reason} mode={mode} commit={commit} playbook=ansible/playbooks/ops1.yml changed={changed}"
    return f"{line} step={step}\n" if mode == "step" else f"{line}\n"


APPLIED = host_line("applied", 10, changed=3)
UNCHANGED = host_line("unchanged", 0, changed=0)


class Result:
    def __init__(
        self, proc: subprocess.CompletedProcess[str], outputs: dict[str, str], summary: str
    ) -> None:
        self.code = proc.returncode
        self.log = proc.stdout + proc.stderr
        self.outputs = outputs
        self.summary = summary

    def visible_log(self) -> str:
        """The log as a reader sees it: the runner consumes the add-mask commands."""
        return "\n".join(
            line for line in self.log.splitlines() if not line.startswith("::add-mask::")
        )


class Sandbox:
    """A scratch directory with stubs on PATH and the files the runner provides."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="gated-step-test-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.stubs = self.dir / "stubs"
        self.temp = self.dir / "runner-temp"
        self.workspace = self.dir / "workspace"
        for path in (self.bin, self.stubs, self.temp, self.workspace):
            path.mkdir()
        for name, text in (("ssh", SSH_STUB), ("timeout", TIMEOUT_STUB), ("yq", YQ_STUB)):
            target = self.bin / name
            target.write_text(text)
            target.chmod(0o755)
        self.output = self.dir / "github-output"
        self.summary = self.dir / "github-summary"
        self.output.write_text("")
        self.summary.write_text("")

    def write(self, relative: str, text: str) -> Path:
        target = self.workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        return target

    def answers(self, *calls: tuple[int, str, str]) -> None:
        """Prepare the answers of the 1st, 2nd... call of ssh: exit status, standard output, standard error."""
        for number, (code, out, err) in enumerate(calls, start=1):
            self.stubs.joinpath(f"rc.{number}").write_text(f"{code}\n")
            self.stubs.joinpath(f"out.{number}").write_text(out)
            self.stubs.joinpath(f"err.{number}").write_text(err)

    def ssh_calls(self) -> list[str]:
        path = self.stubs / "ssh.log"
        return path.read_text().splitlines() if path.exists() else []

    def stub_file(self, name: str) -> str:
        return (self.stubs / name).read_text()

    def run(
        self,
        step: dict[str, Any],
        context: dict[str, str] | None = None,
        extra: dict[str, str] | None = None,
    ) -> Result:
        values = bind(step, {**base_context(), **(context or {})})
        script = self.dir / "step.sh"
        script.write_text(step["run"])
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.dir),
            "RUNNER_TEMP": str(self.temp),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GITHUB_WORKSPACE": str(self.workspace),
            "STUB_DIR": str(self.stubs),
            **(extra or {}),
            **values,
        }
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            env=env,
            capture_output=True,
            text=True,
            cwd=self.workspace,
            stdin=subprocess.DEVNULL,
            # The runner's default mask, so a file a step means to make private is
            # seen as private only if the step made it so.
            preexec_fn=lambda: os.umask(0o022),
            check=False,
        )
        outputs: dict[str, str] = {}
        for line in self.output.read_text().splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
        return Result(proc, outputs, self.summary.read_text())


class StructureTests(unittest.TestCase):
    def test_the_workflow_is_called_never_started_by_an_event_of_its_own(self) -> None:
        self.assertEqual(set(TRIGGERS), {"workflow_call"})

    def test_the_default_permissions_are_read_only(self) -> None:
        self.assertEqual(DOC["permissions"], {"contents": "read"})

    def test_send_job_runs_in_the_environment_input(self) -> None:
        self.assertEqual(JOBS["send"].get("environment"), "${{ inputs.environment }}")

    def test_the_environment_defaults_to_control_plane_step(self) -> None:
        declared = TRIGGERS["workflow_call"]["inputs"]["environment"]
        self.assertEqual(declared["default"], "control-plane-step")

    def test_guard_job_holds_no_environment_and_no_secret(self) -> None:
        self.assertNotIn("environment", JOBS["guard"])
        self.assertNotIn("secrets.", json.dumps(JOBS["guard"]))

    def test_no_job_but_send_reads_a_secret(self) -> None:
        for name, job in JOBS.items():
            if name != "send":
                self.assertNotIn("secrets.", json.dumps(job), name)

    def test_send_waits_for_the_guard_and_is_never_cancelled(self) -> None:
        self.assertEqual(JOBS["send"]["needs"], ["guard"])
        self.assertIs(JOBS["send"]["concurrency"]["cancel-in-progress"], False)
        self.assertIn("inputs.environment", JOBS["send"]["concurrency"]["group"])

    def test_the_checks_job_needs_both_and_always_runs(self) -> None:
        self.assertEqual(JOBS["checks"]["needs"], ["guard", "send"])
        self.assertEqual(JOBS["checks"]["if"], "always()")
        self.assertEqual(JOBS["checks"]["name"], "Ansible step checks passed")

    def test_no_run_body_holds_an_expression(self) -> None:
        for name, job in JOBS.items():
            for step in job["steps"]:
                if "run" in step:
                    self.assertNotIn("${{", step["run"], f"{name}/{step['name']}")

    def test_every_action_is_pinned_to_a_commit_with_a_version_comment(self) -> None:
        uses = re.findall(r"(?m)^\s*(?:- )?uses: (\S+)(.*)$", WORKFLOW.read_text())
        self.assertTrue(uses)
        for target, rest in uses:
            self.assertRegex(target, r"@[0-9a-f]{40}$")
            self.assertRegex(rest, r"# v\d+\.\d+\.\d+")

    def test_every_secret_is_optional_and_named_for_the_environment(self) -> None:
        secrets = TRIGGERS["workflow_call"]["secrets"]
        self.assertEqual(
            set(secrets),
            {
                "ssh_step_key",
                "ssh_step_host",
                "ssh_known_hosts",
                "ssh_jump_host",
                "ssh_jump_key",
                "ssh_jump2_host",
                "ssh_jump2_key",
            },
        )
        for name, declared in secrets.items():
            self.assertIs(declared["required"], False, name)

    def test_no_comment_block_reaches_25_lines(self) -> None:
        run = 0
        for line in WORKFLOW.read_text().splitlines():
            run = run + 1 if line.lstrip().startswith("#") else 0
            self.assertLess(run, 25)


class RequestGuardTests(unittest.TestCase):
    def guard(self, context: dict[str, str] | None = None) -> Result:
        return Sandbox(self).run(step_named("guard", "Refuse unless the owner dispatched"), context)

    def refuses(self, context: dict[str, str]) -> None:
        result = self.guard(context)
        self.assertNotEqual(result.code, 0, context)
        self.assertEqual(result.outputs, {}, context)
        self.assertIn("::error::", result.log)

    def test_the_owner_dispatching_a_shaped_step_passes_and_the_guard_publishes_it(self) -> None:
        result = self.guard()
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs, {"sha": SHA, "step": STEP})

    def test_only_a_dispatch_passes(self) -> None:
        for event in (
            "push",
            "schedule",
            "pull_request",
            "workflow_run",
            "repository_dispatch",
            "",
        ):
            self.refuses({"github.event_name": event})

    def test_only_the_default_branch_passes(self) -> None:
        for ref in (
            "refs/heads/other",
            "refs/tags/v1.0.0",
            "refs/pull/1/merge",
            "refs/heads/main2",
            "",
        ):
            self.refuses({"github.ref": ref})

    def test_a_fork_is_refused(self) -> None:
        self.refuses({"github.event.repository.fork": "true"})

    def test_a_commit_that_is_not_40_lowercase_hex_digits_is_refused(self) -> None:
        for sha in ("", "A" * 40, "a" * 39, "a" * 41, "main", "a" * 39 + "g"):
            self.refuses({"github.sha": sha})

    def test_another_actor_is_refused(self) -> None:
        self.refuses({"github.actor": "someone-else"})

    def test_a_rerun_by_another_actor_is_refused(self) -> None:
        self.refuses({"github.triggering_actor": "someone-else"})

    def test_the_owner_account_is_compared_without_case(self) -> None:
        result = self.guard(
            {
                "inputs.owner-login": OWNER.title(),
                "github.actor": OWNER.upper(),
                "github.triggering_actor": OWNER,
            }
        )
        self.assertEqual(result.code, 0, result.log)

    def test_an_owner_input_that_is_not_an_account_name_is_refused(self) -> None:
        for owner in ("", "-x", "x-", "a b", "a;b", "a" * 40):
            self.refuses(
                {
                    "inputs.owner-login": owner,
                    "github.actor": owner,
                    "github.triggering_actor": owner,
                }
            )

    def test_a_malformed_step_is_refused(self) -> None:
        for step in (
            "",
            "Migrate",
            "1x",
            "a-b",
            "a b",
            "x" * 33,
            "migrate;ls",
            "migrate\nidentity",
            "$(id)",
        ):
            self.refuses({"inputs.step": step})

    def test_a_malformed_word_is_refused(self) -> None:
        for word in (
            "",
            "ABC",
            "migrate-ops1",
            "A" * 33,
            "MIGRATE OPS1",
            "MIGRATE;ID",
            "MIGRATE\nID",
        ):
            self.refuses({"inputs.confirm": word})

    def test_a_malformed_environment_is_refused(self) -> None:
        for environment in ("", "-x", "a b", "a;b", "a" * 65):
            self.refuses({"inputs.environment": environment})

    def test_an_inventory_path_that_is_not_a_relative_path_is_refused(self) -> None:
        for path in ("", "/etc/passwd", "../x.yml", "a/../b.yml", "a b.yml", "a;b.yml", "-x.yml"):
            self.refuses({"inputs.step-inventory": path})

    def test_a_step_inputs_line_that_is_not_an_id_and_a_relative_path_is_refused(self) -> None:
        for text in (
            "migrate",
            "migrate=",
            "Migrate=a.yml",
            "migrate=/etc/passwd",
            "migrate=../a.yml",
            "migrate=a/../b.yml",
            "migrate=a b",
            "a=b;c",
        ):
            self.refuses({"inputs.step-inputs": text})


class CheckoutTests(unittest.TestCase):
    def head(self, expected: str) -> Result:
        sandbox = Sandbox(self)
        git_env = {"GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
        for args in (
            ["init", "-q"],
            [
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "x",
            ],
        ):
            subprocess.run(
                ["git", "-C", str(sandbox.workspace), *args],
                check=True,
                env={**os.environ, **git_env},
            )
        real = subprocess.run(
            ["git", "-C", str(sandbox.workspace), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            env={**os.environ, **git_env},
        ).stdout.strip()
        return sandbox.run(
            step_named("guard", "Verify the checkout"),
            {"steps.request.outputs.sha": real if expected == "real" else expected},
            extra=git_env,
        )

    def test_the_checkout_at_the_dispatched_commit_passes(self) -> None:
        self.assertEqual(self.head("real").code, 0)

    def test_a_checkout_at_another_commit_is_refused(self) -> None:
        self.assertNotEqual(self.head(OTHER_SHA).code, 0)


class InventoryTests(unittest.TestCase):
    def judge(
        self,
        context: dict[str, str] | None = None,
        inventory: str | None = INVENTORY_TEXT,
        prepare: Callable[[Sandbox], None] | None = None,
    ) -> Result:
        sandbox = Sandbox(self)
        if inventory is not None:
            sandbox.write(INVENTORY, inventory)
        if prepare is not None:
            prepare(sandbox)
        return sandbox.run(step_named("guard", "Judge the step and its word"), context)

    def test_a_listed_step_with_its_word_passes(self) -> None:
        self.assertEqual(self.judge().code, 0)
        self.assertEqual(
            self.judge(
                {"steps.request.outputs.step": "identity", "inputs.confirm": "IDENTITY-OPS1"}
            ).code,
            0,
        )

    def test_a_step_that_is_not_on_the_allow_list_is_refused(self) -> None:
        result = self.judge(
            {"steps.request.outputs.step": "provision", "inputs.confirm": "PROVISION-OPS1"}
        )
        self.assertNotEqual(result.code, 0)
        self.assertIn("not on the host's step allow-list", result.log)

    def test_a_wrong_word_is_refused_and_never_printed(self) -> None:
        result = self.judge({"inputs.confirm": "WRONG-WORD"})
        self.assertNotEqual(result.code, 0)
        self.assertIn("not the word of this step", result.log)
        self.assertNotIn("WRONG-WORD", result.log)
        self.assertNotIn(WORD, result.log)

    def test_the_word_of_another_step_is_refused(self) -> None:
        self.assertNotEqual(self.judge({"inputs.confirm": "IDENTITY-OPS1"}).code, 0)

    def test_an_inventory_with_no_step_list_allows_no_step(self) -> None:
        self.assertNotEqual(self.judge(inventory="control_plane_db_apps_image: x\n").code, 0)
        self.assertNotEqual(self.judge(inventory="pull_trigger_steps: []\n").code, 0)

    def test_a_step_listed_twice_is_refused(self) -> None:
        again = "  - id: migrate\n    tag: control_plane_db_migrate\n    word: MIGRATE-OPS1\ncontrol_plane_db_apps_image:"
        twice = INVENTORY_TEXT.replace("control_plane_db_apps_image:", again)
        self.assertEqual(twice.count("id: migrate"), 2)
        self.assertNotEqual(self.judge(inventory=twice).code, 0)

    def test_an_unreadable_or_missing_inventory_is_refused(self) -> None:
        self.assertNotEqual(self.judge(inventory="a: [unclosed\n").code, 0)
        self.assertNotEqual(self.judge(inventory=None).code, 0)
        self.assertNotEqual(self.judge(inventory="- just\n- a list\n").code, 0)

    def test_an_inventory_that_is_a_link_or_leaves_the_checkout_is_refused(self) -> None:
        def as_link(sandbox: Sandbox) -> None:
            outside = sandbox.dir / "outside.yml"
            outside.write_text(INVENTORY_TEXT)
            (sandbox.workspace / INVENTORY).unlink()
            (sandbox.workspace / INVENTORY).symlink_to(outside)

        def through_a_linked_directory(sandbox: Sandbox) -> None:
            outside = sandbox.dir / "outside"
            outside.mkdir()
            (outside / "ops1.yml").write_text(INVENTORY_TEXT)
            (sandbox.workspace / "linked").symlink_to(outside)

        self.assertNotEqual(self.judge(prepare=as_link).code, 0)
        self.assertNotEqual(
            self.judge(
                {"inputs.step-inventory": "linked/ops1.yml"}, prepare=through_a_linked_directory
            ).code,
            0,
        )


class PlanTests(unittest.TestCase):
    def show(
        self,
        context: dict[str, str] | None = None,
        prepare: Callable[[Sandbox], None] | None = None,
    ) -> tuple[Result, Sandbox]:
        sandbox = Sandbox(self)
        sandbox.write(INVENTORY, INVENTORY_TEXT)
        if prepare is not None:
            prepare(sandbox)
        return sandbox.run(step_named("guard", "Show the approver"), context), sandbox

    def test_the_plan_is_three_lines_in_the_log_and_the_summary(self) -> None:
        result, _ = self.show()
        digest = hashlib.sha256(INVENTORY_TEXT.encode()).hexdigest()
        expected = f"commit {SHA}\nstep {STEP}\ninput {INVENTORY} sha256 {digest}\n"
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.summary, expected)
        self.assertEqual(result.log, expected)

    def test_a_step_with_no_named_input_says_so(self) -> None:
        result, _ = self.show({"inputs.step-inputs": ""})
        self.assertEqual(
            result.summary, f"commit {SHA}\nstep {STEP}\ninput none named for this step\n"
        )

    def test_a_step_named_twice_is_refused(self) -> None:
        result, _ = self.show({"inputs.step-inputs": f"{STEP}={INVENTORY}\n{STEP}={INVENTORY}\n"})
        self.assertNotEqual(result.code, 0)

    def test_a_missing_input_file_is_refused(self) -> None:
        result, _ = self.show({"inputs.step-inputs": f"{STEP}=ansible/missing.yml\n"})
        self.assertNotEqual(result.code, 0)

    def test_an_input_file_that_leaves_the_checkout_is_refused(self) -> None:
        def link(sandbox: Sandbox) -> None:
            outside = sandbox.dir / "outside.txt"
            outside.write_text("x")
            (sandbox.workspace / "linked.txt").symlink_to(outside)

        def linked_directory(sandbox: Sandbox) -> None:
            outside = sandbox.dir / "outside"
            outside.mkdir()
            (outside / "input.txt").write_text("x")
            (sandbox.workspace / "linked").symlink_to(outside)

        result, _ = self.show({"inputs.step-inputs": f"{STEP}=linked.txt\n"}, prepare=link)
        self.assertNotEqual(result.code, 0)
        result, _ = self.show(
            {"inputs.step-inputs": f"{STEP}=linked/input.txt\n"}, prepare=linked_directory
        )
        self.assertNotEqual(result.code, 0)

    def test_the_plan_step_reads_no_secret(self) -> None:
        step = step_named("guard", "Show the approver")
        self.assertNotIn("secrets", json.dumps(step))


class SendGuardTests(unittest.TestCase):
    def guard(self, context: dict[str, str] | None = None) -> Result:
        return Sandbox(self).run(
            step_named("send", "Refuse unless this is the owner's guarded step"), context
        )

    def refuses(self, context: dict[str, str]) -> None:
        result = self.guard(context)
        self.assertNotEqual(result.code, 0, context)
        self.assertIn("::error::", result.log)

    def test_the_guarded_step_of_the_owner_passes(self) -> None:
        self.assertEqual(self.guard().code, 0)

    def test_only_a_dispatch_on_the_default_branch_of_a_repository_passes(self) -> None:
        for event in ("push", "schedule", "pull_request", ""):
            self.refuses({"github.event_name": event})
        for ref in ("refs/heads/other", "refs/tags/v1.0.0"):
            self.refuses({"github.ref": ref})
        self.refuses({"github.event.repository.fork": "true"})

    def test_a_commit_or_step_the_guard_did_not_judge_is_refused(self) -> None:
        self.refuses({"needs.guard.outputs.sha": OTHER_SHA})
        self.refuses({"needs.guard.outputs.step": "identity"})
        self.refuses({"needs.guard.outputs.sha": ""})
        self.refuses({"github.sha": "A" * 40, "needs.guard.outputs.sha": "A" * 40})

    def test_another_actor_or_a_rerun_by_another_actor_is_refused(self) -> None:
        self.refuses({"github.actor": "someone-else"})
        self.refuses({"github.triggering_actor": "someone-else"})
        self.refuses({"inputs.owner-login": "", "github.actor": "", "github.triggering_actor": ""})
        self.refuses(
            {"inputs.owner-login": "-x", "github.actor": "-x", "github.triggering_actor": "-x"}
        )

    def test_a_malformed_step_or_word_is_refused(self) -> None:
        for step in ("", "Migrate", "a b", "x" * 33, "migrate;ls"):
            self.refuses({"inputs.step": step, "needs.guard.outputs.step": step})
        for word in ("", "ABC", "migrate-ops1", "A" * 33, "MIGRATE;ID"):
            self.refuses({"inputs.confirm": word})


class SendTests(unittest.TestCase):
    def send(
        self, *answers: tuple[int, str, str], context: dict[str, str] | None = None
    ) -> tuple[Result, Sandbox]:
        sandbox = Sandbox(self)
        sandbox.answers(*answers)
        result = sandbox.run(step_named("send", "Send the step"), context)
        return result, sandbox

    def good(self, context: dict[str, str] | None = None) -> tuple[Result, Sandbox]:
        return self.send((10, APPLIED, ""), (0, UNCHANGED, ""), context=context)

    def key_dir(self, sandbox: Sandbox) -> Path:
        return sandbox.temp / "ansible-step"

    def test_a_step_that_applies_and_then_changes_nothing_succeeds(self) -> None:
        result, sandbox = self.good()
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(len(sandbox.ssh_calls()), 2)
        self.assertIn(
            f"Step {STEP} at commit {SHA}: applied with 3 change(s), then a second run that changed nothing.",
            result.summary,
        )

    def test_a_step_that_was_already_done_succeeds(self) -> None:
        result, sandbox = self.send((0, UNCHANGED, ""), (0, UNCHANGED, ""))
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(len(sandbox.ssh_calls()), 2)

    def test_the_request_is_the_commit_the_step_and_the_word_to_the_one_host(self) -> None:
        _, sandbox = self.good()
        for call in sandbox.ssh_calls():
            self.assertTrue(call.endswith(f"-- {DEST} {SHA} {STEP} {WORD}"), call)

    def test_the_connection_is_strict_and_forwards_nothing(self) -> None:
        _, sandbox = self.good()
        call = sandbox.ssh_calls()[0]
        for option in (
            "-T",
            "IdentitiesOnly=yes",
            "BatchMode=yes",
            "StrictHostKeyChecking=yes",
            "ForwardAgent=no",
            "ClearAllForwardings=yes",
            "GlobalKnownHostsFile=/dev/null",
        ):
            self.assertIn(option, call)
        self.assertNotIn("ProxyJump", call)
        self.assertNotIn(" -F ", call)

    def test_each_call_is_limited_by_the_timeout_input(self) -> None:
        _, sandbox = self.good({"inputs.host-timeout-minutes": "7"})
        self.assertEqual(sandbox.stub_file("timeout.log").split(), ["7m", "7m"])

    def test_a_timeout_that_is_not_a_whole_number_of_minutes_is_refused(self) -> None:
        for minutes in ("", "0", "-1", "7;ls", "65 5", "100000"):
            result, sandbox = self.good({"inputs.host-timeout-minutes": minutes})
            self.assertNotEqual(result.code, 0, minutes)
            self.assertEqual(sandbox.ssh_calls(), [])

    def test_the_key_is_private_on_disk_never_an_argument_and_never_echoed(self) -> None:
        result, sandbox = self.good()
        self.assertEqual(result.code, 0)
        self.assertNotIn("STEPKEYLINE", "\n".join(sandbox.ssh_calls()))
        listing = sandbox.stub_file("ssh.workdir.1")
        self.assertRegex(listing, r"-rw-------[@+.]? .* id\n")
        self.assertRegex(listing, r"-rw-------[@+.]? .* known_hosts\n")
        visible = result.visible_log() + result.summary
        self.assertNotIn("STEPKEYLINE", visible)
        self.assertNotIn("BEGIN OPENSSH", visible)
        self.assertIn("::add-mask::STEPKEYLINEONE0123456789abcdef", result.log)
        self.assertIn("::add-mask::STEPKEYLINETWO0123456789abcdef", result.log)

    def test_the_key_is_removed_when_the_send_succeeds(self) -> None:
        result, sandbox = self.good()
        self.assertEqual(result.code, 0)
        self.assertFalse(self.key_dir(sandbox).exists())

    def test_the_key_is_removed_when_the_send_fails(self) -> None:
        result, sandbox = self.send(
            (20, host_line("refused", 20, changed="-", step="-", reason="confirm-mismatch"), "")
        )
        self.assertNotEqual(result.code, 0)
        self.assertEqual(len(sandbox.ssh_calls()), 1)
        self.assertFalse((self.key_dir(sandbox) / "id").exists())
        self.assertFalse((self.key_dir(sandbox) / "known_hosts").exists())

    def test_the_playbook_output_on_standard_error_is_not_in_the_log(self) -> None:
        result, _ = self.send(
            (10, APPLIED, "PLAYBOOK-OUTPUT-LINE\n"), (0, UNCHANGED, "PLAYBOOK-OUTPUT-LINE\n")
        )
        self.assertEqual(result.code, 0)
        self.assertNotIn("PLAYBOOK-OUTPUT-LINE", result.log)

    def test_an_empty_key_or_known_hosts_or_host_is_refused_before_any_call(self) -> None:
        for name in ("secrets.ssh_step_key", "secrets.ssh_known_hosts", "secrets.ssh_step_host"):
            result, sandbox = self.good({name: ""})
            self.assertNotEqual(result.code, 0, name)
            self.assertEqual(sandbox.ssh_calls(), [], name)

    def test_more_than_one_host_is_refused_and_none_is_called(self) -> None:
        for hosts in (
            f"{DEST}\nother@host.example.test",
            f"{DEST}\nother@host.example.test\n",
            f"{DEST}\n{DEST}",
        ):
            result, sandbox = self.good({"secrets.ssh_step_host": hosts})
            self.assertNotEqual(result.code, 0, hosts)
            self.assertEqual(sandbox.ssh_calls(), [], hosts)

    def test_a_host_that_is_not_user_at_host_is_refused(self) -> None:
        for host in (
            "root@",
            "@host.example.test",
            "a b@host.example.test",
            "x@host.example.test;id",
            f"{DEST} ",
            "ops1.example.test",
        ):
            result, sandbox = self.good({"secrets.ssh_step_host": host})
            self.assertNotEqual(result.code, 0, host)
            self.assertEqual(sandbox.ssh_calls(), [], host)

    def test_one_trailing_newline_after_the_one_host_is_accepted(self) -> None:
        result, _ = self.good({"secrets.ssh_step_host": f"{DEST}\n"})
        self.assertEqual(result.code, 0, result.log)

    def test_an_answer_for_apply_mode_is_not_read_as_a_step(self) -> None:
        hybrid = host_line("applied", 10, mode="apply").rstrip("\n") + f" step={STEP}\n"
        for out in (
            host_line("applied", 10, mode="apply"),
            host_line("applied", 10, mode="check"),
            hybrid,
        ):
            result, sandbox = self.send((10, out, ""), (0, UNCHANGED, ""))
            self.assertNotEqual(result.code, 0, out)
            self.assertEqual(len(sandbox.ssh_calls()), 1)

    def test_an_answer_with_no_step_field_is_refused(self) -> None:
        line = host_line("applied", 10).rsplit(" step=", 1)[0] + "\n"
        result, sandbox = self.send((10, line, ""), (0, UNCHANGED, ""))
        self.assertNotEqual(result.code, 0)
        self.assertEqual(len(sandbox.ssh_calls()), 1)

    def test_an_answer_that_names_another_step_is_refused(self) -> None:
        result, sandbox = self.send(
            (10, host_line("applied", 10, step="identity"), ""), (0, UNCHANGED, "")
        )
        self.assertNotEqual(result.code, 0)
        self.assertIn("another step", result.log)
        self.assertEqual(len(sandbox.ssh_calls()), 1)

    def test_an_answer_for_another_commit_or_exit_status_is_refused(self) -> None:
        for out, code in (
            (host_line("applied", 10, commit=OTHER_SHA), 10),
            (host_line("applied", 10), 0),
            (host_line("unchanged", 0), 10),
        ):
            result, _ = self.send((code, out, ""), (0, UNCHANGED, ""))
            self.assertNotEqual(result.code, 0, out)

    def test_an_answer_that_is_not_exactly_one_line_is_refused(self) -> None:
        for out in (
            "",
            APPLIED + APPLIED,
            "text\n" + APPLIED,
            "garbage\n",
            APPLIED.rstrip("\n") + " extra\n",
        ):
            result, sandbox = self.send((10, out, ""), (0, UNCHANGED, ""))
            self.assertNotEqual(result.code, 0, out)
            self.assertEqual(len(sandbox.ssh_calls()), 1)

    def test_a_refusal_or_a_failure_of_the_host_stops_the_run_with_its_reason(self) -> None:
        refusal = host_line("refused", 20, changed="-", step="-", reason="step-not-allowed")
        result, _ = self.send((20, refusal, ""))
        self.assertNotEqual(result.code, 0)
        self.assertIn("step-not-allowed", result.log)
        mismatch = host_line("refused", 20, changed="-", step=STEP, reason="confirm-mismatch")
        result, sandbox = self.send((20, mismatch, ""), (0, UNCHANGED, ""))
        self.assertNotEqual(result.code, 0)
        self.assertIn("verdict refused, reason confirm-mismatch, exit 20", result.log)
        self.assertEqual(len(sandbox.ssh_calls()), 1)
        for code, verdict in ((21, "busy"), (30, "failed"), (31, "timeout"), (40, "error")):
            result, sandbox = self.send((code, host_line(verdict, code, changed="-"), ""))
            self.assertNotEqual(result.code, 0, verdict)
            self.assertEqual(len(sandbox.ssh_calls()), 1)

    def test_a_failed_connection_stops_the_run(self) -> None:
        result, sandbox = self.send((255, "", "ssh: connect failed\n"))
        self.assertNotEqual(result.code, 0)
        self.assertIn("the SSH connection failed (exit 255)", result.log)
        self.assertEqual(len(sandbox.ssh_calls()), 1)

    def test_an_unchanged_answer_that_counts_a_change_is_refused(self) -> None:
        result, _ = self.send((0, host_line("unchanged", 0, changed=2), ""), (0, UNCHANGED, ""))
        self.assertNotEqual(result.code, 0)

    def test_an_applied_answer_with_no_count_is_refused(self) -> None:
        result, _ = self.send((10, host_line("applied", 10, changed="-"), ""), (0, UNCHANGED, ""))
        self.assertNotEqual(result.code, 0)

    def test_a_second_run_that_changes_something_is_refused(self) -> None:
        for second in (
            (10, APPLIED, ""),
            (0, host_line("unchanged", 0, changed=1), ""),
            (20, host_line("refused", 20, changed="-", step="-"), ""),
        ):
            result, sandbox = self.send((10, APPLIED, ""), second)
            self.assertNotEqual(result.code, 0, second)
            self.assertEqual(len(sandbox.ssh_calls()), 2)
            self.assertNotIn("second run that changed nothing.", result.summary)

    def test_a_second_answer_for_another_step_is_refused(self) -> None:
        result, _ = self.send(
            (10, APPLIED, ""), (0, host_line("unchanged", 0, changed=0, step="identity"), "")
        )
        self.assertNotEqual(result.code, 0)

    def test_one_jump_hop_has_a_stanza_and_a_key_of_its_own(self) -> None:
        result, sandbox = self.good(
            {"secrets.ssh_jump_host": JUMP, "secrets.ssh_jump_key": JUMP_KEY}
        )
        self.assertEqual(result.code, 0, result.log)
        call = sandbox.ssh_calls()[0]
        self.assertIn("ProxyJump=jump_hop ", call + " ")
        self.assertNotIn("jump_hop2", call)
        config = sandbox.stub_file("ssh.config.1")
        for line in (
            "Host jump_hop",
            "HostName jump.example.test",
            "User jumper",
            "IdentitiesOnly yes",
            "StrictHostKeyChecking yes",
            "ClearAllForwardings yes",
            "ForwardAgent no",
        ):
            self.assertIn(line, config)
        self.assertRegex(config, r"IdentityFile .*/jump_id\n")
        self.assertRegex(sandbox.stub_file("ssh.workdir.1"), r"-rw-------[@+.]? .* jump_id\n")
        self.assertNotIn(
            "JUMPKEYLINE", result.visible_log() + "\n".join(sandbox.ssh_calls()) + config
        )
        self.assertIn("::add-mask::JUMPKEYLINEONE0123456789abcdef", result.log)
        self.assertFalse(self.key_dir(sandbox).exists())

    def test_two_jump_hops_chain_in_order(self) -> None:
        context = {
            "secrets.ssh_jump_host": JUMP,
            "secrets.ssh_jump_key": JUMP_KEY,
            "secrets.ssh_jump2_host": JUMP2,
            "secrets.ssh_jump2_key": JUMP2_KEY,
        }
        result, sandbox = self.good(context)
        self.assertEqual(result.code, 0, result.log)
        self.assertIn("ProxyJump=jump_hop,jump_hop2", sandbox.ssh_calls()[0])
        config = sandbox.stub_file("ssh.config.1")
        self.assertLess(config.index("Host jump_hop\n"), config.index("Host jump_hop2\n"))
        self.assertRegex(config, r"IdentityFile .*/jump2_id\n")
        self.assertNotIn("JUMP2KEYLINE", result.visible_log())
        self.assertIn("::add-mask::JUMP2KEYLINEONE123456789abcdef", result.log)

    def test_a_half_configured_or_reused_hop_is_refused_before_any_call(self) -> None:
        jump = {"secrets.ssh_jump_host": JUMP, "secrets.ssh_jump_key": JUMP_KEY}
        both = {**jump, "secrets.ssh_jump2_host": JUMP2, "secrets.ssh_jump2_key": JUMP2_KEY}
        cases: list[tuple[dict[str, str], str]] = [
            ({"secrets.ssh_jump_host": JUMP}, "a jump needs both or neither"),
            ({"secrets.ssh_jump_key": JUMP_KEY}, "a jump needs both or neither"),
            (
                {**jump, "secrets.ssh_jump_host": "not a host"},
                "ssh_jump_host is not of the form user@host",
            ),
            ({**jump, "secrets.ssh_jump_key": KEY}, "each hop has its own key"),
            (
                {"secrets.ssh_jump2_host": JUMP2, "secrets.ssh_jump2_key": JUMP2_KEY},
                "A second hop needs ssh_jump_host",
            ),
            ({**jump, "secrets.ssh_jump2_host": JUMP2}, "a second hop needs both or neither"),
            ({**jump, "secrets.ssh_jump2_key": JUMP2_KEY}, "a second hop needs both or neither"),
            (
                {**both, "secrets.ssh_jump2_host": "x y@z"},
                "ssh_jump2_host is not of the form user@host",
            ),
            ({**both, "secrets.ssh_jump2_key": JUMP_KEY}, "each hop has its own key"),
            ({**both, "secrets.ssh_jump2_key": KEY}, "each hop has its own key"),
        ]
        for context, message in cases:
            result, sandbox = self.good(context)
            self.assertNotEqual(result.code, 0, context)
            self.assertIn(message, result.log, context)
            self.assertEqual(sandbox.ssh_calls(), [], context)
            self.assertFalse(self.key_dir(sandbox).exists(), context)


class ChecksTests(unittest.TestCase):
    def verdict(self, event: str, guard: str, send: str) -> int:
        context = {
            "github.event_name": event,
            "needs.guard.result": guard,
            "needs.send.result": send,
        }
        return Sandbox(self).run(step_named("checks", "Require every job"), context).code

    def test_a_dispatch_must_pass_the_guard_and_the_send(self) -> None:
        self.assertEqual(self.verdict("workflow_dispatch", "success", "success"), 0)
        for guard, send in (
            ("failure", "skipped"),
            ("success", "failure"),
            ("success", "skipped"),
            ("success", "cancelled"),
            ("cancelled", "skipped"),
            ("failure", "success"),
            ("skipped", "success"),
        ):
            self.assertNotEqual(self.verdict("workflow_dispatch", guard, send), 0, (guard, send))

    def test_any_other_event_must_never_send(self) -> None:
        self.assertEqual(self.verdict("push", "failure", "skipped"), 0)
        for send in ("success", "failure", "cancelled"):
            self.assertNotEqual(self.verdict("push", "success", send), 0, send)


class ApplyWorkflowUntouchedTests(unittest.TestCase):
    """The apply workflow keeps the three facts that made a step a workflow of its own."""

    text = APPLY_WORKFLOW.read_text()

    def test_the_apply_answer_pattern_still_pins_apply_mode(self) -> None:
        self.assertIn("mode=apply commit=([0-9a-f]{40})", self.text)

    def test_the_plan_and_apply_jobs_still_run_only_for_a_push(self) -> None:
        self.assertIn(
            '[ "$EVENT_NAME" = push ] || fail "The plan job runs only for a push, not for $EVENT_NAME."',
            self.text,
        )
        self.assertIn(
            '[ "$EVENT_NAME" = push ] || fail "The apply job runs only for a push, not for $EVENT_NAME."',
            self.text,
        )

    def test_the_apply_still_waits_for_a_plan_that_found_a_change(self) -> None:
        self.assertIn("needs.plan.outputs.has_changes != 'false'", self.text)

    def test_the_apply_workflow_knows_nothing_of_a_step(self) -> None:
        for word in (
            "mode=step",
            "control-plane-step",
            "workflow_dispatch",
            "ssh_step_key",
            "inputs.confirm",
            "owner-login",
        ):
            self.assertNotIn(word, self.text)


class DocumentationTests(unittest.TestCase):
    page = DOC_PAGE.read_text()

    def test_the_page_names_every_input_and_secret(self) -> None:
        names = [*TRIGGERS["workflow_call"]["inputs"], *TRIGGERS["workflow_call"]["secrets"]]
        for name in names:
            self.assertIn(f"`{name}`", self.page, name)

    def test_the_page_lists_the_owner_steps_by_name(self) -> None:
        for name in (
            "control-plane-step",
            "ssh_step_key",
            "ssh_step_host",
            "ssh_known_hosts",
            "STEP_OWNER_LOGIN",
            "authorized_keys",
        ):
            self.assertIn(name, self.page, name)

    def test_the_self_test_runs_these_tests_and_the_sabotage_proof(self) -> None:
        text = SELF_WORKFLOW.read_text()
        self.assertIn("test_ansible_gated_step.py", text)
        self.assertIn("tools/sabotage_step.py", text)
        self.assertEqual(
            yaml.safe_load(text).get("on", yaml.safe_load(text).get(True)).keys(),
            {"pull_request", "push"},
        )


if __name__ == "__main__":
    unittest.main()
