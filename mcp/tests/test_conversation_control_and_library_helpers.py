"""Behavioural cover for the small mappers/decoders the conversation surfaces lean on.

These are the private helpers the bigger contract suites reach only through their happy
paths: attachment eviction and byte deletion, the withdrawal failure mapper, the Codex
command-block mapper, the locked helper-host wire protocol (exchange, line decoding,
failure mapping), the Pi library row mapper, and the active projector's poll loop. Every
test asserts a returned value, an on-disk side effect, or the exact typed refusal -- the
helper host is driven with an in-memory process double rather than a spawned helper, and
the poll loop with a scripted bridge rather than a live one.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from collections import OrderedDict
from pathlib import Path
from typing import cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.errors import (
    HarnessBridgeEpochMismatchError,
    HarnessControlError,
)
from agents_remember.serving.conversation.active.projector import facade as projector_facade
from agents_remember.serving.conversation.active.projector.facade import ProjectedSession
from agents_remember.serving.conversation.active.projector.wiring import BridgeReaders
from agents_remember.serving.conversation.control import attachments, withdrawals
from agents_remember.serving.conversation.control.asset_spool import AssetRecord
from agents_remember.serving.conversation.control.refs import OperationIdentity
from agents_remember.serving.conversation.control.service import (
    ControlChannel,
    OperationRejectedError,
)
from agents_remember.serving.conversation.library import helper_host
from agents_remember.serving.conversation.library.codex_normalize import _command_blocks
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.errors import (
    InvalidLibraryCursorError,
    LibraryStoreError,
    StaleNativeIdentityError,
)
from agents_remember.serving.conversation.library.normalize_common import TEXT_BLOCK_CAP
from agents_remember.serving.conversation.library.pi import PiConversationLibrary
from agents_remember.serving.conversation.library.scope import query_digest
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    AuthorizationBinding,
    CapabilityEvidence,
    ConversationContentBlock,
    ConversationLibraryScope,
    FeatureCapability,
    HistoryCapabilities,
    OperationFingerprint,
    ToolInputBlock,
    ToolOutputBlock,
    UnknownVendorBlock,
    operation_fingerprint,
)
from agents_remember.serving.conversation.projectors import projector_for
from agents_remember.serving.harness_control_client import ControlledSession
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    EvidenceFrame,
    EvidencePage,
    NativeEvidenceFrame,
    NativeEvidencePage,
    SubmissionProvenanceBatch,
    WithdrawalResult,
)

CALLER = AuthorizationBinding(principal_id="local-operator:1000", tenant_id="/ws")
EPOCH = "epoch-1"
NOW = "2026-07-30T08:00:00+00:00"
SECRET = b"s" * 32
SCOPE = "/ws"


# --------------------------------------------------------------------------------------
# control/attachments.py :: _evict_attachment_operation, _delete_operation_bytes
# --------------------------------------------------------------------------------------


def _fingerprint(request_id: str) -> OperationFingerprint:
    return operation_fingerprint("attachment-stage", CALLER, {"requestId": request_id})


def _asset(spool_path: Path, *, asset_id: str = "asset-1") -> AssetRecord:
    return AssetRecord(
        asset_id=asset_id,
        kind="image",
        name="dot.png",
        mime_type="image/png",
        size_bytes=3,
        sha256="0" * 64,
        alt="a dot",
        alt_provenance="supplied-description",
        spool_path=spool_path,
    )


def _operation(
    request_id: str,
    *,
    phase: attachments.AttachmentPhase,
    assets: list[AssetRecord] | None = None,
) -> attachments.AttachmentOperation:
    return attachments.AttachmentOperation(
        request_id=request_id,
        fingerprint=_fingerprint(request_id),
        revision=0,
        bridge_epoch=EPOCH,
        phase=phase,
        outcome="pending",
        assets=assets if assets is not None else [],
        expires_at="2026-07-30T09:00:00+00:00",
    )


class AttachmentEvictionTests(unittest.TestCase):
    """``_evict_attachment_operation`` frees exactly one terminal slot, or refuses."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.spool = Path(self._tmpdir.name)

    def _spooled(self, request_id: str) -> AssetRecord:
        directory = self.spool / request_id
        directory.mkdir()
        path = directory / "asset-1"
        path.write_bytes(b"png")
        return _asset(path)

    def test_evicts_a_terminal_operation_and_deletes_its_spooled_bytes(self) -> None:
        channel = ControlChannel()
        live_asset = self._spooled("req-live")
        dead_asset = self._spooled("req-dead")
        channel.attachments["req-live"] = _operation(
            "req-live", phase="staged", assets=[live_asset]
        )
        channel.attachments["req-dead"] = _operation(
            "req-dead", phase="accepted", assets=[dead_asset]
        )

        attachments._evict_attachment_operation(channel, "req-new")

        assert list(channel.attachments) == ["req-live"]
        assert not dead_asset.spool_path.exists()
        assert not dead_asset.spool_path.parent.exists()
        assert live_asset.spool_path.read_bytes() == b"png"

    def test_the_first_terminal_operation_in_insertion_order_is_the_victim(self) -> None:
        channel = ControlChannel()
        channel.attachments["req-a"] = _operation("req-a", phase="queued")
        channel.attachments["req-b"] = _operation("req-b", phase="failed")
        channel.attachments["req-c"] = _operation("req-c", phase="expired")

        attachments._evict_attachment_operation(channel, "req-new")

        assert list(channel.attachments) == ["req-a", "req-c"]

    def test_refuses_when_every_retained_operation_is_still_live(self) -> None:
        channel = ControlChannel()
        asset = self._spooled("req-1")
        channel.attachments["req-1"] = _operation("req-1", phase="staged", assets=[asset])
        channel.attachments["req-2"] = _operation("req-2", phase="dispatching")
        channel.attachments["req-3"] = _operation("req-3", phase="unknown")

        with self.assertRaises(OperationRejectedError) as caught:
            attachments._evict_attachment_operation(channel, "req-new")

        assert str(caught.exception) == "attachment operation store is full of live operations"
        assert list(channel.attachments) == ["req-1", "req-2", "req-3"]
        assert asset.spool_path.exists()

    def test_never_evicts_the_incoming_request_id(self) -> None:
        channel = ControlChannel()
        asset = self._spooled("req-new")
        channel.attachments["req-new"] = _operation("req-new", phase="expired", assets=[asset])

        with self.assertRaises(OperationRejectedError):
            attachments._evict_attachment_operation(channel, "req-new")

        assert list(channel.attachments) == ["req-new"]
        assert asset.spool_path.exists()

    def test_an_empty_store_has_no_victim(self) -> None:
        channel = ControlChannel()

        with self.assertRaises(OperationRejectedError):
            attachments._evict_attachment_operation(channel, "req-new")

        assert channel.attachments == OrderedDict()


