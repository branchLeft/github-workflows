"""Break a copy of the repository with a case's edits and run the tests in it."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .files import REPO, TESTS
from .model import Case, Edit


def apply_edits(root: Path, edits: list[Edit]) -> str | None:
    """Apply the edits under root. Returns a reason if one does not match, else None."""
    for edit in edits:
        target = root / edit.path
        text = target.read_text()
        found = text.count(edit.old)
        if found != edit.count:
            return f"{edit.path}: the text to break was found {found} time(s), expected {edit.count}: {edit.old[:70]!r}"
        if edit.new == edit.old:
            return f"{edit.path}: the edit changes nothing"
        target.write_text(text.replace(edit.old, edit.new))
    return None


def run_tests(root: Path, names: list[str]) -> tuple[int, set[str], str]:
    """Run the test module in root (all of it, or only names). Returns code, red tests as Class.method, log."""
    proc = subprocess.run(
        [sys.executable, str(root / TESTS), *names],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    log = proc.stdout + proc.stderr
    red = {f"{cls}.{meth}" for meth, cls in re.findall(r"(?m)^(?:FAIL|ERROR): (\w+) \(__main__\.(\w+)\.\w+\)", log)}
    return proc.returncode, red, log


def copy_repo(destination: Path) -> None:
    shutil.copytree(REPO, destination, ignore=shutil.ignore_patterns(".git", "__pycache__", "node_modules"))


def run_case(case: Case, discover: bool) -> tuple[Case, bool, str]:
    with tempfile.TemporaryDirectory(prefix="sabotage-") as scratch:
        root = Path(scratch) / "repo"
        copy_repo(root)
        problem = apply_edits(root, case.edits)
        if problem:
            return case, False, f"CANNOT BREAK  {problem}"
        subprocess.run([sys.executable, str(root / TESTS), "--write-snapshot"], cwd=root, check=True)
        if discover:
            _, red, _ = run_tests(root, [])
            return case, True, "red: " + ", ".join(sorted(red)) if red else "NOT RED"
        code, red, log = run_tests(root, case.red)
        missing = [name for name in case.red if name not in red]
        if code == 0 or missing:
            return case, False, f"NOT RED  expected red: {missing or case.red}\n{log[-1500:]}"
        return case, True, "RED  " + ", ".join(sorted(red))
