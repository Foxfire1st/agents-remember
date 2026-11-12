"""The parked authoring a continued sync resolution no longer carries.

A parked candidate is reapplied into the worktree and its conflict resolved by the agent.
The settlement used to ask git only whether unmerged paths remained, so a resolution that
took the official side dropped the parked authoring silently: the tree held no unmerged
path, the sync published "resolved", finalized, and retired the stash -- the only saved copy
of those lines. Here the parked change itself is measured against the resolved tree and the
owner's reconciliation evidence is verified before a continue may call the sync resolved.
The measurement writes nothing; ``sync_omission_report`` owns the report file.

Every parked path is read once: its pre-sync blob, its parked blob and its resolved worktree
entry. The owner's records are indexed by line once per path, and a replacement or merged-fact
target is read once per path however many items cite it, so the cost of a continue grows with
the paths and items it judges, never with their product.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from agents_remember.errors import AuthorityError
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.models.worktree import SyncOmissionsProjection, SyncSide
from agents_remember.worktrees.modules.git import is_ancestor
from agents_remember.worktrees.sync_omission_report import read_reconciliations, report_path
from agents_remember.worktrees.sync_transaction_authority import update_record
from agents_remember.worktrees.sync_transaction_git import (
    MAX_NAMED_SYNC_PATHS,
    ExpectedReappliedContent,
    parked_path_content,
    renamed_parked_paths,
    side_branch_head,
    stashed_parked_paths,
    worktree_entry_content,
)
from agents_remember.worktrees.sync_transaction_git._read import revision_content
from agents_remember.worktrees.sync_transaction_state import (
    SyncOperationRecord,
    SyncOperationStore,
    SyncSideRecord,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

_MISSING_ADDITION = "missing-addition"
_RETAINED_REMOVAL = "retained-removal"
_MISSING_DELETION = "missing-deletion"
_CHANGED_CONTENT = "changed-content"


@dataclass(frozen=True)
class OmittedItem:
    """One authored line (or whole blob) the resolved tree no longer holds."""

    kind: str
    line: str
    count: int = 1


@dataclass(frozen=True)
class OmissionEntry:
    """One parked path, the authored items it carried, and what reconciliation kept."""

    path: str
    evaluatedPath: str
    parkedKind: str
    items: tuple[OmittedItem, ...]
    remaining: tuple[OmittedItem, ...]
    reconciled: int


@dataclass(frozen=True)
class ReportIdentity:
    """The transaction facts the full report names."""

    contractPath: str
    worktree: str
    preSyncHead: str
    stash: str
    carriedHead: str


@dataclass(frozen=True)
class ParkedOmissions:
    """The measured omission inventory for one side of one continue.

    ``entries`` holds every path with omitted items, reconciled ones included, so the report
    keeps the owner's records for them; the totals count only what is still outstanding.
    """

    side: SyncSide
    reportPath: str
    parkedPaths: int
    entries: tuple[OmissionEntry, ...]
    reconciledItems: int
    identity: ReportIdentity
    reconciliations: Mapping[str, list[object]]

    @property
    def omittedPaths(self) -> int:
        return sum(1 for entry in self.entries if entry.remaining)

    @property
    def omittedItems(self) -> int:
        return sum(len(entry.remaining) for entry in self.entries)

    @property
    def omittedByKind(self) -> dict[str, int]:
        """Totals by omission class, so the owner sees the shape before opening the report."""

        counts: Counter[str] = Counter()
        for entry in self.entries:
            counts.update(item.kind for item in entry.remaining)
        return dict(sorted(counts.items()))

    def projection(self) -> SyncOmissionsProjection:
        """The answer block: totals and the full report's path, never the list."""

        return SyncOmissionsProjection(
            side=self.side,
            report=self.reportPath,
            parkedPaths=self.parkedPaths,
            omittedPaths=self.omittedPaths,
            omittedItems=self.omittedItems,
            omittedByKind=self.omittedByKind,
            reconciledItems=self.reconciledItems,
        )

    def as_payload(self) -> dict[str, object]:
        """The answer block as the sync answer carries it."""

        return self.projection().model_dump(mode="json", exclude_none=True)

    def summary(self, side: SyncSideRecord) -> str:
        """The refusal: what is missing, where the report is, and the exact way on."""

        named = [entry.path for entry in self.entries if entry.remaining]
        shown = _named(named)
        items = "1 item" if self.omittedItems == 1 else f"{self.omittedItems} items"
        paths = "1 path" if len(named) == 1 else f"{len(named)} paths"
        by_kind = ", ".join(f"{kind} {count}" for kind, count in self.omittedByKind.items())
        reconciled = (
            "1 item is already reconciled"
            if self.reconciledItems == 1
            else f"{self.reconciledItems} items are already reconciled"
        )
        return (
            f"The {side.side} resolution no longer holds {items} of the parked authoring across "
            f"{paths} ({by_kind}): {shown}. {reconciled} of {self.parkedPaths} parked paths. "
            f"The parked candidate stays in stash {side.wipStash}, potentially its only saved "
            f"copy. The full report is at {self.reportPath}: reconcile the paths in the worktree, "
            "or record each item's replacement or merged fact in that file, then run "
            "worktree_sync with resolution_action='continue'; or end the sync with "
            "resolution_action='cancel'."
        )