class AttachmentOperationByteDeletionTests(unittest.TestCase):
    """``_delete_operation_bytes`` removes staged bytes and the emptied request dir."""

    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        self.root = Path(self._tmpdir.name)

    def test_deletes_every_asset_and_removes_the_emptied_request_directory(self) -> None:
        directory = self.root / "req-1"
        directory.mkdir()
        first = directory / "asset-1"
        second = directory / "asset-2"
        first.write_bytes(b"one")
        second.write_bytes(b"two")
        operation = _operation(
            "req-1",
            phase="expired",
            assets=[_asset(first, asset_id="asset-1"), _asset(second, asset_id="asset-2")],
        )

        attachments._delete_operation_bytes(operation)

        assert not first.exists()
        assert not second.exists()
        assert not directory.exists()

    def test_keeps_a_request_directory_that_still_holds_other_bytes(self) -> None:
        directory = self.root / "req-2"
        directory.mkdir()
        staged = directory / "asset-1"
        staged.write_bytes(b"one")
        stranger = directory / "not-an-asset"
        stranger.write_bytes(b"keep me")
        operation = _operation("req-2", phase="expired", assets=[_asset(staged)])

        attachments._delete_operation_bytes(operation)

        assert not staged.exists()
        assert directory.is_dir()
        assert stranger.read_bytes() == b"keep me"

    def test_an_operation_without_assets_touches_nothing(self) -> None:
        directory = self.root / "req-3"
        directory.mkdir()
        (directory / "asset-1").write_bytes(b"one")

        attachments._delete_operation_bytes(_operation("req-3", phase="staged"))

        assert sorted(child.name for child in directory.iterdir()) == ["asset-1"]

    def test_already_missing_bytes_are_tolerated(self) -> None:
        directory = self.root / "req-4"
        directory.mkdir()
        missing = directory / "asset-1"
        operation = _operation("req-4", phase="expired", assets=[_asset(missing)])

        attachments._delete_operation_bytes(operation)

        assert not directory.exists()


# --------------------------------------------------------------------------------------
# control/withdrawals.py :: _failure_for_result
# --------------------------------------------------------------------------------------


