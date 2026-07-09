"""Durable dashboard terminal-session catalog."""

from __future__ import annotations

import contextlib
import json
import os
import threading
from collections.abc import Iterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import uuid4

# 260707-HFX2-L12 F1/CS-6 D3: a ``terminated`` row is a tombstone (the tmux session is gone and the
# hysteresis refuses to resurrect it), so without reclamation every retire/terminate leaves one to
# accumulate forever in the always-re-read catalog file. Rows older than this window are dropped by
# ``TerminalCatalog.compact``; the authoritative history stays in the observer lifecycle event stream
# (the catalog is a runtime cache), so nothing durable is lost when a stale tombstone is reclaimed.
TERMINATED_RETENTION_SECONDS = 86400.0

TerminalSessionKind = Literal["terminal", "harness"]
TerminalSessionStatus = Literal["running", "exited", "landed", "terminated"]
TerminalLivenessEvidence = Literal["tmux-command-failed", "pane-gone"]
# Live turn-state (260707-HFX-L8): derived from pane observation on the L5 prober cadence, never a
# new hot loop. "working" = the harness appears to be generating; "turn-ended" = an idle prompt
# marker was seen (the model ended its turn); "awaiting-input" = a harness-specific waiting-on-you
# marker; "stale" = no classifiable marker for long enough that the state itself is suspect.
SeatTurnState = Literal["working", "turn-ended", "awaiting-input", "stale"]
# The leaf-uniqueness role: a plain shell (``kind == "terminal"``) is a TERMINAL; any agent harness
# is a CHAT. Uniqueness is per (leaf, role) -- at most one running chat AND one running terminal per
# leaf -- so an agent chat and a scratch terminal can share a leaf without colliding (L5 fix 2).
TerminalSessionRole = Literal["chat", "terminal"]


def role_for_kind(kind: TerminalSessionKind) -> TerminalSessionRole:
    """The leaf-uniqueness role for a launch ``kind``: a shell is a terminal, a harness is a chat."""
    return "terminal" if kind == "terminal" else "chat"


