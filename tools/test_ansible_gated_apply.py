"""Tests of ansible-gated-apply.yml, ansible-drift-check.yml and the ssh-check-hosts
action, run against the text of each step.

Each step's `run:` text runs under bash with `ssh`, `gh` and `timeout` stubbed on
PATH. ANSIBLE_GATED_APPLY_WORKFLOW, ANSIBLE_DRIFT_CHECK_WORKFLOW and
SSH_CHECK_HOSTS_ACTION name a broken copy for a sabotage run. After a change to
any of the three files, regenerate the snapshot with
`python3 tools/test_ansible_gated_apply.py --write-snapshot`. tools/sabotage.py
breaks each control and requires its test here to go red.
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
from typing import Any, NamedTuple

import yaml

import run_workflow_steps

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


# The stand-in for ssh reads its standard input to the end, as the real ssh does
# when it is given none: a call inside a `while read` loop that does not take its
# input from /dev/null swallows the rest of the host list.
SSH_STUB = """#!/usr/bin/env bash
set -eu
cat > /dev/null
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
echo "$1" >> "$STUB_DIR/timeout.log"
shift
exec "$@"
"""

# Answers `date +%s` from a list of times, one per call, so a test controls the clock.
DATE_STUB = """#!/usr/bin/env bash
first=$(head -n 1 "$STUB_DIR/date.times")
tail -n +2 "$STUB_DIR/date.times" > "$STUB_DIR/date.rest"
mv "$STUB_DIR/date.rest" "$STUB_DIR/date.times"
printf '%s\\n' "$first"
"""

# Records the mode of each file it is asked to remove, then removes it.
RM_STUB = """#!/usr/bin/env bash
for arg in "$@"; do
  if [ -e "$arg" ]; then
    echo "$(ls -ld "$arg" | cut -c1-10) $arg" >> "$STUB_DIR/rm.log"
  fi
done
exec /bin/rm "$@"
"""

GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/gh.log"
printf '%s\\n' "$GH_HEAD"
"""


# Answers by the API path, as `gh api PATH [--jq EXPR]` does: the run record from
# run.json (exit code in gh.exit), and a tag or a commit of the shared repository
# from tag.* or commit.* (out, err, exit). On failure, like gh, it prints the
# response body on stdout and the message on stderr. A tag read with no fixture
# or an unknown path exits 98, so a test never passes on an answer it did not
# set. A commit read with no fixture answers as a commit of that id.
RECORD_GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/gh.log"
[ "$1" = api ] || exit 98
case "$2" in
  repos/*/actions/runs/*)
    code=$(cat "$STUB_DIR/gh.exit" 2> /dev/null || echo 0)
    if [ "$code" != 0 ]; then
      echo "gh: HTTP 403" >&2
      exit "$code"
    fi
    cat "$STUB_DIR/run.json"
    exit 0
    ;;
  repos/*/git/tags/*) kind=tag ;;
  repos/*/git/commits/*) kind=commit ;;
  *) exit 98 ;;
esac
if [ "$kind" = commit ] && [ ! -f "$STUB_DIR/commit.exit" ]; then
  if [ "$3" = --jq ]; then
    printf '%s\\n' "${2##*/}"
  else
    printf '{"sha":"%s"}\\n' "${2##*/}"
  fi
  exit 0
fi
[ -f "$STUB_DIR/$kind.exit" ] || exit 98
code=$(cat "$STUB_DIR/$kind.exit")
if [ "$code" = 0 ] && [ "$3" = --jq ]; then
  jq -r "$4" "$STUB_DIR/$kind.out"
else
  cat "$STUB_DIR/$kind.out"