class WithdrawalFailureMappingTests(unittest.TestCase):
    """``_failure_for_result`` maps every non-withdrawn substrate outcome exactly."""

    def setUp(self) -> None:
        self.identity = OperationIdentity(kind="submit", operation_id="op-1", sequence=7)
        self.fingerprint = _fingerprint("wd-1")

    def _map(self, result: WithdrawalResult):
        return withdrawals._failure_for_result(
            withdrawals.WithdrawalTicket(
                epoch=EPOCH,
                identity=self.identity,
                operation_ref="opref-1",
                fingerprint=self.fingerprint,
                withdraw_request_id="wd-1",
            ),
            result,
        )

    def test_not_found_maps_to_a_settled_not_found_record(self) -> None:
        record = self._map(
            WithdrawalResult(
                request_id="req-1",
                outcome="not-found",
                state=None,
                detail="the ledger evicted it",
            )
        )

        assert record is not None
        assert record.outcome == "not-found"
        assert record.phase == "settled"
        assert record.detail == "the ledger evicted it"
        assert record.revision == 1
        assert record.bridge_epoch == EPOCH
        assert record.operation == self.identity
        assert record.recovery_state == "none"
        assert record.recovery_ref is None
        assert record.withdrawn_at is None
        assert record.response.outcome == "not-found"
        assert record.response.operation_ref == "opref-1"

    def test_not_found_without_a_detail_uses_the_authority_default(self) -> None:
        record = self._map(
            WithdrawalResult(request_id="req-1", outcome="not-found", state=None, detail=None)
        )

        assert record is not None
        assert record.detail == "submission is not retained for this cockpit authority"

    def test_not_withdrawable_while_dispatching_is_already_dispatching(self) -> None:
        record = self._map(
            WithdrawalResult(request_id="req-1", outcome="not-withdrawable", state="dispatching")
        )

        assert record is not None
        assert record.outcome == "already-dispatching"
        assert record.detail == "submission is already dispatching"

    def test_not_withdrawable_with_unknown_delivery_is_delivery_unknown(self) -> None:
        record = self._map(
            WithdrawalResult(request_id="req-1", outcome="not-withdrawable", state="unknown")
        )

        assert record is not None
        assert record.outcome == "delivery-unknown"
        assert record.detail == "submission is already unknown"

    def test_not_withdrawable_in_any_other_state_settles_as_not_found(self) -> None:
        record = self._map(
            WithdrawalResult(request_id="req-1", outcome="not-withdrawable", state="delivered")
        )

        assert record is not None
        assert record.outcome == "not-found"
        assert record.detail == "submission is already delivered"

    def test_a_substrate_detail_is_never_overwritten_by_the_default(self) -> None:
        record = self._map(
            WithdrawalResult(
                request_id="req-1",
                outcome="not-withdrawable",
                state="dispatching",
                detail="the runner already wrote it",
            )
        )

        assert record is not None
        assert record.outcome == "already-dispatching"
        assert record.detail == "the runner already wrote it"

    def test_a_withdrawn_result_is_not_a_failure(self) -> None:
        assert (
            self._map(
                WithdrawalResult(
                    request_id="req-1",
                    outcome="withdrawn",
                    state="withdrawn",
                    withdrawn_at=NOW,
                )
            )
            is None
        )


# --------------------------------------------------------------------------------------
# library/codex_normalize.py :: _command_blocks
# --------------------------------------------------------------------------------------


