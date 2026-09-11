"""Focused no-loss cursor and envelope tests for terminal evidence reads."""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest import mock

from agents_remember.errors import HarnessControlError
from agents_remember.models.conversations.control_wire import (
    AdapterSnapshot,
    ControlIdentity,
)
from agents_remember.models.conversations.evidence import (
    EvidenceFrame,
    EvidencePage,
    NativeEvidenceFrame,
    NativeEvidencePage,
)
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_evidence import (
    MAX_NATIVE_LIFT_PAGES,
    read_entry_terminal_evidence,
)
from agents_remember.serving.terminal_liveness import (
    LivenessProbe,
    _terminal_evidence,
    observe_terminal_liveness,
)
from agents_remember.serving.terminal_tmux import TmuxProbeResult

NOW = "2026-09-08T10:00:00+00:00"
CHECKED_AT = datetime(2026, 9, 8, 10, tzinfo=UTC)


def _entry(session_id: str = "worker-1", **overrides: object) -> TerminalCatalogEntry:
    fields: dict[str, object] = dict(
        id=session_id,
        label=f"Chat {session_id}",
        kind="harness",
        harness="codex",
        lifecycle_id=None,
        cwd=Path("/workspace"),
        tmux_name=f"ar-{session_id}",
        command=("codex",),
        created_at="2026-09-08T00:00:00+00:00",
        last_attached_at="2026-09-08T00:00:00+00:00",
        status="running",
        control_state="ready",
        control_endpoint=Path(f"/tmp/{session_id}.sock"),
    )
    fields.update(overrides)
    return TerminalCatalogEntry(**fields)  # type: ignore[arg-type]


def _snapshot(entry: TerminalCatalogEntry) -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(entry.id, entry.tmux_name, entry.created_at),
        control="ready",
        activity="idle",
        acceptance="immediate",
        vendor_session_id="vendor-1",
        raw={},
    )


class _LiveHost:
    def get(self, session_id: str) -> None:
        del session_id

    def probe_session(self, tmux_name: str) -> TmuxProbeResult:
        del tmux_name
        return TmuxProbeResult(exists=True, evidence="alive")

    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return True


def _codex_frame(sequence: int, *, status: str = "completed") -> EvidenceFrame:
    return EvidenceFrame(
        sequence=sequence,
        kind="completed",
        created_at=NOW,
        raw={
            "turn": {
                "id": f"turn-{sequence}",
                "status": status,
                "items": [],
                "completedAt": NOW,
            }
        },
    )


def _evidence_page(
    *frames: EvidenceFrame,
    latest: int | None = None,
    evicted_before: int = 0,
    truncated: bool = False,
) -> EvidencePage:
    return EvidencePage(
        frames=tuple(frames),
        latest_sequence=max((frame.sequence for frame in frames), default=0)
        if latest is None
        else latest,
        evicted_before_sequence=evicted_before,
        truncated=truncated,
        bridge_epoch="epoch-1",
    )


def _pi_frame(
    native_id: str,
    *,
    stop_reason: str | None = "stop",
    native_type: str = "message",
) -> NativeEvidenceFrame:
    message: dict[str, object] = {
        "role": "assistant",
        "content": [{"type": "text", "text": "partial"}],
    }
    if stop_reason is not None:
        message["stopReason"] = stop_reason
    raw: dict[str, object] = {"id": native_id, "type": native_type, "message": message}
    if native_type == "compaction":
        raw = {"id": native_id, "type": native_type, "summary": "compacted"}
    return NativeEvidenceFrame(
        native_id=native_id,
        native_parent_id=None,
        native_type=native_type,
        created_at=NOW,
        raw=raw,
    )


def _native_page(
    *frames: NativeEvidenceFrame,
    next_cursor: str | None = None,
    truncated: bool = False,
) -> NativeEvidencePage:
    return NativeEvidencePage(
        frames=tuple(frames),
        next_cursor=next_cursor,
        truncated=truncated,
        bridge_epoch="epoch-1",
    )


