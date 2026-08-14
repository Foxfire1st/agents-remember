from __future__ import annotations

import asyncio
import hashlib
import tempfile
import unittest
from pathlib import Path

from agents_remember.errors import HarnessBridgeEpochMismatchError, HarnessControlError
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    _interrupt_result,
    _operation_timeline,
    _withdrawal_result,
    read_operation_timeline,
    read_submission_authority,
    submit_control_prompt,
    withdraw_control_submission,
)
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from test_harness_control_plane import (
    NOW,
    _CapableAdapter,
    _ControlledEntry,
    _identity,
    _launch,
    _stage_asset,
)


class WithdrawalRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_recovery_crosses_once_then_tombstone_and_never_on_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("recovery-1")
            adapter = _CapableAdapter()
            bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
            await bridge.start(_launch(identity))
            endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
            server = HarnessControlServer(endpoint, bridge)
            await server.start()
            entry = _ControlledEntry(identity, endpoint.path)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-rec", "a1", b"png")
                # Queue two submissions so the first stays withdrawable behind the second.
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "first in flight",
                    ControlSubmission(
                        source="cockpit", request_id="req-head", expected_bridge_epoch=epoch
                    ),
                )
                await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "recovery body exact",
                    ControlSubmission(
                        source="cockpit",
                        request_id="req-rec",
                        expected_bridge_epoch=epoch,
                        assets=[staged],
                    ),
                )
                result = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="req-rec",
                )
                self.assertEqual(result.outcome, "withdrawn")
                assert result.recovery is not None
                self.assertEqual(result.recovery.text, "recovery body exact")
                self.assertEqual([asset.asset_id for asset in result.recovery.assets], ["a1"])
                self.assertEqual(
                    result.recovery.assets[0].sha256, hashlib.sha256(b"png").hexdigest()
                )
                replay = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="req-rec",
                )
                self.assertEqual(replay.outcome, "withdrawn")
                self.assertEqual(replay.withdrawn_at, result.withdrawn_at)
                self.assertIsNone(replay.recovery)
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                record = next(item for item in page.items if item.operation_id == "req-rec")
                self.assertEqual(record.state, "withdrawn")
                terminal = await asyncio.to_thread(
                    withdraw_control_submission,
                    entry,
                    expected_bridge_epoch=epoch,
                    request_id="req-terminal-missing",
                )
                self.assertEqual(terminal.outcome, "not-found")
                self.assertIsNone(terminal.recovery)
            finally:
                await server.close()
                await bridge.stop("forced")


class ClientValidationTests(unittest.TestCase):
    def test_interrupt_result_validation(self) -> None:
        with self.assertRaises(HarnessControlError):
            _interrupt_result(
                {"acknowledgement": "sometimes", "bridgeEpoch": "e"}, expected_bridge_epoch="e"
            )
        with self.assertRaises(HarnessBridgeEpochMismatchError):
            _interrupt_result(
                {"acknowledgement": "accepted", "bridgeEpoch": "other"},
                expected_bridge_epoch="e",
            )
        ok = _interrupt_result(
            {
                "acknowledgement": "accepted",
                "bridgeEpoch": "e",
                "operation": {
                    "bridgeEpoch": "e",
                    "operationSequence": 3,
                    "operationId": "op-3",
                    "operationKind": "prompt",
                },
                "vendorCorrelationId": "turn-9",
                "detail": None,
                "raw": {},
            },
            expected_bridge_epoch="e",
        )
        self.assertEqual(ok.operation.operation_id, "op-3")  # type: ignore[union-attr]

    def test_timeline_validation_battery(self) -> None:
        base_item = {
            "operationId": "op-1",
            "kind": "prompt",
            "source": "cockpit",
            "state": "queued",
            "sequence": 1,
            "submittedAt": NOW,
            "updatedAt": NOW,
            "acceptedAt": None,
            "payloadDigestPresent": True,
            "vendorCorrelationId": None,
        }

        def page(**overrides: object) -> dict[str, object]:
            base: dict[str, object] = {
                "bridgeEpoch": "e",
                "latestSequence": 2,
                "evictedBeforeSequence": 0,
                "truncated": False,
                "items": [dict(base_item), {**base_item, "operationId": "op-2", "sequence": 2}],
            }
            base.update(overrides)
            return base

        self.assertEqual(len(_operation_timeline(page(), expected_bridge_epoch="e").items), 2)
        for overrides in (
            {"items": [{**base_item, "kind": "set-theme"}]},
            {"items": [{**base_item, "source": "moon"}]},
            {"items": [{**base_item, "sequence": 0}]},
            {"items": [dict(base_item, sequence=2), dict(base_item, sequence=2)]},
            {
                "items": [dict(base_item, sequence=3), dict(base_item, sequence=2)],
                "latestSequence": 3,
            },
            {"evictedBeforeSequence": 5},
            {"truncated": "yes"},
            {"truncated": True, "items": []},
            {"latestSequence": 1},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(HarnessControlError):
                _operation_timeline(page(**overrides), expected_bridge_epoch="e")

    def test_withdrawal_recovery_validation(self) -> None:
        with self.assertRaises(HarnessControlError):
            _withdrawal_result(
                {"requestId": "r", "outcome": "withdrawn", "recovery": "text"},
                request_id="r",
            )
        ok = _withdrawal_result(
            {
                "requestId": "r",
                "outcome": "withdrawn",
                "state": "withdrawn",
                "withdrawnAt": NOW,
                "detail": None,
                "recovery": {
                    "text": "body",
                    "assets": [
                        {
                            "assetId": "a1",
                            "mimeType": "image/png",
                            "byteSize": 3,
                            "sha256": "0" * 64,
                        }
                    ],
                },
            },
            request_id="r",
        )
        assert ok.recovery is not None
        self.assertEqual(ok.recovery.assets[0].asset_id, "a1")
