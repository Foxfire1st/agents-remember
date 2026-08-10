"""Per-harness active projectors (260718-CHATS-L1).

Each projector module maps that harness's native frames — live evidence frames
and native-history page frames — into normalized conversation mapping outputs.
Mappers are pure and schema-strict; the engine in
:mod:`conversation.active.projector` assigns ordinals, revisions, provenance,
envelopes, and unknown-vendor evidence handles.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from agents_remember.models.conversations.evidence import (
    EvidenceFrame,
    NativeEvidenceFrame,
)
from agents_remember.models.conversations.identity import (
    HarnessId,
)
from agents_remember.serving.conversation.projectors import claude, codex, pi
from agents_remember.serving.conversation.projectors.common import MapperOutput


class HarnessProjector(Protocol):
    """The engine-facing surface of one per-harness projector module."""

    harness_id: HarnessId
    uses_native_pages: bool
    uses_transcript_echo: bool
    eager_native_continuation: bool

    def map_native_frame(
        self,
        frame: NativeEvidenceFrame,
        *,
        evidence_ref: str,
    ) -> list[MapperOutput]: ...

    def map_evidence_frame(
        self,
        frame: EvidenceFrame,
        *,
        evidence_ref: str,
        parent_thread_id: str | None = None,
    ) -> list[MapperOutput]: ...

    def map_transcript_echo(
        self,
        entry: Mapping[str, object],
        *,
        evidence_ref: str,
    ) -> list[MapperOutput]: ...


class _CodexProjector:
    harness_id: HarnessId = "codex"
    uses_native_pages = True
    uses_transcript_echo = False
    # Native thread items are lazily re-read when a page notices new completed
    # items; the live window already carries their full item payloads.
    eager_native_continuation = False

    map_native_frame = staticmethod(codex.map_native_frame)
    map_evidence_frame = staticmethod(codex.map_evidence_frame)

    @staticmethod
    def map_transcript_echo(
        entry: Mapping[str, object],
        *,
        evidence_ref: str,
    ) -> list[MapperOutput]:
        raise NotImplementedError("codex has no transcript echo channel")


class _ClaudeProjector:
    harness_id: HarnessId = "claude"
    uses_native_pages = False
    uses_transcript_echo = True
    eager_native_continuation = False

    @staticmethod
    def map_native_frame(
        frame: NativeEvidenceFrame,
        *,
        evidence_ref: str,
    ) -> list[MapperOutput]:
        raise NotImplementedError("claude native pages fail closed stream/replay-only")

    map_evidence_frame = staticmethod(claude.map_evidence_frame)
    map_transcript_echo = staticmethod(claude.map_transcript_echo)


class _PiProjector:
    harness_id: HarnessId = "pi"
    uses_native_pages = True
    uses_transcript_echo = False
    # Entries land durably as messages complete; the engine continues the
    # native read eagerly so live items always carry native identity.
    eager_native_continuation = True

    map_native_frame = staticmethod(pi.map_native_frame)
    map_evidence_frame = staticmethod(pi.map_evidence_frame)

    @staticmethod
    def map_transcript_echo(
        entry: Mapping[str, object],
        *,
        evidence_ref: str,
    ) -> list[MapperOutput]:
        raise NotImplementedError("pi has no transcript echo channel")


PROJECTORS: Mapping[str, HarnessProjector] = {
    "codex": _CodexProjector(),
    "claude": _ClaudeProjector(),
    "pi": _PiProjector(),
}


def projector_for(harness_id: str) -> HarnessProjector | None:
    return PROJECTORS.get(harness_id)


__all__ = ["PROJECTORS", "HarnessProjector", "projector_for"]
