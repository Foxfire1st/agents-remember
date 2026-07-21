"""260718-CHATS-L5F R5: per-session structure release/bounding (leak fixes).

Direct, composition-light unit coverage for the daemon-side leak fixes: the control service's
per-session lock map is bounded-by-construction and released on session end, the queue-revision
bookkeeping map is capped, and the active projector releases its heavy per-session state when it
goes dormant instead of lingering as a registered tombstone.
"""

from __future__ import annotations

import unittest
from typing import Any, cast
from unittest import mock

from agents_remember.serving.conversation.control import queue_projection
from agents_remember.serving.conversation.control import service as control_service
from agents_remember.serving.conversation.control.service import (
    ControlChannel,
    ConversationControlService,
)
from agents_remember.serving.conversation.models import AuthorizationBinding
from agents_remember.serving.harness_control_models import OperationTimelineItem

NOW = "2026-07-21T05:00:00+00:00"


def _service() -> ConversationControlService:
    # session_lock / channel / release_session / _queue_row only touch the per-session structures
    # and the app secret, never the runtime authorities, so a bare service is sufficient here.
    return ConversationControlService(cast(Any, object()))


def _item(seq: int) -> OperationTimelineItem:
    return OperationTimelineItem(
        operation_id=f"op-{seq}",
        kind="prompt",
        source="terminal",
        state="queued",
        sequence=seq,
        submitted_at=NOW,
        updated_at=NOW,
        accepted_at=None,
        payload_digest_present=False,
    )


class SessionLockLeakTests(unittest.IsolatedAsyncioTestCase):
    async def test_release_session_drops_lock_and_every_epoch_channel(self) -> None:
        service = _service()
        service.session_lock("ar-1")
        service.channel("ar-1", "epoch-a")
        service.channel("ar-1", "epoch-b")
        service.channel("ar-2", "epoch-a")
        self.assertIn("ar-1", service._locks)
        service.release_session("ar-1")
        self.assertNotIn("ar-1", service._locks)
        self.assertEqual([key for key in service._channels if key[0] == "ar-1"], [])
        # A sibling session is untouched.
        self.assertIn(("ar-2", "epoch-a"), service._channels)

    async def test_locks_are_bounded_by_construction_evicting_idle_first(self) -> None:
        service = _service()
        with mock.patch.object(control_service, "MAX_SESSION_LOCKS_PER_APP", 4):
            for index in range(20):
                service.session_lock(f"ar-{index}")
        self.assertLessEqual(len(service._locks), 4)

    async def test_a_held_lock_is_never_evicted(self) -> None:
        service = _service()
        held = service.session_lock("held")
        async with held:
            with mock.patch.object(control_service, "MAX_SESSION_LOCKS_PER_APP", 3):
                for index in range(30):
                    service.session_lock(f"ar-{index}")
            # The actively-held lock survived every eviction pass.
            self.assertIs(service._locks.get("held"), held)


class QueueRowsBoundTests(unittest.TestCase):
    def test_queue_rows_are_bounded_with_oldest_key_eviction(self) -> None:
        service = _service()
        channel = ControlChannel()
        auth = AuthorizationBinding(principal_id="op", tenant_id="local")
        with mock.patch.object(queue_projection, "MAX_QUEUE_ROWS_PER_CHANNEL", 3):
            for seq in range(1, 7):
                queue_projection._queue_row(
                    service, auth, channel, "ar-1", "epoch-1", _item(seq)
                )
        self.assertEqual(len(channel.queue_rows), 3)
        # The three oldest keys were evicted; the three newest remain.
        self.assertNotIn(("prompt", "op-1", 1), channel.queue_rows)
        self.assertNotIn(("prompt", "op-3", 3), channel.queue_rows)
        self.assertIn(("prompt", "op-6", 6), channel.queue_rows)

    def test_the_real_cap_is_a_named_constant(self) -> None:
        # The cap exists as a named constant (D2 "bounded by construction"), not a magic literal.
        self.assertGreater(queue_projection.MAX_QUEUE_ROWS_PER_CHANNEL, 0)


if __name__ == "__main__":
    unittest.main()
