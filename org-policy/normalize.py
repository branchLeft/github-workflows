#!/usr/bin/env python3
"""Reduce a ruleset — committed payload or live API response — to a canonical form.

The two sides are not directly comparable as written: the API adds `id`,
`node_id`, `_links` and friends, fills in `parameters: null` on valueless rules
and `actor_id` on bypass actors, and neither side guarantees an order for rules,
status-check contexts or actors.
"""

import json
import sys


def canon_rule(rule):
    params = rule.get("parameters") or {}
    if "required_status_checks" in params:
        params = dict(params)
        params["required_status_checks"] = sorted(
            params["required_status_checks"], key=lambda c: c["context"]
        )
    return {"type": rule["type"], "parameters": params}


def canon(ruleset):
    return {
        "name": ruleset["name"],
        "target": ruleset["target"],
        "enforcement": ruleset["enforcement"],
        "conditions": ruleset.get("conditions", {}),
        "rules": sorted(
            (canon_rule(r) for r in ruleset.get("rules", [])),
            key=lambda r: r["type"],
        ),
        "bypass_actors": sorted(
            (
                {
                    "actor_type": a["actor_type"],
                    "bypass_mode": a["bypass_mode"],
                    "actor_id": a.get("actor_id"),
                }
                for a in ruleset.get("bypass_actors", [])
            ),
            key=lambda a: (a["actor_type"], a["bypass_mode"], str(a["actor_id"])),
        ),
    }


if __name__ == "__main__":
    json.dump(canon(json.load(sys.stdin)), sys.stdout, indent=2, sort_keys=True)
    print()