class CodexCommandBlocksTests(unittest.TestCase):
    """``_command_blocks`` renders a command item without guessing missing evidence."""

    @staticmethod
    def _input(blocks: tuple[ConversationContentBlock, ...]) -> ToolInputBlock:
        assert isinstance(blocks[0], ToolInputBlock)
        return blocks[0]

    @staticmethod
    def _output(blocks: tuple[ConversationContentBlock, ...]) -> ToolOutputBlock:
        assert isinstance(blocks[1], ToolOutputBlock)
        return blocks[1]

    def test_command_and_output_become_two_correlated_blocks(self) -> None:
        blocks = _command_blocks({"command": "ls -la", "output": "total 0"}, "item-9")

        assert [block.type for block in blocks] == ["tool-input", "tool-output"]
        assert self._input(blocks).block_id == "item-9:b0"
        assert self._input(blocks).summary == "ls -la"
        assert self._output(blocks).block_id == "item-9:b1"
        assert self._output(blocks).text == "total 0"

    def test_a_missing_command_is_named_unavailable_rather_than_guessed(self) -> None:
        for item in ({}, {"command": ""}, {"command": 12}, {"command": None}):
            with self.subTest(item=item):
                blocks = _command_blocks(item, "item-1")
                assert len(blocks) == 1
                assert self._input(blocks).summary == "(command unavailable)"

    def test_empty_or_non_text_output_produces_no_output_block(self) -> None:
        for item in (
            {"command": "true"},
            {"command": "true", "output": ""},
            {"command": "true", "output": ["not", "text"]},
        ):
            with self.subTest(item=item):
                blocks = _command_blocks(item, "item-2")
                assert [block.type for block in blocks] == ["tool-input"]

    def test_a_long_command_is_capped_with_a_visible_truncation_marker(self) -> None:
        blocks = _command_blocks({"command": "x" * 600, "output": "y" * (TEXT_BLOCK_CAP + 5)}, "i")

        assert self._input(blocks).summary == "x" * 512 + "\n…[truncated]"
        assert self._output(blocks).text == "y" * TEXT_BLOCK_CAP + "\n…[truncated]"


# --------------------------------------------------------------------------------------
# library/helper_host.py :: _exchange, _decode_lines, _raise_helper_failure
# --------------------------------------------------------------------------------------

REQUESTS = (
    {"protocolVersion": helper_host.HELPER_PROTOCOL_VERSION, "operation": "handshake"},
    {"protocolVersion": helper_host.HELPER_PROTOCOL_VERSION, "operation": "list"},
)


class _FakeHelperProcess:
    """The subprocess boundary as an in-memory pipe pair; nothing is ever spawned."""

    def __init__(
        self,
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        hang: bool = False,
    ) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._hang = hang
        self.received: bytes | None = None
        self.killed = False
        self.waited = False

    async def communicate(self, payload: bytes) -> tuple[bytes, bytes]:
        self.received = payload
        if self._hang:
            await asyncio.sleep(3600)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        self.waited = True
        return self.returncode


class _DoubledHost(helper_host.ConversationLibraryHelperHost):
    """The real host with only the spawn boundary replaced."""

    def __init__(self, process: _FakeHelperProcess, *, timeout_seconds: float = 30.0) -> None:
        super().__init__(
            node_executable="/usr/bin/node",
            root=Path("/helper-root"),
            timeout_seconds=timeout_seconds,
        )
        self.process = process
        self.argv: tuple[str, ...] | None = None

    async def _spawn(self, argv: tuple[str, ...]) -> asyncio.subprocess.Process:
        self.argv = argv
        return cast("asyncio.subprocess.Process", self.process)


class HelperHostExchangeTests(unittest.IsolatedAsyncioTestCase):
    """``_exchange`` writes the JSON-lines request pair and bounds every failure."""

    async def test_writes_json_lines_and_returns_the_decoded_responses(self) -> None:
        stdout = b'{"requestId":"a","status":"ok"}\n{"requestId":"b","status":"ok"}\n'
        host = _DoubledHost(_FakeHelperProcess(stdout=stdout))
        entry = Path("/helper-root/src/pi.ts")

        lines = await host._exchange("/usr/bin/node", entry, REQUESTS)

        assert lines == [
            {"requestId": "a", "status": "ok"},
            {"requestId": "b", "status": "ok"},
        ]
        assert host.argv == ("/usr/bin/node", "--import", "tsx", str(entry))
        assert host.process.received is not None
        written = host.process.received.decode("utf-8").splitlines()
        assert [json.loads(line) for line in written] == [dict(item) for item in REQUESTS]
        assert host.process.received.endswith(b"}\n")
        assert b" " not in host.process.received

    async def test_a_failed_helper_process_never_discloses_stderr(self) -> None:
        host = _DoubledHost(
            _FakeHelperProcess(
                stdout=b'{"requestId":"a"}\n',
                stderr=b"Error: /home/operator/.pi/sessions/secret.jsonl not readable",
                returncode=1,
            )
        )

        with self.assertRaises(LibraryStoreError) as caught:
            await host._exchange("/usr/bin/node", Path("/helper-root/src/pi.ts"), REQUESTS)

        assert str(caught.exception) == "locked helper process failed; raw detail withheld"
        assert "secret.jsonl" not in str(caught.exception)

    async def test_an_oversized_response_is_refused_by_the_byte_bound(self) -> None:
        oversized = b"x" * (helper_host._MAX_RESPONSE_BYTES + 1)
        host = _DoubledHost(_FakeHelperProcess(stdout=oversized, returncode=1))

        with self.assertRaises(LibraryStoreError) as caught:
            await host._exchange("/usr/bin/node", Path("/helper-root/src/pi.ts"), REQUESTS)

        # The byte bound is checked before the exit status, so an oversized response is
        # never decoded, whatever the helper's return code says.
        assert str(caught.exception) == "locked helper response exceeded the byte bound"

    async def test_a_hung_helper_is_killed_and_reported_as_a_timeout(self) -> None:
        process = _FakeHelperProcess(hang=True)
        host = _DoubledHost(process, timeout_seconds=0.01)

        with self.assertRaises(LibraryStoreError) as caught:
            await host._exchange("/usr/bin/node", Path("/helper-root/src/pi.ts"), REQUESTS)

        assert str(caught.exception) == "locked helper timed out"
        assert process.killed is True
        assert process.waited is True


