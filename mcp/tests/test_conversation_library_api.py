"""Registered library route tests over real ASGI with a loopback peer (260718-CHATS-L2).

Drives the actual FastAPI composition (root router + L0 runtime) through its ASGI interface so
routing, validation, camel-case wire shape, and the precise O4 error-status ladder are all
covered on the production path. Native boundaries (ports, opener, proof, retire) are doubled;
the installed-runtime suite covers them live.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest import mock
from urllib.parse import urlencode

from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.library import api as library_api
from agents_remember.serving.conversation.library import factories
from agents_remember.serving.conversation.library import open_service as open_module
from agents_remember.serving.conversation.library.cursor import (
    LibraryCursorAuthority,
    mint_signing_key,
)
from agents_remember.serving.conversation.library.errors import LibraryStoreError
from agents_remember.serving.conversation.library.factories import LibraryShared
from agents_remember.serving.conversation.library.open_service import OpenOperationLedger
from agents_remember.serving.conversation.library.scope import canonical_library_scope
from agents_remember.serving.conversation.library.service import ConversationLibraryService
from agents_remember.serving.conversation.models import (
    AuthorizationBinding,
    CapabilityEvidence,
    ConversationItem,
    ConversationLibraryPage,
    ConversationLibraryPageScope,
    ConversationLibraryRow,
    FeatureCapability,
    HarnessId,
    HistoricalConversationPage,
    HistoryCapabilities,
    NativeConversationRef,
    ProvenanceEvidence,
    TextBlock,
)
from agents_remember.serving.conversation.router import register_conversation_routes
from agents_remember.serving.conversation.runtime import ConversationRuntime, ConversationScope
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_models import (
    AdapterSnapshot,
    ControlIdentity,
    SubmissionAuthorityDescriptor,
)
from agents_remember.serving.hosted_readiness import HostedReadinessResult
from agents_remember.serving.terminal_catalog import TerminalCatalog, TerminalCatalogEntry
from agents_remember.serving.terminal_liveness import TerminalCatalogLivenessConfig, utc_now
from agents_remember.serving.terminal_opener import OpenTerminalResult
from fastapi import FastAPI
from starlette.types import Message, Scope


def _snapshot(vendor: str | None):
    return AdapterSnapshot(
        identity=ControlIdentity(ar_session_id="x", tmux_name="t", created_at="c"),
        control="ready",  # type: ignore[arg-type]
        activity="idle",  # type: ignore[arg-type]
        acceptance="immediate",  # type: ignore[arg-type]
        vendor_session_id=vendor,
    )


class _Host:
    def __init__(self) -> None:
        self.sessions: set[str] = set()

    def has_session(self, tmux_name: str) -> bool:
        return tmux_name in self.sessions

    def terminate(self, sid: str, *, tmux_name: str | None = None) -> None:
        self.sessions.discard(tmux_name or sid)


class _Gates:
    def __init__(self, resume_state: str = "supported") -> None:
        self.resume_state = resume_state

    async def history_capabilities(self, _harness_id: str):
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
                reason="resume target has no production launch seam",
                evidence_tier="none",
            )
        return HistoryCapabilities(
            list=feature,
            read=feature,
            resume=feature,
            completeness=feature,
            tool_completeness=feature,
        )


def _supported_caps():
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
    return HistoryCapabilities(
        list=feature,
        read=feature,
        resume=feature,
        completeness=feature,
        tool_completeness=feature,
    )


_SUPPORTED_CAPS = _supported_caps()


async def asgi_request(
    app: FastAPI,
    method: str,
    path: str,
    *,
    query: Mapping[str, str] | None = None,
    body: Mapping[str, object] | None = None,
    client: tuple[str, int] | None = ("127.0.0.1", 5000),
) -> tuple[int, object]:
    payload = json.dumps(body).encode("utf-8") if body is not None else b""
    headers = [(b"host", b"testserver")]
    if body is not None:
        headers.append((b"content-type", b"application/json"))
    scope: Scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": urlencode(query or {}).encode("ascii"),
        "headers": headers,
        "client": client,
        "server": ("testserver", 80),
        "app": app,
        "state": {},
    }
    messages: list[Message] = []
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": payload, "more_body": False}

    async def send(message: Message) -> None:
        messages.append(message)

    await app(scope, receive, send)
    status = next(
        int(message["status"]) for message in messages if message["type"] == "http.response.start"
    )
    raw = b"".join(
        bytes(message.get("body", b""))  # type: ignore[arg-type]
        for message in messages
        if message["type"] == "http.response.body"
    )
    return status, json.loads(raw) if raw else None


class _FakePort:
    """Scriptable port double enforcing the same scope/cursor checks as the real ports."""

    def __init__(
        self,
        harness_id: HarnessId,
        cursor: LibraryCursorAuthority,
        caller,
        workspace: Path,
    ):
        self.harness_id: HarnessId = harness_id
        self.cursor = cursor
        self.caller = caller
        self.workspace = workspace
        self.list_calls: list[dict[str, Any]] = []
        self.read_calls: list[dict[str, Any]] = []
        self.resolve_calls: list[NativeConversationRef] = []

    async def list(self, scope, *, cursor, limit):
        self.list_calls.append({"scope": scope, "cursor": cursor, "limit": limit})
        digest = self.cursor.identity_digest(
            self.harness_id, "vendor-1", scope.canonical_project_scope
        )
        return ConversationLibraryPage(
            scope=ConversationLibraryPageScope(
                harness_id=self.harness_id,  # type: ignore[arg-type]
                canonical_project_scope=scope.canonical_project_scope,
                query_digest=scope.query_digest,
            ),
            rows=(
                ConversationLibraryRow(
                    conversation_key=self.cursor.mint_conversation_key(
                        scope,
                        vendor_conversation_id="vendor-1",
                        identity_digest=digest,
                        catalog_generation=2,
                    ),
                    identity_digest=digest,
                    title="fake row",
                    safe_native_id_suffix="dor-1",
                    last_activity_at="2026-07-18T00:00:00Z",
                    capabilities=_SUPPORTED_CAPS,
                ),
            ),
            next_cursor=None,
        )

    async def read(self, ref, *, before, limit):
        self.read_calls.append({"ref": ref, "before": before, "limit": limit})
        return HistoricalConversationPage(
            ref=ref,
            items=(
                ConversationItem(
                    item_id="i1",
                    revision=1,
                    global_ordinal=1,
                    lane="unknown-input",
                    source="native-history",
                    provenance=ProvenanceEvidence(strength="native-only", origin="fake"),
                    role="user",
                    kind="message",
                    phase="completed",
                    blocks=(TextBlock(block_id="i1:b0", text="hi"),),
                ),
            ),
            older_cursor=None,
            has_older=False,
            total_items=1,
            historical_capabilities=_SUPPORTED_CAPS,
        )

    async def resolve_resume_target(self, ref):
        self.resolve_calls.append(ref)
        scope = canonical_library_scope(
            self.caller, self.harness_id, None, workspace_root=self.workspace
        )
        return self.cursor.mint_resume_target(
            scope,
            vendor_conversation_id=ref.vendor_conversation_id,
            identity_digest=ref.identity_digest,
            catalog_generation=2,
            launch={"kind": "argv", "args": ["--session", "/x.jsonl"]},
        )


class LibraryApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmpdir.name)
        self.catalog = TerminalCatalog(self.tmp / "terminal-sessions.json")
        self.host = _Host()
        self.resolver = LocalOperatorAuthorizationResolver.for_workspace(self.tmp)
        self.runtime = ConversationRuntime(
            scope=ConversationScope(workspace_root=self.tmp, coordination_root=self.tmp),
            catalog=self.catalog,
            host=self.host,  # type: ignore[arg-type]
            harness_registry=lambda: (),
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(self.tmp),
            authorization=self.resolver,
        )
        self.cursor = LibraryCursorAuthority(mint_signing_key())
        self.caller = self.resolver.resolve(client_host="127.0.0.1")
        self.gates = _Gates()
        self.shared = LibraryShared(
            cursor_authority=self.cursor,
            gates=self.gates,  # type: ignore[arg-type]
            helper_host=None,  # type: ignore[arg-type]
            open_ledger=OpenOperationLedger(),
        )
        self.port = _FakePort("pi", self.cursor, self.caller, self.tmp)
        self.opener_calls: list[Mapping[str, object]] = []
        self.app = FastAPI()
        register_conversation_routes(self.app, self.runtime)
        self._patches = [
            mock.patch.object(factories, "library_shared", lambda _runtime: self.shared),
            mock.patch.object(factories, "build_port", lambda *_a, **_k: self.port),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self) -> None:
        for patch in self._patches:
            patch.stop()
        self._tmpdir.cleanup()

    # -- list / read ---------------------------------------------------------

    async def test_list_route_returns_wire_page_and_authorizes_scope(self) -> None:
        status, body = await asgi_request(
            self.app, "GET", "/api/harnesses/pi/conversations", query={"limit": "5"}
        )
        assert status == 200, body
        assert body["scope"]["harnessId"] == "pi"  # type: ignore[index]
        row = body["rows"][0]  # type: ignore[index]
        assert row["conversationKey"].startswith("ar-lck1.")
        assert row["identityDigest"].startswith("sha256:")
        assert row["capabilities"]["list"]["state"] == "supported"
        assert body["nextCursor"] is None  # type: ignore[index]
        call = self.port.list_calls[0]
        assert call["limit"] == 5
        assert call["scope"].canonical_project_scope == str(self.tmp.resolve())

    async def test_list_route_narrows_scope_and_clamps_limit(self) -> None:
        child = self.tmp / "sub"
        child.mkdir()
        status, _body = await asgi_request(
            self.app,
            "GET",
            "/api/harnesses/pi/conversations",
            query={"cwd": str(child), "limit": "500"},
        )
        assert status == 200
        call = self.port.list_calls[0]
        assert call["scope"].canonical_project_scope == str(child.resolve())
        assert call["limit"] == 100

    async def test_list_route_maps_null_byte_cwd_to_typed_refusal(self) -> None:
        # Review F2: an embedded NUL must surface as the typed scope refusal, never a raw 500.
        status, body = await asgi_request(
            self.app,
            "GET",
            "/api/harnesses/pi/conversations",
            query={"cwd": "sub\\x00dir"},
        )
        assert status == 403, body
        assert body["status"] == "scope-denied"  # type: ignore[index]

    async def test_list_route_rejects_scope_escapes_and_unknown_harness(self) -> None:
        for query in ({"cwd": ".."}, {"cwd": "/etc"}, {"cwd": "missing"}):
            status, body = await asgi_request(
                self.app, "GET", "/api/harnesses/pi/conversations", query=query
            )
            assert status == 403, (query, body)
            assert body["status"] == "scope-denied"  # type: ignore[index]
        status, body = await asgi_request(self.app, "GET", "/api/harnesses/tmux/conversations")
        assert status == 404
        assert body["status"] == "unknown-harness"  # type: ignore[index]

    async def test_list_route_maps_malformed_cursor_and_capability(self) -> None:
        status, body = await asgi_request(
            self.app, "GET", "/api/harnesses/pi/conversations", query={"cursor": "garbage"}
        )
        assert status == 400
        assert body["status"] == "invalid-cursor"  # type: ignore[index]

        self.gates.resume_state = "unavailable"  # gates report the whole surface unavailable
        self.gates.list_state = "unavailable"  # type: ignore[attr-defined]
        self.gates.history_capabilities = (  # type: ignore[method-assign]
            _unavailable_gates_history
        )
        status, body = await asgi_request(self.app, "GET", "/api/harnesses/pi/conversations")
        assert status == 422
        assert body["status"] == "capability-unavailable"  # type: ignore[index]
        assert body["capabilityState"] == "unavailable"  # type: ignore[index]

    async def test_list_route_maps_store_errors_to_503(self) -> None:
        async def _failing_list(scope, *, cursor, limit):
            raise LibraryStoreError("Codex native payload failed shape validation: nope")

        self.port.list = _failing_list  # type: ignore[method-assign]
        status, body = await asgi_request(self.app, "GET", "/api/harnesses/pi/conversations")
        assert status == 503
        assert body["status"] == "library-unavailable"  # type: ignore[index]

    async def test_list_route_maps_store_errors_to_503_for_each_harness(self) -> None:
        # Review F4 route pins: the typed store failure maps to 503 on every harness path.
        async def _failing_list(scope, *, cursor, limit):
            raise LibraryStoreError("native payload has an out-of-range timestamp")

        self.port.list = _failing_list  # type: ignore[method-assign]
        for harness in ("codex", "claude", "pi"):
            self.port.harness_id = harness
            status, body = await asgi_request(
                self.app, "GET", f"/api/harnesses/{harness}/conversations"
            )
            assert status == 503, (harness, body)
            assert body["status"] == "library-unavailable"  # type: ignore[index]

    async def test_read_route_returns_historical_page(self) -> None:
        key = self._mint_key()
        status, body = await asgi_request(
            self.app, "GET", f"/api/harnesses/pi/conversations/{key}", query={"limit": "10"}
        )
        assert status == 200, body
        assert body["ref"]["vendorConversationId"] == "vendor-1"  # type: ignore[index]
        assert body["items"][0]["globalOrdinal"] == 1  # type: ignore[index]
        assert body["totalItems"] == 1  # type: ignore[index]
        assert body["historicalCapabilities"]["read"]["state"] == "supported"  # type: ignore[index]

    async def test_read_route_rejects_foreign_principal_key(self) -> None:
        foreign = AuthorizationBinding(
            principal_id="local-operator:9999", tenant_id=str(self.tmp.resolve())
        )
        scope = canonical_library_scope(foreign, "pi", None, workspace_root=self.tmp)
        digest = self.cursor.identity_digest("pi", "vendor-1", scope.canonical_project_scope)
        foreign_key = str(
            self.cursor.mint_conversation_key(
                scope,
                vendor_conversation_id="vendor-1",
                identity_digest=digest,
                catalog_generation=2,
            )
        )
        status, body = await asgi_request(
            self.app, "GET", f"/api/harnesses/pi/conversations/{foreign_key}"
        )
        assert status == 403
        assert body["status"] == "authorization-failed"  # type: ignore[index]

    async def test_non_loopback_peer_fails_closed_on_every_route(self) -> None:
        key = self._mint_key()
        for method, path, body in (
            ("GET", "/api/harnesses/pi/conversations", None),
            ("GET", f"/api/harnesses/pi/conversations/{key}", None),
            ("POST", f"/api/harnesses/pi/conversations/{key}/open", {"requestId": "r"}),
        ):
            status, payload = await asgi_request(
                self.app, method, path, body=body, client=("10.0.0.5", 9000)
            )
            assert status == 403, (path, payload)
            assert payload["status"] == "authorization-failed"  # type: ignore[index]

    # -- open -----------------------------------------------------------------

    def _mint_key(self) -> str:
        scope = canonical_library_scope(self.caller, "pi", None, workspace_root=self.tmp)
        digest = self.cursor.identity_digest("pi", "vendor-1", scope.canonical_project_scope)
        return str(
            self.cursor.mint_conversation_key(
                scope,
                vendor_conversation_id="vendor-1",
                identity_digest=digest,
                catalog_generation=2,
            )
        )

    def _digest(self) -> str:
        scope = canonical_library_scope(self.caller, "pi", None, workspace_root=self.tmp)
        return self.cursor.identity_digest("pi", "vendor-1", scope.canonical_project_scope)

    def _opener(self, **kwargs: object) -> OpenTerminalResult:
        self.opener_calls.append(kwargs)
        session_id = str(kwargs["session_id"])
        tmux_name = f"tmux-{session_id}"
        self.host.sessions.add(tmux_name)
        entry = TerminalCatalogEntry(
            id=session_id,
            label="open",
            kind="harness",
            harness="pi",
            lifecycle_id=None,
            cwd=self.tmp,
            tmux_name=tmux_name,
            command=("pi",),
            created_at="2026-07-18T00:00:00Z",
            last_attached_at="2026-07-18T00:00:00Z",
            status="running",
            control_endpoint=Path("/tmp/endpoint.sock"),
        )
        self.catalog.upsert(entry)
        return OpenTerminalResult(status="opened", entry=entry, kind="harness")

    def _open_service_patches(self, vendor: str | None = "vendor-1"):
        def _readiness(*_args, **kwargs):
            session_id = kwargs.get("session_id", "x")
            return HostedReadinessResult(
                "ready",
                session_id,
                entry=self.catalog.get(session_id),
                snapshot=_snapshot(vendor),
            )

        return [
            mock.patch.object(open_module, "hosted_session_readiness", _readiness),
            mock.patch.object(
                open_module,
                "read_submission_authority",
                lambda _entry: SubmissionAuthorityDescriptor(bridge_epoch="epoch-1"),
            ),
            mock.patch.object(
                library_api,
                "build_open_service",
                lambda runtime, authorization: open_module.ConversationOpenService(
                    runtime=runtime,
                    shared=self.shared,
                    authorization=authorization,
                    library=ConversationLibraryService(
                        runtime=runtime,
                        shared=self.shared,
                        authorization=authorization,
                        port_builder=lambda _h: self.port,  # type: ignore[arg-type]
                    ),
                    port_builder=lambda _h: self.port,  # type: ignore[arg-type]
                    opener=self._opener,
                    proof_wait_seconds=0.01,
                ),
            ),
        ]

    async def test_open_created_replays_and_focuses_only_proven_identity(self) -> None:
        key = self._mint_key()
        patches = self._open_service_patches()
        for patch in patches:
            patch.start()
        try:
            payload = {"requestId": "req-1", "expectedIdentityDigest": self._digest()}
            status, body = await asgi_request(
                self.app, "POST", f"/api/harnesses/pi/conversations/{key}/open", body=payload
            )
            assert status == 201, body
            assert body["outcome"] == "opened"  # type: ignore[index]
            assert body["phase"] == "opened"  # type: ignore[index]
            assert body["identity"]["vendorConversationId"] == "vendor-1"  # type: ignore[index]
            assert body["identity"]["bridgeEpoch"] == "epoch-1"  # type: ignore[index]
            assert body["rollback"] == "not-needed"  # type: ignore[index]
            assert len(self.opener_calls) == 1

            replay = await asgi_request(
                self.app, "POST", f"/api/harnesses/pi/conversations/{key}/open", body=payload
            )
            assert replay == (status, body)
            assert len(self.opener_calls) == 1

            conflict = await asgi_request(
                self.app,
                "POST",
                f"/api/harnesses/pi/conversations/{key}/open",
                body={**payload, "launchContext": {"leafKey": "other"}},
            )
            assert conflict[0] == 409
            assert conflict[1]["status"] == "request-conflict"  # type: ignore[index]
            assert len(self.opener_calls) == 1
        finally:
            for patch in patches:
                patch.stop()

    async def test_open_maps_stale_digest_unknown_request_and_timeout(self) -> None:
        key = self._mint_key()
        patches = self._open_service_patches()
        builder_patch = patches[2]
        builder_patch.start()
        try:
            stale = await asgi_request(
                self.app,
                "POST",
                f"/api/harnesses/pi/conversations/{key}/open",
                body={"requestId": "req-x", "expectedIdentityDigest": "sha256:" + "0" * 64},
            )
            assert stale[0] == 409
            assert stale[1]["status"] == "stale-identity"  # type: ignore[index]

            unknown = await asgi_request(
                self.app,
                "POST",
                f"/api/harnesses/pi/conversations/{key}/open-status",
                body={"requestId": "never-seen"},
            )
            assert unknown[0] == 404
            assert unknown[1]["status"] == "unknown-request"  # type: ignore[index]
        finally:
            builder_patch.stop()

        # Timeout path: readiness never proves within the bound.
        not_ready = mock.patch.object(
            open_module,
            "hosted_session_readiness",
            lambda *a, **k: HostedReadinessResult("not-ready", "x", entry=None, snapshot=None),
        )
        builder_patch.start()
        not_ready.start()
        try:
            status, body = await asgi_request(
                self.app,
                "POST",
                f"/api/harnesses/pi/conversations/{key}/open",
                body={"requestId": "req-t", "expectedIdentityDigest": self._digest()},
            )
            assert status == 202
            assert body["outcome"] == "timeout-unknown"  # type: ignore[index]
            status_route = await asgi_request(
                self.app,
                "POST",
                f"/api/harnesses/pi/conversations/{key}/open-status",
                body={"requestId": "req-t"},
            )
            assert status_route[0] == 202
            assert status_route[1]["revision"] == body["revision"]  # type: ignore[index]
        finally:
            not_ready.stop()
            builder_patch.stop()

    async def test_open_launch_failure_and_identity_mismatch_statuses(self) -> None:
        key = self._mint_key()
        failing_opener = mock.patch.object(
            library_api,
            "build_open_service",
            lambda runtime, authorization: open_module.ConversationOpenService(
                runtime=runtime,
                shared=self.shared,
                authorization=authorization,
                library=ConversationLibraryService(
                    runtime=runtime,
                    shared=self.shared,
                    authorization=authorization,
                    port_builder=lambda _h: self.port,  # type: ignore[arg-type]
                ),
                port_builder=lambda _h: self.port,  # type: ignore[arg-type]
                opener=lambda **_k: OpenTerminalResult(status="bad-kind", detail="no binary"),
                proof_wait_seconds=0.01,
            ),
        )
        failing_opener.start()
        try:
            status, body = await asgi_request(
                self.app,
                "POST",
                f"/api/harnesses/pi/conversations/{key}/open",
                body={"requestId": "req-f", "expectedIdentityDigest": self._digest()},
            )
            assert status == 503
            assert body["outcome"] == "launch-failed"  # type: ignore[index]
        finally:
            failing_opener.stop()

        retired: list[str] = []
        patches = [
            *self._open_service_patches(vendor="other-vendor"),
            mock.patch.object(
                open_module,
                "retire_entry",
                lambda _c, _h, entry, **_k: retired.append(entry.id),
            ),
        ]
        for patch in patches:
            patch.start()
        try:
            status, body = await asgi_request(
                self.app,
                "POST",
                f"/api/harnesses/pi/conversations/{key}/open",
                body={"requestId": "req-m", "expectedIdentityDigest": self._digest()},
            )
            assert status == 409
            assert body["outcome"] == "identity-mismatch"  # type: ignore[index]
            assert body["rollback"] == "retired"  # type: ignore[index]
            assert retired == [body["arSessionId"]]  # type: ignore[index]
        finally:
            for patch in patches:
                patch.stop()


async def _unavailable_gates_history(_harness: str):
    feature = FeatureCapability(
        state="unavailable",
        reason="harness not installed: 'pi'",
        evidence_tier="none",
    )
    return HistoryCapabilities(
        list=feature,
        read=feature,
        resume=feature,
        completeness=feature,
        tool_completeness=feature,
    )


if __name__ == "__main__":
    unittest.main()
