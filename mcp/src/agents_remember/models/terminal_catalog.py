"""Terminal catalog row vocabulary shared with the conversation tree (L9 split).

The catalog row, its literals, and the pure JSON parsing helpers moved out of
``serving/terminal_catalog.py``; the store itself stays in serving. Bodies are
unchanged from the pre-split module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from agents_remember.models.conversations.control_wire import (
    AcceptanceState,
    ActivityState,
    ControlState,
)
from agents_remember.models.task_document_ref import TaskDocumentRef

TerminalSessionKind = Literal["terminal", "harness"]

TerminalSessionStatus = Literal["running", "exited", "landed", "terminated"]

TerminalLivenessEvidence = Literal["tmux-command-failed", "pane-gone"]
# Live turn-state (260707-HFX-L8): derived from pane observation on the L5 prober cadence, never a
# new hot loop. "working" = the harness appears to be generating; "turn-ended" = an idle prompt
# marker was seen (the model ended its turn); "awaiting-input" = a harness-specific waiting-on-you
# marker; "stale" = no classifiable marker for long enough that the state itself is suspect.

SeatTurnState = Literal["working", "turn-ended", "awaiting-input", "stale"]
TerminalOutcome = Literal["completed", "interrupted", "failed", "unknown"]
InterruptOrigin = Literal["developer", "unknown"]
# The structural-seat role fallback: a plain shell (``kind == "terminal"``) is a TERMINAL; any
# otherwise-unclassified harness is a CHAT. Named role seats persist their actual role.

TerminalSessionRole = Literal["chat", "terminal"]


@dataclass(frozen=True)
class CatalogTurnEvidence:
    """One seat-turn projection: the seat state plus the lifted terminal outcome."""

    state: SeatTurnState
    changed_at: str
    terminal_outcome: TerminalOutcome | None = None
    terminal_outcome_at: str | None = None
    terminal_evidence_id: str | None = None
    interrupted_by: InterruptOrigin | None = None


def role_for_kind(kind: TerminalSessionKind) -> TerminalSessionRole:
    """The leaf-uniqueness role for a launch ``kind``: a shell is a terminal, a harness is a chat."""
    return "terminal" if kind == "terminal" else "chat"


def seat_at_turn_boundary(entry: TerminalCatalogEntry) -> bool:
    """Whether a running seat may receive a push at a turn boundary."""
    if entry.status != "running":
        return False
    if entry.turn_state in {"turn-ended", "awaiting-input"}:
        return True
    return entry.turn_state is None and entry.control_state == "ready"


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
    # The real JSON-primary task document this seat occupies. The stable work identity is this
    # reference plus ``seat_role``; runtime ids are only the current occupant's correlation data.
    task_document_ref: TaskDocumentRef | None = None
    # The role occupying ``task_document_ref``. Unlike ``spawn_role`` (immutable origin provenance),
    # this is current binding state: moving/replacing an occupant updates document + role atomically.
    seat_role: str | None = None
    # A replacement may declare the structural seat it will occupy while the incumbent still owns
    # the live slot. This is the same document identity, never a second address namespace.
    replacement_for_task_document_ref: TaskDocumentRef | None = None
    # Spawned-by provenance (L2 agent dispatch): the spawning session id + lifecycle id when this row
    # was created by the internal ``spawn_agent_session`` primitive behind public
    # ``dispatch_agent``. Written only when set, so a
    # hand-opened or dashboard-opened row reads both back as ``None``. The dashboard reads these to
    # render the orchestration tree (spawner -> spawned edges) once that surface lands.
    spawned_by_session: str | None = None
    spawned_by_lifecycle: str | None = None
    spawned_by_kind: str | None = None
    # Plane-owned structural parent address. This is distinct from spawn ancestry: it records the
    # canonical document+role seat that owns a reviewer manifestation when the reviewer role can
    # validly live at more than one altitude. Occupant ids remain correlation-only.
    structural_parent_task_document_ref: TaskDocumentRef | None = None
    structural_parent_role: str | None = None
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
    # The durable inbox row that completed this occupant's one-call spawn transaction. This is
    # private reconciliation evidence, not a delivery address. It remains after bounded inbox
    # compaction so a retry cannot mistake a briefed live seat for a crash-stranded process.
    dispatch_brief_entry_id: str | None = None
    # Protocol-backed control metadata (260713-PHA-L1): additive and absent on legacy/plain-terminal
    # rows. ``control_endpoint`` is a user-private local socket; the exact identity tuple remains
    # id + tmux_name + created_at, and every IPC request repeats it.
    control_state: ControlState | None = None
    control_endpoint: Path | None = None
    control_protocol: str | None = None
    control_activity: ActivityState | None = None
    control_acceptance: AcceptanceState | None = None
    control_vendor_session_id: str | None = None
    control_pending_interaction: dict[str, object] | None = None
    # Multiplexed sub-agent pendings: additive; the singular
    # slot above stays the parent-thread entry exactly as before.
    control_pending_interactions: list[dict[str, object]] | None = None
    control_last_event_sequence: int | None = None
    control_raw: dict[str, object] | None = None
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
    terminal_outcome: TerminalOutcome | None = None
    terminal_outcome_at: str | None = None
    terminal_evidence_id: str | None = None
    interrupted_by: InterruptOrigin | None = None
    terminal_evidence_sequence: int | None = None
    terminal_native_cursor: str | None = None
    interrupt_requested_by: Literal["developer"] | None = None
    interrupt_requested_at: str | None = None
    interrupt_requested_turn_id: str | None = None
    state_signal_emitted_for: str | None = None
    non_reaction_emitted_for: str | None = None
    compound_idle_emitted_for: str | None = None

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
            task_document_ref=_optional_task_document_ref(data, "taskDocumentRef"),
            seat_role=migrated_seat_role(
                persisted=_optional_str(data, "seatRole"),
                spawn_role=spawn_role,
                kind=kind,
            ),
            replacement_for_task_document_ref=_optional_task_document_ref(
                data, "replacementForTaskDocumentRef"
            ),
            spawned_by_session=_optional_str(data, "spawnedBySession"),
            spawned_by_lifecycle=_optional_str(data, "spawnedByLifecycle"),
            spawned_by_kind=_optional_str(data, "spawnedByKind"),
            structural_parent_task_document_ref=_optional_task_document_ref(
                data, "structuralParentTaskDocumentRef"
            ),
            structural_parent_role=_optional_str(data, "structuralParentRole"),
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
            dispatch_brief_entry_id=_optional_str(data, "dispatchBriefEntryId"),
            control_state=_control_state(data.get("controlState")),
            control_endpoint=_optional_path(data, "controlEndpoint"),
            control_protocol=_optional_str(data, "controlProtocol"),
            control_activity=_control_activity(data.get("controlActivity")),
            control_acceptance=_control_acceptance(data.get("controlAcceptance")),
            control_vendor_session_id=_optional_str(data, "controlVendorSessionId"),
            control_pending_interaction=_optional_object(data.get("controlPendingInteraction")),
            control_pending_interactions=_optional_object_list(
                data.get("controlPendingInteractions")
            ),
            control_last_event_sequence=_optional_non_negative_int(
                data.get("controlLastEventSequence")
            ),
            control_raw=_optional_object(data.get("controlRaw")),
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
            terminal_outcome=_terminal_outcome(data.get("terminalOutcome")),
            terminal_outcome_at=_optional_str(data, "terminalOutcomeAt"),
            terminal_evidence_id=_optional_str(data, "terminalEvidenceId"),
            interrupted_by=_interrupt_origin(data.get("interruptedBy")),
            terminal_evidence_sequence=_optional_non_negative_int(
                data.get("terminalEvidenceSequence")
            ),
            terminal_native_cursor=_optional_str(data, "terminalNativeCursor"),
            interrupt_requested_by=_interrupt_requested_by(data.get("interruptRequestedBy")),
            interrupt_requested_at=_optional_str(data, "interruptRequestedAt"),
            interrupt_requested_turn_id=_optional_str(data, "interruptRequestedTurnId"),
            state_signal_emitted_for=_optional_str(data, "stateSignalEmittedFor"),
            non_reaction_emitted_for=_optional_str(data, "nonReactionEmittedFor"),
            compound_idle_emitted_for=_optional_str(data, "compoundIdleEmittedFor"),
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
                    "taskDocumentRef": _optional_task_document_ref_json(self.task_document_ref),
                }
            )
        )
        data["seatRole"] = self.binding_role
        data.update(
            _present_fields(
                {
                    "replacementForTaskDocumentRef": _optional_task_document_ref_json(
                        self.replacement_for_task_document_ref
                    ),
                    "spawnedBySession": self.spawned_by_session,
                    "spawnedByLifecycle": self.spawned_by_lifecycle,
                    "spawnedByKind": self.spawned_by_kind,
                    "structuralParentTaskDocumentRef": _optional_task_document_ref_json(
                        self.structural_parent_task_document_ref
                    ),
                    "structuralParentRole": self.structural_parent_role,
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
                    "dispatchBriefEntryId": self.dispatch_brief_entry_id,
                    "controlState": self.control_state,
                    "controlEndpoint": _optional_path_text(self.control_endpoint),
                    "controlProtocol": self.control_protocol,
                    "controlActivity": self.control_activity,
                    "controlAcceptance": self.control_acceptance,
                    "controlVendorSessionId": self.control_vendor_session_id,
                    "controlPendingInteraction": self.control_pending_interaction,
                    "controlPendingInteractions": self.control_pending_interactions,
                    "controlLastEventSequence": self.control_last_event_sequence,
                    "controlRaw": self.control_raw,
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
                    "terminalOutcome": self.terminal_outcome,
                    "terminalOutcomeAt": self.terminal_outcome_at,
                    "terminalEvidenceId": self.terminal_evidence_id,
                    "interruptedBy": self.interrupted_by,
                    "terminalEvidenceSequence": self.terminal_evidence_sequence,
                    "terminalNativeCursor": self.terminal_native_cursor,
                    "interruptRequestedBy": self.interrupt_requested_by,
                    "interruptRequestedAt": self.interrupt_requested_at,
                    "interruptRequestedTurnId": self.interrupt_requested_turn_id,
                    "stateSignalEmittedFor": self.state_signal_emitted_for,
                    "nonReactionEmittedFor": self.non_reaction_emitted_for,
                    "compoundIdleEmittedFor": self.compound_idle_emitted_for,
                }
            )
        )
        if self.liveness_failures:
            data["livenessFailures"] = self.liveness_failures
        if self.status == "exited" and self.exit_evidence is not None:
            data["exitEvidence"] = self.exit_evidence
        return data

    def with_attachment(self, attached_at: str) -> TerminalCatalogEntry:
        # ``replace`` preserves every other field (incl. task binding + spawn provenance) so a new
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

    def with_task_binding(
        self,
        task_document_ref: TaskDocumentRef,
        seat_role: str,
    ) -> TerminalCatalogEntry:
        """Move to one seat, retaining address-bound brief proof only for that same seat."""

        same_address = (
            self.binding_task_document_ref == task_document_ref and self.binding_role == seat_role
        )

        return replace(
            self,
            task_document_ref=task_document_ref,
            seat_role=seat_role,
            replacement_for_task_document_ref=None,
            dispatch_brief_entry_id=(self.dispatch_brief_entry_id if same_address else None),
            structural_parent_task_document_ref=(
                self.structural_parent_task_document_ref if same_address else None
            ),
            structural_parent_role=self.structural_parent_role if same_address else None,
        )

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
    def binding_task_document_ref(self) -> TaskDocumentRef | None:
        """The task document this occupant works for, including a staged replacement."""

        return self.task_document_ref or self.replacement_for_task_document_ref


def _string_tuple(raw: object) -> tuple[str, ...] | None:
    """A free-form string list read back from JSON (``None`` for absent/legacy rows)."""
    if not isinstance(raw, list):
        return None
    return tuple(str(item) for item in raw)


def _optional_str(data: dict[str, object], key: str) -> str | None:
    raw = data.get(key)
    return str(raw) if raw is not None else None


def _optional_task_document_ref(data: dict[str, object], key: str) -> TaskDocumentRef | None:
    raw = data.get(key)
    return TaskDocumentRef.model_validate(raw) if raw is not None else None


def _optional_task_document_ref_json(raw: TaskDocumentRef | None) -> dict[str, str] | None:
    return raw.model_dump() if raw is not None else None


def _optional_path(data: dict[str, object], key: str) -> Path | None:
    raw = _optional_str(data, key)
    return Path(raw) if raw is not None else None


def _optional_list(raw: tuple[str, ...] | None) -> list[str] | None:
    return list(raw) if raw is not None else None


def _optional_path_text(raw: Path | None) -> str | None:
    return str(raw) if raw is not None else None


def _optional_object(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict) or not all(isinstance(key, str) for key in raw):
        return None
    return dict(raw)


def _optional_object_list(raw: object) -> list[dict[str, object]] | None:
    if not isinstance(raw, list):
        return None
    entries = [_optional_object(item) for item in raw]
    if any(entry is None for entry in entries):
        return None
    return [entry for entry in entries if entry is not None]


def _optional_non_negative_int(raw: object) -> int | None:
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    return None


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


def _terminal_outcome(raw: object) -> TerminalOutcome | None:
    if raw in ("completed", "interrupted", "failed", "unknown"):
        return raw  # type: ignore[return-value]
    return None


def _interrupt_origin(raw: object) -> InterruptOrigin | None:
    if raw in ("developer", "unknown"):
        return raw  # type: ignore[return-value]
    return None


def _interrupt_requested_by(raw: object) -> Literal["developer"] | None:
    return "developer" if raw == "developer" else None


def _non_negative_int(raw: object) -> int:
    if isinstance(raw, int) and raw > 0:
        return raw
    return 0


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


def _control_activity(raw: object) -> ActivityState | None:
    if raw in {"idle", "running", "blocked", "settling", "unknown"}:
        return raw  # type: ignore[return-value] -- membership narrows the runtime contract.
    return None


def _control_acceptance(raw: object) -> AcceptanceState | None:
    if raw in {"immediate", "queued", "rejected", "unknown", "unsupported"}:
        return raw  # type: ignore[return-value] -- membership narrows the runtime contract.
    return None


LEGACY_CHAT_SEAT_ROLE = "chat"

TERMINAL_SEAT_ROLE = "terminal"


def _clean(role: str | None) -> str | None:
    if role is None:
        return None
    cleaned = role.strip()
    return cleaned or None


def migrated_seat_role(*, persisted: str | None, spawn_role: str | None, kind: str) -> str:
    """Resolve a catalog row's binding role, including the one-time legacy fallback."""

    if kind == "terminal":
        return TERMINAL_SEAT_ROLE
    return _clean(persisted) or _clean(spawn_role) or LEGACY_CHAT_SEAT_ROLE