class HelperLineDecodingTests(unittest.TestCase):
    """``_decode_lines`` proves the response count before anything is interpreted."""

    def test_blank_lines_are_ignored_and_every_object_is_decoded(self) -> None:
        stdout = b'{"a":1}\n\n   \n{"b":[2]}\n'

        assert helper_host._decode_lines(stdout, expected=2) == [{"a": 1}, {"b": [2]}]

    def test_too_few_lines_is_an_incomplete_response(self) -> None:
        with self.assertRaises(LibraryStoreError) as caught:
            helper_host._decode_lines(b'{"a":1}\n', expected=2)

        assert str(caught.exception) == "locked helper returned an incomplete response"

    def test_too_many_lines_is_an_incomplete_response(self) -> None:
        with self.assertRaises(LibraryStoreError) as caught:
            helper_host._decode_lines(b'{"a":1}\n{"b":2}\n{"c":3}\n', expected=2)

        assert str(caught.exception) == "locked helper returned an incomplete response"

    def test_an_empty_stream_is_an_incomplete_response(self) -> None:
        # A helper that died before writing anything must read as incomplete, not as malformed
        # JSON and not as zero results: the caller retries an incomplete response.
        with self.assertRaises(LibraryStoreError) as caught:
            helper_host._decode_lines(b"", expected=1)

        assert str(caught.exception) == "locked helper returned an incomplete response"

    def test_undecodable_bytes_are_replaced_rather_than_raised(self) -> None:
        assert helper_host._decode_lines(b'{"a":"\xff"}\n', expected=1) == [{"a": "�"}]

    def test_malformed_json_is_refused(self) -> None:
        with self.assertRaises(LibraryStoreError) as caught:
            helper_host._decode_lines(b"{not json}\n", expected=1)

        assert str(caught.exception) == "locked helper returned malformed JSON"

    def test_a_non_object_line_is_refused(self) -> None:
        with self.assertRaises(LibraryStoreError) as caught:
            helper_host._decode_lines(b"[1,2]\n", expected=1)

        assert str(caught.exception) == "locked helper response is not an object"


class HelperFailureMappingTests(unittest.TestCase):
    """``_raise_helper_failure`` types the helper's error and bounds its detail."""

    def test_stale_identity_raises_the_stale_native_identity_error(self) -> None:
        with self.assertRaises(StaleNativeIdentityError) as caught:
            helper_host._raise_helper_failure(
                {"error": "stale-identity", "detail": "the session file moved"}
            )

        assert str(caught.exception) == "the session file moved"

    def test_invalid_request_raises_the_cursor_error(self) -> None:
        with self.assertRaises(InvalidLibraryCursorError) as caught:
            helper_host._raise_helper_failure(
                {"error": "invalid-request", "detail": "cursor is not a number"}
            )

        assert str(caught.exception) == "cursor is not a number"

    def test_any_other_error_raises_the_store_error(self) -> None:
        with self.assertRaises(LibraryStoreError) as caught:
            helper_host._raise_helper_failure({"error": "io-failure", "detail": "disk went away"})

        assert type(caught.exception) is LibraryStoreError
        assert str(caught.exception) == "disk went away"

    def test_an_unnamed_error_still_raises_the_store_error(self) -> None:
        with self.assertRaises(LibraryStoreError) as caught:
            helper_host._raise_helper_failure({})

        assert str(caught.exception) == "helper request failed"

    def test_a_missing_or_non_text_detail_falls_back_to_fixed_copy(self) -> None:
        for line in (
            {"error": "stale-identity"},
            {"error": "stale-identity", "detail": ""},
            {"error": "stale-identity", "detail": 17},
            {"error": "stale-identity", "detail": None},
        ):
            with self.subTest(line=line), self.assertRaises(StaleNativeIdentityError) as caught:
                helper_host._raise_helper_failure(line)
            assert str(caught.exception) == "helper request failed"


