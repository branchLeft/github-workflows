"""Tests of ansible-gated-apply.yml, run against the text of each step.

Each step's `run:` text runs under bash with `ssh`, `gh` and `timeout` stubbed on
PATH. ANSIBLE_GATED_APPLY_WORKFLOW names a broken copy for a sabotage run.
After a workflow change, regenerate the snapshot with
`python3 tools/test_ansible_gated_apply.py --write-snapshot`.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import yaml

import run_workflow_steps

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


# A stand-in for yq v4 that answers only the one call the workflow makes,
# `yq -o=json . FILE`, by reading the file with PyYAML. Any other call, a missing
# file or a file that is not YAML exits non-zero, as yq does. It is not yq: the
# real yq runs in the self-test's own run of the discover job.
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
        for name, text in (("ssh", SSH_STUB), ("timeout", TIMEOUT_STUB), ("gh", GH_STUB), ("yq", YQ_STUB)):
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
SNAPSHOT = REPO / "tools/ansible-gated-apply.structure.json"
# A bare constant false: `${{ false }}`, or `false` as a whole operand of && or ||.
# The comparison `inputs.dry-run == false` is not a bare constant and is allowed.
BARE_FALSE = re.compile(r"\$\{\{\s*false\s*\}\}|(^|[&|(]\s*)false(\s*$|\s*[&|)])")


def key_name(key: Any) -> str:
    return "on" if key is True else str(key)


def step_structure(step: dict[str, Any]) -> dict[str, Any]:
    """A step as data, with its `run:` body replaced by the SHA-256 of its text."""
    entry: dict[str, Any] = {}
    for key, value in step.items():
        if key == "run":
            entry["run-sha256"] = hashlib.sha256(str(value).encode()).hexdigest()
        else:
            entry[key_name(key)] = value
    return entry


def structure(doc: dict[Any, Any]) -> dict[str, Any]:
    """Every key of a workflow at every level, with the text of each `run:` body reduced to its hash."""
    result: dict[str, Any] = {}
    for key, value in doc.items():
        if key != "jobs":
            result[key_name(key)] = value
    jobs: dict[str, Any] = {}
    for name, job in doc["jobs"].items():
        entry: dict[str, Any] = {key_name(k): v for k, v in job.items() if k != "steps"}
        if "steps" in job:
            entry["steps"] = [step_structure(step) for step in job["steps"]]
        jobs[name] = entry
    result["jobs"] = jobs
    return result


def snapshot_text() -> str:
    """The structure of both workflows, as the committed snapshot must hold it."""
    both = {"gated": structure(DOC), "self": structure(yaml.safe_load(SELF_WORKFLOW.read_text()))}
    return json.dumps(both, indent=2, sort_keys=True) + "\n"


class StructureTests(unittest.TestCase):
    def test_both_workflows_match_the_committed_structure_snapshot(self) -> None:
        self.assertEqual(
            SNAPSHOT.read_text(),
            snapshot_text(),
            "a workflow changed: regenerate with `python3 tools/test_ansible_gated_apply.py --write-snapshot`",
        )

    def test_no_job_or_step_condition_is_a_bare_constant_false(self) -> None:
        self_doc: dict[Any, Any] = yaml.safe_load(SELF_WORKFLOW.read_text())
        for label, doc in (("gated", DOC), ("self", self_doc)):
            for name, job in doc["jobs"].items():
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


def discover_env(**changes: str) -> dict[str, str]:
    values = {
        "EVENT_NAME": "push",
        "REF": "refs/heads/main",
        "DEFAULT_BRANCH": "main",
        "IS_FORK": "false",
        "ENVIRONMENT": "production",
        "WORKING_DIRECTORY": ".",
        "ANSIBLE_DIRECTORY": "ansible",
    }
    values.update(changes)
    return values


class DiscoverTests(unittest.TestCase):
    def decide(self, **changes: str) -> str:
        result = Sandbox(self).run(find_step("discover", "context"), discover_env(**changes))
        self.assertEqual(result.code, 0, result.log)
        return result.outputs.get("delivery", "<missing>")

    def test_a_push_to_the_default_branch_of_the_repository_itself_delivers(self) -> None:
        self.assertEqual(self.decide(), "true")

    def test_every_other_event_branch_or_fork_does_not_deliver(self) -> None:
        cases = {
            "pull request": {"EVENT_NAME": "pull_request", "REF": "refs/pull/40/merge"},
            "manual run": {"EVENT_NAME": "workflow_dispatch"},
            "schedule": {"EVENT_NAME": "schedule"},
            "push to another branch": {"REF": "refs/heads/feature"},
            "push to a tag": {"REF": "refs/tags/v1.0.0"},
            "push to a default branch that is not main": {"DEFAULT_BRANCH": "trunk", "REF": "refs/heads/trunk-x"},
            "push from a fork": {"IS_FORK": "true"},
        }
        for label, changes in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.decide(**changes), "false")


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

    def test_the_apply_guard_refuses_only_the_literal_false_and_lets_every_other_value_through(self) -> None:
        # The contract of the merged workflow: `!= false`, so a missing or unreadable
        # output still reaches the approval and is never a silent skip. Several values,
        # so that a guard written as `= true` or `= 1` cannot pass for one of them.
        for value in ("true", "", "garbage", "True", "FALSE", "0", "no", "false "):
            with self.subTest(has_changes=value):
                result = self.guard(HAS_CHANGES=value)
                self.assertEqual(result.code, 0, result.log)

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


FIXTURE = REPO / ".github/fixtures/ansible-gated-apply"
LINT_JOB = "lint"
INSTALL_STEPS = ("Create the tool environment", "Install the pinned tools")
# The venv's bin directory under the sandbox's RUNNER_TEMP, where the steps look for a tool.
VENV_BIN = "ansible-venv/bin"
RECORDING_TOOL = """#!/usr/bin/env bash
name="${0##*/}"
printf '%s\\n' "$@" > "$STUB_DIR/$name.argv"
"""


def shards_env(**changes: str) -> dict[str, str]:
    values = {
        "WORKING_DIRECTORY": ".",
        "ANSIBLE_DIRECTORY": "ansible",
        "ROLE_DIRECTORIES": "roles/*\n",
    }
    values.update(changes)
    return values


GOOD_SCENARIO = "dependency:\n  name: galaxy\n  enabled: false\ndriver:\n  name: docker\n"
EMPTY_COLLECTIONS = "---\ncollections: []\n"


class ShardDiscoveryTests(unittest.TestCase):
    """The discover step's rule on requirements.yml and on each shard's Molecule dependency step."""

    def discover(
        self,
        scenario: str | None = GOOD_SCENARIO,
        requirements: str | None = EMPTY_COLLECTIONS,
        extra: dict[str, str] | None = None,
    ) -> Result:
        """Run the step on a project with one role, `alpha`. `extra` maps a path under the Ansible directory to its text."""
        sandbox = Sandbox(self)
        ansible = sandbox.dir / "ansible"
        scenario_dir = ansible / "roles" / "alpha" / "molecule" / "default"
        scenario_dir.mkdir(parents=True)
        if scenario is not None:
            scenario_dir.joinpath("molecule.yml").write_text(scenario)
        if requirements is not None:
            ansible.joinpath("requirements.yml").write_text(requirements)
        for relative, text in (extra or {}).items():
            target = ansible / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        return sandbox.run(find_step("discover", "shards"), shards_env())

    def test_a_scenario_that_disables_the_dependency_step_and_an_empty_collection_list_pass(self) -> None:
        result = self.discover()
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(json.loads(result.outputs["matrix"]), {"include": [{"name": "roles/alpha", "path": "roles/alpha"}]})

    def test_an_absent_requirements_file_passes_because_nothing_reads_it(self) -> None:
        self.assertEqual(self.discover(requirements=None).code, 0)

    def test_requirements_that_list_anything_else_fail_and_name_the_file(self) -> None:
        cases = {
            "a collection": "---\ncollections:\n  - name: ansible.posix\n    version: 2.2.2\n",
            "a role": "---\ncollections: []\nroles:\n  - name: geerlingguy.docker\n",
            "an empty roles list beside the collections": "---\ncollections: []\nroles: []\n",
            "an unknown key": "---\ncollections: []\nextra: 1\n",
            "a bare list": "---\n- name: ansible.posix\n",
            "a null collections value": "---\ncollections:\n",
            "a scalar": "---\njust text\n",
            "a comment only": "---\n# nothing here\n",
            "an empty file": "",
            "a file that is not YAML": "collections: [\n",
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                result = self.discover(requirements=text)
                self.assertNotEqual(result.code, 0, label)
                self.assertIn("requirements.yml must hold only", result.log)
                self.assertNotIn("matrix", result.outputs)

    def test_a_requirements_path_that_is_a_dangling_link_fails(self) -> None:
        sandbox = Sandbox(self)
        scenario_dir = sandbox.dir / "ansible" / "roles" / "alpha" / "molecule" / "default"
        scenario_dir.mkdir(parents=True)
        scenario_dir.joinpath("molecule.yml").write_text(GOOD_SCENARIO)
        (sandbox.dir / "ansible" / "requirements.yml").symlink_to(sandbox.dir / "nowhere.yml")
        self.assertNotEqual(sandbox.run(find_step("discover", "shards"), shards_env()).code, 0)

    def test_a_requirements_file_in_the_role_directory_that_lists_anything_fails(self) -> None:
        """Molecule's prerun installs these from Galaxy; it searches the six paths of ansible-compat's REQUIREMENT_LOCATIONS."""
        paths = [
            "requirements.yml",
            "roles/requirements.yml",
            "collections/requirements.yml",
            "tests/requirements.yml",
            "tests/integration/requirements.yml",
            "tests/unit/requirements.yml",
        ]
        listed = {
            "a collection": "---\ncollections:\n  - name: ansible.posix\n",
            "a role": "---\ncollections: []\nroles:\n  - name: geerlingguy.docker\n",
            "a bare list": "---\n- name: ansible.posix\n",
            "a file that is not YAML": "collections: [\n",
        }
        for path in paths:
            for label, text in listed.items():
                with self.subTest(path=path, case=label):
                    result = self.discover(extra={f"roles/alpha/{path}": text})
                    self.assertNotEqual(result.code, 0)
                    self.assertIn(f"roles/alpha/{path} must hold only 'collections: []'", result.log)
                    self.assertNotIn("matrix", result.outputs)

    def test_an_empty_collections_list_in_the_role_directory_passes(self) -> None:
        """Prerun installs nothing from an empty list (run for real with Molecule)."""
        for path in ("requirements.yml", "roles/requirements.yml", "tests/integration/requirements.yml"):
            with self.subTest(path=path):
                result = self.discover(extra={f"roles/alpha/{path}": EMPTY_COLLECTIONS})
                self.assertEqual(result.code, 0, result.log)

    def test_a_role_requirements_path_that_is_a_dangling_link_fails(self) -> None:
        sandbox = Sandbox(self)
        role = sandbox.dir / "ansible" / "roles" / "alpha"
        role.joinpath("molecule", "default").mkdir(parents=True)
        role.joinpath("molecule", "default", "molecule.yml").write_text(GOOD_SCENARIO)
        (role / "requirements.yml").symlink_to(sandbox.dir / "nowhere.yml")
        result = sandbox.run(find_step("discover", "shards"), shards_env())
        self.assertNotEqual(result.code, 0)
        self.assertIn("roles/alpha/requirements.yml must hold only", result.log)

    def test_a_galaxy_file_anywhere_under_the_ansible_directory_fails(self) -> None:
        """Prerun installs a galaxy.yml's dependencies from Galaxy, in the role directory, one level below it, or two levels above it."""
        galaxy = "---\nnamespace: branchleft\nname: probe\nversion: 1.0.0\ndependencies:\n  branchleft.other: '*'\n"
        places = [
            "galaxy.yml",
            "roles/alpha/galaxy.yml",
            "roles/alpha/branchleft/galaxy.yml",
            "roles/alpha/deeper/still/galaxy.yml",
            "galaxy.yaml",
            "roles/alpha/galaxy.yaml",
        ]
        for place in places:
            with self.subTest(place=place):
                result = self.discover(extra={place: galaxy})
                self.assertNotEqual(result.code, 0)
                self.assertIn(f"{place} must not exist", result.log)
                self.assertNotIn("matrix", result.outputs)

    def test_a_galaxy_file_that_is_a_dangling_link_fails(self) -> None:
        sandbox = Sandbox(self)
        role = sandbox.dir / "ansible" / "roles" / "alpha"
        role.joinpath("molecule", "default").mkdir(parents=True)
        role.joinpath("molecule", "default", "molecule.yml").write_text(GOOD_SCENARIO)
        (sandbox.dir / "ansible" / "galaxy.yml").symlink_to(sandbox.dir / "nowhere.yml")
        result = sandbox.run(find_step("discover", "shards"), shards_env())
        self.assertNotEqual(result.code, 0)
        self.assertIn("galaxy.yml must not exist", result.log)

    def test_a_scenario_that_does_not_disable_the_dependency_step_fails(self) -> None:
        cases = {
            "enabled": "dependency:\n  name: galaxy\n  enabled: true\n",
            "no dependency key": "driver:\n  name: docker\n",
            "dependency without enabled": "dependency:\n  name: galaxy\n",
            "enabled as the string false": "dependency:\n  enabled: 'false'\n",
            "dependency not a mapping": "dependency: galaxy\n",
            "a list at the top": "- dependency:\n    enabled: false\n",
            "a file that is not YAML": "dependency: [\n",
            "an empty file": "",
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                result = self.discover(scenario=text)
                self.assertNotEqual(result.code, 0, label)
                self.assertIn("dependency.enabled to false", result.log)
                self.assertNotIn("matrix", result.outputs)

    def test_a_role_with_no_scenario_still_fails(self) -> None:
        result = self.discover(scenario=None)
        self.assertNotEqual(result.code, 0)
        self.assertIn("molecule/default/molecule.yml", result.log)

    def test_the_fixture_project_passes_the_rule(self) -> None:
        sandbox = Sandbox(self)
        values = shards_env(WORKING_DIRECTORY=str(FIXTURE))
        result = sandbox.run(find_step("discover", "shards"), values)
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(json.loads(result.outputs["matrix"]), {"include": [{"name": "roles/fixture_config", "path": "roles/fixture_config"}]})


def logical_lines(text: str) -> list[str]:
    """The requirement lines of a pip file, with backslash continuations joined and comments dropped."""
    joined: list[str] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if line.endswith("\\"):
            pending += line[:-1].rstrip() + " "
            continue
        joined.append((pending + line).strip())
        pending = ""
    if pending:
        joined.append(pending.strip())
    return joined


TOOLS = {"ansible", "ansible-lint", "molecule", "molecule-plugins", "yamllint", "mypy", "ruff"}
EXACT_PIN = re.compile(r"(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[A-Za-z0-9._,-]+\])?==(?P<version>\d+(?:\.\d+)*)", re.ASCII)
HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}", re.ASCII)


