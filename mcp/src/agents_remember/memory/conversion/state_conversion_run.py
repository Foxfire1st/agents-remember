"""One run of the state conversion: prepare its plan and report before writing.

The run writes only under the memory root it is given and reads the task documents under the task
root; it touches no coordination state. A dry run writes nothing into the memory.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from agents_remember.errors import StateConversionError
from agents_remember.memory.conversion.state_conversion import (
    StateConversionPlan,
    plan_state_conversion,
)
from agents_remember.memory.conversion.state_files import (
    ConversionReportFile,
    read_knowledge_json,
    selects_knowledge_path,
    write_all_or_nothing,
)
from agents_remember.memory.conversion.task_review_facts import read_task_owners
from agents_remember.models.knowledge_files.documents import LAYOUT_MARKER_PATH


@dataclass(frozen=True)
class StateConversionRequest:
    memory_root: Path
    task_root: Path
    apply: bool


@dataclass(frozen=True)
class StateConversionRun:
    request: StateConversionRequest
    at: str
    plan: StateConversionPlan
    task_documents_read: int
    documents_without_owner: dict[str, str]
    input_paths: frozenset[Path]

    def report_document(self) -> dict[str, Any]:
        """The run's full report: counts, the files changed (or to change), owners without fact."""

        report = {
            "memoryRoot": self.request.memory_root.as_posix(),
            "taskRoot": self.request.task_root.as_posix(),
            "applied": self.request.apply,
            "at": self.at,
            "taskDocumentsRead": self.task_documents_read,
            "counts": self.plan.counts.to_document(),
            "filesChanged": len(self.plan.changes),
            "changedFiles": sorted(self.plan.changes),
            "ownersWithoutReviewFact": dict(sorted(self.plan.owners_without_fact.items())),
        }
        if self.documents_without_owner:
            report["documentsWithoutOwner"] = dict(sorted(self.documents_without_owner.items()))
        return report

    def apply(self, *, report: ConversionReportFile | None = None) -> None:
        """Write the prepared conversion and any file report as one operation."""

        write_all_or_nothing(
            self.request.memory_root, self.plan.changes, self.plan.originals, report=report
        )

    def file_report(self, path: Path, payload: bytes) -> ConversionReportFile:
        """Bind a report destination distinct from the conversion's evidence and writes."""

        target = path.resolve()
        memory = self.request.memory_root
        if (
            selects_knowledge_path(memory, path)
            or selects_knowledge_path(memory, target)
            or any(target == used.resolve() for used in self.input_paths)
        ):
            raise StateConversionError(
                f"the report {path} overlaps the conversion's input or memory output; choose "
                "a different --report path and run the command again"
            )
        return ConversionReportFile(target, payload)


def clock_now() -> str:
    """The run's one time, local with its offset, to the second."""

    return datetime.now().astimezone().isoformat(timespec="seconds")


def prepare_state_conversion(request: StateConversionRequest, *, at: str) -> StateConversionRun:
    """Plan the conversion of ``request.memory_root`` without writing either output."""

    files = read_knowledge_json(request.memory_root)
    owners = read_task_owners(request.task_root)
    plan = plan_state_conversion(files, owners, at=at, repository=request.memory_root)
    memory_paths = {request.memory_root / name for name in (*files, LAYOUT_MARKER_PATH)}
    return StateConversionRun(
        request,
        at,
        plan,
        len(owners.document_paths),
        owners.documents_without_owner,
        frozenset(memory_paths | owners.document_paths),
    )