# --------------------------------------------------------------------------------------
# library/pi.py :: PiConversationLibrary._row
# --------------------------------------------------------------------------------------


def _capabilities_value() -> HistoryCapabilities:
    evidence = CapabilityEvidence(
        runtime_version="0.80.7", fixture_id="test-fixture", observed_at=NOW
    )
    feature = FeatureCapability(
        state="supported",
        reason="test-supported",
        evidence_tier="runtime-fixture",
        evidence=evidence,
    )
    return HistoryCapabilities(
        list=feature,
        read=feature,
        resume=feature,
        completeness=feature,
        tool_completeness=feature,
    )


async def _capabilities(_harness: str) -> HistoryCapabilities:
    return _capabilities_value()


class PiLibraryRowTests(unittest.TestCase):
    """``PiConversationLibrary._row`` binds one native session row to a signed key."""

    def setUp(self) -> None:
        self.library = PiConversationLibrary(
            authorization=CALLER,
            cursor_authority=LibraryCursorAuthority(mint_signing_key()),
            capabilities=_capabilities,  # type: ignore[arg-type]
            helper_host=None,  # type: ignore[arg-type]
        )
        self.scope = ConversationLibraryScope(
            authorization=CALLER,
            harness_id="pi",
            canonical_project_scope=SCOPE,
            query_digest=query_digest("pi", SCOPE),
        )
        self.caps = _capabilities_value()

    def _row(self, raw: object):
        return self.library._row(raw, self.scope, generation=42, capabilities=self.caps)

    def test_binds_identity_digest_title_and_last_activity(self) -> None:
        row = self._row(
            {
                "sessionId": "sess-abcdef123456",
                "name": "  Refactor the parser  ",
                "firstMessage": "ignored once a name exists",
                "modified": "2026-07-29T10:00:00Z",
            }
        )

        expected_digest = self.library._cursor_authority.identity_digest(
            "pi", "sess-abcdef123456", SCOPE
        )
        assert row.identity_digest == expected_digest
        assert row.title == "Refactor the parser"
        assert row.safe_native_id_suffix == "123456"
        assert row.last_activity_at == "2026-07-29T10:00:00Z"
        assert row.capabilities == self.caps

        binding, vendor = self.library._cursor_authority.verify_conversation_key(
            row.conversation_key
        )
        assert vendor == "sess-abcdef123456"
        assert binding.catalog_generation == 42
        assert binding.identity_digest == expected_digest
        assert binding.scope == self.scope

    def test_falls_back_to_the_first_message_when_no_name_is_stored(self) -> None:
        row = self._row({"sessionId": "sess-1", "firstMessage": "why is the build red"})

        assert row.title == "why is the build red"

    def test_a_row_with_neither_name_nor_first_message_is_honestly_untitled(self) -> None:
        row = self._row({"sessionId": "sess-1", "name": "   ", "firstMessage": 5})

        assert row.title == "(untitled session)"

    def test_a_non_text_or_empty_modified_yields_no_last_activity(self) -> None:
        for raw in (
            {"sessionId": "sess-1"},
            {"sessionId": "sess-1", "modified": ""},
            {"sessionId": "sess-1", "modified": 1784035249127},
        ):
            with self.subTest(raw=raw):
                assert self._row(raw).last_activity_at is None

    def test_a_short_session_id_keeps_its_whole_suffix(self) -> None:
        assert self._row({"sessionId": "ab"}).safe_native_id_suffix == "ab"

    def test_a_row_that_is_not_an_object_is_refused(self) -> None:
        with self.assertRaises(LibraryStoreError) as caught:
            self._row(["sess-1"])

        assert str(caught.exception) == "Pi helper row is not an object"

    def test_a_row_without_a_usable_session_id_is_refused(self) -> None:
        for raw in ({}, {"sessionId": ""}, {"sessionId": 12}):
            with self.subTest(raw=raw), self.assertRaises(LibraryStoreError) as caught:
                self._row(raw)
            assert str(caught.exception) == "Pi helper response lacks sessionId"


# --------------------------------------------------------------------------------------
# active/projector/facade.py :: ActiveSessionProjector._run
# --------------------------------------------------------------------------------------


