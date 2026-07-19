"""Idempotent exact-identity open/status/reconcile for the native library (260718-CHATS-L2).

One stable ``requestId`` + immutable fingerprint drives one launch, ever. The bounded
in-memory ledger (mirroring the submission-authority pattern: LRU terminal eviction, hard
refusal when full of live work) keys operations by caller principal + requestId; replaying the
identical authorized request returns the same operation/revision and never spawns again, while
the same id with a changed fingerprint conflicts without launching. The deterministic session
id derived from (principal, requestId) additionally lets the tracked opener itself absorb a
replay after a ledger loss (restart): the same spawn is ensured, never duplicated.

Open launches a NEW tracked AR session through the existing shared opener (never a second
opener, never an in-place identity mutation), then waits bounded for exact catalog proof: the
correlated session id, the requested harness, the exact native conversation id, and a readable
bridge epoch. Only then does the operation become ``opened`` (201). A proof timeout stays
``timeout-unknown`` and reconcilable — never a relaunch. Failed or mismatched spawns are
retired idempotently through the shared retirement mechanics with the rollback state reported;
the previous conversation, draft, focus, and scroll are never touched (there is no browser or
Toad state in this service at all).
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from agents_remember.observer.events import now_iso
from agents_remember.serving.conversation.library.errors import (
    LibraryScopeError,
    LibraryStoreError,
    OpenLedgerFullError,
    OpenRequestConflictError,
    StaleNativeIdentityError,
    UnknownNativeConversationError,
    UnknownOpenRequestError,
)
from agents_remember.serving.conversation.library.scope import canonical_library_scope
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    AuthorizationBinding,
    ConversationLibraryScope,
    HarnessId,
    NativeConversationRef,
    OpenConversationOperation,
    OperationFingerprint,
    operation_fingerprint,
)
from agents_remember.serving.conversation.runtime import ConversationRuntime
from agents_remember.serving.harness_control_client import read_submission_authority
from agents_remember.serving.hosted_readiness import hosted_session_readiness
from agents_remember.serving.retire import retire_entry
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry
from agents_remember.serving.terminal_opener import (
    OpenTerminalResult,
    open_terminal_session,
)

if TYPE_CHECKING:
    from agents_remember.serving.conversation.library.factories import LibraryShared
    from agents_remember.serving.conversation.library.service import (
        ConversationLibraryService,
        PortBuilder,
    )

_OPEN_LEDGER_LIMIT = 256
_DEFAULT_PROOF_WAIT_SECONDS = 45.0
_RETIRE_ACTOR = "conversation-library-open"

_TERMINAL_OUTCOMES = frozenset(
    {
        "opened",
        "unsupported",
        "stale-identity",
        "launch-failed",
        "identity-mismatch",
        "request-conflict",
    }
)


@dataclass
class _OpenRecord:
    """Server-private ledger state; the wire operation is a strict projection of it."""

    request_id: str
    fingerprint: OperationFingerprint
    harness_id: HarnessId
    ref: NativeConversationRef
    scope: ConversationLibraryScope
    key_token: str
    launch_context: Mapping[str, str | None]
    revision: int = 1
    phase: str = "requested"
    outcome: str = "pending"
    rollback: str = "not-needed"
    detail: str | None = None
    # The deterministic session id is minted at record creation for replay idempotence; it is
    # NOT launch evidence. Only `launched` (set after the opener commits the catalog row)
    # authorizes proof observation and retirement (review F1/O5: id presence != launched).
    ar_session_id: str | None = None
    launched: bool = False
    bridge_epoch: str | None = None
    identity: ActiveConversationRef | None = None
    catalog_generation: int | None = None
    launch_args: tuple[str, ...] = ()
    # Codex-only exact resume identity for the landed L0E opener channel; never set for
    # non-codex records (the opener fail-closes on any other harness receiving it).
    resume_thread_id: str | None = None
    # Spawn-ownership discriminator (review F5): True when a LIVE catalog row with this
    # record's deterministic session id already existed before the opener call. Absorbed
    # pre-existing sessions are never retired, whatever they prove.
    absorbed_existing: bool = False
    retire_done: bool = False

    @property
    def terminal(self) -> bool:
        return self.outcome in _TERMINAL_OUTCOMES

    def transition(self, phase: str) -> None:
        self.phase = phase
        self.revision += 1

    def to_operation(self) -> OpenConversationOperation:
        return OpenConversationOperation(
            request_id=self.request_id,
            request_fingerprint=self.fingerprint,
            revision=self.revision,
            phase=self.phase,  # type: ignore[arg-type] - narrowed by the transition guards
            outcome=self.outcome,  # type: ignore[arg-type] - narrowed likewise
            ar_session_id=self.ar_session_id if self.identity is not None else None,
            bridge_epoch=self.bridge_epoch if self.identity is not None else None,
            identity=self.identity,
            # The landed contract publishes a catalog generation only beside an exact proven
            # identity; the record still tracks it privately for pre-proof phases.
            catalog_generation=(self.catalog_generation if self.identity is not None else None),
            rollback=self.rollback,  # type: ignore[arg-type] - narrowed by the model guard
            detail=self.detail,
        )


class OpenOperationLedger:
    """Bounded idempotence ledger: LRU terminal eviction, hard refusal when full of live work."""

    def __init__(self, limit: int = _OPEN_LEDGER_LIMIT) -> None:
        if limit < 1:
            raise ValueError("open ledger limit must be positive")
        self._limit = limit
        self._records: OrderedDict[tuple[str, str], _OpenRecord] = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def retained_record_count(self) -> int:
        return len(self._records)

    def lock(self) -> asyncio.Lock:
        return self._lock

    def get(self, key: tuple[str, str]) -> _OpenRecord | None:
        record = self._records.get(key)
        if record is not None:
            self._records.move_to_end(key)
        return record

    def add(self, key: tuple[str, str], record: _OpenRecord) -> None:
        self._make_room()
        self._records[key] = record

    def _make_room(self) -> None:
        while len(self._records) >= self._limit:
            for candidate_key, candidate in self._records.items():
                if candidate.terminal:
                    del self._records[candidate_key]
                    break
            else:
                raise OpenLedgerFullError(
                    "the open-operation ledger is full of live or unresolved operations; "
                    "reconcile or settle outstanding opens before retrying"
                )


class ConversationOpenService:
    """Idempotent exact open/status/reconcile composed over the tracked opener + catalog."""

    def __init__(
        self,
        *,
        runtime: ConversationRuntime,
        shared: LibraryShared,
        authorization: AuthorizationBinding,
        library: ConversationLibraryService,
        port_builder: PortBuilder,
        opener: Callable[..., OpenTerminalResult] = open_terminal_session,
        proof_wait_seconds: float = _DEFAULT_PROOF_WAIT_SECONDS,
    ) -> None:
        self._runtime = runtime
        self._shared = shared
        self._authorization = authorization
        self._library = library
        self._port_builder = port_builder
        self._opener = opener
        self._proof_wait = proof_wait_seconds

    async def open(
        self,
        harness_id: HarnessId,
        key_token: str,
        *,
        request_id: str,
        expected_identity_digest: str,
        cwd: str | None,
        launch_context: Mapping[str, str | None],
    ) -> OpenConversationOperation:
        scope, ref, binding = self._library.resolve_key(harness_id, key_token)
        if expected_identity_digest != binding.identity_digest:
            raise StaleNativeIdentityError(
                "the expected identity digest no longer matches the library row; refresh it"
            )
        if cwd is not None:
            narrowed = canonical_library_scope(
                self._authorization,
                harness_id,
                cwd,
                workspace_root=self._runtime.scope.workspace_root,
            )
            if narrowed.canonical_project_scope != scope.canonical_project_scope:
                raise LibraryScopeError(
                    "requested cwd does not match the conversation's canonical scope"
                )
        fingerprint = operation_fingerprint(
            "conversation-open",
            self._authorization,
            {
                "harnessId": harness_id,
                "conversationKey": key_token,
                "identityDigest": binding.identity_digest,
                "canonicalProjectScope": scope.canonical_project_scope,
                "launchContext": dict(launch_context),
            },
        )
        key = (self._authorization.principal_id, request_id)
        ledger = self._shared.open_ledger
        async with ledger.lock():
            existing = ledger.get(key)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise OpenRequestConflictError(
                        "requestId was already used with a different open request"
                    )
                return existing.to_operation()
            record = _OpenRecord(
                request_id=request_id,
                fingerprint=fingerprint,
                harness_id=harness_id,
                ref=ref,
                scope=scope,
                key_token=key_token,
                launch_context=dict(launch_context),
                ar_session_id=_open_session_id(self._authorization.principal_id, request_id),
            )
            ledger.add(key, record)
        try:
            return await self._drive(record)
        except Exception as exc:
            # Review O4: an escaping fault must settle the record, never strand a live
            # pending slot in the bounded ledger.
            return self._fail(
                record, "launch-failed", f"the open drive failed before catalog proof: {exc}"
            )

    async def status(
        self,
        harness_id: HarnessId,
        key_token: str,
        *,
        request_id: str,
    ) -> OpenConversationOperation:
        record = await self._require_record(harness_id, key_token, request_id)
        await self._refresh(record, retry_retirement=False)
        return record.to_operation()

    async def reconcile(
        self,
        harness_id: HarnessId,
        key_token: str,
        *,
        request_id: str,
    ) -> OpenConversationOperation:
        record = await self._require_record(harness_id, key_token, request_id)
        await self._refresh(record, retry_retirement=True)
        return record.to_operation()

    # -- drive --------------------------------------------------------------

    async def _drive(self, record: _OpenRecord) -> OpenConversationOperation:
        capabilities = await self._shared.gates.history_capabilities(record.harness_id)
        feature = capabilities.resume
        if feature.state != "supported":
            return self._fail(record, "unsupported", feature.reason)
        prepared = await self._prepare(record)
        if prepared is not None:
            return prepared
        record.transition("launching")
        result = await self._launch(record)
        if result.status != "opened" or result.entry is None:
            return self._fail(
                record,
                "launch-failed",
                result.detail or f"tracked opener returned {result.status}",
            )
        # The opener committed the catalog row: only now is the record launched. Proof
        # observation and retirement key on this marker, never on the session id's presence.
        record.launched = True
        record.transition("catalog-wait")
        return await self._prove(record)

    async def _prepare(self, record: _OpenRecord) -> OpenConversationOperation | None:
        """Resolve + validate the exact resume target; a failure operation, or None to launch."""

        try:
            target = await self._port_builder(record.harness_id).resolve_resume_target(record.ref)
        except (StaleNativeIdentityError, UnknownNativeConversationError) as exc:
            return self._fail(record, "stale-identity", str(exc))
        except LibraryStoreError as exc:
            return self._fail(record, "launch-failed", str(exc))
        target_binding, vendor, launch = self._shared.cursor_authority.verify_resume_target(target)
        if vendor != record.ref.vendor_conversation_id:
            return self._fail(
                record, "stale-identity", "resume target resolved a different native identity"
            )
        record.catalog_generation = target_binding.catalog_generation
        kind = launch.get("kind")
        if kind == "argv":
            return self._prepare_argv(record, launch)
        if kind == "codex-thread-resume" and record.harness_id == "codex":
            return self._prepare_codex_kind(record, launch)
        return self._fail(
            record,
            "unsupported",
            "the resolved resume target has no production launch seam on this harness",
        )

    def _prepare_argv(
        self, record: _OpenRecord, launch: Mapping[str, object]
    ) -> OpenConversationOperation | None:
        args = launch.get("args")
        if (
            not isinstance(args, list)
            or not args
            or not all(isinstance(arg, str) and arg for arg in args)
        ):
            return self._fail(record, "launch-failed", "resume target carries invalid launch argv")
        record.launch_args = tuple(cast(list[str], args))
        return None

    def _prepare_codex_kind(
        self, record: _OpenRecord, launch: Mapping[str, object]
    ) -> OpenConversationOperation | None:
        thread_id = launch.get("threadId")
        if (
            not isinstance(thread_id, str)
            or not thread_id
            or thread_id != thread_id.strip()
            or any(character.isspace() for character in thread_id)
        ):
            return self._fail(
                record, "launch-failed", "codex resume target carries an invalid thread id"
            )
        record.resume_thread_id = thread_id
        return None

    async def _launch(self, record: _OpenRecord) -> OpenTerminalResult:
        assert record.ar_session_id is not None  # set at record creation
        host = cast(TerminalHost, self._runtime.host)
        # The L0 composition provably binds the concrete TerminalHost (app.py constructs it);
        # the runtime's Protocol type is narrower than the tracked opener's requirement.
        env: dict[str, str] = {}
        seat_role = record.launch_context.get("seatRole")
        if seat_role:
            env["AR_SPAWN_ROLE"] = seat_role
        existing = self._runtime.catalog.get(record.ar_session_id)
        record.absorbed_existing = (
            existing is not None
            and record.ar_session_id is not None
            and self._runtime.host.has_session(existing.tmux_name)
        )
        try:
            return await asyncio.to_thread(
                self._opener,
                catalog=self._runtime.catalog,
                host=host,
                session_id=record.ar_session_id,
                kind="harness",
                harness=record.harness_id,
                workspace_root=Path(record.scope.canonical_project_scope),
                shell=os.environ.get("SHELL") or "/bin/bash",
                label=f"Library open: {record.harness_id} {record.ref.vendor_conversation_id[-6:]}",
                leaf_key=record.launch_context.get("leafKey") or None,
                env=env,
                launch_args=list(record.launch_args),
                resume_thread_id=record.resume_thread_id,
                control_root=self._runtime.scope.coordination_root / "runtime" / "harness-control",
                harnesses=self._runtime.harness_registry(),
            )
        except Exception as exc:  # the opener raises typed errors on internal failures
            return OpenTerminalResult(status="launch-conflict", detail=str(exc))

    async def _prove(self, record: _OpenRecord) -> OpenConversationOperation:
        assert record.ar_session_id is not None
        readiness = await asyncio.to_thread(
            hosted_session_readiness,
            self._runtime.catalog,
            self._runtime.host,
            session_id=record.ar_session_id,
            wait_seconds=self._proof_wait,
        )
        return self._settle_observation(
            record, readiness.status, readiness.entry, readiness.snapshot
        )

    # -- refresh (status/reconcile) ------------------------------------------

    async def _refresh(self, record: _OpenRecord, *, retry_retirement: bool) -> None:
        if record.terminal:
            if retry_retirement and record.rollback in {"retire-failed", "retire-pending"}:
                self._retire(record)
            return
        if not record.launched or record.ar_session_id is None:
            # Pre-launch polls (gate/resolve/opener window) must never settle the record:
            # an absent catalog row before launch is not proof of launch failure (review F1).
            return
        readiness = await asyncio.to_thread(
            hosted_session_readiness,
            self._runtime.catalog,
            self._runtime.host,
            session_id=record.ar_session_id,
            wait_seconds=0.0,
        )
        if readiness.status == "not-ready":
            return
        self._settle_observation(record, readiness.status, readiness.entry, readiness.snapshot)

    def _settle_observation(
        self,
        record: _OpenRecord,
        status: str,
        entry: TerminalCatalogEntry | None,
        snapshot: object,
    ) -> OpenConversationOperation:
        vendor = getattr(snapshot, "vendor_session_id", None)
        control = getattr(snapshot, "control", None)
        if status == "ready" and entry is not None:
            if vendor == record.ref.vendor_conversation_id:
                return self._open(record, entry)
            if isinstance(vendor, str) and vendor:
                return self._mismatch(record, entry, vendor)
            # A ready bridge that has not yet published a native identity can neither prove
            # nor disprove the open. Stay reconcilable instead of retiring a session that
            # may still prove correct; the next status/reconcile observes again.
        if control == "failed":
            return self._fail_launch_with_retire(
                record,
                entry,
                f"the spawned harness bridge failed before catalog proof: "
                f"{getattr(snapshot, 'raw', {}).get('bridgeError', 'unknown')}",
            )
        if status in {"unknown-session", "terminated"}:
            return self._fail_launch_with_retire(
                record, entry, f"the spawned session is {status} before catalog proof"
            )
        # Proof wait expired with the session still coming up: stay reconcilable.
        record.outcome = "timeout-unknown"
        record.revision += 1
        record.detail = (
            "catalog proof did not arrive within the observation bound; reconcile with the "
            "same requestId"
        )
        return record.to_operation()

    def _open(self, record: _OpenRecord, entry: TerminalCatalogEntry) -> OpenConversationOperation:
        try:
            descriptor = read_submission_authority(entry)
        except Exception as exc:
            return self._fail_launch_with_retire(
                record, entry, f"catalog proof could not read the bridge epoch: {exc}"
            )
        record.bridge_epoch = descriptor.bridge_epoch
        record.identity = ActiveConversationRef(
            harness_id=record.ref.harness_id,
            vendor_conversation_id=record.ref.vendor_conversation_id,
            project_scope=record.ref.project_scope,
            identity_digest=record.ref.identity_digest,
            ar_session_id=entry.id,
            bridge_epoch=descriptor.bridge_epoch,
        )
        record.phase = "opened"
        record.outcome = "opened"
        record.revision += 1
        record.rollback = "not-needed"
        return record.to_operation()

    def _mismatch(
        self,
        record: _OpenRecord,
        entry: TerminalCatalogEntry,
        actual_vendor: object,
    ) -> OpenConversationOperation:
        if record.absorbed_existing:
            # The live session occupying this lane predates the operation (review F5):
            # the request's target disagrees with it, so this open cannot proceed — but the
            # foreign session is never retired or otherwise disturbed.
            return self._fail(
                record,
                "launch-failed",
                "the deterministic session lane is occupied by a pre-existing live session "
                "whose native identity does not match this request; it was left untouched — "
                "retry with a fresh requestId",
            )
        try:
            descriptor = read_submission_authority(entry)
        except Exception as exc:
            return self._fail_launch_with_retire(
                record, entry, f"identity mismatch evidence is unreadable: {exc}"
            )
        record.bridge_epoch = descriptor.bridge_epoch
        actual = actual_vendor if isinstance(actual_vendor, str) and actual_vendor else "unknown"
        record.identity = ActiveConversationRef(
            harness_id=record.ref.harness_id,
            vendor_conversation_id=actual,
            project_scope=record.ref.project_scope,
            identity_digest=self._shared.cursor_authority.identity_digest(
                record.ref.harness_id, actual, record.ref.project_scope
            ),
            ar_session_id=entry.id,
            bridge_epoch=descriptor.bridge_epoch,
        )
        record.phase = "failed"
        record.outcome = "identity-mismatch"
        record.revision += 1
        record.detail = (
            f"the spawned session proved native identity {actual!r}, not the requested "
            f"{record.ref.vendor_conversation_id!r}; the mismatched row was retired"
        )
        self._retire(record)
        return record.to_operation()

    # -- failures / retirement ------------------------------------------------

    def _fail(
        self,
        record: _OpenRecord,
        outcome: str,
        detail: str,
    ) -> OpenConversationOperation:
        record.phase = "failed"
        record.outcome = outcome
        record.revision += 1
        record.detail = detail
        record.rollback = "not-needed"
        return record.to_operation()

    def _fail_launch_with_retire(
        self,
        record: _OpenRecord,
        entry: TerminalCatalogEntry | None,
        detail: str,
    ) -> OpenConversationOperation:
        del entry  # the record's own session id is the retirement authority
        if record.absorbed_existing:
            # The failing session predates this operation; never retire it (review F5).
            return self._fail(
                record,
                "launch-failed",
                f"{detail}; the pre-existing session was left untouched",
            )
        record.phase = "failed"
        record.outcome = "launch-failed"
        record.revision += 1
        record.detail = f"{detail}; the failed spawned row was retired"
        # Pre-identity launch failures publish no spawned identity; the wire rollback stays
        # not-needed (landed contract) while the server tombstones the row idempotently.
        self._retire(record)
        return record.to_operation()

    def _retire(self, record: _OpenRecord) -> None:
        if record.retire_done:
            if record.identity is not None:
                record.rollback = "retired"
            return
        session_id = record.ar_session_id
        if session_id is None or not record.launched or record.absorbed_existing:
            # Nothing this record spawned: there is no owned row to retire (review F5).
            return
        entry = self._runtime.catalog.get(session_id)
        if entry is None:
            # A missing row is not a completed retirement: never poison the guard with a
            # false tombstone. With a published identity the retirement is still owed, so
            # report retire-pending (phase retiring) and let reconcile retry it (review F1b).
            if record.identity is not None:
                record.phase = "retiring"
                record.rollback = "retire-pending"
            return
        self._do_retire(record, entry)

    def _do_retire(self, record: _OpenRecord, entry: TerminalCatalogEntry) -> None:
        try:
            retire_entry(
                self._runtime.catalog,
                cast(TerminalHost, self._runtime.host),
                entry,
                at=now_iso(),
                by_session=_RETIRE_ACTOR,
                reason=f"library open {record.outcome}",
                edge="library-open",
            )
        except Exception:
            if record.identity is not None:
                record.rollback = "retire-failed"
            return
        record.retire_done = True
        if record.identity is not None:
            # A completed retirement rests at phase failed; retiring is only the pending state.
            record.phase = "failed"
            record.rollback = "retired"

    # -- lookup ----------------------------------------------------------------

    async def _require_record(
        self,
        harness_id: HarnessId,
        key_token: str,
        request_id: str,
    ) -> _OpenRecord:
        # Status/reconcile re-authorize the key and scope before touching the ledger.
        self._library.resolve_key(harness_id, key_token)
        key = (self._authorization.principal_id, request_id)
        ledger = self._shared.open_ledger
        async with ledger.lock():
            record = ledger.get(key)
        if record is None:
            raise UnknownOpenRequestError(
                "no open operation exists for this requestId; post open first"
            )
        if record.harness_id != harness_id or record.key_token != key_token:
            raise OpenRequestConflictError(
                "the requestId's open operation names a different conversation"
            )
        return record


def _open_session_id(principal_id: str, request_id: str) -> str:
    """Deterministic tracked-session id for one caller's open request (replay-safe)."""

    digest = hashlib.sha256(f"{principal_id}|{request_id}".encode()).hexdigest()
    return f"ar-open-{digest[:24]}"


__all__ = ["ConversationOpenService", "OpenOperationLedger"]
