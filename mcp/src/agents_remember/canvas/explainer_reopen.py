"""What an archive takes of a leaf's explainer, and why a reopen after it is refused (R103 v2).

A removal keeps the explainer's published revisions and the seat counter beside them, so a reopen
starts a new seat at the next generation. An archive (master retirement, sprint archive) reclaims
both with the task's frames. Its receipt therefore names, for each leaf, the revisions and the
counter it took and why a reopen is unavailable: a fresh counter would number seats into ids the
archived chats still hold. A build reads that receipt and refuses with the reason it records.

The receipt the task keeps is the one witness: nothing is left inside the explainer folder, so a
removal stays complete.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from agents_remember.application.review_artifact_receipts import CLEANUP_REPORT_NAME
from agents_remember.canvas.explainer_paths import (
    EXPLAINER_FRAME,
    FRAMES_DIRECTORY,
    REVISIONS_DIRECTORY,
    SEAT_COUNTER_NAME,
    explained_task,
)
from agents_remember.canvas.explainer_state import ExplainerStateError, generation
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.tasks.task_paths import ARCHIVE_DIR, slugify

__all__ = [
    "RECLAIMED",
    "WOULD_RECLAIM",
    "absent_task_refusal",
    "archived_refusal",
    "archived_reopen",
    "reclaimed_explainers",
    "reopen_unavailable",
]

#: The state of a record whose revisions and counter the archive took, or is about to take (the
#: plan an archive attempt writes before it deletes anything).
RECLAIMED = "reclaimed"
WOULD_RECLAIM = "would-reclaim"


def reopen_unavailable(leaf: str, revisions: list[int], number: int | None) -> str:
    """The sentence the receipt records, and the refusal a later build answers with."""

    published = ", ".join(str(revision) for revision in revisions) or "none"
    counter = "no seat counter" if number is None else f"its seat counter (generation {number})"
    return (
        f"the task was archived, so the explainer of leaf {leaf} cannot be reopened: its published "
        f"revisions {published} and {counter} were reclaimed with the task's frames, and a new "
        "counter would number seats into ids its archived chats still hold"
    )


def absent_task_refusal(tasks: Path, repository: str, master: str) -> str:
    """The plain fact that a master has no folder, then where to look if it was archived.

    It never asserts the archive and never looks: ``tasks`` is ``<coordination>/tasks/<repository>``,
    and the receipt's place under ``0_archive`` is named by the archive's naming rule alone.
    """

    receipt = tasks / ARCHIVE_DIR / master / "notes" / "reports" / CLEANUP_REPORT_NAME
    return (
        f"the task {repository}/{master} has no directory at {tasks / master}; if this master was "
        "archived, the archive reclaimed the explainer's revisions and seat counter, and reopening "
        f"does not restore them; the receipt is under {ARCHIVE_DIR} for that master, at {receipt}"
    )


def archived_refusal(leaf: str, receipt: Path | None) -> str:
    """The product's own sentence for a leaf whose task an archive took, and where its receipt is.

    It is static: the refusal reads nothing of the archived task, so a moved layout cannot make it
    unreadable. ``receipt`` is where the archive files its receipt, named from the task root.
    """

    where = (
        receipt.as_posix()
        if receipt is not None
        else f"the archived task's notes/reports/{CLEANUP_REPORT_NAME}"
    )
    return (
        f"the task was archived: the archive reclaimed the published revisions and the seat "
        f"counter of the explainer of leaf {leaf}, and reopening does not restore them; the "
        f"archive's receipt is filed at {where}"
    )


def reclaimed_explainers(
    task_root: Path, leaves: Iterable[str], *, dry_run: bool = False
) -> list[dict[str, Any]]:
    """One record per explainer frame of the task: what an archive of it takes, before it does.

    ``leaves`` names the task's leaves, so a frame folder (a slug) is recorded under its leaf id.
    """

    area = durable_reports_root(task_root) / FRAMES_DIRECTORY
    if area.is_symlink() or not area.is_dir():
        return []
    by_slug = {slugify(leaf): leaf for leaf in leaves}
    records: list[dict[str, Any]] = []
    for frame in sorted(area.glob(f"*/{EXPLAINER_FRAME}")):
        if frame.is_symlink() or frame.parent.is_symlink() or not frame.is_dir():
            continue
        leaf = by_slug.get(frame.parent.name, frame.parent.name)
        revisions = _revisions(frame / REVISIONS_DIRECTORY)
        counter = frame / SEAT_COUNTER_NAME
        number = _counter(counter)
        records.append(
            {
                "leaf": leaf,
                "revisions": revisions,
                "seatCounter": counter.as_posix(),
                "generation": number,
                "state": WOULD_RECLAIM if dry_run else RECLAIMED,
                "reopen": reopen_unavailable(leaf, revisions, number),
            }
        )
    return records


def _revisions(folder: Path) -> list[int]:
    if folder.is_symlink() or not folder.is_dir():
        return []
    return sorted(int(path.name) for path in folder.iterdir() if path.name.isdigit())


def _counter(path: Path) -> int | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return generation(path)
    except ExplainerStateError:
        return None


def archived_reopen(workspace: Path) -> str | None:
    """The reason an archive recorded for this leaf's explainer, or ``None`` when none did.

    Every receipt the task keeps is read -- the latest and each one set aside -- because a later
    archive attempt that found the frames already gone records no explainer of its own. A plan an
    interrupted attempt wrote counts too: its frames may already be gone.
    """

    task = explained_task(workspace)
    if task is None:
        return None
    reports = durable_reports_root(task)
    stem, suffix = CLEANUP_REPORT_NAME.rsplit(".", 1)
    slug = slugify(workspace.name)
    for path in sorted(reports.glob(f"{stem}*.{suffix}")):
        for record in _records(path):
            state = record.get("state")
            if state in {RECLAIMED, WOULD_RECLAIM} and slugify(str(record.get("leaf"))) == slug:
                return str(record.get("reopen") or reopen_unavailable(workspace.name, [], None))
    return None


def _records(path: Path) -> list[dict[str, Any]]:
    if path.is_symlink() or not path.is_file():
        return []
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    records = document.get("explainerRecords") if isinstance(document, dict) else None
    return [record for record in records or [] if isinstance(record, dict)]
