"""Sabotage proof for tools/test_ansible_gated_apply.py: break each control, require its test red.

See sabotage.comments.md, block 1.
"""

from __future__ import annotations

import sys

from sabotage_proof.cli import main

if __name__ == "__main__":
    sys.exit(main(__doc__))
