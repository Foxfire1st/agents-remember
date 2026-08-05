"""Dormant native resolver factory and per-app library derivation (260718-CHATS-L2).

Child leaves never touch ``app.state``: the L0 composition hands them the immutable
:class:`ConversationRuntime` and they derive child-owned ports from it. The library's shared
authorities — the cursor signing key, the live gate registry, the helper host, and the bounded
open-operation ledger — must still be app-scoped (a per-request rebuild would re-run gates and
forget open operations, and a module-level singleton would break the per-app isolation L0
proves). They are therefore memoized per exact runtime in a weak-key map: each app gets its own
bundle, the bundle is reclaimed with its runtime, and nothing is held at import time.

Per-request pieces (ports, services) are built fresh with the caller's server-resolved
authorization binding, so every minted cursor, key, and operation re-binds that exact
principal/tenant on every call.
"""

from __future__ import annotations

import threading
import weakref
from dataclasses import dataclass

from agents_remember.kernel.harnesses import Harness
from agents_remember.serving.conversation.library.claude import ClaudeConversationLibrary
from agents_remember.serving.conversation.library.codex import CodexConversationLibrary
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.errors import UnknownLibraryHarnessError
from agents_remember.serving.conversation.library.gates import LibraryGateRegistry
from agents_remember.serving.conversation.library.helper_host import (
    ConversationLibraryHelperHost,
)
from agents_remember.serving.conversation.library.open_service import (
    ConversationOpenService,
    LibraryBinding,
    OpenOperationLedger,
)
from agents_remember.serving.conversation.library.pi import PiConversationLibrary
from agents_remember.serving.conversation.library.service import (
    ConversationLibraryService,
    LibraryPort,
)
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    HarnessId,
)
from agents_remember.serving.conversation.runtime import ConversationRuntime

NORMALIZED_HARNESS_IDS: tuple[HarnessId, ...] = ("codex", "claude", "pi")


@dataclass(frozen=True)
class LibraryShared:
    """App-scoped library authorities derived once per exact runtime."""

    cursor_authority: LibraryCursorAuthority
    gates: LibraryGateRegistry
    helper_host: ConversationLibraryHelperHost
    open_ledger: OpenOperationLedger


_shared_by_runtime: weakref.WeakKeyDictionary[ConversationRuntime, LibraryShared] = (
    weakref.WeakKeyDictionary()
)
_shared_lock = threading.Lock()


def library_shared(runtime: ConversationRuntime) -> LibraryShared:
    """Return the one library bundle for this exact app runtime (built once, then reused)."""

    shared = _shared_by_runtime.get(runtime)
    if shared is not None:
        return shared
    with _shared_lock:
        shared = _shared_by_runtime.get(runtime)
        if shared is None:
            helper_host = ConversationLibraryHelperHost()
            gates = LibraryGateRegistry(
                harness_registry=runtime.harness_registry,
                workspace_root=runtime.scope.workspace_root,
                helper_host=helper_host,
            )
            shared = LibraryShared(
                cursor_authority=LibraryCursorAuthority(mint_signing_key()),
                gates=gates,
                helper_host=helper_host,
                open_ledger=OpenOperationLedger(),
            )
            _shared_by_runtime[runtime] = shared
    return shared


def build_port(
    runtime: ConversationRuntime,
    shared: LibraryShared,
    authorization: AuthorizationBinding,
    harness_id: str,
) -> LibraryPort:
    """Build the dormant resolver port for one harness, bound to this caller's identity."""

    harness = _require_harness(runtime, harness_id)
    capabilities = shared.gates.history_capabilities
    if harness_id == "codex":
        return CodexConversationLibrary(
            authorization=authorization,
            cursor_authority=shared.cursor_authority,
            capabilities=capabilities,
            harness=harness,
        )
    if harness_id == "claude":
        return ClaudeConversationLibrary(
            authorization=authorization,
            cursor_authority=shared.cursor_authority,
            capabilities=capabilities,
            helper_host=shared.helper_host,
        )
    if harness_id == "pi":
        return PiConversationLibrary(
            authorization=authorization,
            cursor_authority=shared.cursor_authority,
            capabilities=capabilities,
            helper_host=shared.helper_host,
        )
    raise UnknownLibraryHarnessError(f"unknown harness: {harness_id!r}")


def build_library_service(
    runtime: ConversationRuntime,
    authorization: AuthorizationBinding,
) -> ConversationLibraryService:
    shared = library_shared(runtime)
    return ConversationLibraryService(
        runtime=runtime,
        shared=shared,
        authorization=authorization,
        port_builder=lambda harness_id: build_port(runtime, shared, authorization, harness_id),
    )


def build_open_service(
    runtime: ConversationRuntime,
    authorization: AuthorizationBinding,
) -> ConversationOpenService:
    shared = library_shared(runtime)
    port_builder = lambda harness_id: build_port(  # noqa: E731 - one intent per call
        runtime, shared, authorization, harness_id
    )
    return ConversationOpenService(
        LibraryBinding(runtime=runtime, shared=shared, authorization=authorization),
        library=ConversationLibraryService(
            runtime=runtime,
            shared=shared,
            authorization=authorization,
            port_builder=port_builder,
        ),
        port_builder=port_builder,
    )


def require_normalized_harness(harness_id: str) -> HarnessId:
    """Narrow a raw path segment to a normalized harness id or raise (route maps 404)."""

    if harness_id in NORMALIZED_HARNESS_IDS:
        return harness_id  # type: ignore[return-value] - membership-proven literal
    raise UnknownLibraryHarnessError(f"unknown harness: {harness_id!r}")


def _require_harness(runtime: ConversationRuntime, harness_id: str) -> Harness:
    harness = next((item for item in runtime.harness_registry() if item.id == harness_id), None)
    if harness is None:
        raise UnknownLibraryHarnessError(f"unknown harness: {harness_id!r}")
    return harness


__all__ = [
    "NORMALIZED_HARNESS_IDS",
    "LibraryShared",
    "build_library_service",
    "build_open_service",
    "build_port",
    "library_shared",
    "require_normalized_harness",
]
