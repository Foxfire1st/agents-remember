"""Wire-contract conformance for the eve session protocol: parsing in, and requests out.

Two halves of the same contract. The reader's absolute cursor is the one piece of this adapter that
cannot be repaired after the fact -- an off-by-one or a frame-boundary mistake silently drops or
duplicates a durable event, and the sessions that suffered it are the ones already running -- so its
index arithmetic and envelope parsing are pinned against eve's documented shapes. The writer's half
is what the production client actually puts on the socket: the adapter conformance suite drives an
``EveRuntimeTransport`` double, which is the right seam for protocol behaviour but blind to those
bytes, so the request bodies are asserted here from the real client with only process startup
replaced.
"""

from __future__ import annotations

import json
import sys
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "mcp" / "src"))

from agents_remember.errors import HarnessControlError
from agents_remember.serving.eve_protocol import (
    EVE_HEALTH_PATH,
    EVE_SESSION_ID_HEADER,
    EVE_STREAM_TAIL_INDEX_HEADER,
    EVE_STREAM_VERSION_HEADER,
    TURN_POLICY_QUEUE,
    EveEventDeduplicator,
    EveRuntimeLaunch,
    EveStreamEvent,
    eve_session_control_route,
    eve_session_route,
    eve_session_stream_route,
    parse_event_frame,
    parse_session_acceptance,
    parse_stream_tail_index,
    parse_stream_version,
)
from agents_remember.serving.eve_runtime_client import (
    EveRuntimeProcess,
    cancel_turn_body,
    create_session_body,
    follow_up_body,
)
from agents_remember.serving.eve_stream_cursor import (
    EveNdjsonDecoder,
    join_frame_text,
)


def _frame(event_type: str, data: dict[str, object], *, index_id: str = "evt_1") -> bytes:
    return (
        json.dumps(
            {
                "type": event_type,
                "data": data,
                "meta": {"id": index_id, "at": "2026-09-16T00:00:00Z"},
            }
        ).encode()
        + b"\n"
    )


class EveRouteTests(unittest.TestCase):
    """Routes are ID-addressed; nothing here can create or follow a replacement session."""

    def test_every_session_route_names_the_exact_durable_id(self) -> None:
        self.assertEqual(EVE_HEALTH_PATH, "/eve/v1/health")
        self.assertEqual(eve_session_route("wrun_A"), "/eve/v1/session/wrun_A")
        self.assertEqual(
            eve_session_control_route("wrun_A", "cancel"), "/eve/v1/session/wrun_A/cancel"
        )
        self.assertEqual(
            eve_session_stream_route("wrun_A", start_index=0), "/eve/v1/session/wrun_A/stream"
        )
        self.assertEqual(
            eve_session_stream_route("wrun_A", start_index=17),
            "/eve/v1/session/wrun_A/stream?startIndex=17",
        )

    def test_a_negative_cursor_and_an_empty_id_are_refused(self) -> None:
        with self.assertRaises(HarnessControlError):
            eve_session_stream_route("wrun_A", start_index=-1)
        with self.assertRaises(HarnessControlError):
            eve_session_route("")


class EveStreamHeaderTests(unittest.TestCase):
    """The declared stream version is required, not inferred from the JSON."""

    def test_supported_versions_are_accepted_and_unknown_ones_fail(self) -> None:
        for version in ("21", "22", "23", "24", "25"):
            self.assertEqual(parse_stream_version({EVE_STREAM_VERSION_HEADER: version}), version)
        with self.assertRaises(HarnessControlError):
            parse_stream_version({EVE_STREAM_VERSION_HEADER: "26"})
        with self.assertRaises(HarnessControlError):
            parse_stream_version({})

    def test_the_optional_tail_header_is_read_or_absent(self) -> None:
        self.assertEqual(parse_stream_tail_index({EVE_STREAM_TAIL_INDEX_HEADER: "-1"}), -1)
        self.assertEqual(parse_stream_tail_index({EVE_STREAM_TAIL_INDEX_HEADER: "12"}), 12)
        self.assertIsNone(parse_stream_tail_index({}))
        with self.assertRaises(HarnessControlError):
            parse_stream_tail_index({EVE_STREAM_TAIL_INDEX_HEADER: "many"})

    def test_header_lookup_is_case_insensitive_and_requires_a_value(self) -> None:
        self.assertEqual(
            parse_stream_version({"X-Eve-Stream-Version": "25"}),
            "25",
        )
        with self.assertRaises(HarnessControlError):
            parse_stream_version({EVE_STREAM_VERSION_HEADER: "   "})