class ReadEntryTerminalEvidenceTests(unittest.TestCase):
    def test_truncated_deque_advances_only_to_last_returned_frame(self) -> None:
        entry = _entry(terminal_evidence_sequence=40)
        page = _evidence_page(
            *(_codex_frame(sequence) for sequence in range(41, 46)),
            latest=60,
            truncated=True,
        )
        with mock.patch(
            "agents_remember.serving.terminal_evidence.read_control_evidence",
            return_value=page,
        ) as reader:
            read = read_entry_terminal_evidence(entry)

        reader.assert_called_once_with(entry, after_sequence=40)
        self.assertEqual(read.evidence_sequence, 45)
        self.assertIsNotNone(read.projection)

    def test_complete_page_reaching_tail_advances_to_last_frame(self) -> None:
        entry = _entry(terminal_evidence_sequence=40)
        page = _evidence_page(_codex_frame(41), latest=41)
        with mock.patch(
            "agents_remember.serving.terminal_evidence.read_control_evidence",
            return_value=page,
        ):
            read = read_entry_terminal_evidence(entry)
        self.assertEqual(read.evidence_sequence, 41)

    def test_complete_empty_page_retains_persisted_cursor(self) -> None:
        cases = ((None, _evidence_page(latest=0)), (40, _evidence_page(latest=40)))
        for cursor, page in cases:
            with self.subTest(cursor=cursor):
                entry = _entry(terminal_evidence_sequence=cursor)
                with mock.patch(
                    "agents_remember.serving.terminal_evidence.read_control_evidence",
                    return_value=page,
                ):
                    read = read_entry_terminal_evidence(entry)
                self.assertIsNone(read.projection)
                self.assertEqual(read.evidence_sequence, cursor)

    def test_eviction_gap_raises_and_keeps_cursor(self) -> None:
        entry = _entry(terminal_evidence_sequence=40)
        page = _evidence_page(_codex_frame(45), latest=45, evicted_before=41)
        with (
            mock.patch(
                "agents_remember.serving.terminal_evidence.read_control_evidence",
                return_value=page,
            ),
            self.assertRaises(HarnessControlError),
        ):
            read_entry_terminal_evidence(entry)
        self.assertEqual(entry.terminal_evidence_sequence, 40)

    def test_incoherent_complete_deque_envelope_raises_and_keeps_cursor(self) -> None:
        entry = _entry(terminal_evidence_sequence=40)
        cases = (
            ("stale-frame", _evidence_page(_codex_frame(40), latest=40)),
            ("tail-mismatch", _evidence_page(_codex_frame(41), latest=60)),
            ("empty-ahead", _evidence_page(latest=60)),
            ("empty-truncated", _evidence_page(latest=60, truncated=True)),
        )
        for name, page in cases:
            with (
                self.subTest(name=name),
                mock.patch(
                    "agents_remember.serving.terminal_evidence.read_control_evidence",
                    return_value=page,
                ),
                self.assertRaises(HarnessControlError),
            ):
                read_entry_terminal_evidence(entry)
        self.assertEqual(entry.terminal_evidence_sequence, 40)

    def test_liveness_contains_eviction_gap_without_cursor_persistence(self) -> None:
        entry = _entry(terminal_evidence_sequence=40, turn_state="working")
        page = _evidence_page(_codex_frame(45), latest=60, evicted_before=44)
        with tempfile.TemporaryDirectory() as directory:
            catalog = TerminalCatalog(Path(directory) / "catalog.json")
            catalog.upsert(entry)
            probe = LivenessProbe(
                pane_capturer=lambda _tmux_name: "",
                snapshot_reader=_snapshot,
            )
            with mock.patch(
                "agents_remember.serving.terminal_evidence.read_control_evidence",
                return_value=page,
            ):
                observe_terminal_liveness(
                    catalog,
                    _LiveHost(),
                    entry,
                    checked_at=CHECKED_AT,
                    probe=probe,
                )
            persisted = catalog.get(entry.id)

        self.assertIsNotNone(persisted)
        assert persisted is not None
        self.assertEqual(persisted.terminal_evidence_sequence, 40)
        self.assertIsNone(persisted.terminal_outcome)

    def test_unsupported_harness_has_no_projection_or_cursor_advance(self) -> None:
        entry = _entry(harness="future-harness", terminal_evidence_sequence=40)
        with (
            mock.patch(
                "agents_remember.serving.terminal_evidence.read_control_evidence"
            ) as evidence,
            mock.patch(
                "agents_remember.serving.harness_control_client.read_control_native_page"
            ) as native,
        ):
            read = read_entry_terminal_evidence(entry)

        evidence.assert_not_called()
        native.assert_not_called()
        self.assertIsNone(read.projection)
        self.assertIsNone(read.evidence_sequence)
        self.assertIsNone(read.native_cursor)


