#!/usr/bin/env python3
"""Check a caller's workflow keeps the no-op deploy gate's contract.
The rules and their reasons are in check-apply-gate.md beside this file."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import PurePosixPath
from typing import NamedTuple

ACTION_PREFIX = "branchLeft/github-workflows/.github/actions/plan-has-changes@"
CHECK_PREFIX = "branchLeft/github-workflows/.github/actions/check-apply-gate@"
STATUS_FUNCTIONS = ("always", "failure", "cancelled")
DEFAULT_SAVE_STEP = "Save the update plan for the no-change check"
JOB_KEY = re.compile(r"^  ([A-Za-z0-9_-]+):[ \t]*(?:#.*)?$")
FIELD = re.compile(r"^    ([A-Za-z0-9_-]+):[ \t]*(.*)$")
STEP_FIELD = re.compile(r"^        ([A-Za-z0-9_-]+):[ \t]*(.*)$")


class Finding(NamedTuple):
    code: str
    job: str
    message: str


class Unreadable(Exception):
    """The workflow is not a shape this script understands; the check fails."""


def _strip_comment(value: str) -> str:
    """The value up to a `#` that follows a space or tab, trimmed. Scanned with
    `str.find`, so a long run of spaces costs one pass."""
    at = value.find("#")
    while at != -1:
        if at > 0 and value[at - 1] in " \t":
            return value[:at].strip()
        at = value.find("#", at + 1)
    return value.strip()


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def job_blocks(text: str) -> dict[str, list[str]]:
    """Each top-level job's lines, keyed by job id."""
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if re.fullmatch(r"jobs:[ \t]*(?:#.*)?", line)]
    if len(starts) != 1:
        raise Unreadable("expected exactly one top-level `jobs:` key")
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    for line in lines[starts[0] + 1 :]:
        if line.strip() and not line.startswith(" ") and not line.startswith("#"):
            break
        key = JOB_KEY.match(line)
        if key:
            current = key.group(1)
            if current in jobs:
                raise Unreadable(f"job {current} is listed twice")
            jobs[current] = []
        elif current is not None:
            jobs[current].append(line)
    return jobs


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _scalar_or_block(lines: list[str], index: int, value: str) -> str:
    """A field's value: one line, or the folded text of a `>`/`|` block below it."""
    if re.fullmatch(r"[>|][+-]?", value):
        body = []
        for follow in lines[index + 1 :]:
            if follow.strip() and _indent(follow) <= _indent(lines[index]):
                break
            if follow.strip():
                body.append(follow.strip())
        return " ".join(body)
    text = _strip_comment(value)
    if text:
        # A plain or quoted scalar may continue on more indented lines.
        for follow in lines[index + 1 :]:
            if not follow.strip() or _indent(follow) <= _indent(lines[index]):
                break
            text += " " + _strip_comment(follow)
    return _unquote(text)


def field(job: list[str], key: str) -> str | None:
    """A job-level field at four spaces, or None; more than one is unreadable."""
    found = []
    for i, line in enumerate(job):
        match = FIELD.match(line)
        if match and match.group(1) == key:
            found.append(_scalar_or_block(job, i, match.group(2)))
    if len(found) > 1:
        raise Unreadable(f"field `{key}` appears more than once in one job")
    return found[0] if found else None


def needs_of(job: list[str]) -> list[str]:
    """The job ids a job needs, from a scalar, a flow list or a block list."""
    for i, line in enumerate(job):
        match = FIELD.match(line)
        if not match or match.group(1) != "needs":
            continue
        value = _strip_comment(match.group(2))
        if value.startswith("["):
            return [n.strip() for n in value.strip("[]").split(",") if n.strip()]
        if value:
            return [value]
        names = []
        for follow in job[i + 1 :]:
            item = re.match(r"^      - (.+)$", follow)
            if not item:
                if follow.strip() and not follow.lstrip().startswith("#"):
                    break
                continue
            names.append(_strip_comment(item.group(1)))
        return names
    return []


def outputs_of(job: list[str]) -> dict[str, str]:
    """The job's `outputs:` map, one key per line at six spaces."""
    found: dict[str, str] = {}
    inside = False
    for line in job:
        if re.fullmatch(r"    outputs:[ \t]*", line):
            inside = True
            continue
        if inside:
            if line.strip() and not line.startswith("      "):
                break
            item = re.match(r"^      ([A-Za-z0-9_-]+):[ \t]*(.*)$", line)
            if item:
                found[item.group(1)] = _unquote(_strip_comment(item.group(2)))
    return found


