"""The immutable app-scoped conversation runtime authority (260718-CHATS-L0).

This is the one-time composition repair for the structured Chats seam.
``create_app`` / ``register_harness_control_routes`` already construct every
server authority the conversation services need (scope, terminal catalog/host,
effective harness registry, liveness clock/config, capability evidence); before
L0 those authorities died inside the harness-control closure and the mounted
child routers had no production path to any of them.

The :class:`ConversationRuntime` binds exactly those existing authorities into
one immutable typed value, installed on the FastAPI app exactly once by the
stable root composition. It adds NO behavior of its own: no conversation store
or index, no lifecycle authority, no second terminal opener, and no
active/library/control service — those remain child-leaf owned and reach this
runtime only through the narrow request dependency in ``dependencies.py``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, fields
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI

from agents_remember.errors import ConversationCompositionError
from agents_remember.serving.conversation.authorization import (
    ConversationAuthorizationResolver,
)
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_liveness import (
    Clock,
    TerminalCatalogLivenessConfig,
    TerminalLivenessHost,
)

if TYPE_CHECKING:
    from agents_remember.serving.harnesses import Harness

HarnessRegistry = Callable[[], Sequence["Harness"]]

CONVERSATION_RUNTIME_STATE_KEY = "conversation_runtime"


@dataclass(frozen=True)
class ConversationScope:
    """The canonical workspace/coordination scope the runtime is bound to (R3)."""

    workspace_root: Path
    coordination_root: Path


@dataclass(frozen=True)
class ConversationRuntime:
    """One immutable app-scoped authority bundle for structured conversation services.

    Frozen at composition: the bound authorities cannot be swapped after
    installation, and a missing authority fails construction immediately so a
    broken composition can never reach a request.
    """

    scope: ConversationScope
    catalog: TerminalCatalog
    host: TerminalLivenessHost
    harness_registry: HarnessRegistry
    liveness_clock: Clock
    liveness_config: TerminalCatalogLivenessConfig
    capability_catalog: HarnessCapabilityCatalog
    authorization: ConversationAuthorizationResolver

    def __post_init__(self) -> None:
        missing = [field.name for field in fields(self) if getattr(self, field.name) is None]
        if missing:
            raise ConversationCompositionError(
                "conversation runtime missing required authorities: " + ", ".join(missing)
            )


def install_conversation_runtime(app: FastAPI, runtime: ConversationRuntime) -> None:
    """Bind ``runtime`` to ``app.state`` exactly once; a second install fails closed."""
    if getattr(app.state, CONVERSATION_RUNTIME_STATE_KEY, None) is not None:
        raise ConversationCompositionError(
            "conversation runtime is already installed on this application"
        )
    setattr(app.state, CONVERSATION_RUNTIME_STATE_KEY, runtime)


def conversation_runtime_from_app(app: FastAPI) -> ConversationRuntime:
    """Return the installed runtime; a missing or foreign binding fails closed."""
    runtime = getattr(app.state, CONVERSATION_RUNTIME_STATE_KEY, None)
    if runtime is None:
        raise ConversationCompositionError(
            "conversation runtime is not installed on this application"
        )
    if not isinstance(runtime, ConversationRuntime):
        raise ConversationCompositionError(
            "app.state.conversation_runtime does not hold a ConversationRuntime"
        )
    return runtime


__all__ = [
    "CONVERSATION_RUNTIME_STATE_KEY",
    "ConversationRuntime",
    "ConversationScope",
    "HarnessRegistry",
    "conversation_runtime_from_app",
    "install_conversation_runtime",
]
