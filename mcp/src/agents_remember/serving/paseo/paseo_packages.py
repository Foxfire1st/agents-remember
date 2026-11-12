"""The immutable npm package tree shipped with this build."""

from __future__ import annotations

from pathlib import Path

from agents_remember.kernel.primitives.paseo_host_contract import HOST_DATA


def installed_lock_matches(prefix: Path) -> bool:
    path = prefix / "package-lock.json"
    return path.is_file() and path.read_bytes() == (HOST_DATA / "package-lock.json").read_bytes()
