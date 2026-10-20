"""Tests of ansible-gated-apply.yml, ansible-drift-check.yml and the ssh-check-hosts
action, run against the text of each step.

Each step's `run:` text runs under bash with `ssh`, `gh` and `timeout` stubbed on
PATH. ANSIBLE_GATED_APPLY_WORKFLOW, ANSIBLE_DRIFT_CHECK_WORKFLOW and
SSH_CHECK_HOSTS_ACTION name a broken copy for a sabotage run. After a change to
any of the three files, regenerate the snapshot with
`python3 tools/test_ansible_gated_apply.py --write-snapshot`.
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
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parent.parent
WORKFLOW = Path(os.environ.get("ANSIBLE_GATED_APPLY_WORKFLOW", REPO / ".github/workflows/ansible-gated-apply.yml"))
SHA = "a" * 40
OTHER_SHA = "b" * 40
HOST_ONE = "deploy@host-one"
HOST_TWO = "deploy@host-two"

DRIFT_WORKFLOW = Path(os.environ.get("ANSIBLE_DRIFT_CHECK_WORKFLOW", REPO / ".github/workflows/ansible-drift-check.yml"))
ACTION = Path(os.environ.get("SSH_CHECK_HOSTS_ACTION", REPO / ".github/actions/ssh-check-hosts/action.yml"))
ACTION_USES = "./.gw/.github/actions/ssh-check-hosts"

DOC: dict[Any, Any] = yaml.safe_load(WORKFLOW.read_text())
JOBS: dict[str, dict[str, Any]] = DOC["jobs"]
DRIFT_DOC: dict[Any, Any] = yaml.safe_load(DRIFT_WORKFLOW.read_text())
DRIFT_JOBS: dict[str, dict[str, Any]] = DRIFT_DOC["jobs"]
ACTION_DOC: dict[Any, Any] = yaml.safe_load(ACTION.read_text())
ACTION_STEPS: list[dict[str, Any]] = ACTION_DOC["runs"]["steps"]

# Every file that holds step text the hygiene tests cover: label, path, and its steps by job.
WORKFLOW_FILES = (("gated", WORKFLOW, DOC), ("drift", DRIFT_WORKFLOW, DRIFT_DOC))
TEXT_FILES = (("gated", WORKFLOW), ("drift", DRIFT_WORKFLOW), ("action", ACTION))


def all_steps() -> list[tuple[str, str, dict[str, Any]]]:
    """Every step of the three files as (file label, job or "action", step)."""
    found: list[tuple[str, str, dict[str, Any]]] = []
    for label, _, doc in WORKFLOW_FILES:
        for name, job in doc["jobs"].items():
            found.extend((label, name, step) for step in job["steps"])
    found.extend(("action", "action", step) for step in ACTION_STEPS)
    return found


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


def action_step(step_id: str) -> dict[str, Any]:
    for step in ACTION_STEPS:
        if step.get("id") == step_id:
            return step
    raise KeyError(f"action/{step_id}")


def drift_step(job: str, step_id: str) -> dict[str, Any]:
    for step in DRIFT_JOBS[job]["steps"]:
        if step.get("id") == step_id:
            return step
    raise KeyError(f"drift/{job}/{step_id}")


def drift_step_named(job: str, fragment: str) -> dict[str, Any]:
    for step in DRIFT_JOBS[job]["steps"]:
        if fragment in step.get("name", ""):
            return step
    raise KeyError(f"drift/{job}/{fragment}")


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


RECORD_GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/gh.log"
code=$(cat "$STUB_DIR/gh.exit" 2> /dev/null || echo 0)
if [ "$code" != 0 ]; then
  echo "gh: HTTP 403" >&2
  exit "$code"
fi
cat "$STUB_DIR/run.json"
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

    def __init__(self, test: unittest.TestCase, gh_stub: str = GH_STUB) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="gated-apply-test-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.stubs = self.dir / "stubs"
        self.temp = self.dir / "runner-temp"
        for path in (self.bin, self.stubs, self.temp):
            path.mkdir()
        for name, text in (("ssh", SSH_STUB), ("timeout", TIMEOUT_STUB), ("gh", gh_stub)):
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
        "DRY_RUN": "false",
        "DRY_RUN_STUB": "",
    }
    values.update(changes)
    return values


def composite_validate_env(**changes: str) -> dict[str, str]:
    values = {
        "HOSTS": f"{HOST_ONE}\n{HOST_TWO}\n",
        "SHA": SHA,
        "DRY_RUN": "false",
        "DRY_RUN_STUB": "",
        "FAIL_ON_CHANGES": "false",
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
ACTION_SELF_WORKFLOW = REPO / ".github/workflows/ssh-check-hosts-self.yml"
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


def action_structure(doc: dict[Any, Any]) -> dict[str, Any]:
    """A composite action as data: every key, with each step's `run:` body reduced to its hash."""
    result: dict[str, Any] = {}
    for key, value in doc.items():
        if key != "runs":
            result[key_name(key)] = value
    runs = {key_name(k): v for k, v in doc["runs"].items() if k != "steps"}
    runs["steps"] = [step_structure(step) for step in doc["runs"]["steps"]]
    result["runs"] = runs
    return result


def snapshot_text() -> str:
    """The structure of every workflow and action of this module, as the committed snapshot must hold it."""
    every = {
        "gated": structure(DOC),
        "self": structure(yaml.safe_load(SELF_WORKFLOW.read_text())),
        "drift": structure(DRIFT_DOC),
        "action": action_structure(ACTION_DOC),
        "action-self": structure(yaml.safe_load(ACTION_SELF_WORKFLOW.read_text())),
    }
    return json.dumps(every, indent=2, sort_keys=True) + "\n"