class PiCursorContinuationTests(unittest.TestCase):
    def _pi_entry(self, **overrides: object) -> TerminalCatalogEntry:
        fields = dict(overrides)
        fields["harness"] = "pi"
        return _entry("worker-1", **fields)

    def test_empty_page_retains_persisted_native_cursor(self) -> None:
        entry = self._pi_entry(terminal_native_cursor="entry-250")
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            return_value=_native_page(),
        ) as reader:
            read = read_entry_terminal_evidence(entry)

        reader.assert_called_once_with(entry, cursor="entry-250", limit=200)
        self.assertIsNone(read.projection)
        self.assertEqual(read.native_cursor, "entry-250")

    def test_forward_read_starts_after_persisted_native_cursor(self) -> None:
        entry = self._pi_entry(terminal_native_cursor="entry-251")
        page = _native_page(_pi_frame("entry-252"), _pi_frame("entry-261", stop_reason="aborted"))
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            return_value=page,
        ) as reader:
            read = read_entry_terminal_evidence(entry)

        reader.assert_called_once_with(entry, cursor="entry-251", limit=200)
        self.assertEqual(read.native_cursor, "entry-261")
        self.assertIsNotNone(read.projection)
        assert read.projection is not None
        self.assertEqual(read.projection.evidence_id, "native:entry-261")

    def test_page_walk_is_bounded_at_maximum_native_pages(self) -> None:
        entry = self._pi_entry()
        pages = [
            _native_page(
                _pi_frame(f"entry-{index}", stop_reason=None),
                next_cursor=f"entry-{index}",
                truncated=True,
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

    def test_unmappable_and_nonterminal_frames_still_advance_native_cursor(self) -> None:
        entry = self._pi_entry()
        malformed = NativeEvidenceFrame(
            native_id="entry-bad",
            native_parent_id=None,
            native_type="message",
            created_at=NOW,
            raw={"id": "entry-bad", "type": "message"},
        )
        page = _native_page(malformed, _pi_frame("entry-open", stop_reason=None))
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            return_value=page,
        ):
            read = read_entry_terminal_evidence(entry)

        self.assertIsNone(read.projection)
        self.assertEqual(read.native_cursor, "entry-open")

    def test_later_native_page_failure_has_no_result_or_cursor_advance(self) -> None:
        entry = self._pi_entry(terminal_native_cursor="entry-250")
        first_page = _native_page(
            _pi_frame("entry-251"),
            next_cursor="entry-251",
            truncated=True,
        )
        with mock.patch(
            "agents_remember.serving.harness_control_client.read_control_native_page",
            side_effect=[first_page, HarnessControlError("later page failed")],
        ):
            result = _terminal_evidence(LivenessProbe(), entry)

        self.assertIsNone(result)
        self.assertEqual(entry.terminal_native_cursor, "entry-250")


if __name__ == "__main__":
    unittest.main()
