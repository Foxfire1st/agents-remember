"""Canonical terminal evidence mapping skips non-claims and preserves later claims."""

from __future__ import annotations

import unittest

from agents_remember.models.conversations.evidence import (
    NativeEvidenceFrame,
    NativeEvidencePage,
)
from agents_remember.serving.terminal_evidence import latest_native_terminal_evidence

NOW = "2026-09-08T10:00:00+00:00"


def _frame(native_id: str, raw: dict[str, object]) -> NativeEvidenceFrame:
    return NativeEvidenceFrame(
        native_id=native_id,
        native_parent_id=None,
        native_type="message",
        created_at=NOW,
        raw=raw,
    )


def _page(*frames: NativeEvidenceFrame) -> NativeEvidencePage:
    return NativeEvidencePage(
        frames=tuple(frames),
        next_cursor=None,
        truncated=False,
        bridge_epoch="epoch-1",
    )


def _assistant_message(*, stop_reason: str | None = None) -> dict[str, object]:
    message: dict[str, object] = {
        "role": "assistant",
        "content": [{"type": "text", "text": "native response"}],
    }
    if stop_reason is not None:
        message["stopReason"] = stop_reason
    return {"id": "message", "type": "message", "message": message}


class CanonicalNativeTerminalMappingTests(unittest.TestCase):
    def test_unmappable_and_nonterminal_frames_make_no_terminal_claim(self) -> None:
        page = _page(
            _frame("malformed-1", {"id": "malformed-1", "type": "message"}),
            _frame("open-1", _assistant_message()),
        )

        self.assertIsNone(latest_native_terminal_evidence(page, "pi"))

    def test_unmappable_frame_does_not_hide_later_canonical_terminal_frame(self) -> None:
        page = _page(
            _frame("malformed-1", {"id": "malformed-1", "type": "message"}),
            _frame("completed-1", _assistant_message(stop_reason="stop")),
        )

        projection = latest_native_terminal_evidence(page, "pi")

        self.assertIsNotNone(projection)
        assert projection is not None
        self.assertEqual(projection.evidence_id, "native:completed-1")
        self.assertEqual(projection.evidence.outcome, "completed")
        self.assertEqual(projection.evidence.stop_reason, "stop")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
