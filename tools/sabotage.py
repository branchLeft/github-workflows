"""Sabotage proof for tools/test_ansible_gated_apply.py: break each control, require its test red.

What it does, how to run one case and how to add one: README, section
"ansible-drift-check.yml", paragraph on tools/sabotage.py. The cases are in
tools/sabotage_proof/cases/, one module per family of controls.
"""

from __future__ import annotations

import sys

from sabotage_proof.cli import main

if __name__ == "__main__":
    sys.exit(main(__doc__))
