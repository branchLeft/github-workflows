"""Tests of ansible-gated-apply.yml.

The workflow keeps its logic in the `run:` text of its steps, so no file of this
repository is needed at run time. These tests take the text of a step by its id
and run it under bash with the step's environment supplied by the test and
`ssh`, `gh` and `timeout` replaced by stubs on PATH, so each test runs the text
that CI runs. ANSIBLE_GATED_APPLY_WORKFLOW names another copy of the workflow,
which is how a sabotage run points the tests at a deliberately broken copy.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = Path(os.environ.get("ANSIBLE_GATED_APPLY_WORKFLOW", REPO / ".github/workflows/ansible-gated-apply.yml"))
SHA = "a" * 40
OTHER_SHA = "b" * 40
HOST_ONE = "deploy@host-one"
HOST_TWO = "deploy@host-two"

DOC: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
JOBS: dict[str, dict[str, Any]] = DOC["jobs"]


def find_step(job: str, step_id: str) -> dict[str, Any]:
    for step in JOBS[job]["steps"]:
        if step.get("id") == step_id:
            return step
    raise KeyError(f"{job}/{step_id}")


def find_step_named(job: str, fragment: str) -> dict[str, Any]:
    for step in JOBS[job]["steps"]:
        if fragment in step.get("name", ""):
            return step
    raise KeyError(f"{job}/{fragment}")


SSH_STUB = """#!/usr/bin/env bash
set -eu
dest="${@: -2:1}"
sha="${@: -1}"
echo "$*" >> "$STUB_DIR/ssh.log"
count_file="$STUB_DIR/$dest.count"
n=$(( $(cat "$count_file" 2> /dev/null || echo 0) + 1 ))
echo "$n" > "$count_file"
base="$STUB_DIR/$dest"
if [ -f "$base.$n.rc" ]; then
  base="$base.$n"
fi
sed "s/@SHA@/$sha/g" "$base.out"
sed "s/@SHA@/$sha/g" "$base.err" >&2
exit "$(cat "$base.rc")"
"""

TIMEOUT_STUB = """#!/usr/bin/env bash
shift
exec "$@"
"""

GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/gh.log"
printf '%s\\n' "$GH_HEAD"
"""


def summary_line(verdict: str, code: int, mode: str, commit: str, changed: int, reason: str = "ok") -> str:
    return (
        f"pull-trigger verdict={verdict} exit={code} reason={reason} mode={mode} "
        f"commit={commit} playbook=ansible/playbooks/host.yml changed={changed}\n"
    )


class Result:
    def __init__(self, proc: subprocess.CompletedProcess[str], outputs: dict[str, str], summary: str) -> None:
        self.proc = proc
        self.code = proc.returncode
        self.log = proc.stdout + proc.stderr
        self.outputs = outputs
        self.summary = summary


class Sandbox:
    """A scratch directory with stubs on PATH and the files the runner provides."""

    def __init__(self, test: unittest.TestCase) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="gated-apply-test-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.stubs = self.dir / "stubs"
        self.temp = self.dir / "runner-temp"
        for path in (self.bin, self.stubs, self.temp):
            path.mkdir()
        for name, text in (("ssh", SSH_STUB), ("timeout", TIMEOUT_STUB), ("gh", GH_STUB)):
            target = self.bin / name
            target.write_text(text)
            target.chmod(0o755)
        self.output = self.dir / "github-output"
        self.summary = self.dir / "github-summary"
        self.output.write_text("")
        self.summary.write_text("")

    def host(self, dest: str, code: int, out: str, err: str = "", second: tuple[int, str, str] | None = None) -> None:
        self.stubs.joinpath(f"{dest}.rc").write_text(f"{code}\n")
        self.stubs.joinpath(f"{dest}.out").write_text(out)
        self.stubs.joinpath(f"{dest}.err").write_text(err)
        if second is not None:
            self.stubs.joinpath(f"{dest}.2.rc").write_text(f"{second[0]}\n")
            self.stubs.joinpath(f"{dest}.2.out").write_text(second[1])
            self.stubs.joinpath(f"{dest}.2.err").write_text(second[2])

    def ssh_log(self) -> list[str]:
        path = self.stubs / "ssh.log"
        return path.read_text().splitlines() if path.exists() else []

    def calls(self, dest: str) -> int:
        path = self.stubs / f"{dest}.count"
        return int(path.read_text()) if path.exists() else 0

    def run(self, step: dict[str, Any], values: dict[str, str], extra: dict[str, str] | None = None) -> Result:
        declared = set(step.get("env", {}))
        given = set(values)
        if declared != given:
            raise AssertionError(f"step env mismatch: missing {declared - given}, unexpected {given - declared}")
        script = self.dir / "step.sh"
        script.write_text(step["run"])
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "RUNNER_TEMP": str(self.temp),
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_STEP_SUMMARY": str(self.summary),
            "GITHUB_WORKSPACE": str(REPO),
            "STUB_DIR": str(self.stubs),
            **(extra or {}),
            **values,
        }
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            env=env,
            capture_output=True,
            text=True,
            cwd=self.dir,
            check=False,
        )
        outputs: dict[str, str] = {}
        for line in self.output.read_text().splitlines():
            key, _, value = line.partition("=")
            outputs[key] = value
        return Result(proc, outputs, self.summary.read_text())


