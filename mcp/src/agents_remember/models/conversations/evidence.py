"""Protocol-neutral evidence wire contracts for hosted harness sessions.

The evidence frames, pages, truncation envelope, and native-page windowing
shared by the control plane and the conversation projectors. Declaration
bodies are unchanged from the pre-split module.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Protocol, runtime_checkable

from agents_remember.errors import HarnessControlError

AR_EVIDENCE_KEY = "arEvidence"
"""Reserved ``AdapterEvent.raw`` key carrying one full native payload for the evidence buffer.

Mappers place full native frames under this key only; every pre-existing raw key keeps its exact
shape. The control bridge diverts the payload into the bounded evidence deque and republishes the
event without this key, so ``snapshot.raw`` and every projection of it stay byte-identical.
"""


AR_EVIDENCE_METHOD_KEY = "arEvidenceMethod"
"""Reserved ``AdapterEvent.raw`` key carrying the native notification method / frame ``type``.

Some native protocols (notably the Codex app-server) carry the discriminating method name
*outside* the notification ``params`` payload. When an adapter diverts only ``params`` under
``AR_EVIDENCE_KEY`` the method would otherwise be stripped before a projector ever sees it, forcing
the projector to re-guess meaning from the params shape. An adapter that has this fact sets it here
so the bridge preserves it on the diverted :class:`EvidenceFrame` as typed ``native_method``
metadata; like ``AR_EVIDENCE_KEY`` it is stripped from the republished event so ``snapshot.raw``
stays byte-identical.
"""


AR_TERMINAL_OUTCOME_KEY = "arTerminalOutcome"
"""Adapter-attributed correlated terminal classification riding one diverted evidence payload.

Some harnesses emit no native marker that distinguishes an interrupt settlement from a real
failure (claude's stream-json answers an accepted interrupt with a plain
``error_during_execution``/``is_error`` result). The adapter is the only component that knows an
interrupt was accepted for the exact settling operation, so it stamps its correlated
:class:`TerminalOutcome` on the diverted payload copy under this reserved ``ar*`` key. The native
frame keys stay byte-intact; consumers (projectors, the interrupt settlement ledger) trust the
stamp when present and fall back to native-frame classification only when it is absent. The
truncation envelope re-carries the scalar so a clipped settlement frame never loses the
correlation.
"""


EVIDENCE_TRUNCATION_MARKER = "…[truncated]"
"""Visible marker appended to every clipped evidence payload preview."""


MAX_PRESERVED_EVIDENCE_SCALAR_CHARS = 256
"""Length ceiling for a terminal-identity scalar re-carried by the truncation envelope.

