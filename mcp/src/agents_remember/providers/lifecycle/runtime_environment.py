"""Runtime environment helpers for provider lifecycle commands."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def runtime_root_from_script() -> Path:
    return Path(__file__).resolve().parents[1]


def configure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def subprocess_env(env: dict[str, str] | None = None) -> dict[str, str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    merged_env["PYTHONUTF8"] = "1"
    merged_env["PYTHONIOENCODING"] = "utf-8"
    return merged_env


def default_coordination_root() -> Path:
    """Infer the installed coordination root from this script's location."""

    return Path(__file__).resolve().parents[1]
