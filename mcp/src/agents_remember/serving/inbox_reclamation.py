"""Confirmed-gone reconciliation for ephemeral agent-notifier inbox alerts."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from agents_remember.controlplane.operator_inbox_records import OperatorInboxEntry
from agents_remember.serving.terminal_catalog import TerminalCatalogEntry

CONFIRMED_GONE_REASON = "subject-session-confirmed-gone"

TmuxSnapshotEvidence = Literal[
    "tmux-session-list",
    "tmux-no-server",
    "tmux-command-failed",
]


@dataclass(frozen=True)
class TmuxSessionNameSnapshot:
    """One evidence-bearing view of every tmux session name."""

    names: frozenset[str]
    evidence: TmuxSnapshotEvidence

    @property
    def ok(self) -> bool:
        return self.evidence != "tmux-command-failed"


TmuxSessionNameSnapshotter = Callable[[], TmuxSessionNameSnapshot]


@dataclass(frozen=True)
class InboxReclamationPlan:
    """The body-free aggregate and row ids produced by one reconciliation pass."""

    resolve_reasons: Mapping[str, str]
    candidate_row_count: int
    unique_subject_count: int
    kept_row_count: int
    evidence_class: str

    @property
    def resolved_row_count(self) -> int:
        return len(self.resolve_reasons)


def snapshot_tmux_session_names() -> TmuxSessionNameSnapshot:
    """List AR-owned tmux names once, failing closed on indeterminate command errors."""
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            check=False,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return TmuxSessionNameSnapshot(frozenset(), "tmux-command-failed")
    if result.returncode == 0:
        return TmuxSessionNameSnapshot(
            frozenset(name for line in result.stdout.splitlines() if (name := line.strip())),
            "tmux-session-list",
        )
    if "no server running" in result.stderr.lower():
        return TmuxSessionNameSnapshot(frozenset(), "tmux-no-server")
    return TmuxSessionNameSnapshot(frozenset(), "tmux-command-failed")


def plan_confirmed_gone_reclamation(
    current: Mapping[str, OperatorInboxEntry],
    *,
    catalog_entries: Sequence[TerminalCatalogEntry],
    snapshotter: TmuxSessionNameSnapshotter,
) -> InboxReclamationPlan:
    """Select only agent-notifier alerts whose exact subject session is positively gone."""
    candidates = [entry for entry in current.values() if _eligible(entry)]
    subjects = {entry.subjectAgentId for entry in candidates if entry.subjectAgentId is not None}
    gone_by_catalog, gone_by_tmux, snapshot = _confirmed_gone_subjects(
        subjects,
        catalog_entries=catalog_entries,
        snapshotter=snapshotter,
    )
    gone_subjects = gone_by_catalog | gone_by_tmux
    resolve_reasons = {
        entry.id: CONFIRMED_GONE_REASON
        for entry in candidates
        if entry.subjectAgentId in gone_subjects
    }
    return InboxReclamationPlan(
        resolve_reasons=resolve_reasons,
        candidate_row_count=len(candidates),
        unique_subject_count=len(subjects),
        kept_row_count=len(candidates) - len(resolve_reasons),
        evidence_class=_evidence_class(
            gone_by_catalog=gone_by_catalog,
            gone_by_tmux=gone_by_tmux,
            snapshot=snapshot,
        ),
    )


def _confirmed_gone_subjects(
    subjects: set[str],
    *,
    catalog_entries: Sequence[TerminalCatalogEntry],
    snapshotter: TmuxSessionNameSnapshotter,
) -> tuple[set[str], set[str], TmuxSessionNameSnapshot | None]:
    catalog_by_id = {entry.id: entry for entry in catalog_entries}
    gone_by_catalog: set[str] = set()
    missing_from_catalog: set[str] = set()
    for subject in subjects:
        catalog_entry = catalog_by_id.get(subject)
        if catalog_entry is None:
            missing_from_catalog.add(subject)
        elif catalog_entry.status == "terminated":
            gone_by_catalog.add(subject)
    if not missing_from_catalog:
        return gone_by_catalog, set(), None
    snapshot = snapshotter()
    if not snapshot.ok:
        return gone_by_catalog, set(), snapshot
    gone_by_tmux = {
        subject for subject in missing_from_catalog if f"ar-{subject}" not in snapshot.names
    }
    return gone_by_catalog, gone_by_tmux, snapshot


def _eligible(entry: OperatorInboxEntry) -> bool:
    return (
        entry.state == "pending"
        # Both values are the same relay-authored alert until the rename window closes.
        and entry.createdBy in {"supervisor", "agent-notifier"}
        and entry.messageKind in ("nudge", "escalation")
        and entry.subjectAgentId is not None
    )


def _evidence_class(
    *,
    gone_by_catalog: set[str],
    gone_by_tmux: set[str],
    snapshot: TmuxSessionNameSnapshot | None,
) -> str:
    if gone_by_catalog and gone_by_tmux:
        return "catalog-terminal+tmux-name-absence"
    if gone_by_catalog:
        return "catalog-terminal"
    if gone_by_tmux:
        return "tmux-name-absence"
    if snapshot is not None and not snapshot.ok:
        return "tmux-indeterminate"
    return "no-positive-gone-evidence"
