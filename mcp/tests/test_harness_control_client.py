"""Retry-safety tests for the blocking exact-session control client."""

from __future__ import annotations

import json
import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlClientError
from agents_remember.serving.harness_control_client import (
    SUBMIT_TIMEOUT_SECONDS,
    read_control_native_page,
    request_control,
    set_control_model,
    submit_control_prompt,
)


@dataclass(frozen=True)
class _Entry:
    id: str = "session-1"
    tmux_name: str = "ar-session-1"
    created_at: str = "2026-07-16T08:00:00+00:00"
    control_endpoint: Path = Path("/tmp/control.sock")


class _Socket:
    def __init__(self, *, send_error: bool = False, sendall_error: bool = False) -> None:
        self.send_error = send_error
        self.sendall_error = sendall_error

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def settimeout(self, _timeout: float) -> None:
        return None

    def connect(self, _endpoint: str) -> None:
        return None

    def send(self, _data: bytes) -> int:
        if self.send_error:
            raise OSError("failed before first byte")
        return 1

    def sendall(self, _data: bytes) -> None:
        if self.sendall_error:
            raise OSError("failed after first byte")

    def recv(self, _size: int) -> bytes:
        return b""


class _SlowEchoSocket(_Socket):
    """Answers only when the caller's socket timeout covers the bridge's replay-echo delay."""

    def __init__(self, payload: bytes, *, delay_seconds: float) -> None:
        super().__init__()
        self.payload = payload
        self.delay_seconds = delay_seconds
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recv(self, _size: int) -> bytes:
        if self.timeout is None or self.timeout < self.delay_seconds:
            raise TimeoutError("timed out waiting for the replay echo")
        return self.payload


class _WholeWriteSocket(_Socket):
    """Accepts the whole request in one ``send``, then behaves like a peer that already closed.

    This is the ordinary production shape: the server answers a small request and closes with our
    bytes drained, so by the time the caller would write a remainder the peer is gone and any write
    raises EPIPE. ``sendall`` here fails unconditionally to prove it is never reached.
    """

    def __init__(self, payload: bytes) -> None:
        super().__init__()
        self.payload = payload
        self.sendall_calls = 0

    def send(self, data: bytes) -> int:
        return len(data)

    def sendall(self, _data: bytes) -> None:
        self.sendall_calls += 1
        raise BrokenPipeError(32, "Broken pipe")

    def recv(self, _size: int) -> bytes:
        return self.payload


class HarnessControlWriteCompletionTests(unittest.TestCase):
    def test_a_fully_accepted_request_never_issues_a_remainder_write(self) -> None:
        # A one-send request left an EMPTY remainder, and `sendall` is a do-while over its buffer, so
        # it still issued a zero-length send. Once the server had answered and closed with the request
        # drained, that pointless write raised EPIPE and the client reported the completed exchange as
        # a may_have_sent disconnect — surfacing as an intermittent broken pipe under load.
        socket = _WholeWriteSocket(
            json.dumps({"ok": True, "result": {"snapshot": "ready"}}).encode() + b"\n"
        )
        with mock.patch(
            "agents_remember.serving.harness_control_client.socket.socket",
            return_value=socket,
        ):
            result = request_control(_Entry(), "snapshot")
        self.assertEqual(socket.sendall_calls, 0)
        self.assertEqual(result, {"snapshot": "ready"})

    def test_a_partially_accepted_request_still_writes_its_remainder(self) -> None:
        class _PartialWriteSocket(_Socket):
            def __init__(self) -> None:
                super().__init__()
                self.remainders: list[bytes] = []

            def send(self, _data: bytes) -> int:
                return 1

            def sendall(self, data: bytes) -> None:
                self.remainders.append(data)

            def recv(self, _size: int) -> bytes:
                return json.dumps({"ok": True, "result": {"snapshot": "ready"}}).encode() + b"\n"

        socket = _PartialWriteSocket()
        with mock.patch(
            "agents_remember.serving.harness_control_client.socket.socket",
            return_value=socket,
        ):
            request_control(_Entry(), "snapshot")
        self.assertEqual(len(socket.remainders), 1)
        self.assertTrue(socket.remainders[0])