class LockTests(unittest.TestCase):
    """The fixture's lock. The same facts shared-infra's ansible/checks/check_pins.py checks of its own."""

    def setUp(self) -> None:
        self.chosen = (FIXTURE / "ansible/requirements.in").read_text()
        self.lock = (FIXTURE / "ansible/requirements.txt").read_text()

    def test_requirements_in_pins_each_tool_exactly_and_names_nothing_else(self) -> None:
        names: set[str] = set()
        for line in logical_lines(self.chosen):
            match = EXACT_PIN.fullmatch(line)
            self.assertIsNotNone(match, line)
            assert match is not None
            names.add(match["name"])
        self.assertEqual(names, TOOLS)

    def test_every_line_of_the_lock_is_an_exact_pin_with_a_hash(self) -> None:
        lines = logical_lines(self.lock)
        self.assertGreater(len(lines), 40)
        for line in lines:
            requirement = line.split(" --hash=")[0].split(" ; ")[0]
            self.assertIsNotNone(EXACT_PIN.fullmatch(requirement), line)
            self.assertRegex(line, HASH, f"{requirement} has no hash")

    def test_the_lock_agrees_with_requirements_in(self) -> None:
        locked: dict[str, str] = {}
        for line in logical_lines(self.lock):
            match = EXACT_PIN.match(line)
            assert match is not None
            locked.setdefault(match["name"].lower(), match["version"])
        for line in logical_lines(self.chosen):
            match = EXACT_PIN.fullmatch(line)
            assert match is not None
            self.assertEqual(locked.get(match["name"].lower()), match["version"], line)

    def test_the_lock_header_records_a_universal_hashed_compile_of_requirements_in(self) -> None:
        header = " ".join(line for line in self.lock.splitlines()[:3] if line.startswith("#"))
        for fact in ("uv pip compile requirements.in", "--universal", "--generate-hashes", "--python-version 3.12"):
            self.assertIn(fact, header)