@dataclass(frozen=True)
class _ParkedPath:
    """One parked path read once: the pre-sync, parked and resolved content it is judged by."""

    side: SyncSideRecord
    carriedHead: str
    path: str
    evaluatedPath: str
    base: bytes | None
    parked: ExpectedReappliedContent
    resolved: ExpectedReappliedContent

    @cached_property
    def parked_lines(self) -> Counter[bytes]:
        return _line_counts(self.parked.content) if self.parked.kind == "bytes" else Counter()

    @property
    def kind(self) -> str:
        if self.base is None and self.parked.kind == "bytes":
            return "added-file"
        if self.base is not None and self.parked.kind == "absent":
            return "deleted-file"
        return "modified"


def _named(paths: list[str]) -> str:
    shown = ", ".join(paths[:MAX_NAMED_SYNC_PATHS])
    if len(paths) > MAX_NAMED_SYNC_PATHS:
        shown += f" and {len(paths) - MAX_NAMED_SYNC_PATHS} more"
    return shown


def journal_omissions(
    store: SyncOperationStore,
    record: SyncOperationRecord,
    side_name: SyncSide,
    side: SyncSideRecord,
    inventory: ParkedOmissions,
) -> SyncOperationRecord:
    """Journal the report and its count on the side, keeping the phase open."""

    updated = side.model_copy(
        update={
            "omissionReport": inventory.reportPath,
            "omittedItems": inventory.omittedItems,
        }
    )
    return update_record(store, record, phase=record.phase, side=updated, side_name=side_name)


def measure(
    side: SyncSideRecord,
    contract: WorktreeContract,
    record: SyncOperationRecord,
) -> ParkedOmissions:
    """Measure every parked-changed path against the resolved worktree; write nothing.

    Reconciliations already recorded in the report are read back and verified before the
    totals are published, and the result carries everything the full report names, so a
    continue writes it with ``write_report`` and a preview only reports it.
    """

    report = report_path(contract, record, side.side)
    records = read_reconciliations(report)
    carried = side_branch_head(side)
    renames = renamed_parked_paths(side, carried)
    paths = stashed_parked_paths(side)
    entries: list[OmissionEntry] = []
    for path in paths:
        parked = _read_parked_path(side, carried, path, renames)
        items = _omissions(parked)
        if items:
            entries.append(_judged_entry(parked, items, records.get(path) or []))
    return ParkedOmissions(
        side=side.side,
        reportPath=report.as_posix(),
        parkedPaths=len(paths),
        entries=tuple(entries),
        reconciledItems=sum(entry.reconciled for entry in entries),
        identity=ReportIdentity(
            contractPath=record.contractPath,
            worktree=side.worktree,
            preSyncHead=side.preSyncHead,
            stash=side.wipStash,
            carriedHead=carried,
        ),
        reconciliations=records,
    )