class StructureTests(unittest.TestCase):
    def test_every_workflow_and_the_action_match_the_committed_structure_snapshot(self) -> None:
        self.assertEqual(
            SNAPSHOT.read_text(),
            snapshot_text(),
            "a workflow or the action changed: regenerate with `python3 tools/test_ansible_gated_apply.py --write-snapshot`",
        )

    def test_no_job_or_step_condition_is_a_bare_constant_false(self) -> None:
        self_doc: dict[Any, Any] = yaml.safe_load(SELF_WORKFLOW.read_text())
        action_self_doc: dict[Any, Any] = yaml.safe_load(ACTION_SELF_WORKFLOW.read_text())
        for label, doc in (("gated", DOC), ("self", self_doc), ("drift", DRIFT_DOC), ("action-self", action_self_doc)):
            for name, job in doc["jobs"].items():
                texts = [job.get("if")] + [step.get("if") for step in job.get("steps", [])]
                for text in texts:
                    if text is not None:
                        with self.subTest(workflow=label, job=name, condition=str(text)):
                            self.assertNotRegex(str(text), BARE_FALSE)

    def test_the_only_trigger_is_workflow_call(self) -> None:
        self.assertEqual(set(DOC[True]), {"workflow_call"})
        self.assertEqual(set(DRIFT_DOC[True]), {"workflow_call"})

    def test_no_pull_request_target_and_no_continue_on_error(self) -> None:
        for label, path in TEXT_FILES:
            with self.subTest(file=label):
                text = path.read_text()
                self.assertNotIn("pull_request_target", text)
                self.assertNotIn("continue-on-error", text)

    def test_permissions_are_read_only_everywhere_in_the_gated_workflow(self) -> None:
        self.assertEqual(DOC["permissions"], {"contents": "read"})
        for name, job in JOBS.items():
            if name == "plan":
                self.assertEqual(job["permissions"], {"actions": "read", "contents": "read"})
            else:
                self.assertNotIn("permissions", job, name)
        self.assertNotRegex(WORKFLOW.read_text(), r"(?m)^\s*[a-z-]+:\s*write\s*$")

    def test_every_action_is_pinned_to_a_commit_with_a_version_comment_or_is_the_one_checked_out_action(self) -> None:
        found: dict[str, list[str]] = {}
        for label, path in TEXT_FILES:
            uses = re.findall(r"(?m)^\s*(?:- )?uses:\s*(\S+)(.*)$", path.read_text())
            found[label] = [target for target, _ in uses]
            for target, rest in uses:
                with self.subTest(file=label, uses=target):
                    if target == ACTION_USES:
                        self.assertIn(label, ("gated", "drift"))
                        continue
                    self.assertRegex(target, r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
                    self.assertRegex(rest, r"^\s+# v\d")
        self.assertTrue(found["gated"])
        self.assertEqual(found["action"], [])
        self.assertEqual(found["drift"].count(ACTION_USES), 1)
        self.assertEqual(found["gated"].count(ACTION_USES), 1)

    def test_every_job_sets_a_timeout(self) -> None:
        for label, _, doc in WORKFLOW_FILES:
            for name, job in doc["jobs"].items():
                self.assertIn("timeout-minutes", job, f"{label}/{name}")

    def test_no_expression_inside_a_run_body(self) -> None:
        for label, name, step in all_steps():
            self.assertNotIn("${{", step.get("run", ""), f"{label}/{name}/{step.get('name')}")

    def test_every_run_step_of_the_action_names_bash_and_stops_on_error(self) -> None:
        for step in ACTION_STEPS:
            with self.subTest(step=step.get("name")):
                self.assertEqual(step.get("shell"), "bash")
                self.assertTrue(step["run"].startswith("set -euo pipefail\n"))

    def test_the_plan_job_calls_the_action_from_the_checkout_and_passes_the_plan_inputs(self) -> None:
        step = find_step("plan", "ssh")
        self.assertEqual(step["uses"], ACTION_USES)
        self.assertEqual(
            step["with"],
            {
                "hosts": "${{ secrets.ssh_hosts }}",
                "known-hosts": "${{ secrets.ssh_known_hosts }}",
                "check-key": "${{ secrets.ssh_check_key }}",
                "sha": "${{ steps.guard.outputs.sha }}",
                "dry-run": "${{ inputs.dry-run }}",
                "dry-run-stub": "${{ inputs.dry-run-stub }}",
                "working-directory": "${{ inputs.working-directory }}",
                "host-timeout": "${{ inputs.host-timeout-minutes }}",
                "environment": "${{ inputs.environment }}",
            },
        )
        self.assertNotIn("fail-on-changes", step["with"])

    def test_the_plan_job_outputs_come_from_the_steps_that_make_them(self) -> None:
        self.assertEqual(
            JOBS["plan"]["outputs"],
            {
                "has_changes": "${{ steps.ssh.outputs.has-changes }}",
                "sha": "${{ steps.guard.outputs.sha }}",
                "hosts_digest": "${{ steps.ssh.outputs.hosts-digest }}",
            },
        )
        self.assertEqual(
            ACTION_DOC["outputs"]["has-changes"]["value"], "${{ steps.report.outputs.has_changes }}"
        )
        self.assertEqual(
            ACTION_DOC["outputs"]["hosts-digest"]["value"], "${{ steps.validate.outputs.hosts_digest }}"
        )

    def test_the_host_list_is_validated_in_the_action_and_nowhere_else(self) -> None:
        pattern = "[a-z_][a-z0-9_-]{0,31}@"
        bound = '"$count" -le 50'
        counts = {label: (path.read_text().count(pattern), path.read_text().count(bound)) for label, path in TEXT_FILES}
        self.assertEqual(counts, {"gated": (0, 0), "drift": (0, 0), "action": (1, 1)})

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
                "DRY_RUN": "${{ inputs.dry-run }}",
                "DRY_RUN_STUB": "${{ inputs.dry-run-stub }}",
            }),
            ("plan", "Resolve the commit this workflow was called at", {
                "GH_TOKEN": "${{ github.token }}",
                "REPO": "${{ github.repository }}",
                "RUN_ID": "${{ github.run_id }}",
                "CALLED_WORKFLOW": "branchLeft/github-workflows/.github/workflows/ansible-gated-apply.yml",
            }),
            ("plan", "Verify the checkout", {
                "PINNED_SHA": "${{ steps.pin.outputs.sha }}",
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

    def test_each_expression_that_feeds_an_action_step_comes_from_its_expected_source(self) -> None:
        cases = {
            "validate": {
                "HOSTS": "${{ inputs.hosts }}",
                "SHA": "${{ inputs.sha }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "DRY_RUN_STUB": "${{ inputs.dry-run-stub }}",
                "FAIL_ON_CHANGES": "${{ inputs.fail-on-changes }}",
            },
            "check": {
                "HOSTS": "${{ inputs.hosts }}",
                "SHA": "${{ inputs.sha }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "DRY_RUN_STUB": "${{ inputs.dry-run-stub }}",
                "WORKING_DIRECTORY": "${{ inputs.working-directory }}",
                "HOST_TIMEOUT_MINUTES": "${{ inputs.host-timeout }}",
                "SSH_KEY": "${{ inputs.check-key }}",
                "KNOWN_HOSTS": "${{ inputs.known-hosts }}",
            },
            "report": {
                "SHA": "${{ inputs.sha }}",
                "ENVIRONMENT": "${{ inputs.environment }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "FAIL_ON_CHANGES": "${{ inputs.fail-on-changes }}",
                "SSH_KEY": "${{ inputs.check-key }}",
            },
        }
        for step_id, expected in cases.items():
            with self.subTest(step=step_id):
                self.assertEqual(action_step(step_id).get("env"), expected)

    def test_each_expression_that_feeds_a_drift_step_comes_from_its_expected_source(self) -> None:
        cases = [
            ("drift", "Refuse outside a schedule", {
                "EVENT_NAME": "${{ github.event_name }}",
                "REF": "${{ github.ref }}",
                "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
                "IS_FORK": "${{ github.event.repository.fork }}",
                "SHA": "${{ github.sha }}",
                "HOST_LABEL": "${{ inputs.host-label }}",
            }),
            ("drift", "Resolve the commit", {
                "GH_TOKEN": "${{ github.token }}",
                "REPO": "${{ github.repository }}",
                "RUN_ID": "${{ github.run_id }}",
                "CALLED_WORKFLOW": "branchLeft/github-workflows/.github/workflows/ansible-drift-check.yml",
            }),
            ("drift", "Verify the checkout", {"PINNED_SHA": "${{ steps.pin.outputs.sha }}"}),
            ("alert", "Open the issue", {
                "GH_TOKEN": "${{ github.token }}",
                "GH_REPO": "${{ github.repository }}",
                "HOST_LABEL": "${{ inputs.host-label }}",
                "RUN_URL": "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}",
                "OUTCOME": "${{ needs.drift.outputs.outcome }}",
                "DETAIL": "${{ needs.drift.outputs.detail }}",
            }),
        ]
        for job, fragment, expected in cases:
            with self.subTest(job=job, step=fragment):
                self.assertEqual(drift_step_named(job, fragment).get("env"), expected)

    def test_the_shared_actions_are_checked_out_after_a_pin_that_cannot_be_empty_and_before_they_run(self) -> None:
        sources = (("gated", JOBS["plan"]["steps"]), ("drift", DRIFT_JOBS["drift"]["steps"]))
        for label, steps in sources:
            with self.subTest(file=label):
                checkouts = [i for i, s in enumerate(steps) if str(s.get("uses", "")).startswith("actions/checkout@")]
                shared = [i for i in checkouts if steps[i].get("with", {}).get("path") == ".gw"]
                self.assertEqual(len(shared), 1)
                pin = next(i for i, s in enumerate(steps) if s.get("id") == "pin")
                verify = next(i for i, s in enumerate(steps) if "Verify the checkout" in s.get("name", ""))
                action = next(i for i, s in enumerate(steps) if s.get("uses") == ACTION_USES)
                self.assertLess(pin, shared[0])
                self.assertLess(shared[0], verify)
                self.assertLess(verify, action)
                self.assertEqual(
                    steps[shared[0]]["with"],
                    {
                        "repository": "branchLeft/github-workflows",
                        "ref": "${{ steps.pin.outputs.sha }}",
                        "path": ".gw",
                        "persist-credentials": False,
                    },
                )
                for other in checkouts:
                    if other != shared[0]:
                        self.assertLess(other, pin)
                        self.assertEqual(steps[other].get("if"), "inputs.dry-run == true")
                self.assertNotIn("job_workflow_sha", json.dumps(steps))

    def test_the_drift_job_has_no_checkout_of_the_caller_and_the_plan_job_only_in_a_dry_run(self) -> None:
        other = [s for s in DRIFT_JOBS["drift"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@") and s["with"].get("path") != ".gw"]
        self.assertEqual(other, [])
        caller = [s for s in JOBS["plan"]["steps"] if str(s.get("uses", "")).startswith("actions/checkout@") and s["with"].get("path") != ".gw"]
        self.assertEqual([s.get("if") for s in caller], ["inputs.dry-run == true"])

    def test_the_pin_and_verify_steps_are_the_same_text_in_both_workflows(self) -> None:
        self.assertEqual(find_step("plan", "pin")["run"], drift_step("drift", "pin")["run"])
        self.assertEqual(
            find_step_named("plan", "Verify the checkout")["run"],
            drift_step_named("drift", "Verify the checkout")["run"],
        )

    def test_the_action_inputs_outputs_and_defaults_are_the_declared_ones(self) -> None:
        inputs = ACTION_DOC["inputs"]
        self.assertEqual(
            set(inputs),
            {"hosts", "known-hosts", "check-key", "sha", "dry-run", "dry-run-stub", "working-directory", "host-timeout", "environment", "fail-on-changes"},
        )
        self.assertEqual(inputs["fail-on-changes"]["default"], "false")
        self.assertEqual(inputs["dry-run"]["default"], "false")
        self.assertEqual(set(ACTION_DOC["outputs"]), {"has-changes", "hosts-digest", "outcome", "detail"})

    def test_the_drift_workflow_takes_the_declared_inputs_and_three_required_secrets_and_no_apply_key(self) -> None:
        call = DRIFT_DOC[True]["workflow_call"]
        self.assertEqual(set(call["inputs"]), {"host-label", "host-timeout-minutes", "check-timeout-minutes"})
        self.assertTrue(call["inputs"]["host-label"]["required"])
        self.assertEqual(set(call["secrets"]), {"ssh_hosts", "ssh_check_key", "ssh_known_hosts"})
        for name, secret in call["secrets"].items():
            self.assertTrue(secret["required"], name)
        self.assertNotIn("ssh_apply_key", DRIFT_WORKFLOW.read_text())

    def test_the_drift_workflow_is_two_jobs_and_the_key_job_never_applies(self) -> None:
        self.assertEqual(set(DRIFT_JOBS), {"drift", "alert"})
        self.assertNotIn("environment", json.dumps(DRIFT_JOBS))
        self.assertNotIn("environment:", DRIFT_WORKFLOW.read_text())
        step = next(s for s in DRIFT_JOBS["drift"]["steps"] if s.get("uses") == ACTION_USES)
        self.assertEqual(
            step["with"],
            {
                "hosts": "${{ secrets.ssh_hosts }}",
                "known-hosts": "${{ secrets.ssh_known_hosts }}",
                "check-key": "${{ secrets.ssh_check_key }}",
                "sha": "${{ github.sha }}",
                "host-timeout": "${{ inputs.host-timeout-minutes }}",
                "fail-on-changes": "true",
            },
        )
        self.assertEqual(
            DRIFT_JOBS["drift"]["outputs"],
            {
                "guard_refused": "${{ steps.guard.outputs.refused }}",
                "outcome": "${{ steps.check.outputs.outcome }}",
                "detail": "${{ steps.check.outputs.detail }}",
            },
        )

    def test_the_check_key_is_read_by_the_drift_job_only_and_the_alert_job_reads_no_secret(self) -> None:
        self.assertIn("secrets.ssh_check_key", json.dumps(DRIFT_JOBS["drift"]))
        self.assertNotIn("secrets.", json.dumps(DRIFT_JOBS["alert"]))
        drift_text = json.dumps(DRIFT_JOBS["drift"])
        pin_text = json.dumps(drift_step("drift", "pin"))
        self.assertEqual(pin_text.count("github.token"), 1)
        self.assertEqual(drift_text.count("github.token"), 1)
        self.assertIn("github.token", json.dumps(DRIFT_JOBS["alert"]))

    def test_issues_write_is_granted_to_the_alert_job_alone_and_nothing_else_may_write(self) -> None:
        self.assertEqual(DRIFT_DOC["permissions"], {"contents": "read"})
        self.assertEqual(DRIFT_JOBS["drift"]["permissions"], {"actions": "read", "contents": "read"})
        self.assertEqual(DRIFT_JOBS["alert"]["permissions"], {"issues": "write"})
        written = re.findall(r"(?m)^\s*([a-z-]+):\s*write\s*$", DRIFT_WORKFLOW.read_text())
        self.assertEqual(written, ["issues"])

    def test_the_alert_runs_after_a_failed_check_that_passed_the_guard_and_serialises_per_host_set(self) -> None:
        job = DRIFT_JOBS["alert"]
        self.assertEqual(job["needs"], ["drift"])
        condition = " ".join(str(job["if"]).split())
        for required in ("always()", "needs.drift.result == 'failure'", "needs.drift.outputs.guard_refused != 'true'"):
            self.assertIn(required, condition)
        self.assertFalse(job["concurrency"]["cancel-in-progress"])
        self.assertIn("inputs.host-label", job["concurrency"]["group"])
        self.assertEqual([s.get("if") for s in job["steps"]], [None])


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

    def test_a_push_to_the_default_branch_passes_and_publishes_the_commit(self) -> None:
        result = self.guard()
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["sha"], SHA)

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

    def test_a_dry_run_needs_its_stand_in(self) -> None:
        self.assertNotEqual(self.guard(DRY_RUN="true").code, 0)
        self.assertEqual(self.guard(DRY_RUN="true", DRY_RUN_STUB="stub/host.sh").code, 0)


class ValidateHostsTests(unittest.TestCase):
    """The host list and the inputs the action refuses before any key is read."""

    def validate(self, **changes: str) -> Result:
        return Sandbox(self).run(action_step("validate"), composite_validate_env(**changes))

    def test_a_valid_list_passes_and_publishes_the_digest_of_the_list_and_never_logs_a_host(self) -> None:
        result = self.validate()
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["hosts_digest"], digest([HOST_ONE, HOST_TWO]))
        self.assertNotIn(HOST_ONE, result.log)
        other = self.validate(HOSTS=f"{HOST_TWO}\n{HOST_ONE}\n")
        self.assertEqual(other.outputs["hosts_digest"], digest([HOST_TWO, HOST_ONE]))
        self.assertNotEqual(other.outputs["hosts_digest"], result.outputs["hosts_digest"])

    def test_hosts_must_be_listed_valid_and_unique(self) -> None:
        for hosts in ("", "\n\n", "root@host-one;id", "host-one", "Deploy@host-one", f"{HOST_ONE}\n{HOST_ONE}", "a@b c"):
            self.assertNotEqual(self.validate(HOSTS=hosts).code, 0, repr(hosts))

    def test_the_list_is_one_to_fifty_hosts(self) -> None:
        fifty = "".join(f"deploy@host-{n}\n" for n in range(50))
        self.assertEqual(self.validate(HOSTS=fifty).code, 0)
        self.assertNotEqual(self.validate(HOSTS=fifty + "deploy@host-50\n").code, 0)

    def test_a_commit_that_is_not_forty_lowercase_hex_digits_is_refused(self) -> None:
        for sha in ("main", "A" * 40, "a" * 39, ""):
            self.assertNotEqual(self.validate(SHA=sha).code, 0, sha)

    def test_a_dry_run_needs_its_stand_in_and_the_flags_must_be_true_or_false(self) -> None:
        self.assertNotEqual(self.validate(DRY_RUN="true").code, 0)
        self.assertEqual(self.validate(DRY_RUN="true", DRY_RUN_STUB="stub/host.sh").code, 0)
        self.assertNotEqual(self.validate(DRY_RUN="yes").code, 0)
        self.assertNotEqual(self.validate(FAIL_ON_CHANGES="1").code, 0)
        self.assertNotEqual(self.validate(FAIL_ON_CHANGES="").code, 0)

    def test_the_digest_the_action_publishes_is_the_one_the_apply_guard_recomputes(self) -> None:
        for hosts in ([HOST_ONE], [HOST_ONE, HOST_TWO], [HOST_TWO, HOST_ONE, "ci@third"]):
            with self.subTest(hosts=hosts):
                listed = "".join(f"{host}\n" for host in hosts)
                published = self.validate(HOSTS=listed).outputs["hosts_digest"]
                accepted = Sandbox(self).run(
                    find_step("apply", "guard"), apply_guard_env(HOSTS=listed, PLANNED_DIGEST=published)
                )
                self.assertEqual(accepted.code, 0, accepted.log)


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
        "FAIL_ON_CHANGES": "false",
        "SSH_KEY": "FAKE_PRIVATE_KEY_BODY_LINE_ONE_0123456789",
    }
    values.update(changes)
    return values


CLEAN = summary_line("unchanged", 0, "check", "@SHA@", 0)
WOULD_CHANGE = summary_line("would-change", 11, "check", "@SHA@", 2)


class PlanTests(unittest.TestCase):
    def plan(self, sandbox: Sandbox, **report_changes: str) -> tuple[Result, Result]:
        checked = sandbox.run(action_step("check"), check_env())
        self.assertEqual(checked.code, 0, checked.log)
        return checked, sandbox.run(action_step("report"), report_env(**report_changes))

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
        checked = sandbox.run(action_step("check"), check_env(HOSTS="deploy@host\n"))
        self.assertEqual(checked.code, 0, checked.log)
        result = sandbox.run(action_step("report"), report_env())
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
        checked = sandbox.run(action_step("check"), values)
        self.assertEqual(checked.code, 0, checked.log)
        self.assertEqual(sandbox.ssh_log(), [])
        result = sandbox.run(action_step("report"), report_env(DRY_RUN="true", SSH_KEY=""))
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["has_changes"], "true")
        self.assertTrue(result.summary.startswith("DRY RUN"), result.summary)

    def test_a_real_run_needs_both_secrets(self) -> None:
        for name in ("SSH_KEY", "KNOWN_HOSTS"):
            sandbox = Sandbox(self)
            result = sandbox.run(action_step("check"), check_env(**{name: ""}))
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
        checked = planned.run(action_step("check"), check_env())
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


