"""Projector engine: hydration, ordering, idempotence, provenance, rehydration, gaps."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from dataclasses import replace
from itertools import pairwise
from unittest import mock

import agents_remember.serving.conversation.active.projector as projector_module
from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.conversation.active.cursor import mint_event_cursor
from agents_remember.serving.conversation.active.projector import (
    ActiveSessionProjector,
    EvidenceTimelineRegressed,
    ZipperEvidenceEvicted,
    mutation_stream,
)
from agents_remember.serving.conversation.active.projector.facade import ProjectedSession
from agents_remember.serving.conversation.active.projector.wiring import BridgeReaders
from agents_remember.serving.conversation.active.store import ProjectionStore
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    AuthorizationBinding,
    ConversationEventEnvelope,
    GapMutation,
    StatusMutation,
    ToolOutputBlock,
    UnknownVendorBlock,
)
from agents_remember.serving.conversation.projectors import claude, codex, pi, projector_for
from agents_remember.serving.conversation.projectors.codex import map_native_frame
from agents_remember.serving.conversation.projectors.common import MappedBlockDelta, MappedItem
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    EvidenceFrame,
    EvidencePage,
    NativeEvidenceFrame,
    NativeEvidencePage,
    SubmissionProvenance,
    SubmissionProvenanceBatch,
)

NOW = "2026-07-19T08:00:00+00:00"
SECRET = b"x" * 32


def _identity(harness: str = "codex") -> ActiveConversationRef:
    return ActiveConversationRef(
        harness_id=harness,  # type: ignore[arg-type]
        vendor_conversation_id="vendor-1",
        project_scope="/workspace",
        identity_digest="digest-1",
        ar_session_id="ar-1",
        bridge_epoch="epoch-1",
    )


def _authorization() -> AuthorizationBinding:
    return AuthorizationBinding(principal_id="local-operator:1000", tenant_id="/workspace")


class _ControlledEntry:
    id = "ar-1"
    tmux_name = "ar-t-1"
    created_at = NOW
    control_endpoint = None


class _ScriptedBridge:
    """In-memory scripted substrate mirroring the IPC read shapes."""

    def __init__(self, *, harness: str = "codex", epoch: str = "epoch-1") -> None:
        self.epoch = epoch
        self.harness = harness
        self.evidence_frames: list[EvidenceFrame] = []
        self.transcript_entries: list[Mapping[str, object]] = []
        self.native_frames: list[NativeEvidenceFrame] = []
        self.native_error: Exception | None = None
        self.provenance: dict[str, str] = {}
        self.evicted_before = 0
        self.snapshot = AdapterSnapshot(
            identity=ControlIdentity(ar_session_id="ar-1", tmux_name="ar-t-1", created_at=NOW),
            control="ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id="vendor-1",
            raw={},
        )

    def push_evidence(self, kind: str, raw: dict) -> None:
        self.evidence_frames.append(
            EvidenceFrame(
                sequence=len(self.evidence_frames) + 1,
                kind=kind,
                created_at=NOW,
                raw=raw,
            )
        )

    def read_evidence(self, entry, *, after_sequence=0, limit=500, expected_bridge_epoch=None):
        del entry
        if expected_bridge_epoch is not None and expected_bridge_epoch != self.epoch:
            raise HarnessBridgeEpochMismatchError(expected_bridge_epoch, self.epoch)
        frames = [f for f in self.evidence_frames if f.sequence > after_sequence]
        latest = self.evidence_frames[-1].sequence if self.evidence_frames else 0
        return EvidencePage(
            frames=tuple(frames[:limit]),
            latest_sequence=latest,
            evicted_before_sequence=self.evicted_before,
            truncated=len(frames) > limit,
            bridge_epoch=self.epoch,
        )

    def read_native_page(self, entry, *, cursor=None, limit=200, expected_bridge_epoch=None):
        # The production seam (``read_control_native_page``) takes exactly these; a double that
        # also accepted a ``byte_budget`` would let a caller pass one the real reader rejects.
        del entry, expected_bridge_epoch
        if self.native_error is not None:
            raise self.native_error
        start = 0
        if cursor is not None:
            for index, frame in enumerate(self.native_frames):
                if frame.native_id == cursor:
                    start = index + 1
                    break
        selected = self.native_frames[start : start + limit]
        truncated = start + limit < len(self.native_frames)
        return NativeEvidencePage(
            frames=tuple(selected),
            next_cursor=selected[-1].native_id if truncated and selected else None,
            truncated=truncated,
            bridge_epoch=self.epoch,
        )

    def read_transcript(self, entry, *, after_sequence=0, limit=500):
        del entry
        return tuple(
            entry_ for entry_ in self.transcript_entries if _entry_sequence(entry_) > after_sequence
        )[:limit]

    def read_provenance(self, entry, *, expected_bridge_epoch, request_ids):
        del entry
        if expected_bridge_epoch != self.epoch:
            raise HarnessBridgeEpochMismatchError(expected_bridge_epoch, self.epoch)
        return SubmissionProvenanceBatch(
            bridge_epoch=self.epoch,
            provenance=tuple(
                SubmissionProvenance(
                    request_id=request_id,
                    outcome="found" if request_id in self.provenance else "not-found",
                    source=self.provenance.get(request_id),  # type: ignore[arg-type]
                    state="delivered",
                    submitted_at=NOW,
                    updated_at=NOW,
                    accepted_at=NOW,
                )
                for request_id in request_ids
            ),
        )

    def read_snapshot(self, entry) -> AdapterSnapshot:
        del entry
        return self.snapshot


def _entry_sequence(entry: Mapping[str, object]) -> int:
    value = entry.get("sequence")
    return value if isinstance(value, int) and not isinstance(value, bool) else -1


def _projector(bridge: _ScriptedBridge, harness: str = "codex") -> ActiveSessionProjector:
    mapper = projector_for(harness)
    assert mapper is not None
    return ActiveSessionProjector(
        ProjectedSession(
            identity=_identity(harness),
            authorization=_authorization(),
            entry=_ControlledEntry(),
            mapper=mapper,
            secret=SECRET,
        ),
        clock=lambda: NOW,
        readers=BridgeReaders(
            evidence=bridge.read_evidence,
            native_page=bridge.read_native_page,
            transcript=bridge.read_transcript,
            provenance=bridge.read_provenance,
            snapshot=bridge.read_snapshot,
        ),
    )


def _codex_turn(bridge: _ScriptedBridge, turn: str, *, prompt: str = "go") -> None:
    bridge.push_evidence(
        "codex-notification",
        {
            "threadId": "vendor-1",
            "turnId": turn,
            "startedAtMs": 1,
            "item": {
                "id": f"{turn}-user",
                "type": "userMessage",
                "clientId": f"req-{turn}",
                "content": [{"type": "text", "text": prompt}],
            },
        },
    )
    bridge.push_evidence(
        "codex-notification",
        {
            "threadId": "vendor-1",
            "turnId": turn,
            "startedAtMs": 2,
            "item": {"id": f"{turn}-agent", "type": "agentMessage", "text": "working"},
        },
    )
    bridge.push_evidence(
        "codex-notification",
        {"threadId": "vendor-1", "turnId": turn, "itemId": f"{turn}-agent", "delta": " more"},
    )
    bridge.push_evidence(
        "completed",
        {"threadId": "vendor-1", "turn": {"id": turn, "status": "completed", "items": []}},
    )


class CodexEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_hydration_orders_items_and_mints_stable_ids(self) -> None:
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        ids = [item.item_id for item in result.items]
        self.assertEqual(
            ids,
            ["turn-1-user", "turn-1-agent", "turn-result:turn-1"],
        )
        agent = result.items[1]
        from agents_remember.serving.conversation.models import MarkdownBlock  # noqa: PLC0415

        self.assertIsInstance(agent.blocks[0], MarkdownBlock)
        self.assertEqual(
            next(block for block in agent.blocks if isinstance(block, MarkdownBlock)).markdown,
            "working more",
        )
        self.assertEqual(agent.revision, 2)
        self.assertEqual([item.global_ordinal for item in result.items], [1, 2, 3])
        self.assertEqual(result.total_items, 3)
        self.assertFalse(result.has_older)

    async def test_provenance_resolution_exact_then_unknown(self) -> None:
        bridge = _ScriptedBridge()
        bridge.provenance["req-turn-1"] = "durable"
        _codex_turn(bridge, "turn-1")
        bridge.provenance["req-turn-2"] = "cockpit"
        _codex_turn(bridge, "turn-2")
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        by_id = {item.item_id: item for item in result.items}
        first = by_id["turn-1-user"]
        self.assertEqual(first.lane, "agent-bus")
        self.assertEqual(first.source, "durable-inbox")
        self.assertEqual(first.provenance.producer, "agent-bus")
        self.assertEqual(first.provenance.strength, "exact")
        second = by_id["turn-2-user"]
        self.assertEqual(second.lane, "operator")
        self.assertEqual(second.source, "cockpit-composer")
        # A user item with no provenance record stays honest unknown-input.
        bridge2 = _ScriptedBridge()
        _codex_turn(bridge2, "turn-9")
        result2 = await _projector(bridge2).page(before_ordinal=None, limit=50)
        unknown = result2.items[0]
        self.assertEqual(unknown.lane, "unknown-input")
        self.assertEqual(unknown.provenance.strength, "native-only")

    async def test_native_remap_after_resolution_stays_model_valid(self) -> None:
        """The projector must never emit an item that

        violates its own ``preserve_input_authority`` under real codex traffic. A user message
        resolves to ``cockpit`` (operator/exact); codex then re-maps the SAME native user item
        (which re-emits the ``unknown-input`` default). Before the fix the store's upsert adopted the
        candidate's ``unknown-input`` lane while preserving the resolved ``exact`` provenance --
        silently stored (``model_copy`` skips validation) and 500-ing the active-page route only at
        response re-validation. Every emitted item must survive a full model re-validation.
        """
        from agents_remember.serving.conversation.models import ConversationItem  # noqa: PLC0415

        bridge = _ScriptedBridge()
        bridge.provenance["req-turn-1"] = "cockpit"
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        first = await projector.page(before_ordinal=None, limit=50)
        resolved = {item.item_id: item for item in first.items}["turn-1-user"]
        self.assertEqual(resolved.lane, "operator")
        self.assertEqual(resolved.provenance.strength, "exact")

        # Codex re-maps the same native user item on a later observation cycle.
        bridge.push_evidence(
            "codex-notification",
            {
                "threadId": "vendor-1",
                "turnId": "turn-1",
                "startedAtMs": 9,
                "item": {
                    "id": "turn-1-user",
                    "type": "userMessage",
                    "clientId": "req-turn-1",
                    "content": [{"type": "text", "text": "go"}],
                },
            },
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)

        # Every item the projector serves must re-validate as the route response would.
        for item in result.items:
            ConversationItem.model_validate(item.model_dump(by_alias=True))
        remapped = {item.item_id: item for item in result.items}["turn-1-user"]
        self.assertEqual(remapped.lane, "operator", "a re-map must not revert the resolved lane")
        self.assertEqual(remapped.source, "cockpit-composer")
        self.assertEqual(remapped.provenance.strength, "exact")

    async def test_settled_live_turns_project_once_when_native_ids_disjoint(self) -> None:
        """A settled codex turn must project EXACTLY once.

        On a hosted codex ``+ Chat`` thread the live notification channel and ``thread/read`` use
        DISJOINT id namespaces for the same settled turn (live ``msg_*``/UUID item ids vs positional
        ``item-N``), so the store's id-keyed dedupe can never converge them and
        ``_refresh_native_tip``'s post-turn native re-walk mints a second, ``native-history`` twin of
        every settled turn (developer-visible duplicate rows, deterministic 2/2 turns on the wire).
        Here the native page groups the twins under the SAME turn ids the live channel used.
        """
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        _codex_turn(bridge, "turn-2")
        await projector.poll_once()  # both turns now settled live under their live-id namespace
        live = await projector.page(before_ordinal=None, limit=50)
        live_ids = [item.item_id for item in live.items]
        self.assertEqual(
            live_ids,
            [
                "turn-1-user",
                "turn-1-agent",
                "turn-result:turn-1",
                "turn-2-user",
                "turn-2-agent",
                "turn-result:turn-2",
            ],
        )
        # The hosted thread now persists the SAME settled turns through thread/read under DISJOINT
        # positional item ids, grouped under the live turn ids.
        bridge.native_frames.extend(
            [
                NativeEvidenceFrame(
                    native_id="item-1",
                    native_parent_id="turn-1",
                    native_type="userMessage",
                    created_at=NOW,
                    raw={
                        "id": "item-1",
                        "type": "userMessage",
                        "clientId": "req-turn-1",
                        "content": [{"type": "text", "text": "go"}],
                    },
                ),
                NativeEvidenceFrame(
                    native_id="item-2",
                    native_parent_id="turn-1",
                    native_type="agentMessage",
                    created_at=NOW,
                    raw={"id": "item-2", "type": "agentMessage", "text": "working more"},
                ),
                NativeEvidenceFrame(
                    native_id="item-3",
                    native_parent_id="turn-2",
                    native_type="userMessage",
                    created_at=NOW,
                    raw={
                        "id": "item-3",
                        "type": "userMessage",
                        "clientId": "req-turn-2",
                        "content": [{"type": "text", "text": "go"}],
                    },
                ),
                NativeEvidenceFrame(
                    native_id="item-4",
                    native_parent_id="turn-2",
                    native_type="agentMessage",
                    created_at=NOW,
                    raw={"id": "item-4", "type": "agentMessage", "text": "working more"},
                ),
            ]
        )
        # Any further live evidence marks the native tip dirty; the next page re-walks thread/read.
        bridge.push_evidence(
            "codex-notification",
            {"threadId": "vendor-1", "turnId": "turn-2", "tokenUsage": {"totalTokens": 1}},
        )
        await projector.poll_once()
        settled = await projector.page(before_ordinal=None, limit=50)
        settled_ids = [item.item_id for item in settled.items]
        self.assertEqual(
            settled_ids, live_ids, "settled turns must project once, never as native-history twins"
        )
        self.assertNotIn("item-1", settled_ids)
        self.assertEqual(settled.total_items, len(live_ids))

    async def test_settled_live_turns_project_once_when_hosted_renumbers_turn_ids(self) -> None:
        """Single projection holds even if the hosted thread RENUMBERS turn ids.

        Correlation must not depend on the native turn id equalling the live turn id: ``clientId`` is
        our own submitted request id and survives verbatim into ``thread/read``, so a native user
        frame whose ``clientId`` we submitted live anchors its whole native turn as a live duplicate.
        """
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        live = await projector.page(before_ordinal=None, limit=50)
        live_ids = [item.item_id for item in live.items]
        self.assertEqual(live_ids, ["turn-1-user", "turn-1-agent", "turn-result:turn-1"])
        # thread/read persists the settled turn under a RENUMBERED native turn id + positional items,
        # but the userMessage still carries our submitted clientId.
        bridge.native_frames.extend(
            [
                NativeEvidenceFrame(
                    native_id="item-1",
                    native_parent_id="hist-turn-a",
                    native_type="userMessage",
                    created_at=NOW,
                    raw={
                        "id": "item-1",
                        "type": "userMessage",
                        "clientId": "req-turn-1",
                        "content": [{"type": "text", "text": "go"}],
                    },
                ),
                NativeEvidenceFrame(
                    native_id="item-2",
                    native_parent_id="hist-turn-a",
                    native_type="agentMessage",
                    created_at=NOW,
                    raw={"id": "item-2", "type": "agentMessage", "text": "working more"},
                ),
            ]
        )
        bridge.push_evidence(
            "codex-notification",
            {"threadId": "vendor-1", "turnId": "turn-1", "tokenUsage": {"totalTokens": 1}},
        )
        await projector.poll_once()
        settled = await projector.page(before_ordinal=None, limit=50)
        settled_ids = [item.item_id for item in settled.items]
        self.assertEqual(
            settled_ids, live_ids, "clientId must anchor the whole renumbered native turn"
        )
        self.assertNotIn("item-1", settled_ids)
        self.assertNotIn("item-2", settled_ids)

    async def test_prior_session_native_history_survives_live_turns(self) -> None:
        """The suppression must never drop GENUINE prior-session history.

        A resumed thread's earlier turns (never observed live in this projector) must hydrate in full;
        only turns this run settled live are suppressed from the native re-walk.
        """
        bridge = _ScriptedBridge()
        bridge.native_frames.append(
            NativeEvidenceFrame(
                native_id="hist-1",
                native_parent_id="turn-0",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "hist-1", "type": "agentMessage", "text": "earlier session"},
            )
        )
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        ids = [item.item_id for item in result.items]
        self.assertEqual(ids, ["hist-1", "turn-1-user", "turn-1-agent", "turn-result:turn-1"])

    async def test_rehydration_reproduces_identical_projection(self) -> None:
        bridge = _ScriptedBridge()
        bridge.provenance["req-turn-1"] = "cockpit"
        _codex_turn(bridge, "turn-1")
        first = await _projector(bridge).page(before_ordinal=None, limit=50)
        second_projector = _projector(bridge)
        second = await second_projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [(i.item_id, i.revision, i.global_ordinal, i.blocks) for i in first.items],
            [(i.item_id, i.revision, i.global_ordinal, i.blocks) for i in second.items],
        )

    async def test_native_page_hydration_and_older_paging(self) -> None:
        bridge = _ScriptedBridge()
        for index in range(5):
            bridge.native_frames.append(
                NativeEvidenceFrame(
                    native_id=f"native-{index}",
                    native_parent_id="turn-0",
                    native_type="agentMessage",
                    created_at=NOW,
                    raw={"id": f"native-{index}", "type": "agentMessage", "text": f"n{index}"},
                )
            )
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        latest = await projector.page(before_ordinal=None, limit=3)
        self.assertEqual(
            [item.item_id for item in latest.items],
            ["turn-1-user", "turn-1-agent", "turn-result:turn-1"],
        )
        self.assertTrue(latest.has_older)
        older = await projector.page(before_ordinal=6, limit=3)
        self.assertEqual(
            [item.item_id for item in older.items], ["native-2", "native-3", "native-4"]
        )
        self.assertTrue(older.has_older)
        oldest = await projector.page(before_ordinal=3, limit=3)
        self.assertEqual([item.item_id for item in oldest.items], ["native-0", "native-1"])
        self.assertFalse(oldest.has_older)
        self.assertEqual(oldest.total_items, 8)

    async def test_ephemeral_native_refusal_keeps_live_window_partial(self) -> None:
        bridge = _ScriptedBridge()
        bridge.native_error = HarnessControlError(
            "Codex native evidence page failed: ephemeral thread refuses includeTurns"
        )
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(len(result.items), 3)
        self.assertIsNone(result.total_items)
        self.assertFalse(result.has_older)

    async def test_gap_on_epoch_flip(self) -> None:
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        bridge.epoch = "epoch-2"
        with self.assertRaises(HarnessBridgeEpochMismatchError):
            await projector.poll_once()


class ClaudeEngineTests(unittest.IsolatedAsyncioTestCase):
    def _claude_turn(self, bridge: _ScriptedBridge, n: int) -> None:
        bridge.transcript_entries.append(
            {
                "sequence": n * 2 - 1,
                "role": "user",
                "text": f"prompt {n}",
                "createdAt": NOW,
                "requestId": f"req-{n}",
                "vendorCorrelationId": f"uuid-user-{n}",
            }
        )
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": f"uuid-agent-{n}",
                "session_id": "vendor-1",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": f"answer {n}"}],
                },
            },
        )
        bridge.push_evidence(
            "completed",
            {"type": "result", "subtype": "success", "is_error": False, "uuid": f"uuid-result-{n}"},
        )

    async def test_zipper_orders_users_before_their_turn_frames(self) -> None:
        bridge = _ScriptedBridge(harness="claude")
        self._claude_turn(bridge, 1)
        self._claude_turn(bridge, 2)
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-agent-1",
                "uuid-result-1:result",
                "uuid-user-2",
                "uuid-agent-2",
                "uuid-result-2:result",
            ],
        )

    async def test_nonuser_transcript_entries_mint_no_echo_unknown_vendor_rows(self) -> None:
        # The claude transcript carries assistant and result entries alongside
        # user submission echoes (claude_stream_state emits role="assistant"/"result"). Feeding those
        # non-user entries to the user-only echo mapper is what produced the
        # "claude:echo: unrecognized submission echo shape" rows. The echo poller must advance past
        # them, minting zero unknown-vendor echo rows while the user echo + evidence still map.
        bridge = _ScriptedBridge(harness="claude")
        bridge.transcript_entries.append(
            {
                "sequence": 1,
                "role": "user",
                "text": "prompt 1",
                "createdAt": NOW,
                "requestId": "req-1",
                "vendorCorrelationId": "uuid-user-1",
            }
        )
        # The interleaved assistant/result transcript entries the production claude state emits.
        bridge.transcript_entries.append(
            {
                "sequence": 2,
                "role": "assistant",
                "text": "answer 1",
                "createdAt": NOW,
                "requestId": "req-1",
                "vendorCorrelationId": "uuid-agent-1",
            }
        )
        bridge.transcript_entries.append(
            {
                "sequence": 3,
                "role": "result",
                "text": "completed",
                "createdAt": NOW,
                "requestId": "req-1",
                "vendorCorrelationId": "uuid-result-1",
            }
        )
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-1",
                "session_id": "vendor-1",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "answer 1"}],
                },
            },
        )
        bridge.push_evidence(
            "completed",
            {"type": "result", "subtype": "success", "is_error": False, "uuid": "uuid-result-1"},
        )
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        vendor_types = [
            getattr(block, "vendor_type", None)
            for item in result.items
            for block in item.blocks
            if getattr(block, "type", None) == "unknown-vendor"
        ]
        self.assertNotIn("claude:echo", vendor_types)
        self.assertTrue(
            all(
                "echo-" not in item.item_id or item.item_id == "uuid-user-1"
                for item in result.items
            )
        )
        self.assertIn("uuid-user-1", [item.item_id for item in result.items])

    async def test_truncation_envelope_classifies_as_evidence_truncated_not_malformed(self) -> None:
        # An oversized frame the evidence-substrate clip bounded to the evidence budget
        # arrives as the substrate's OWN envelope (arEvidenceTruncated), not a vendor shape.
        # Exact parsing branded it "claude:malformed: native frame failed exact schema parsing";
        # it must classify honestly, naming the preserved
        # frame type and original size, and never as malformed.
        bridge = _ScriptedBridge(harness="claude")
        bridge.push_evidence(
            "state",
            {
                "arEvidenceTruncated": True,
                "originalBytes": 44268,
                "preview": '{"type":"user","message":{"role":"user","content":[{"tool_use_id"…',
                "type": "user",
            },
        )
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        vendor_blocks = [
            block
            for item in result.items
            for block in item.blocks
            if isinstance(block, UnknownVendorBlock)
        ]
        self.assertEqual(
            [block.vendor_type for block in vendor_blocks],
            ["claude:evidence-truncated"],
        )
        self.assertEqual(
            vendor_blocks[0].safe_summary,
            "oversized user frame clipped to the evidence budget (44268 bytes)",
        )

    async def test_zipper_handles_split_result_across_polls(self) -> None:
        bridge = _ScriptedBridge(harness="claude")
        # Turn 1 result arrives only in a later poll than user 2's echo.
        bridge.transcript_entries.append(
            {
                "sequence": 1,
                "role": "user",
                "text": "p1",
                "createdAt": NOW,
                "requestId": "req-1",
                "vendorCorrelationId": "uuid-user-1",
            }
        )
        bridge.transcript_entries.append(
            {
                "sequence": 2,
                "role": "user",
                "text": "p2",
                "createdAt": NOW,
                "requestId": "req-2",
                "vendorCorrelationId": "uuid-user-2",
            }
        )
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-1",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            },
        )
        projector = _projector(bridge, harness="claude")
        bridge2 = bridge
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            ["uuid-user-1", "uuid-agent-1", "uuid-user-2"],
        )
        bridge2.push_evidence(
            "completed",
            {"type": "result", "subtype": "success", "is_error": False, "uuid": "uuid-result-1"},
        )
        bridge2.transcript_entries.append(
            {
                "sequence": 3,
                "role": "user",
                "text": "p3",
                "createdAt": NOW,
                "requestId": "req-3",
                "vendorCorrelationId": "uuid-user-3",
            }
        )
        await projector.poll_once()
        result2 = await projector.page(before_ordinal=None, limit=50)
        ids = [item.item_id for item in result2.items]
        self.assertLess(ids.index("uuid-result-1:result"), ids.index("uuid-user-3"))

    async def test_page_never_claims_completeness_while_frames_pend(self) -> None:
        # Pending zipper frames are projected content the store cannot show
        # yet. A page that claims a complete totalItems while frames pend tells the client
        # "this is the whole history" — which the UI honestly renders as a short transcript
        # over a live session. Completeness must account for the pend.
        #
        # The pend must NOT be signalled through hasOlder. Pending frames are NEWER
        # content and the older-history channel cannot carry them: widening hasOlder alone
        # leaves olderOrdinal None, `exclude_none` drops olderCursor from the wire, and the
        # browser renders a live "Load older" button whose click refetches the newest page
        # forever — sticky for the life of the projection, on a page that simultaneously
        # reported a complete total. hasOlder stays correlated with its cursor; the total goes
        # unknown instead.
        bridge = _ScriptedBridge(harness="claude")
        # An assistant frame with NO user echo yet: it lands in _pending_frames for its turn.
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-orphan",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a"}]},
            },
        )
        projector = _projector(bridge, harness="claude")
        settled = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            settled.total_items,
            1,
            "with nothing pending the window is complete and its total is known",
        )
        # Force the pend to persist: a fresh frame with its echo still absent.
        projector._echo.pending_frames.append(
            EvidenceFrame(
                sequence=99,
                kind="state",
                created_at=NOW,
                raw={
                    "type": "assistant",
                    "uuid": "uuid-agent-pending",
                    "session_id": "vendor-1",
                    "message": {"role": "assistant", "content": [{"type": "text", "text": "b"}]},
                },
            )
        )
        pended = await projector.page(before_ordinal=None, limit=50)
        self.assertIsNone(
            pended.total_items,
            "a page must not claim a complete total while zipper frames are still pending",
        )
        self.assertFalse(
            pended.has_older,
            "pending frames are NEWER content: they must never be signalled as older history",
        )
        self.assertEqual(
            pended.has_older,
            pended.older_ordinal is not None,
            "hasOlder must hold exactly when an older cursor exists (store.py mints both "
            "from one expression); a claimed older page with no cursor is a dead button",
        )

    async def test_regressed_evidence_tip_gaps_instead_of_freezing(self) -> None:
        # `caught_up` compares the consumed watermark against the bridge's
        # latest_sequence. If the bridge re-baselines its evidence numbering (tip moves
        # BACKWARD), the comparison is true forever and the projector silently freezes on its
        # pre-reset window while the session keeps running. A regressed tip must fail loud.
        bridge = _ScriptedBridge(harness="claude")
        self._claude_turn(bridge, 1)
        projector = _projector(bridge, harness="claude")
        await projector.page(before_ordinal=None, limit=50)
        # The bridge re-baselines: its whole evidence buffer resets behind the consumed tip.
        bridge.evidence_frames.clear()
        with self.assertRaises(EvidenceTimelineRegressed):
            await projector.poll_once()

    async def test_idempotent_refeed_after_rebuild(self) -> None:
        bridge = _ScriptedBridge(harness="claude")
        self._claude_turn(bridge, 1)
        first = await _projector(bridge, harness="claude").page(before_ordinal=None, limit=50)
        second = await _projector(bridge, harness="claude").page(before_ordinal=None, limit=50)
        self.assertEqual(
            [(i.item_id, i.revision, i.global_ordinal) for i in first.items],
            [(i.item_id, i.revision, i.global_ordinal) for i in second.items],
        )


class PiEngineTests(unittest.IsolatedAsyncioTestCase):
    async def test_entries_hydrate_and_eager_continuation(self) -> None:
        bridge = _ScriptedBridge(harness="pi")
        bridge.native_frames.append(
            NativeEvidenceFrame(
                native_id="entry-1",
                native_parent_id=None,
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-1",
                    "type": "message",
                    "message": {"role": "user", "content": "hello", "timestamp": 1},
                },
            )
        )
        projector = _projector(bridge, harness="pi")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual([item.item_id for item in result.items], ["entry-1"])
        self.assertEqual(result.items[0].lane, "unknown-input")
        bridge.native_frames.append(
            NativeEvidenceFrame(
                native_id="entry-2",
                native_parent_id="entry-1",
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-2",
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "hi"}],
                        "stopReason": "stop",
                        "timestamp": 2,
                    },
                },
            )
        )
        await projector.poll_once()
        result2 = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual([item.item_id for item in result2.items], ["entry-1", "entry-2"])
        self.assertEqual(result2.total_items, 2)


class StoreTests(unittest.TestCase):
    def test_upsert_skips_identical_replay(self) -> None:
        outputs = map_native_frame(
            NativeEvidenceFrame(
                native_id="item-1",
                native_parent_id="turn-1",
                native_type="agentMessage",
                created_at=NOW,
                raw={"id": "item-1", "type": "agentMessage", "text": "same"},
            ),
            evidence_ref="ref",
        )
        store = ProjectionStore()
        mapped = outputs[0]
        assert isinstance(mapped, MappedItem)
        first = store.apply_item(mapped)
        self.assertEqual(first[0].kind, "append-item")
        self.assertEqual(store.apply_item(mapped), [])
        self.assertEqual(store.item_count, 1)


class ToolConvergenceTests(unittest.TestCase):
    """Partial-block tool items converge by scoped block-union."""

    def _blocks(self, item) -> dict:
        return {block.block_id: block for block in item.blocks}

    def test_claude_tool_use_then_tool_result_keeps_input_and_output(self) -> None:
        store = ProjectionStore()
        use = claude.map_evidence_frame(
            EvidenceFrame(
                sequence=1,
                kind="state",
                created_at=NOW,
                raw={
                    "type": "assistant",
                    "uuid": "uuid-1",
                    "session_id": "vendor-1",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "tool_use",
                                "id": "toolu_1",
                                "name": "Bash",
                                "input": {"command": "ls -la"},
                            }
                        ],
                    },
                },
            ),
            evidence_ref="ref-1",
        )
        for output in use:
            if isinstance(output, MappedItem):
                store.apply_item(output)
        result = claude.map_evidence_frame(
            EvidenceFrame(
                sequence=2,
                kind="state",
                created_at=NOW,
                raw={
                    "type": "user",
                    "uuid": "uuid-2",
                    "message": {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": [{"type": "text", "text": "total 0"}],
                                "is_error": False,
                            }
                        ],
                    },
                },
            ),
            evidence_ref="ref-2",
        )
        for output in result:
            if isinstance(output, MappedItem):
                store.apply_item(output)
        (item,) = store.items()
        blocks = self._blocks(item)
        self.assertEqual(set(blocks), {"input", "output"})
        self.assertEqual(blocks["input"].summary, "Bash")
        self.assertEqual(blocks["input"].data, {"command": "ls -la"})
        self.assertEqual(blocks["output"].text, "total 0")
        self.assertEqual(item.phase, "completed")

    def test_reordered_task_started_tagging_never_regresses_a_terminal_phase(self) -> None:
        """Reordered evidence — the Agent
        tool_result settles the call BEFORE task_started binds the agent identity —
        keeps the terminal phase while the agent ref still lands."""

        session = "vendor-1"
        tool_id = "toolu_agent_1"
        frames = [
            {
                "type": "assistant",
                "uuid": "uuid-spawn",
                "session_id": session,
                "parent_tool_use_id": None,
                "message": {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_id,
                            "name": "Agent",
                            "input": {
                                "description": "probe",
                                "subagent_type": "Explore",
                                "run_in_background": False,
                                "prompt": "read probe.txt",
                            },
                        }
                    ],
                },
            },
            {
                "type": "user",
                "uuid": "uuid-spawn-result",
                "session_id": session,
                "parent_tool_use_id": None,
                "message": {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": [{"type": "text", "text": "The word is: probe"}],
                        }
                    ],
                },
            },
            # The reordered binder: task_started arrives AFTER the tool_result.
            {
                "type": "system",
                "subtype": "task_started",
                "task_id": "task-1",
                "tool_use_id": tool_id,
                "description": "probe",
                "subagent_type": "Explore",
                "task_type": "local_agent",
                "prompt": "read probe.txt",
                "uuid": "uuid-started",
                "session_id": session,
            },
        ]
        store = ProjectionStore()
        for sequence, raw in enumerate(frames, start=1):
            for output in claude.map_evidence_frame(
                EvidenceFrame(sequence=sequence, kind="state", created_at=NOW, raw=raw),
                evidence_ref=f"ref-{sequence}",
            ):
                if isinstance(output, MappedItem):
                    store.apply_item(output)
        item = next(item for item in store.items() if item.item_id == tool_id)
        self.assertEqual(item.phase, "completed")
        assert item.agent is not None
        self.assertEqual(item.agent.agent_id, "task-1")

    def test_pi_live_start_update_end_keeps_input_through_every_step(self) -> None:
        store = ProjectionStore()
        start = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=1,
                kind="pi:tool_execution_start",
                created_at=NOW,
                raw={
                    "type": "tool_execution_start",
                    "toolCallId": "tc-1",
                    "toolName": "bash",
                    "args": {"command": "ls"},
                },
            ),
            evidence_ref="ref-1",
        )
        update = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=2,
                kind="pi:tool_execution_update",
                created_at=NOW,
                raw={
                    "type": "tool_execution_update",
                    "toolCallId": "tc-1",
                    "toolName": "bash",
                    "args": {"command": "ls"},
                    "partialResult": {"content": [{"type": "text", "text": "part"}]},
                },
            ),
            evidence_ref="ref-2",
        )
        end = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=3,
                kind="pi:tool_execution_end",
                created_at=NOW,
                raw={
                    "type": "tool_execution_end",
                    "toolCallId": "tc-1",
                    "toolName": "bash",
                    "result": {"content": [{"type": "text", "text": "done"}]},
                    "isError": False,
                },
            ),
            evidence_ref="ref-3",
        )
        for outputs in (start, update, end):
            for output in outputs:
                if isinstance(output, MappedItem):
                    store.apply_item(output)
        (item,) = store.items()
        blocks = self._blocks(item)
        self.assertEqual(set(blocks), {"input", "output"})
        self.assertEqual(blocks["input"].summary, "bash")
        self.assertEqual(blocks["input"].data, {"command": "ls"})
        self.assertEqual(item.phase, "completed")
        self.assertEqual(item.revision, 3)

    def test_pi_entry_call_then_tool_result_keeps_input_and_output(self) -> None:
        store = ProjectionStore()
        call = pi.map_native_frame(
            NativeEvidenceFrame(
                native_id="entry-1",
                native_parent_id=None,
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-1",
                    "type": "message",
                    "message": {
                        "role": "assistant",
                        "content": [
                            {
                                "type": "toolCall",
                                "id": "tc-9",
                                "name": "bash",
                                "arguments": {"command": "ls"},
                            }
                        ],
                        "stopReason": "toolUse",
                        "timestamp": 1,
                    },
                },
            ),
            evidence_ref="ref-1",
        )
        result = pi.map_native_frame(
            NativeEvidenceFrame(
                native_id="entry-2",
                native_parent_id="entry-1",
                native_type="message",
                created_at=NOW,
                raw={
                    "id": "entry-2",
                    "type": "message",
                    "message": {
                        "role": "toolResult",
                        "toolCallId": "tc-9",
                        "toolName": "bash",
                        "content": [{"type": "text", "text": "done"}],
                        "isError": False,
                        "timestamp": 2,
                    },
                },
            ),
            evidence_ref="ref-2",
        )
        for outputs in (call, result):
            for output in outputs:
                if isinstance(output, MappedItem):
                    store.apply_item(output)
        tool = next(item for item in store.items() if item.item_id == "tc-9")
        blocks = self._blocks(tool)
        self.assertEqual(set(blocks), {"input", "output"})
        self.assertEqual(blocks["input"].summary, "bash")
        self.assertEqual(blocks["output"].text, "done")
        self.assertEqual(tool.phase, "completed")

    def test_codex_full_item_remap_is_identical_under_union(self) -> None:
        outputs = map_native_frame(
            NativeEvidenceFrame(
                native_id="item-7",
                native_parent_id="turn-1",
                native_type="commandExecution",
                created_at=NOW,
                raw={
                    "id": "item-7",
                    "type": "commandExecution",
                    "command": "ls",
                    "cwd": "/workspace",
                    "status": "completed",
                    "aggregatedOutput": "total 0",
                    "exitCode": 0,
                },
            ),
            evidence_ref="ref",
        )
        store = ProjectionStore()
        mapped = outputs[0]
        assert isinstance(mapped, MappedItem)
        store.apply_item(mapped)
        self.assertEqual(store.apply_item(mapped), [])
        (item,) = store.items()
        self.assertEqual(item.revision, 1)
        self.assertEqual({block.block_id for block in item.blocks}, {"input", "output"})


class DeltaStreamingTests(unittest.TestCase):
    """Streamed command output is live and never duplicated.

    Two store invariants: a delta addressed to a block the existing item does not carry yet
    MINTS the block (emitted as upsert-item so a subscriber converges without a re-page), and
    an arriving item whose block carries aggregated content DROPS that block's buffered delta
    backlog instead of replaying it on top.
    """

    def _started_command(self, sequence: int = 1) -> list:
        return codex.map_evidence_frame(
            EvidenceFrame(
                sequence=sequence,
                kind="codex-notification",
                created_at=NOW,
                raw={
                    "item": {
                        "id": "item-cmd",
                        "type": "commandExecution",
                        "command": "make test",
                        "status": "inProgress",
                    },
                    "turnId": "turn-1",
                    "startedAtMs": 1,
                },
            ),
            evidence_ref=f"ref-{sequence}",
        )

    def _completed_command(self, sequence: int, aggregated: str) -> list:
        return codex.map_evidence_frame(
            EvidenceFrame(
                sequence=sequence,
                kind="codex-notification",
                created_at=NOW,
                raw={
                    "item": {
                        "id": "item-cmd",
                        "type": "commandExecution",
                        "command": "make test",
                        "status": "completed",
                        "aggregatedOutput": aggregated,
                        "exitCode": 0,
                    },
                    "turnId": "turn-1",
                },
            ),
            evidence_ref=f"ref-{sequence}",
        )

    def _delta(self, sequence: int, text: str) -> list:
        return codex.map_evidence_frame(
            EvidenceFrame(
                sequence=sequence,
                kind="codex-notification",
                created_at=NOW,
                raw={"itemId": "item-cmd", "delta": text},
            ),
            evidence_ref=f"ref-{sequence}",
        )

    @staticmethod
    def _apply(store: ProjectionStore, outputs: list) -> list:
        mutations = []
        for output in outputs:
            if isinstance(output, MappedItem):
                mutations.extend(store.apply_item(output))
            elif isinstance(output, MappedBlockDelta):
                mutations.extend(store.apply_delta(output))
        return mutations

    @staticmethod
    def _output_text(store: ProjectionStore) -> str | None:
        (item,) = store.items()
        block = next((b for b in item.blocks if b.block_id == "output"), None)
        return block.text if isinstance(block, ToolOutputBlock) else None

    def test_output_delta_mints_missing_block_and_streams_live(self) -> None:
        store = ProjectionStore()
        self._apply(store, self._started_command())
        first = self._apply(store, self._delta(2, "line 1\n"))
        # The mint crosses as a full upsert (a bare delta naming an unknown block would force
        # the browser reducer to re-page).
        self.assertEqual([mutation.kind for mutation in first], ["upsert-item"])
        second = self._apply(store, self._delta(3, "line 2\n"))
        self.assertEqual([mutation.kind for mutation in second], ["append-block-delta"])
        self.assertEqual(self._output_text(store), "line 1\nline 2\n")

    def test_completed_aggregate_drops_buffered_backlog(self) -> None:
        # Deltas arrive before the item ever materializes (arrival race) and then the completed
        # item carries the FULL aggregated output: the backlog must not replay on top.
        store = ProjectionStore()
        self._apply(store, self._delta(1, "line 1\n"))
        self._apply(store, self._delta(2, "line 2\n"))
        self._apply(store, self._completed_command(3, "line 1\nline 2\nline 3\n"))
        self.assertEqual(self._output_text(store), "line 1\nline 2\nline 3\n")

    def test_streamed_then_completed_aggregate_never_duplicates(self) -> None:
        # The live path: started item, streamed deltas, then the aggregate-carrying completion.
        # The completion's output block is authoritative and replaces the accumulated stream.
        store = ProjectionStore()
        self._apply(store, self._started_command())
        self._apply(store, self._delta(2, "line 1\n"))
        self._apply(store, self._delta(3, "line 2\n"))
        self._apply(store, self._completed_command(4, "line 1\nline 2\n"))
        self.assertEqual(self._output_text(store), "line 1\nline 2\n")
        (item,) = store.items()
        self.assertEqual(item.phase, "completed")

    def test_buffered_deltas_apply_when_started_item_carries_no_output(self) -> None:
        # Backlog before the STARTED item (which carries no output block): nothing is dropped —
        # the flush mints the block and the stream stays whole.
        store = ProjectionStore()
        self._apply(store, self._delta(1, "early\n"))
        self._apply(store, self._started_command(2))
        self.assertEqual(self._output_text(store), "early\n")


class GenesisCursorTests(unittest.IsolatedAsyncioTestCase):
    """Genesis (sequence-1) envelopes chain to the stream origin.

    A fresh page names event cursor 0; the first live frame must carry that
    cursor as its previousCursor instead of dropping the key (exclude_none),
    or the browser chain guard re-pages on every fresh chat.
    """

    def _origin(self, projector: ActiveSessionProjector) -> str:
        return str(
            mint_event_cursor(
                SECRET,
                _authorization(),
                _identity(),
                generation=projector.generation,
                sequence=0,
            )
        )

    async def test_genesis_envelope_previous_cursor_is_event_cursor_zero(self) -> None:
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        bridge.push_evidence(
            "codex-notification",
            {
                "threadId": "vendor-1",
                "turnId": "turn-1",
                "completedAtMs": 10,
                "item": {"id": "turn-1-agent", "type": "agentMessage", "text": "hi"},
            },
        )
        await projector.poll_once()
        retained = projector.retained_after(0)
        genesis = retained[0]
        self.assertEqual(genesis.sequence, 1)
        assert genesis.previous_cursor is not None
        self.assertEqual(str(genesis.previous_cursor), self._origin(projector))
        # exclude_none wire serialization used to drop the key entirely; the
        # previous cursor now rides every frame.
        wire = genesis.model_dump(mode="json", by_alias=True, exclude_none=True)
        self.assertEqual(wire["previousCursor"], self._origin(projector))
        # The rest of the chain links frame to frame with no hole.
        for earlier, later in pairwise(retained):
            assert later.previous_cursor is not None
            self.assertEqual(str(later.previous_cursor), str(earlier.cursor))

    async def test_genesis_gap_envelope_previous_cursor_is_event_cursor_zero(self) -> None:
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        queue = projector.subscribe()
        await projector._gap("generation-changed")
        gap = queue.get_nowait()
        assert isinstance(gap, ConversationEventEnvelope)
        self.assertEqual(gap.sequence, 1)
        assert isinstance(gap.mutation, GapMutation)
        assert gap.previous_cursor is not None
        self.assertEqual(str(gap.previous_cursor), self._origin(projector))
        self.assertEqual(str(gap.mutation.requested_after), self._origin(projector))
        self.assertIs(queue.get_nowait(), projector_module.CLOSE_SENTINEL)


class HydrationBaselineStatusTests(unittest.IsolatedAsyncioTestCase):
    """The page already delivers the hydration baseline.

    The first poll after a quiet boot must not re-emit the page's status
    revision: the re-emitted envelope carries recomputed derived freshness
    (observed_at/age_ms) at the SAME revision, which the browser reducer's
    same-revision/different-payload guard treats as a protocol fault — the
    measured deterministic re-page on every quiet fresh chat. The cursor chain
    itself was already whole (the live frame's previousCursor equals the page
    cursor); the redundant emission is what had to go.
    """

    async def test_quiet_first_poll_after_page_emits_no_status_frame(self) -> None:
        # The fresh-chat boot repro: page + subscribe, then a poll whose snapshot
        # is unchanged. Nothing may reach the subscriber — the page already
        # carried this exact status revision atomically with the resume cursor.
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        page = await projector.page(before_ordinal=None, limit=50)
        queue = projector.subscribe()
        await projector.poll_once()
        self.assertTrue(queue.empty())
        self.assertEqual(projector.retained_after(0), ())
        # The baseline is marked delivered: a later semantic change still emits.
        self.assertEqual(projector._coordinator.status_revision_emitted, page.status.revision)

    async def test_first_poll_emits_status_only_when_semantic_state_changes(self) -> None:
        # Boot-timing variant: the status moves between page and first poll. The
        # frame must carry a strictly newer revision (never a same-revision
        # re-emission) and chain straight onto the page cursor.
        bridge = _ScriptedBridge()
        projector = _projector(bridge)
        page = await projector.page(before_ordinal=None, limit=50)
        queue = projector.subscribe()
        bridge.snapshot = replace(
            bridge.snapshot, activity="running", raw={"activeTurnId": "turn-1"}
        )
        await projector.poll_once()
        frames: list[ConversationEventEnvelope] = []
        while not queue.empty():
            item = queue.get_nowait()
            assert isinstance(item, ConversationEventEnvelope)
            frames.append(item)
        self.assertEqual(len(frames), 1)
        frame = frames[0]
        self.assertEqual(frame.mutation.op, "status")
        assert isinstance(frame.mutation, StatusMutation)
        self.assertGreater(frame.mutation.status.revision, page.status.revision)
        assert frame.previous_cursor is not None
        self.assertEqual(str(frame.previous_cursor), str(page.event_cursor))


class OverflowGapTests(unittest.IsolatedAsyncioTestCase):
    """An overflowed consumer gets exactly one gap then close."""

    async def test_overflow_delivers_one_gap_then_close_and_no_sequence_hole(self) -> None:
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        with mock.patch.object(mutation_stream, "SUBSCRIBER_QUEUE_LIMIT", 4):
            queue = projector.subscribe()
            for index in range(6):
                bridge.push_evidence(
                    "codex-notification",
                    {
                        "threadId": "vendor-1",
                        "turnId": "turn-1",
                        "completedAtMs": index + 10,
                        "item": {
                            "id": f"extra-{index}",
                            "type": "agentMessage",
                            "text": f"extra {index}",
                        },
                    },
                )
            await projector.poll_once()
            received: list[object] = []
            while not queue.empty():
                received.append(queue.get_nowait())
            envelopes = [item for item in received if isinstance(item, ConversationEventEnvelope)]
            gaps = [item for item in envelopes if item.mutation.op == "gap"]
            self.assertEqual(len(gaps), 1)
            gap = gaps[0]
            assert isinstance(gap.mutation, GapMutation)
            self.assertEqual(gap.mutation.reason, "retention-overflow")
            self.assertTrue(gap.mutation.requires_repage)
            self.assertTrue(gap.mutation.close_after_event)
            self.assertIs(received[-1], projector_module.CLOSE_SENTINEL)
            self.assertLess(received.index(gap), len(received) - 1)
            sequences = [envelope.sequence for envelope in projector.retained_after(0)]
            self.assertEqual(sequences, list(range(1, len(sequences) + 1)))
            self.assertIn(gap.sequence, sequences)
            previous = gap.previous_cursor
            assert previous is not None
            self.assertNotEqual(str(previous), str(gap.cursor))


class ZipperEvictionGapTests(unittest.IsolatedAsyncioTestCase):
    """Evidence eviction gaps the echo zipper with ordering-fault."""

    async def test_eviction_after_hydration_raises_for_echo_harness(self) -> None:
        bridge = _ScriptedBridge(harness="claude")
        bridge.transcript_entries.append(
            {
                "sequence": 1,
                "role": "user",
                "text": "p1",
                "createdAt": NOW,
                "requestId": "req-1",
                "vendorCorrelationId": "uuid-user-1",
            }
        )
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-1",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            },
        )
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual([item.item_id for item in result.items], ["uuid-user-1", "uuid-agent-1"])
        bridge.evicted_before = 1
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-2",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a2"}]},
            },
        )
        with self.assertRaises(ZipperEvidenceEvicted):
            await projector.poll_once()

    async def test_eviction_does_not_gap_non_echo_harness(self) -> None:
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        bridge.evicted_before = 1
        bridge.push_evidence(
            "codex-notification",
            {
                "threadId": "vendor-1",
                "turnId": "turn-2",
                "completedAtMs": 20,
                "item": {"id": "extra-1", "type": "agentMessage", "text": "x"},
            },
        )
        await projector.poll_once()
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertIn("extra-1", [item.item_id for item in result.items])
        self.assertIsNone(result.total_items)

    async def test_fresh_projector_rehydrates_from_remaining_window(self) -> None:
        bridge = _ScriptedBridge(harness="claude")
        bridge.evicted_before = 2
        bridge.push_evidence(
            "state",
            {
                "type": "assistant",
                "uuid": "uuid-agent-1",
                "session_id": "vendor-1",
                "message": {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            },
        )
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual([item.item_id for item in result.items], ["uuid-agent-1"])
        self.assertIsNone(result.total_items)

    def _scripted_claude_turns(self, bridge: _ScriptedBridge, turns: range) -> None:
        for n in turns:
            bridge.transcript_entries.append(
                {
                    "sequence": n,
                    "role": "user",
                    "text": f"prompt {n}",
                    "createdAt": NOW,
                    "requestId": f"req-{n}",
                    "vendorCorrelationId": f"uuid-user-{n}",
                }
            )
            bridge.push_evidence(
                "state",
                {
                    "type": "assistant",
                    "uuid": f"uuid-agent-{n}",
                    "session_id": "vendor-1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": f"answer {n}"}],
                    },
                },
            )
            bridge.push_evidence(
                "completed",
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "uuid": f"uuid-result-{n}",
                },
            )

    async def test_rehydration_realigns_echoes_after_evidence_eviction(self) -> None:
        # After a dormancy release + re-hydration the
        # bridge's bounded evidence deque (2,000 frames) has prefix-evicted the oldest turn
        # bodies while the transcript deque (1,000 entries) still retains every submission
        # echo, including turns that were queued as follow-ups. The echo zipper paired
        # strictly positionally from sequence 0, so the first retained echoes zipped against
        # LATER turns' bodies — the developer's tail rebuilt with the last four answers
        # pinned four turns early and the four newest user messages orphaned at the bottom.
        # Both channels keep suffix order, so hydration must realign by turn count: the
        # leading echoes whose bodies were evicted project as bare user items, then each
        # assistant body follows its OWN user message and no tail user message orphans.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        # The retained evidence window holds only the last four turns' bodies.
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
                "uuid-result-8:result",
            ],
        )
        self.assertIsNone(result.total_items)

    async def test_rehydration_realigns_with_inflight_tail_turn(self) -> None:
        # Same evicted window, but the newest turn is still running at hydration: its body
        # has no result frame yet. The open body belongs to the LAST retained echo, so the
        # realignment must leave it paired instead of shifting the zip one turn further.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        # Retained: turns 5-7 complete plus turn 8's assistant frame (no result yet).
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:15]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
            ],
        )

    async def test_rehydration_realigns_echoless_leading_bodies(self) -> None:
        # The mirror asymmetry: the transcript window evicted the oldest echoes while the
        # evidence window still carries their turn bodies. Those leading bodies belong to
        # turns whose user messages are gone; they project echo-less, in order, and the
        # retained echoes zip against the LAST retained bodies — never the first ones.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        bridge.evicted_before = 4
        bridge.evidence_frames = bridge.evidence_frames[4:]
        # Only the last two turns' echoes survived in the transcript window.
        bridge.transcript_entries = bridge.transcript_entries[6:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-agent-3",
                "uuid-result-3:result",
                "uuid-agent-4",
                "uuid-result-4:result",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
                "uuid-result-8:result",
            ],
        )

    async def test_rehydration_ignores_settled_turn_close_frames(self) -> None:
        # The live 2.1.218 wire closes every slash-command turn with ONE post-result
        # command_lifecycle/completed frame. When that close frame trails the retained
        # window it belongs to the SETTLED turn, not to a phantom in-flight one: counting
        # it as an open body would flush one leading body echo-less and shift the whole
        # realigned zip one turn early.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        bridge.push_evidence(
            "state",
            {"type": "command_lifecycle", "command_uuid": "cmd-8", "state": "completed"},
        )
        # Retained: turns 5-8 complete plus turn 8's post-result lifecycle close frame.
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
                "uuid-agent-8",
                "uuid-result-8:result",
            ],
        )

    async def test_rehydration_ignores_non_turn_trailing_frames(self) -> None:
        # `_opens_echo_turn_body` must be an ALLOWLIST of frames the live wire
        # proves are in-turn, not a denylist of the two shapes this round happened to see.
        # Anything else trailing the last retained result inflates the body count by one and
        # shifts the ENTIRE realigned tail one turn early — every answer above the user
        # message that produced it, the newest user message orphaned answerless: the exact
        # ghost the realignment exists to eliminate.
        #
        # The repo-provable trigger is the interrupt race: `_handle_result`
        # (claude_stream_state.py) pops the pending control entry when the turn settles, so an
        # interrupt control_response that lost the race to natural completion arrives
        # unmatched and is diverted to evidence AFTER that turn's result. Rate-limit telemetry
        # and any frame type the mapper has never seen trail results the same way.
        trailing_shapes: list[tuple[str, dict]] = [
            # Unmatched interrupt answer (shape from the 2.1.217 interrupt fixture).
            (
                "control_response",
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "success",
                        "request_id": "ar-claude-interrupt-1",
                        "response": {"still_queued": []},
                    },
                },
            ),
            # Telemetry that rides outside turns entirely.
            (
                "rate_limit_event",
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "allowed", "rateLimitType": "five_hour"},
                    "session_id": "vendor-1",
                },
            ),
            # A shape no mapper version has seen: unknown is not proof of an open body.
            ("undocumented", {"type": "ar-test-undocumented-frame", "session_id": "vendor-1"}),
        ]
        for label, raw in trailing_shapes:
            with self.subTest(trailing=label):
                bridge = _ScriptedBridge(harness="claude")
                self._scripted_claude_turns(bridge, range(1, 9))
                bridge.push_evidence("state", raw)
                # Retained: turns 5-8 complete plus the one non-turn trailing frame.
                bridge.evicted_before = 8
                bridge.evidence_frames = bridge.evidence_frames[8:]
                projector = _projector(bridge, harness="claude")
                result = await projector.page(before_ordinal=None, limit=50)
                self.assertEqual(
                    [item.item_id for item in result.items],
                    [
                        "uuid-user-1",
                        "uuid-user-2",
                        "uuid-user-3",
                        "uuid-user-4",
                        "uuid-user-5",
                        "uuid-agent-5",
                        "uuid-result-5:result",
                        "uuid-user-6",
                        "uuid-agent-6",
                        "uuid-result-6:result",
                        "uuid-user-7",
                        "uuid-agent-7",
                        "uuid-result-7:result",
                        "uuid-user-8",
                        "uuid-agent-8",
                        "uuid-result-8:result",
                    ],
                )

    async def test_rehydration_pairs_lifecycle_only_inflight_turn(self) -> None:
        # The in-flight mirror: the newest turn has only lifecycle frames yet (a queued
        # follow-up just starting, no assistant content and no result). Its echo is the
        # LAST retained one and must stay the open turn — not orphan as a bare item — so
        # the answer frames arriving after hydration land under their own user message.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 9))
        bridge.push_evidence(
            "state",
            {"type": "command_lifecycle", "command_uuid": "cmd-8", "state": "queued"},
        )
        bridge.push_evidence(
            "state",
            {"type": "command_lifecycle", "command_uuid": "cmd-8", "state": "started"},
        )
        # Retained: turns 5-7 complete; turn 8 has only its two lifecycle start frames.
        bridge.evicted_before = 8
        bridge.evidence_frames = bridge.evidence_frames[8:14] + bridge.evidence_frames[16:18]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in result.items],
            [
                "uuid-user-1",
                "uuid-user-2",
                "uuid-user-3",
                "uuid-user-4",
                "uuid-user-5",
                "uuid-agent-5",
                "uuid-result-5:result",
                "uuid-user-6",
                "uuid-agent-6",
                "uuid-result-6:result",
                "uuid-user-7",
                "uuid-agent-7",
                "uuid-result-7:result",
                "uuid-user-8",
            ],
        )
        # Turn 8's content crosses after hydration: it must land under its own echo.
        bridge.evidence_frames.append(
            EvidenceFrame(
                sequence=19,
                kind="state",
                created_at=NOW,
                raw={
                    "type": "assistant",
                    "uuid": "uuid-agent-8",
                    "session_id": "vendor-1",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "answer 8"}],
                    },
                },
            )
        )
        bridge.evidence_frames.append(
            EvidenceFrame(
                sequence=20,
                kind="completed",
                created_at=NOW,
                raw={
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "uuid": "uuid-result-8",
                },
            )
        )
        await projector.poll_once()
        continued = await projector.page(before_ordinal=None, limit=50)
        self.assertEqual(
            [item.item_id for item in continued.items][-3:],
            ["uuid-user-8", "uuid-agent-8", "uuid-result-8:result"],
        )

    async def test_rehydration_realigns_across_transcript_pages(self) -> None:
        # A genuinely long conversation retains more echoes than one 500-entry transcript
        # page: the realignment must count every retained echo (hydration drains the
        # window), or it undercounts the orphans and re-introduces the constant-offset
        # mis-zip it exists to fix — here 600 echoes against the last three turn bodies.
        bridge = _ScriptedBridge(harness="claude")
        self._scripted_claude_turns(bridge, range(1, 601))
        bridge.evicted_before = 1194
        bridge.evidence_frames = bridge.evidence_frames[1194:]
        projector = _projector(bridge, harness="claude")
        result = await projector.page(before_ordinal=None, limit=700)
        ids = [item.item_id for item in result.items]
        self.assertEqual(len(ids), 606)
        self.assertEqual(ids[:4], ["uuid-user-1", "uuid-user-2", "uuid-user-3", "uuid-user-4"])
        self.assertEqual(
            ids[-9:],
            [
                "uuid-user-598",
                "uuid-agent-598",
                "uuid-result-598:result",
                "uuid-user-599",
                "uuid-agent-599",
                "uuid-result-599:result",
                "uuid-user-600",
                "uuid-agent-600",
                "uuid-result-600:result",
            ],
        )
        # The 597 evicted-body echoes project bare, in order, before the paired tail.
        self.assertEqual(ids[596], "uuid-user-597")
        self.assertEqual(ids[597], "uuid-user-598")


class DormantReleaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_dormant_state_frees_heavy_projection_and_retires_shell(self) -> None:
        # A dormant projector must release its full ProjectionStore, the
        # live-turn/request id-sets, and retained envelopes instead of lingering as a registered
        # tombstone until 32-LRU eviction. After release the shell is retired (matches() is False),
        # so the next access re-creates a fresh projector.
        bridge = _ScriptedBridge(harness="codex")
        _codex_turn(bridge, "turn-1")
        _codex_turn(bridge, "turn-2")
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        self.assertGreater(len(result.items), 0)
        self.assertGreater(len(projector._native.live_turn_ids), 0)

        projector._release_dormant_state()

        self.assertTrue(projector._closed)
        self.assertEqual(
            projector._stream.store.page(before_ordinal=None, limit=50, total_known=True).items,
            (),
        )
        self.assertEqual(projector._native.live_turn_ids, {})
        self.assertEqual(projector._native.live_request_ids, {})
        self.assertEqual(projector._stream.retention, [])
        # The retired shell no longer matches its identity, so a re-access re-creates it.
        self.assertFalse(projector.matches(_identity("codex")))


if __name__ == "__main__":
    unittest.main()
