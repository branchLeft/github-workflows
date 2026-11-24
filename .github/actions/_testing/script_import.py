"""Import an action's hyphenated script by path, for that action's tests.

Shared by the action tests under .github/actions/. A script's filename has hyphens, so
it is not a legal module name for a plain import. When the named environment variable is
set, the tests load the script at that path instead: that is how a sabotage test breaks a
copy of the script and not the original.
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

    `env_var`, when set and present in the environment, names the path to load instead.
    `register` also puts the module in sys.modules, for tests that patch through it.
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