class EveAcceptanceTests(unittest.TestCase):
    """The durable session id comes from the body or the header; its absence is a failure."""

    def test_body_and_header_are_both_accepted(self) -> None:
        self.assertEqual(parse_session_acceptance({"sessionId": "wrun_A"}, {}), ("wrun_A", None))
        self.assertEqual(
            parse_session_acceptance({}, {EVE_SESSION_ID_HEADER: "wrun_B"}), ("wrun_B", None)
        )
        self.assertEqual(
            parse_session_acceptance({"sessionId": "wrun_A", "deliveryId": "delivery-1"}, {}),
            ("wrun_A", "delivery-1"),
        )

    def test_an_acceptance_without_identity_fails_instead_of_unbinding_the_session(self) -> None:
        with self.assertRaises(HarnessControlError):
            parse_session_acceptance({}, {})
        with self.assertRaises(HarnessControlError):
            parse_session_acceptance({"ok": True}, {EVE_SESSION_ID_HEADER: "  "})


class EveFrameTests(unittest.TestCase):
    """Frames are parsed from their declared schema, never by scanning the text."""

    def test_the_envelope_is_read_field_by_field(self) -> None:
        event = parse_event_frame(
            json.dumps(
                {
                    "type": "turn.started",
                    "data": {"sequence": 0, "turnId": "turn_0"},
                    "meta": {
                        "id": "evt_01ABC",
                        "at": "2026-09-16T00:00:00.000Z",
                        "deliveryIds": ["delivery-1"],
                    },
                }
            ),
            index=3,
        )
        self.assertEqual(event.index, 3)
        self.assertEqual(event.type, "turn.started")
        self.assertEqual(event.event_id, "evt_01ABC")
        self.assertEqual(event.delivery_ids, ("delivery-1",))
        self.assertEqual(event.turn_id, "turn_0")
        self.assertEqual(
            event.raw_frame()["meta"],
            {
                "id": "evt_01ABC",
                "at": "2026-09-16T00:00:00.000Z",
                "deliveryIds": ["delivery-1"],
            },
        )

    def test_a_pre_version_twenty_event_has_no_id_and_still_parses(self) -> None:
        event = parse_event_frame(
            json.dumps(
                {"type": "session.waiting", "data": {}, "meta": {"at": "2026-01-01T00:00:00Z"}}
            ),
            index=0,
        )
        self.assertIsNone(event.event_id)
        self.assertEqual(event.delivery_ids, ())
        self.assertEqual(event.raw_frame()["meta"], {"at": "2026-01-01T00:00:00Z"})

    def test_malformed_frames_are_refused(self) -> None:
        for line in (
            "not json",
            "[]",
            json.dumps({"data": {}}),
            json.dumps({"type": ""}),
            json.dumps({"type": "turn.started", "data": []}),
        ):
            with self.assertRaises(HarnessControlError):
                parse_event_frame(line, index=0)


