"""Launch, capability and resume-port fakes for the conversation open-service tests."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from pathlib import Path

from agents_remember.models.conversations.capabilities import (
    CapabilityEvidence,
    FeatureCapability,
    HistoryCapabilities,
)
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
    HarnessId,
    NativeConversationRef,
)
from agents_remember.models.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.conversation.library.cursor import LibraryCursorAuthority
from agents_remember.serving.conversation.library.errors import StaleNativeIdentityError
from agents_remember.serving.conversation.library.scope import canonical_library_scope
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_opener import (
    OpenTerminalResult,
    SpawnProvenance,
    TerminalLaunchRequest,
)

CALLER = AuthorizationBinding(
    principal_id="local-operator:1000", tenant_id="/tmp/tenant-must-match"
)


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


class _BlockingPort(_Port):
    """Resolve gate: holds the open drive before launch until the test releases it."""

    def __init__(self, cursor, gate: asyncio.Event, entered: asyncio.Event) -> None:
        super().__init__(cursor)
        self._gate = gate
        self._entered = entered

    async def resolve_resume_target(self, ref: NativeConversationRef):
        self._entered.set()
        await self._gate.wait()
        return await super().resolve_resume_target(ref)
