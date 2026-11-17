"""Load repository instruction tooling for its focused structural checks."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import ModuleType

RENEW_COMMAND = "mcp/.venv/bin/python scripts/renew-instructions.py"


def renewal_module(name: str) -> ModuleType:
    """Use the tooling implementation belonging to this test checkout."""
    scripts = str(Path(__file__).resolve().parents[4] / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    return importlib.import_module("instruction_renewal." + name)