def _elapsed_seconds(first_failed_at: str, checked_at: datetime) -> float:
    first = datetime.fromisoformat(first_failed_at)
    return (checked_at - first).total_seconds()


DEFAULT_LIVENESS_FAILURE_THRESHOLD = 3
DEFAULT_LIVENESS_FAILURE_WINDOW_SECONDS = 5.0
DEFAULT_PANE_GONE_FAILURE_THRESHOLD = 1
DEFAULT_LIVENESS_SWEEP_INTERVAL_SECONDS = 10.0


@dataclass(frozen=True)
class TerminalCatalogLivenessConfig:
    """The hysteresis that decides when probe failures are allowed to exit-mark a row.

    One value because the thresholds only mean something together: a failure count without the
    minimum window would mark a row on a burst of fast failures, and the pane-gone threshold is
    deliberately lower than the command-failure one because that evidence is definitive. The sweep
    interval belongs with them for the same reason -- it sets how quickly those counts accumulate.
    """

    failure_threshold: int = DEFAULT_LIVENESS_FAILURE_THRESHOLD
    minimum_failure_window_seconds: float = DEFAULT_LIVENESS_FAILURE_WINDOW_SECONDS
    pane_gone_failure_threshold: int = DEFAULT_PANE_GONE_FAILURE_THRESHOLD
    sweep_interval_seconds: float = DEFAULT_LIVENESS_SWEEP_INTERVAL_SECONDS


DEFAULT_LIVENESS_HYSTERESIS = TerminalCatalogLivenessConfig()
