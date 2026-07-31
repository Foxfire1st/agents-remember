"""ConversationOpenService deep tests with doubled launch/proof/retire boundaries (260718-CHATS-L2)."""

from __future__ import annotations

import asyncio
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest import mock

from agents_remember.serving.conversation.library import open_service as open_module
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.errors import (
    OpenLedgerFullError,
    OpenRequestConflictError,
    StaleNativeIdentityError,
)
from agents_remember.serving.conversation.library.factories import LibraryShared
from agents_remember.serving.conversation.library.open_service import (
    ConversationOpenService,
    LibraryBinding,
    OpenOperationLedger,
    OpenRequest,
)
from agents_remember.serving.conversation.library.scope import canonical_library_scope
from agents_remember.serving.conversation.library.service import ConversationLibraryService
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    CapabilityEvidence,
    FeatureCapability,
    HarnessId,
    HistoryCapabilities,
    NativeConversationRef,
)
from agents_remember.serving.conversation.runtime import ConversationRuntime, ConversationScope
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    SubmissionAuthorityDescriptor,
)
from agents_remember.serving.hosted_readiness import HostedReadinessResult, HostedReadinessStatus
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import TerminalCatalogLivenessConfig, utc_now
from agents_remember.serving.terminal_opener import (
    OpenTerminalResult,
    SpawnProvenance,
    TerminalLaunchRequest,
)

CALLER = AuthorizationBinding(
    principal_id="local-operator:1000", tenant_id="/tmp/tenant-must-match"
)
VENDOR = "019f6607-2425-7dac-910b-9301dcd44871"


class _Host:
    def __init__(self) -> None:
        self.sessions: set[str] = set()
        self.terminated: list[str] = []

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.sessions

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:  # noqa: ARG002 - host protocol

        self.terminated.append(sid)


class _Gates:
    def __init__(self, resume_state: str = "supported") -> None:
        self.resume_state = resume_state

    async def history_capabilities(self, _harness_id: str) -> HistoryCapabilities:
        if self.resume_state == "supported":
            feature = FeatureCapability(
                state="supported",
                reason="gate passed",
                evidence_tier="runtime-fixture",
                evidence=CapabilityEvidence(
                    runtime_version="0.80.7",
                    fixture_id="gate-test",
                    observed_at="2026-07-18T00:00:00Z",
                ),
            )
        else:
            feature = FeatureCapability(
                state=self.resume_state,  # type: ignore[arg-type]
                reason="the AR session opener has no seam for this resume target",
                evidence_tier="none",
            )
        return HistoryCapabilities(
            list=feature,
            read=feature,
            resume=feature,
            completeness=feature,
            tool_completeness=feature,
        )


class _Port:
    def __init__(self, cursor: LibraryCursorAuthority, *, stale: bool = False) -> None:
        self.harness_id: HarnessId = "pi"
        self._cursor = cursor
        self._stale = stale
        self.resolved: list[NativeConversationRef] = []

    async def resolve_resume_target(self, ref: NativeConversationRef):
        self.resolved.append(ref)
        if self._stale:
            raise StaleNativeIdentityError("the native conversation vanished")
        scope = canonical_library_scope(CALLER, "pi", None, workspace_root=Path(ref.project_scope))
        return self._cursor.mint_resume_target(
            scope,
            vendor_conversation_id=ref.vendor_conversation_id,
            identity_digest=ref.identity_digest,
            catalog_generation=3,
            launch={"kind": "argv", "args": ["--session", "/home/x/.pi/sess.jsonl"]},
        )


class _CodexKindPort(_Port):
    """Resolve target of the codex L0E-channel kind instead of argv."""

    def __init__(
        self, cursor, *, harness: HarnessId = "codex", thread_id: str = "thr_exact_1"
    ) -> None:
        super().__init__(cursor)
        self.harness_id: HarnessId = harness
        self._thread_id = thread_id

    async def resolve_resume_target(self, ref: NativeConversationRef):
        self.resolved.append(ref)
        scope = canonical_library_scope(
            CALLER,
            self.harness_id,
            None,
            workspace_root=Path(ref.project_scope),  # type: ignore[arg-type]
        )
        return self._cursor.mint_resume_target(
            scope,
            vendor_conversation_id=ref.vendor_conversation_id,
            identity_digest=ref.identity_digest,
            catalog_generation=3,
            launch={"kind": "codex-thread-resume", "threadId": self._thread_id},
        )