class InstallStepTests(unittest.TestCase):
    """The install steps of the lint and shard jobs: hashes required, no Galaxy, and one wording."""

    def test_the_lint_and_shard_jobs_carry_the_same_environment_and_install_steps(self) -> None:
        for name in INSTALL_STEPS:
            with self.subTest(step=name):
                self.assertEqual(find_step_named("lint", name), find_step_named("shard", name))

    def test_no_pip_install_in_the_workflow_lacks_the_hash_and_virtualenv_flags(self) -> None:
        seen = 0
        for job_name, job in JOBS.items():
            for step in job["steps"]:
                for line in str(step.get("run", "")).splitlines():
                    if re.search(r"\bpip\b.*\binstall\b|\binstall\b.*\bpip\b", line):
                        seen += 1
                        self.assertIn("--require-hashes", line, f"{job_name}: {line}")
                        self.assertIn("--require-virtualenv", line, f"{job_name}: {line}")
        self.assertGreaterEqual(seen, 2)

    def test_both_jobs_run_pip_with_the_hash_flag_against_the_lock(self) -> None:
        for job in ("lint", "shard"):
            with self.subTest(job=job):
                sandbox = Sandbox(self)
                self.install_stub(sandbox, "pip")
                result = sandbox.run(find_step_named(job, "Install the pinned tools"), {"ANSIBLE_DIRECTORY": "ansible"})
                self.assertEqual(result.code, 0, result.log)
                argv = (sandbox.stubs / "pip.argv").read_text().split()
                self.assertEqual(argv, ["install", "--require-virtualenv", "--require-hashes", "-r", "ansible/requirements.txt"])

    def test_the_workflow_never_checks_out_its_own_repository_by_the_job_workflow_sha(self) -> None:
        # `github.job_workflow_sha` is empty on the runner, so a second checkout that
        # takes it silently takes main (measured in the docs-lint self run). The lint
        # commands live in this file instead, and the self-test reads them from here.
        text = WORKFLOW.read_text()
        self.assertNotIn("job_workflow_sha", text)
        self.assertNotIn("job_workflow_ref", text)
        for job_name, job in JOBS.items():
            for step in job["steps"]:
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    self.assertNotIn("repository", step.get("with", {}), f"{job_name}: {step.get('name')}")
                    self.assertNotIn("ref", step.get("with", {}), f"{job_name}: {step.get('name')}")

    def test_nothing_fetches_a_collection_from_galaxy_or_sets_a_collections_path(self) -> None:
        text = WORKFLOW.read_text()
        for forbidden in ("ansible-galaxy", "collection install", "ANSIBLE_COLLECTIONS_PATH"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#")))

    @staticmethod
    def install_stub(sandbox: Sandbox, tool: str) -> None:
        directory = sandbox.temp / VENV_BIN
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / tool
        target.write_text(RECORDING_TOOL)
        target.chmod(0o755)


class LintStepTests(unittest.TestCase):
    """Each lint command, run with a recording stand-in for its tool."""

    def argv(self, step_name: str, tool: str) -> list[str]:
        sandbox = Sandbox(self)
        InstallStepTests.install_stub(sandbox, tool)
        result = sandbox.run(find_step_named(LINT_JOB, step_name), {"ANSIBLE_DIRECTORY": "ansible"})
        self.assertEqual(result.code, 0, result.log)
        return (sandbox.stubs / f"{tool}.argv").read_text().split()

    def test_ansible_lint_runs_offline_strict_over_the_ansible_directory(self) -> None:
        # The sandbox has no network, so the offline pin has to be an assertion on the arguments.
        self.assertEqual(self.argv("ansible-lint at the production profile", "ansible-lint"), ["--offline", "--strict", "ansible"])

    def test_yamllint_runs_strict_with_the_project_configuration(self) -> None:
        self.assertEqual(self.argv("yamllint every YAML file", "yamllint"), ["--strict", "-c", ".yamllint", "ansible"])


class DependabotTests(unittest.TestCase):
    def test_dependabot_watches_the_fixture_lock_with_the_uv_ecosystem(self) -> None:
        config = yaml.safe_load((REPO / ".github/dependabot.yml").read_text())
        entries = [
            entry
            for entry in config["updates"]
            if entry.get("package-ecosystem") == "uv" and entry.get("directory") == "/.github/fixtures/ansible-gated-apply/ansible"
        ]
        self.assertEqual(len(entries), 1)
        self.assertIsInstance(entries[0].get("schedule"), dict)
        self.assertTrue((FIXTURE / "ansible/requirements.in").is_file())


class LintFailFixtureTests(unittest.TestCase):
    RULE = "name[missing]"
    SUMMARY = "Failed: 1 failure(s), 0 warning(s)"

    @property
    def job(self) -> dict[str, Any]:
        document: dict[Any, Any] = yaml.safe_load(SELF_WORKFLOW.read_text())
        return document["jobs"]["lint-expected-fail"]

    def test_the_failing_role_sits_beside_the_real_project_and_shares_its_lint_configuration(self) -> None:
        self.assertTrue((FIXTURE / "lint-fail/roles/lint_fail/tasks/main.yml").is_file())
        self.assertFalse((FIXTURE / "ansible/lint-fail").exists())
        for name in (".ansible-lint", ".yamllint"):
            self.assertFalse((FIXTURE / "lint-fail" / name).exists(), name)
            self.assertTrue((FIXTURE / name).is_file(), name)

    def test_the_readme_the_job_and_the_task_agree_on_the_rule_id_and_the_status(self) -> None:
        readme = (FIXTURE / "lint-fail/README.md").read_text()
        text = str(self.job["steps"][-1]["run"])
        for fact in (self.RULE, self.SUMMARY):
            self.assertIn(fact, readme)
            self.assertIn(fact, text)
        self.assertRegex(readme, r"Exit status of ansible-lint: `2`")
        self.assertIn('[ "$status" = 2 ]', text)
        task = (FIXTURE / "lint-fail/roles/lint_fail/tasks/main.yml").read_text()
        self.assertNotRegex(task, r"(?m)^\s*-?\s*name:")

    def test_the_job_runs_the_lint_jobs_own_steps_by_name_and_is_bounded(self) -> None:
        text = str(self.job["steps"][-1]["run"])
        self.assertIn("timeout-minutes", self.job)
        self.assertIn(".github/workflows/ansible-gated-apply.yml lint", text)
        names = [step.get("name") for step in JOBS[LINT_JOB]["steps"]]
        for step_name in (*INSTALL_STEPS, "ansible-lint at the production profile"):
            self.assertIn(f'--step "{step_name}"', text)
            self.assertIn(step_name, names)

    def run_assertions(self, status: int, log: str) -> Result:
        sandbox = Sandbox(self)
        stub = sandbox.bin / "python3"
        stub.write_text(f"#!/usr/bin/env bash\ncat <<'LOG'\n{log}\nLOG\nexit {status}\n")
        stub.chmod(0o755)
        return sandbox.run(self.job["steps"][-1], {})

    def test_the_job_passes_only_for_the_named_rule_with_the_violation_status(self) -> None:
        good = f"{self.SUMMARY} in 5 files\n{self.RULE}: All tasks should be named.\n"
        self.assertEqual(self.run_assertions(2, good).code, 0)
        bad = {
            "a clean run": (0, good),
            "another failure status": (1, good),
            "a runner refusal": (99, good),
            "another rule": (2, f"{self.SUMMARY}\nyaml[truthy]: Truthy value.\n"),
            "the rule without the one-failure line": (2, f"Failed: 3 failure(s), 0 warning(s)\n{self.RULE}: All tasks should be named.\n"),
            "no output": (2, ""),
        }
        for label, (status, log) in bad.items():
            with self.subTest(case=label):
                self.assertNotEqual(self.run_assertions(status, log).code, 0, label)


WORKFLOW_TEXT = """\
jobs:
  work:
    defaults:
      run:
        working-directory: ${{ inputs.base }}
    steps:
      - name: One
        env:
          WHO: ${{ inputs.who }}
        run: |
          echo "one $WHO" >> "$OUT"
          mkdir -p "$RUNNER_TEMP/bin"
          printf '#!/usr/bin/env bash\\necho from-the-tool\\n' > "$RUNNER_TEMP/bin/sometool"
          chmod +x "$RUNNER_TEMP/bin/sometool"
          echo "$RUNNER_TEMP/bin" >> "$GITHUB_PATH"
          echo "FROM_ONE=carried" >> "$GITHUB_ENV"
      - name: Two
        env:
          WHO: ${{ inputs.who }}
        run: |
          echo "two $WHO $FROM_ONE $(sometool) $(pwd -P | xargs basename)" >> "$OUT"
      - name: Fails
        run: exit 7
      - name: After the failure
        run: echo after >> "$OUT"
      - name: Remote
        uses: example/action@v1
      - name: Guarded
        if: always()
        run: echo guarded
      - name: Foreign expression
        env:
          SHA: ${{ github.sha }}
        run: echo "$SHA"
      - name: Expression in the body
        run: echo "${{ inputs.who }}"
"""


class RunWorkflowStepsTests(unittest.TestCase):
    """tools/run_workflow_steps.py, which lets a self-test job run the workflow's own steps."""

    def setUp(self) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="run-steps-test-"))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.workflow = self.dir / "workflow.yml"
        self.workflow.write_text(WORKFLOW_TEXT)
        self.out = self.dir / "out.txt"
        (self.dir / "base").mkdir()
        (self.dir / "other").mkdir()
        os.environ["OUT"] = str(self.out)
        self.addCleanup(os.environ.pop, "OUT", None)

    def run_main(self, *arguments: str) -> int:
        base = ["--input", f"base={self.dir / 'base'}"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run_workflow_steps.main([str(self.workflow), "work", *base, *arguments])

    def written(self) -> list[str]:
        return self.out.read_text().splitlines() if self.out.exists() else []

    def test_it_runs_the_steps_in_order_with_inputs_in_the_working_directory_and_the_path_and_env_files(self) -> None:
        status = self.run_main("--input", "who=alice", "--step", "One", "--step", "Two")
        self.assertEqual(status, 0)
        self.assertEqual(self.written(), ["one alice", "two alice carried from-the-tool base"])

    def test_a_step_input_replaces_the_input_for_that_step_only(self) -> None:
        status = self.run_main("--input", "who=alice", "--step", "One", "--step", "Two", "--step-input", "Two", "who=bob")
        self.assertEqual(status, 0)
        self.assertEqual(self.written()[0], "one alice")
        self.assertTrue(self.written()[1].startswith("two bob "), self.written())

    def test_the_first_failing_step_stops_the_run_and_its_own_status_is_returned(self) -> None:
        status = self.run_main("--input", "who=a", "--step", "One", "--step", "Fails", "--step", "After the failure")
        self.assertEqual(status, 7)
        self.assertEqual(self.written(), ["one a"])

    def test_a_request_it_cannot_honour_exits_99_before_any_step_runs(self) -> None:
        cases = {
            "an unknown step": ["--step", "One", "--step", "Nope"],
            "a step that uses an action": ["--step", "One", "--step", "Remote"],
            "a step with a condition": ["--step", "One", "--step", "Guarded"],
            "an expression it does not know": ["--step", "One", "--step", "Foreign expression"],
            "an expression in the run text": ["--step", "One", "--step", "Expression in the body"],
            "no steps": [],
            "a step input for a step that is not run": ["--step", "One", "--step-input", "Two", "who=x"],
            "a step input that is not NAME=VALUE": ["--step", "One", "--step-input", "One", "who"],
        }
        for label, arguments in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.run_main("--input", "who=a", *arguments), 99, label)
                self.assertEqual(self.written(), [], label)

    def test_an_input_nobody_gave_exits_99(self) -> None:
        self.assertEqual(self.run_main("--step", "One"), 99)
        self.assertEqual(self.written(), [])

    def test_an_unknown_job_or_an_unreadable_workflow_exits_99(self) -> None:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run_workflow_steps.main([str(self.workflow), "nojob", "--step", "One"]), 99)
            self.assertEqual(run_workflow_steps.main([str(self.dir / "missing.yml"), "work", "--step", "One"]), 99)

    def test_it_reads_the_real_workflow_and_resolves_every_step_it_is_asked_for(self) -> None:
        job = run_workflow_steps.load_job(WORKFLOW, LINT_JOB)
        planned = run_workflow_steps.plan_steps(
            job,
            [*INSTALL_STEPS, "ansible-lint at the production profile"],
            {"ansible-directory": "ansible", "working-directory": "."},
            {"ansible-lint at the production profile": {"ansible-directory": "lint-fail"}},
        )
        self.assertEqual([name for name, _, _ in planned], [*INSTALL_STEPS, "ansible-lint at the production profile"])
        self.assertEqual(planned[1][2], {"ANSIBLE_DIRECTORY": "ansible"})
        self.assertEqual(planned[2][2], {"ANSIBLE_DIRECTORY": "lint-fail"})


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-snapshot"]:
        SNAPSHOT.write_text(snapshot_text())
    else:
        unittest.main()
