"""The omission report file: its home, the owner's recorded reconciliations, and the full write.

The report is the one place an owner records reconciliation evidence for parked authoring a
resolution dropped, and every continue rewrites it with the records it read back. A report
that cannot be read is therefore never taken as "nothing recorded": the rewrite would replace
every recorded reconciliation with an empty list. Only a missing file means no records; any
other failure refuses and leaves the file exactly as the owner left it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING

from agents_remember.errors import AgentsRememberError
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.observer.events import now_iso
from agents_remember.worktrees.sync_transaction_state import SyncOperationRecord
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.worktrees.sync_parked_omissions import OmittedItem, ParkedOmissions

OMISSIONS_SCHEMA = "ar-sync-omissions/v1"
_WAY_ON = (
    "then run worktree_sync with resolution_action='continue'; or end the sync with "
    "resolution_action='cancel'."
)


class OmissionReportError(AgentsRememberError):
    """The omission report could not be read or written; the file is left as it was."""


class OmissionReportUnreadableError(OmissionReportError):
    """The owner's report exists but is not a readable reconciliation document."""


class OmissionReportWriteError(OmissionReportError):
    """The full report could not be written to the enclosure's scratch."""


def report_path(contract: WorktreeContract, record: SyncOperationRecord, side: str) -> Path:
    """The report's home in the enclosure's own scratch, never in a synced worktree."""

    return (
        contract.worktree_group / "scratch" / f"sync-omissions-{record.generation}" / f"{side}.json"
    )


def read_reconciliations(report: Path) -> dict[str, list[object]]:
    """The reconciliations an owner recorded in the report, keyed by parked path.

    A missing report holds no records. A report that cannot be read, is not JSON, or does not
    have the report's shape raises :class:`OmissionReportUnreadableError` naming the file.
    """

    try:
        text = report.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except (OSError, UnicodeDecodeError) as error:
        raise _unreadable(report, f"could not be read ({error})", "make it readable") from error
    try:
        data = json.loads(text)
    except ValueError as error:
        raise _unreadable(report, f"is not valid JSON ({error})") from error
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        raise _unreadable(report, "is not a report object with an 'entries' list")
    return dict(_entry_records(report, index, entry) for index, entry in enumerate(entries))


def _entry_records(report: Path, index: int, entry: object) -> tuple[str, list[object]]:
    """One entry's path and recorded reconciliations; a malformed entry refuses."""

    if not isinstance(entry, dict):
        raise _unreadable(report, f"holds entry {index} that is not an object")
    path = entry.get("path")
    reconciliations = entry.get("reconciliations", [])
    if not isinstance(path, str) or not isinstance(reconciliations, list):
        raise _unreadable(
            report, f"holds entry {index} without a string 'path' and a 'reconciliations' list"
        )
    return path, list(reconciliations)


def _unreadable(
    report: Path, problem: str, remedy: str = "fix the JSON in it"
) -> OmissionReportUnreadableError:
    return OmissionReportUnreadableError(
        f"The omissions report at {report.as_posix()} {problem}. The sync left the file "
        f"untouched and settled nothing; {remedy}, {_WAY_ON}"
    )


def write_report(inventory: ParkedOmissions) -> ParkedOmissions:
    """Write the full report where the enclosure's cleanup owns it; return what was written."""

    report = Path(inventory.reportPath)
    try:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_bytes(canonical_json_bytes(_report_document(inventory)))
    except OSError as error:
        raise OmissionReportWriteError(
            f"The omissions report could not be written to {report.as_posix()} ({error}). "
            f"Make that location writable, {_WAY_ON}"
        ) from error
    return inventory


def _report_document(inventory: ParkedOmissions) -> dict[str, object]:
    """The full report, preserving the owner's recorded reconciliations."""

    identity = inventory.identity
    records: Mapping[str, list[object]] = inventory.reconciliations
    return {
        "schema": OMISSIONS_SCHEMA,
        "generatedAt": now_iso(),
        "side": inventory.side,
        "contractPath": identity.contractPath,
        "worktree": identity.worktree,
        "carriedHead": identity.carriedHead,
        "preSyncHead": identity.preSyncHead,
        "stash": identity.stash,
        "totals": {
            "parkedPaths": inventory.parkedPaths,
            "omittedPaths": inventory.omittedPaths,
            "omittedItems": inventory.omittedItems,
            "reconciledItems": inventory.reconciledItems,
        },
        "entries": [
            {
                "path": entry.path,
                "evaluatedPath": entry.evaluatedPath,
                "kind": entry.parkedKind,
                "omitted": _items(entry.items),
                "remaining": _items(entry.remaining),
                "reconciliations": records.get(entry.path, []),
            }
            for entry in inventory.entries
        ],
    }


def _items(items: tuple[OmittedItem, ...]) -> list[dict[str, object]]:
    return [{"class": item.kind, "line": item.line, "count": item.count} for item in items]
