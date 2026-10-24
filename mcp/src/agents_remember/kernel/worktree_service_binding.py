"""The one holder for this process's worktree services.

MIK-R204 @ v1 rule 3: the worktree services are an operation collaborator, not discovery work. A server
startup needs to name *how* to build them and nothing else, so the naming lives here -- in a module light
enough to import while the tool surface is being declared -- and the services package is imported by the
supplier the first time an operation actually asks for them.

This module is the single definition. `agents_remember.worktrees.services` re-exports these names so its
existing public surface keeps working; that re-export is direct and preserves object identity, which is why a
caller that binds here and a caller that binds through the services module reach the same state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

type WorktreeServices = Any


class WorktreeServicesUnboundError(RuntimeError):
    """Raised when a worktree operation needs services that were never bound."""


class _WorktreeServiceBinding:
    """This process's worktree services, built once on first use if a supplier was named."""

    def __init__(self) -> None:
        self.current: Any = None
        self.supplier: Callable[[], Any] | None = None

    def reset(self) -> None:
        """Clear the bound services and any pending supplier: the unbound state, as before.

        A reset is the test and teardown statement that this process has no services; leaving a
        pending supplier behind would let the next lookup rebuild them and read a state the caller
        had just cleared. A launched server's supplier is established once at its creation, and no
        product path resets a live process's binding.
        """
        self.current = None
        self.supplier = None


_BINDING = _WorktreeServiceBinding()


def bind_worktree_services(services: WorktreeServices) -> None:
    """Bind the composition-provided services for this process.

    Binding the services directly does not touch a pending supplier. A later reset clears both the
    services and the supplier, so it always answers the unbound state the caller asked for.
    """
    _BINDING.current = services


def set_worktree_services_supplier(supplier: Callable[[], WorktreeServices] | None) -> None:
    """Name how to build the services later, instead of building them now (MIK-R204 @ v1 rule 3).

    Startup must not pay for operation services that discovery never needs. The composition hands over a
    supplier; the first worktree operation that asks builds them, once, and the process keeps that instance
    exactly as if it had been bound at startup.
    """
    _BINDING.supplier = supplier


def reset_worktree_services() -> None:
    """Clear the bound services (tests and process teardown)."""
    _BINDING.reset()


def worktree_services() -> WorktreeServices:
    """The bound services, building them from the supplier once if that is all this process has."""
    if _BINDING.current is None and _BINDING.supplier is not None:
        # First dependent operation, not startup: build once and keep it.
        supplied = _BINDING.supplier()
        _BINDING.supplier = None
        _BINDING.current = supplied
    if _BINDING.current is None:
        raise WorktreeServicesUnboundError(
            "worktree services are not bound; the MCP/CLI composition must bind them"
        )
    return _BINDING.current


__all__ = [
    "WorktreeServicesUnboundError",
    "bind_worktree_services",
    "reset_worktree_services",
    "set_worktree_services_supplier",
    "worktree_services",
]
