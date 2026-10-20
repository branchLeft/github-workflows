"""Run named steps of one job of a workflow file, exactly as their `run:` text is written.

The self-test of ansible-gated-apply.yml uses it so that the job proving the lint
tool rejects bad input runs the reusable workflow's own install and lint steps, and
not a second copy of their commands. The workflow file is the one home.

    python3 tools/run_workflow_steps.py WORKFLOW JOB \
        --input working-directory=DIR --input ansible-directory=DIR \
        --step "Step name" [--step "Another step"] \
        [--step-input "Step name" ansible-directory=OTHER]

Only the expression `${{ inputs.NAME }}` is understood, and it is replaced by the
value given with --input (or by the step's own --step-input). Any other expression,
a step with `uses:` or `if:`, a missing step or a missing input stops the run with
exit status 99 before any step runs. Otherwise the steps run in order under
`bash -e`, each with the job's default working directory, and the run stops at the
first step that fails with that step's own exit status.

A step may append to GITHUB_PATH and GITHUB_ENV as it does on a runner; the lines
reach the steps after it. RUNNER_TEMP is taken from the environment or made.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml

USAGE_ERROR = 99
EXPRESSION = re.compile(r"\$\{\{\s*([^}]*?)\s*\}\}")
INPUT_REFERENCE = re.compile(r"inputs\.([A-Za-z0-9_-]+)")


class UsageError(Exception):
    """The request cannot be run as written."""


def substitute(text: str, values: dict[str, str]) -> str:
    """Replace every `${{ inputs.NAME }}` in ``text``; any other expression is refused."""

    def replace(match: re.Match[str]) -> str:
        reference = INPUT_REFERENCE.fullmatch(match.group(1))
        if reference is None:
            raise UsageError(f"unsupported expression: {match.group(0)}")
        name = reference.group(1)
        if name not in values:
            raise UsageError(f"no value for input {name}")
        return values[name]

    return EXPRESSION.sub(replace, text)


def parse_pairs(items: Sequence[str]) -> dict[str, str]:
    """Return ``{name: value}`` from NAME=VALUE items."""
    pairs: dict[str, str] = {}
    for item in items:
        name, separator, value = item.partition("=")
        if not separator or not name:
            raise UsageError(f"not NAME=VALUE: {item}")
        pairs[name] = value
    return pairs


def load_job(workflow: Path, job: str) -> dict[str, Any]:
    """Return one job of the workflow file."""
    try:
        document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
        loaded = document["jobs"][job]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as error:
        raise UsageError(f"cannot read job {job} of {workflow}: {type(error).__name__}") from error
    if not isinstance(loaded, dict) or not isinstance(loaded.get("steps"), list):
        raise UsageError(f"job {job} has no steps")
    return loaded


def plan_steps(
    job: dict[str, Any],
    names: Sequence[str],
    inputs: dict[str, str],
    overrides: dict[str, dict[str, str]],
) -> list[tuple[str, str, dict[str, str]]]:
    """Return ``(name, script, env)`` per requested step, with every expression resolved."""
    by_name = {str(step.get("name")): step for step in job["steps"]}
    for name in overrides:
        if name not in names:
            raise UsageError(f"--step-input names a step that is not run: {name}")
    planned: list[tuple[str, str, dict[str, str]]] = []
    for name in names:
        step = by_name.get(name)
        if step is None:
            raise UsageError(f"no step named {name!r}")
        if "run" not in step:
            raise UsageError(f"step {name!r} has no run text")
        for key in ("uses", "if", "shell", "working-directory", "continue-on-error"):
            if key in step:
                raise UsageError(f"step {name!r} sets {key}, which this runner does not honour")
        values = {**inputs, **overrides.get(name, {})}
        env = {key: substitute(str(value), values) for key, value in step.get("env", {}).items()}
        # The expressions in a run body are refused by the workflow's own tests; here too.
        script = str(step["run"])
        if "${{" in script:
            raise UsageError(f"step {name!r} has an expression in its run text")
        planned.append((name, script, env))
    return planned


def apply_file_commands(path_file: Path, env_file: Path, env: dict[str, str]) -> None:
    """Carry what a step wrote to GITHUB_PATH and GITHUB_ENV into ``env`` for the steps after it."""
    additions = [line for line in path_file.read_text().splitlines() if line]
    if additions:
        env["PATH"] = os.pathsep.join([*additions, env["PATH"]])
    for line in env_file.read_text().splitlines():
        name, separator, value = line.partition("=")
        if separator and name:
            env[name] = value
    path_file.write_text("")
    env_file.write_text("")


def run_steps(planned: Sequence[tuple[str, str, dict[str, str]]], directory: Path) -> int:
    """Run the steps in order; return 0, or the exit status of the first step that fails."""
    scratch = Path(tempfile.mkdtemp(prefix="run-workflow-steps-"))
    path_file = scratch / "github_path"
    env_file = scratch / "github_env"
    path_file.write_text("")
    env_file.write_text("")
    env = dict(os.environ)
    env.setdefault("RUNNER_TEMP", str(scratch))
    env["GITHUB_PATH"] = str(path_file)
    env["GITHUB_ENV"] = str(env_file)
    for name, script, step_env in planned:
        script_file = scratch / "step.sh"
        script_file.write_text(script, encoding="utf-8")
        print(f"::group::{name}", flush=True)
        completed = subprocess.run(
            ["bash", "--noprofile", "--norc", "-e", str(script_file)],
            env={**env, **step_env},
            cwd=directory,
            check=False,
        )
        print("::endgroup::", flush=True)
        if completed.returncode != 0:
            return completed.returncode
        apply_file_commands(path_file, env_file, env)
    return 0


def main(argv: Sequence[str]) -> int:
    """Parse the request, resolve it whole, then run it."""
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n", maxsplit=1)[0])
    parser.add_argument("workflow", type=Path)
    parser.add_argument("job")
    parser.add_argument("--input", action="append", default=[], metavar="NAME=VALUE")
    parser.add_argument("--step", action="append", default=[], metavar="NAME")
    parser.add_argument("--step-input", action="append", nargs=2, default=[], metavar=("STEP", "NAME=VALUE"))
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return USAGE_ERROR if stop.code else 0
    try:
        if not args.step:
            raise UsageError("no --step given")
        job = load_job(args.workflow, args.job)
        inputs = parse_pairs(args.input)
        overrides: dict[str, dict[str, str]] = {}
        for step_name, pair in args.step_input:
            overrides.setdefault(step_name, {}).update(parse_pairs([pair]))
        defaults = job.get("defaults", {}).get("run", {})
        directory = Path(substitute(str(defaults.get("working-directory", ".")), inputs))
        planned = plan_steps(job, args.step, inputs, overrides)
    except UsageError as error:
        print(f"run_workflow_steps: {error}", file=sys.stderr)
        return USAGE_ERROR
    return run_steps(planned, directory)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