class EveCursorTests(unittest.TestCase):
    """The absolute event index advances once per event, across every read boundary."""

    def test_frames_split_across_reads_keep_their_absolute_index(self) -> None:
        decoder = EveNdjsonDecoder(start_index=4)
        payload = _frame("message.appended", {"messageDelta": "he"}, index_id="evt_a")
        payload += _frame("message.appended", {"messageDelta": "llo"}, index_id="evt_b")
        first = decoder.feed(payload[: len(payload) // 2])
        second = decoder.feed(payload[len(payload) // 2 :])
        events = [*first, *second]
        self.assertEqual([event.index for event in events], [4, 5])
        self.assertEqual([event.event_id for event in events], ["evt_a", "evt_b"])
        self.assertEqual(decoder.next_index, 6)
        self.assertEqual(join_frame_text(events, "messageDelta"), "hello")

    def test_blank_keep_alive_lines_do_not_consume_an_index(self) -> None:
        decoder = EveNdjsonDecoder(start_index=0)
        events = decoder.feed(b"\n\n" + _frame("session.waiting", {}) + b"\n")
        self.assertEqual([event.index for event in events], [0])
        self.assertEqual(decoder.next_index, 1)

    def test_a_trailing_frame_without_a_newline_is_still_decoded(self) -> None:
        decoder = EveNdjsonDecoder(start_index=2)
        self.assertEqual(decoder.feed(_frame("session.waiting", {})[:-1]), [])
        tail = decoder.finish()
        self.assertEqual([event.index for event in tail], [2])
        self.assertEqual(tail[0].type, "session.waiting")

    def test_an_oversized_frame_is_refused_rather_than_buffered_without_bound(self) -> None:
        decoder = EveNdjsonDecoder(start_index=0, max_frame_bytes=64)
        with self.assertRaises(HarnessControlError):
            decoder.feed(b"x" * 200)

    def test_a_frame_exactly_at_the_ceiling_is_still_read(self) -> None:
        payload = _frame("session.waiting", {"note": "y" * 20})
        decoder = EveNdjsonDecoder(start_index=0, max_frame_bytes=len(payload))
        events = decoder.feed(payload)
        self.assertEqual([event.type for event in events], ["session.waiting"])


class EveEnvelopeIdentityTests(unittest.TestCase):
    """``turn_id`` reads the documented coordinate and nothing else."""

    def test_turn_identity_is_exact_and_optional(self) -> None:
        with_turn = EveStreamEvent(
            index=0,
            type="message.appended",
            data={"turnId": "turn_3"},
            event_id=None,
            emitted_at=None,
        )
        without_turn = EveStreamEvent(
            index=1, type="session.waiting", data={}, event_id=None, emitted_at=None
        )
        self.assertEqual(with_turn.turn_id, "turn_3")
        self.assertIsNone(without_turn.turn_id)
        self.assertEqual(without_turn.raw_frame()["meta"], {})


class EveReplayWindowTests(unittest.TestCase):
    """The one replay bound: the adapter holds this component, so these are its cases.

    An off-by-one or an unbounded set here is invisible on the wire until a long-lived session
    rewinds, which is why the eviction boundary is asserted at two sizes rather than one.
    """

    @staticmethod
    def _event(index: int, event_id: str | None) -> EveStreamEvent:
        return EveStreamEvent(
            index=index,
            type="message.appended",
            data={"messageDelta": "x"},
            event_id=event_id,
            emitted_at=None,
        )

    def test_a_repeated_id_is_a_replay_and_the_first_sight_is_not(self) -> None:
        window = EveEventDeduplicator(window=4)
        self.assertFalse(window.is_replay(self._event(0, "evt_a")))
        self.assertTrue(window.is_replay(self._event(1, "evt_a")))
        self.assertFalse(window.is_replay(self._event(2, "evt_b")))
        self.assertEqual(window.retained, 2)

    def test_the_window_evicts_oldest_first_and_stays_bounded(self) -> None:
        window = EveEventDeduplicator(window=3)
        for index, event_id in enumerate(("evt_a", "evt_b", "evt_c", "evt_d", "evt_e")):
            window.is_replay(self._event(index, event_id))
        # Five distinct ids through a three-id window: the earliest two are forgotten, so they read
        # as new again while the retained tail is still recognised.
        self.assertEqual(window.retained, 3)
        self.assertFalse(window.is_replay(self._event(5, "evt_a")))
        self.assertTrue(window.is_replay(self._event(6, "evt_e")))

    def test_window_size_one_still_recognises_its_single_entry(self) -> None:
        window = EveEventDeduplicator(window=1)
        window.is_replay(self._event(0, "evt_a"))
        self.assertTrue(window.is_replay(self._event(1, "evt_a")))
        self.assertEqual(window.retained, 1)

    def test_an_event_without_an_id_is_never_a_replay(self) -> None:
        window = EveEventDeduplicator(window=4)
        self.assertFalse(window.is_replay(self._event(0, None)))
        self.assertFalse(window.is_replay(self._event(1, None)))
        # Nothing was retained, so the caller's cursor remains the only replay proof for these.
        self.assertEqual(window.retained, 0)

    def test_a_nonpositive_window_is_refused(self) -> None:
        for window in (0, -1):
            with self.subTest(window=window), self.assertRaises(HarnessControlError):
                EveEventDeduplicator(window=window)


def _record(respond: Callable[[httpx.Request], dict[str, Any]]) -> _Recorder:
    """One recording endpoint; the helper the request cases build their stub server with."""

    return _Recorder(respond)


class _Recorder:
    """A recording HTTP endpoint plus the client that talks to it."""

    def __init__(self, respond: Callable[[httpx.Request], dict[str, Any]]) -> None:
        self.requests: list[httpx.Request] = []
        self._respond = respond

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        body = dict(self._respond(request))
        status = int(body.pop("__status", 202))
        headers = body.pop("__headers", None)
        return httpx.Response(status, json=body, headers=headers)

    def bodies(self) -> list[dict[str, Any]]:
        """Every recorded request body; a request eve receives with no body reads as ``{}``."""

        recorded: list[dict[str, Any]] = []
        for request in self.requests:
            raw = request.content.decode("utf-8")
            recorded.append(json.loads(raw) if raw else {})
        return recorded

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


class _StartedRuntime(EveRuntimeProcess):
    """The production client with process startup replaced by a recording transport.

    The single replaced member is ``start``: no child process is spawned, and every request below
    is composed and sent by the production methods under test.
    """

    def __init__(self, recorder: _Recorder) -> None:
        super().__init__(
            EveRuntimeLaunch(runtime_root="/tmp/ar-eve-wire", port=1),
            client_factory=lambda: httpx.AsyncClient(
                transport=httpx.MockTransport(recorder.handler),
                base_url="http://127.0.0.1:1",
            ),
        )

    async def start(self) -> None:
        self._client = self._client_factory()


SESSION_ID = "wrun_wire_fixture"


class EveWireBodyTests(unittest.TestCase):
    """The request bodies themselves, asserted directly so a policy change cannot hide."""

    def test_create_and_follow_up_both_spell_the_queued_policy(self) -> None:
        for label, body in (
            ("create", create_session_body("hello")),
            ("follow-up", follow_up_body("hello")),
        ):
            with self.subTest(route=label):
                self.assertEqual(body["turnPolicy"], TURN_POLICY_QUEUE)
                self.assertNotEqual(body["turnPolicy"], "steer")
                self.assertEqual(body["message"], "hello")

    def test_cancel_body_addresses_the_exact_observed_turn(self) -> None:
        self.assertEqual(cancel_turn_body("turn_3"), {"turnId": "turn_3"})
        # No observed turn means "cancel whatever is active", which eve spells as an empty body.
        self.assertIsNone(cancel_turn_body(None))


class EveWireRequestTests(unittest.IsolatedAsyncioTestCase):
    """What the production methods put on the wire for each operation."""

    async def _runtime(self, recorder: _Recorder) -> _StartedRuntime:
        runtime = _StartedRuntime(recorder)
        await runtime.start()
        return runtime

    async def test_create_session_posts_the_queued_policy(self) -> None:
        recorder = _record(lambda _: {"ok": True, "sessionId": SESSION_ID, "status": "accepted"})
        runtime = await self._runtime(recorder)
        try:
            session_id, _ = await runtime.create_session("first task")
        finally:
            await runtime.stop("forced")
        self.assertEqual(session_id, SESSION_ID)
        self.assertEqual(recorder.paths(), ["/eve/v1/session"])
        self.assertEqual(recorder.bodies(), [create_session_body("first task")])
        self.assertEqual(recorder.bodies()[0]["turnPolicy"], "queue")

    async def test_follow_up_posts_the_queued_policy_to_the_durable_id(self) -> None:
        recorder = _record(lambda _: {"ok": True, "sessionId": SESSION_ID, "status": "accepted"})
        runtime = await self._runtime(recorder)
        try:
            session_id, delivery_id = await runtime.send_message(SESSION_ID, "second task")
        finally:
            await runtime.stop("forced")
        self.assertEqual(session_id, SESSION_ID)
        self.assertIsNone(delivery_id)
        self.assertEqual(recorder.paths(), [f"/eve/v1/session/{SESSION_ID}"])
        body = recorder.bodies()[0]
        self.assertEqual(body, {"message": "second task", "turnPolicy": "queue"})
        # The steer default is the failure this asserts against: a follow-up that steered would
        # cancel the peer's active turn instead of queueing behind it.
        self.assertNotEqual(body.get("turnPolicy"), "steer")

    async def test_a_session_named_only_in_the_response_header_is_still_accepted(self) -> None:
        recorder = _record(
            lambda _: {
                "__status": 202,
                "ok": True,
                "status": "accepted",
                "__headers": {EVE_SESSION_ID_HEADER: SESSION_ID},
            }
        )
        runtime = await self._runtime(recorder)
        try:
            session_id, _ = await runtime.send_message(SESSION_ID, "header identity only")
        finally:
            await runtime.stop("forced")
        # A body with no id leaves the header as the only proof of which session was continued.
        self.assertEqual(session_id, SESSION_ID)

    async def test_cancel_posts_the_exact_observed_turn(self) -> None:
        recorder = _record(lambda _: {"ok": True, "sessionId": SESSION_ID, "status": "accepted"})
        runtime = await self._runtime(recorder)
        try:
            body = await runtime.cancel_turn(SESSION_ID, turn_id="turn_3")
        finally:
            await runtime.stop("forced")
        self.assertEqual(recorder.paths(), [f"/eve/v1/session/{SESSION_ID}/cancel"])
        self.assertEqual(recorder.bodies(), [{"turnId": "turn_3"}])
        self.assertEqual(body.get("status"), "accepted")

    async def test_cancel_without_an_observed_turn_sends_no_turn_id(self) -> None:
        recorder = _record(lambda _: {"ok": True, "status": "accepted"})
        runtime = await self._runtime(recorder)
        try:
            await runtime.cancel_turn(SESSION_ID, turn_id=None)
        finally:
            await runtime.stop("forced")
        self.assertEqual(recorder.bodies(), [{}])

    async def test_input_responses_post_the_strict_request_id_shape(self) -> None:
        recorder = _record(lambda _: {"ok": True, "sessionId": SESSION_ID, "status": "accepted"})
        runtime = await self._runtime(recorder)
        try:
            await runtime.send_input_responses(
                SESSION_ID, [{"requestId": "req_1", "optionId": "approve"}]
            )
        finally:
            await runtime.stop("forced")
        self.assertEqual(
            recorder.bodies(),
            [{"inputResponses": [{"requestId": "req_1", "optionId": "approve"}]}],
        )

    async def test_an_unknown_session_refusal_never_creates_a_replacement(self) -> None:
        recorder = _record(
            lambda _: {
                "__status": 409,
                "code": "session_not_active",
                "error": "The session is no longer active.",
                "ok": False,
            }
        )
        runtime = await self._runtime(recorder)
        try:
            with self.assertRaises(HarnessControlError) as raised:
                await runtime.send_message("wrun_retired", "again")
        finally:
            await runtime.stop("forced")
        self.assertIn("no replacement session is created", str(raised.exception))
        # Exactly one attempt: the refusal is never retried against a different address.
        self.assertEqual(len(recorder.requests), 1)

    async def test_a_no_active_turn_cancel_is_reported_rather_than_raised(self) -> None:
        recorder = _record(lambda _: {"__status": 200, "ok": True, "status": "no_active_turn"})
        runtime = await self._runtime(recorder)
        try:
            body = await runtime.cancel_turn(SESSION_ID, turn_id="turn_9")
        finally:
            await runtime.stop("forced")
        # eve answers 200 for a parked or unknown address and 202 for an accepted cancel; both are
        # success, so the caller receives the status instead of an HTTP failure.
        self.assertEqual(body.get("status"), "no_active_turn")

    async def test_a_message_acceptance_without_any_identity_is_refused(self) -> None:
        recorder = _record(lambda _: {"__status": 202, "ok": True, "status": "accepted"})
        runtime = await self._runtime(recorder)
        try:
            with self.assertRaises(HarnessControlError) as raised:
                await runtime.create_session("identity missing")
        finally:
            await runtime.stop("forced")
        self.assertIn("no durable session id", str(raised.exception))
        self.assertNotIn(EVE_SESSION_ID_HEADER, recorder.requests[0].headers)

    async def test_a_disconnected_write_reports_that_it_may_have_been_sent(self) -> None:
        def explode(_: httpx.Request) -> dict[str, Any]:
            raise httpx.ConnectError("connection refused")

        runtime = _StartedRuntime(_Recorder(explode))
        await runtime.start()
        try:
            with self.assertRaises(HarnessControlError) as raised:
                await runtime.send_message(SESSION_ID, "possibly delivered")
        finally:
            await runtime.stop("forced")
        # The ambiguity is what the caller reconciles against, so it must survive the raise.
        self.assertEqual(getattr(raised.exception, "may_have_sent", None), True)


if __name__ == "__main__":
    unittest.main()