@dataclass(frozen=True)
class TerminalCatalogEntry:
    """One dashboard-owned terminal or harness session."""

    id: str
    label: str
    kind: TerminalSessionKind
    harness: str | None
    lifecycle_id: str | None
    cwd: Path
    tmux_name: str
    command: tuple[str, ...]
    created_at: str
    last_attached_at: str
    status: TerminalSessionStatus
    terminated_at: str | None = None
    # The durable leaf-identity key (qualified leaf id ``repo/master/leaf-id``), opaque to the
    # backend: the catalog is the leaf->chat registry. Written only when set (like ``harness`` /
    # ``lifecycleId`` / ``terminatedAt``) so legacy rows with no ``leafKey`` read back as ``None``
    # -- no schema bump, migration-safe. A chat claims a leaf at open/attach, enclosure-independent.
    leaf_key: str | None = None
    # Spawned-by provenance (L2 agent dispatch): the spawning session id + lifecycle id when this row
    # was created by the ``spawn_agent_session`` tool (an orchestrator spawning a manager, a manager
    # spawning a worker). Same migration-safe pattern as ``leaf_key`` -- written only when set, so a
    # hand-opened or dashboard-opened row reads both back as ``None``. The dashboard reads these to
    # render the orchestration tree (spawner -> spawned edges) once that surface lands.
    spawned_by_session: str | None = None
    spawned_by_lifecycle: str | None = None
    # The l-01 role this session was spawned AS (``AR_SPAWN_ROLE`` seeded into the spawn env by the
    # dispatching seat -- orchestrator/strategist/manager/worker/reviewer/designer), recorded at first
    # spawn so the Chats command tree (L14) can group command chats without re-reading tmux env.
    # Same migration-safe written-only-when-set pattern as the provenance fields above.
    spawn_role: str | None = None
    # Free-form spawn provenance (260703-L16): the escape-hatch role knobs, recorded VERBATIM and
    # never validated -- launch_args rode the harness argv, session_commands were pasted post-launch
    # before the brief, prompt_keywords were prepended to the brief paste. Same migration-safe
    # written-only-when-set pattern as the fields above.
    launch_args: tuple[str, ...] | None = None
    prompt_keywords: tuple[str, ...] | None = None
    session_commands: tuple[str, ...] | None = None
    # The RESOLVED dispatch level (leaf|master|portfolio) this seat was spawned AT, plus whether the
    # dispatcher supplied it ("explicit") or it defaulted ("default") -- the rolesPerLevel knob
    # resolution input (260703-L16, ruling 2026-07-07T08:15). Written-only-when-set.
    spawn_level: str | None = None
    spawn_level_source: str | None = None
    # Liveness probe state (260707-HFX-L5): consecutive failed probes are persisted so a daemon
    # restart cannot erase hysteresis, while a later successful probe can clear a false exit mark.
    liveness_failures: int = 0
    liveness_first_failed_at: str | None = None
    liveness_last_failed_at: str | None = None
    liveness_evidence: TerminalLivenessEvidence | None = None
    exit_evidence: TerminalLivenessEvidence | None = None
    # Retirement provenance (260707-HFX-L8): a retire is a TERMINAL mark layered on top of the
    # existing ``terminated`` status (liveness hysteresis already never resurrects a terminated row,
    # see ``with_liveness_success``/``with_liveness_failure`` -- retirement rides that same
    # invariant instead of inventing a second terminal state). Written-only-when-set, same
    # migration-safe pattern as the fields above.
    retired_at: str | None = None
    retired_by_session: str | None = None
    retired_reason: str | None = None
    retired_edge: str | None = None
    # Landed/archive provenance (260707-HFX2-L11): normal successful completion marks a seat as
    # inspectable and non-active without killing tmux or hiding the transcript. Explicit retire/cleanup
    # still uses the terminal ``terminated`` state above.
    landed_at: str | None = None
    landed_reason: str | None = None
    landed_edge: str | None = None
    # Live identity (260707-HFX-L8, issue #4): ``label`` is mutable post-spawn via the rename API;
    # ``spawned_label`` freezes the ORIGINAL label the first time a rename happens, for audit --
    # never overwritten again. ``None`` until the first rename (no rename = no provenance to keep).
    spawned_label: str | None = None
    # Live turn-state (260707-HFX-L8, issue #4): classified from pane observation on the L5 prober
    # cadence. ``None`` until the first classification (legacy/newly-spawned rows read back as
    # unclassified, not a fabricated state).
    turn_state: SeatTurnState | None = None
    turn_state_changed_at: str | None = None

    @classmethod
    def from_json(cls, data: dict[str, object]) -> TerminalCatalogEntry:
        raw_command = data.get("command", [])
        command = raw_command if isinstance(raw_command, list) else []
        return cls(
            id=str(data["id"]),
            label=str(data["label"]),
            kind="harness" if data.get("kind") == "harness" else "terminal",
            harness=str(data["harness"]) if data.get("harness") is not None else None,
            lifecycle_id=(
                str(data["lifecycleId"]) if data.get("lifecycleId") is not None else None
            ),
            cwd=Path(str(data["cwd"])),
            tmux_name=str(data["tmuxName"]),
            command=tuple(str(part) for part in command),
            created_at=str(data["createdAt"]),
            last_attached_at=str(data["lastAttachedAt"]),
            status=_status(data.get("status")),
            terminated_at=(
                str(data["terminatedAt"]) if data.get("terminatedAt") is not None else None
            ),
            leaf_key=str(data["leafKey"]) if data.get("leafKey") is not None else None,
            spawned_by_session=(
                str(data["spawnedBySession"]) if data.get("spawnedBySession") is not None else None
            ),
            spawned_by_lifecycle=(
                str(data["spawnedByLifecycle"])
                if data.get("spawnedByLifecycle") is not None
                else None
            ),
            spawn_role=str(data["spawnRole"]) if data.get("spawnRole") is not None else None,
            launch_args=_string_tuple(data.get("launchArgs")),
            prompt_keywords=_string_tuple(data.get("promptKeywords")),
            session_commands=_string_tuple(data.get("sessionCommands")),
            spawn_level=str(data["spawnLevel"]) if data.get("spawnLevel") is not None else None,
            spawn_level_source=(
                str(data["spawnLevelSource"]) if data.get("spawnLevelSource") is not None else None
            ),
            liveness_failures=_non_negative_int(data.get("livenessFailures")),
            liveness_first_failed_at=(
                str(data["livenessFirstFailedAt"])
                if data.get("livenessFirstFailedAt") is not None
                else None
            ),
            liveness_last_failed_at=(
                str(data["livenessLastFailedAt"])
                if data.get("livenessLastFailedAt") is not None
                else None
            ),
            liveness_evidence=_liveness_evidence(data.get("livenessEvidence")),
            exit_evidence=_liveness_evidence(data.get("exitEvidence")),
            retired_at=str(data["retiredAt"]) if data.get("retiredAt") is not None else None,
            retired_by_session=(
                str(data["retiredBySession"]) if data.get("retiredBySession") is not None else None
            ),
            retired_reason=(
                str(data["retiredReason"]) if data.get("retiredReason") is not None else None
            ),
            retired_edge=str(data["retiredEdge"]) if data.get("retiredEdge") is not None else None,
            landed_at=str(data["landedAt"]) if data.get("landedAt") is not None else None,
            landed_reason=(
                str(data["landedReason"]) if data.get("landedReason") is not None else None
            ),
            landed_edge=str(data["landedEdge"]) if data.get("landedEdge") is not None else None,
            spawned_label=(
                str(data["spawnedLabel"]) if data.get("spawnedLabel") is not None else None
            ),
            turn_state=_turn_state(data.get("turnState")),
            turn_state_changed_at=(
                str(data["turnStateChangedAt"])
                if data.get("turnStateChangedAt") is not None
                else None
            ),
        )

    def to_json(self) -> dict[str, object]:
        data: dict[str, object] = {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "cwd": str(self.cwd),
            "tmuxName": self.tmux_name,
            "command": list(self.command),
            "createdAt": self.created_at,
            "lastAttachedAt": self.last_attached_at,
            "status": self.status,
        }
        if self.harness is not None:
            data["harness"] = self.harness
        if self.lifecycle_id is not None:
            data["lifecycleId"] = self.lifecycle_id
        if self.terminated_at is not None:
            data["terminatedAt"] = self.terminated_at
        if self.leaf_key is not None:
            data["leafKey"] = self.leaf_key
        if self.spawned_by_session is not None:
            data["spawnedBySession"] = self.spawned_by_session
        if self.spawned_by_lifecycle is not None:
            data["spawnedByLifecycle"] = self.spawned_by_lifecycle
        if self.spawn_role is not None:
            data["spawnRole"] = self.spawn_role
        if self.launch_args is not None:
            data["launchArgs"] = list(self.launch_args)
        if self.prompt_keywords is not None:
            data["promptKeywords"] = list(self.prompt_keywords)
        if self.session_commands is not None:
            data["sessionCommands"] = list(self.session_commands)
        if self.spawn_level is not None:
            data["spawnLevel"] = self.spawn_level
        if self.spawn_level_source is not None:
            data["spawnLevelSource"] = self.spawn_level_source
        if self.liveness_failures:
            data["livenessFailures"] = self.liveness_failures
        if self.liveness_first_failed_at is not None:
            data["livenessFirstFailedAt"] = self.liveness_first_failed_at
        if self.liveness_last_failed_at is not None:
            data["livenessLastFailedAt"] = self.liveness_last_failed_at
        if self.liveness_evidence is not None:
            data["livenessEvidence"] = self.liveness_evidence
        if self.status == "exited" and self.exit_evidence is not None:
            data["exitEvidence"] = self.exit_evidence
        if self.retired_at is not None:
            data["retiredAt"] = self.retired_at
        if self.retired_by_session is not None:
            data["retiredBySession"] = self.retired_by_session
        if self.retired_reason is not None:
            data["retiredReason"] = self.retired_reason
        if self.retired_edge is not None:
            data["retiredEdge"] = self.retired_edge
        if self.landed_at is not None:
            data["landedAt"] = self.landed_at
        if self.landed_reason is not None:
            data["landedReason"] = self.landed_reason
        if self.landed_edge is not None:
            data["landedEdge"] = self.landed_edge
        if self.spawned_label is not None:
            data["spawnedLabel"] = self.spawned_label
        if self.turn_state is not None:
            data["turnState"] = self.turn_state
        if self.turn_state_changed_at is not None:
            data["turnStateChangedAt"] = self.turn_state_changed_at
        return data

    def with_attachment(self, attached_at: str) -> TerminalCatalogEntry:
        # ``replace`` preserves every other field (incl. leaf_key + spawned-by provenance) so a new
        # column never silently drops on a re-attach.
        return replace(
            self,
            last_attached_at=attached_at,
            status="landed" if self.status == "landed" else "running",
            terminated_at=None,
            liveness_failures=0,
            liveness_first_failed_at=None,
            liveness_last_failed_at=None,
            liveness_evidence=None,
            exit_evidence=None,
        )

    def with_status(
        self, status: TerminalSessionStatus, *, at: str | None = None
    ) -> TerminalCatalogEntry:
        return replace(
            self,
            status=status,
            terminated_at=at if status == "terminated" else self.terminated_at,
        )

    def with_leaf_key(self, leaf_key: str | None) -> TerminalCatalogEntry:
        """A copy bound to ``leaf_key`` (or unbound when ``None``); the leaf-attach write point."""
        return replace(self, leaf_key=leaf_key)

    def with_retirement(
        self,
        *,
        at: str,
        by_session: str | None,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry:
        """The explicit retire terminal mark: ``terminated`` status + retirement provenance.

        Idempotent -- retiring an already-terminated row returns it unchanged (never re-stamps
        provenance, never a zombie row that gets retired twice). The existing liveness hysteresis
        already refuses to resurrect a ``terminated`` row (``with_liveness_success``), so a retired
        seat composes with L5 for free.
        """
        if self.status == "terminated":
            return self
        return replace(
            self,
            status="terminated",
            terminated_at=at,
            retired_at=at,
            retired_by_session=by_session,
            retired_reason=reason,
            retired_edge=edge,
        )

    def with_landing(
        self,
        *,
        at: str,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry:
        """The normal completion mark: inspectable archive, no tmux kill, no active leaf claim."""
        if self.status == "terminated":
            return self
        if self.status == "landed":
            return self
        return replace(
            self,
            status="landed",
            landed_at=at,
            landed_reason=reason,
            landed_edge=edge,
            liveness_failures=0,
            liveness_first_failed_at=None,
            liveness_last_failed_at=None,
            liveness_evidence=None,
            exit_evidence=None,
        )

    def with_label(self, label: str) -> TerminalCatalogEntry:
        """A copy renamed to ``label`` -- identity text ONLY, never ``spawn_role`` (L6 immutability).

        The FIRST rename freezes the original label into ``spawned_label`` for audit; later renames
        leave that provenance field alone.
        """
        return replace(self, label=label, spawned_label=self.spawned_label or self.label)

    def with_turn_state(self, state: SeatTurnState, *, changed_at: str) -> TerminalCatalogEntry:
        """A copy classified into ``state``, or ``self`` unchanged when the state did not transition."""
        if self.turn_state == state:
            return self
        return replace(self, turn_state=state, turn_state_changed_at=changed_at)

    def with_liveness_success(self) -> TerminalCatalogEntry:
        """Clear liveness failures and restore an exited row when the tmux session probes alive."""
        if (
            self.status == "running"
            and self.liveness_failures == 0
            and self.liveness_first_failed_at is None
            and self.liveness_last_failed_at is None
            and self.liveness_evidence is None
            and self.exit_evidence is None
        ):
            return self
        if self.status == "terminated":
            return self
        if self.status == "landed":
            return replace(
                self,
                liveness_failures=0,
                liveness_first_failed_at=None,
                liveness_last_failed_at=None,
                liveness_evidence=None,
                exit_evidence=None,
            )
        return replace(
            self,
            status="running",
            liveness_failures=0,
            liveness_first_failed_at=None,
            liveness_last_failed_at=None,
            liveness_evidence=None,
            exit_evidence=None,
        )

    def with_liveness_failure(
        self,
        *,
        evidence: TerminalLivenessEvidence,
        checked_at: datetime,
        failure_threshold: int,
        minimum_failure_window_seconds: float,
        pane_gone_failure_threshold: int,
    ) -> TerminalCatalogEntry:
        """Record one failed liveness probe and mark exited only after the evidence threshold."""
        if self.status != "running":
            return self
        checked_at_text = checked_at.isoformat()
        first_failed_at = self.liveness_first_failed_at or checked_at_text
        failures = self.liveness_failures + 1
        threshold = (
            max(1, pane_gone_failure_threshold)
            if evidence == "pane-gone"
            else max(1, failure_threshold)
        )
        minimum_window = 0.0 if evidence == "pane-gone" else minimum_failure_window_seconds
        should_exit = failures >= threshold and _elapsed_seconds(first_failed_at, checked_at) >= (
            max(0.0, minimum_window)
        )
        return replace(
            self,
            status="exited" if should_exit else self.status,
            liveness_failures=failures,
            liveness_first_failed_at=first_failed_at,
            liveness_last_failed_at=checked_at_text,
            liveness_evidence=evidence,
            exit_evidence=evidence if should_exit else self.exit_evidence,
        )

    @property
    def role(self) -> TerminalSessionRole:
        """This session's leaf-uniqueness role, derived from its kind (chat vs. terminal)."""
        return role_for_kind(self.kind)


def terminal_catalog_path(coordination_root: Path) -> Path:
    """Runtime catalog path for dashboard-owned terminal sessions."""

    return coordination_root / "logs" / "dashboard" / "terminal-sessions.json"


class TerminalCatalog:
    """JSON-backed catalog for dashboard-owned terminal sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # Serialize the read-modify-write within the process. FastAPI runs sync handlers in a threadpool,
        # so concurrent open/attach/terminate/refresh requests would otherwise each read a stale snapshot
        # and clobber one another (lost updates) — and, with the old shared temp file, interleave their
        # bytes into a torn file that then 500s every reader. The unique-temp + atomic replace in `_write`
        # keeps any single write valid even across processes; this lock makes concurrent mutations in THIS
        # process compose instead of racing. (RLock so a mutator may call another lock-taking helper.)
        self._lock = threading.RLock()
        # 260707-HFX2-L12 F1/CS-6 D2: an in-memory unit-of-work buffer for a full-catalog sweep. When a
        # ``batch()`` is active, ``_read``/``_write`` hit this buffer instead of disk, so the liveness
        # sweep's per-entry read-modify-writes cost one disk read (at batch begin) + one disk write (at
        # commit) total, not O(n) disk reads and O(n) disk rewrites. ``None`` when no batch is active.
        self._batch: list[TerminalCatalogEntry] | None = None
        self._batch_dirty = False

    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        entries = self._read()
        if include_terminated:
            return entries
        return [entry for entry in entries if entry.status != "terminated"]

    def get(self, session_id: str) -> TerminalCatalogEntry | None:
        return next((entry for entry in self._read() if entry.id == session_id), None)

    def active_for_leaf(
        self, leaf_key: str, *, role: TerminalSessionRole = "chat"
    ) -> TerminalCatalogEntry | None:
        """The single RUNNING session of ``role`` that owns ``leaf_key``, or ``None``.

        Uniqueness is per (leaf, role): a leaf may hold at most one running chat AND one running
        terminal, so the probe is role-scoped (the default ``"chat"`` is the agent slot). ``list()``
        already excludes terminated rows; gating on ``status == "running"`` means an exited/terminated
        session frees its leaf. The liveness sweeper and direct liveness observations keep persisted
        catalog status honest without letting transient tmux command failures immediately free a live
        session's leaf claim. This is the server-authoritative uniqueness probe the opener + attach-leaf
        routes call immediately before an upsert.
        """
        return next(
            (
                entry
                for entry in self.list()
                if entry.leaf_key == leaf_key and entry.status == "running" and entry.role == role
            ),
            None,
        )

    def upsert(self, entry: TerminalCatalogEntry) -> None:
        with self._lock:
            entries = [current for current in self._read() if current.id != entry.id]
            entries.append(entry)
            self._write(entries)

    def mark_attached(self, session_id: str, attached_at: str) -> TerminalCatalogEntry | None:
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_attachment(attached_at)
            entries[index] = updated
            self._write(entries)
            return updated

    def mark_exited(self, session_id: str) -> TerminalCatalogEntry | None:
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            if entry.status in ("landed", "terminated"):
                return entry
            updated = entry.with_status("exited")
            entries[index] = updated
            self._write(entries)
            return updated

    def record_liveness_probe(
        self,
        session_id: str,
        *,
        alive: bool,
        checked_at: datetime,
        evidence: TerminalLivenessEvidence | None = None,
        failure_threshold: int = 3,
        minimum_failure_window_seconds: float = 5.0,
        pane_gone_failure_threshold: int = 1,
    ) -> TerminalCatalogEntry | None:
        """Persist one liveness observation with hysteresis and success-side self-healing."""
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            if alive:
                updated = entry.with_liveness_success()
            elif evidence is None:
                updated = entry
            else:
                updated = entry.with_liveness_failure(
                    evidence=evidence,
                    checked_at=checked_at,
                    failure_threshold=failure_threshold,
                    minimum_failure_window_seconds=minimum_failure_window_seconds,
                    pane_gone_failure_threshold=pane_gone_failure_threshold,
                )
            if updated != entry:
                entries[index] = updated
                self._write(entries)
            return updated

    def mark_terminated(self, session_id: str, terminated_at: str) -> TerminalCatalogEntry | None:
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_status("terminated", at=terminated_at)
            entries[index] = updated
            self._write(entries)
            return updated

    def mark_retired(
        self,
        session_id: str,
        *,
        at: str,
        by_session: str | None,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry | None:
        """The explicit retire terminal mark (260707-HFX-L8): never a zombie row, never resurrected."""
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_retirement(
                at=at, by_session=by_session, reason=reason, edge=edge
            )
            if updated != entries[index]:
                entries[index] = updated
                self._write(entries)
            return updated

    def mark_landed(
        self,
        session_id: str,
        *,
        at: str,
        reason: str,
        edge: str,
    ) -> TerminalCatalogEntry | None:
        """Mark a successful completion as landed/archive without closing the tmux session."""
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            updated = entry.with_landing(at=at, reason=reason, edge=edge)
            if updated != entry:
                entries[index] = updated
                self._write(entries)
            return updated

    def set_label(self, session_id: str, label: str) -> TerminalCatalogEntry | None:
        """Rename a session's display label (identity text only -- ``spawn_role`` never changes)."""
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_label(label)
            entries[index] = updated
            self._write(entries)
            return updated

    def record_turn_state(
        self, session_id: str, state: SeatTurnState, *, changed_at: str
    ) -> TerminalCatalogEntry | None:
        """Persist a live turn-state classification; a no-op write when the state did not change."""
        with self._lock:
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            entry = entries[index]
            updated = entry.with_turn_state(state, changed_at=changed_at)
            if updated != entry:
                entries[index] = updated
                self._write(entries)
            return updated

    @contextlib.contextmanager
    def batch(self) -> Iterator[None]:
        """Read-once / write-once unit of work for a full-catalog sweep (F1/CS-6 D2).

        The liveness sweep calls a per-entry mutator (``record_liveness_probe`` / ``record_turn_state``)
        for every session, and each mutator did its own full-file read + rewrite -- O(n) disk reads and
        O(n) disk rewrites per sweep, each O(n) to parse/serialise, i.e. O(n^2) disk work that grows with
        the session count. Inside this context the catalog is read from disk exactly once (here, at begin)
        and every mutator's ``_read``/``_write`` hits the in-memory buffer; the single atomic disk write
        happens on exit. The lock is NOT held across the ``yield`` -- only for the begin read and the
        commit write -- so the slow tmux probes between mutators do not block concurrent API catalog
        access, and each mutator still takes the lock per-call for its (now in-memory) read-modify-write.
        Re-entrant: a nested ``batch()`` reuses the outer buffer and defers commit to the outermost frame.
        """
        with self._lock:
            if self._batch is not None:
                yield
                return
            self._batch = self._read_disk()
            self._batch_dirty = False
        try:
            yield
        finally:
            # Clear the buffer and flush it under ONE lock acquisition: a mutator that races the commit
            # either already wrote into ``entries`` (flushed here) or sees ``_batch is None`` and writes
            # disk directly after this block releases -- never a torn interleave or a clobbered update.
            with self._lock:
                entries = self._batch
                dirty = self._batch_dirty
                self._batch = None
                self._batch_dirty = False
                if dirty and entries is not None:
                    self._write_disk(entries)

    def compact(self, *, now: datetime, retain_seconds: float = TERMINATED_RETENTION_SECONDS) -> int:
        """Reclaim ``terminated`` tombstones older than ``retain_seconds`` so the file stays bounded.

        260707-HFX2-L12 F1/CS-6 D3: terminated rows are never resurrected (the hysteresis refuses) and
        the catalog is re-read on every sweep, so unbounded tombstone growth is the reclamation gap. Only
        ``terminated`` rows past the window are dropped -- ``running``/``exited`` rows are live, and
        ``landed`` rows are inspectable archives reclaimed by the L11 manual group-cleanup, never here.
        Provenance survives in the observer lifecycle event stream, so no separate archive file is kept.
        Returns the number of rows reclaimed. Composes inside ``batch()`` (drops from the buffer, folded
        into the one commit write).
        """
        with self._lock:
            entries = self._read()
            kept = [
                entry
                for entry in entries
                if not (
                    entry.status == "terminated"
                    and _terminated_beyond(entry, now=now, retain_seconds=retain_seconds)
                )
            ]
            if len(kept) == len(entries):
                return 0
            self._write(kept)
            return len(entries) - len(kept)

    def _read(self) -> list[TerminalCatalogEntry]:
        # Inside a batch the buffer IS the current state -- a shallow copy so a caller's in-place
        # ``entries[index] = ...`` never mutates the shared buffer out from under a concurrent mutator
        # (entries are frozen dataclasses, so a shallow copy is a safe snapshot).
        if self._batch is not None:
            return list(self._batch)
        return self._read_disk()

    def _write(self, entries: list[TerminalCatalogEntry]) -> None:
        # A batch defers durability to commit: every mutator's read-modify-write stays atomic under the
        # lock (it re-reads the buffer, mutates, writes it back), so concurrency semantics are identical
        # to the per-write-to-disk path -- only the final os.replace is coalesced to one per sweep.
        if self._batch is not None:
            self._batch = list(entries)
            self._batch_dirty = True
            return
        self._write_disk(entries)

    def _read_disk(self) -> list[TerminalCatalogEntry]:
        if not self.path.exists():
            return []
        raw = _load_catalog_json(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return []
        sessions = raw.get("sessions", [])
        if not isinstance(sessions, list):
            return []
        return [TerminalCatalogEntry.from_json(item) for item in sessions if isinstance(item, dict)]

    def _write_disk(self, entries: list[TerminalCatalogEntry]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # A UNIQUE temp per write (pid + uuid): a shared fixed-name temp let concurrent writers — even two
        # request threads in one process — interleave their bytes into a torn file. With a private temp +
        # atomic os.replace, every reader sees a complete file; the worst case is a lost update, never
        # corruption. The temp is removed if the write fails so a crash never leaves a partial sibling.
        tmp = self.path.with_name(f".{self.path.name}.{os.getpid()}.{uuid4().hex}.tmp")
        payload = {
            "schema": "ar-dashboard-terminal-sessions/v1",
            "sessions": [entry.to_json() for entry in sorted(entries, key=lambda e: e.created_at)],
        }
        try:
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(OSError):
                tmp.unlink()
            raise


def _string_tuple(raw: object) -> tuple[str, ...] | None:
    """A free-form string list read back from JSON (``None`` for absent/legacy rows)."""
    if not isinstance(raw, list):
        return None
    return tuple(str(item) for item in raw)


def _liveness_evidence(raw: object) -> TerminalLivenessEvidence | None:
    if raw == "tmux-command-failed":
        return "tmux-command-failed"
    if raw == "pane-gone":
        return "pane-gone"
    return None


def _turn_state(raw: object) -> SeatTurnState | None:
    if raw in ("working", "turn-ended", "awaiting-input", "stale"):
        return raw  # type: ignore[return-value]
    return None


def _non_negative_int(raw: object) -> int:
    if isinstance(raw, int) and raw > 0:
        return raw
    return 0


def _elapsed_seconds(first_failed_at: str, checked_at: datetime) -> float:
    first = datetime.fromisoformat(first_failed_at)
    return (checked_at - first).total_seconds()


def _terminated_beyond(
    entry: TerminalCatalogEntry, *, now: datetime, retain_seconds: float
) -> bool:
    """Whether a terminated row's ``terminated_at`` is older than the reclamation window.

    A row with no/unparseable ``terminated_at`` is conservatively KEPT (never reclaimed on a guess).
    """
    if entry.terminated_at is None:
        return False
    try:
        stamped = datetime.fromisoformat(entry.terminated_at)
    except ValueError:
        return False
    if stamped.tzinfo is None:
        stamped = stamped.replace(tzinfo=UTC)
    return (now - stamped).total_seconds() > retain_seconds


def _index_of(entries: list[TerminalCatalogEntry], session_id: str) -> int | None:
    """The position of ``session_id`` in ``entries`` (for an in-place status update), or ``None``."""
    return next((i for i, entry in enumerate(entries) if entry.id == session_id), None)


def _load_catalog_json(text: str) -> object:
    """Parse the catalog JSON, self-healing from a torn write instead of 500-ing the whole dashboard.

    A legacy fixed-temp write (or two dashboards on one coordination root) could leave a valid object
    followed by ``Extra data`` -- a partial duplicate fragment from an interleaved write. Recover the first
    complete object (the real catalog; the trailing fragment is the torn tail). Unparseable content degrades
    to an empty catalog so a corrupt file is treated as "no sessions" and the next write overwrites it clean.
    """
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        obj, _ = json.JSONDecoder().raw_decode(text.lstrip())
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj


def _status(raw: object) -> TerminalSessionStatus:
    if raw == "exited":
        return "exited"
    if raw == "landed":
        return "landed"
    if raw == "terminated":
        return "terminated"
    return "running"