def plan_guard_env(**changes: str) -> dict[str, str]:
    values = {
        "EVENT_NAME": "push",
        "REF": "refs/heads/main",
        "DEFAULT_BRANCH": "main",
        "IS_FORK": "false",
        "SHA": SHA,
        "ENVIRONMENT": "production",
        "HOSTS": f"{HOST_ONE}\n{HOST_TWO}\n",
        "DRY_RUN": "false",
        "DRY_RUN_STUB": "",
    }
    values.update(changes)
    return values


def digest(hosts: list[str]) -> str:
    return hashlib.sha256(" ".join(hosts).encode()).hexdigest()


def expected_ssh_call(dest: str, sha: str) -> list[str]:
    """The exact arguments the plan and apply steps pass to ssh, key and known-hosts paths named."""
    return [
        "-T", "-i", "KEY", "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes", "-o", "UserKnownHostsFile=KNOWN",
        "-o", "GlobalKnownHostsFile=/dev/null", "-o", "ConnectTimeout=30",
        "-o", "ServerAliveInterval=30", "-o", "ForwardAgent=no", "-o", "ClearAllForwardings=yes",
        "--", dest, sha,
    ]


def ssh_call_with_paths_named(line: str) -> list[str]:
    tokens = line.split(" ")
    if "-i" in tokens:
        tokens[tokens.index("-i") + 1] = "KEY"
    return ["UserKnownHostsFile=KNOWN" if token.startswith("UserKnownHostsFile=") else token for token in tokens]


SELF_WORKFLOW = REPO / ".github/workflows/ansible-gated-apply-self.yml"
APPLY_IF = (
    "needs.discover.outputs.delivery == 'true' && needs.plan.result == 'success' "
    "&& needs.plan.outputs.has_changes != 'false' && inputs.dry-run == false "
    "&& inputs.environment != ''"
)
# Every job of each workflow, with its job-level `if` and its ordered steps as
# (name, `if`) pairs. A job with no steps is a reusable-workflow call.
GATED_TABLE: dict[str, tuple[str | None, list[tuple[str, str | None]] | None]] = {
    "discover": (None, [
        ("Checkout", None),
        ("Decide whether this run may reach a host", None),
        ("List the Molecule shards from the repository tree", None),
    ]),
    "lint": (None, [
        ("Checkout", None),
        ("Create the tool environment", None),
        ("Install the pinned tools", None),
        ("Install the pinned collections", None),
        ("yamllint every YAML file under the Ansible directory", None),
        ("ansible-lint at the production profile", None),
        ("Type-check the Python", None),
        ("Lint the Python", None),
        ("Check the Python formatting", None),
    ]),
    "shard": (None, [
        ("Checkout", None),
        ("Refuse a redirected container engine", None),
        ("Create the tool environment", None),
        ("Install the pinned tools", None),
        ("Install the pinned collections", None),
        ("Molecule default sequence, with the idempotence step", None),
        ("Molecule check action", None),
    ]),
    "plan": ("needs.discover.outputs.delivery == 'true'", [
        ("Checkout", "inputs.dry-run == true"),
        ("Refuse outside a push to the default branch, and check the inputs", None),
        ("Check every host in check mode", None),
        ("Judge the answers, scan the diff and write the plan", None),
    ]),
    "apply": (APPLY_IF, [
        ("Refuse unless this is the approved, planned push", None),
        ("Refuse if the default branch has moved since the plan", None),
        ("Apply on each host, then run it again to prove nothing is left to change", None),
    ]),
    "checks": ("always()", [
        ("Require every job to have ended as expected", None),
    ]),
}
SELF_TABLE: dict[str, tuple[str | None, list[tuple[str, str | None]] | None]] = {
    "unit-tests": (None, [
        ("Checkout", None),
        ("Run the tests of the workflow's steps", None),
    ]),
    "gated-apply": (None, None),
}
# A bare constant false: `${{ false }}`, or `false` as a whole operand of && or ||.
# The comparison `inputs.dry-run == false` is not a bare constant and is allowed.
BARE_FALSE = re.compile(r"\$\{\{\s*false\s*\}\}|(^|[&|(]\s*)false(\s*$|\s*[&|)])")


def normalised_if(node: Any) -> str | None:
    return None if node is None else " ".join(str(node).split())