class _ControlledEntry:
    id = "ar-1"
    tmux_name = "ar-t-1"
    created_at = NOW
    control_endpoint = None


class _ScriptedBridge:
    """The substrate reads a projector performs, scripted in memory."""

    def __init__(self) -> None:
        self.evidence_frames: list[EvidenceFrame] = []
        self.native_frames: list[NativeEvidenceFrame] = []
        self.evidence_error: Exception | None = None
        self.latest_sequence: int | None = None
        self.evidence_reads = 0

    def push_evidence(self, kind: str, raw: dict[str, object]) -> None:
        self.evidence_frames.append(
            EvidenceFrame(
                sequence=len(self.evidence_frames) + 1, kind=kind, created_at=NOW, raw=raw
            )
        )

    def read_evidence(self, entry, *, after_sequence=0, limit=500, expected_bridge_epoch=None):
        del entry, expected_bridge_epoch
        self.evidence_reads += 1
        if self.evidence_error is not None:
            raise self.evidence_error
        frames = tuple(f for f in self.evidence_frames if f.sequence > after_sequence)
        latest = self.latest_sequence
        if latest is None:
            latest = self.evidence_frames[-1].sequence if self.evidence_frames else 0
        return EvidencePage(
            frames=frames[:limit],
            latest_sequence=latest,
            evicted_before_sequence=0,
            truncated=False,
            bridge_epoch=EPOCH,
        )

    def read_native_page(self, entry, *, cursor=None, limit=200, expected_bridge_epoch=None):
        del entry, cursor, limit, expected_bridge_epoch
        return NativeEvidencePage(
            frames=tuple(self.native_frames),
            next_cursor=None,
            truncated=False,
            bridge_epoch=EPOCH,
        )

    def read_transcript(self, entry, *, after_sequence=0, limit=500):
        del entry, after_sequence, limit
        return ()

    def read_provenance(self, entry, *, expected_bridge_epoch, request_ids):
        del entry, expected_bridge_epoch, request_ids
        return SubmissionProvenanceBatch(bridge_epoch=EPOCH, provenance=())

    def read_snapshot(self, entry) -> AdapterSnapshot:
        del entry
        return AdapterSnapshot(
            identity=ControlIdentity(ar_session_id="ar-1", tmux_name="ar-t-1", created_at=NOW),
            control="ready",
            activity="idle",
            acceptance="immediate",
            vendor_session_id="vendor-1",
            raw={},
        )