class HarnessControlClientRetrySafetyTests(unittest.TestCase):
    def test_refused_control_socket_yields_honest_note_and_unlinks_stale_socket(self) -> None:
        # A controlled runner that exited uncleanly leaves a stale socket that
        # refuses (ECONNREFUSED). The client must render a designed lifecycle note WITHOUT the raw
        # "[Errno 111] Connection refused" surprise, and unlink the stale socket so the next attempt
        # sees the absent (ENOENT) case cleanly.
        import errno as _errno  # noqa: PLC0415
        import tempfile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as tmp:
            stale = Path(tmp) / "control.sock"
            stale.write_bytes(b"")  # a socket file that exists but has no listener

            class _RefusingSocket(_Socket):
                def connect(self, _endpoint: str) -> None:
                    raise ConnectionRefusedError(_errno.ECONNREFUSED, "Connection refused")

            with (
                mock.patch(
                    "agents_remember.serving.harness_control_client.socket.socket",
                    return_value=_RefusingSocket(),
                ),
                self.assertRaises(HarnessControlClientError) as raised,
            ):
                request_control(_Entry(control_endpoint=stale), "stop")
            message = str(raised.exception)
            self.assertIn("already exited", message)
            self.assertNotIn("Errno", message)
            self.assertNotIn("Connection refused", message)
            self.assertFalse(raised.exception.may_have_sent)
            # The stale socket file was unlinked so the next probe reads the absent case.
            self.assertFalse(stale.exists())

    def test_may_have_sent_is_false_until_socket_accepts_a_byte(self) -> None:
        with (
            mock.patch(
                "agents_remember.serving.harness_control_client.socket.socket",
                return_value=_Socket(send_error=True),
            ),
            self.assertRaises(HarnessControlClientError) as raised,
        ):
            request_control(_Entry(), "submit")
        self.assertFalse(raised.exception.may_have_sent)

    def test_may_have_sent_is_true_after_socket_accepts_a_byte(self) -> None:
        with (
            mock.patch(
                "agents_remember.serving.harness_control_client.socket.socket",
                return_value=_Socket(sendall_error=True),
            ),
            self.assertRaises(HarnessControlClientError) as raised,
        ):
            request_control(_Entry(), "submit")
        self.assertTrue(raised.exception.may_have_sent)

    def test_post_write_submit_failure_returns_unknown_with_same_request_id(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_client.request_control",
            side_effect=HarnessControlClientError("response timed out", may_have_sent=True),
        ) as request:
            receipt = submit_control_prompt(
                _Entry(),
                "one whole message",
                source="terminal",
                request_id="request-7",
            )
        self.assertEqual((receipt.request_id, receipt.acceptance), ("request-7", "unknown"))
        request.assert_called_once()

    def test_submit_waits_for_a_replay_echo_beyond_the_control_default(self) -> None:
        # The bridge answers a submit only after the harness CLI's replay echo
        # (measured 2-10s live). A 2.0s submit timeout turned that accepted message into a spurious
        # acceptance="unknown" + 120s reconcile loop; submit now waits up to SUBMIT_TIMEOUT_SECONDS.
        socket = _SlowEchoSocket(
            json.dumps(
                {
                    "ok": True,
                    "result": {
                        "requestId": "request-9",
                        "acceptance": "immediate",
                        "submittedAt": "2026-07-16T08:00:03+00:00",
                        "acceptedAt": "2026-07-16T08:00:03+00:00",
                        "vendorCorrelationId": "vendor-9",
                    },
                }
            ).encode()
            + b"\n",
            delay_seconds=3.0,
        )
        with mock.patch(
            "agents_remember.serving.harness_control_client.socket.socket",
            return_value=socket,
        ):
            receipt = submit_control_prompt(
                _Entry(),
                "one whole message",
                source="terminal",
                request_id="request-9",
            )
        self.assertEqual((receipt.request_id, receipt.acceptance), ("request-9", "immediate"))
        self.assertEqual(receipt.vendor_correlation_id, "vendor-9")
        self.assertEqual(socket.timeout, SUBMIT_TIMEOUT_SECONDS)

    def test_non_submit_actions_keep_the_fail_fast_control_default(self) -> None:
        socket = _SlowEchoSocket(b"{}\n", delay_seconds=3.0)
        with (
            mock.patch(
                "agents_remember.serving.harness_control_client.socket.socket",
                return_value=socket,
            ),
            self.assertRaises(HarnessControlClientError),
        ):
            request_control(_Entry(), "snapshot")
        self.assertEqual(socket.timeout, 2.0)

    def test_mismatched_receipt_stays_unknown_and_is_not_resent(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_client.request_control",
            return_value={
                "requestId": "different-request",
                "acceptance": "immediate",
                "submittedAt": "2026-07-16T08:00:00+00:00",
                "vendorCorrelationId": "vendor-different",
                "acceptedAt": "2026-07-16T08:00:01+00:00",
                "detail": None,
                "raw": {},
            },
        ) as request:
            receipt = submit_control_prompt(
                _Entry(),
                "one whole message",
                source="terminal",
                request_id="request-7",
            )
        self.assertEqual((receipt.request_id, receipt.acceptance), ("request-7", "unknown"))
        self.assertNotEqual(receipt.vendor_correlation_id, "vendor-different")
        request.assert_called_once()

    def test_post_write_set_failure_returns_unknown_without_retry(self) -> None:
        with mock.patch(
            "agents_remember.serving.harness_control_client.request_control",
            side_effect=HarnessControlClientError("response reset", may_have_sent=True),
        ) as request:
            result = set_control_model(_Entry(), "model-b")
        self.assertEqual(
            (result.ok, result.acceptance, result.requested_value),
            (False, "unknown", "model-b"),
        )
        request.assert_called_once()

    def test_native_page_serializes_thread_id_only_when_set(self) -> None:
        # The threadId field is additive on evidence-native-page — serialized only
        # when set, so a pre-multiplex consumer sees the byte-identical single-thread request.
        canned = {
            "frames": [],
            "nextCursor": None,
            "truncated": False,
            "bridgeEpoch": "epoch-1",
        }
        with mock.patch(
            "agents_remember.serving.harness_control_client.request_control",
            return_value=canned,
        ) as request:
            page = read_control_native_page(_Entry(), thread_id="agent-thread-1")
        self.assertEqual(page.bridge_epoch, "epoch-1")
        self.assertEqual(request.call_args.args[1], "evidence-native-page")
        self.assertEqual(
            request.call_args.args[2], {"limit": 200, "threadId": "agent-thread-1"}
        )

        with mock.patch(
            "agents_remember.serving.harness_control_client.request_control",
            return_value=canned,
        ) as request:
            read_control_native_page(_Entry(), cursor="entry-2")
        self.assertEqual(
            request.call_args.args[2], {"limit": 200, "cursor": "entry-2"}
        )


if __name__ == "__main__":
    unittest.main()