Every preserved field is a protocol enum (pi ``stopReason``, codex turn ``status``), a frame
type name, or a vendor turn id — a handful of characters in every real shape, so 256 is orders
of magnitude above any legitimate value while staying tiny against the evidence budget. A scalar
longer than this is a malformed-frame signal, not trustworthy terminal identity: it is dropped
WHOLE (never truncated — a partial id/status could mis-correlate at settlement), degrading the
envelope to the pre-identity total clip for that one field. This keeps an oversized scalar from
ever making the truncation envelope exceed its own byte budget (which would raise instead of
clip, and in the bridge event loop that raise is session-fatal)."""


MAX_NATIVE_EVIDENCE_PAGE = 200
"""Server-side frame cap for one native-domain evidence page."""


EVIDENCE_PAGE_BYTE_BUDGET = 48 * 1024
"""Default serialized-byte budget for one evidence page (bounded below the IPC wire cap)."""


@dataclass(frozen=True)
class EvidenceFrame:
    """One diverted native payload in the deque coordinate domain (adapter event sequence)."""

    sequence: int
    kind: str
    created_at: str
    raw: Mapping[str, object] = field(default_factory=dict)
    native_method: str | None = None
    """The native notification method / frame ``type`` when the adapter carries it out of band.

    Preserved verbatim from ``AR_EVIDENCE_METHOD_KEY`` so a projector switches on the real method
    instead of re-guessing meaning from the ``params`` shape; ``None`` when the adapter embeds the
    discriminator inside ``raw`` (as Claude and Pi do with the frame ``type``).
    """

    thread_id: str | None = None
    """The native thread this frame belongs to when the harness multiplexes.

    Codex auto-attaches sub-agent thread listeners to the seat's connection, so one evidence
    stream carries many threads; ``thread_id`` is the demux key (``None`` = the parent/session
    thread, matching pre-multiplex behavior). Claude encodes its sidechain join key
    (``parent_tool_use_id``) inside ``raw`` instead.
    """


@dataclass(frozen=True)
class EvidencePage:
    """One bounded deque-domain page; every coordinate is an adapter event sequence."""

    frames: tuple[EvidenceFrame, ...]
    latest_sequence: int
    evicted_before_sequence: int
    truncated: bool
    bridge_epoch: str


@dataclass(frozen=True)
class NativeEvidenceFrame:
    """One native-history frame with typed harness identity, never buried in ``raw``."""

    native_id: str
    native_parent_id: str | None
    native_type: str
    created_at: str | None
    raw: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class NativeEvidencePage:
    """One bounded native-domain page continued only by the opaque ``next_cursor``."""

    frames: tuple[NativeEvidenceFrame, ...]
    next_cursor: str | None
    truncated: bool
    bridge_epoch: str


@runtime_checkable
class NativePageReader(Protocol):
    """Structural native-history read; concrete adapters opt in without a protocol edit."""

    async def read_native_page(
        self,
        *,
        cursor: str | None,
        limit: int,
        byte_budget: int,
    ) -> NativeEvidencePage: ...


def evidence_frame_json(value: EvidenceFrame) -> dict[str, object]:
    payload: dict[str, object] = {
        "sequence": value.sequence,
        "kind": value.kind,
        "createdAt": value.created_at,
        "raw": dict(value.raw),
    }
    if value.native_method is not None:
        payload["nativeMethod"] = value.native_method
    if value.thread_id is not None:
        payload["threadId"] = value.thread_id
    return payload


def evidence_page_json(value: EvidencePage) -> dict[str, object]:
    return {
        "frames": [evidence_frame_json(frame) for frame in value.frames],
        "latestSequence": value.latest_sequence,
        "evictedBeforeSequence": value.evicted_before_sequence,
        "truncated": value.truncated,
        "bridgeEpoch": value.bridge_epoch,
    }


def native_evidence_frame_json(value: NativeEvidenceFrame) -> dict[str, object]:
    return {
        "nativeId": value.native_id,
        "nativeParentId": value.native_parent_id,
        "nativeType": value.native_type,
        "createdAt": value.created_at,
        "raw": dict(value.raw),
    }


def native_evidence_page_json(value: NativeEvidencePage) -> dict[str, object]:
    return {
        "frames": [native_evidence_frame_json(frame) for frame in value.frames],
        "nextCursor": value.next_cursor,
        "truncated": value.truncated,
        "bridgeEpoch": value.bridge_epoch,
    }


def _bounded_identity_scalar(value: object) -> str | None:
    """One preserved scalar if it is a string within the identity length ceiling, else ``None``.

    A non-string or an over-length string is dropped whole (never truncated), so a malformed
    giant scalar can neither cross nor collapse the truncation envelope's own byte budget.
    """

    if isinstance(value, str) and len(value) <= MAX_PRESERVED_EVIDENCE_SCALAR_CHARS:
        return value
    return None


_TOP_LEVEL_IDENTITY_KEYS = ("type", "subtype", "terminal_reason", AR_TERMINAL_OUTCOME_KEY)
"""Identity/status enums the settlement reads take straight off the frame root."""


def _bounded_identity_scalars(
    source: Mapping[str, object], keys: tuple[str, ...]
) -> dict[str, object]:
    """The subset of ``keys`` present in ``source`` as bounded scalars, kept at their own names.

    A key is dropped whole when it is absent, non-string, or over-length -- never invented and
    never truncated, so a surviving value is always the exact scalar a consumer would have read.
    """

    preserved: dict[str, object] = {}
    for key in keys:
        scalar = _bounded_identity_scalar(source.get(key))
        if scalar is not None:
            preserved[key] = scalar
    return preserved


def _nested_identity_scalars(
    payload: Mapping[str, object], *, path: str, keys: tuple[str, ...]
) -> dict[str, object]:
    """One nested object's surviving identity scalars, rebuilt at ``path``.

    Empty when the path is absent, is not an object, or contributed nothing -- so a clipped frame
    never grows an empty ``message``/``turn`` shell that a consumer could mistake for evidence.
    """

    nested = payload.get(path)
    if not isinstance(nested, Mapping):
        return {}
    kept = _bounded_identity_scalars(nested, keys)
    return {path: kept} if kept else {}


def _preserved_evidence_identity(payload: Mapping[str, object]) -> dict[str, object]:
    """The terminal-identity fields that survive a clip, each at its original payload path.

    Only tiny scalar identity/status enums cross — never text, content blocks, items, or any
    other body. Each scalar is bounded by ``MAX_PRESERVED_EVIDENCE_SCALAR_CHARS`` and dropped
    whole when absent, non-string, or over-length (never invented, never truncated); a value is
    copied only when it is present as the exact bounded scalar the settlement consumers read:

    * top-level ``type`` — the frame kind every clipped frame keeps (pi ``message_end``); the
      pi settlement read is ``frame.raw.get("type") == "message_end"``.
    * ``message.stopReason`` — the pi terminal enum; the read is ``frame.raw["message"]
      ["stopReason"]``. Only ``stopReason`` crosses; the message role and content never do.
    * ``turn.id`` + ``turn.status`` — the codex terminal-turn identity and status enum; the
      read correlates ``frame.raw["turn"]["id"] == turn_id`` before taking ``turn["status"]``,
      so both must survive or the frame is skipped. The turn's items/error never cross.
    * top-level ``subtype`` + ``terminal_reason`` — the claude result-frame terminal enums; the
      claude settlement read classifies the completed-kind frame from exactly these two.
    * ``arTerminalOutcome`` — the adapter-attributed correlated terminal classification
      (:data:`AR_TERMINAL_OUTCOME_KEY`); the claude settlement read takes it over the native
      ``error_during_execution`` shape, so it must survive or a clipped interrupt settlement
      degrades to a mis-read failure.
    """

    return {
        **_bounded_identity_scalars(payload, _TOP_LEVEL_IDENTITY_KEYS),
        **_nested_identity_scalars(payload, path="message", keys=("stopReason",)),
        **_nested_identity_scalars(payload, path="turn", keys=("id", "status")),
    }


_CONTENT_TRUNCATION_LIMITS = (65536, 16384, 4096, 1024, 320)
"""Descending per-string character ceilings the content-preserving clip attempts.

