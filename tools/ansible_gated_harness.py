"""The harness the ansible-gated test modules share: a run's result and a scratch directory.

test_ansible_gated_apply.py and test_ansible_gated_step.py each keep their own stand-in
commands and their own environment for a run, because those differ between the two gates.
What is the same lives here: the result of a run, the scratch directory with the stand-in
commands on PATH, and the subprocess call that runs a step's script with the runner's
default umask.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


class Result:
    def __init__(
        self, proc: subprocess.CompletedProcess[str], outputs: dict[str, str], summary: str
    ) -> None:
        self.proc = proc
        self.code = proc.returncode
        self.log = proc.stdout + proc.stderr
        self.outputs = outputs
        self.summary = summary

    def visible_log(self) -> str:
        """The log as a reader sees it: the runner consumes the add-mask commands."""
        return "\n".join(
            line for line in self.log.splitlines() if not line.startswith("::add-mask::")
        )


class SandboxBase:
    """A scratch directory with stand-in commands on PATH and the files the runner provides.

    `stub_commands` are (name, text) pairs written as executables into bin/. Subclasses build
    the environment for `run_script` themselves, because each gate's environment differs.
    """

    def __init__(self, test: unittest.TestCase, prefix: str, stub_commands: tuple[tuple[str, str], ...]) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix=prefix))
        test.addCleanup(shutil.rmtree, self.dir, True)
        self.bin = self.dir / "bin"
        self.stubs = self.dir / "stubs"
        self.temp = self.dir / "runner-temp"
        for path in (self.bin, self.stubs, self.temp):
            path.mkdir()
        for name, text in stub_commands:
            self.stub(name, text)
        self.output = self.dir / "github-output"
        self.summary = self.dir / "github-summary"
        self.output.write_text("")
        self.summary.write_text("")

    def stub(self, name: str, text: str) -> None:
        """Put a stand-in command on PATH for this sandbox only."""
        target = self.bin / name
        target.write_text(text)
        target.chmod(0o755)

    def run_script(self, script_text: str, env: dict[str, str], cwd: Path) -> Result:
        script = self.dir / "step.sh"
        script.write_text(script_text)
        proc = subprocess.run(
            ["bash", "--noprofile", "--norc", "-eo", "pipefail", str(script)],
            env=env,
            capture_output=True,
            text=True,
            cwd=cwd,
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
