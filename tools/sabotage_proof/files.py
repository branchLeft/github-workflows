"""The files a case breaks, and the test module that must go red, as paths from the repository root."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TESTS = "tools/test_ansible_gated_apply.py"

ACTION = ".github/actions/ssh-check-hosts/action.yml"
SCAN = ".github/actions/ssh-check-hosts/host_output_scan.py"
GATED_APPLY = ".github/workflows/ansible-gated-apply.yml"
DRIFT_CHECK = ".github/workflows/ansible-drift-check.yml"
JUMP_PROOF = "tools/ssh_jump_proof.py"
JUMP_SELF_TEST = ".github/workflows/ssh-jump-self.yml"