class Step(NamedTuple):
    fields: dict[str, str]
    text: str
    raw: str


def steps_of(job: list[str]) -> list[Step]:
    """The job's steps in order, each with its keys and its raw text."""
    start = next((i for i, line in enumerate(job) if re.fullmatch(r"    steps:[ \t]*", line)), None)
    if start is None:
        return []
    raw: list[list[str]] = []
    originals: list[list[str]] = []
    for line in job[start + 1 :]:
        if line.strip() and not line.startswith("      ") and not line.lstrip().startswith("#"):
            break
        if line.startswith("      - "):
            raw.append(["        " + line[len("      - ") :]])
            originals.append([line])
        elif raw:
            raw[-1].append(line)
            originals[-1].append(line)
    steps = []
    for lines, original in zip(raw, originals):
        fields: dict[str, str] = {}
        for i, line in enumerate(lines):
            match = STEP_FIELD.match(line)
            if match and match.group(1) not in fields:
                fields[match.group(1)] = _scalar_or_block(lines, i, match.group(2))
        steps.append(Step(fields, "\n".join(lines), "\n".join(original)))
    return steps


def env_lines(step: Step) -> list[str]:
    """The lines under the step's own `env:` key, which sits at eight spaces."""
    lines = step.text.splitlines()
    for i, line in enumerate(lines):
        if re.fullmatch(r"        env:[ \t]*(?:#.*)?", line):
            body = []
            for follow in lines[i + 1 :]:
                if follow.strip() and _indent(follow) <= 8:
                    break
                body.append(follow)
            return body
    return []


def condition_of(value: str) -> str:
    """An `if:` value as one expression: whitespace folded, a whole-value `${{ }}` removed."""
    text = " ".join(value.split())
    wrapped = re.fullmatch(r"\$\{\{\s*(.*?)\s*\}\}", text)
    return wrapped.group(1) if wrapped else text


def _outside_quotes(condition: str) -> str:
    """The condition with every quoted string replaced by an empty pair of quotes."""
    return re.sub(r"'[^']*'|\"[^\"]*\"", "''", condition)


def and_operands(condition: str) -> list[str]:
    """Split an expression on `&&` outside parentheses and quotes."""
    operands, current, depth, quote, i = [], "", 0, "", 0
    while i < len(condition):
        char = condition[i]
        if quote:
            quote = "" if char == quote else quote
        elif char in "'\"":
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif depth == 0 and condition.startswith("&&", i):
            operands.append(current.strip())
            current, i = "", i + 2
            continue
        current += char
        i += 1
    operands.append(current.strip())
    return operands


def check_if(plan: str, apply: str, job: list[str]) -> list[Finding]:
    raw = field(job, "if")
    if raw is None:
        return [Finding("GATE-IF-MISSING", apply, "the apply job has no `if:`, so it never skips on the verdict")]
    condition = condition_of(raw)
    findings = []
    bare = _outside_quotes(condition)
    if "||" in bare:
        findings.append(Finding("GATE-IF-OR", apply, "an `||` lets one operand alone start the job"))
    for name in STATUS_FUNCTIONS:
        if re.search(rf"\b{name}\s*\(", bare):
            findings.append(
                Finding("GATE-IF-STATUS", apply, f"`{name}()` runs the job when the plan job failed or was skipped")
            )
    wanted = f"needs.{plan}.outputs.has_changes != 'false'"
    mentions = [o for o in and_operands(condition) if "has_changes" in o]
    if [" ".join(o.split()) for o in mentions] != [wanted]:
        findings.append(
            Finding(
                "GATE-IF-OPERAND",
                apply,
                f"the one has_changes operand must be exactly `{wanted}`, as its own top-level `&&` operand",
            )
        )
    return findings


def code_of(text: str) -> str:
    """The text without its comment-only lines, which are not code."""
    return "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))