def _read_parked_path(
    side: SyncSideRecord, carried: str, path: str, renames: dict[str, str]
) -> _ParkedPath:
    """Read one parked path's three contents, following a rename the carried line made."""

    evaluated, resolved = path, worktree_entry_content(side, path)
    renamed = renames.get(path)
    if resolved.kind == "absent" and renamed is not None:
        evaluated, resolved = renamed, worktree_entry_content(side, renamed)
    return _ParkedPath(
        side=side,
        carriedHead=carried,
        path=path,
        evaluatedPath=evaluated,
        base=revision_content(Path(side.repository), side.preSyncHead, path),
        parked=parked_path_content(side, path),
        resolved=resolved,
    )


def _judged_entry(
    parked: _ParkedPath, items: tuple[OmittedItem, ...], records: list[object]
) -> OmissionEntry:
    remaining, reconciled = _reconcile(items, records, parked)
    return OmissionEntry(
        path=parked.path,
        evaluatedPath=parked.evaluatedPath,
        parkedKind=parked.kind,
        items=items,
        remaining=remaining,
        reconciled=reconciled,
    )


def _omissions(parked: _ParkedPath) -> tuple[OmittedItem, ...]:
    """The parked authored items the resolved content does not hold."""

    content, resolved, path = parked.parked, parked.resolved, parked.path
    if content.kind == "absent":
        return () if resolved.kind == "absent" else (OmittedItem(_MISSING_DELETION, path),)
    if content.kind != "bytes":
        return _identity_omissions(content, resolved, path)
    if _binary(content.content) or (resolved.kind == "bytes" and _binary(resolved.content)):
        return _identity_omissions(content, resolved, path)
    if resolved.kind != "bytes":
        return (OmittedItem(_CHANGED_CONTENT, path, len(parked.parked_lines)),)
    base = Counter() if parked.base is None else _line_counts(parked.base)
    return _line_omissions(base, parked.parked_lines, _line_counts(resolved.content))


def _identity_omissions(
    parked: ExpectedReappliedContent, resolved: ExpectedReappliedContent, path: str
) -> tuple[OmittedItem, ...]:
    """A whole-blob comparison for content whose lines cannot be counted."""

    if (parked.kind, parked.content) == (resolved.kind, resolved.content):
        return ()
    return (OmittedItem(_CHANGED_CONTENT, path),)


def _line_omissions(
    base: Counter[bytes],
    parked: Counter[bytes],
    counts: Counter[bytes],
) -> tuple[OmittedItem, ...]:
    """Authored additions the resolved tree lacks and authored removals it kept.

    A line the parked change added is present when the resolved file holds at least as many
    occurrences; a line it removed is present when the resolved file holds no more than the
    parked change left. Blank lines carry no authored fact and are not counted.
    """

    items: list[OmittedItem] = []
    for line, parked_count in parked.items():
        base_count = base.get(line, 0)
        if parked_count > base_count:
            missing = parked_count - counts.get(line, 0)
            if missing > 0:
                items.append(OmittedItem(_MISSING_ADDITION, _text(line), missing))
        elif parked_count < base_count:
            retained = counts.get(line, 0) - parked_count
            if retained > 0:
                items.append(OmittedItem(_RETAINED_REMOVAL, _text(line), retained))
    return tuple(items)


def _line_counts(content: bytes) -> Counter[bytes]:
    return Counter(line for line in content.split(b"\n") if line.strip())


def _binary(content: bytes) -> bool:
    return b"\0" in content


def _text(line: bytes) -> str:
    return line.decode("utf-8", "backslashreplace")


def _reconcile(
    items: tuple[OmittedItem, ...],
    records: list[object],
    parked: _ParkedPath,
) -> tuple[tuple[OmittedItem, ...], int]:
    """Keep only the items no recorded evidence resolves; count what evidence resolved."""

    evidence = _EvidenceReader(parked, _records_by_line(records))
    remaining = tuple(item for item in items if not evidence.holds(item))
    return remaining, len(items) - len(remaining)


