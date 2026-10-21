"""The command-line route's own value and causes, and the remedy it names (MIK-R194 rule 9).

A command-line leaf write cannot publish a ``raise``: the question belongs in the leaf's task document,
which the command has no authority to reach. Its refusal names one fixed clause for the cause it met
and the mounted ``knowledge_write`` call that can publish the same hand-off, with absolute paths, the
hand-off's label and the authorization reference. Both halves live here rather than inside the route
modules, which keep only the calls that use them.

A clause is never an error's own text. The causes are sorted in the order the write meets them: no
trusted settings at or above the working directory, the checkout guard refusing an undeclared run from
the primary checkout, a settings file that cannot be loaded, or settings for another coordination root,
which the caller compares against the contract.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = ["CommandRoute", "checked_guard", "discovered_settings", "mounted_route"]

# The guard's own clause, spelled once: the refusal carries no error text (rule 9).
CHECKOUT_GUARD_CLAUSE = (
    "the checkout guard refuses settings to an undeclared run from the primary checkout"
)


@dataclass(frozen=True)
class CommandRoute:
    """What a command-line write knows about itself, so a refused raise can name the route that works.

    ``settings`` resolves the MCP authority settings this run would use, or returns the one fixed
    clause naming why it cannot. The other fields are what the mounted ``knowledge_write`` call needs
    to repeat this exact hand-off: the hand-off list's absolute path and recorded label, the
    contract's absolute path, and the authorization reference.
    """

    settings: Callable[[], Any]
    handoff_path: str
    handoff_name: str
    contract_path: str
    authorization_ref: str


def mounted_route(route: CommandRoute, clause: str) -> str:
    """Why this command cannot publish the raise, and the mounted call that can.

    Nothing here mentions ``--config``, which no route reads.
    """

    return (
        f"this command cannot reach the leaf's task document: {clause}. Send the same hand-off "
        "through the mounted knowledge_write: "
        f"contractPath {route.contract_path}, handoffPath {route.handoff_path}, "
        f"handoffName {route.handoff_name}, authorizationRef {route.authorization_ref}; "
        "preview, then commit: true."
    )


def checked_guard(guard: Callable[[], Any]) -> str | None:
    """``None`` when the checkout guard admits this run, else its fixed clause."""

    try:
        guard()
    except Exception:
        return CHECKOUT_GUARD_CLAUSE
    return None


def discovered_settings(
    cwd: Path,
    *,
    discover: Callable[[Path], Path],
    guard: Callable[[], Any],
    load: Callable[[Path], Any],
    unreadable: tuple[type[BaseException], ...],
) -> Any:
    """The settings this command line would use, or the one fixed clause naming why it cannot.

    ``discover``, ``guard`` and ``load`` are the command's own three moves, passed in so this module
    holds the sorting rather than importing the command layer: no trusted settings at or above ``cwd``,
    the checkout guard's clause, a settings file that cannot be loaded, or the loaded settings, whose
    coordination root the caller compares against the contract.
    """

    try:
        path = discover(cwd)
    except Exception:
        return f"no trusted settings file is found at or above {cwd}"
    clause = checked_guard(guard)
    if clause is not None:
        return clause
    try:
        return load(path)
    except unreadable:
        return f"the settings {path} cannot be loaded"