class StructureTests(unittest.TestCase):
    def test_every_job_and_step_of_both_workflows_is_exactly_the_expected_table(self) -> None:
        self_doc: dict[Any, Any] = yaml.safe_load(SELF_WORKFLOW.read_text())
        for label, doc, table in (("gated", DOC, GATED_TABLE), ("self", self_doc, SELF_TABLE)):
            jobs: dict[str, dict[str, Any]] = doc["jobs"]
            with self.subTest(workflow=label):
                self.assertEqual(set(jobs), set(table))
            for name, (job_if, steps) in table.items():
                job = jobs.get(name, {})
                with self.subTest(workflow=label, job=name):
                    self.assertEqual(normalised_if(job.get("if")), job_if)
                    self.assertNotIn("continue-on-error", job)
                    if steps is None:
                        self.assertIn("uses", job)
                        self.assertNotIn("steps", job)
                        continue
                    actual = [(step.get("name"), normalised_if(step.get("if"))) for step in job.get("steps", [])]
                    self.assertEqual(actual, steps)
                    for step in job["steps"]:
                        self.assertNotIn("continue-on-error", step, step.get("name"))
            for name, job in jobs.items():
                texts = [job.get("if")] + [step.get("if") for step in job.get("steps", [])]
                for text in texts:
                    if text is not None:
                        with self.subTest(workflow=label, job=name, condition=str(text)):
                            self.assertNotRegex(str(text), BARE_FALSE)

    def test_the_only_trigger_is_workflow_call(self) -> None:
        self.assertEqual(set(DOC[True]), {"workflow_call"})

    def test_no_pull_request_target_and_no_continue_on_error(self) -> None:
        text = WORKFLOW.read_text()
        self.assertNotIn("pull_request_target", text)
        self.assertNotIn("continue-on-error", text)

    def test_permissions_are_read_only_everywhere(self) -> None:
        self.assertEqual(DOC["permissions"], {"contents": "read"})
        for name, job in JOBS.items():
            self.assertNotIn("permissions", job, name)
        self.assertNotRegex(WORKFLOW.read_text(), r"(?m)^\s*[a-z-]+:\s*write\s*$")

    def test_every_action_is_pinned_to_a_commit_with_a_version_comment(self) -> None:
        uses = re.findall(r"(?m)^\s*(?:- )?uses:\s*(\S+)(.*)$", WORKFLOW.read_text())
        self.assertTrue(uses)
        for target, rest in uses:
            self.assertRegex(target, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
            self.assertRegex(rest, r"^\s+# v\d")

    def test_every_job_sets_a_timeout(self) -> None:
        for name, job in JOBS.items():
            self.assertIn("timeout-minutes", job, name)

    def test_no_expression_inside_a_run_body(self) -> None:
        for name, job in JOBS.items():
            for step in job["steps"]:
                self.assertNotIn("${{", step.get("run", ""), f"{name}/{step.get('name')}")

    def test_the_apply_job_is_gated(self) -> None:
        job = JOBS["apply"]
        self.assertEqual(job["environment"], "${{ inputs.environment }}")
        self.assertIn("plan", job["needs"])
        condition = " ".join(str(job["if"]).split())
        for required in (
            "needs.discover.outputs.delivery == 'true'",
            "needs.plan.result == 'success'",
            "needs.plan.outputs.has_changes != 'false'",
            "inputs.dry-run == false",
        ):
            self.assertIn(required, condition)
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_the_plan_job_runs_only_for_delivery(self) -> None:
        self.assertEqual(JOBS["plan"]["if"], "needs.discover.outputs.delivery == 'true'")
        self.assertNotIn("environment", JOBS["plan"])

    def test_each_key_is_read_by_one_job_only(self) -> None:
        for name, job in JOBS.items():
            text = json.dumps(job)
            self.assertEqual("secrets.ssh_apply_key" in text, name == "apply", name)
            self.assertEqual("secrets.ssh_check_key" in text, name == "plan", name)

    def test_the_gate_job_is_named_for_callers_and_needs_every_job(self) -> None:
        job = JOBS["checks"]
        self.assertEqual(job["name"], "Ansible checks passed")
        self.assertEqual(job["if"], "always()")
        self.assertEqual(set(job["needs"]), set(JOBS) - {"checks"})

    def test_each_expression_that_feeds_a_gate_or_apply_step_comes_from_its_expected_source(self) -> None:
        cases = [
            ("checks", "Require every job", {
                "DISCOVER": "${{ needs.discover.result }}",
                "DELIVERY": "${{ needs.discover.outputs.delivery }}",
                "LINT": "${{ needs.lint.result }}",
                "SHARD": "${{ needs.shard.result }}",
                "PLAN": "${{ needs.plan.result }}",
                "APPLY": "${{ needs.apply.result }}",
                "HAS_CHANGES": "${{ needs.plan.outputs.has_changes }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
            }),
            ("plan", "Refuse outside a push", {
                "EVENT_NAME": "${{ github.event_name }}",
                "REF": "${{ github.ref }}",
                "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
                "IS_FORK": "${{ github.event.repository.fork }}",
                "SHA": "${{ github.sha }}",
                "ENVIRONMENT": "${{ inputs.environment }}",
                "HOSTS": "${{ secrets.ssh_hosts }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "DRY_RUN_STUB": "${{ inputs.dry-run-stub }}",
            }),
            ("plan", "Check every host", {
                "HOSTS": "${{ secrets.ssh_hosts }}",
                "SHA": "${{ steps.guard.outputs.sha }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "DRY_RUN_STUB": "${{ inputs.dry-run-stub }}",
                "WORKING_DIRECTORY": "${{ inputs.working-directory }}",
                "HOST_TIMEOUT_MINUTES": "${{ inputs.host-timeout-minutes }}",
                "SSH_KEY": "${{ secrets.ssh_check_key }}",
                "KNOWN_HOSTS": "${{ secrets.ssh_known_hosts }}",
            }),
            ("plan", "Judge the answers", {
                "SHA": "${{ steps.guard.outputs.sha }}",
                "ENVIRONMENT": "${{ inputs.environment }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "SSH_KEY": "${{ secrets.ssh_check_key }}",
            }),
            ("apply", "Refuse unless this is the approved", {
                "EVENT_NAME": "${{ github.event_name }}",
                "REF": "${{ github.ref }}",
                "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
                "IS_FORK": "${{ github.event.repository.fork }}",
                "SHA": "${{ github.sha }}",
                "PLANNED_SHA": "${{ needs.plan.outputs.sha }}",
                "HAS_CHANGES": "${{ needs.plan.outputs.has_changes }}",
                "PLANNED_DIGEST": "${{ needs.plan.outputs.hosts_digest }}",
                "HOSTS": "${{ secrets.ssh_hosts }}",
                "ENVIRONMENT": "${{ inputs.environment }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
            }),
            ("apply", "default branch has moved", {
                "GH_TOKEN": "${{ github.token }}",
                "REPO": "${{ github.repository }}",
                "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
                "PLANNED_SHA": "${{ needs.plan.outputs.sha }}",
            }),
            ("apply", "Apply on each host", {
                "HOSTS": "${{ secrets.ssh_hosts }}",
                "SHA": "${{ needs.plan.outputs.sha }}",
                "HOST_TIMEOUT_MINUTES": "${{ inputs.host-timeout-minutes }}",
                "SSH_KEY": "${{ secrets.ssh_apply_key }}",
                "KNOWN_HOSTS": "${{ secrets.ssh_known_hosts }}",
            }),
        ]
        for job, fragment, expected in cases:
            with self.subTest(job=job, step=fragment):
                self.assertEqual(find_step_named(job, fragment).get("env"), expected)

    def test_the_apply_job_steps_are_the_guard_then_the_head_check_then_the_apply_with_no_if(self) -> None:
        steps = JOBS["apply"]["steps"]
        self.assertEqual(
            [step.get("name") for step in steps],
            [
                "Refuse unless this is the approved, planned push",
                "Refuse if the default branch has moved since the plan",
                "Apply on each host, then run it again to prove nothing is left to change",
            ],
        )
        for step in steps:
            self.assertNotIn("if", step, step.get("name"))


class PlanGuardTests(unittest.TestCase):
    def guard(self, **changes: str) -> Result:
        return Sandbox(self).run(find_step("plan", "guard"), plan_guard_env(**changes))

    def test_a_push_to_the_default_branch_passes_and_publishes_the_commit_and_the_host_digest(self) -> None:
        result = self.guard()
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["sha"], SHA)
        self.assertEqual(result.outputs["hosts_digest"], digest([HOST_ONE, HOST_TWO]))
        self.assertNotIn(HOST_ONE, result.log)

    def test_a_pull_request_is_refused(self) -> None:
        result = self.guard(EVENT_NAME="pull_request")
        self.assertNotEqual(result.code, 0)
        self.assertIn("only for a push", result.log)

    def test_another_branch_is_refused(self) -> None:
        result = self.guard(REF="refs/heads/feature")
        self.assertNotEqual(result.code, 0)
        self.assertIn("default branch", result.log)

    def test_a_fork_is_refused(self) -> None:
        result = self.guard(IS_FORK="true")
        self.assertNotEqual(result.code, 0)
        self.assertIn("fork", result.log)

    def test_a_commit_that_is_not_forty_lowercase_hex_digits_is_refused(self) -> None:
        for sha in ("main", "A" * 40, "a" * 39, ""):
            self.assertNotEqual(self.guard(SHA=sha).code, 0, sha)

    def test_an_empty_environment_is_refused_because_it_would_have_no_approval(self) -> None:
        result = self.guard(ENVIRONMENT="")
        self.assertNotEqual(result.code, 0)
        self.assertIn("no approval", result.log)

    def test_hosts_must_be_listed_valid_and_unique(self) -> None:
        for hosts in ("", "\n\n", "root@host-one;id", "host-one", "Deploy@host-one", f"{HOST_ONE}\n{HOST_ONE}", "a@b c"):
            self.assertNotEqual(self.guard(HOSTS=hosts).code, 0, repr(hosts))

    def test_a_dry_run_needs_its_stand_in(self) -> None:
        self.assertNotEqual(self.guard(DRY_RUN="true").code, 0)
        self.assertEqual(self.guard(DRY_RUN="true", DRY_RUN_STUB="stub/host.sh").code, 0)