fi
cat "$STUB_DIR/$kind.err" >&2
exit "$code"
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

    def __init__(self, test: unittest.TestCase, gh_stub: str = GH_STUB) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="gated-apply-test-"))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.stubs = self.dir / "stubs"
        self.temp = self.dir / "runner-temp"
        for path in (self.bin, self.stubs, self.temp):
            path.mkdir()
        for name, text in (("ssh", SSH_STUB), ("timeout", TIMEOUT_STUB), ("gh", gh_stub), ("yq", YQ_STUB)):
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

    def removals(self) -> list[str]:
        """The mode and path of each existing file `rm` was asked to remove, when the rm stand-in is on PATH."""
        path = self.stubs / "rm.log"
        return path.read_text().splitlines() if path.exists() else []

    def timeouts(self) -> list[str]:
        """The limit given to each call of `timeout`, in order."""
        path = self.stubs / "timeout.log"
        return path.read_text().splitlines() if path.exists() else []

    def stub(self, name: str, text: str) -> None:
        """Put another stand-in command on PATH for this sandbox only."""
        target = self.bin / name
        target.write_text(text)
        target.chmod(0o755)

    def clock(self, *times: int) -> None:
        """Make `date +%s` answer these times, one per call."""
        self.stub("date", DATE_STUB)
        self.stubs.joinpath("date.times").write_text("".join(f"{t}\n" for t in times))

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
        "HOST_TIMEOUT_MINUTES": "65",
        "TOTAL_TIMEOUT_MINUTES": "",
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
        self.assertNotIn("total-timeout", step["with"])

    def test_the_drift_job_is_bounded_by_the_caller_input_and_gives_the_hosts_less_than_that(self) -> None:
        self.assertEqual(DRIFT_JOBS["drift"]["timeout-minutes"], "${{ inputs.check-timeout-minutes }}")
        guard = drift_step("drift", "guard")
        self.assertEqual(guard["env"]["CHECK_TIMEOUT"], "${{ inputs.check-timeout-minutes }}")
        self.assertEqual(drift_step_named("drift", "Check every host")["with"]["total-timeout"], "${{ steps.guard.outputs.budget }}")

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
        # The whole condition, every term joined by &&. A term joined by || would
        # pass a per-term assertIn, so the test compares the entire string.
        condition = " ".join(str(job["if"]).split())
        self.assertEqual(
            condition,
            "needs.discover.outputs.delivery == 'true' "
            "&& needs.plan.result == 'success' "
            "&& needs.plan.outputs.has_changes != 'false' "
            "&& inputs.dry-run == false "
            "&& inputs.environment != ''",
        )
        self.assertFalse(job["concurrency"]["cancel-in-progress"])

    def test_the_plan_job_runs_only_for_delivery(self) -> None:
        self.assertEqual(
            " ".join(str(JOBS["plan"]["if"]).split()),
            "${{ !cancelled() && !failure() && needs.discover.outputs.delivery == 'true' }}",
        )
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
                "RERUN": "${{ needs.discover.outputs.recheck }}",
            }),
            ("discover", "Decide whether this run may reach a host", {
                "EVENT_NAME": "${{ github.event_name }}",
                "REF": "${{ github.ref }}",
                "DEFAULT_BRANCH": "${{ github.event.repository.default_branch }}",
                "IS_FORK": "${{ github.event.repository.fork }}",
                "ENVIRONMENT": "${{ inputs.environment }}",
                "WORKING_DIRECTORY": "${{ inputs.working-directory }}",
                "ANSIBLE_DIRECTORY": "${{ inputs.ansible-directory }}",
                "SKIP_RERUN": "${{ inputs.skip-rerun-after-merge-group }}",
            }),
            ("discover", "Confirm the merge queue", {
                "GH_TOKEN": "${{ github.token }}",
                "REPO": "${{ github.repository }}",
                "SHA": "${{ github.sha }}",
                "CHECK_NAME": "${{ inputs.merge-group-check-name }}",
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

    def test_the_apply_job_steps_are_the_guard_then_the_apply_with_no_if(self) -> None:
        steps = JOBS["apply"]["steps"]
        self.assertEqual(
            [step.get("name") for step in steps],
            [
                "Refuse unless this is the approved, planned push",
                "Apply on each host, then run it again to prove nothing is left to change",
            ],
        )
        for step in steps:
            self.assertNotIn("if", step, step.get("name"))
        # The host is the authority on order. A head check here would stop an approved
        # apply that the host is willing to run, so no step asks GitHub for the head.
        for step in steps:
            self.assertNotIn("gh api", step.get("run", ""), step.get("name"))
            self.assertNotIn("GH_TOKEN", step.get("env", {}), step.get("name"))

    def test_each_expression_that_feeds_an_action_step_comes_from_its_expected_source(self) -> None:
        cases = {
            "validate": {
                "HOSTS": "${{ inputs.hosts }}",
                "SHA": "${{ inputs.sha }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "DRY_RUN_STUB": "${{ inputs.dry-run-stub }}",
                "FAIL_ON_CHANGES": "${{ inputs.fail-on-changes }}",
                "HOST_TIMEOUT_MINUTES": "${{ inputs.host-timeout }}",
                "TOTAL_TIMEOUT_MINUTES": "${{ inputs.total-timeout }}",
            },
            "check": {
                "HOSTS": "${{ inputs.hosts }}",
                "SHA": "${{ inputs.sha }}",
                "DRY_RUN": "${{ inputs.dry-run }}",
                "DRY_RUN_STUB": "${{ inputs.dry-run-stub }}",
                "WORKING_DIRECTORY": "${{ inputs.working-directory }}",
                "HOST_TIMEOUT_MINUTES": "${{ inputs.host-timeout }}",
                "TOTAL_TIMEOUT_MINUTES": "${{ inputs.total-timeout }}",
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
                "HOST_TIMEOUT": "${{ inputs.host-timeout-minutes }}",
                "CHECK_TIMEOUT": "${{ inputs.check-timeout-minutes }}",
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
            {"hosts", "known-hosts", "check-key", "sha", "dry-run", "dry-run-stub", "working-directory", "host-timeout", "total-timeout", "environment", "fail-on-changes"},
        )
        self.assertEqual(inputs["fail-on-changes"]["default"], "false")
        self.assertEqual(inputs["total-timeout"]["default"], "")
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
                "total-timeout": "${{ steps.guard.outputs.budget }}",
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

    def test_the_alert_runs_after_a_failed_or_timed_out_check_that_passed_the_guard_and_serialises_per_host_set(self) -> None:
        job = DRIFT_JOBS["alert"]
        self.assertEqual(job["needs"], ["drift"])
        condition = " ".join(str(job["if"]).split())
        # A job that hits timeout-minutes reports `cancelled` to the job that needs it
        # (measured: https://github.com/branchLeft/github-workflows/actions/runs/39514617371),
        # so the condition has to name both results or a timeout ends silently.
        # The whole condition is compared, not each term: a term joined by || would
        # pass a per-term assertIn, and an alert that fires after a refused guard
        # is a control failing open.
        self.assertEqual(
            condition,
            "always() "
            "&& (needs.drift.result == 'failure' || needs.drift.result == 'cancelled') "
            "&& needs.drift.outputs.guard_refused != 'true'",
        )
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
        "SKIP_RERUN": "false",
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


class SkipRerunDeclarationTests(unittest.TestCase):
    def test_the_inputs_default_to_todays_behaviour(self) -> None:
        inputs = DOC[True]["workflow_call"]["inputs"]
        self.assertEqual(inputs["skip-rerun-after-merge-group"]["type"], "boolean")
        self.assertIs(inputs["skip-rerun-after-merge-group"]["default"], False)
        self.assertEqual(inputs["merge-group-check-name"]["default"], "")

    def test_lint_and_the_shards_run_unless_discover_says_the_merge_queue_already_did(self) -> None:
        for name in ("lint", "shard"):
            with self.subTest(job=name):
                self.assertEqual(JOBS[name]["if"], "needs.discover.outputs.recheck == 'true'")

    def test_the_confirmation_is_a_discover_step_that_runs_only_when_the_rerun_is_skipped(self) -> None:
        step = find_step("discover", "confirm")
        self.assertEqual(step["if"], "steps.context.outputs.rerun == 'false'")
        self.assertIn("confirm", [s.get("id") for s in JOBS["discover"]["steps"]])
        self.assertEqual(JOBS["discover"]["outputs"]["recheck"], "${{ steps.context.outputs.rerun }}")


class RerunDecisionTests(unittest.TestCase):
    def rerun(self, **changes: str) -> str:
        result = Sandbox(self).run(find_step("discover", "context"), discover_env(**changes))
        self.assertEqual(result.code, 0, result.log)
        return result.outputs.get("rerun", "<missing>")

    def test_a_delivery_run_that_asks_to_skip_does_not_rerun(self) -> None:
        self.assertEqual(self.rerun(SKIP_RERUN="true"), "false")

    def test_a_delivery_run_that_does_not_ask_reruns(self) -> None:
        self.assertEqual(self.rerun(), "true")

    def test_a_run_that_does_not_deliver_always_reruns_whatever_it_asks(self) -> None:
        for changes in ({"EVENT_NAME": "pull_request"}, {"EVENT_NAME": "merge_group"}, {"REF": "refs/heads/feature"}, {"IS_FORK": "true"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.rerun(SKIP_RERUN="true", **changes), "true")


MERGE_GROUP_NAME = "ansible / Ansible checks passed"
QUEUE_BRANCH = "gh-readonly-queue/main/pr-7-" + "c" * 40

# Answers the two reads the confirmation makes from files in the stub directory:
# the check runs of the commit, and the check suite of each run. A read with no
# file fails, as an API error does.
CONFIRM_GH_STUB = """#!/usr/bin/env bash
echo "$*" >> "$STUB_DIR/gh.log"
case "$*" in
  *"/check-runs"*) file="$STUB_DIR/runs.json" ;;
  *"/check-suites/"*) file="$STUB_DIR/suite-$(basename "${@: -1}").json" ;;
  *) exit 2 ;;
esac
if [ ! -f "$file" ]; then
  echo "gh: HTTP 403" >&2
  exit 1
fi
cat "$file"
"""


def check_run(**changes: object) -> dict[str, object]:
    run: dict[str, object] = {
        "name": MERGE_GROUP_NAME,
        "status": "completed",
        "conclusion": "success",
        "head_sha": SHA,
        "app": {"slug": "github-actions"},
        "check_suite": {"id": 501},
    }
    run.update(changes)
    return run


class ConfirmMergeGroupTests(unittest.TestCase):
    """The refusal: delivery without lint and Molecule needs proof that the merge queue passed this commit."""

    def confirm(
        self,
        runs: list[dict[str, object]] | None = None,
        suites: dict[int, dict[str, object]] | None = None,
        raw_runs: str | None = None,
        **changes: str,
    ) -> Result:
        sandbox = Sandbox(self, gh_stub=CONFIRM_GH_STUB)
        if raw_runs is not None:
            sandbox.stubs.joinpath("runs.json").write_text(raw_runs)
        elif runs is not None:
            sandbox.stubs.joinpath("runs.json").write_text(json.dumps({"total_count": len(runs), "check_runs": runs}))
        for suite_id, suite in (suites if suites is not None else {501: {"head_branch": QUEUE_BRANCH, "head_sha": SHA}}).items():
            sandbox.stubs.joinpath(f"suite-{suite_id}.json").write_text(json.dumps(suite))
        values = {
            "GH_TOKEN": "TOKEN_FOR_TEST",
            "REPO": "org/repo",
            "SHA": SHA,
            "CHECK_NAME": MERGE_GROUP_NAME,
        }
        values.update(changes)
        return sandbox.run(find_step("discover", "confirm"), values)

    def refused(self, result: Result, fragment: str) -> None:
        self.assertNotEqual(result.code, 0, result.log)
        self.assertIn(fragment, result.log)

    def test_a_passing_merge_queue_check_for_the_commit_lets_the_delivery_continue(self) -> None:
        result = self.confirm([check_run()])
        self.assertEqual(result.code, 0, result.log)

    def test_the_read_names_the_commit_and_the_check_and_the_token_stays_out_of_the_log(self) -> None:
        sandbox = Sandbox(self, gh_stub=CONFIRM_GH_STUB)
        sandbox.stubs.joinpath("runs.json").write_text(json.dumps({"check_runs": [check_run()]}))
        sandbox.stubs.joinpath("suite-501.json").write_text(json.dumps({"head_branch": QUEUE_BRANCH, "head_sha": SHA}))
        values = {"GH_TOKEN": "TOKEN_FOR_TEST", "REPO": "org/repo", "SHA": SHA, "CHECK_NAME": MERGE_GROUP_NAME}
        result = sandbox.run(find_step("discover", "confirm"), values)
        self.assertEqual(result.code, 0, result.log)
        calls = (sandbox.stubs / "gh.log").read_text()
        self.assertIn(f"repos/org/repo/commits/{SHA}/check-runs", calls)
        self.assertIn(f"check_name={MERGE_GROUP_NAME}", calls)
        self.assertNotIn("TOKEN_FOR_TEST", result.log)

    def test_a_commit_with_no_check_runs_is_refused(self) -> None:
        self.refused(self.confirm([]), "No passing")

    def test_a_check_that_failed_or_is_unfinished_or_was_skipped_is_refused(self) -> None:
        cases = (
            {"conclusion": "failure"},
            {"conclusion": "cancelled"},
            {"conclusion": "skipped"},
            {"conclusion": None, "status": "in_progress"},
            {"status": "queued", "conclusion": None},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.refused(self.confirm([check_run(**changes)]), "No passing")

    def test_a_check_of_another_name_is_refused(self) -> None:
        self.refused(self.confirm([check_run(name="ansible / Lint the Ansible inputs")]), "No passing")

    def test_a_check_run_made_by_another_app_is_refused(self) -> None:
        self.refused(self.confirm([check_run(app={"slug": "some-other-app"})]), "No passing")
        self.refused(self.confirm([check_run(app=None)]), "No passing")

    def test_a_check_run_for_another_commit_is_refused(self) -> None:
        self.refused(self.confirm([check_run(head_sha=OTHER_SHA)]), "No passing")

    def test_a_passing_check_that_is_not_from_a_merge_queue_run_is_refused(self) -> None:
        for suite in (
            {"head_branch": "main", "head_sha": SHA},
            {"head_branch": "feature", "head_sha": SHA},
            {"head_branch": None, "head_sha": SHA},
            {"head_branch": QUEUE_BRANCH, "head_sha": OTHER_SHA},
        ):
            with self.subTest(suite=suite):
                self.refused(self.confirm([check_run()], suites={501: suite}), "merge queue")

    def test_a_passing_merge_queue_run_is_found_among_other_checks(self) -> None:
        runs = [check_run(conclusion="failure", check_suite={"id": 502}), check_run(check_suite={"id": 503}), check_run(check_suite={"id": 501})]
        suites = {
            502: {"head_branch": QUEUE_BRANCH, "head_sha": SHA},
            503: {"head_branch": "main", "head_sha": SHA},
            501: {"head_branch": QUEUE_BRANCH, "head_sha": SHA},
        }
        self.assertEqual(self.confirm(runs, suites=suites).code, 0)

    def test_when_the_checks_cannot_be_read_it_cannot_tell_and_refuses(self) -> None:
        self.refused(self.confirm(None), "could not be read")

    def test_an_answer_that_is_not_json_or_has_no_check_runs_is_refused(self) -> None:
        for raw in ("", "not json", "{}", '{"check_runs": null}', "[]"):
            with self.subTest(raw=raw):
                self.assertNotEqual(self.confirm(raw_runs=raw).code, 0)

    def test_when_the_suite_cannot_be_read_it_refuses(self) -> None:
        self.refused(self.confirm([check_run()], suites={}), "could not be read")

    def test_an_empty_check_name_or_a_commit_that_is_not_forty_hex_digits_is_refused(self) -> None:
        self.refused(self.confirm([check_run()], CHECK_NAME=""), "check-name")
        for sha in ("main", "A" * 40, "a" * 39, ""):
            with self.subTest(sha=sha):
                self.assertNotEqual(self.confirm([check_run()], SHA=sha).code, 0)


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

    def test_the_timeouts_are_whole_numbers_of_minutes_and_the_total_may_be_empty(self) -> None:
        for name in ("HOST_TIMEOUT_MINUTES", "TOTAL_TIMEOUT_MINUTES"):
            for value in ("0", "-1", "1.5", "abc", "05", "10000", "1\n2", " 5"):
                with self.subTest(name=name, value=repr(value)):
                    self.assertNotEqual(self.validate(**{name: value}).code, 0)
            self.assertEqual(self.validate(**{name: "9999"}).code, 0)
            self.assertEqual(self.validate(**{name: "1"}).code, 0)
        self.assertNotEqual(self.validate(HOST_TIMEOUT_MINUTES="").code, 0)
        self.assertEqual(self.validate(TOTAL_TIMEOUT_MINUTES="").code, 0)

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
        "TOTAL_TIMEOUT_MINUTES": "",
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

    def test_every_host_is_asked_when_the_call_reads_its_standard_input(self) -> None:
        hosts = [HOST_ONE, HOST_TWO, "deploy@host-three"]
        sandbox = Sandbox(self)
        for dest in hosts:
            sandbox.host(dest, 0, CLEAN)
        checked = sandbox.run(action_step("check"), check_env(HOSTS="".join(f"{h}\n" for h in hosts)))
        self.assertEqual(checked.code, 0, checked.log)
        self.assertEqual([call.split(" ")[-2] for call in sandbox.ssh_log()], hosts)
        result = sandbox.run(action_step("report"), report_env())
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(result.outputs["has_changes"], "false")
        self.assertIn("host 3", result.summary)
        self.assertIn("Asked 3 host(s)", checked.log)

    def test_every_host_is_asked_when_the_dry_run_stand_in_reads_its_standard_input(self) -> None:
        hosts = ["ci@first-stub-host", "ci@second-stub-host", "ci@third-stub-host"]
        sandbox = Sandbox(self)
        stand_in = sandbox.dir / "workspace" / "stub" / "host.sh"
        stand_in.parent.mkdir(parents=True)
        stand_in.write_text(
            "#!/usr/bin/env bash\ncat > /dev/null\n"
            f"printf 'pull-trigger verdict=unchanged exit=0 reason=ok mode=check commit=%s playbook=ansible/playbooks/host.yml changed=0\\n' \"$2\"\n"
        )
        stand_in.chmod(0o755)
        values = check_env(DRY_RUN="true", DRY_RUN_STUB="stub/host.sh", HOSTS="".join(f"{h}\n" for h in hosts), SSH_KEY="", KNOWN_HOSTS="")
        checked = sandbox.run(action_step("check"), values, {"GITHUB_WORKSPACE": str(sandbox.dir / "workspace")})
        self.assertEqual(checked.code, 0, checked.log)
        result = sandbox.run(action_step("report"), report_env(DRY_RUN="true", SSH_KEY=""))
        self.assertEqual(result.code, 0, result.log)
        self.assertIn("host 3", result.summary)
        self.assertEqual(result.outputs["has_changes"], "false")

    def test_without_a_total_time_each_call_gets_the_host_limit_in_seconds(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 0, CLEAN)
        sandbox.host(HOST_TWO, 0, CLEAN)
        self.assertEqual(sandbox.run(action_step("check"), check_env(HOST_TIMEOUT_MINUTES="65")).code, 0)
        self.assertEqual(sandbox.timeouts(), ["3900s", "3900s"])

    def test_with_a_total_time_each_call_is_cut_to_what_is_left_and_a_host_reached_after_it_is_not_asked(self) -> None:
        hosts = [HOST_ONE, HOST_TWO, "deploy@host-three"]
        sandbox = Sandbox(self)
        for dest in hosts:
            sandbox.host(dest, 0, CLEAN)
        # The clock: the start, then one reading per host. Two minutes in all.
        sandbox.clock(1000, 1000, 1060, 1130)
        values = check_env(HOSTS="".join(f"{h}\n" for h in hosts), TOTAL_TIMEOUT_MINUTES="2")
        checked = sandbox.run(action_step("check"), values)
        self.assertEqual(checked.code, 0, checked.log)
        self.assertEqual(sandbox.timeouts(), ["120s", "60s"])
        self.assertEqual([call.split(" ")[-2] for call in sandbox.ssh_log()], hosts[:2])
        self.assertEqual(sandbox.calls("deploy@host-three"), 0)
        result = sandbox.run(action_step("report"), report_env(FAIL_ON_CHANGES="true"))
        self.assertNotEqual(result.code, 0)
        self.assertEqual(result.outputs["outcome"], "no-answer")
        self.assertEqual(result.outputs["detail"], "no usable answer (1)")
        self.assertIn("| host 3 | no-answer | not asked: the time given to the check was spent before this host |", result.summary)

    def test_a_total_time_longer_than_the_host_limit_leaves_the_host_limit_alone(self) -> None:
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 0, CLEAN)
        sandbox.host(HOST_TWO, 0, CLEAN)
        sandbox.clock(1000, 1000, 1010)
        values = check_env(HOST_TIMEOUT_MINUTES="1", TOTAL_TIMEOUT_MINUTES="85")
        self.assertEqual(sandbox.run(action_step("check"), values).code, 0)
        self.assertEqual(sandbox.timeouts(), ["60s", "60s"])

    def test_key_material_is_copied_private_and_removed_on_every_way_out(self) -> None:
        would_change = (11, WOULD_CHANGE, "+greeting = NEW\n")
        cases = {
            "every host clean, green": ({HOST_ONE: (0, CLEAN, ""), HOST_TWO: (0, CLEAN, "")}, "false", True),
            "a host would change in a plan, green": ({HOST_ONE: (0, CLEAN, ""), HOST_TWO: would_change}, "false", True),
            "a host would change in a drift check, red": ({HOST_ONE: (0, CLEAN, ""), HOST_TWO: would_change}, "true", False),
            "a host failed, red": ({HOST_ONE: (0, CLEAN, ""), HOST_TWO: (255, "", "")}, "false", False),
            "a diff is withheld, red": ({HOST_ONE: (11, WOULD_CHANGE, "+api_token = 0123456789abcdef\n"), HOST_TWO: (0, CLEAN, "")}, "false", False),
        }
        for name, (answers, fail_on_changes, green) in cases.items():
            with self.subTest(case=name):
                sandbox = Sandbox(self)
                sandbox.stub("rm", RM_STUB)
                for dest, (code, out, err) in answers.items():
                    sandbox.host(dest, code, out, err)
                self.assertEqual(sandbox.run(action_step("check"), check_env()).code, 0)
                before = (sandbox.stubs / "rm.log").read_text().splitlines()
                result = sandbox.run(action_step("report"), report_env(FAIL_ON_CHANGES=fail_on_changes))
                self.assertEqual(result.code == 0, green, result.log)
                self.assertFalse((sandbox.temp / "ansible-plan" / "keylines").exists(), name)
                removed = [line for line in (sandbox.stubs / "rm.log").read_text().splitlines() if line not in before]
                self.assertEqual(len(removed), 1, removed)
                self.assertTrue(removed[0].startswith("-rw-------"), removed)
                self.assertTrue(removed[0].endswith("/keylines"), removed)

    def test_the_check_key_and_known_hosts_are_removed_on_every_way_out_a_failure_included(self) -> None:
        def broken_date(sandbox: Sandbox) -> None:
            # A failure after the keys are written and before the loop ends: `set -e` leaves the step.
            sandbox.stub("date", "#!/usr/bin/env bash\nexit 1\n")

        cases = {
            "every host answered": (None, 0),
            "a host dropped the connection": (None, 0),
            "the step stopped on a failing command after the keys were written": (broken_date, 1),
        }
        for name, (prepare, code) in cases.items():
            with self.subTest(case=name):
                sandbox = Sandbox(self)
                sandbox.stub("rm", RM_STUB)
                sandbox.host(HOST_ONE, 0, CLEAN)
                sandbox.host(HOST_TWO, 255 if "dropped" in name else 0, "" if "dropped" in name else CLEAN)
                if prepare:
                    prepare(sandbox)
                result = sandbox.run(action_step("check"), check_env())
                self.assertEqual(result.code, code, result.log)
                log = sandbox.removals()
                self.assertEqual(sorted(line.split("/")[-1] for line in log), ["id", "known_hosts"], log)
                self.assertTrue(all(line.startswith("-rw-------") for line in log), log)
                for leftover in ("id", "known_hosts"):
                    self.assertFalse((sandbox.temp / "ansible-plan" / leftover).exists(), name)


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

    def test_the_planned_commit_must_be_40_lowercase_hex_digits(self) -> None:
        # Uppercase of the right commit: the equality check also refuses it, so
        # the format check is the one that must name the refusal.
        result = self.guard(PLANNED_SHA=SHA.upper())
        self.assertNotEqual(result.code, 0)
        self.assertIn("lowercase hex", result.log)

    def test_the_planned_host_digest_must_be_64_lowercase_hex_digits(self) -> None:
        # Uppercase of the right digest: the equality check also refuses it, so
        # the format check is the one that must name the refusal.
        result = self.guard(PLANNED_DIGEST=digest([HOST_ONE, HOST_TWO]).upper())
        self.assertNotEqual(result.code, 0)
        self.assertIn("digest is missing", result.log)


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

    def test_every_planned_host_is_applied_and_checked_again_when_the_call_reads_its_standard_input(self) -> None:
        hosts = [HOST_ONE, HOST_TWO, "deploy@host-three"]
        sandbox = Sandbox(self)
        for dest in hosts:
            sandbox.host(dest, 10, APPLIED, second=(0, UNCHANGED, ""))
        result = self.apply(sandbox, HOSTS="".join(f"{h}\n" for h in hosts))
        self.assertEqual(result.code, 0, result.log)
        for dest in hosts:
            self.assertEqual(sandbox.calls(dest), 2, dest)
        self.assertIn("Applied commit", result.log)
        self.assertIn("on 3 host(s)", result.log)

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

    def test_a_host_that_already_ran_a_newer_commit_refuses_and_the_apply_fails_with_the_reason(self) -> None:
        # The host refuses a commit older than the one it last applied: the apply fails
        # at once, names the reason in the log, and asks no second run.
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 20, summary_line("refused", 20, "apply", "@SHA@", 0, "rollback"))
        result = self.apply(sandbox, HOSTS=f"{HOST_ONE}\n")
        self.assertNotEqual(result.code, 0, result.log)
        self.assertIn("reason rollback", result.log)
        self.assertNotIn("Applied commit", result.log)
        self.assertEqual(sandbox.calls(HOST_ONE), 1)

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

    def test_the_known_hosts_secret_must_be_present(self) -> None:
        # Same shape as test_the_apply_key_secret_must_be_present: a host that would
        # apply, a non-zero exit, no ssh call, and the refusal's own message. The
        # message is what proves the refusal ran, not only that the step failed.
        sandbox = Sandbox(self)
        sandbox.host(HOST_ONE, 10, APPLIED, second=(0, UNCHANGED, ""))
        result = self.apply(sandbox, KNOWN_HOSTS="")
        self.assertNotEqual(result.code, 0, result.log)
        self.assertIn("The ssh_known_hosts secret is empty.", result.log)
        self.assertEqual(sandbox.ssh_log(), [])

    def test_the_apply_key_and_known_hosts_are_removed_on_every_way_out_a_refusal_included(self) -> None:
        cases = {
            "every host applied and clean": (0, (10, APPLIED, ""), (0, UNCHANGED, "")),
            "the first host refused": (1, (20, summary_line("refused", 20, "apply", "@SHA@", 0, "rollback"), ""), None),
            "the first host was busy": (1, (21, summary_line("busy", 21, "apply", "@SHA@", 0), ""), None),
            "the connection was dropped": (1, (255, "", ""), None),
            "the answer was not a summary line": (1, (10, "nothing\n", ""), None),
            "the second run changed something": (1, (10, APPLIED, ""), (10, APPLIED, "")),
        }
        for name, (code, first, second) in cases.items():
            with self.subTest(case=name):
                sandbox = Sandbox(self)
                sandbox.stub("rm", RM_STUB)
                sandbox.host(HOST_ONE, first[0], first[1], first[2], second=second)
                sandbox.host(HOST_TWO, 10, APPLIED, second=(0, UNCHANGED, ""))
                result = self.apply(sandbox)
                self.assertEqual(result.code, code, result.log)
                log = sandbox.removals()
                self.assertEqual(sorted(line.split("/")[-1] for line in log), ["id", "known_hosts"], log)
                self.assertTrue(all(line.startswith("-rw-------") for line in log), log)
                for leftover in ("id", "known_hosts"):
                    self.assertFalse((sandbox.temp / "ansible-apply" / leftover).exists(), name)


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
        "RERUN": "true",
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

    def test_a_delivery_run_that_skipped_the_rerun_needs_lint_and_the_shards_skipped(self) -> None:
        skipped = {"DELIVERY": "true", "RERUN": "false", "LINT": "skipped", "SHARD": "skipped", "PLAN": "success", "HAS_CHANGES": "true", "APPLY": "success"}
        self.assertEqual(self.gate(**skipped), 0)
        self.assertNotEqual(self.gate(**{**skipped, "LINT": "success"}), 0)
        self.assertNotEqual(self.gate(**{**skipped, "SHARD": "success"}), 0)
        self.assertNotEqual(self.gate(**{**skipped, "LINT": "failure"}), 0)
        self.assertNotEqual(self.gate(**{**skipped, "DISCOVER": "failure"}), 0)

    def test_a_run_that_reruns_still_needs_lint_and_the_shards_to_have_passed(self) -> None:
        self.assertNotEqual(self.gate(RERUN="true", LINT="skipped"), 0)
        self.assertNotEqual(self.gate(RERUN="true", SHARD="skipped"), 0)
        self.assertNotEqual(self.gate(RERUN=""), 0)


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


class GhReply(NamedTuple):
    """What the stub gh answers for one read: the body on stdout, the message on stderr, the exit code."""

    out: str
    err: str = ""
    code: int = 0


TAG_OBJECT_SHA = "c" * 40
PEELED_SHA = "d" * 40
SHARED_REPOSITORY = "branchLeft/github-workflows"
NOT_FOUND = GhReply('{"message":"Not Found","status":"404"}', "gh: Not Found (HTTP 404)", 1)


def plain_commit(sha: str = SHA) -> GhReply:
    return GhReply(json.dumps({"sha": sha, "tree": {"sha": "9" * 40}}))


def tag_object(object_type: Any = "commit", sha: Any = PEELED_SHA, verified: Any = True, verification: bool = True) -> GhReply:
    body: dict[str, Any] = {"sha": TAG_OBJECT_SHA, "object": {"type": object_type, "sha": sha}}
    if verification:
        body["verification"] = {"verified": verified, "reason": "valid"}
    return GhReply(json.dumps(body))


class PinTests(unittest.TestCase):
    """The commit the shared actions are taken from: never empty, never the default branch."""

    def workspace(self, sandbox: Sandbox) -> Path:
        path = sandbox.dir / "ws"
        path.mkdir()
        return path

    def resolve(
        self,
        pin: Any,
        record: Any = None,
        gh_exit: int = 0,
        existing: str = "",
        tag: GhReply | None = None,
        commit: GhReply | None = None,
        temp_dir: Path | None = None,
    ) -> tuple[Sandbox, Result]:
        """Run a pin step against a run record that holds `record` as its referenced workflows.

        `tag` and `commit` are the answers of the tag read and the commit read. Unless a test sets
        another, the id is no tag object and is a commit, whatever sha the run record names.
        A `commit` of None answers as a commit of the id asked for.
        """
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
        for kind, reply in (("tag", tag or NOT_FOUND), ("commit", commit)):
            if reply is None:
                continue
            (sandbox.stubs / f"{kind}.out").write_text(reply.out)
            (sandbox.stubs / f"{kind}.err").write_text(reply.err)
            (sandbox.stubs / f"{kind}.exit").write_text(f"{reply.code}\n")
        step = pin()
        values = {"GH_TOKEN": "TOKEN_FOR_TEST", "REPO": "org/repo", "RUN_ID": "12345", "CALLED_WORKFLOW": step["env"]["CALLED_WORKFLOW"]}
        extra = {"GITHUB_WORKSPACE": str(ws)}
        if temp_dir is not None:
            extra["TMPDIR"] = str(temp_dir)
        return sandbox, sandbox.run(step, values, extra)

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
                    self.assertEqual(
                        (sandbox.stubs / "gh.log").read_text().splitlines()[0],
                        "api repos/org/repo/actions/runs/12345",
                    )

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

    def reads(self, sandbox: Sandbox) -> list[str]:
        """The calls the step made, without the run record read."""
        return [line for line in (sandbox.stubs / "gh.log").read_text().splitlines() if "/actions/runs/" not in line]

    def called_workflow(self, pin: Any) -> str:
        return pin()["env"]["CALLED_WORKFLOW"].rsplit("/", 1)[1]

    def test_a_signed_tag_object_is_peeled_to_its_commit_which_is_published_and_both_are_logged(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            with self.subTest(file=label):
                sandbox, result = self.resolve(pin, [self.entry(self.called_workflow(pin), TAG_OBJECT_SHA)], tag=tag_object())
                self.assertEqual(result.code, 0, result.log)
                self.assertEqual(result.outputs, {"sha": PEELED_SHA})
                self.assertIn(f"commit {PEELED_SHA}, the commit tag object {TAG_OBJECT_SHA} points at", result.log)
                self.assertEqual(self.reads(sandbox), [f"api repos/{SHARED_REPOSITORY}/git/tags/{TAG_OBJECT_SHA}"])

    def test_a_plain_commit_is_published_after_the_tag_read_found_nothing_and_the_commit_read_answered(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            with self.subTest(file=label):
                sandbox, result = self.resolve(pin, [self.entry(self.called_workflow(pin), SHA)])
                self.assertEqual(result.code, 0, result.log)
                self.assertEqual(result.outputs, {"sha": SHA})
                self.assertNotIn("tag object", result.log)
                self.assertEqual(
                    self.reads(sandbox),
                    [f"api repos/{SHARED_REPOSITORY}/git/tags/{SHA}", f"api repos/{SHARED_REPOSITORY}/git/commits/{SHA} --jq .sha"],
                )

    def test_every_answer_that_is_not_a_verified_tag_of_a_commit_or_a_plain_commit_is_refused_and_publishes_nothing(self) -> None:
        cases: dict[str, tuple[GhReply, GhReply | None, str]] = {
            "an unverified tag": (tag_object(verified=False), None, "not a verified tag (verified: false)"),
            "a tag with no verification": (tag_object(verification=False), None, "(verified: missing)"),
            "a verified flag that is the text true": (tag_object(verified="true"), None, "(verified: missing)"),
            "a verified flag that is the number one": (tag_object(verified=1), None, "(verified: missing)"),
            "a tag pointing at a tag": (tag_object(object_type="tag"), None, "of type tag, not a commit"),
            "a tag pointing at a tree": (tag_object(object_type="tree"), None, "of type tree, not a commit"),
            "a tag pointing at a blob": (tag_object(object_type="blob"), None, "of type blob, not a commit"),
            "a tag with no object type": (tag_object(object_type=None), None, "of type missing"),
            "a tag with a type that is not text": (tag_object(object_type=["commit"]), None, "of type missing"),
            "a tag with an empty type": (tag_object(object_type=""), None, "of type missing"),
            "a short commit id": (tag_object(sha="d" * 39), None, f"(found {'d' * 39})"),
            "a long commit id": (tag_object(sha="d" * 41), None, f"(found {'d' * 41})"),
            "an upper-case commit id": (tag_object(sha="D" * 40), None, f"(found {'D' * 40})"),
            "a commit id that is missing": (tag_object(sha=None), None, "(found missing)"),
            "a commit id that is a number": (tag_object(sha=7), None, "(found missing)"),
            "a commit id that is very long": (tag_object(sha="d" * 500), None, "(found " + "d" * 41 + ")"),
            "an answer that is not JSON": (GhReply("<html>oops</html>"), None, "could not be read"),
            "an answer that is an empty object": (GhReply("{}"), None, "of type missing"),
            "an answer whose object is text": (GhReply('{"object": "x"}'), None, "could not be read"),
            "a forbidden tag read": (GhReply('{"message":"Forbidden"}', "gh: Forbidden (HTTP 403)", 1), None, "answered: gh: Forbidden (HTTP 403)"),
            "a failing tag read": (GhReply("", "gh: Server Error (HTTP 500)", 1), None, "answered: gh: Server Error (HTTP 500)"),
            "a tag read that never reached GitHub": (GhReply("", "error connecting to api.github.com", 1), None, "answered: error connecting"),
            "a tag read that fails with no message": (GhReply("", "", 1), None, "answered: )"),
            "a tag read whose message only mentions a 404": (GhReply("", "gh: HTTP 404 was seen (HTTP 502)", 1), None, "answered: gh: HTTP 404 was seen"),
            "neither a tag object nor a commit": (NOT_FOUND, NOT_FOUND, "neither a tag object nor a commit"),
            "a forbidden commit read": (NOT_FOUND, GhReply("", "gh: Forbidden (HTTP 403)", 1), "answered: gh: Forbidden (HTTP 403)"),
            "a failing commit read": (NOT_FOUND, GhReply("", "gh: Server Error (HTTP 500)", 1), "answered: gh: Server Error (HTTP 500)"),
            "a commit read of another id": (NOT_FOUND, plain_commit(OTHER_SHA), "answered a different id"),
            "a commit read with no id": (NOT_FOUND, GhReply("{}"), "answered a different id"),
            "a commit read that is not JSON": (NOT_FOUND, GhReply("not json"), "answered a different id"),
        }
        for label, pin, _ in PIN_SOURCES:
            for name, (tag, commit, finding) in cases.items():
                with self.subTest(file=label, case=name):
                    sandbox, result = self.resolve(pin, [self.entry(self.called_workflow(pin), SHA)], tag=tag, commit=commit)
                    self.assertNotEqual(result.code, 0, f"{name} was accepted: {result.log}")
                    self.assertEqual(result.outputs, {})
                    self.assertIn(finding, result.log)
                    self.assertIn("Refusing to fall back to the default branch.", result.log)
                    self.assertEqual(result.log.count("::error::"), 1)

    def test_a_tag_read_that_fails_for_any_reason_but_a_404_never_reaches_the_commit_read(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            for stderr in ("gh: Forbidden (HTTP 403)", "gh: Server Error (HTTP 500)", "error connecting to api.github.com", ""):
                with self.subTest(file=label, stderr=stderr):
                    sandbox, result = self.resolve(pin, [self.entry(self.called_workflow(pin), SHA)], tag=GhReply("", stderr, 1))
                    self.assertNotEqual(result.code, 0)
                    self.assertEqual(self.reads(sandbox), [f"api repos/{SHARED_REPOSITORY}/git/tags/{SHA}"])

    def test_a_tag_that_peels_to_a_commit_is_never_checked_as_a_plain_commit_and_the_shared_repository_is_read_not_the_callers(self) -> None:
        for label, pin, _ in PIN_SOURCES:
            with self.subTest(file=label):
                sandbox, _ = self.resolve(pin, [self.entry(self.called_workflow(pin), TAG_OBJECT_SHA)], tag=tag_object(verified=False))
                for call in self.reads(sandbox):
                    self.assertTrue(call.startswith(f"api repos/{SHARED_REPOSITORY}/git/tags/"), call)
                self.assertEqual(len(self.reads(sandbox)), 1)

    def test_text_the_api_or_gh_sends_back_cannot_add_a_workflow_command_or_a_second_line_to_the_message(self) -> None:
        hostile = "x\n::error::injected\n::set-output name=sha::" + OTHER_SHA
        cases = {
            "a type": (tag_object(object_type=hostile), None),
            "a commit id": (tag_object(sha=hostile), None),
            "a tag read message": (GhReply("", "gh: Forbidden (HTTP 403)\n" + hostile, 1), None),
            "a first line of tag read message": (GhReply("", hostile, 1), None),
            "a commit read message": (NOT_FOUND, GhReply("", hostile, 1)),
        }
        for label, pin, _ in PIN_SOURCES:
            for name, (tag, commit) in cases.items():
                with self.subTest(file=label, case=name):
                    _, result = self.resolve(pin, [self.entry(self.called_workflow(pin), SHA)], tag=tag, commit=commit)
                    self.assertNotEqual(result.code, 0)
                    self.assertEqual(result.outputs, {})
                    self.assertEqual(result.log.count("::error::"), 1, result.log)
                    self.assertNotIn("\n::", result.log)

    def test_the_scratch_file_for_the_error_text_is_removed_on_success_and_on_failure(self) -> None:
        answers = (
            ("peeled", {"tag": tag_object()}),
            ("plain", {}),
            ("refused", {"tag": tag_object(verified=False)}),
            ("unreadable", {"tag": GhReply("", "gh: Server Error (HTTP 500)", 1)}),
        )
        for label, pin, _ in PIN_SOURCES:
            for name, answer in answers:
                with self.subTest(file=label, answer=name):
                    scratch = Path(tempfile.mkdtemp(prefix="pin-temp-"))
                    self.addCleanup(shutil.rmtree, scratch, True)
                    self.resolve(pin, [self.entry(self.called_workflow(pin), SHA)], temp_dir=scratch, **answer)
                    self.assertEqual(list(scratch.iterdir()), [])

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
        "HOST_TIMEOUT": "65",
        "CHECK_TIMEOUT": "90",
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
                    self.assertEqual(result.outputs, {"refused": "false", "budget": "85"})

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
            "host timeout of zero": {"HOST_TIMEOUT": "0"},
            "host timeout that is not a number": {"HOST_TIMEOUT": "65.5"},
            "empty host timeout": {"HOST_TIMEOUT": ""},
            "check timeout that is not a number": {"CHECK_TIMEOUT": "ninety"},
            "check timeout below ten minutes": {"CHECK_TIMEOUT": "9"},
            "check timeout above 350 minutes": {"CHECK_TIMEOUT": "351"},
            "check timeout with a second line": {"CHECK_TIMEOUT": "90\n90"},
        }
        for name, changes in cases.items():
            with self.subTest(case=name):
                result = self.guard(**changes)
                self.assertNotEqual(result.code, 0)
                self.assertEqual(result.outputs, {"refused": "true"})

    def test_the_hosts_get_five_minutes_less_than_the_job_bound(self) -> None:
        for check, budget in (("10", "5"), ("90", "85"), ("350", "345")):
            with self.subTest(check=check):
                result = self.guard(CHECK_TIMEOUT=check)
                self.assertEqual(result.code, 0, result.log)
                self.assertEqual(result.outputs["budget"], budget)

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

    def discover_with_links(self, links: dict[str, str], files: dict[str, str]) -> Result:
        """Run the step on a project with one role, `alpha`, and a tree `ext` beside the Ansible directory.

        `files` maps a path under the sandbox to its text. `links` maps a path under the Ansible directory to a
        target path under the sandbox, and each link is made after the files, so a link can point into `ext`.
        """
        sandbox = Sandbox(self)
        ansible = sandbox.dir / "ansible"
        scenario_dir = ansible / "roles" / "alpha" / "molecule" / "default"
        scenario_dir.mkdir(parents=True)
        scenario_dir.joinpath("molecule.yml").write_text(GOOD_SCENARIO)
        ansible.joinpath("requirements.yml").write_text(EMPTY_COLLECTIONS)
        for relative, text in files.items():
            target = sandbox.dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)
        for relative, destination in links.items():
            link = ansible / relative
            link.parent.mkdir(parents=True, exist_ok=True)
            link.symlink_to(sandbox.dir / destination)
        return sandbox.run(find_step("discover", "shards"), shards_env())

    def test_a_link_in_the_role_directory_that_leaves_the_ansible_directory_fails(self) -> None:
        """Prerun resolves each entry of the role directory, so a link renamed from galaxy.yml still reaches the file it names."""
        galaxy = "---\nnamespace: branchleft\nname: probe\nversion: 1.0.0\ndependencies:\n  community.general: '*'\n"
        cases = {
            "a renamed link to a file": ({"roles/alpha/notes.yml": "ext/galaxy.yml"}, {"ext/galaxy.yml": galaxy}),
            "a link to a directory": ({"roles/alpha/branchleft": "ext/ns"}, {"ext/ns/galaxy.yml": galaxy}),
            "a link to a directory with no galaxy file in it": ({"roles/alpha/branchleft": "ext/ns"}, {"ext/ns/other.yml": "---\n"}),
            "a link to a file with an ordinary name": ({"roles/alpha/notes.yml": "ext/plain.yml"}, {"ext/plain.yml": "---\n"}),
        }
        for label, (links, files) in cases.items():
            with self.subTest(case=label):
                result = self.discover_with_links(links, files)
                self.assertNotEqual(result.code, 0, label)
                (name,) = links
                self.assertIn(f"{name} is a link that leaves the Ansible directory", result.log)
                self.assertNotIn("matrix", result.outputs)

    def test_a_role_directory_that_is_a_link_out_of_the_ansible_directory_fails(self) -> None:
        """The role directory is the working directory of the run, so prerun reads ../../galaxy.yml from where the link points."""
        files = {
            "ext/roles/x/molecule/default/molecule.yml": GOOD_SCENARIO,
            "ext/galaxy.yml": "---\nnamespace: branchleft\nname: probe\nversion: 1.0.0\n",
        }
        result = self.discover_with_links({"roles/x": "ext/roles/x"}, files)
        self.assertNotEqual(result.code, 0)
        self.assertIn("roles/x is a link that leaves the Ansible directory", result.log)
        self.assertNotIn("matrix", result.outputs)

    def discover_with_linked_parent(self, destination: str) -> Result:
        """Run the step on a project whose `roles` directory is itself a link to `destination` under the sandbox."""
        sandbox = Sandbox(self)
        ansible = sandbox.dir / "ansible"
        ansible.mkdir()
        ansible.joinpath("requirements.yml").write_text(EMPTY_COLLECTIONS)
        scenario_dir = sandbox.dir / destination / "x" / "molecule" / "default"
        scenario_dir.mkdir(parents=True)
        scenario_dir.joinpath("molecule.yml").write_text(GOOD_SCENARIO)
        sandbox.dir.joinpath("ext", "galaxy.yml").parent.mkdir(exist_ok=True)
        sandbox.dir.joinpath("ext", "galaxy.yml").write_text("---\nnamespace: branchleft\nname: probe\nversion: 1.0.0\ndependencies:\n  community.general: '*'\n")
        ansible.joinpath("roles").symlink_to(sandbox.dir / destination)
        return sandbox.run(find_step("discover", "shards"), shards_env())

    def test_a_role_directory_reached_through_a_linked_parent_out_of_the_ansible_directory_fails(self) -> None:
        """Molecule runs in the physical directory, so the role directory is judged by where it is, not by whether it is a link."""
        result = self.discover_with_linked_parent("ext/roles")
        self.assertNotEqual(result.code, 0)
        self.assertIn("roles/x is a role directory whose physical path leaves the Ansible directory", result.log)
        self.assertNotIn("matrix", result.outputs)

    def test_a_role_directory_reached_through_a_linked_parent_inside_the_ansible_directory_passes(self) -> None:
        """The control: a linked parent is refused for where it leads, not for being a link."""
        result = self.discover_with_linked_parent("ansible/real_roles")
        self.assertEqual(result.code, 0, result.log)
        self.assertEqual(json.loads(result.outputs["matrix"]), {"include": [{"name": "roles/x", "path": "roles/x"}]})

    def test_a_link_in_the_role_directory_that_cannot_be_resolved_fails(self) -> None:
        """A dangling link has no resolved path to judge, and macOS realpath fails on it, so it is refused rather than guessed at."""
        result = self.discover_with_links({"roles/alpha/notes.yml": "nowhere.yml"}, {})
        self.assertNotEqual(result.code, 0)
        self.assertIn("roles/alpha/notes.yml is a link that cannot be resolved", result.log)
        self.assertNotIn("matrix", result.outputs)

    def test_a_link_in_the_role_directory_to_a_galaxy_file_inside_the_tree_fails_by_what_it_names(self) -> None:
        """A renamed link to a galaxy.yml that is inside the tree is caught by the link rule as well as by the file rule."""
        galaxy = "---\nnamespace: branchleft\nname: probe\nversion: 1.0.0\n"
        for name in ("galaxy.yml", "galaxy.yaml"):
            with self.subTest(target=name):
                result = self.discover_with_links({"roles/alpha/notes.yml": f"ansible/roles/alpha/files/{name}"}, {f"ansible/roles/alpha/files/{name}": galaxy})
                self.assertNotEqual(result.code, 0)
                self.assertIn("roles/alpha/notes.yml is a link to a file named galaxy.yml or galaxy.yaml", result.log)

    def test_a_link_in_the_role_directory_into_a_directory_holding_a_galaxy_file_fails(self) -> None:
        galaxy = "---\nnamespace: branchleft\nname: probe\nversion: 1.0.0\n"
        cases = {
            "to the directory": ("ansible/roles/alpha/files", {"ansible/roles/alpha/files/galaxy.yml": galaxy}),
            "to a file in it": ("ansible/roles/alpha/files/other.yml", {"ansible/roles/alpha/files/galaxy.yaml": galaxy, "ansible/roles/alpha/files/other.yml": "---\n"}),
        }
        for label, (destination, files) in cases.items():
            with self.subTest(case=label):
                result = self.discover_with_links({"roles/alpha/ns": destination}, files)
                self.assertNotEqual(result.code, 0)
                self.assertIn("roles/alpha/ns is a link into a directory holding a galaxy.yml or galaxy.yaml", result.log)

    def test_a_link_that_stays_inside_the_tree_and_names_no_galaxy_file_passes(self) -> None:
        """The control: the rule refuses what prerun can read, not every link. A deeper link out of the tree is not read by prerun."""
        cases = {
            "an entry linked to a file in the tree": ({"roles/alpha/notes.yml": "ansible/roles/alpha/files/plain.yml"}, {"ansible/roles/alpha/files/plain.yml": "---\n"}),
            "an entry linked to a directory in the tree": ({"roles/alpha/docs": "ansible/roles/alpha/files"}, {"ansible/roles/alpha/files/plain.yml": "---\n"}),
            "a link two levels down that leaves the tree": ({"roles/alpha/files/shared": "ext/shared.conf"}, {"ext/shared.conf": "x\n"}),
        }
        for label, (links, files) in cases.items():
            with self.subTest(case=label):
                result = self.discover_with_links(links, files)
                self.assertEqual(result.code, 0, result.log)
                self.assertEqual(json.loads(result.outputs["matrix"]), {"include": [{"name": "roles/alpha", "path": "roles/alpha"}]})

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
            pin_seen = False
            for step in job["steps"]:
                if step.get("id") == "pin":
                    pin_seen = True
                if str(step.get("uses", "")).startswith("actions/checkout@"):
                    with_ = step.get("with", {})
                    if with_.get("repository") == "branchLeft/github-workflows":
                        # The shared actions, at the commit the run record names (the pin step).
                        # The pair is allowed only where a `pin` step runs before this checkout;
                        # elsewhere `steps.pin.outputs.sha` is empty and checks out main.
                        self.assertTrue(pin_seen, f"{job_name}: {step.get('name')} checks out the shared actions with no pin step before it")
                        self.assertEqual(with_.get("ref"), "${{ steps.pin.outputs.sha }}", f"{job_name}: {step.get('name')}")
                        continue
                    self.assertNotIn("repository", with_, f"{job_name}: {step.get('name')}")
                    self.assertNotIn("ref", with_, f"{job_name}: {step.get('name')}")

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
