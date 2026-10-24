"""The durable review-presenter attempt store: record identities, ledger and retention.

:mod:`agents_remember.canvas.presenter` owns the capability's orchestration; this module owns the
one thing it must not re-implement: how an attempt record is named, guarded, read, written and
reclaimed. Every record is one JSON file under the page's presenter directory, sized before it is
read, and every transition is a locked read-modify-write of a fresh record, so a delayed host
receipt cannot revive a cancelled attempt.

The capability name, the record schema identity and the attempt models live here because the store
refuses before any orchestration exists: an unknown attempt identity and a record path that escapes
the task are named here, and every record is sized before its bytes are read. The refusal type itself
lives in the light owner :mod:`agents_remember.canvas.presenter_refusals` and is imported back here, so
``presenter_store.PresenterRefused`` is that same class: the store raises it, ``presenter`` and
``presenter_inputs`` raise it, and a declaration module that only catches it loads none of this module's
frame, evidence and comparison imports (MIK-R204 @ v1 rule 1).

Listing is chronological and bounded (the caller shows 20 of the 32 retained); request and session
lookups read every retained record, not only a displayed window. A request identity outlives the
record it names, so a retry of a reclaimed attempt is refused by name instead of starting a
duplicate session (L104-B20). Retention actually evicts the oldest settled attempts and reclaims
their working folders.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from agents_remember.application.review_frame_store import (
    FramePublication,
    FrameRefused,
    FrameScope,
)
from agents_remember.canvas.paths import (
    PathRefused,
    ensure_directory,
    guard_path,
    presenter_work_base,
)
from agents_remember.canvas.presenter_files import (
    descriptor_path,
    directory_fd,
    open_at,
    open_binary,
    remove_directory,
    unlink_file,
    write_file,
)
from agents_remember.canvas.presenter_refusals import PresenterRefused
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.kernel.file_lock import exclusive_file_lock, lock_path_for
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import FrameBinding

__all__ = [
    "ATTEMPT_SCHEMA",
    "CAPABILITY",
    "LIVE_STATES",
    "MAX_ACTIVITY",
    "MAX_CONVERSATION",
    "MAX_MESSAGE_CHARS",
    "MAX_RECORD_BYTES",
    "MAX_REQUEST_LEDGER",
    "MAX_RETAINED_ATTEMPTS",
    "MAX_SCAN_ENTRIES",
    "REQUEST_LEDGER_NAME",
    "ActivityEntry",
    "AttemptStore",
    "ConversationEntry",
    "PresenterAttempt",
    "PresenterRefused",
    "RunnerReceipt",
    "digest_bytes",
    "failed_changes",
    "open_binary",
    "read_bounded",
    "receipt_changes",
]

CAPABILITY = "review-presenter"
ATTEMPT_SCHEMA = "ar-review-presenter-attempt/v1"
MAX_RETAINED_ATTEMPTS = 32
#: One bounded directory scan. A retained page holds at most two files per attempt plus its lock.
MAX_SCAN_ENTRIES = 4 * MAX_RETAINED_ATTEMPTS
#: Request identities outlive the records they name, so a retry of a reclaimed attempt is
#: refused by name instead of starting a duplicate session (L104-B20).
MAX_REQUEST_LEDGER = 4 * MAX_RETAINED_ATTEMPTS
REQUEST_LEDGER_NAME = "requests.json"
#: One attempt record or evidence packet is metadata; it is sized before it is read.
MAX_RECORD_BYTES = 256 * 1024
MAX_ACTIVITY = 200
MAX_CONVERSATION = 200
MAX_MESSAGE_CHARS = 8192

_STATES = ("started", "running", "published", "failed", "cancelled")
LIVE_STATES = frozenset({"started", "running"})


class StartOutcomeUnknown(PresenterRefused):
    """The native create may have taken; reconcile only its exact prepared request."""


class ActivityEntry(BaseModel):
    """One attributable line of build activity. It exposes no private chain of thought."""

    model_config = ConfigDict(extra="forbid")

    at: str
    kind: Literal["inspection", "question", "stage", "conclusion", "limitation"]
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class ConversationEntry(BaseModel):
    """One turn of the trusted side conversation, bound to the attempt's comparison and revision."""

    model_config = ConfigDict(extra="forbid")

    at: str
    role: Literal["presenter", "developer", "system-owner"]
    comparison: str
    revision: int | None = Field(default=None, ge=1)
    text: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class PresenterAttempt(BaseModel):
    """The durable record of one authoring attempt: who, for what, from what, and what happened."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_: Literal["ar-review-presenter-attempt/v1"] = Field(
        default=ATTEMPT_SCHEMA, alias="schema"
    )
    capability: Literal["review-presenter"] = CAPABILITY
    attempt_id: str
    request_id: str
    caller: str
    repository_id: str
    task: str
    leaf_id: str
    frame_id: str
    comparison: FrameBinding
    evidence_digest: str
    focus: str
    state: Literal["started", "running", "published", "failed", "cancelled"]
    created_at: str
    updated_at: str
    working_directory: str
    parent_revision: int | None = Field(default=None, ge=1)
    predecessor_attempt: str | None = None
    execution_id: str | None = None
    session_id: str | None = None
    model: str | None = None
    design_source: Literal["repository", "default"]
    design_sha256: str
    design_words: int
    instructions_sha256: str
    launch_provenance: dict[str, Any] = Field(default_factory=dict)
    instruction_composition: dict[str, Any] = Field(default_factory=dict)
    host_status: dict[str, Any] = Field(default_factory=dict)
    activity: tuple[ActivityEntry, ...] = ()
    conversation: tuple[ConversationEntry, ...] = ()
    published_revision: int | None = Field(default=None, ge=1)
    published_at: str | None = None
    detail: str | None = None


@dataclass(frozen=True)
class RunnerReceipt:
    """What a confined authoring execution answered when it was started."""

    execution_id: str
    session_id: str | None = None
    model: str | None = None
    detail: str = ""
    provenance: dict[str, Any] | None = None


def digest_bytes(payload: bytes) -> str:
    """The one sha256 a record, a design text or a capability text is named by."""

    return hashlib.sha256(payload).hexdigest()


def read_bounded(path: Path, limit: int, *, reason: str, detail: str) -> bytes:
    """Read at most ``limit`` bytes, sizing the file first so a huge one is refused unread."""

    try:
        with open_binary(path) as handle:
            size = os.fstat(handle.fileno()).st_size
            if size > limit:
                raise PresenterRefused(reason, f"{detail} ({size} bytes, bound {limit})")
            payload = handle.read(limit + 1)
    except OSError as error:
        raise PresenterRefused(reason, f"{detail}: {error}") from error
    if len(payload) > limit:
        raise PresenterRefused(reason, f"{detail}; the file grew while it was read")
    return payload


def failed_changes(current: PresenterAttempt, refusal: PresenterRefused) -> dict[str, Any]:
    """A runner refusal fails a live attempt and only annotates a settled one."""

    detail = f"{refusal.reason}: {refusal.detail}"
    if current.state in LIVE_STATES:
        return {"state": "failed", "detail": detail}
    return {"detail": f"{detail} (the attempt stayed {current.state})"}


def receipt_changes(
    current: PresenterAttempt, receipt: RunnerReceipt, launch_model: str
) -> dict[str, Any]:
    """Record a host receipt without reviving an attempt that was cancelled meanwhile."""

    changes: dict[str, Any] = {
        "execution_id": receipt.execution_id,
        "session_id": receipt.session_id,
        "model": receipt.model or launch_model,
    }
    if receipt.provenance is not None:
        changes["launch_provenance"] = receipt.provenance
    if current.state == "started":
        changes["state"] = "running"
        changes["detail"] = receipt.detail or None
    elif current.state in LIVE_STATES:
        changes["detail"] = receipt.detail or current.detail
    else:
        note = "a delayed host receipt was recorded; no state was revived"
        changes["detail"] = f"{current.state}: {note}" + (
            f" ({receipt.detail})" if receipt.detail else ""
        )
    return changes


def _attempt_segment(value: str) -> str | None:
    if not value or "/" in value or "\\" in value or value.startswith(".") or value in {".", ".."}:
        return None
    return value


class AttemptStore:
    """One page's durable attempts: their records, the request ledger and effective retention."""

    def __init__(
        self,
        config: McpRuntimeConfig,
        *,
        scope: FrameScope,
        directory: Path,
        clock: Callable[[], str],
    ) -> None:
        self._config = config
        self._scope = scope
        self._directory = directory
        self._clock = clock

    # -- paths ---------------------------------------------------------------------------------

    def record_path(self, attempt_id: str) -> Path:
        name = _attempt_segment(attempt_id)
        if name is None:
            raise PresenterRefused(
                "unknown-attempt", f"{attempt_id!r} is not one attempt identity in this page"
            )
        return self._guard_record(self._directory / f"{name}.json")

    def evidence_path(self, attempt_id: str) -> Path:
        name = _attempt_segment(attempt_id)
        if name is None:
            raise PresenterRefused(
                "unknown-attempt", f"{attempt_id!r} is not one attempt identity in this page"
            )
        return self._guard_record(self._directory / f"{name}.evidence.json")

    def _guard_record(self, path: Path) -> Path:
        try:
            return self._scope.guard(path, label="attempt record")
        except FrameRefused as refusal:
            raise PresenterRefused("unsafe-presenter-path", str(refusal)) from refusal

    def _lock(self) -> Path:
        return lock_path_for(self._guard_record(self._directory / "attempts"))

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Hold the store's one lock across the caller's complete transaction."""

        path = self._lock()
        ensure_directory(self._scope.task_root, path.parent, label="presenter directory")
        with (
            directory_fd(path.parent) as parent,
            exclusive_file_lock(
                path, "review presenter", io_path=descriptor_path(parent, path.name)
            ),
        ):
            yield

    def _ledger_path(self) -> Path:
        return self._guard_record(self._directory / REQUEST_LEDGER_NAME)

    def ledger(self) -> dict[str, str]:
        """The bounded request-identity ledger: request id -> the attempt it created."""

        try:
            path = self._ledger_path()
            if not path.is_file():
                return {}
            payload = json.loads(
                read_bounded(
                    path,
                    MAX_RECORD_BYTES,
                    reason="ledger-too-large",
                    detail="the request ledger is beyond its bound",
                )
            )
        except (OSError, PresenterRefused, json.JSONDecodeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return {
            str(key): str(value)
            for key, value in payload.items()
            if isinstance(key, str) and isinstance(value, str)
        }

    def _remember_request_locked(self, request_id: str, attempt_id: str) -> None:
        ledger = self.ledger()
        ledger[request_id] = attempt_id
        while len(ledger) > MAX_REQUEST_LEDGER:
            ledger.pop(next(iter(ledger)))
        write_file(
            self._ledger_path(),
            canonical_json_bytes(ledger),
        )

    # -- records -------------------------------------------------------------------------------

    def records(self) -> list[PresenterAttempt]:
        """Every retained attempt, newest first by creation time, from one bounded scan.

        The directory is re-guarded on every call (L104-B22): a store attached before its folder was
        swapped for a symlink must refuse *before* it enumerates, not after it has listed a foreign
        directory once.
        """

        try:
            directory = self._guard_record(self._directory)
        except PresenterRefused:
            return []
        if not directory.is_dir():
            return []
        records: list[PresenterAttempt] = []
        try:
            with directory_fd(directory) as descriptor, os.scandir(descriptor) as entries:
                for index, entry in enumerate(entries):
                    if index >= MAX_SCAN_ENTRIES:
                        break
                    if not entry.name.endswith(".json"):
                        continue
                    if entry.name.endswith(".evidence.json") or entry.name == REQUEST_LEDGER_NAME:
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                    attempt = self._read_at(descriptor, entry.name)
                    if attempt is not None:
                        records.append(attempt)
        except OSError:
            return []
        records.sort(key=lambda item: (item.created_at, item.attempt_id), reverse=True)
        return records

    @staticmethod
    def _read_at(descriptor: int, name: str) -> PresenterAttempt | None:
        try:
            with open_at(descriptor, name) as handle:
                if os.fstat(handle.fileno()).st_size > MAX_RECORD_BYTES:
                    return None
                payload = handle.read(MAX_RECORD_BYTES + 1)
            return (
                PresenterAttempt.model_validate_json(payload)
                if len(payload) <= MAX_RECORD_BYTES
                else None
            )
        except (OSError, ValueError):
            return None

    def _read_path(self, path: Path) -> PresenterAttempt | None:
        try:
            guarded = self._guard_record(path)
            payload = read_bounded(
                guarded,
                MAX_RECORD_BYTES,
                reason="record-too-large",
                detail=f"the attempt record {guarded.name} is beyond its metadata bound",
            )
            return PresenterAttempt.model_validate(json.loads(payload))
        except (OSError, PresenterRefused, json.JSONDecodeError, ValueError):
            return None

    def read(self, attempt_id: str) -> PresenterAttempt | None:
        try:
            path = self.record_path(attempt_id)
        except PresenterRefused:
            return None
        return self._read_path(path) if path.is_file() else None

    def read_evidence(self, attempt_id: str, expected: str) -> EvidencePacket | None:
        """The attempt's admitted evidence packet, read bounded, or ``None`` if it is not intact."""

        try:
            path = self.evidence_path(attempt_id)
            if not path.is_file():
                return None
            payload = read_bounded(
                path,
                MAX_RECORD_BYTES,
                reason="evidence-too-large",
                detail="the attempt's evidence packet is beyond its metadata bound",
            )
        except (OSError, PresenterRefused):
            return None
        if digest_bytes(payload) != expected:
            return None
        try:
            return EvidencePacket.model_validate_json(payload)
        except ValueError:
            return None

    def current(self) -> PresenterAttempt | None:
        """The newest live attempt, else the newest retained one: the default panel view."""

        records = self.records()
        live = next((item for item in records if item.state in LIVE_STATES), None)
        if live is not None:
            return live
        return records[0] if records else None

    def live(self) -> PresenterAttempt | None:
        """The latest-created attempt still open, independent of the displayed window."""

        return next((item for item in self.records() if item.state in LIVE_STATES), None)

    def by_request(self, request_id: str) -> PresenterAttempt | None:
        """Reconcile one request identity across every retained record, not the shown window."""

        return next((item for item in self.records() if item.request_id == request_id), None)

    def by_session(self, session_id: str) -> PresenterAttempt | None:
        """Find one attempt by its recorded host session, across every retained record."""

        return next((item for item in self.records() if item.session_id == session_id), None)

    def write(self, attempt: PresenterAttempt) -> PresenterAttempt:
        with self.locked():
            return self.write_locked(attempt)

    def write_locked(self, attempt: PresenterAttempt) -> PresenterAttempt:
        directory = self._directory
        ensure_directory(self._scope.task_root, directory, label="presenter directory")
        kept = self._bounded_record(attempt)
        write_file(
            self.record_path(kept.attempt_id),
            canonical_json_bytes(json.loads(kept.model_dump_json(by_alias=True))),
        )
        self._remember_request_locked(kept.request_id, kept.attempt_id)
        self._prune_locked()
        return kept

    def _bounded_record(self, attempt: PresenterAttempt) -> PresenterAttempt:
        """Keep one record inside its own byte ceiling by settling its oldest turns (L104-B20).

        A record that grew past :data:`MAX_RECORD_BYTES` would be refused by every reader as
        ``record-too-large`` and disappear from current/live/by-request reconciliation, so the bound
        is enforced at the writer: the oldest conversation turn, else the oldest activity line, is
        dropped until the serialized record fits. The attempt itself, its state and its published
        revision are never dropped; a record that cannot fit without them is refused by name.
        """

        def serialized(candidate: PresenterAttempt) -> bytes:
            return canonical_json_bytes(json.loads(candidate.model_dump_json(by_alias=True)))

        kept = attempt
        while len(serialized(kept)) > MAX_RECORD_BYTES:
            conversation = kept.conversation
            activity = kept.activity
            if conversation and len(conversation) >= len(activity):
                kept = kept.model_copy(update={"conversation": conversation[1:]})
            elif activity:
                kept = kept.model_copy(update={"activity": activity[1:]})
            else:
                raise PresenterRefused(
                    "record-too-large",
                    f"the attempt record for {attempt.attempt_id} cannot fit its metadata bound",
                )
        return kept

    def prune(self) -> None:
        """Keep the newest :data:`MAX_RETAINED_ATTEMPTS`; a live attempt is never removed."""

        with self.locked():
            self._prune_locked()

    def _prune_locked(self) -> None:
        records = self.records()
        if len(records) <= MAX_RETAINED_ATTEMPTS:
            return
        for attempt in reversed(records[MAX_RETAINED_ATTEMPTS:]):
            if attempt.state in LIVE_STATES:
                continue
            self.remove(attempt)

    def remove(self, attempt: PresenterAttempt) -> None:
        """Delete one settled attempt's record, evidence and working folder."""

        try:
            unlink_file(self.record_path(attempt.attempt_id))
            unlink_file(self.evidence_path(attempt.attempt_id))
        except (OSError, PresenterRefused):
            pass
        working = Path(attempt.working_directory)
        try:
            guard_path(presenter_work_base(self._config), working, label="reclaimed working folder")
        except PathRefused:
            return
        with suppress(OSError):
            remove_directory(working)

    def mutate(self, attempt_id: str, change: Any) -> PresenterAttempt:
        """One locked read-modify-write: a fresh record in, a whole fresh record out."""

        with self.locked():
            current = self.require(attempt_id)
            changes = change(current)
            if not changes:
                return current
            payload = {**current.model_dump(), **changes, "updated_at": self._clock()}
            return self.write_locked(PresenterAttempt.model_validate(payload))

    def record_publication(self, attempt: PresenterAttempt, publication: FramePublication) -> None:
        """Record that the store published this attempt's revision, and what it answered."""

        manifest = publication.manifest
        assert manifest is not None
        revision = manifest.revision
        detail = publication.detail

        def change(_current: PresenterAttempt) -> dict[str, Any]:
            return {
                "state": "published",
                "published_revision": revision,
                "published_at": self._clock(),
                "detail": detail,
            }

        self.mutate(attempt.attempt_id, change)

    def record_publication_refusal(self, attempt_id: str, refusal: FrameRefused) -> None:
        """Record that the frame store refused the publication this attempt asked for."""

        detail = f"publication refused: {refusal}"

        def change(current: PresenterAttempt) -> dict[str, Any]:
            if current.state in LIVE_STATES:
                return {"state": "failed", "detail": detail}
            return {"detail": f"{detail} (the attempt stayed {current.state})"}

        self.mutate(attempt_id, change)

    def require(self, attempt_id: str) -> PresenterAttempt:
        attempt = self.read(attempt_id)
        if attempt is None:
            raise PresenterRefused("unknown-attempt", f"no attempt {attempt_id!r} in this page")
        return attempt