def check_env(**changes: str) -> dict[str, str]:
    values = {
        "HOSTS": f"{HOST_ONE}\n{HOST_TWO}\n",
        "SHA": SHA,
        "DRY_RUN": "false",
        "DRY_RUN_STUB": "",
        "WORKING_DIRECTORY": ".",
        "HOST_TIMEOUT_MINUTES": "65",
        "SSH_KEY": "FAKE_PRIVATE_KEY_BODY_LINE_ONE_0123456789",
        "KNOWN_HOSTS": "host-one ssh-ed25519 PUBLIC",
    }
    values.update(changes)
    return values


def report_env(**changes: str) -> dict[str, str]:
    values = {
        "SHA": SHA,
        "ENVIRONMENT": "production",
        "DRY_RUN": "false",
        "SSH_KEY": "FAKE_PRIVATE_KEY_BODY_LINE_ONE_0123456789",
    }
    values.update(changes)
    return values


CLEAN = summary_line("unchanged", 0, "check", "@SHA@", 0)
WOULD_CHANGE = summary_line("would-change", 11, "check", "@SHA@", 2)


class PlanTests(unittest.TestCase):
    def plan(self, sandbox: Sandbox, **report_changes: str) -> tuple[Result, Result]:
        checked = sandbox.run(find_step("plan", "check"), check_env())
        self.assertEqual(checked.code, 0, checked.log)
        return checked, sandbox.run(find_step("plan", "report"), report_env(**report_changes))

    def test_every_host_clean_gives_no_changes_and_a_first_line_that_says_the_apply_is_skipped(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 0, CLEAN)
        sandbox.host(HOST_TWO, 0, CLEAN)
        _, result = self.plan(sandbox)
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["has_changes"], "false")
        first = result.summary.splitlines()[0]
        self.assertTrue(first.startswith("No changes:"), first)
        self.assertIn("apply is skipped", first)

    def test_one_host_with_changes_gives_has_changes_and_shows_its_diff(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 0, CLEAN)
        sandbox.host(HOST_TWO, 11, WOULD_CHANGE, "--- before\n+++ after\n-greeting = OLD\n+greeting = NEW\n")
        _, result = self.plan(sandbox)
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["has_changes"], "true")
        self.assertTrue(result.summary.splitlines()[0].startswith("Changes: 1 of 2"))
        self.assertIn("+greeting = NEW", result.summary)
        self.assertNotIn("+greeting = NEW", result.log)

    def test_the_ssh_call_is_the_restricted_route_and_nothing_else(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 0, CLEAN)
        sandbox.host(HOST_TWO, 0, CLEAN)
        self.plan(sandbox)
        first = sandbox.ssh_log()[0]
        for option in ("-T", "BatchMode=yes", "StrictHostKeyChecking=yes", "IdentitiesOnly=yes", "ForwardAgent=no", "ClearAllForwardings=yes"):
            self.assertIn(option, first)
        self.assertTrue(first.endswith(f"-- {HOST_ONE} {SHA}"), first)
        self.assertEqual(len(sandbox.ssh_log()), 2)
        self.assertFalse((sandbox.temp / "ansible-plan" / "id").exists())

    def test_a_host_that_fails_fails_the_job_and_asks_for_no_approval(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 0, CLEAN)
        sandbox.host(HOST_TWO, 30, summary_line("failed", 30, "check", "@SHA@", 0, "playbook-failed"), "fatal: a task failed\n")
        _, result = self.plan(sandbox)
        self.assertNotEqual(result.code, 0)
        self.assertEqual(result.outputs["has_changes"], "true")
        self.assertTrue(result.summary.splitlines()[0].startswith("Plan failed:"))

    def test_a_refused_request_and_a_busy_host_are_failures(self) -> None:
        for code, verdict in ((20, "refused"), (21, "busy"), (40, "error")):
            sandbox = Sandbox(self)
            sandbox.host(HOST_ONE, code, summary_line(verdict, code, "check", "@SHA@", 0, "not-on-main"))
            sandbox.host(HOST_TWO, 0, CLEAN)
            _, result = self.plan(sandbox)
            self.assertNotEqual(result.code, 0, verdict)

    def test_a_dropped_connection_is_a_failure_and_its_text_is_not_shown(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 255, "", "ssh: connect to host host-one port 22: refused\n")
        sandbox.host(HOST_TWO, 0, CLEAN)
        _, result = self.plan(sandbox)
        self.assertNotEqual(result.code, 0)
        self.assertNotIn("host-one", result.summary)
        self.assertNotIn("refused", result.summary)

    def test_an_answer_that_is_not_one_summary_line_for_this_commit_is_a_failure(self) -> None:
        bad_answers = [
            "",
            CLEAN + CLEAN,
            "hello\n",
            summary_line("unchanged", 0, "check", OTHER_SHA, 0),
            summary_line("unchanged", 0, "apply", "@SHA@", 0),
            summary_line("unchanged", 0, "check", "@SHA@", 3),
            summary_line("unchanged", 11, "check", "@SHA@", 0),
        ]
        for answer in bad_answers:
            sandbox = Sandbox(self)
            sandbox.host(HOST_ONE, 0, answer)
            sandbox.host(HOST_TWO, 0, CLEAN)
            _, result = self.plan(sandbox)
            self.assertNotEqual(result.code, 0, repr(answer))
            self.assertEqual(result.outputs["has_changes"], "true", repr(answer))

    def test_a_clean_exit_with_changes_counted_is_never_no_changes(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 0, summary_line("unchanged", 0, "check", "@SHA@", 1))
        sandbox.host(HOST_TWO, 0, CLEAN)
        _, result = self.plan(sandbox)
        self.assertNotEqual(result.outputs["has_changes"], "false")

    def test_host_names_are_replaced_by_a_label_even_when_the_name_is_inside_the_label(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host("deploy@host", 11, WOULD_CHANGE, "connecting to deploy@host as deploy: host is changing\n")
        checked = sandbox.run(find_step("plan", "check"), check_env(HOSTS="deploy@host\n"))
        self.assertEqual(checked.code, 0, checked.log)
        result = sandbox.run(find_step("plan", "report"), report_env())
        self.assertEqual(result.code, 0, result.log)
        self.assertIn("connecting to host 1 as deploy: host 1 is changing", result.summary)

    def test_a_secret_looking_string_in_the_diff_is_withheld_and_fails_the_job(self) -> None:
        samples = {
            "credential-assignment": "+api_token = 0123456789abcdef\n",
            "private-key": "+-----BEGIN OPENSSH PRIVATE KEY-----\n",
            "aws-access-key": "+id: " + "AKIA" + "ABCDEFGHIJKLMNOP\n",
            "github-token": "+x: ghp_abcdefghijklmnopqrstuvwxyz0123456789\n",
            "jwt": "+x: eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.\n",
            "ci-key-material": "+x: FAKE_PRIVATE_KEY_BODY_LINE_ONE_0123456789\n",
        }
        for name, line in samples.items():
            sandbox = Sandbox(self)
            sandbox.host(HOST_ONE, 11, WOULD_CHANGE, "--- before\n" + line)
            sandbox.host(HOST_TWO, 0, CLEAN)
            _, result = self.plan(sandbox)
            self.assertNotEqual(result.code, 0, name)
            self.assertIn(name, result.summary, name)
            self.assertNotIn(line.strip().lstrip("+"), result.summary + result.log, name)
            self.assertNotIn("--- before", result.summary, name)

    def test_a_diff_too_large_to_show_whole_fails_the_job(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 11, WOULD_CHANGE, "+line of changed text\n" * 20000)
        sandbox.host(HOST_TWO, 0, CLEAN)
        _, result = self.plan(sandbox)
        self.assertNotEqual(result.code, 0)
        self.assertIn("too large", result.summary)
        self.assertNotIn("+line of changed text", result.summary)

    def test_control_characters_are_removed_and_a_backtick_fence_cannot_be_closed_early(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 11, WOULD_CHANGE, "+red \x1b[31mtext\x1b[0m\n+````\n+```` injected\n")
        sandbox.host(HOST_TWO, 0, CLEAN)
        _, result = self.plan(sandbox)
        self.assertEqual(result.code, 0, result.log)
        self.assertNotIn("\x1b", result.summary)
        self.assertIn("`````text", result.summary)

    def test_a_dry_run_calls_the_stand_in_and_never_ssh(self) -> None:
        sandbox = Sandbox(self)
        stub = "stub/host.sh"
        values = check_env(
            DRY_RUN="true",
            DRY_RUN_STUB=stub,
            WORKING_DIRECTORY=".github/fixtures/ansible-gated-apply",
            HOSTS="ci@first-stub-host\nci@second-stub-host\n",
            SSH_KEY="",
            KNOWN_HOSTS="",
        )
        checked = sandbox.run(find_step("plan", "check"), values)
        self.assertEqual(checked.code, 0, checked.log)
        self.assertEqual(sandbox.ssh_log(), [])
        result = sandbox.run(find_step("plan", "report"), report_env(DRY_RUN="true", SSH_KEY=""))
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["has_changes"], "true")
        self.assertTrue(result.summary.startswith("DRY RUN"), result.summary)

    def test_a_real_run_needs_both_secrets(self) -> None:
        for name in ("SSH_KEY", "KNOWN_HOSTS"):
            sandbox = Sandbox(self)
            result = sandbox.run(find_step("plan", "check"), check_env(**{name: ""}))
            self.assertNotEqual(result.code, 0, name)
            self.assertEqual(sandbox.ssh_log(), [])


