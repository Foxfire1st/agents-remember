"""Retry-safety tests for the blocking exact-session control client."""

from __future__ import annotations

import sys
import unittest
from dataclasses import dataclass
from pathlib import Path
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import HarnessControlClientError
from agents_remember.serving.harness_control_client import (
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


class HarnessControlClientRetrySafetyTests(unittest.TestCase):
    def test_refused_control_socket_yields_honest_note_and_unlinks_stale_socket(self) -> None:
        # 260718-CHATS-L5F R6: a controlled runner that exited uncleanly leaves a stale socket that
        # refuses (ECONNREFUSED). The client must render a designed lifecycle note WITHOUT the raw
        # "[Errno 111] Connection refused" surprise, and unlink the stale socket so the next attempt
        # sees the absent (ENOENT) case cleanly. (developer image1 banner)
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


if __name__ == "__main__":
    unittest.main()