def _records_by_line(records: list[object]) -> dict[str, dict[str, object]]:
    """The first record naming each line, as the owner wrote them."""

    indexed: dict[str, dict[str, object]] = {}
    for record in records:
        if isinstance(record, dict) and isinstance(record.get("line"), str):
            indexed.setdefault(record["line"], record)
    return indexed


class _EvidenceReader:
    """Whether recorded reconciliations hold, reading each cited target once per path.

    A replacement is remeasured in the resolved worktree now; a merged fact must be a commit
    on the carried line's own history. Either must hold the authored fact itself: the named
    replacement line, the item's own line at that commit, or the parked file's lines. A
    replacement path the owner wrote is confined to the worktree: one that escapes it, by
    ``..`` or as an absolute path, is no evidence.
    """

    def __init__(self, parked: _ParkedPath, records_by_line: dict[str, dict[str, object]]):
        self._parked = parked
        self._records = records_by_line
        self._worktree: dict[str, Counter[bytes] | None] = {}
        self._revisions: dict[tuple[str, str], Counter[bytes] | None] = {}
        self._ancestors: dict[str, bool] = {}

    def holds(self, item: OmittedItem) -> bool:
        evidence = self._records.get(item.line)
        if evidence is None:
            return False
        replacement = evidence.get("replacement")
        if isinstance(replacement, dict) and self._replacement_holds(replacement, item):
            return True
        merged = evidence.get("mergedFact")
        return isinstance(merged, dict) and self._merged_fact_holds(merged, item)

    def _replacement_holds(self, replacement: dict[str, object], item: OmittedItem) -> bool:
        counts = self._worktree_lines(replacement.get("path"))
        if counts is None:
            return False
        line = str(replacement.get("line", ""))
        if line:
            return bool(counts.get(line.encode(), 0))
        return self._holds_fact(counts, item)

    def _merged_fact_holds(self, merged: dict[str, object], item: OmittedItem) -> bool:
        commit = str(merged.get("commit", ""))
        target = str(merged.get("path") or self._parked.evaluatedPath)
        if not commit or not self._on_carried_line(commit):
            return False
        key = (commit, target)
        if key not in self._revisions:
            content = revision_content(Path(self._parked.side.repository), commit, target)
            self._revisions[key] = None if content is None else _line_counts(content)
        counts = self._revisions[key]
        return counts is not None and self._holds_fact(counts, item)

    def _worktree_lines(self, requested: object) -> Counter[bytes] | None:
        """The resolved worktree's line counts for a cited target, or ``None`` for no bytes."""

        target = self._confined(requested)
        if target is None:
            return None
        if target not in self._worktree:
            content = worktree_entry_content(self._parked.side, target)
            self._worktree[target] = (
                _line_counts(content.content) if content.kind == "bytes" else None
            )
        return self._worktree[target]

    def _confined(self, requested: object) -> str | None:
        if not requested:
            return self._parked.evaluatedPath
        try:
            return confine_rel(Path(self._parked.side.worktree), str(requested))
        except AuthorityError:
            return None

    def _on_carried_line(self, commit: str) -> bool:
        if commit not in self._ancestors:
            repository = Path(self._parked.side.repository)
            self._ancestors[commit] = is_ancestor(repository, commit, self._parked.carriedHead)
        return self._ancestors[commit]

    def _holds_fact(self, counts: Counter[bytes], item: OmittedItem) -> bool:
        """Whether one content's line counts hold the parked fact this item names."""

        if item.kind in {_CHANGED_CONTENT, _MISSING_DELETION}:
            if self._parked.parked.kind != "bytes":
                return True
            return all(
                counts.get(line, 0) >= count for line, count in self._parked.parked_lines.items()
            )
        return bool(counts.get(item.line.encode(), 0))