def apply_guard_env(**changes: str) -> dict[str, str]:
    values = {
        "EVENT_NAME": "push",
        "REF": "refs/heads/main",
        "DEFAULT_BRANCH": "main",
        "IS_FORK": "false",
        "SHA": SHA,
        "PLANNED_SHA": SHA,
        "HAS_CHANGES": "true",
        "PLANNED_DIGEST": digest([HOST_ONE, HOST_TWO]),
        "HOSTS": f"{HOST_ONE}\n{HOST_TWO}\n",
        "ENVIRONMENT": "production",
        "DRY_RUN": "false",
    }
    values.update(changes)
    return values


class ApplyGuardTests(unittest.TestCase):
    def guard(self, **changes: str) -> Result:
        return Sandbox(self).run(find_step("apply", "guard"), apply_guard_env(**changes))

    def test_the_planned_push_passes(self) -> None:
        result = self.guard()
        self.assertEqual(result.code, 0, result.log)

    def test_the_apply_refuses_in_a_pull_request_in_a_fork_and_in_a_dry_run(self) -> None:
        self.assertNotEqual(self.guard(EVENT_NAME="pull_request").code, 0)
        self.assertNotEqual(self.guard(REF="refs/pull/1/merge").code, 0)
        self.assertNotEqual(self.guard(IS_FORK="true").code, 0)
        self.assertNotEqual(self.guard(DRY_RUN="true").code, 0)

    def test_the_apply_refuses_without_an_environment(self) -> None:
        result = self.guard(ENVIRONMENT="")
        self.assertNotEqual(result.code, 0)
        self.assertIn("no approval", result.log)

    def test_the_apply_refuses_a_plan_with_nothing_to_change(self) -> None:
        self.assertNotEqual(self.guard(HAS_CHANGES="false").code, 0)

    def test_the_apply_refuses_a_commit_other_than_the_planned_one(self) -> None:
        result = self.guard(PLANNED_SHA=OTHER_SHA)
        self.assertNotEqual(result.code, 0)
        self.assertIn("differs", result.log)
        self.assertNotEqual(self.guard(PLANNED_SHA="").code, 0)

    def test_the_apply_refuses_hosts_that_were_not_planned(self) -> None:
        self.assertNotEqual(self.guard(HOSTS=f"{HOST_ONE}\ndeploy@host-three\n").code, 0)
        self.assertNotEqual(self.guard(PLANNED_DIGEST="").code, 0)


