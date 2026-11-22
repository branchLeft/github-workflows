"""Every sabotage case, from one module per family of controls."""

from __future__ import annotations

from ..model import Case
from . import (
    action_report,
    apply_gate,
    concurrency,
    continue_apply,
    delivery,
    drift_check,
    host_keys,
    jump,
    jump_proof,
    log_line,
    pin,
    push,
    rerun_and_gate,
    scan,
    second_hop,
    skip_failed_hosts,
)

CASES: list[Case] = [
    case
    for module in (
        delivery,
        drift_check,
        action_report,
        pin,
        apply_gate,
        rerun_and_gate,
        jump,
        jump_proof,
        push,
        log_line,
        second_hop,
        scan,
        concurrency,
        skip_failed_hosts,
        continue_apply,
        host_keys,
    )
    for case in module.CASES
]
