"""Leaf task-document lookup and lifecycle stamping (task domain).

A leaf's JSON-primary task document is the lifecycle-keyed work content of its
enclosure (task_doc stamps ``lifecycleId`` at create time when authored against
a leaf contract). Reopen (L11) makes the binding survive restarts too: the doc
follows the enclosure across lifecycles by explicit restamp, never by read-time
heuristics. The lookup mirrors the observer projection's exact joins — doc id,
``enclosures[]`` refs, then file stem, all case-insensitive (doc ids are
authored labels like ``260628-L11`` while enclosure leaf ids are lowercase
directory names like ``260628-l11``).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from agents_remember.tasks.document import TaskDocument
from agents_remember.tasks.readiness import CompletionBlocker, completion_blockers
from agents_remember.tasks.store import read_task_doc


class TerminalLeafResolutionError(ValueError):
    """A terminal writer could not prove one exact contract-bound leaf document."""


class LeafLifecycleRestampBlocked(ValueError):
    """A lifecycle restamp would republish a false terminal leaf claim."""

    def __init__(self, plan: LeafLifecycleRestampPlan) -> None:
        self.plan = plan
        exact = [blocker.model_dump() for blocker in plan.blockers]
        super().__init__(f"leaf lifecycle restamp refused; unresolved work units: {exact!r}")


@dataclass(frozen=True)
class LeafLifecycleRestampPlan:
    """A read-only lifecycle-restamp decision, safe to compute before start effects."""

    doc_path: Path | None
    lifecycle_id: str
    candidate: TaskDocument | None
    changed: bool
    blockers: tuple[CompletionBlocker, ...] = ()


def _matches_leaf_identity(json_path: Path, doc: TaskDocument, leaf_id: str) -> bool:
    want = leaf_id.strip().lower()
    return (
        (doc.id or "").strip().lower() == want
        or any((ref.leafId or "").strip().lower() == want for ref in doc.enclosures)
        or json_path.stem.strip().lower() == want
    )


def find_leaf_doc(task_root: Path, leaf_id: str) -> tuple[Path, TaskDocument] | None:
    """The leaf's task document under ``task_root``, or None when never authored."""
    want = leaf_id.strip().lower()
    if not want:
        return None
    for json_path in sorted(task_root.glob("*.json")):
        try:
            doc = read_task_doc(json_path)
        except Exception:
            continue
        if doc.kind == "master":
            continue
        if _matches_leaf_identity(json_path, doc, want):
            return json_path, doc
    return None


def resolve_terminal_leaf_doc(
    task_root: Path,
    leaf_id: str,
    *,
    asserted_path: Path | None = None,
) -> tuple[Path, TaskDocument] | None:
    """Resolve exactly one leaf for a terminal write, failing closed on identity doubt.

    Start/reopen deliberately retain :func:`find_leaf_doc`'s fail-soft behavior. A
    terminal transition has a stronger contract: an asserted path is an identity
    assertion and duplicate matches are ambiguous. Unrelated sibling documents are
    normal in a series root and do not prevent a true no-document result for this leaf.
    """
    root = task_root.resolve(strict=False)
    want = leaf_id.strip()
    if not want:
        raise TerminalLeafResolutionError("terminal leaf resolution requires a nonblank leaf id")

    asserted = _terminal_asserted_path(root, asserted_path)
    matches = _terminal_matches(root, want, asserted)
    if len(matches) > 1:
        paths = ", ".join(path.as_posix() for path, _doc in matches)
        raise TerminalLeafResolutionError(
            f"leaf identity {leaf_id!r} is ambiguous across terminal task documents: {paths}"
        )
    if asserted is not None:
        _assert_terminal_path(asserted, want, matches)
    return matches[0] if matches else None