class HeadMovedTests(unittest.TestCase):
    def head_check(self, head: str) -> Result:
        sandbox = Sandbox(self)
        step = find_step_named("apply", "default branch has moved")
        values = {
            "GH_TOKEN": "TOKEN_FOR_TEST",
            "REPO": "org/repo",
            "DEFAULT_BRANCH": "main",
            "PLANNED_SHA": SHA,
        }
        return sandbox.run(step, values, {"GH_HEAD": head})

    def test_a_branch_head_that_moved_stops_the_apply(self) -> None:
        result = self.head_check(OTHER_SHA)
        self.assertNotEqual(result.code, 0)
        self.assertIn("moved on", result.log)

    def test_an_unmoved_head_lets_it_continue(self) -> None:
        self.assertEqual(self.head_check(SHA).code, 0)


def apply_env(**changes: str) -> dict[str, str]:
    values = {
        "HOSTS": f"{HOST_ONE}\n{HOST_TWO}\n",
        "SHA": SHA,
        "HOST_TIMEOUT_MINUTES": "65",
        "SSH_KEY": "FAKE_PRIVATE_KEY_BODY_LINE_ONE_0123456789",
        "KNOWN_HOSTS": "host-one ssh-ed25519 PUBLIC",
    }
    values.update(changes)
    return values


