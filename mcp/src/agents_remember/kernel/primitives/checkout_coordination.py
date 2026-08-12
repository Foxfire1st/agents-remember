"""Fail-closed coordination isolation for code loaded from a source checkout.

Regular MCP and dashboard processes declare their trusted execution mode before
loading authority settings.  An undeclared process loaded from a linked Agents
Remember worktree instead owns one disposable coordination root inside that
worktree's enclosure.  The primary checkout has no such disposable enclosure,
so undeclared access from it is refused.

This is an application boundary for supported Agents Remember paths.  It does
not claim to sandbox arbitrary Python or shell filesystem access.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

DaemonRole = Literal["mcp", "dashboard"]
ExecutionMode = Literal["mcp", "dashboard", "lifecycle-operation", "test"]
CheckoutKind = Literal["linked", "primary"]

DEV_COORDINATION_DIRECTORY = "dev-ar-coordination"
REPOSITORY_ID = "agents-remember"

_PACKAGE_SOURCE = Path(__file__).resolve()
_declared: dict[str, ExecutionMode] = {}


class CheckoutCoordinationError(RuntimeError):
    """An undeclared checkout process attempted an unsafe coordination action."""


@dataclass(frozen=True)
class CheckoutLocation:
    """The Git checkout that supplied the imported Agents Remember package."""

    checkout_root: Path
    kind: CheckoutKind

    @property
    def worktree_group(self) -> Path:
        return self.checkout_root.parent

    @property
    def coordination_root(self) -> Path:
        return self.worktree_group / "provider-runtime" / DEV_COORDINATION_DIRECTORY

    @property
    def synthetic_config_path(self) -> Path:
        return self.worktree_group / "provider-runtime" / "dev-mcp-settings.json"

    @property
    def reports_root(self) -> Path:
        """Task-local operational artifacts, outside coordination authority state."""
        return self.worktree_group / "reports"


def declare_execution_mode(mode: ExecutionMode) -> None:
    """Declare a trusted daemon or explicit test process before config loading."""
    _declared["mode"] = mode


def declare_test_process() -> None:
    """Declare pytest's explicit hermetic mode without pretending it is a daemon."""
    declare_execution_mode("test")


def declare_lifecycle_operation_process() -> None:
    """Declare the task-bound detached closeout/integration worker.

    The worker is launched only from a durable plane-owned operation record.  It must use
    live coordination authority to claim the bound gate and finalize the task edge, but it
    is not either long-lived store daemon and therefore receives no daemon writer role.
    """
    declare_execution_mode("lifecycle-operation")


def declared_execution_mode() -> ExecutionMode | None:
    """Return this interpreter's declared execution mode, if any."""
    return _declared.get("mode")


def declared_daemon_role() -> DaemonRole | None:
    """Narrow the execution mode to the two durable-store daemon roles."""
    mode = declared_execution_mode()
    return cast(DaemonRole, mode) if mode in {"mcp", "dashboard"} else None


def resolve_checkout_location(source_path: Path | None = None) -> CheckoutLocation | None:
    """Resolve the Git checkout that owns the loaded package, independent of cwd.

    A linked worktree has a ``.git`` file; the primary checkout has a ``.git``
    directory.  The repository-shape checks keep an unrelated ancestor Git
    repository from being mistaken for the Agents Remember checkout.
    """
    source = (source_path or _PACKAGE_SOURCE).resolve()
    for candidate in source.parents:
        if not (candidate / "mcp" / "pyproject.toml").is_file():
            continue
        if not (candidate / "mcp" / "src" / "agents_remember").is_dir():
            continue
        git_marker = candidate / ".git"
        if git_marker.is_file():
            return CheckoutLocation(checkout_root=candidate, kind="linked")
        if git_marker.is_dir():
            return CheckoutLocation(checkout_root=candidate, kind="primary")
    return None


def checkout_cli_location() -> CheckoutLocation | None:
    """Return the linked checkout for undeclared CLI code, or refuse primary.

    Installed-package CLI behavior is unchanged because an installed wheel has
    no owning Agents Remember Git checkout.  Trusted daemon and explicit test
    processes are also outside checkout CLI mode.
    """
    if declared_execution_mode() is not None:
        return None
    location = resolve_checkout_location()
    if location is None:
        return None
    if location.kind == "primary":
        raise CheckoutCoordinationError(
            "undeclared Agents Remember execution from the primary checkout is refused: "
            "run the regular MCP/dashboard entry point or use a task worktree, whose CLI "
            "writes are isolated under provider-runtime/dev-ar-coordination"
        )
    return location


def require_durable_write_target(target: Path) -> None:
    """Refuse checkout writes outside its disposable coordinator or task reports.

    ``reports/`` is the enclosure-local operation/test artifact boundary.  It is
    deliberately not a coordination root and contains no inbox, gate, lifecycle,
    or observer authority rows.  Detached operations loaded from unpublished code
    may therefore advance their one self-overwriting report there without gaining
    write access to the live coordinator.
    """
    location = checkout_cli_location()
    if location is None:
        return
    resolved_target = target.resolve()
    coordination_root = location.coordination_root.resolve()
    reports_root = location.reports_root.resolve()
    if not (
        resolved_target.is_relative_to(coordination_root)
        or resolved_target.is_relative_to(reports_root)
    ):
        raise CheckoutCoordinationError(
            "unpublished checkout code may write coordination rows only inside its leaf-local "
            f"coordination root ({coordination_root}) and operational artifacts only inside "
            f"its enclosure reports root ({reports_root}); refused target: {resolved_target}"
        )
