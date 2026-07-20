"""Projector engine: hydration, ordering, idempotence, provenance, rehydration, gaps."""

from __future__ import annotations

import unittest
from collections.abc import Mapping
from unittest import mock

import agents_remember.serving.conversation.active.projector as projector_module
from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.conversation.active.projector import (
    ActiveSessionProjector,
    ZipperEvidenceEvicted,
)
from agents_remember.serving.conversation.active.store import ProjectionStore
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    AuthorizationBinding,
    ConversationEventEnvelope,
    GapMutation,
)
from agents_remember.serving.conversation.projectors import claude, pi, projector_for
from agents_remember.serving.conversation.projectors.codex import map_native_frame
from agents_remember.serving.conversation.projectors.common import MappedItem
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
            identity=ControlIdentity(
                ar_session_id="ar-1", tmux_name="ar-t-1", created_at=NOW
            ),
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

    def read_native_page(self, entry, *, cursor=None, limit=200, byte_budget=None, expected_bridge_epoch=None):
        del entry, byte_budget, expected_bridge_epoch
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
            entry_
            for entry_ in self.transcript_entries
            if _entry_sequence(entry_) > after_sequence
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
        identity=_identity(harness),
        authorization=_authorization(),
        entry=_ControlledEntry(),
        mapper=mapper,
        secret=SECRET,
        clock=lambda: NOW,
        evidence_reader=bridge.read_evidence,
        native_page_reader=bridge.read_native_page,
        transcript_reader=bridge.read_transcript,
        provenance_reader=bridge.read_provenance,
        snapshot_reader=bridge.read_snapshot,
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
        self.assertEqual(
            [item.global_ordinal for item in result.items], [1, 2, 3]
        )
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
        """260718-CHATS-L5 H2 (L4 verdict E2): the L1 projector must never emit an item that

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

        # Every item the L1 projector serves must re-validate as the route response would.
        for item in result.items:
            ConversationItem.model_validate(item.model_dump(by_alias=True))
        remapped = {item.item_id: item for item in result.items}["turn-1-user"]
        self.assertEqual(remapped.lane, "operator", "a re-map must not revert the resolved lane")
        self.assertEqual(remapped.source, "cockpit-composer")
        self.assertEqual(remapped.provenance.strength, "exact")

    async def test_settled_live_turns_project_once_when_native_ids_disjoint(self) -> None:
        """260718-CHATS-L5 F1 (L4 verdict blocker): a settled codex turn must project EXACTLY once.

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
                "turn-1-user", "turn-1-agent", "turn-result:turn-1",
                "turn-2-user", "turn-2-agent", "turn-result:turn-2",
            ],
        )
        # The hosted thread now persists the SAME settled turns through thread/read under DISJOINT
        # positional item ids, grouped under the live turn ids.
        bridge.native_frames.extend(
            [
                NativeEvidenceFrame(
                    native_id="item-1", native_parent_id="turn-1", native_type="userMessage",
                    created_at=NOW,
                    raw={
                        "id": "item-1", "type": "userMessage", "clientId": "req-turn-1",
                        "content": [{"type": "text", "text": "go"}],
                    },
                ),
                NativeEvidenceFrame(
                    native_id="item-2", native_parent_id="turn-1", native_type="agentMessage",
                    created_at=NOW,
                    raw={"id": "item-2", "type": "agentMessage", "text": "working more"},
                ),
                NativeEvidenceFrame(
                    native_id="item-3", native_parent_id="turn-2", native_type="userMessage",
                    created_at=NOW,
                    raw={
                        "id": "item-3", "type": "userMessage", "clientId": "req-turn-2",
                        "content": [{"type": "text", "text": "go"}],
                    },
                ),
                NativeEvidenceFrame(
                    native_id="item-4", native_parent_id="turn-2", native_type="agentMessage",
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
        """260718-CHATS-L5 F1: single projection holds even if the hosted thread RENUMBERS turn ids.

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
                    native_id="item-1", native_parent_id="hist-turn-a", native_type="userMessage",
                    created_at=NOW,
                    raw={
                        "id": "item-1", "type": "userMessage", "clientId": "req-turn-1",
                        "content": [{"type": "text", "text": "go"}],
                    },
                ),
                NativeEvidenceFrame(
                    native_id="item-2", native_parent_id="hist-turn-a", native_type="agentMessage",
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
        self.assertEqual(settled_ids, live_ids, "clientId must anchor the whole renumbered native turn")
        self.assertNotIn("item-1", settled_ids)
        self.assertNotIn("item-2", settled_ids)

    async def test_prior_session_native_history_survives_live_turns(self) -> None:
        """260718-CHATS-L5 F1 guard: the suppression must never drop GENUINE prior-session history.

        A resumed thread's earlier turns (never observed live in this projector) must hydrate in full;
        only turns this run settled live are suppressed from the native re-walk.
        """
        bridge = _ScriptedBridge()
        bridge.native_frames.append(
            NativeEvidenceFrame(
                native_id="hist-1", native_parent_id="turn-0", native_type="agentMessage",
                created_at=NOW,
                raw={"id": "hist-1", "type": "agentMessage", "text": "earlier session"},
            )
        )
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        result = await projector.page(before_ordinal=None, limit=50)
        ids = [item.item_id for item in result.items]
        self.assertEqual(
            ids, ["hist-1", "turn-1-user", "turn-1-agent", "turn-result:turn-1"]
        )

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
                "message": {"role": "assistant", "content": [{"type": "text", "text": f"answer {n}"}]},
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
            ["uuid-user-1", "uuid-agent-1", "uuid-result-1:result", "uuid-user-2", "uuid-agent-2", "uuid-result-2:result"],
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
        self.assertEqual(
            [item.item_id for item in result2.items], ["entry-1", "entry-2"]
        )
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
    """F1 fix pins: partial-block tool items converge by scoped block-union."""

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
                            {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "ls -la"}}
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
                            {"type": "tool_result", "tool_use_id": "toolu_1", "content": [{"type": "text", "text": "total 0"}], "is_error": False}
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

    def test_pi_live_start_update_end_keeps_input_through_every_step(self) -> None:
        store = ProjectionStore()
        start = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=1,
                kind="pi:tool_execution_start",
                created_at=NOW,
                raw={"type": "tool_execution_start", "toolCallId": "tc-1", "toolName": "bash", "args": {"command": "ls"}},
            ),
            evidence_ref="ref-1",
        )
        update = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=2,
                kind="pi:tool_execution_update",
                created_at=NOW,
                raw={"type": "tool_execution_update", "toolCallId": "tc-1", "toolName": "bash", "args": {"command": "ls"}, "partialResult": {"content": [{"type": "text", "text": "part"}]}},
            ),
            evidence_ref="ref-2",
        )
        end = pi.map_evidence_frame(
            EvidenceFrame(
                sequence=3,
                kind="pi:tool_execution_end",
                created_at=NOW,
                raw={"type": "tool_execution_end", "toolCallId": "tc-1", "toolName": "bash", "result": {"content": [{"type": "text", "text": "done"}]}, "isError": False},
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
                            {"type": "toolCall", "id": "tc-9", "name": "bash", "arguments": {"command": "ls"}}
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


class OverflowGapTests(unittest.IsolatedAsyncioTestCase):
    """F2 fix pins: overflowed consumer gets exactly one gap then close."""

    async def test_overflow_delivers_one_gap_then_close_and_no_sequence_hole(self) -> None:
        bridge = _ScriptedBridge()
        _codex_turn(bridge, "turn-1")
        projector = _projector(bridge)
        await projector.page(before_ordinal=None, limit=50)
        with mock.patch.object(projector_module, "SUBSCRIBER_QUEUE_LIMIT", 4):
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
            envelopes = [
                item for item in received if isinstance(item, ConversationEventEnvelope)
            ]
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
    """F3 fix pins: evidence eviction gaps the echo zipper with ordering-fault."""

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


if __name__ == "__main__":
    unittest.main()
