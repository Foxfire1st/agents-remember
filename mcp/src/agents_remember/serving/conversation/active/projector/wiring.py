"""The two bundles every component of one session's projection is built from.

An active-session projection is a small component graph -- native-evidence ingestion, echo
ingestion, child-history hydration, interaction projection, a rebuild coordinator -- and all of it
projects EXACTLY ONE session at EXACTLY ONE bridge epoch. That invariant is what these two objects
make structural: every component receives the same spine and the same readers, so no component can
end up ingesting one session's evidence into another session's stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents_remember.serving.harness_control_client import (
    read_control_evidence,
    read_control_native_page,
    read_control_snapshot,
    read_control_transcript,
    read_submission_provenance,
)

if TYPE_CHECKING:
    from agents_remember.serving.conversation.models import ActiveConversationRef
    from agents_remember.serving.conversation.projectors import HarnessProjector
    from agents_remember.serving.harness_control_client import ControlledSession

    from .agent_authority import AgentAuthority
    from .mutation_stream import ProjectionMutationStream
    from .references import ProjectionEvidenceRefs


@dataclass(frozen=True)
class BridgeReaders:
    """The whole read surface a projection is assembled from: five bridge reads, one session.

    They are one substitution. A test (or a replay harness) that fakes the evidence reader while
    leaving the transcript reader live is reading two different sessions and will happily project
    the interleaving, so the readers are chosen as a set.
    """

    evidence: Callable[..., Any] = read_control_evidence
    native_page: Callable[..., Any] = read_control_native_page
    transcript: Callable[..., Any] = read_control_transcript
    provenance: Callable[..., Any] = read_submission_provenance
    snapshot: Callable[..., Any] = read_control_snapshot


LIVE_BRIDGE_READERS = BridgeReaders()
"""The production reads: every one of them goes to the real control bridge."""


@dataclass(frozen=True)
class SessionProjectionSpine:
    """The machinery every ingestion component of ONE session's projection shares.

    The identity and its controlled session say *what* is being projected; the mapper says how that
    harness's shapes are read; the stream is the single place mutations are published; the agent
    authority and evidence refs are the identity and reference minting every component must agree
    on; the apply lock serializes them; the clock stamps them. Handing each component the same
    spine is what makes "one projection, one session, one epoch" checkable rather than a
    convention repeated across five parameter lists.
    """

    identity: ActiveConversationRef
    entry: ControlledSession
    mapper: HarnessProjector
    stream: ProjectionMutationStream
    agents: AgentAuthority
    refs: ProjectionEvidenceRefs
    apply_lock: asyncio.Lock
    clock: Callable[[], str]

    @property
    def parent_thread_id(self) -> str:
        """The vendor conversation this projection is rooted at (child threads hang off it)."""

        return self.identity.vendor_conversation_id

    @property
    def bridge_epoch(self) -> str:
        return self.identity.bridge_epoch


__all__ = [
    "LIVE_BRIDGE_READERS",
    "BridgeReaders",
    "SessionProjectionSpine",
]