def _terminal_asserted_path(root: Path, asserted_path: Path | None) -> Path | None:
    if asserted_path is None:
        return None
    asserted = asserted_path.resolve(strict=False)
    if asserted.parent != root or asserted.suffix != ".json":
        raise TerminalLeafResolutionError(
            f"asserted task document must be a direct JSON child of {root}: {asserted}"
        )
    if not asserted.exists():
        raise TerminalLeafResolutionError(f"asserted task document does not exist: {asserted}")
    return asserted


def _terminal_matches(
    root: Path,
    leaf_id: str,
    asserted: Path | None,
) -> list[tuple[Path, TaskDocument]]:
    matches: list[tuple[Path, TaskDocument]] = []
    for json_path in sorted(root.glob("*.json")):
        resolved_path = json_path.resolve(strict=False)
        try:
            doc = read_task_doc(json_path)
        except (OSError, ValueError) as exc:
            if resolved_path == asserted or json_path.stem.strip().lower() == leaf_id.lower():
                raise TerminalLeafResolutionError(
                    f"cannot read terminal leaf candidate {resolved_path}: {exc}"
                ) from exc
            continue
        if doc.kind == "master":
            continue
        if _matches_leaf_identity(json_path, doc, leaf_id):
            matches.append((resolved_path, doc))
    return matches


def _assert_terminal_path(
    asserted: Path,
    leaf_id: str,
    matches: list[tuple[Path, TaskDocument]],
) -> None:
    try:
        asserted_doc = read_task_doc(asserted)
    except (OSError, ValueError) as exc:
        raise TerminalLeafResolutionError(
            f"cannot read asserted task document {asserted}: {exc}"
        ) from exc
    if asserted_doc.kind == "master" or not _matches_leaf_identity(asserted, asserted_doc, leaf_id):
        raise TerminalLeafResolutionError(
            f"asserted task document {asserted} is not bound to contract leaf {leaf_id!r}"
        )
    if not matches or matches[0][0] != asserted:
        resolved = matches[0][0] if matches else None
        raise TerminalLeafResolutionError(
            f"asserted task document {asserted} does not equal contract-bound leaf {resolved}"
        )


def plan_leaf_doc_lifecycle_restamp(
    task_root: Path, leaf_id: str, lifecycle_id: str
) -> LeafLifecycleRestampPlan:
    """Plan a lifecycle-only write and expose terminal blockers without mutating bytes."""
    found = find_leaf_doc(task_root, leaf_id)
    if found is None:
        return LeafLifecycleRestampPlan(None, lifecycle_id, None, False)
    json_path, doc = found
    if doc.lifecycleId == lifecycle_id:
        return LeafLifecycleRestampPlan(json_path, lifecycle_id, None, False)
    data = doc.model_dump(by_alias=True)
    data["lifecycleId"] = lifecycle_id
    updated = TaskDocument.model_validate(data)
    blockers = tuple(completion_blockers(updated)) if updated.status == "Completed" else ()
    return LeafLifecycleRestampPlan(json_path, lifecycle_id, updated, True, blockers)


def restamp_leaf_doc_lifecycle(
    task_root: Path,
    leaf_id: str,
    lifecycle_id: str,
    *,
    publish: Callable[[Path, TaskDocument], object],
) -> dict | None:
    """Point the leaf's doc at ``lifecycle_id`` (the enclosure's current lifecycle).

    Overwrites any previous stamp: the enclosure's newest lifecycle IS the doc's
    binding — a reopened leaf's doc must follow the fresh lifecycle, not the
    finalized one. Returns a small report dict, or None when the leaf has no doc
    yet (a first start authors the doc afterwards, already stamped by task_doc).
    """
    plan = plan_leaf_doc_lifecycle_restamp(task_root, leaf_id, lifecycle_id)
    if plan.blockers:
        raise LeafLifecycleRestampBlocked(plan)
    if plan.doc_path is None:
        return None
    if plan.candidate is not None:
        publish(task_root, plan.candidate)
    return {
        "docPath": plan.doc_path.as_posix(),
        "lifecycleId": lifecycle_id,
        "changed": plan.changed,
    }
