"""The types a sabotage case is written in."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Edit:
    """One exact replacement in one file: `old` must be found `count` times, and each is replaced by `new`."""

    path: str
    old: str
    new: str
    count: int = 1


@dataclass
class Case:
    name: str
    edits: list[Edit]
    # Tests, as Class.method, that must go red. At least one.
    red: list[str] = field(default_factory=list)


class Suite:
    """A test class of tools/test_ansible_gated_apply.py. Calling it with a test's name gives `Class.method`."""

    def __init__(self, class_name: str) -> None:
        self.class_name = class_name

    def __call__(self, test: str) -> str:
        return f"{self.class_name}.{test}"
