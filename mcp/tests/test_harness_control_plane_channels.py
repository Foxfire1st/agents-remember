from __future__ import annotations

import asyncio
import base64
import hashlib
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from typing import cast

from agents_remember.errors import (
    CodexAppServerError,
    HarnessControlError,
    HarnessRequestConflictError,
)
from agents_remember.models.conversations.control_wire import (
    AssetReference,
    ControlIdentity,
    ControlOperationRef,
)
from agents_remember.serving.codex_app_server_turns import turn_input as codex_turn_input
from agents_remember.serving.harness_control_bridge import HarnessControlBridge
from agents_remember.serving.harness_control_client import (
    ControlSubmission,
    read_operation_timeline,
    read_submission_authority,
    submit_control_prompt,
)
from agents_remember.serving.harness_control_ipc import HarnessControlServer, LocalControlEndpoint
from agents_remember.serving.harness_control_models import (
    PromptRequest,
)
from agents_remember.serving.pi_rpc_adapter import PiRpcAdapter
from test_harness_control_plane import (
    NOW,
    _CapableAdapter,
    _codex_adapter,
    _codex_fixture,
    _ControlledEntry,
    _drive_completions,
    _FakeCodexTransport,
    _FakePiTransport,
    _fixture_object,
    _identity,
    _launch,
    _obj,
    _PlainAdapter,
    _prime_codex_start,
    _stage_asset,
)