def reads_action_output(step: Step, action_id: str) -> bool:
    """Whether a non-comment line under the step's own `env:` binds the action's
    `has_changes` output to a variable that a non-comment line then reads as
    `$NAME` or `${NAME}`."""
    code = code_of(step.text)
    source = r"\$\{\{\s*steps\." + re.escape(action_id) + r"\.outputs\.has_changes\s*\}\}"
    bindings = code_of("\n".join(env_lines(step)))
    for bound in re.finditer(r"^[ \t]+([A-Za-z_][A-Za-z0-9_]*):[ \t]*" + source + r"[ \t]*$", bindings, re.M):
        if re.search(r"\$\{?" + bound.group(1) + r"\b", code):
            return True
    return False


GLOBS = re.compile(r"\*\.json|preview[-.]?\*")


def _stem(plan_file: str) -> str:
    """The plan file's name without its directory and extension, so that
    `preview-plan`, `preview-plan.js` and `preview-plan.json.bak` all contain it."""
    return PurePosixPath(plan_file).name


def _step_problem(step: Step, plan_file: str) -> str | None:
    """Why a step other than the save step and the action may expose the saved plan."""
    code = code_of(step.text)
    for needle, why in ((_stem(plan_file), "names the plan file"), ("--save-plan", "saves a plan"), ("upload-artifact", "uploads")):
        if needle in code:
            return why
    if GLOBS.search(code):
        return "globs over the working directory's JSON files"
    return None


def check_plan_file(jobs: dict[str, list[str]], plan_jobs: list[str], plan_file: str, save_name: str) -> list[Finding]:
    """The saved plan can hold resource inputs and ciphertext: only the save step and
    the action may name it, the save step discards its stdout and may fail without
    failing the job, and nothing uploads, prints or globs it."""
    findings = []
    stem = _stem(plan_file)
    for name, lines in jobs.items():
        if name in plan_jobs:
            continue
        text = "\n".join(lines)
        for step in steps_of(lines):
            # The job that runs this check has to name the plan file to pass it on.
            if step.fields.get("uses", "").startswith(CHECK_PREFIX):
                text = text.replace(step.raw, "", 1)
        code = code_of(text)
        if stem in code or "--save-plan" in code:
            findings.append(Finding("GATE-PLAN-READ", name, f"a job that is not a plan job names the plan file `{plan_file}` or saves a plan"))
    for name in plan_jobs:
        findings += _check_plan_job(name, steps_of(jobs[name]), plan_file, save_name)
    return findings


def check_experimental(text: str) -> list[Finding]:
    """`--save-plan` must work without the experimental switch, which no caller sets."""
    if "PULUMI_EXPERIMENTAL" in code_of(text):
        return [Finding("GATE-PLAN-EXPERIMENTAL", "workflow", "the workflow mentions PULUMI_EXPERIMENTAL; the saved plan must not need it")]
    return []


def _check_plan_job(name: str, steps: list[Step], plan_file: str, save_name: str) -> list[Finding]:
    saves = [s for s in steps if s.fields.get("name") == save_name]
    if len(saves) != 1:
        return [Finding("GATE-PLAN-SAVE", name, f"expected exactly one step named `{save_name}`, found {len(saves)}")]
    save = saves[0]
    code = code_of(save.text)
    findings = []
    if save.fields.get("continue-on-error") != "true":
        findings.append(Finding("GATE-PLAN-SAVE", name, "the save step must set `continue-on-error: true`; a missing plan reads as changes"))
    command = r"--save-plan[ \t]+" + re.escape(plan_file) + r"[ \t]*>[ \t]*/dev/null[ \t]*$"
    if not re.search(command, code, re.M) or code.count("--save-plan") != 1 or code.count(_stem(plan_file)) != 1:
        findings.append(
            Finding(
                "GATE-PLAN-SAVE",
                name,
                f"the save step must run `--save-plan {plan_file} > /dev/null` once, with nothing after it on that command and nothing else in the step naming the file",
            )
        )
    for step in steps:
        label = step.fields.get("name", "an unnamed step")
        if step is save:
            continue
        if step.fields.get("uses", "").startswith(ACTION_PREFIX):
            given = re.search(r"^[ \t]+plan-file:[ \t]*(\S+)", code_of(step.text), re.M)
            if not given or _unquote(given.group(1)) != plan_file:
                findings.append(Finding("GATE-PLAN-INPUT", name, f"step `{label}` must pass `plan-file: {plan_file}`"))
            continue
        problem = _step_problem(step, plan_file)
        if problem:
            findings.append(Finding("GATE-PLAN-READ", name, f"step `{label}` {problem}; only the save step and the action may touch the saved plan"))
    return findings