APPLIED = summary_line("applied", 10, "apply", "@SHA@", 2)
UNCHANGED = summary_line("unchanged", 0, "apply", "@SHA@", 0)


class ApplyTests(unittest.TestCase):
    def apply(self, sandbox: Sandbox, **changes: str) -> Result:
        return sandbox.run(find_step_named("apply", "Apply on each host"), apply_env(**changes))

    def test_apply_then_a_clean_second_run_succeeds_on_every_host(self) -> None:
        sandbox = Sandbox(self)
        for dest in (HOST_ONE, HOST_TWO):
            sandbox.host(dest, 10, APPLIED, "playbook output\n", second=(0, UNCHANGED, ""))
        result = self.apply(sandbox)
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(sandbox.calls(HOST_ONE), 2)
        self.assertEqual(sandbox.calls(HOST_TWO), 2)
        self.assertNotIn("playbook output", result.log + result.summary)
        self.assertNotIn(HOST_ONE, result.log + result.summary)
        self.assertFalse((sandbox.temp / "ansible-apply" / "id").exists())

    def test_a_host_that_needs_a_second_change_fails_the_job(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 10, APPLIED, second=(10, APPLIED, ""))
        result = self.apply(sandbox)
        self.assertNotEqual(result.code, 0)
        self.assertIn("second run", result.log)

    def test_a_first_or_second_run_that_counts_a_change_while_saying_unchanged_fails_the_job(self) -> None:
        cases = {
            "the first run says unchanged and counts one change": (0, summary_line("unchanged", 0, "apply", "@SHA@", 1), None),
            "the second run says unchanged and counts one change": (10, APPLIED, (0, summary_line("unchanged", 0, "apply", "@SHA@", 1), "")),
            "the second run exits 11": (10, APPLIED, (11, summary_line("would-change", 11, "apply", "@SHA@", 2), "")),
            "the second run exits 10": (10, APPLIED, (10, APPLIED, "")),
        }
        for name, (code, out, second) in cases.items():
            with self.subTest(name):
                sandbox = Sandbox(self)
                sandbox.host(HOST_ONE, code, out, second=second)
                result = self.apply(sandbox, HOSTS=f"{HOST_ONE}\n")
                self.assertNotEqual(result.code, 0, result.log)
                self.assertNotIn("Applied commit", result.log)

    def test_the_first_failure_stops_the_run_before_the_next_host(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 30, summary_line("failed", 30, "apply", "@SHA@", 0, "playbook-failed"))
        sandbox.host(HOST_TWO, 10, APPLIED, second=(0, UNCHANGED, ""))
        result = self.apply(sandbox)
        self.assertNotEqual(result.code, 0)
        self.assertEqual(sandbox.calls(HOST_TWO), 0)

    def test_an_answer_for_another_mode_or_commit_is_refused(self) -> None:
        for answer in (summary_line("applied", 10, "check", "@SHA@", 2), summary_line("applied", 10, "apply", OTHER_SHA, 2), "nothing\n"):
            sandbox = Sandbox(self)
            sandbox.host(HOST_ONE, 10, answer, second=(0, UNCHANGED, ""))
            self.assertNotEqual(self.apply(sandbox).code, 0, repr(answer))

    def test_a_dropped_connection_fails_the_job(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 255, "", "ssh: host-one unreachable\n")
        result = self.apply(sandbox)
        self.assertNotEqual(result.code, 0)
        self.assertNotIn("unreachable", result.log)

    def test_the_apply_key_secret_must_be_present(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 10, APPLIED, second=(0, UNCHANGED, ""))
        self.assertNotEqual(self.apply(sandbox, SSH_KEY="").code, 0)
        self.assertEqual(sandbox.ssh_log(), [])