def git(*args: str, cwd: Path) -> str:
    """Run git in a scratch repository with no signing, hooks or global identity."""
    command = ["git", "-c", "user.name=test", "-c", "user.email=test@example.invalid", "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args]
    return subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


PIN_SOURCES = (
    ("gated", lambda: find_step("plan", "pin"), lambda: find_step_named("plan", "Verify the checkout")),
    ("drift", lambda: drift_step("drift", "pin"), lambda: drift_step_named("drift", "Verify the checkout")),
)


class PinTests(unittest.TestCase):
    """The commit the shared actions are taken from: never empty, never the default branch."""

    def workspace(self, sandbox: Sandbox) -> Path:
        path = sandbox.dir / "ws"
        path.mkdir()
        return path

    def resolve(self, pin: Any, record: Any = None, gh_exit: int = 0, existing: str = "") -> tuple[Sandbox, Result]:
        """Run a pin step against a run record that holds `record` as its referenced workflows."""
        sandbox = Sandbox(self, gh_stub=RECORD_GH_STUB)
        ws = self.workspace(sandbox)
        if existing == "directory":
            (ws / ".gw").mkdir()
        elif existing == "file":
            (ws / ".gw").write_text("")
        elif existing == "link":
            (ws / ".gw").symlink_to(sandbox.dir / "nowhere")
        body: dict[str, Any] = {"id": 1} if record is None else {"id": 1, "referenced_workflows": record}
        (sandbox.stubs / "run.json").write_text(json.dumps(body))
        (sandbox.stubs / "gh.exit").write_text(f"{gh_exit}\n")
        step = pin()
        values = {"GH_TOKEN": "TOKEN_FOR_TEST", "REPO": "org/repo", "RUN_ID": "12345", "CALLED_WORKFLOW": step["env"]["CALLED_WORKFLOW"]}
        return sandbox, sandbox.run(step, values, {"GITHUB_WORKSPACE": str(ws)})

    def entry(self, workflow: str, sha: str, repo: str = "branchLeft/github-workflows", ref: str = "refs/tags/v1.2.3") -> dict[str, str]:
        return {"path": f"{repo}/.github/workflows/{workflow}@v1.2.3", "ref": ref, "sha": sha}

    def test_the_commit_of_the_workflow_that_was_called_is_published_logged_and_read_from_the_run_record(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            workflow = pin()["env"]["CALLED_WORKFLOW"].rsplit("/", 1)[1]
            for sha in (SHA, OTHER_SHA):
                with self.subTest(file=label, sha=sha[:1]):
                    sandbox, result = self.resolve(pin,[self.entry("docs-lint.yml", "c" * 40), self.entry(workflow, sha)])
                    self.assertEqual(result.code, 0, result.log)
                    self.assertEqual(result.outputs, {"sha": sha})
                    self.assertIn(sha, result.log)
                    self.assertEqual((sandbox.stubs / "gh.log").read_text().strip(), "api repos/org/repo/actions/runs/12345")

    def test_the_same_workflow_called_twice_at_one_commit_passes(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            workflow = pin()["env"]["CALLED_WORKFLOW"].rsplit("/", 1)[1]
            with self.subTest(file=label):
                _, result = self.resolve(pin,[self.entry(workflow, SHA), self.entry(workflow, SHA)])
                self.assertEqual(result.code, 0, result.log)

    def test_no_commit_two_commits_or_a_commit_of_another_repository_is_refused_and_never_falls_back(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            workflow = pin()["env"]["CALLED_WORKFLOW"].rsplit("/", 1)[1]
            cases = {
                "no referenced workflows": None,
                "an empty list": [],
                "only another workflow": [self.entry("docs-lint.yml", SHA)],
                "the same name in another repository": [self.entry(workflow, SHA, repo="evil/github-workflows")],
                "the same name in another path": [{"path": f"branchLeft/github-workflows/.github/workflows/x/{workflow}@v1", "ref": "r", "sha": SHA}],
                "two commits": [self.entry(workflow, SHA), self.entry(workflow, OTHER_SHA)],
                "an empty commit": [self.entry(workflow, "")],
                "a branch name": [self.entry(workflow, "main")],
                "an upper-case commit": [self.entry(workflow, "A" * 40)],
                "a short commit": [self.entry(workflow, "a" * 39)],
                "a long commit": [self.entry(workflow, "a" * 41)],
                "a commit with a second line": [self.entry(workflow, SHA + "\nsha=" + OTHER_SHA)],
            }
            for name, record in cases.items():
                with self.subTest(file=label, case=name):
                    _, result = self.resolve(pin,record)
                    self.assertNotEqual(result.code, 0)
                    self.assertEqual(result.outputs, {})
                    self.assertIn("Refusing to fall back", result.log)

    def test_a_run_record_that_cannot_be_read_is_refused_and_names_the_permission(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            with self.subTest(file=label):
                _, result = self.resolve(pin,[], gh_exit=1)
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.outputs, {})
                self.assertIn("actions: read", result.log)

    def test_a_run_id_that_is_not_a_number_is_refused_before_any_call(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            with self.subTest(file=label):
                sandbox = Sandbox(self, gh_stub=RECORD_GH_STUB)
                ws = self.workspace(sandbox)
                step = pin()
                values = {"GH_TOKEN": "T", "REPO": "org/repo", "RUN_ID": "1/../2", "CALLED_WORKFLOW": step["env"]["CALLED_WORKFLOW"]}
                result = sandbox.run(step, values, {"GITHUB_WORKSPACE": str(ws)})
                self.assertNotEqual(result.code, 0)
                self.assertFalse((sandbox.stubs / "gh.log").exists())

    def test_a_path_the_caller_already_holds_is_refused(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            workflow = pin()["env"]["CALLED_WORKFLOW"].rsplit("/", 1)[1]
            for kind in ("directory", "file", "link"):
                with self.subTest(file=label, kind=kind):
                    _, result = self.resolve(pin,[self.entry(workflow, SHA)], existing=kind)
                    self.assertNotEqual(result.code, 0)
                    self.assertEqual(result.outputs, {})
                    self.assertIn(".gw already exists", result.log)

    def test_each_workflow_names_its_own_file_as_the_one_it_looks_for(self) -> None:
        self.assertEqual(
            find_step("plan", "pin")["env"]["CALLED_WORKFLOW"],
            "branchLeft/github-workflows/.github/workflows/ansible-gated-apply.yml",
        )
        self.assertEqual(
            drift_step("drift", "pin")["env"]["CALLED_WORKFLOW"],
            "branchLeft/github-workflows/.github/workflows/ansible-drift-check.yml",
        )

    def checkout(self, sandbox: Sandbox, with_action: bool = True) -> tuple[Path, str, str]:
        ws = self.workspace(sandbox)
        gw = ws / ".gw"
        gw.mkdir()
        git("init", "-q", cwd=gw)
        if with_action:
            target = gw / ".github/actions/ssh-check-hosts"
            target.mkdir(parents=True)
            (target / "action.yml").write_text("name: stand-in\n")
        else:
            (gw / "README").write_text("nothing\n")
        git("add", "-A", cwd=gw)
        git("commit", "-q", "-m", "first", cwd=gw)
        first = git("rev-parse", "HEAD", cwd=gw)
        git("commit", "-q", "--allow-empty", "-m", "second", cwd=gw)
        return ws, first, git("rev-parse", "HEAD", cwd=gw)

    def test_the_checkout_must_be_exactly_the_pinned_commit_and_hold_the_action(self) -> None:
        for label, _, verify in PIN_SOURCES:
            with self.subTest(file=label):
                sandbox = Sandbox(self)
                ws, first, second = self.checkout(sandbox)
                env = {"GITHUB_WORKSPACE": str(ws)}
                self.assertEqual(sandbox.run(verify(), {"PINNED_SHA": second}, env).code, 0)
                landed_elsewhere = sandbox.run(verify(), {"PINNED_SHA": first}, env)
                self.assertNotEqual(landed_elsewhere.code, 0)
                self.assertIn("not at the commit", landed_elsewhere.log)
                self.assertNotEqual(sandbox.run(verify(), {"PINNED_SHA": ""}, env).code, 0)
                bare = Sandbox(self)
                ws, _, head = self.checkout(bare, with_action=False)
                self.assertNotEqual(bare.run(verify(), {"PINNED_SHA": head}, {"GITHUB_WORKSPACE": str(ws)}).code, 0)


def drift_guard_env(**changes: str) -> dict[str, str]:
    values = {
        "EVENT_NAME": "schedule",
        "REF": "refs/heads/main",
        "DEFAULT_BRANCH": "main",
        "IS_FORK": "false",
        "SHA": SHA,
        "HOST_LABEL": "edge-1",
    }
    values.update(changes)
    return values


class DriftGuardTests(unittest.TestCase):
    def guard(self, **changes: str) -> Result:
        return Sandbox(self).run(drift_step("drift", "guard"), drift_guard_env(**changes))

    def test_a_schedule_and_a_manual_run_on_the_default_branch_pass_and_say_so(self) -> None:
        for event in ("schedule", "workflow_dispatch"):
            for branch in ("main", "trunk"):
                with self.subTest(event=event, branch=branch):
                    result = self.guard(EVENT_NAME=event, DEFAULT_BRANCH=branch, REF=f"refs/heads/{branch}")
                    self.assertEqual(result.code, 0, result.log)
                    self.assertEqual(result.outputs, {"refused": "false"})

    def test_every_other_event_branch_fork_commit_or_label_is_refused_and_flagged_as_a_refusal(self) -> None:
        cases = {
            "push": {"EVENT_NAME": "push"},
            "pull request": {"EVENT_NAME": "pull_request", "REF": "refs/pull/1/merge"},
            "workflow run": {"EVENT_NAME": "workflow_run"},
            "repository dispatch": {"EVENT_NAME": "repository_dispatch"},
            "empty event": {"EVENT_NAME": ""},
            "another branch": {"REF": "refs/heads/feature"},
            "a tag": {"REF": "refs/tags/v1.0.0"},
            "payload without the default branch": {"DEFAULT_BRANCH": "", "REF": "refs/heads/"},
            "a fork": {"IS_FORK": "true"},
            "payload without the fork flag": {"IS_FORK": ""},
            "short commit": {"SHA": "a" * 39},
            "upper-case commit": {"SHA": "A" * 40},
            "commit with a second line": {"SHA": SHA + "\n" + SHA},
            "empty label": {"HOST_LABEL": ""},
            "label with a space": {"HOST_LABEL": "edge 1"},
            "label with a second line": {"HOST_LABEL": "edge-1\nx"},
            "label of 33 characters": {"HOST_LABEL": "a" * 33},
            "label starting with a dash": {"HOST_LABEL": "-edge"},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                result = self.guard(**changes)
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.outputs, {"refused": "true"})

    def test_a_label_of_thirty_two_characters_passes(self) -> None:
        self.assertEqual(self.guard(HOST_LABEL="a" + "b" * 31).code, 0)


def drift_answers(**kinds: tuple[int, str, str]) -> dict[str, tuple[int, str, str]]:
    """Host answers by destination, in the order given."""
    return {f"ci@{name}": answer for name, answer in kinds.items()}


DRIFT_CLEAN = (0, CLEAN, "")


def drifted(count: int = 2) -> tuple[int, str, str]:
    return (11, summary_line("would-change", 11, "check", "@SHA@", count), "--- before\n+++ after\n-greeting = OLD\n+greeting = NEW\n")


def refused(code: int, verdict: str, reason: str) -> tuple[int, str, str]:
    return (code, summary_line(verdict, code, "check", "@SHA@", 0, reason), "")


class DriftReportTests(unittest.TestCase):
    """The check and report steps with fail-on-changes: what is red, and what the outcome says."""

    def run_report(self, answers: dict[str, tuple[int, str, str]], fail_on_changes: str = "true") -> Result:
        sandbox = Sandbox(self)
        for dest, (code, out, err) in answers.items():
            sandbox.host(dest, code, out, err)
        listed = "".join(f"{dest}\n" for dest in answers)
        checked = sandbox.run(action_step("check"), check_env(HOSTS=listed))
        self.assertEqual(checked.code, 0, checked.log)
        result = sandbox.run(action_step("report"), report_env(FAIL_ON_CHANGES=fail_on_changes))
        for name in answers:
            self.assertNotIn(name, result.log + result.summary + json.dumps(result.outputs), name)
            self.assertNotIn(name.split("@")[1], result.log + result.summary + json.dumps(result.outputs), name)
        return result

    def test_every_host_clean_is_green_with_no_drift_and_no_changes(self) -> None:
        result = self.run_report(drift_answers(alpha=DRIFT_CLEAN, beta=DRIFT_CLEAN))
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["outcome"], "ok")
        self.assertEqual(result.outputs["has_changes"], "false")
        self.assertEqual(result.outputs["detail"], "none")
        self.assertTrue(result.summary.splitlines()[0].startswith("No drift:"), result.summary)

    def test_a_host_that_would_change_is_red_in_a_drift_check_and_green_in_a_plan(self) -> None:
        for drifting in (1, 2, 3):
            with self.subTest(drifting=drifting):
                answers = drift_answers(alpha=DRIFT_CLEAN, beta=drifted(), gamma=drifted(5), delta=drifted(7))
                answers = dict(list(answers.items())[: 1 + drifting])
                red = self.run_report(answers)
                self.assertNotEqual(red.code, 0)
                self.assertEqual(red.outputs["outcome"], "drift")
                self.assertEqual(red.outputs["detail"], f"drift: {drifting} changed")
                first = red.summary.splitlines()[0]
                self.assertTrue(first.startswith("Not clean at commit"), first)
                self.assertIn(f"drift: {drifting} changed", first)
                self.assertNotIn("approval", first)
                green = self.run_report(answers, fail_on_changes="false")
                self.assertEqual(green.code, 0, green.log)
                self.assertEqual(green.outputs["has_changes"], "true")
                self.assertTrue(green.summary.splitlines()[0].startswith(f"Changes: {drifting} of"), green.summary)

    def test_a_refusal_is_not_drift_and_names_its_reason(self) -> None:
        cases = [
            (refused(21, "busy", "ok"), "refused: busy"),
            (refused(20, "refused", "not-on-main"), "refused: not-on-main"),
            (refused(20, "refused", "rollback"), "refused: rollback"),
            (refused(20, "refused", "signer-not-allowed"), "refused: signer-not-allowed"),
            (refused(20, "refused", "playbook-not-allowed"), "refused: playbook-not-allowed"),
            (refused(20, "refused", "an-unlisted-reason"), "refused: other"),
        ]
        for answer, kind in cases:
            with self.subTest(kind=kind):
                result = self.run_report(drift_answers(alpha=DRIFT_CLEAN, beta=answer))
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.outputs["outcome"], "refused")
                self.assertEqual(result.outputs["detail"], f"{kind} (1)")
                self.assertIn(f"| {kind} |", result.summary)
                self.assertNotIn("drift:", result.summary)

    def test_an_unreachable_host_an_unreadable_answer_and_a_failed_playbook_are_each_named_and_red(self) -> None:
        cases = [
            ((255, "", "ssh: connect to host: refused\n"), "unreachable", "unreachable (1)"),
            ((0, "hello\n", ""), "no-answer", "no usable answer (1)"),
            ((0, "", ""), "no-answer", "no usable answer (1)"),
            ((30, summary_line("failed", 30, "check", "@SHA@", 0, "playbook-failed"), ""), "failed", "failed (1)"),
            ((31, summary_line("timed-out", 31, "check", "@SHA@", 0, "timeout"), ""), "failed", "failed (1)"),
            ((40, summary_line("error", 40, "check", "@SHA@", 0, "internal"), ""), "failed", "failed (1)"),
            ((0, summary_line("unchanged", 0, "check", "@SHA@", 2), ""), "failed", "failed (1)"),
        ]
        for answer, outcome, detail in cases:
            with self.subTest(outcome=outcome, detail=detail, exit=answer[0]):
                result = self.run_report(drift_answers(alpha=DRIFT_CLEAN, beta=answer))
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.outputs["outcome"], outcome)
                self.assertEqual(result.outputs["detail"], detail)

    def test_a_drifted_host_whose_diff_is_withheld_is_named_as_withheld_and_not_as_drift(self) -> None:
        secret = (11, summary_line("would-change", 11, "check", "@SHA@", 1), "+api_token = 0123456789abcdef\n")
        large = (11, summary_line("would-change", 11, "check", "@SHA@", 1), "+line of changed text\n" * 20000)
        for answer in (secret, large):
            result = self.run_report(drift_answers(alpha=DRIFT_CLEAN, beta=answer))
            self.assertNotEqual(result.code, 0)
            self.assertEqual(result.outputs["outcome"], "withheld")
            self.assertEqual(result.outputs["detail"], "diff withheld (1)")
            self.assertNotIn("0123456789abcdef", result.summary + result.log)

    def test_more_than_one_kind_gives_a_mixed_outcome_with_every_kind_counted(self) -> None:
        answers = drift_answers(alpha=drifted(), bravo=drifted(), charlie=refused(21, "busy", "ok"), delta=(255, "", ""), echo=DRIFT_CLEAN)
        result = self.run_report(answers)
        self.assertNotEqual(result.code, 0)
        self.assertEqual(result.outputs["outcome"], "mixed")
        self.assertEqual(result.outputs["detail"], "drift: 2 changed; refused: busy (1); unreachable (1)")

    def test_two_kinds_of_refusal_are_still_one_refused_outcome(self) -> None:
        answers = drift_answers(alpha=refused(21, "busy", "ok"), bravo=refused(20, "refused", "rollback"))
        result = self.run_report(answers)
        self.assertEqual(result.outputs["outcome"], "refused")
        self.assertEqual(result.outputs["detail"], "refused: busy (1); refused: rollback (1)")


ALERT_GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/gh.log"
previous=""
for arg in "$@"; do
  if [ "$previous" = "--body-file" ]; then
    cp "$arg" "$STUB_DIR/body.md"
  fi
  previous="$arg"
done
if [ "$1 $2" = "issue list" ] && [ -f "$STUB_DIR/open-issue" ]; then
  cat "$STUB_DIR/open-issue"
fi
exit 0
"""

RUN_URL = "https://github.com/org/repo/actions/runs/12345"


def alert_env(**changes: str) -> dict[str, str]:
    values = {
        "GH_TOKEN": "TOKEN_FOR_TEST",
        "GH_REPO": "org/repo",
        "HOST_LABEL": "edge-1",
        "RUN_URL": RUN_URL,
        "OUTCOME": "drift",
        "DETAIL": "drift: 1 changed",
    }
    values.update(changes)
    return values


class AlertTests(unittest.TestCase):
    def alert(self, open_issue: str | None = None, **changes: str) -> tuple[Sandbox, Result]:
        sandbox = Sandbox(self, gh_stub=ALERT_GH_STUB)
        if open_issue is not None:
            (sandbox.stubs / "open-issue").write_text(open_issue + "\n")
        return sandbox, sandbox.run(drift_step_named("alert", "Open the issue"), alert_env(**changes))

    def calls(self, sandbox: Sandbox) -> list[str]:
        path = sandbox.stubs / "gh.log"
        return path.read_text().splitlines() if path.exists() else []

    def test_with_no_open_issue_one_is_created_with_the_label_and_the_title_and_no_comment_is_made(self) -> None:
        for label in ("edge-1", "web.2"):
            with self.subTest(label=label):
                sandbox, result = self.alert(HOST_LABEL=label)
                self.assertEqual(result.code, 0, result.log)
                calls = self.calls(sandbox)
                creates = [c for c in calls if c.startswith("issue create")]
                self.assertEqual(len(creates), 1, calls)
                self.assertIn(f"--title Host drift: {label}", creates[0])
                self.assertIn(f"--label host-drift:{label}", creates[0])
                self.assertEqual([c for c in calls if c.startswith("issue comment")], [])
                self.assertTrue(any(c.startswith("issue list --state open") and f"--label host-drift:{label}" in c for c in calls), calls)

    def test_with_an_open_issue_a_comment_is_added_to_that_issue_and_none_is_created(self) -> None:
        for number in ("7", "412"):
            with self.subTest(number=number):
                sandbox, result = self.alert(open_issue=number)
                self.assertEqual(result.code, 0, result.log)
                calls = self.calls(sandbox)
                self.assertEqual([c for c in calls if c.startswith("issue create")], [])
                comments = [c for c in calls if c.startswith("issue comment")]
                self.assertEqual(len(comments), 1, calls)
                self.assertTrue(comments[0].startswith(f"issue comment {number} --body-file"), comments)

    def test_the_issue_is_found_by_its_label_and_never_by_a_title_search(self) -> None:
        sandbox, _ = self.alert()
        calls = self.calls(sandbox)
        self.assertFalse(any("--search" in c or "gh search" in c for c in calls), calls)
        self.assertTrue(any(c.startswith("label create host-drift:edge-1 --force") for c in calls), calls)

    def test_the_body_holds_the_run_the_outcome_and_the_detail_and_no_host(self) -> None:
        sandbox, result = self.alert()
        self.assertEqual(result.code, 0, result.log)
        body = (sandbox.stubs / "body.md").read_text()
        self.assertIn(RUN_URL, body)
        self.assertIn("Outcome: drift", body)
        self.assertIn("Detail: drift: 1 changed", body)
        for host in (HOST_ONE, "host-one", "--- before", "greeting"):
            self.assertNotIn(host, body)

    def test_a_missing_outcome_says_unknown_and_anything_outside_the_safe_alphabet_is_dropped(self) -> None:
        sandbox, result = self.alert(OUTCOME="", DETAIL="")
        self.assertEqual(result.code, 0, result.log)
        body = (sandbox.stubs / "body.md").read_text()
        self.assertIn("Outcome: unknown", body)
        self.assertIn("read the run", body)
        sandbox, result = self.alert(OUTCOME="drift`$(id)", DETAIL="drift: 1 changed\n- Run: https://x.example/@y <b>`id`</b>")
        self.assertEqual(result.code, 0, result.log)
        body = (sandbox.stubs / "body.md").read_text()
        for forbidden in ("`", "$", "<", ">", "@", "/x.example", "/@"):
            self.assertNotIn(forbidden, body.replace(RUN_URL, ""))
        lines = body.splitlines()
        self.assertEqual(len(lines), 5)
        self.assertEqual([line for line in lines if line.startswith("- Run: ")], [f"- Run: {RUN_URL}"])

    def test_a_label_that_is_not_a_plain_short_word_is_refused_before_any_call(self) -> None:
        for label in ("", "edge 1", "edge\n1", "a" * 33, "-edge", "x;y", "edge/1"):
            with self.subTest(label=repr(label)):
                sandbox, result = self.alert(HOST_LABEL=label)
                self.assertNotEqual(result.code, 0)
                self.assertEqual(self.calls(sandbox), [])


if __name__ == "__main__":
    if sys.argv[1:] == ["--write-snapshot"]:
        SNAPSHOT.write_text(snapshot_text())
    else:
        unittest.main()