class AssetChannelTests(unittest.IsolatedAsyncioTestCase):
    async def _serve(self, adapter, identity: ControlIdentity, tmp: str):
        bridge = HarnessControlBridge(identity, adapter, clock=lambda: NOW)
        await bridge.start(_launch(identity))
        endpoint = LocalControlEndpoint.for_session(Path(tmp), identity)
        server = HarnessControlServer(endpoint, bridge)
        await server.start()
        return bridge, server, _ControlledEntry(identity, endpoint.path), endpoint

    async def test_schema_battery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-schema")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-ok", "a1", b"png-bytes")
                base: dict[str, object] = {
                    "text": "with asset",
                    "source": "cockpit",
                    "request_id": "req-ok",
                    "expected_bridge_epoch": epoch,
                }

                async def submit_with(**overrides: object):
                    kwargs = dict(base)
                    kwargs.update(overrides)
                    text = kwargs.pop("text")
                    return await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        text,  # type: ignore[arg-type]
                        ControlSubmission(**kwargs),  # type: ignore[arg-type]
                    )

                ok = await submit_with(assets=[staged])
                self.assertEqual(ok.acceptance, "immediate")
                self.assertEqual(
                    [ref.asset_id for ref in adapter.asset_submissions[0].assets], ["a1"]
                )
                bad_cases = [
                    {"assets": "not-a-list"},
                    {"assets": []},
                    {"assets": [staged] * 2},
                    {"assets": ["not-an-object"]},
                    {"assets": [{**staged, "mimeType": "application/pdf"}]},
                    {"assets": [{**staged, "byteSize": 0}]},
                    {"assets": [{**staged, "byteSize": 6 * 1024 * 1024}]},
                    {"assets": [{**staged, "byteSize": "big"}]},
                    {"assets": [{**staged, "sha256": "zz" * 32}]},
                    {"assets": [{**staged, "sha256": "AB" * 32}]},
                    {"assets": [staged, staged]},
                    {"assets": [{**staged, "byteSize": cast(int, staged["byteSize"]) + 1}]},
                    {"assets": [{**staged, "sha256": "0" * 64}]},
                ]
                for index, overrides in enumerate(bad_cases):
                    with self.subTest(case=index), self.assertRaises(HarnessControlError):
                        await submit_with(request_id=f"req-bad-{index}", **overrides)
                self.assertEqual(len(adapter.asset_submissions), 1)
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_traversal_battery_either_component(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-traversal")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-trav", "a1", b"png")
                bad_asset_ids = [
                    "../escape",
                    "/etc/passwd",
                    "a/b",
                    "a\\b",
                    ".",
                    "..",
                    "x" * 256,
                    "nul\0id",
                ]
                for bad in bad_asset_ids:
                    with self.subTest(assetId=bad), self.assertRaises(HarnessControlError):
                        await asyncio.to_thread(
                            submit_control_prompt,
                            entry,
                            "trav",
                            ControlSubmission(
                                source="cockpit",
                                request_id="req-trav",
                                expected_bridge_epoch=epoch,
                                assets=[{**staged, "assetId": bad}],
                            ),
                        )
                bad_request_ids = ["../escape", "a/b", ".", "..", "x" * 256]
                for bad in bad_request_ids:
                    with self.subTest(requestId=bad), self.assertRaises(HarnessControlError):
                        await asyncio.to_thread(
                            submit_control_prompt,
                            entry,
                            "trav",
                            ControlSubmission(
                                source="cockpit",
                                request_id=bad,
                                expected_bridge_epoch=epoch,
                                assets=[staged],
                            ),
                        )
                self.assertEqual(adapter.asset_submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_digest_and_size_verification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-verify")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-v", "a1", b"real-png-bytes")
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "v",
                        ControlSubmission(
                            source="cockpit",
                            request_id="req-v",
                            expected_bridge_epoch=epoch,
                            assets=[{**staged, "byteSize": cast(int, staged["byteSize"]) + 3}],
                        ),
                    )
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "v",
                        ControlSubmission(
                            source="cockpit",
                            request_id="req-v",
                            expected_bridge_epoch=epoch,
                            assets=[{**staged, "sha256": "f" * 64}],
                        ),
                    )
                missing = dict(staged)
                with self.assertRaises(HarnessControlError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "v",
                        ControlSubmission(
                            source="cockpit",
                            request_id="req-missing",
                            expected_bridge_epoch=epoch,
                            assets=[missing],
                        ),
                    )
                self.assertEqual(adapter.asset_submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_non_capable_adapter_returns_unsupported_and_timeline_marks_it(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-unsupported")
            adapter = _PlainAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                staged = _stage_asset(Path(tmp), "req-u", "a1", b"png")
                receipt = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "u",
                    ControlSubmission(
                        source="cockpit",
                        request_id="req-u",
                        expected_bridge_epoch=epoch,
                        assets=[staged],
                    ),
                )
                self.assertEqual(receipt.acceptance, "unsupported")
                self.assertIn("asset submissions", receipt.detail or "")
                page = await asyncio.to_thread(
                    read_operation_timeline, entry, expected_bridge_epoch=epoch
                )
                record = next(item for item in page.items if item.operation_id == "req-u")
                self.assertEqual(record.state, "unsupported")
                self.assertEqual(adapter.submissions, [])
            finally:
                await server.close()
                await bridge.stop("forced")

    async def test_idempotence_digest_covers_asset_identity_only_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            identity = _identity("asset-idem")
            adapter = _CapableAdapter()
            bridge, server, entry, _endpoint = await self._serve(adapter, identity, tmp)
            try:
                descriptor = await asyncio.to_thread(read_submission_authority, entry)
                epoch = descriptor.bridge_epoch
                first = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "same text",
                    ControlSubmission(
                        source="cockpit", request_id="req-idem", expected_bridge_epoch=epoch
                    ),
                )
                replay = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "same text",
                    ControlSubmission(
                        source="cockpit", request_id="req-idem", expected_bridge_epoch=epoch
                    ),
                )
                self.assertEqual(replay.acceptance, first.acceptance)
                await _drive_completions(adapter, ["req-idem"])
                # Same text, now with an asset: conflict, never a silent dedupe.
                staged = _stage_asset(Path(tmp), "req-idem", "a1", b"png")
                with self.assertRaises(HarnessRequestConflictError):
                    await asyncio.to_thread(
                        submit_control_prompt,
                        entry,
                        "same text",
                        ControlSubmission(
                            source="cockpit",
                            request_id="req-idem",
                            expected_bridge_epoch=epoch,
                            assets=[staged],
                        ),
                    )
                # Identical replay with the same asset set dedupes honestly.
                staged_b = _stage_asset(Path(tmp), "req-idem-b", "a1", b"png-b")
                first_b = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "asset text",
                    ControlSubmission(
                        source="cockpit",
                        request_id="req-idem-b",
                        expected_bridge_epoch=epoch,
                        assets=[staged_b],
                    ),
                )
                replay_b = await asyncio.to_thread(
                    submit_control_prompt,
                    entry,
                    "asset text",
                    ControlSubmission(
                        source="cockpit",
                        request_id="req-idem-b",
                        expected_bridge_epoch=epoch,
                        assets=[staged_b],
                    ),
                )
                self.assertEqual(replay_b.acceptance, first_b.acceptance)
                self.assertEqual(len(adapter.asset_submissions), 1)
            finally:
                await server.close()
                await bridge.stop("forced")