def check_pair(jobs: dict[str, list[str]], plan: str, apply: str, verdict: str, summary: str | None) -> list[Finding]:
    missing = [Finding("GATE-NO-JOB", name, "no such job in the workflow") for name in (plan, apply) if name not in jobs]
    if missing:
        return missing
    findings = []
    if plan not in needs_of(jobs[apply]):
        findings.append(Finding("GATE-NEEDS", apply, f"the apply job does not need `{plan}`"))
    findings += check_if(plan, apply, jobs[apply])

    published = f"${{{{ steps.{verdict}.outputs.has_changes }}}}"
    if outputs_of(jobs[plan]).get("has_changes") != published:
        findings.append(Finding("GATE-OUTPUT", plan, f"the job output `has_changes` must be exactly `{published}`"))

    steps = steps_of(jobs[plan])
    actions = [s for s in steps if s.fields.get("uses", "").startswith(ACTION_PREFIX)]
    if len(actions) != 1:
        findings.append(
            Finding("GATE-ACTION", plan, f"expected exactly one step using the plan-has-changes action, found {len(actions)}")
        )
    verdicts = [s for s in steps if s.fields.get("id") == verdict]
    if len(verdicts) != 1:
        findings.append(Finding("GATE-VERDICT-STEP", plan, f"expected exactly one step with id `{verdict}`, found {len(verdicts)}"))
        return findings
    step = verdicts[0]
    if condition_of(step.fields.get("if", "")) != "always()":
        findings.append(Finding("GATE-VERDICT-ALWAYS", plan, f"step `{verdict}` must run `if: always()` so a verdict is always written"))
    if len(actions) == 1 and step is not actions[0]:
        action_id = actions[0].fields.get("id", "")
        if not action_id or not reads_action_output(step, action_id):
            findings.append(
                Finding(
                    "GATE-VERDICT-SOURCE",
                    plan,
                    f"step `{verdict}` must bind the action's `has_changes` output to a variable in `env:` and read it",
                )
            )
    if summary is not None:
        names = [s.fields.get("name", "") for s in steps]
        if summary not in names:
            findings.append(Finding("GATE-ORDER", plan, f"no step named `{summary}`"))
        elif steps.index(step) > names.index(summary):
            findings.append(Finding("GATE-ORDER", plan, f"step `{verdict}` must come before `{summary}`, so its line is first"))
    return findings


def parse_pairs(pairs: str) -> list[tuple[str, str]]:
    result = []
    for token in pairs.split():
        plan, _, apply = token.partition(":")
        if not plan or not apply or ":" in apply:
            raise Unreadable(f"pair `{token}` is not PLAN_JOB:APPLY_JOB")
        result.append((plan, apply))
    if not result:
        raise Unreadable("no PLAN_JOB:APPLY_JOB pair given")
    return result


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="check-apply-gate.py", description=__doc__.split("\n")[0])
    parser.add_argument("--workflow", required=True)
    parser.add_argument("--pairs", required=True, help="whitespace-separated PLAN_JOB:APPLY_JOB")
    parser.add_argument("--verdict-step", required=True)
    parser.add_argument("--summary-step", default=None)
    parser.add_argument("--plan-file", required=True, help="the path --save-plan writes, as the workflow spells it")
    parser.add_argument("--save-step", default=DEFAULT_SAVE_STEP, help="name of the step that saves the plan")
    args = parser.parse_args(argv[1:])
    try:
        with open(args.workflow, encoding="utf-8") as handle:
            text = handle.read()
        jobs = job_blocks(text)
        found: list[Finding] = []
        pairs = parse_pairs(args.pairs)
        for plan, apply in pairs:
            found += check_pair(jobs, plan, apply, args.verdict_step, args.summary_step or None)
        plan_jobs = sorted({plan for plan, _ in pairs if plan in jobs})
        found += check_plan_file(jobs, plan_jobs, args.plan_file, args.save_step)
        found += check_experimental(text)
    except (OSError, UnicodeDecodeError, Unreadable) as error:
        print(f"::error file={args.workflow}::GATE-UNREADABLE {error}")
        return 1
    for item in found:
        print(f"::error file={args.workflow}::{item.code} {item.job}: {item.message}")
    if found:
        return 1
    print(f"ok: {args.workflow} gates every listed apply job on an explicit false")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
