"""Report product checkouts whose virtual environment needs to move to uv."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path
from typing import NotRequired, TypedDict

from agents_remember.kernel.git_command import run_git_bytes
from agents_remember.python_runtime import is_uv_minor_base, read_venv_home

_CONTRACT_PATH = Path("scripts/python-runtime-contract.env")
_MINOR_SETTING = re.compile(r"^AR_PYTHON_MINOR=([0-9]+\.[0-9]+)$", re.MULTILINE)


class StaleCheckout(TypedDict):
    checkout: str
    bootstrapCommand: str


class CheckoutEnvironmentInventory(TypedDict):
    staleCheckouts: list[StaleCheckout]
    instruction: NotRequired[str]
    error: NotRequired[str]


def inspect_checkout_environments(repository: Path) -> CheckoutEnvironmentInventory:
    """Read the selected product repository's registered environments without rebuilding them."""
    inventory: CheckoutEnvironmentInventory = {"staleCheckouts": []}
    contract = repository / _CONTRACT_PATH
    try:
        minor = _MINOR_SETTING.search(contract.read_text(encoding="utf-8"))
        if minor is None:
            inventory["error"] = f"No Python minor version was found in {contract}."
            return inventory
        listing = run_git_bytes(repository, ["worktree", "list", "--porcelain", "-z"])
        if listing.returncode:
            detail = listing.stderr.decode("utf-8", "replace").strip()
            detail = detail or "Git failed to list worktrees."
            inventory["error"] = f"Could not list registered checkouts for {repository}. {detail}"
            return inventory
        checkouts = (
            Path(os.fsdecode(field.removeprefix(b"worktree ")))
            for field in listing.stdout.split(b"\0")
            if field.startswith(b"worktree ")
        )
        inventory["staleCheckouts"] = [
            _migration_row(checkout)
            for checkout in checkouts
            if _is_stale_environment(checkout, minor[1])
        ]
    except (OSError, UnicodeError) as error:
        inventory["error"] = (
            f"Could not inspect checkout Python environments for {repository}. {error}"
        )
    if inventory["staleCheckouts"]:
        inventory["instruction"] = (
            "Run each bootstrap command once its checkout has the uv-managed bootstrap."
        )
    return inventory


def _is_stale_environment(checkout: Path, minor: str) -> bool:
    venv = checkout / "mcp/.venv"
    if not venv.exists():
        return False
    home = read_venv_home(venv)
    return home is None or home.name != "bin" or not is_uv_minor_base(home.parent, minor)


def _migration_row(checkout: Path) -> StaleCheckout:
    checkout_path = checkout.as_posix()
    return {
        "checkout": checkout_path,
        "bootstrapCommand": f"cd {shlex.quote(checkout_path)} && scripts/bootstrap-mcp-venv.sh",
    }