class ActiveProjectorPollLoopTests(unittest.IsolatedAsyncioTestCase):
    """``ActiveSessionProjector._run`` releases, gaps, or stops -- and never spins on."""

    def _projector(self, bridge: _ScriptedBridge):
        mapper = projector_for("codex")
        assert mapper is not None
        identity = ActiveConversationRef(
            harness_id="codex",
            vendor_conversation_id="vendor-1",
            project_scope="/workspace",
            identity_digest="digest-1",
            ar_session_id="ar-1",
            bridge_epoch=EPOCH,
        )
        projector = projector_facade.ActiveSessionProjector(
            ProjectedSession(
                identity=identity,
                authorization=AuthorizationBinding(
                    principal_id="local-operator:1000", tenant_id="/workspace"
                ),
                entry=cast("ControlledSession", _ControlledEntry()),
                mapper=mapper,
                secret=SECRET,
            ),
            clock=lambda: NOW,
            readers=BridgeReaders(
                evidence=bridge.read_evidence,
                native_page=bridge.read_native_page,
                transcript=bridge.read_transcript,
                provenance=bridge.read_provenance,
                snapshot=bridge.read_snapshot,
            ),
        )
        self.addAsyncCleanup(projector.close)
        return projector

    @staticmethod
    def _agent_turn(bridge: _ScriptedBridge) -> None:
        bridge.push_evidence(
            "codex-notification",
            {
                "threadId": "vendor-1",
                "turnId": "turn-1",
                "startedAtMs": 1,
                "item": {"id": "turn-1-agent", "type": "agentMessage", "text": "working"},
            },
        )

    @staticmethod
    async def _drain(queue: asyncio.Queue[object]) -> list[object]:
        drained: list[object] = []
        while not queue.empty():
            drained.append(queue.get_nowait())
        return drained

    async def _run_until_stopped(self, projector) -> asyncio.Queue[object]:
        """Subscribe (which starts the real poll task) and await the loop's own exit."""

        queue = projector.subscribe()
        task = projector._poll_task
        assert task is not None
        await asyncio.wait_for(task, timeout=10)
        return queue

    async def test_dormant_release_clears_the_projection_and_stops_the_loop(self) -> None:
        bridge = _ScriptedBridge()
        self._agent_turn(bridge)
        projector = self._projector(bridge)
        await projector.poll_once()
        assert projector.retained_after(0) != ()

        with mock.patch.object(projector_facade, "POLL_INTERVAL_SECONDS", 0.0):
            await projector._run()

        assert projector._closed is True
        assert projector.retained_after(0) == ()
        assert projector.retention_floor == 0
        assert projector.subscriber_count == 0
        assert projector._poll_task is None

    async def test_a_bridge_epoch_flip_emits_a_generation_changed_gap(self) -> None:
        bridge = _ScriptedBridge()
        bridge.evidence_error = HarnessBridgeEpochMismatchError(EPOCH, "epoch-2")
        projector = self._projector(bridge)

        with mock.patch.object(projector_facade, "POLL_INTERVAL_SECONDS", 0.0):
            queue = await self._run_until_stopped(projector)

        envelope, sentinel = await self._drain(queue)
        assert envelope.mutation.op == "gap"  # type: ignore[attr-defined]
        assert envelope.mutation.reason == "generation-changed"  # type: ignore[attr-defined]
        assert sentinel is projector_facade.CLOSE_SENTINEL
        assert projector._closed is True
        # The epoch arm is terminal on the first observation, not a counted read failure.
        assert projector._consecutive_failures == 0
        assert bridge.evidence_reads == 1

    async def test_a_regressed_evidence_timeline_emits_an_ordering_fault_gap(self) -> None:
        bridge = _ScriptedBridge()
        self._agent_turn(bridge)
        projector = self._projector(bridge)
        await projector.poll_once()

        bridge.evidence_frames = []
        bridge.latest_sequence = 0

        with mock.patch.object(projector_facade, "POLL_INTERVAL_SECONDS", 0.0):
            queue = await self._run_until_stopped(projector)

        envelope, sentinel = await self._drain(queue)
        assert envelope.mutation.reason == "ordering-fault"  # type: ignore[attr-defined]
        assert sentinel is projector_facade.CLOSE_SENTINEL
        assert projector._closed is True

    async def test_an_unmappable_native_frame_degrades_without_an_ordering_fault_gap(self) -> None:
        bridge = _ScriptedBridge()
        bridge.native_frames = [
            NativeEvidenceFrame(
                native_id="n1",
                native_parent_id="turn-1",
                native_type="agentMessage",
                created_at=NOW,
            )
        ]
        projector = self._projector(bridge)

        page = await projector.page(before_ordinal=None, limit=10)

        (item,) = page.items
        assert item.item_id == "n1"
        assert item.turn_id == "turn-1"
        (block,) = item.blocks
        assert isinstance(block, UnknownVendorBlock)
        assert block.vendor_type == "codex:malformed"
        assert projector._closed is False
        assert all(envelope.mutation.op != "gap" for envelope in projector.retained_after(0))

    async def test_repeated_control_failures_gap_only_at_the_read_failure_ceiling(self) -> None:
        bridge = _ScriptedBridge()
        bridge.evidence_error = HarnessControlError("bridge read failed")
        projector = self._projector(bridge)

        with mock.patch.object(projector_facade, "POLL_INTERVAL_SECONDS", 0.0):
            queue = await self._run_until_stopped(projector)

        ceiling = projector_facade.MAX_CONSECUTIVE_READ_FAILURES
        # The loop tolerated ceiling - 1 failures before gapping on the last one.
        assert projector._consecutive_failures == ceiling
        assert bridge.evidence_reads == ceiling
        envelope, sentinel = await self._drain(queue)
        assert envelope.mutation.reason == "generation-changed"  # type: ignore[attr-defined]
        assert sentinel is projector_facade.CLOSE_SENTINEL

    async def test_cancellation_stops_the_loop_without_a_gap(self) -> None:
        bridge = _ScriptedBridge()
        projector = self._projector(bridge)

        with mock.patch.object(projector_facade, "POLL_INTERVAL_SECONDS", 3600.0):
            queue = projector.subscribe()
            task = projector._poll_task
            assert task is not None
            await asyncio.sleep(0)
            task.cancel()
            await task

        assert projector._poll_task is None
        assert projector._closed is False
        assert queue.empty()
        assert bridge.evidence_reads == 0


if __name__ == "__main__":
    unittest.main()