class AssetNativeConstructionTests(unittest.IsolatedAsyncioTestCase):
    def _asset_ref(self, root: Path, request_id: str, asset_id: str, data: bytes) -> AssetReference:
        _stage_asset(root, request_id, asset_id, data)
        return AssetReference(
            asset_id=asset_id,
            mime_type="image/png",
            byte_size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            spool_path=root / "assets" / request_id / asset_id,
        )

    async def test_codex_local_image_blocks_and_receipt_asset_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport = _FakeCodexTransport()
            _prime_codex_start(transport)
            adapter = _codex_adapter(transport)
            await adapter.start(_launch(_identity("codex-asset"), harness_id="codex"))
            try:
                data = _codex_fixture()
                turn_result = deepcopy(_fixture_object(data, "turnStartResult"))
                _fixture_object(turn_result, "turn")["id"] = "turn-assets"
                _fixture_object(turn_result, "turn")["status"] = "inProgress"
                transport.queue("turn/start", turn_result)
                ref = self._asset_ref(root, "req-img", "img-1", b"\x89PNG-fake")
                operation = ControlOperationRef(
                    bridge_epoch="e", sequence=1, operation_id="req-img", kind="prompt"
                )
                await adapter.preflight_operation(operation)
                receipt = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-img",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(receipt.acceptance, "immediate")
                turn_start = next(r for r in transport.requests if r[0] == "turn/start")
                blocks = _obj(turn_start[1])["input"]
                assert isinstance(blocks, list)
                self.assertEqual(_obj(blocks[0]), {"type": "text", "text": "see image"})
                self.assertEqual(
                    _obj(blocks[1]),
                    {"type": "localImage", "path": str(root / "assets" / "req-img" / "img-1")},
                )
                self.assertEqual(receipt.raw["assetIds"], ["img-1"])
                # Corrupt the staged file: pre-verification must reject with no native write.
                (root / "assets" / "req-img" / "img-1").write_bytes(b"corrupted")
                rejected = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-img-2",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(rejected.acceptance, "rejected")
                self.assertEqual(len([r for r in transport.requests if r[0] == "turn/start"]), 1)
            finally:
                await adapter.stop("forced")

    def test_an_asset_with_no_local_path_is_refused_before_the_native_write(self) -> None:
        """`spool_path` is runner-local and never serialized, so a rebuilt reference has none.

        A reference that came back over the wire -- or one a caller built from a receipt --
        carries the digest and the size but no staged file. Codex is handed a filesystem
        PATH for a local image, so there is nothing to hand it: the turn input refuses
        rather than building a block that names nothing, and the refusal is the same
        `CodexAppServerError` a corrupted staged file gets.
        """
        unstaged = AssetReference(
            asset_id="img-1",
            mime_type="image/png",
            byte_size=9,
            sha256="0" * 64,
        )

        with self.assertRaisesRegex(CodexAppServerError, "requires a verified spool path"):
            codex_turn_input(
                PromptRequest(
                    request_id="req-unstaged",
                    source="cockpit",
                    text="see image",
                    submitted_at=NOW,
                    assets=(unstaged,),
                )
            )

        # The text block alone is built without complaint, so the refusal is about the
        # asset rather than about the request.
        self.assertEqual(
            codex_turn_input(
                PromptRequest(
                    request_id="req-text",
                    source="cockpit",
                    text="no image",
                    submitted_at=NOW,
                )
            ),
            [{"type": "text", "text": "no image"}],
        )

    async def test_pi_image_content_blocks_and_receipt_asset_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transport = _FakePiTransport()
            adapter = PiRpcAdapter(transport_factory=lambda: transport, clock=lambda: NOW)
            await adapter.start(_launch(_identity("pi-asset"), harness_id="pi"))
            try:
                payload = b"\x89PNG-pi"
                ref = self._asset_ref(root, "req-pi-img", "img-1", payload)
                operation = ControlOperationRef(
                    bridge_epoch="e", sequence=1, operation_id="req-pi-img", kind="prompt"
                )
                await adapter.preflight_operation(operation)
                receipt = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-pi-img",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(receipt.acceptance, "immediate")
                prompt = next(c for c in transport.commands if c["type"] == "prompt")
                self.assertEqual(prompt["message"], "see image")
                images = prompt["images"]
                assert isinstance(images, list)
                self.assertEqual(
                    [_obj(image) for image in images],
                    [
                        {
                            "type": "image",
                            "mimeType": "image/png",
                            "data": base64.b64encode(payload).decode("ascii"),
                        }
                    ],
                )
                self.assertEqual(receipt.raw["assetIds"], ["img-1"])
                (root / "assets" / "req-pi-img" / "img-1").write_bytes(b"corrupted")
                rejected = await adapter.submit_with_assets(
                    PromptRequest(
                        request_id="req-pi-img-2",
                        source="cockpit",
                        text="see image",
                        submitted_at=NOW,
                        operation=operation,
                        assets=(ref,),
                    )
                )
                self.assertEqual(rejected.acceptance, "rejected")
                self.assertEqual(len([c for c in transport.commands if c["type"] == "prompt"]), 1)
            finally:
                await adapter.stop("forced")