def gate_env(**changes: str) -> dict[str, str]:
    values = {
        "DISCOVER": "success",
        "DELIVERY": "false",
        "LINT": "success",
        "SHARD": "success",
        "PLAN": "skipped",
        "APPLY": "skipped",
        "HAS_CHANGES": "",
        "DRY_RUN": "false",
    }
    values.update(changes)
    return values


class GateTests(unittest.TestCase):
    def gate(self, **changes: str) -> int:
        step = JOBS["checks"]["steps"][0]
        return Sandbox(self).run(step, gate_env(**changes)).code

    def test_a_pull_request_with_every_check_green_passes(self) -> None:
        self.assertEqual(self.gate(), 0)

    def test_a_failed_lint_or_molecule_job_makes_the_gate_red(self) -> None:
        self.assertNotEqual(self.gate(LINT="failure"), 0)
        self.assertNotEqual(self.gate(SHARD="failure"), 0)
        self.assertNotEqual(self.gate(SHARD="skipped"), 0)
        self.assertNotEqual(self.gate(DISCOVER="failure"), 0)

    def test_a_plan_or_apply_that_ran_outside_a_delivery_run_makes_the_gate_red(self) -> None:
        self.assertNotEqual(self.gate(PLAN="success"), 0)
        self.assertNotEqual(self.gate(APPLY="success"), 0)

    def test_a_delivery_run_needs_a_green_plan_and_an_apply_that_matches_the_plan(self) -> None:
        delivery = {"DELIVERY": "true", "PLAN": "success"}
        self.assertEqual(self.gate(**delivery, HAS_CHANGES="true", APPLY="success"), 0)
        self.assertEqual(self.gate(**delivery, HAS_CHANGES="false", APPLY="skipped"), 0)
        self.assertNotEqual(self.gate(**delivery, HAS_CHANGES="true", APPLY="skipped"), 0)
        self.assertNotEqual(self.gate(**delivery, HAS_CHANGES="true", APPLY="failure"), 0)
        self.assertNotEqual(self.gate(DELIVERY="true", PLAN="failure", APPLY="skipped"), 0)
        self.assertEqual(self.gate(**delivery, DRY_RUN="true", HAS_CHANGES="true", APPLY="skipped"), 0)
        self.assertNotEqual(self.gate(**delivery, DRY_RUN="true", HAS_CHANGES="true", APPLY="success"), 0)

    def test_an_unknown_delivery_answer_is_red(self) -> None:
        self.assertNotEqual(self.gate(DELIVERY=""), 0)


class SshOptionTests(unittest.TestCase):
    def test_the_plan_and_apply_calls_pass_the_same_exact_ssh_options(self) -> None:
        planned = Sandbox(self)
        planned.host(HOST_ONE, 0, CLEAN)
        planned.host(HOST_TWO, 0, CLEAN)
        checked = planned.run(find_step_named("plan", "Check every host"), check_env())
        self.assertEqual(checked.code, 0, checked.log)
        applied = Sandbox(self)
        for dest in (HOST_ONE, HOST_TWO):
            applied.host(dest, 10, APPLIED, second=(0, UNCHANGED, ""))
        result = applied.run(find_step_named("apply", "Apply on each host"), apply_env())
        self.assertEqual(result.code, 0, result.log)
        calls = planned.ssh_log() + applied.ssh_log()
        self.assertEqual(len(calls), 6)
        for call in calls:
            dest = call.split(" ")[-2]
            self.assertIn(dest, (HOST_ONE, HOST_TWO))
            tokens = call.split(" ")
            self.assertTrue(tokens[tokens.index("-i") + 1].endswith("/id"), call)
            known_hosts = [token for token in tokens if token.startswith("UserKnownHostsFile=")]
            self.assertEqual(len(known_hosts), 1, call)
            self.assertTrue(known_hosts[0].endswith("/known_hosts"), call)
            self.assertEqual(ssh_call_with_paths_named(call), expected_ssh_call(dest, SHA))


if __name__ == "__main__":
    unittest.main()