The floor stays above ``MAX_PRESERVED_EVIDENCE_SCALAR_CHARS`` so identity/status scalars are
never shortened by a content clip; anything that still exceeds the byte budget at the floor is
structurally oversized and degrades to the legacy preview envelope.
"""


def _truncate_string_leaves(value: object, limit: int) -> object:
    """A structural copy of ``value`` with every string VALUE longer than ``limit`` shortened.

    Only leaf string values shrink — each carries the truncation marker plus its omitted length,
    so a shortened tool input/output stays visibly partial. Mapping keys, nesting, numbers, and
    booleans are untouched, which is what keeps exact schema discrimination valid on the copy.
    """

    if isinstance(value, str):
        if len(value) <= limit:
            return value
        omitted = len(value) - limit
        return f"{value[:limit]}{EVIDENCE_TRUNCATION_MARKER} [{omitted} chars omitted]"
    if isinstance(value, Mapping):
        return {key: _truncate_string_leaves(item, limit) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_truncate_string_leaves(item, limit) for item in value]
    return value


def clip_evidence_payload(payload: Mapping[str, object], *, max_bytes: int) -> Mapping[str, object]:
    """Bound one JSON evidence payload to ``max_bytes`` serialized, with any clip visible.

    Unclipped payloads are returned as a plain copy. An oversized payload degrades by CONTENT
    before structure: long string leaves are truncated in place (marker + omitted count) at
    descending ceilings until the serialized copy fits, and the copy is stamped
    ``arEvidenceContentTruncated``/``originalBytes``. Every mapper still parses the exact native
    shape — frame type, ids, tool inputs, diffs all survive — so an oversized Write/Edit/output
    frame renders as its real item with visibly shortened text instead of an unknown-vendor row.

    Only when even the structure cannot fit inside the budget does the legacy preview envelope
    apply: serialized size never exceeds ``max_bytes``, the preview ends with the truncation
    marker so consumers never mistake a partial payload for a complete native frame, and the
    frame's terminal-identity enums re-cross at their original payload paths
    (``_preserved_evidence_identity``) so exact-turn interrupt settlement stays honest.
    """

    if max_bytes < 1:
        raise HarnessControlError("evidence payload clip requires a positive byte budget")
    try:
        encoded = json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise HarnessControlError("adapter evidence payload must be JSON-serializable") from exc
    if len(encoded.encode("utf-8")) <= max_bytes:
        return dict(payload)
    original_bytes = len(encoded.encode("utf-8"))
    for limit in _CONTENT_TRUNCATION_LIMITS:
        softened = _truncate_string_leaves(dict(payload), limit)
        if not isinstance(softened, dict):  # pragma: no cover - Mapping input always copies to dict
            break
        clipped_content: dict[str, object] = {
            **softened,
            "arEvidenceContentTruncated": True,
            "originalBytes": original_bytes,
        }
        serialized = json.dumps(clipped_content, ensure_ascii=False, separators=(",", ":"))
        if len(serialized.encode("utf-8")) <= max_bytes:
            return clipped_content
    preserved = _preserved_evidence_identity(payload)
    preview = encoded
    for _ in range(64):
        clipped: dict[str, object] = {
            "arEvidenceTruncated": True,
            "originalBytes": original_bytes,
            "preview": preview + EVIDENCE_TRUNCATION_MARKER,
            **preserved,
        }
        if len(json.dumps(clipped, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) <= (
            max_bytes
        ):
            return clipped
        preview = preview[: len(preview) // 2]
    raise HarnessControlError("evidence payload clip budget is below the truncation envelope")


def evidence_frame_wire_bytes(frame: EvidenceFrame) -> int:
    return _serialized_size(evidence_frame_json(frame))


def native_evidence_frame_wire_bytes(frame: NativeEvidenceFrame) -> int:
    return _serialized_size(native_evidence_frame_json(frame))


def window_native_evidence_page(
    frames: tuple[NativeEvidenceFrame, ...],
    *,
    cursor: str | None,
    limit: int,
    byte_budget: int,
) -> NativeEvidencePage:
    """Window one full native read into a bounded page with an opaque native continuation.

    The cursor names the last native id of the previous page; the next page starts strictly
    after it and is minted only from the fresh native read. A single oversized frame is clipped
    so every page makes progress; the bridge stamps ``bridge_epoch`` on the result.
    """

    if limit < 1:
        raise HarnessControlError("native evidence page limit must be positive")
    if byte_budget < 1:
        raise HarnessControlError("native evidence page byte budget must be positive")
    start = _native_window_start(frames, cursor)
    selected: list[NativeEvidenceFrame] = []
    used = 0
    end = start
    while end < len(frames) and len(selected) < limit:
        frame = frames[end]
        if native_evidence_frame_wire_bytes(frame) > byte_budget:
            frame = replace(frame, raw=clip_evidence_payload(frame.raw, max_bytes=byte_budget // 2))
        size = native_evidence_frame_wire_bytes(frame)
        if selected and used + size > byte_budget:
            break
        selected.append(frame)
        used += size
        end += 1
    truncated = end < len(frames)
    next_cursor = selected[-1].native_id if truncated and selected else None
    return NativeEvidencePage(
        frames=tuple(selected),
        next_cursor=next_cursor,
        truncated=truncated,
        bridge_epoch="",
    )


def _native_window_start(frames: tuple[NativeEvidenceFrame, ...], cursor: str | None) -> int:
    if cursor is None:
        return 0
    for index, frame in enumerate(frames):
        if frame.native_id == cursor:
            return index + 1
    raise HarnessControlError("native evidence page cursor is absent from the current native read")


def _serialized_size(value: Mapping[str, object]) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