class _Opener:
    def __init__(self, catalog: TerminalCatalog, host: _Host, *, fail: bool = False) -> None:
        self.catalog = catalog
        self.host = host
        self.fail = fail
        self.calls: list[Mapping[str, object]] = []

    def __call__(self, **kwargs: object) -> OpenTerminalResult:
        self.calls.append(kwargs)
        if self.fail:
            return OpenTerminalResult(status="bad-kind", detail="harness not installed")
        session_id = str(kwargs["session_id"])
        launch = kwargs["launch"]
        provenance = kwargs["provenance"]
        assert isinstance(launch, TerminalLaunchRequest)
        assert isinstance(provenance, SpawnProvenance)
        tmux_name = f"tmux-{session_id}"
        self.host.sessions.add(tmux_name)
        entry = TerminalCatalogEntry(
            id=session_id,
            label=str(provenance.label or session_id),
            kind="harness",
            harness=str(launch.harness),
            lifecycle_id=None,
            cwd=Path(str(launch.workspace_root)),
            tmux_name=tmux_name,
            command=("pi", "--mode", "rpc"),
            created_at="2026-07-18T00:00:00Z",
            last_attached_at="2026-07-18T00:00:00Z",
            status="running",
            control_endpoint=Path("/tmp/endpoint.sock"),
        )
        self.catalog.upsert(entry)
        return OpenTerminalResult(status="opened", entry=entry, kind="harness")


class _DedupeOpener(_Opener):
    """Mimics the real opener's live-row absorb: an existing live row is returned as-is."""

    def __call__(self, **kwargs: object) -> OpenTerminalResult:
        session_id = str(kwargs["session_id"])
        existing = self.catalog.get(session_id)
        if existing is not None and self.host.has_session(existing.tmux_name):
            return OpenTerminalResult(status="opened", entry=existing, kind="harness")
        return super().__call__(**kwargs)


def _readiness(
    status: HostedReadinessStatus, session_id: str, entry, snapshot
) -> HostedReadinessResult:
    return HostedReadinessResult(status, session_id, entry=entry, snapshot=snapshot)


def _snapshot(vendor: str | None, *, control: str = "ready") -> AdapterSnapshot:
    return AdapterSnapshot(
        identity=ControlIdentity(ar_session_id="x", tmux_name="t", created_at="c"),
        control=control,  # type: ignore[arg-type]
        activity="idle",  # type: ignore[arg-type]
        acceptance="immediate",  # type: ignore[arg-type]
        vendor_session_id=vendor,
    )


class OpenServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = _Host()
        self.cursor = LibraryCursorAuthority(mint_signing_key())
        self.ledger = OpenOperationLedger(limit=4)
        self.gates = _Gates()
        self.port = _Port(self.cursor)
        caller = AuthorizationBinding(
            principal_id=CALLER.principal_id, tenant_id=str(self.tmp.resolve())
        )
        self.caller = caller
        self.runtime = ConversationRuntime(
            scope=ConversationScope(workspace_root=self.tmp, coordination_root=self.tmp),
            catalog=self.catalog,
            host=self.host,  # type: ignore[arg-type]
            harness_registry=lambda: (),
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(self.tmp),
            authorization=_FixedResolver(caller),
        )
        self.shared = LibraryShared(
            cursor_authority=self.cursor,
            gates=self.gates,  # type: ignore[arg-type]
            helper_host=None,  # type: ignore[arg-type]
            open_ledger=self.ledger,
        )
        self.library = ConversationLibraryService(
            runtime=self.runtime,
            shared=self.shared,
            authorization=caller,
            port_builder=lambda _h: self.port,  # type: ignore[arg-type]
        )
        self.opener = _Opener(self.catalog, self.host)
        self.service = ConversationOpenService(
            LibraryBinding(runtime=self.runtime, shared=self.shared, authorization=caller),
            library=self.library,
            port_builder=lambda _h: self.port,  # type: ignore[arg-type]
            opener=self.opener,
            proof_wait_seconds=0.01,
        )
        self.key_token = self._mint_key()

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    # -- review F1 regression: pre-launch status/reconcile race --------------------

    def _blocking_service(self, gate: asyncio.Event) -> ConversationOpenService:
        blocking_port = _BlockingPort(self.cursor, gate)
        return ConversationOpenService(
            LibraryBinding(runtime=self.runtime, shared=self.shared, authorization=self.caller),
            library=ConversationLibraryService(
                runtime=self.runtime,
                shared=self.shared,
                authorization=self.caller,
                port_builder=lambda _h: blocking_port,  # type: ignore[arg-type]
            ),
            port_builder=lambda _h: blocking_port,  # type: ignore[arg-type]
            opener=self.opener,
            proof_wait_seconds=0.01,
        )

    async def test_prelaunch_poll_stays_pending_then_real_mismatch_retires_for_real(
        self,
    ) -> None:
        gate = asyncio.Event()
        service = self._blocking_service(gate)
        retired: list[str] = []
        real_retire = open_module.retire_entry

        def _retire(catalog, host, entry, *args, **kwargs):
            retired.append(entry.id)
            return real_retire(catalog, host, entry, *args, **kwargs)

        open_task = asyncio.create_task(
            service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-race",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        )
        for _ in range(100):  # let the drive reach the parked resolve (pre-launch)
            await asyncio.sleep(0.005)
            if ("pi", "req-race") in getattr(self.ledger, "_records", {}):
                break

        with mock.patch.object(open_module, "retire_entry", _retire):
            interim = await service.status("pi", self.key_token, request_id="req-race")
            # Pre-launch poll: never a terminal outcome, never a retirement claim.
            assert interim.outcome == "pending"
            assert interim.phase in {"requested", "launching"}
            assert retired == []
            assert (
                self.catalog.get(
                    str(self.opener.calls[0]["session_id"]) if self.opener.calls else "none"
                )
                is None
            )

            interim_reconcile = await service.reconcile("pi", self.key_token, request_id="req-race")
            assert interim_reconcile.outcome == "pending"

            # Release the drive into a wrong-vendor proof: the mismatch retires for real.
            gate.set()
            with (
                mock.patch.object(
                    open_module,
                    "hosted_session_readiness",
                    self._ready_with("wrong-vendor"),
                ),
                mock.patch.object(
                    open_module,
                    "read_submission_authority",
                    lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-race"),
                ),
            ):
                operation = await open_task

        assert operation.outcome == "identity-mismatch"
        assert operation.rollback == "retired"
        assert retired == [operation.ar_session_id]
        ar_session_id = operation.ar_session_id
        assert ar_session_id is not None
        entry = self.catalog.get(ar_session_id)
        assert entry is not None and entry.retired_at is not None

    async def test_absent_row_at_retire_reports_pending_and_reconcile_completes_it(
        self,
    ) -> None:
        retired: list[str] = []

        def _retire(catalog, host, entry, *_args, **_kwargs):
            retired.append(entry.id)

        real_get = self.catalog.get
        calls = {"n": 0}

        def _flaky_get(session_id: str):
            calls["n"] += 1
            # The readiness double bypasses this wrapper, so the flaky sequence is exactly:
            # n1 = launch absorbed-ownership check (real: row absent pre-spawn), n2 = the
            # retire attempt (must observe absent), n3+ = reconcile's successful retries.
            if calls["n"] == 2:
                return None
            return real_get(session_id)

        def _real_get_readiness(*_args, **kwargs):
            session_id = kwargs.get("session_id", "x")
            return HostedReadinessResult(
                "ready",
                session_id,
                entry=real_get(session_id),
                snapshot=_snapshot("wrong-vendor"),
            )

        with (
            mock.patch.object(open_module, "retire_entry", _retire),
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                _real_get_readiness,
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-p"),
            ),
            mock.patch.object(self.catalog, "get", side_effect=_flaky_get),
        ):
            operation = await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-pending",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
            # No false tombstone: the owed retirement is pending, visible, and unreconciled.
            assert operation.outcome == "identity-mismatch"
            assert operation.phase == "retiring"
            assert operation.rollback == "retire-pending"
            assert retired == []

            reconciled = await self.service.reconcile(
                "pi", self.key_token, request_id="req-pending"
            )
            assert reconciled.rollback == "retired"
            assert reconciled.phase == "failed"
            assert retired == [operation.ar_session_id]

    # -- held-open fix round (codex resume through the landed L0E channel) ------------

    def _codex_key(self, vendor: str = "thr_exact_1") -> tuple[str, str]:
        scope = canonical_library_scope(self.caller, "codex", None, workspace_root=self.tmp)
        digest = self.cursor.identity_digest("codex", vendor, scope.canonical_project_scope)
        return (
            str(
                self.cursor.mint_conversation_key(
                    scope,
                    vendor_conversation_id=vendor,
                    identity_digest=digest,
                    catalog_generation=3,
                )
            ),
            digest,
        )

    def _codex_service(self, port: _Port) -> ConversationOpenService:
        return ConversationOpenService(
            LibraryBinding(runtime=self.runtime, shared=self.shared, authorization=self.caller),
            library=ConversationLibraryService(
                runtime=self.runtime,
                shared=self.shared,
                authorization=self.caller,
                port_builder=lambda _h: port,  # type: ignore[arg-type]
            ),
            port_builder=lambda _h: port,  # type: ignore[arg-type]
            opener=self.opener,
            proof_wait_seconds=0.01,
        )

    async def test_codex_open_passes_resume_thread_id_through_the_channel(self) -> None:
        port = _CodexKindPort(self.cursor, harness="codex", thread_id="thr_exact_1")
        service = self._codex_service(port)
        key, digest = self._codex_key()
        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with("thr_exact_1"),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-cx"),
            ),
        ):
            operation = await service.open(
                "codex",
                key,
                OpenRequest(
                    request_id="req-codex-1",
                    expected_identity_digest=digest,
                    cwd=None,
                    launch_context={},
                ),
            )
        assert operation.outcome == "opened", operation.detail
        assert len(self.opener.calls) == 1
        launch: Any = self.opener.calls[0]["launch"]
        assert launch.harness == "codex"
        assert launch.control.resume_thread_id == "thr_exact_1"
        assert launch.knobs.launch_args == []
        assert operation.identity is not None
        assert operation.identity.vendor_conversation_id == "thr_exact_1"
        assert operation.identity.bridge_epoch == "epoch-cx"

    async def test_non_codex_open_never_carries_resume_thread_id(self) -> None:
        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with(VENDOR),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-pi"),
            ),
        ):
            operation = await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-pi-channel",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        assert operation.outcome == "opened", operation.detail
        launch: Any = self.opener.calls[0]["launch"]
        assert launch.harness == "pi"
        assert launch.control.resume_thread_id is None
        assert launch.knobs.launch_args == ["--session", "/home/x/.pi/sess.jsonl"]

    async def test_codex_open_with_invalid_resume_target_fails_typed(self) -> None:
        port = _CodexKindPort(self.cursor, harness="codex", thread_id="  ")
        service = self._codex_service(port)
        key, digest = self._codex_key()
        operation = await service.open(
            "codex",
            key,
            OpenRequest(
                request_id="req-codex-bad",
                expected_identity_digest=digest,
                cwd=None,
                launch_context={},
            ),
        )
        assert operation.outcome == "launch-failed"
        assert operation.identity is None
        assert not self.opener.calls

    async def test_codex_kind_target_on_non_codex_record_is_rejected(self) -> None:
        port = _CodexKindPort(self.cursor, harness="pi", thread_id="thr_exact_1")
        service = self._codex_service(port)
        operation = await service.open(
            "pi",
            self.key_token,
            OpenRequest(
                request_id="req-kind-mismatch",
                expected_identity_digest=self._digest(),
                cwd=None,
                launch_context={},
            ),
        )
        assert operation.outcome == "unsupported"
        assert operation.identity is None
        assert not self.opener.calls

    # -- review F5 regression: spawn ownership after ledger-record eviction -----------

    def _f5_service(self, opener: _Opener, port: _Port | None = None) -> ConversationOpenService:
        return ConversationOpenService(
            LibraryBinding(runtime=self.runtime, shared=self.shared, authorization=self.caller),
            library=ConversationLibraryService(
                runtime=self.runtime,
                shared=self.shared,
                authorization=self.caller,
                port_builder=lambda _h: port or self.port,  # type: ignore[arg-type]
            ),
            port_builder=lambda _h: port or self.port,  # type: ignore[arg-type]
            opener=opener,
            proof_wait_seconds=0.01,
        )

    def _opened_first_session_id(self) -> str:
        return str(self.opener.calls[0]["session_id"])

    async def _drive_first_open(self, service: ConversationOpenService):
        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with(VENDOR),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-first"),
            ),
        ):
            operation = await service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-f5",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        assert operation.outcome == "opened"
        return operation

    async def test_identical_replay_after_eviction_absorbs_and_opens(self) -> None:
        dedupe = _DedupeOpener(self.catalog, self.host)
        service = self._f5_service(dedupe)
        self.opener = dedupe
        first = await self._drive_first_open(service)
        first_session_id = self._opened_first_session_id()
        assert first.ar_session_id == first_session_id

        self.shared.open_ledger._records.clear()  # terminal record evicted (bounded LRU)

        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with(VENDOR),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-first"),
            ),
        ):
            replay = await service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-f5",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        # R4 idempotence survives eviction: the absorb re-opens the same session, no retire.
        assert replay.outcome == "opened"
        assert replay.ar_session_id == first_session_id
        assert replay.identity is not None
        assert replay.identity.vendor_conversation_id == VENDOR
        # Exactly one genuine spawn exists: the second record's launch was absorbed, so the
        # opener's spawn path never ran again and no second process/row exists.
        assert len(dedupe.calls) == 1
        assert len([name for name in self.host.sessions if first_session_id in name]) == 1
        entry = self.catalog.get(first_session_id)
        assert entry is not None and entry.status == "running" and entry.retired_at is None

    async def test_evicted_changed_conversation_never_retires_foreign_session(self) -> None:
        dedupe = _DedupeOpener(self.catalog, self.host)
        service = self._f5_service(dedupe)
        self.opener = dedupe
        await self._drive_first_open(service)
        first_session_id = self._opened_first_session_id()
        first_entry = self.catalog.get(first_session_id)
        assert first_entry is not None

        self.shared.open_ledger._records.clear()  # the first record is forgotten

        retired: list[str] = []

        def _retire(catalog, host, entry, *_args, **_kwargs):
            retired.append(entry.id)

        # The caller reuses the same requestId for a DIFFERENT conversation.
        other_vendor = "aaaaaaaa-1111-2222-3333-bbbbbbbbbbbb"
        scope = canonical_library_scope(self.caller, "pi", None, workspace_root=self.tmp)
        other_digest = self.cursor.identity_digest(
            "pi", other_vendor, scope.canonical_project_scope
        )
        other_key = str(
            self.cursor.mint_conversation_key(
                scope,
                vendor_conversation_id=other_vendor,
                identity_digest=other_digest,
                catalog_generation=4,
            )
        )
        self.port.resolved.clear()
        self.port._cursor = self.cursor

        with (
            mock.patch.object(open_module, "retire_entry", _retire),
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with(VENDOR),  # the live row still proves the FIRST conversation
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-first"),
            ),
        ):
            operation = await service.open(
                "pi",
                other_key,
                OpenRequest(
                    request_id="req-f5",
                    expected_identity_digest=other_digest,
                    cwd=None,
                    launch_context={},
                ),
            )
        # Honest settlement, never a retirement of the foreign live session.
        assert operation.outcome == "launch-failed"
        assert operation.identity is None
        assert operation.rollback == "not-needed"
        assert "left untouched" in (operation.detail or "")
        assert retired == []
        entry = self.catalog.get(first_session_id)
        assert entry is not None
        assert entry.status == "running" and entry.retired_at is None
        assert self.host.terminated == []

    def _ready_with(self, vendor: str | None):
        def _readiness(*_args, **kwargs):
            session_id = kwargs.get("session_id", "x")
            entry = self.catalog.get(session_id)
            return HostedReadinessResult(
                "ready", session_id, entry=entry, snapshot=_snapshot(vendor)
            )

        return _readiness

    def _mint_key(self) -> str:
        scope = canonical_library_scope(self.caller, "pi", None, workspace_root=self.tmp)
        digest = self.cursor.identity_digest("pi", VENDOR, scope.canonical_project_scope)
        return str(
            self.cursor.mint_conversation_key(
                scope,
                vendor_conversation_id=VENDOR,
                identity_digest=digest,
                catalog_generation=3,
            )
        )

    def _digest(self) -> str:
        scope = canonical_library_scope(self.caller, "pi", None, workspace_root=self.tmp)
        return self.cursor.identity_digest("pi", VENDOR, scope.canonical_project_scope)

    async def test_open_proves_exact_identity_and_replays_idempotently(self) -> None:
        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with(VENDOR),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-1"),
            ),
        ):
            operation = await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-1",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        assert operation.outcome == "opened"
        assert operation.phase == "opened"
        assert operation.rollback == "not-needed"
        assert operation.catalog_generation == 3
        identity = operation.identity
        assert identity is not None
        assert identity.vendor_conversation_id == VENDOR
        assert identity.bridge_epoch == "epoch-1"
        assert identity.ar_session_id.startswith("ar-open-")
        assert operation.ar_session_id == identity.ar_session_id
        assert len(self.opener.calls) == 1
        launch: Any = self.opener.calls[0]["launch"]
        assert launch.kind == "harness" and launch.harness == "pi"
        assert launch.knobs.launch_args == ["--session", "/home/x/.pi/sess.jsonl"]
        assert str(launch.workspace_root) == str(self.tmp)

        replay = await self.service.open(
            "pi",
            self.key_token,
            OpenRequest(
                request_id="req-1",
                expected_identity_digest=self._digest(),
                cwd=None,
                launch_context={},
            ),
        )
        assert replay == operation
        assert len(self.opener.calls) == 1

    async def test_changed_fingerprint_conflicts_without_launching(self) -> None:
        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with(VENDOR),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-1"),
            ),
        ):
            await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-1",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        # The same requestId under different launch context is a fingerprint conflict.
        with self.assertRaises(OpenRequestConflictError):
            await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-1",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={"leafKey": "different"},
                ),
            )
        assert len(self.opener.calls) == 1

    async def test_unsupported_resume_never_launches(self) -> None:
        self.gates.resume_state = "unavailable"
        operation = await self.service.open(
            "pi",
            self.key_token,
            OpenRequest(
                request_id="req-2",
                expected_identity_digest=self._digest(),
                cwd=None,
                launch_context={},
            ),
        )
        assert operation.outcome == "unsupported"
        assert operation.phase == "failed"
        assert operation.identity is None
        assert operation.rollback == "not-needed"
        assert not self.opener.calls

    async def test_stale_expected_digest_fails_before_launch(self) -> None:
        with self.assertRaises(StaleNativeIdentityError):
            await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-3",
                    expected_identity_digest="sha256:" + "0" * 64,
                    cwd=None,
                    launch_context={},
                ),
            )
        assert not self.opener.calls

    async def test_stale_native_resolve_maps_to_stale_identity(self) -> None:
        self.port._stale = True
        operation = await self.service.open(
            "pi",
            self.key_token,
            OpenRequest(
                request_id="req-4",
                expected_identity_digest=self._digest(),
                cwd=None,
                launch_context={},
            ),
        )
        assert operation.outcome == "stale-identity"
        assert operation.identity is None
        assert not self.opener.calls

    async def test_launch_failure_maps_to_503_shape_and_no_identity(self) -> None:
        self.opener.fail = True
        operation = await self.service.open(
            "pi",
            self.key_token,
            OpenRequest(
                request_id="req-5",
                expected_identity_digest=self._digest(),
                cwd=None,
                launch_context={},
            ),
        )
        assert operation.outcome == "launch-failed"
        assert operation.identity is None
        assert operation.rollback == "not-needed"

    async def test_identity_mismatch_retires_and_reports(self) -> None:
        retired: list[str] = []

        def _retire(catalog, host, entry, *_args, **_kwargs):
            retired.append(entry.id)

        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with("different-vendor"),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-9"),
            ),
            mock.patch.object(open_module, "retire_entry", _retire),
        ):
            operation = await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-6",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        assert operation.outcome == "identity-mismatch"
        assert operation.rollback == "retired"
        assert operation.identity is not None
        assert operation.identity.vendor_conversation_id == "different-vendor"
        assert operation.catalog_generation == 3
        assert retired == [operation.ar_session_id]

    async def test_timeout_unknown_stays_reconcilable_and_opens_later(self) -> None:
        ready = {"value": False}

        def _readiness(*_args, **kwargs):
            if not ready["value"]:
                return HostedReadinessResult("not-ready", "x", entry=None, snapshot=None)
            session_id = kwargs.get("session_id", "x")
            return HostedReadinessResult(
                "ready",
                session_id,
                entry=self.catalog.get(session_id),
                snapshot=_snapshot(VENDOR),
            )

        with (
            mock.patch.object(open_module, "hosted_session_readiness", _readiness),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-2"),
            ),
        ):
            operation = await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-7",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
            assert operation.outcome == "timeout-unknown"
            assert operation.phase == "catalog-wait"
            assert operation.identity is None
            first_revision = operation.revision

            pending = await self.service.status("pi", self.key_token, request_id="req-7")
            assert pending.outcome == "timeout-unknown"
            assert pending.revision == first_revision  # polling never advances revision

            ready["value"] = True
            reconciled = await self.service.reconcile("pi", self.key_token, request_id="req-7")
            assert reconciled.outcome == "opened"
            assert reconciled.revision > first_revision
            assert reconciled.identity is not None
            assert reconciled.identity.bridge_epoch == "epoch-2"

    async def test_ledger_full_of_live_operations_refuses(self) -> None:
        with mock.patch.object(
            open_module,
            "hosted_session_readiness",
            lambda *a, **k: HostedReadinessResult("not-ready", "x", entry=None, snapshot=None),
        ):
            for index in range(4):
                await self.service.open(
                    "pi",
                    self.key_token,
                    OpenRequest(
                        request_id=f"req-live-{index}",
                        expected_identity_digest=self._digest(),
                        cwd=None,
                        launch_context={},
                    ),
                )
            with self.assertRaises(OpenLedgerFullError):
                await self.service.open(
                    "pi",
                    self.key_token,
                    OpenRequest(
                        request_id="req-live-overflow",
                        expected_identity_digest=self._digest(),
                        cwd=None,
                        launch_context={},
                    ),
                )
        assert self.ledger.retained_record_count == 4

    async def test_terminal_records_evict_for_new_operations(self) -> None:
        with (
            mock.patch.object(
                open_module,
                "hosted_session_readiness",
                self._ready_with(VENDOR),
            ),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="e"),
            ),
        ):
            for index in range(4):
                await self.service.open(
                    "pi",
                    self.key_token,
                    OpenRequest(
                        request_id=f"req-done-{index}",
                        expected_identity_digest=self._digest(),
                        cwd=None,
                        launch_context={},
                    ),
                )
            operation = await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-done-fresh",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
            assert operation.outcome == "opened"
        assert self.ledger.retained_record_count == 4

    async def test_ready_without_vendor_identity_stays_reconcilable_not_retired(self) -> None:
        # A ready bridge that has not published a native identity yet is not mismatch proof:
        # the operation stays timeout-unknown and the spawned session is left running.
        with mock.patch.object(
            open_module,
            "hosted_session_readiness",
            lambda *a, **k: HostedReadinessResult(
                "ready",
                k.get("session_id", "x"),
                entry=self.catalog.get(k.get("session_id", "x")),
                snapshot=_snapshot(None),
            ),
        ):
            operation = await self.service.open(
                "pi",
                self.key_token,
                OpenRequest(
                    request_id="req-novendor",
                    expected_identity_digest=self._digest(),
                    cwd=None,
                    launch_context={},
                ),
            )
        assert operation.outcome == "timeout-unknown"
        assert operation.identity is None
        session_id = str(self.opener.calls[0]["session_id"])
        entry = self.catalog.get(session_id)
        assert entry is not None and entry.status == "running" and entry.retired_at is None

    async def test_existing_catalog_rows_are_never_touched(self) -> None:
        other = TerminalCatalogEntry(
            id="other-session",
            label="other",
            kind="harness",
            harness="codex",
            lifecycle_id=None,
            cwd=self.tmp,
            tmux_name="other-tmux",
            command=("codex",),
            created_at="2026-07-18T00:00:00Z",
            last_attached_at="2026-07-18T00:00:00Z",
            status="running",
        )
        self.catalog.upsert(other)
        before = self.catalog.get("other-session")
        self.opener.fail = True
        await self.service.open(
            "pi",
            self.key_token,
            OpenRequest(
                request_id="req-8",
                expected_identity_digest=self._digest(),
                cwd=None,
                launch_context={},
            ),
        )
        after = self.catalog.get("other-session")
        assert after == before
        assert after is not None and after.status == "running" and after.retired_at is None


class _BlockingPort(_Port):
    """Resolve gate: holds the open drive before launch until the test releases it."""

    def __init__(self, cursor, gate: asyncio.Event) -> None:
        super().__init__(cursor)
        self._gate = gate

    async def resolve_resume_target(self, ref: NativeConversationRef):
        await self._gate.wait()
        return await super().resolve_resume_target(ref)


class _FixedResolver:
    def __init__(self, binding: AuthorizationBinding) -> None:
        self._binding = binding

    def resolve(self, *, client_host: str | None) -> AuthorizationBinding:  # noqa: ARG002 - protocol signature

        return self._binding

    def require(self, authorization: AuthorizationBinding) -> None:
        if authorization != self._binding:
            raise AssertionError("unexpected cross-principal binding in test")


if __name__ == "__main__":
    unittest.main()
