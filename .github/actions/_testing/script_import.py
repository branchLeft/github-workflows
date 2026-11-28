"""Import an action's hyphenated script by path, for that action's tests.

See script_import.comments.md, block 1.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import sys
from types import ModuleType
from typing import Optional


def load_script(
    here: pathlib.Path,
    script: str,
    module_name: str,
    env_var: Optional[str] = None,
    register: bool = False,
) -> ModuleType:
    """Load `script` from the directory `here` as module `module_name`.

    See script_import.comments.md, block 2.
    """
    override = os.environ.get(env_var) if env_var else None
    path = pathlib.Path(override or here / script).resolve()
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    if register:
        sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module
