"""Projection-lift forcing tests: terminal evidence lands on the catalog seat truth.

The lift must make done != interrupted at seat granularity, keep killed seats
``exited`` (never "done"), keep hung seats ``stale``, and carry interrupt origin
provenance from the dashboard interrupt stamp.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

from agents_remember.errors import HarnessControlError
from agents_remember.serving.conversation.active.status import TurnTerminalEvidence
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    EvidenceFrame,
    EvidencePage,
    NativeEvidenceFrame,
    NativeEvidencePage,
)
from agents_remember.serving.hosted_control_projection import snapshot_turn_state
from agents_remember.serving.seat_turn_truth import (
    record_interrupt_request,
    record_non_reaction_emitted,
    record_state_signal_emitted,
    record_terminal_cursors,
    record_turn_projection,
)
from agents_remember.serving.terminal_catalog import (
    CatalogTurnEvidence,
    TerminalCatalog,
    TerminalCatalogEntry,
    seat_at_turn_boundary,
)
from agents_remember.serving.terminal_evidence import (
    MAX_NATIVE_LIFT_PAGES,
    TerminalEvidenceProjection,
    TerminalEvidenceRead,
    interrupted_origin,
    latest_native_terminal_evidence,
    latest_terminal_evidence,
    read_entry_terminal_evidence,
)
from agents_remember.serving.terminal_liveness import LivenessProbe, observe_terminal_liveness
from agents_remember.serving.terminal_tmux import TmuxProbeResult

NOW = datetime(2026, 7, 13, 15, 41, 0, tzinfo=UTC)


def _entry(session_id: str, **overrides: object) -> TerminalCatalogEntry:
    fields: dict[str, object] = dict(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-07-13T00:00:00+00:00",
        last_attached_at="2026-07-13T00:00:00+00:00",
        status="running",
        control_state="ready",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
    )
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)  # type: ignore[arg-type]


def _snapshot(
    *,
    control: str = "ready",
    activity: str = "idle",
    sequence: int = 10,
) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(
            ar_session_id="worker-1",
            tmux_name="ar-worker-1",
            created_at="2026-07-13T00:00:00+00:00",
        ),
        control=control,  # type: ignore[arg-type]
        activity=activity,  # type: ignore[arg-type]
        acceptance="immediate",
        last_event_sequence=sequence,
        raw={},
    )


def _codex_frame(status: str, *, sequence: int, turn_id: str = "turn-9") -> EvidenceFrame:
    return EvidenceFrame(
        sequence=sequence,
        kind="completed",
        created_at="2026-07-13T15:40:00+00:00",
        raw={
            "turn": {
                "id": turn_id,
                "status": status,
                "items": [],
                "completedAt": "2026-07-13T15:40:00+00:00",
            }
        },
    )


def _pi_native_frame(
    entry_id: str,
    *,
    stop_reason: str | None = "aborted",
    created_at: str = "2026-07-13T15:40:00+00:00",
) -> NativeEvidenceFrame:
    message: dict[str, object] = {
        "role": "assistant",
        "content": [{"type": "text", "text": "partial"}],
    }
    if stop_reason is not None:
        message["stopReason"] = stop_reason
    return NativeEvidenceFrame(
        native_id=entry_id,
        native_parent_id=None,
        native_type="message",
        created_at=created_at,
        raw={
            "id": entry_id,
            "type": "message",
            "message": message,
        },
    )


def _page(*frames: EvidenceFrame) -> EvidencePage:
    return EvidencePage(
        frames=tuple(frames),
        latest_sequence=max((frame.sequence for frame in frames), default=0),
        evicted_before_sequence=0,
        truncated=False,
        bridge_epoch="epoch-1",
    )


class _FakeHost:
    def has_session(self, tmux_name: str) -> bool:  # noqa: ARG002
        return True

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:  # noqa: ARG002
        return TmuxProbeResult(exists=True, evidence="alive")

    def get(self, _sid: str) -> None:
        return None


def _observe_once(
    catalog: TerminalCatalog,
    entry: TerminalCatalogEntry,
    *,
    terminal: TerminalEvidenceProjection | None = None,
    snapshot: AdapterSnapshot | None = None,
    checked_at: datetime = NOW,
) -> TerminalCatalogEntry:
    probe = LivenessProbe(
        snapshot_reader=lambda _entry: snapshot or _snapshot(),
        terminal_reader=lambda _entry: TerminalEvidenceRead(projection=terminal),
    )
    observation = observe_terminal_liveness(
        catalog,
        _FakeHost(),
        entry,
        checked_at=checked_at,
        probe=probe,
    )
    return observation.entry


class LatestTerminalEvidenceTests(unittest.TestCase):
    def test_codex_completed_frame_lifts_outcome_and_turn_id(self) -> None:
        projection = latest_terminal_evidence(_page(_codex_frame("completed", sequence=7)), "codex")
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "completed")
        self.assertEqual(projection.evidence.turn_id, "turn-9")
        self.assertEqual(projection.evidence_id, "turn-9")

    def test_codex_interrupted_frame_maps_to_interrupted(self) -> None:
        projection = latest_terminal_evidence(
            _page(_codex_frame("interrupted", sequence=7)), "codex"
        )
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "interrupted")

    def test_pi_aborted_frame_maps_to_interrupted_without_turn_id(self) -> None:
        page = NativeEvidencePage(
            frames=(
                NativeEvidenceFrame(
                    native_id="entry-8",
                    native_parent_id=None,
                    native_type="message",
                    created_at="2026-07-13T15:40:00+00:00",
                    raw={
                        "id": "entry-8",
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "partial"}],
                            "stopReason": "aborted",
                        },
                    },
                ),
            ),
            next_cursor=None,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        projection = latest_native_terminal_evidence(page, "pi")
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "interrupted")
        self.assertIsNone(projection.evidence.turn_id)
        self.assertEqual(projection.evidence_id, "native:entry-8")

    def test_latest_terminal_evidence_wins_across_frames(self) -> None:
        page = _page(
            _codex_frame("completed", sequence=6, turn_id="turn-8"),
            _codex_frame("interrupted", sequence=7),
        )
        projection = latest_terminal_evidence(page, "codex")
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "interrupted")
        self.assertEqual(projection.evidence_id, "turn-9")

    def test_empty_page_yields_no_evidence(self) -> None:
        self.assertIsNone(latest_terminal_evidence(_page(), "codex"))

    def test_unknown_harness_or_projector_yields_no_evidence(self) -> None:
        page = _page(_codex_frame("completed", sequence=7))
        self.assertIsNone(latest_terminal_evidence(page, None))
        self.assertIsNone(latest_terminal_evidence(page, "bogus"))
        native = NativeEvidencePage(
            frames=(),
            next_cursor=None,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        self.assertIsNone(latest_native_terminal_evidence(native, None))
        self.assertIsNone(latest_native_terminal_evidence(native, "bogus"))

    def test_native_mapping_skips_unmappable_frames(self) -> None:
        page = NativeEvidencePage(
            frames=(
                NativeEvidenceFrame(
                    native_id="bad-1",
                    native_parent_id=None,
                    native_type="message",
                    created_at="2026-07-13T15:39:00+00:00",
                    raw={"id": "bad-1", "type": "message"},
                ),
                NativeEvidenceFrame(
                    native_id="entry-8",
                    native_parent_id=None,
                    native_type="message",
                    created_at="2026-07-13T15:40:00+00:00",
                    raw={
                        "id": "entry-8",
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "partial"}],
                            "stopReason": "aborted",
                        },
                    },
                ),
            ),
            next_cursor=None,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        projection = latest_native_terminal_evidence(page, "pi")
        assert projection is not None
        self.assertEqual(projection.evidence.outcome, "interrupted")


class ReadEntryTerminalEvidenceTests(unittest.TestCase):
    def test_non_harness_or_endpointless_rows_return_none(self) -> None:
        for entry in (
            _entry("plain", kind="terminal"),
            replace(_entry("w"), harness=None),
            replace(_entry("w"), control_endpoint=None),
        ):
            read = read_entry_terminal_evidence(entry)
            self.assertIsNone(read.projection)
            self.assertIsNone(read.evidence_sequence)
            self.assertIsNone(read.native_cursor)

    def test_codex_row_reads_evidence_page(self) -> None:
        entry = _entry("worker-1", turn_state="working")
        with mock.patch(
            "agents_remember.serving.terminal_evidence.read_control_evidence",
            return_value=_page(_codex_frame("completed", sequence=7)),
        ) as reader:
            read = read_entry_terminal_evidence(entry)
        reader.assert_called_once()
        assert read.projection is not None
        self.assertEqual(read.projection.evidence_id, "turn-9")
        self.assertEqual(read.evidence_sequence, 7)

    def test_pi_row_reads_native_page(self) -> None:
        entry = replace(
            _entry("worker-1", turn_state="working"),
            harness="pi",
        )
        native = NativeEvidencePage(
            frames=(
                NativeEvidenceFrame(
                    native_id="entry-8",
                    native_parent_id=None,
                    native_type="message",
                    created_at="2026-07-13T15:40:00+00:00",
                    raw={
                        "id": "entry-8",
                        "type": "message",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "partial"}],
                            "stopReason": "aborted",
                        },
                    },
                ),
            ),
            next_cursor=None,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            return_value=native,
        ) as reader:
            read = read_entry_terminal_evidence(entry)
        reader.assert_called_once()
        assert read.projection is not None
        self.assertEqual(read.projection.evidence_id, "native:entry-8")
        self.assertEqual(read.native_cursor, "entry-8")

    def test_pi_read_walks_to_the_tail_beyond_one_page(self) -> None:
        entry = replace(
            _entry("worker-1", turn_state="working"),
            harness="pi",
        )
        page1 = NativeEvidencePage(
            frames=tuple(
                _pi_native_frame(
                    f"entry-{index}",
                    stop_reason="stop",
                    created_at=f"2026-07-13T15:{index // 60:02d}:{index % 60:02d}+00:00",
                )
                for index in range(1, 201)
            ),
            next_cursor="entry-200",
            truncated=True,
            bridge_epoch="epoch-1",
        )
        page2 = NativeEvidencePage(
            frames=(
                *(
                    _pi_native_frame(
                        f"entry-{index}",
                        stop_reason=(
                            "aborted" if index == 240 else ("stop" if index <= 239 else None)
                        ),
                        created_at=f"2026-07-13T15:{index // 60:02d}:{index % 60:02d}+00:00",
                    )
                    for index in range(201, 251)
                ),
                NativeEvidenceFrame(
                    native_id="entry-251",
                    native_parent_id=None,
                    native_type="compaction",
                    created_at="2026-07-13T15:05:00+00:00",
                    raw={"id": "entry-251", "type": "compaction", "summary": "compacted"},
                ),
            ),
            next_cursor=None,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            side_effect=[page1, page2],
        ) as reader:
            read = read_entry_terminal_evidence(entry)
        self.assertEqual(reader.call_count, 2)
        assert read.projection is not None
        self.assertEqual(read.projection.evidence_id, "native:entry-240")
        self.assertEqual(read.native_cursor, "entry-251")

    def test_pi_read_tracks_forward_from_the_persisted_cursor(self) -> None:
        entry = replace(
            _entry("worker-1", turn_state="working"),
            harness="pi",
            terminal_native_cursor="entry-251",
        )
        page = NativeEvidencePage(
            frames=(
                _pi_native_frame("entry-252", stop_reason="stop"),
                _pi_native_frame("entry-261", stop_reason="aborted"),
            ),
            next_cursor=None,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            return_value=page,
        ) as reader:
            read = read_entry_terminal_evidence(entry)
        reader.assert_called_once()
        assert read.projection is not None
        self.assertEqual(read.projection.evidence_id, "native:entry-261")
        self.assertEqual(read.native_cursor, "entry-261")

    def test_pi_read_empty_page_does_not_advance(self) -> None:
        entry = replace(
            _entry("worker-1", turn_state="working"),
            harness="pi",
            terminal_native_cursor="entry-250",
        )
        page = NativeEvidencePage(
            frames=(),
            next_cursor=None,
            truncated=False,
            bridge_epoch="epoch-1",
        )
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            return_value=page,
        ) as reader:
            read = read_entry_terminal_evidence(entry)
        reader.assert_called_once()
        self.assertIsNone(read.projection)
        self.assertEqual(read.native_cursor, "entry-250")

    def test_pi_read_page_walk_is_bounded(self) -> None:
        entry = replace(
            _entry("worker-1", turn_state="working"),
            harness="pi",
        )
        pages = [
            NativeEvidencePage(
                frames=(
                    _pi_native_frame(
                        f"entry-{index}",
                        stop_reason=None,
                        created_at=f"2026-07-13T15:{index // 60:02d}:{index % 60:02d}+00:00",
                    ),
                ),
                next_cursor=f"entry-{index}",
                truncated=True,
                bridge_epoch="epoch-1",
            )
            for index in range(1, MAX_NATIVE_LIFT_PAGES + 1)
        ]
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            side_effect=pages,
        ) as reader:
            read = read_entry_terminal_evidence(entry)
        self.assertEqual(reader.call_count, MAX_NATIVE_LIFT_PAGES)
        self.assertIsNone(read.projection)
        self.assertEqual(read.native_cursor, f"entry-{MAX_NATIVE_LIFT_PAGES}")


class CatalogSeatTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog = TerminalCatalog(Path(self.tmp.name) / "catalog.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_completed_settles_then_claims_turn_ended_not_done(self) -> None:
        entry = _entry("worker-1", turn_state="working")
        self.catalog.upsert(entry)
        first = _observe_once(
            self.catalog,
            entry,
            terminal=TerminalEvidenceProjection(
                evidence=TurnTerminalEvidence(outcome="completed", turn_id="turn-9"),
                evidence_id="turn-9",
                observed_at="2026-07-13T15:40:00+00:00",
            ),
        )
        self.assertEqual(first.terminal_outcome, "completed")
        self.assertEqual(first.terminal_evidence_id, "turn-9")
        self.assertEqual(first.state_signal_emitted_for, None)
        # The canonical settle step claims working for the completed observation...
        self.assertEqual(first.turn_state, "working")
        # ...and the next idle snapshot claims turn-ended with the outcome preserved.
        second = _observe_once(self.catalog, first, terminal=None)
        self.assertEqual(second.turn_state, "turn-ended")
        self.assertEqual(second.terminal_outcome, "completed")

    def test_interrupted_is_turn_ended_immediately_and_never_completed(self) -> None:
        entry = _entry("worker-1", turn_state="working")
        self.catalog.upsert(entry)
        observed = _observe_once(
            self.catalog,
            entry,
            terminal=TerminalEvidenceProjection(
                evidence=TurnTerminalEvidence(outcome="interrupted", turn_id="turn-9"),
                evidence_id="turn-9",
                observed_at="2026-07-13T15:40:00+00:00",
            ),
        )
        self.assertEqual(observed.turn_state, "turn-ended")
        self.assertEqual(observed.terminal_outcome, "interrupted")
        self.assertEqual(observed.interrupted_by, "unknown")

    def test_developer_interrupt_stamp_attributes_origin(self) -> None:
        entry = _entry(
            "worker-1",
            turn_state="working",
            interrupt_requested_by="developer",
            interrupt_requested_at="2026-07-13T15:39:00+00:00",
            interrupt_requested_turn_id="turn-9",
        )
        observed = _observe_once(
            self.catalog,
            entry,
            terminal=TerminalEvidenceProjection(
                evidence=TurnTerminalEvidence(outcome="interrupted", turn_id="turn-9"),
                evidence_id="turn-9",
                observed_at="2026-07-13T15:40:00+00:00",
            ),
        )
        self.assertEqual(observed.interrupted_by, "developer")

    def test_interrupt_stamp_for_another_turn_stays_unknown(self) -> None:
        entry = _entry(
            "worker-1",
            turn_state="working",
            interrupt_requested_by="developer",
            interrupt_requested_at="2026-07-13T15:39:00+00:00",
            interrupt_requested_turn_id="turn-8",
        )
        observed = _observe_once(
            self.catalog,
            entry,
            terminal=TerminalEvidenceProjection(
                evidence=TurnTerminalEvidence(outcome="interrupted", turn_id="turn-9"),
                evidence_id="turn-9",
                observed_at="2026-07-13T15:40:00+00:00",
            ),
        )
        self.assertEqual(observed.interrupted_by, "unknown")

    def test_killed_seat_stays_exited(self) -> None:
        entry = _entry("worker-1", status="exited", terminal_outcome="completed")
        self.catalog.upsert(entry)
        # A killed seat is never at a turn boundary and no relay may claim it done.
        self.assertFalse(seat_at_turn_boundary(entry))

    def test_hung_seat_stays_stale_and_holds_boundary(self) -> None:
        entry = _entry("worker-1", turn_state="stale", turn_state_changed_at=NOW.isoformat())
        self.assertFalse(seat_at_turn_boundary(entry))

    def test_transient_terminal_read_failure_retries_the_same_window(self) -> None:
        entry = _entry("worker-1", turn_state="working")
        self.catalog.upsert(entry)
        calls = {"count": 0}

        def reader(_entry: TerminalCatalogEntry) -> TerminalEvidenceRead:
            calls["count"] += 1
            if calls["count"] == 1:
                raise HarnessControlError("transient evidence read failure")
            return TerminalEvidenceRead(
                projection=TerminalEvidenceProjection(
                    evidence=TurnTerminalEvidence(outcome="completed", turn_id="turn-9"),
                    evidence_id="turn-9",
                    observed_at="2026-07-13T15:40:00+00:00",
                ),
                evidence_sequence=20,
            )

        probe = LivenessProbe(
            snapshot_reader=lambda _entry: _snapshot(sequence=20),
            terminal_reader=reader,
        )
        first = observe_terminal_liveness(
            self.catalog,
            _FakeHost(),
            entry,
            checked_at=NOW,
            probe=probe,
        ).entry
        self.assertIsNone(first.terminal_outcome)
        self.assertIsNone(first.terminal_evidence_sequence)

        second = observe_terminal_liveness(
            self.catalog,
            _FakeHost(),
            first,
            checked_at=NOW + timedelta(seconds=10),
            probe=probe,
        ).entry
        self.assertEqual(second.terminal_outcome, "completed")
        self.assertEqual(second.terminal_evidence_id, "turn-9")
        self.assertEqual(second.terminal_evidence_sequence, 20)

    def test_boundary_vocabulary(self) -> None:
        self.assertTrue(seat_at_turn_boundary(_entry("m", turn_state="turn-ended")))
        self.assertTrue(seat_at_turn_boundary(_entry("m", turn_state="awaiting-input")))
        self.assertTrue(seat_at_turn_boundary(_entry("m", turn_state=None)))
        self.assertFalse(seat_at_turn_boundary(_entry("m", turn_state="working")))
        self.assertFalse(seat_at_turn_boundary(_entry("m", turn_state="stale")))
        self.assertFalse(seat_at_turn_boundary(_entry("m", status="exited")))


class OriginResolutionTests(unittest.TestCase):
    def test_interrupted_origin_resolution(self) -> None:
        terminal = TurnTerminalEvidence(outcome="interrupted", turn_id="turn-9")
        stamped = _entry(
            "w",
            interrupt_requested_by="developer",
            interrupt_requested_turn_id="turn-9",
        )
        self.assertEqual(interrupted_origin(stamped, terminal), "developer")
        unstamped = _entry("w")
        self.assertEqual(interrupted_origin(unstamped, terminal), "unknown")
        mismatch = _entry(
            "w",
            interrupt_requested_by="developer",
            interrupt_requested_turn_id="turn-8",
        )
        self.assertEqual(interrupted_origin(mismatch, terminal), "unknown")
        no_turn_id = TurnTerminalEvidence(outcome="interrupted", turn_id=None)
        self.assertEqual(interrupted_origin(stamped, no_turn_id), "developer")
        completed = TurnTerminalEvidence(outcome="completed", turn_id="turn-9")
        self.assertIsNone(interrupted_origin(stamped, completed))
        self.assertIsNone(interrupted_origin(stamped, None))


class SeatTurnTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.catalog = TerminalCatalog(Path(self.tmp.name) / "catalog.json")

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_record_turn_projection_missing_row_returns_none(self) -> None:
        stamp = CatalogTurnEvidence(state="turn-ended", changed_at=NOW.isoformat())
        self.assertIsNone(record_turn_projection(self.catalog, "missing", stamp))

    def test_record_turn_projection_same_state_is_a_noop_write(self) -> None:
        entry = _entry(
            "w",
            turn_state="turn-ended",
            terminal_outcome="completed",
            terminal_outcome_at="2026-07-13T15:40:00+00:00",
            terminal_evidence_id="turn-9",
        )
        self.catalog.upsert(entry)
        stamp = CatalogTurnEvidence(
            state="turn-ended",
            changed_at=NOW.isoformat(),
            terminal_outcome="completed",
            terminal_outcome_at="2026-07-13T15:40:00+00:00",
            terminal_evidence_id="turn-9",
        )
        updated = record_turn_projection(self.catalog, "w", stamp)
        assert updated is not None
        stored = self.catalog.get("w")
        assert stored is not None
        self.assertEqual(updated, stored)
        self.assertEqual(stored.turn_state, "turn-ended")
        self.assertEqual(stored.terminal_evidence_id, "turn-9")

    def test_signal_markers_are_idempotent(self) -> None:
        entry = _entry("w")
        self.catalog.upsert(entry)
        record_state_signal_emitted(self.catalog, "w", "turn-9")
        record_state_signal_emitted(self.catalog, "w", "turn-9")
        row = self.catalog.get("w")
        assert row is not None
        self.assertEqual(row.state_signal_emitted_for, "turn-9")
        record_non_reaction_emitted(self.catalog, "w", "row-1")
        record_non_reaction_emitted(self.catalog, "w", "row-1")
        row = self.catalog.get("w")
        assert row is not None
        self.assertEqual(row.non_reaction_emitted_for, "row-1")
        record_state_signal_emitted(self.catalog, "missing", "turn-9")
        record_non_reaction_emitted(self.catalog, "missing", "row-1")

    def test_interrupt_request_stamp_is_idempotent(self) -> None:
        self.catalog.upsert(_entry("w"))
        record_interrupt_request(
            self.catalog,
            "w",
            by="developer",
            at="2026-07-13T15:39:00+00:00",
            turn_id="turn-9",
        )
        record_interrupt_request(
            self.catalog,
            "w",
            by="developer",
            at="2026-07-13T15:39:00+00:00",
            turn_id="turn-9",
        )
        row = self.catalog.get("w")
        assert row is not None
        self.assertEqual(row.interrupt_requested_by, "developer")
        self.assertEqual(row.interrupt_requested_turn_id, "turn-9")
        record_interrupt_request(
            self.catalog,
            "missing",
            by="developer",
            at="2026-07-13T15:39:00+00:00",
            turn_id="turn-9",
        )

    def test_terminal_cursors_advance_and_are_idempotent(self) -> None:
        self.catalog.upsert(_entry("w"))
        record_terminal_cursors(
            self.catalog,
            "w",
            evidence_sequence=20,
            native_cursor="entry-250",
        )
        row = self.catalog.get("w")
        assert row is not None
        self.assertEqual(row.terminal_evidence_sequence, 20)
        self.assertEqual(row.terminal_native_cursor, "entry-250")
        record_terminal_cursors(
            self.catalog,
            "w",
            evidence_sequence=20,
            native_cursor="entry-250",
        )
        row = self.catalog.get("w")
        assert row is not None
        self.assertEqual(row.terminal_evidence_sequence, 20)
        record_terminal_cursors(self.catalog, "missing", evidence_sequence=1)


class SnapshotParityTests(unittest.TestCase):
    def test_terminal_parameter_preserves_previous_signature(self) -> None:
        ready_idle = _snapshot()
        self.assertEqual(snapshot_turn_state(ready_idle), None)
        self.assertEqual(snapshot_turn_state(ready_idle, previous="working"), "turn-ended")
        terminal = TurnTerminalEvidence(outcome="completed", turn_id="turn-9")
        self.assertEqual(
            snapshot_turn_state(ready_idle, "codex", previous="working", terminal=terminal),
            "working",
        )
        failed = TurnTerminalEvidence(outcome="failed", turn_id="turn-9")
        self.assertEqual(
            snapshot_turn_state(ready_idle, "codex", previous="working", terminal=failed),
            "turn-ended",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
