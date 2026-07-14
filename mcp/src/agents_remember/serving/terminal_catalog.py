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

from agents_remember.serving.harness_control_models import ControlState
from agents_remember.serving.seat_binding import migrated_seat_role
from agents_remember.serving.terminal_catalog_lock import exclusive_terminal_catalog_lock

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
    # The role occupying ``leaf_key``. Unlike ``spawn_role`` (immutable origin provenance), this is
    # binding state: hand-opened sessions gain it through attach, and moving a session updates the
    # leaf + role atomically. Legacy rows migrate from spawnRole, otherwise chat (terminal rows keep
    # their distinct terminal slot).
    seat_role: str | None = None
    # Explicit manager-declared leaf linkage for an unbound replacement seat. Unlike ``cwd`` (the
    # fleet-wide workspace root), this value varies per leaf and can safely credit chain progress.
    replacement_for_leaf: str | None = None
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
    # never validated -- launch_args rode the harness argv, session_commands were applied during
    # fresh-session launch, and prompt_keywords await the later post-readiness brief. Same
    # migration-safe written-only-when-set pattern as the fields above.
    launch_args: tuple[str, ...] | None = None
    prompt_keywords: tuple[str, ...] | None = None
    session_commands: tuple[str, ...] | None = None
    # The RESOLVED dispatch level (leaf|master|portfolio) this seat was spawned AT, plus whether the
    # dispatcher supplied it ("explicit") or it defaulted ("default") -- the rolesPerLevel knob
    # resolution input (260703-L16, ruling 2026-07-07T08:15). Written-only-when-set.
    spawn_level: str | None = None
    spawn_level_source: str | None = None
    # Settings-resolved knobs are pinned on harness argv/session commands. Acceptance provenance is
    # the unique id-bearing input and the harness-owned JSONL file that recorded it.
    resolved_model: str | None = None
    resolved_effort: str | None = None
    session_log_entry_id: str | None = None
    session_log_path: Path | None = None
    # Protocol-backed control metadata (260713-PHA-L1): additive and absent on legacy/plain-terminal
    # rows. ``control_endpoint`` is a user-private local socket; the exact identity tuple remains
    # id + tmux_name + created_at, and every IPC request repeats it.
    control_state: ControlState | None = None
    control_endpoint: Path | None = None
    control_protocol: str | None = None
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
        kind: TerminalSessionKind = "harness" if data.get("kind") == "harness" else "terminal"
        spawn_role = _optional_str(data, "spawnRole")
        return cls(
            id=str(data["id"]),
            label=str(data["label"]),
            kind=kind,
            harness=_optional_str(data, "harness"),
            lifecycle_id=_optional_str(data, "lifecycleId"),
            cwd=Path(str(data["cwd"])),
            tmux_name=str(data["tmuxName"]),
            command=tuple(str(part) for part in command),
            created_at=str(data["createdAt"]),
            last_attached_at=str(data["lastAttachedAt"]),
            status=_status(data.get("status")),
            terminated_at=_optional_str(data, "terminatedAt"),
            leaf_key=_optional_str(data, "leafKey"),
            seat_role=migrated_seat_role(
                persisted=_optional_str(data, "seatRole"),
                spawn_role=spawn_role,
                kind=kind,
            ),
            replacement_for_leaf=_optional_str(data, "replacementForLeaf"),
            spawned_by_session=_optional_str(data, "spawnedBySession"),
            spawned_by_lifecycle=_optional_str(data, "spawnedByLifecycle"),
            spawn_role=spawn_role,
            launch_args=_string_tuple(data.get("launchArgs")),
            prompt_keywords=_string_tuple(data.get("promptKeywords")),
            session_commands=_string_tuple(data.get("sessionCommands")),
            spawn_level=_optional_str(data, "spawnLevel"),
            spawn_level_source=_optional_str(data, "spawnLevelSource"),
            resolved_model=_optional_str(data, "resolvedModel"),
            resolved_effort=_optional_str(data, "resolvedEffort"),
            session_log_entry_id=_optional_str(data, "sessionLogEntryId"),
            session_log_path=_optional_path(data, "sessionLogPath"),
            control_state=_control_state(data.get("controlState")),
            control_endpoint=_optional_path(data, "controlEndpoint"),
            control_protocol=_optional_str(data, "controlProtocol"),
            liveness_failures=_non_negative_int(data.get("livenessFailures")),
            liveness_first_failed_at=_optional_str(data, "livenessFirstFailedAt"),
            liveness_last_failed_at=_optional_str(data, "livenessLastFailedAt"),
            liveness_evidence=_liveness_evidence(data.get("livenessEvidence")),
            exit_evidence=_liveness_evidence(data.get("exitEvidence")),
            retired_at=_optional_str(data, "retiredAt"),
            retired_by_session=_optional_str(data, "retiredBySession"),
            retired_reason=_optional_str(data, "retiredReason"),
            retired_edge=_optional_str(data, "retiredEdge"),
            landed_at=_optional_str(data, "landedAt"),
            landed_reason=_optional_str(data, "landedReason"),
            landed_edge=_optional_str(data, "landedEdge"),
            spawned_label=_optional_str(data, "spawnedLabel"),
            turn_state=_turn_state(data.get("turnState")),
            turn_state_changed_at=_optional_str(data, "turnStateChangedAt"),
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
        data.update(
            _present_fields(
                {
                    "harness": self.harness,
                    "lifecycleId": self.lifecycle_id,
                    "terminatedAt": self.terminated_at,
                    "leafKey": self.leaf_key,
                }
            )
        )
        data["seatRole"] = self.binding_role
        data.update(
            _present_fields(
                {
                    "replacementForLeaf": self.replacement_for_leaf,
                    "spawnedBySession": self.spawned_by_session,
                    "spawnedByLifecycle": self.spawned_by_lifecycle,
                    "spawnRole": self.spawn_role,
                    "launchArgs": _optional_list(self.launch_args),
                    "promptKeywords": _optional_list(self.prompt_keywords),
                    "sessionCommands": _optional_list(self.session_commands),
                    "spawnLevel": self.spawn_level,
                    "spawnLevelSource": self.spawn_level_source,
                    "resolvedModel": self.resolved_model,
                    "resolvedEffort": self.resolved_effort,
                    "sessionLogEntryId": self.session_log_entry_id,
                    "sessionLogPath": _optional_path_text(self.session_log_path),
                    "controlState": self.control_state,
                    "controlEndpoint": _optional_path_text(self.control_endpoint),
                    "controlProtocol": self.control_protocol,
                    "livenessFirstFailedAt": self.liveness_first_failed_at,
                    "livenessLastFailedAt": self.liveness_last_failed_at,
                    "livenessEvidence": self.liveness_evidence,
                    "retiredAt": self.retired_at,
                    "retiredBySession": self.retired_by_session,
                    "retiredReason": self.retired_reason,
                    "retiredEdge": self.retired_edge,
                    "landedAt": self.landed_at,
                    "landedReason": self.landed_reason,
                    "landedEdge": self.landed_edge,
                    "spawnedLabel": self.spawned_label,
                    "turnState": self.turn_state,
                    "turnStateChangedAt": self.turn_state_changed_at,
                }
            )
        )
        if self.liveness_failures:
            data["livenessFailures"] = self.liveness_failures
        if self.status == "exited" and self.exit_evidence is not None:
            data["exitEvidence"] = self.exit_evidence
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

    def with_leaf_binding(self, leaf_key: str, seat_role: str) -> TerminalCatalogEntry:
        """A copy moved to one ``(leaf_key, seat_role)`` binding in a single catalog write."""

        return replace(self, leaf_key=leaf_key, seat_role=seat_role)

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

    @property
    def binding_role(self) -> str:
        """The persisted seat role, with the migration rule available before the first rewrite."""

        return migrated_seat_role(
            persisted=self.seat_role,
            spawn_role=self.spawn_role,
            kind=self.kind,
        )

    @property
    def binding_leaf_key(self) -> str | None:
        """The leaf this seat works for, including an unbound replacement's declared target."""

        return self.leaf_key or self.replacement_for_leaf


def terminal_catalog_path(coordination_root: Path) -> Path:
    """Runtime catalog path for dashboard-owned terminal sessions."""

    return coordination_root / "logs" / "dashboard" / "terminal-sessions.json"


class TerminalCatalog:
    """JSON-backed catalog for dashboard-owned terminal sessions."""

    def __init__(self, path: Path) -> None:
        self.path = path
        # The RLock composes threads sharing this instance. ``_catalog_access`` adds the stable-file
        # flock that composes dashboard and MCP processes; atomic replace alone only prevents torn JSON.
        self._lock = threading.RLock()
        # 260707-HFX2-L12 F1/CS-6 D2: an in-memory unit-of-work buffer for a full-catalog sweep. When a
        # ``batch()`` is active, ``_read``/``_write`` hit this buffer instead of disk, so the liveness
        # sweep's per-entry read-modify-writes cost one disk read (at batch begin) + one disk write (at
        # commit) total, not O(n) disk reads and O(n) disk rewrites. ``None`` when no batch is active.
        self._batch: list[TerminalCatalogEntry] | None = None
        self._batch_dirty = False

    def list(self, *, include_terminated: bool = False) -> list[TerminalCatalogEntry]:
        entries = self._read_snapshot()
        if include_terminated:
            return entries
        return [entry for entry in entries if entry.status != "terminated"]

    def get(self, session_id: str) -> TerminalCatalogEntry | None:
        return next((entry for entry in self._read_snapshot() if entry.id == session_id), None)

    def active_for_leaf(self, leaf_key: str, *, seat_role: str) -> TerminalCatalogEntry | None:
        """The single RUNNING session of ``seat_role`` that owns ``leaf_key``, or ``None``.

        Uniqueness is per (leaf, seat role): worker, reviewer, curator, manager, architect, generic
        chat, and terminal bindings coexist. Gating on ``status == "running"`` means a completed or
        terminated holder frees only its own role slot.
        """
        return next(
            (
                entry
                for entry in self.list()
                if entry.leaf_key == leaf_key
                and entry.status == "running"
                and entry.binding_role == seat_role
            ),
            None,
        )

    def upsert(self, entry: TerminalCatalogEntry) -> None:
        with self._catalog_access():
            entries = [current for current in self._read() if current.id != entry.id]
            entries.append(entry)
            self._write(entries)

    def mark_attached(self, session_id: str, attached_at: str) -> TerminalCatalogEntry | None:
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_attachment(attached_at)
            entries[index] = updated
            self._write(entries)
            return updated

    def mark_exited(self, session_id: str) -> TerminalCatalogEntry | None:
        with self._catalog_access():
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
        with self._catalog_access():
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
        with self._catalog_access():
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
        with self._catalog_access():
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
        with self._catalog_access():
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
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = entries[index].with_label(label)
            entries[index] = updated
            self._write(entries)
            return updated

    def bind_session_log(
        self,
        session_id: str,
        *,
        entry_id: str,
        path: Path,
    ) -> TerminalCatalogEntry | None:
        """Persist log provenance onto the latest row without replaying an open-time snapshot."""
        with self._catalog_access():
            entries = self._read()
            index = _index_of(entries, session_id)
            if index is None:
                return None
            updated = replace(
                entries[index],
                session_log_entry_id=entry_id,
                session_log_path=path.resolve(),
            )
            entries[index] = updated
            self._write(entries)
            return updated

    def record_turn_state(
        self, session_id: str, state: SeatTurnState, *, changed_at: str
    ) -> TerminalCatalogEntry | None:
        """Persist a live turn-state classification; a no-op write when the state did not change."""
        with self._catalog_access():
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
        happens on exit. The cross-process lock intentionally spans the bounded probe lifecycle:
        another process's spawn/terminate waits, then reads and composes from this committed state.
        That serialization is the narrow defense against the reproduced stale-sweep overwrite while
        preserving exactly one catalog read and one write. Nested batches reuse the outer buffer.
        """
        with self._lock:
            if self._batch is not None:
                yield
                return
        with exclusive_terminal_catalog_lock(self.path):
            with self._lock:
                self._batch = self._read_disk()
                self._batch_dirty = False
            try:
                yield
            finally:
                with self._lock:
                    entries = self._batch
                    dirty = self._batch_dirty
                    self._batch = None
                    self._batch_dirty = False
                    if dirty and entries is not None:
                        self._write_disk(entries)

    def compact(
        self, *, now: datetime, retain_seconds: float = TERMINATED_RETENTION_SECONDS
    ) -> int:
        """Reclaim ``terminated`` tombstones older than ``retain_seconds`` so the file stays bounded.

        260707-HFX2-L12 F1/CS-6 D3: terminated rows are never resurrected (the hysteresis refuses) and
        the catalog is re-read on every sweep, so unbounded tombstone growth is the reclamation gap. Only
        ``terminated`` rows past the window are dropped -- ``running``/``exited`` rows are live, and
        ``landed`` rows are inspectable archives reclaimed by the L11 manual group-cleanup, never here.
        Provenance survives in the observer lifecycle event stream, so no separate archive file is kept.
        Returns the number of rows reclaimed. Composes inside ``batch()`` (drops from the buffer, folded
        into the one commit write).
        """
        with self._catalog_access():
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

    @contextlib.contextmanager
    def _catalog_access(self) -> Iterator[None]:
        """Serialize one mutation, reusing an outer batch's held file lock."""

        with self._lock:
            if self._batch is not None:
                yield
                return
        # Acquire the process lock before re-taking the instance lock, matching ``batch``. Holding
        # the instance lock while waiting for a batch's flock would deadlock that batch at commit.
        with exclusive_terminal_catalog_lock(self.path), self._lock:
            yield

    def _read_snapshot(self) -> list[TerminalCatalogEntry]:
        """Read one coherent atomic-file snapshot without waiting for the writer flock.

        A same-instance batch exposes its current buffer under the thread lock. Other instances
        read the last committed file while a slow liveness batch is probing panes; atomic replace
        makes that snapshot coherent, and read-only access never performs schema migration.
        """

        with self._lock:
            if self._batch is not None:
                return list(self._batch)
        return self._read_disk()

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
        rows = [item for item in sessions if isinstance(item, dict)]
        return [TerminalCatalogEntry.from_json(item) for item in rows]

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


def _optional_str(data: dict[str, object], key: str) -> str | None:
    raw = data.get(key)
    return str(raw) if raw is not None else None


def _optional_path(data: dict[str, object], key: str) -> Path | None:
    raw = _optional_str(data, key)
    return Path(raw) if raw is not None else None


def _optional_list(raw: tuple[str, ...] | None) -> list[str] | None:
    return list(raw) if raw is not None else None


def _optional_path_text(raw: Path | None) -> str | None:
    return str(raw) if raw is not None else None


def _present_fields(fields: dict[str, object | None]) -> dict[str, object]:
    return {key: value for key, value in fields.items() if value is not None}


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


def _control_state(raw: object) -> ControlState | None:
    if raw in {"starting", "ready", "disconnected", "failed", "unsupported"}:
        return raw  # type: ignore[return-value] -- membership narrows the runtime contract.
    return None
