"""Leftover copies of the retired knowledge database: the content probe and the one cleanup run.

MIK-R26 rule 6. The canonical knowledge database is retired; what it left under a coordination root
are SQLite files with the knowledge schema: enclosure baselines and candidates, retained comparison
snapshots, curator scratch copies. A copy is identified **by content, not by name**: a regular file
that starts with the SQLite header and holds the tables ``invariant`` and ``invariant_revision``
(``pub2.sqlite`` and ``head.sqlite`` count). A file that also holds ``ix_*`` tables is a derived
index (MIK-R23), never a copy.

:func:`sqlite_table_names` is the one content probe: it opens a file read-only and immutable, reads
its table names and closes it. The archive hook (MIK-R25 rule 5) and the cleanup below both use it;
nothing else in production opens a leftover copy.

:func:`plan_cleanup` classifies every copy under a coordination root by the rule that decides it,
and :func:`run_cleanup` deletes those rule 6 names:

* ``git-tracked``: skipped. It leaves with its branch, or at archive.
* ``provider-runtime``: deleted. A copy under a ``provider-runtime`` directory.
* ``worktree-gone``: deleted. A copy under ``worktrees/`` whose worktree no longer exists.
* ``archived-task-notes``: deleted. A copy under the notes of a task in ``0_archive``.
* ``completed-task-notes``: deleted. A copy under the notes of a task that is not archived
  but whose task document (``task.json``) says ``Completed`` or ``abandoned`` (MIK-R82 rule 2).
* ``unarchived-task-notes``: left for that task's archive hook (D17).
* ``other``: left, and named, because rule 6 names no category for it.
* ``undetermined``: left, and named: Git could not say whether the file is tracked.

A ``completed-task-notes`` copy that is a comparison generation's retained snapshot is not
unlinked directly: it is released through its deletion owner
(:func:`agents_remember.application.review_comparison_reclamation.discard_comparison_snapshots`),
which removes the verified snapshot bytes before writing each half's unavailable-history
record, so a later reopen of that generation says its history was deleted and why. Only a
snapshot whose manifest reads back and names this exact file is routed that way; anything
else under completed notes is an ordinary leftover and is unlinked. The task's review refs
and retained-code refs stay until its archive: this command never touches refs.

Citation source indexes outside the exact managed root are listed and deleted through the
citation cache census (:mod:`agents_remember.application.citation_cache_census`), which owns
that population end to end: every publisher-owned slot anywhere under the root -- the
``temp/citation-source-index`` tree, task and enclosure ``ar-cache-*`` placements -- each
with its slot directory, under the rule ``unmanaged-citation-source-index`` (MIK-R82
rule 3).

Derived index caches are skipped by construction, twice: nothing under
``<root>/runtime/knowledge-index`` is examined, and a file with ``ix_*`` tables is never a copy.
A symbolic link is never followed, read or deleted. Git is asked only inside the repository a file
lies in (the nearest directory with a ``.git`` entry below the root), never at the root itself.
Companions receive their own protection checks, and a refused companion stays even when its
eligible dataset was removed. One the reviewed dry run listed as kept is the reviewed outcome and is
named in ``keptCompanions``; one that became protected after the review is named in ``failures``.

The apply is bound to a reviewed dry run. The dry run lists every copy it would delete and every
companion it would remove or keep, and prints a ``reviewDigest`` over that list. :func:`run_cleanup`
takes that digest, scans again, and refuses to delete anything when the scan's digest differs.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

from agents_remember.application.citation_cache_census import (
    CitationCache,
    delete_citation_caches,
    scan_citation_caches,
)
from agents_remember.application.dataset_copy_survey import (
    _NOTES,
    _PROVIDER_RUNTIME,
    _TASKS,
    _WORKTREES,
    _census_generation_manifests,
    _census_task_documents,
    _classify,
    _record_unreadable,
    _release_snapshot_groups,
    _snapshot_generation,
    _tracking,
    _UnreadableControl,
    _walk,
    is_derived_index,
    is_knowledge_dataset,
    sqlite_table_names,
)

__all__ = [
    "CLEANUP_SCHEMA",
    "Copy",
    "CopyRule",
    "plan_cleanup",
    "run_cleanup",
]

CLEANUP_SCHEMA: Final = "ar-knowledge-copy-cleanup/v1"
# What the database route wrote beside a dataset; removed with it, and nothing else is.
_COMPANION_NAMES: Final = frozenset(
    {"candidate-receipt.json", "baseline-origin.json", "baseline-generation.json"}
)
_COMPANION_SUFFIXES: Final = (".lock", "-wal", "-shm", "-journal")

CopyRule = Literal[
    "git-tracked",
    "provider-runtime",
    "worktree-gone",
    "archived-task-notes",
    "completed-task-notes",
    "unarchived-task-notes",
    "other",
    "undetermined",
]
_DELETED_RULES: Final = frozenset(
    {"provider-runtime", "worktree-gone", "archived-task-notes", "completed-task-notes"}
)
_RULE_TEXT: Final[dict[str, str]] = {
    "git-tracked": "skipped: Git tracks it, so it leaves with its branch or at archive",
    "provider-runtime": "deleted: a copy in a provider-runtime directory",
    "worktree-gone": "deleted: a copy in a worktree that no longer exists",
    "archived-task-notes": "deleted: a copy under the notes of an archived task",
    "completed-task-notes": (
        "deleted: a copy under the notes of a task whose document says Completed or abandoned"
    ),
    "unarchived-task-notes": (
        "left: under the notes of a task that is not archived; its archive hook deletes it (D17)"
    ),
    "other": "left: MIK-R26 rule 6 names no category for this location",
    "undetermined": "left: Git could not say whether the file is tracked",
}


@dataclass(frozen=True)
class Copy:
    """One leftover copy, the rule that decides it, and where it belongs."""

    path: Path
    relative: str
    size: int
    rule: CopyRule
    owner: str
    detail: str = ""

    @property
    def deleted_by_rule(self) -> bool:
        return self.rule in _DELETED_RULES

    def document(self) -> dict[str, Any]:
        return {
            "path": self.relative,
            "bytes": self.size,
            "rule": self.rule,
            "ruleText": _RULE_TEXT[self.rule] + (f" ({self.detail})" if self.detail else ""),
            "owner": self.owner,
        }


@dataclass
class _Plan:
    root: Path
    copies: list[Copy] = field(default_factory=list)
    # ``relative copy path -> (task root, leaf id, generation id)`` for completed-task copies
    # that are a generation's retained snapshot: the owner release they are deleted through.
    snapshot_owners: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    caches: list[CitationCache] = field(default_factory=list)
    # The slot directories an apply deleted; the dry run leaves this empty.
    deleted_caches: list[str] = field(default_factory=list)
    derived_indexes: int = 0
    derived_index_bytes: int = 0
    unreadable: list[dict[str, str]] = field(default_factory=list)


def _require_coordination_root(root: Path) -> Path:
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f"{root} is not a directory")
    resolved = root.resolve()
    if not ((resolved / _TASKS).is_dir() or (resolved / _WORKTREES).is_dir()):
        raise ValueError(
            f"{resolved} holds neither tasks/ nor worktrees/, so it is not a coordination root"
        )
    return resolved


def plan_cleanup(coordination_root: Path) -> dict[str, Any]:
    """The dry run: every copy under the root with the rule that decides it. Nothing is changed.

    Citation source indexes outside the exact managed root are listed the same way, with
    their slot directories. A present control document that cannot be read -- a task
    ``task.json``, or a comparison generation manifest -- is listed under ``unreadable``
    instead of being classified past; the apply refuses while any is listed.
    """

    plan = _scan(_require_coordination_root(coordination_root))
    return _document(plan, mode="dry-run", deleted=[], removed=[], failures=[])


def run_cleanup(coordination_root: Path, reviewed_digest: str) -> dict[str, Any]:
    """Delete exactly what the reviewed dry run listed; refuse, deleting nothing, if the scan differs.

    ``reviewed_digest`` is the ``reviewDigest`` of a dry run. Eligible copies and removable
    companions are deleted, and untracked citation caches go with their slot directories.
    A completed-task copy that is a retained snapshot is released through its generation's
    deletion owner instead of being unlinked, so the reopen reads why its history is gone.
    A companion the review listed as kept is kept and reported as kept; any other refusal
    is a failure, because the run then did not do what was reviewed. A scan that found a
    present control document it could not read refuses before any effect, naming it.
    """

    plan = _scan(_require_coordination_root(coordination_root))
    _refuse_unreadable(plan)
    review = _review_list(plan)
    current = _review_digest(review)
    if current != reviewed_digest:
        raise ValueError(
            f"the scan differs from the reviewed dry run (reviewed {reviewed_digest}, now {current});"
            " nothing was deleted: run the dry run again and review its list"
        )
    return _apply_plan(plan, current, review)


def _apply_plan(plan: _Plan, current: str, review: dict[str, Any]) -> dict[str, Any]:
    """Delete the reviewed population: owner releases, direct unlinks, then cache slots."""

    reviewed_kept = {entry["path"] for entry in review["companionsKept"]}
    owner_removed, owner_failed = _release_snapshot_groups(plan)
    state = _ApplyState(
        reviewed_kept=reviewed_kept,
        owner_removed=owner_removed,
        owner_failed=owner_failed,
    )
    for relative in sorted(state.owner_failed):
        state.failures.append({"path": relative, "detail": state.owner_failed[relative]})
    for copy in plan.copies:
        _apply_one_copy(plan, copy, state)
    plan.deleted_caches = delete_citation_caches(
        plan.root, plan.caches, state.failures, tracking=_tracking
    )
    document = _document(
        plan,
        mode="apply",
        deleted=state.deleted,
        removed=state.removed,
        failures=state.failures,
    )
    document["reviewDigest"] = current
    document["keptCompanions"] = state.kept
    return document


@dataclass
class _ApplyState:
    """The accumulators one apply threads through every planned copy."""

    reviewed_kept: set[str]
    owner_removed: set[str]
    owner_failed: dict[str, str]
    deleted: list[Copy] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    kept: list[dict[str, str]] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)
    gone: set[Path] = field(default_factory=set)


def _apply_one_copy(plan: _Plan, copy: Copy, state: _ApplyState) -> None:
    """Delete one planned copy: an owner release, a rechecked unlink, or a named refusal."""

    if not copy.deleted_by_rule:
        return
    if copy.relative in state.owner_failed:
        return
    if copy.relative in state.owner_removed:
        state.deleted.append(copy)
        state.gone.add(copy.path)
        try:
            fresh = _remove_companions(
                plan.root, _companions(copy.path, state.gone), state.reviewed_kept
            )
            state.removed.extend(fresh[0])
            state.kept.extend(fresh[1])
            state.failures.extend(fresh[2])
            state.removed.extend(_remove_empty_parents(plan.root, copy.path.parent))
        except OSError as error:
            state.failures.append({"path": copy.relative, "detail": str(error)})
        return
    refusal = _recheck(plan.root, copy)
    if refusal is not None:
        state.failures.append({"path": copy.relative, "detail": refusal})
        return
    try:
        companions = _companions(copy.path, state.gone)
        copy.path.unlink()
        state.gone.add(copy.path)
        state.deleted.append(copy)
        fresh = _remove_companions(plan.root, companions, state.reviewed_kept)
        state.removed.extend(fresh[0])
        state.kept.extend(fresh[1])
        state.failures.extend(fresh[2])
        state.removed.extend(_remove_empty_parents(plan.root, copy.path.parent))
    except OSError as error:
        state.failures.append({"path": copy.relative, "detail": str(error)})


def _refuse_unreadable(plan: _Plan) -> None:
    """Refuse an apply whose scan found a present control document it could not read."""

    if plan.unreadable:
        listing = "; ".join(f"{entry['path']}: {entry['detail']}" for entry in plan.unreadable)
        raise ValueError(
            f"the scan found {len(plan.unreadable)} unreadable control document(s) "
            f"({listing}); nothing was deleted: repair or remove the unreadable "
            "file(s), run the dry run again and review its list"
        )


def _remove_companions(
    root: Path, companions: list[Path], reviewed_kept: set[str]
) -> tuple[list[str], list[dict[str, str]], list[dict[str, str]]]:
    """Remove each companion the database route wrote beside a deleted copy.

    Returns ``(removed, kept, failures)``: a companion the review listed as kept is kept
    and reported as kept; any other refusal is a failure, because the run then did not do
    what was reviewed.
    """

    removed: list[str] = []
    kept: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    for companion in companions:
        relative = companion.relative_to(root).as_posix()
        refusal = _companion_refusal(root, companion)
        if refusal is not None:
            outcome = kept if relative in reviewed_kept else failures
            outcome.append({"path": relative, "detail": refusal})
            continue
        companion.unlink()
        removed.append(relative)
    return removed, kept, failures


def _review_list(plan: _Plan) -> dict[str, Any]:
    """What an apply would do, in deletion order: the copies, and each companion's outcome."""

    copies: list[dict[str, Any]] = []
    removes: list[str] = []
    keeps: list[dict[str, str]] = []
    gone: set[Path] = set()
    for copy in plan.copies:
        if not copy.deleted_by_rule:
            continue
        copies.append({"path": copy.relative, "rule": copy.rule, "bytes": copy.size})
        for companion in _companions(copy.path, gone):
            relative = companion.relative_to(plan.root).as_posix()
            refusal = _companion_refusal(plan.root, companion)
            if refusal is None:
                removes.append(relative)
            else:
                keeps.append({"path": relative, "detail": refusal})
        gone.add(copy.path)
    caches = [
        {"path": cache.relative, "rule": cache.rule, "bytes": cache.size}
        for cache in plan.caches
        if cache.deleted_by_rule
    ]
    caches_kept = [
        {"path": cache.relative, "detail": cache.detail or "Git tracks the slot"}
        for cache in plan.caches
        if not cache.deleted_by_rule
    ]
    return {
        "copies": copies,
        "companionsRemoved": removes,
        "companionsKept": keeps,
        "caches": caches,
        "cachesKept": caches_kept,
    }


def _review_digest(review: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(review, sort_keys=True).encode("utf-8")).hexdigest()


def _scan(root: Path) -> _Plan:
    plan = _Plan(root=root)
    found = _walk(plan)
    for path in found:
        try:
            _scan_one(plan, root, path)
        except _UnreadableControl as unreadable:
            _record_unreadable(plan, root, unreadable.path, unreadable.detail)
    _census_task_documents(root, plan)
    _census_generation_manifests(root, plan, found)
    scan_citation_caches(plan.root, plan.caches, plan.unreadable, tracking=_tracking)
    return plan


def _scan_one(plan: _Plan, root: Path, path: Path) -> None:
    tables = sqlite_table_names(path)
    if not is_knowledge_dataset(tables):
        return
    size = path.stat().st_size
    if is_derived_index(tables):
        plan.derived_indexes += 1
        plan.derived_index_bytes += size
        return
    rule, owner, detail = _classify(root, path)
    relative = path.relative_to(root).as_posix()
    if rule == "completed-task-notes":
        release = _snapshot_generation(root, path)
        if release is not None:
            task_root, leaf_id, generation_id = release
            detail = (
                f"{detail}; a retained comparison snapshot, released through "
                "discard_comparison_snapshots"
            )
            plan.snapshot_owners[relative] = (
                task_root.as_posix(),
                leaf_id,
                generation_id,
            )
    plan.copies.append(
        Copy(
            path=path,
            relative=relative,
            size=size,
            rule=rule,
            owner=owner,
            detail=detail,
        )
    )


def _recheck(root: Path, copy: Copy) -> str | None:
    """Why a planned deletion is not carried out after all, or ``None``: the file is re-read."""

    path = copy.path
    if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
        return "the file changed since it was listed"
    tables = sqlite_table_names(path)
    if not is_knowledge_dataset(tables) or is_derived_index(tables):
        return "the file is no longer a knowledge dataset copy"
    try:
        rule, _owner, _detail = _classify(root, path)
    except _UnreadableControl as unreadable:
        return f"its deletion was refused: {unreadable.detail}"
    if rule != copy.rule:
        return f"the file now falls under {rule}"
    return None


def _companions(copy: Path, gone: set[Path]) -> list[Path]:
    """Companion paths, including protected links so their refused deletion is reported.

    ``gone`` holds the copies already deleted in this run, so the dry run and the apply agree on
    which dataset is the last one in its directory (only then do the shared companions go).
    """

    directory = copy.parent
    own = [
        directory / f"{copy.name}{suffix}"
        for suffix in _COMPANION_SUFFIXES
        if (directory / f"{copy.name}{suffix}").is_symlink()
        or (directory / f"{copy.name}{suffix}").is_file()
    ]
    other_datasets = [
        other
        for other in directory.iterdir()
        if other != copy
        and other not in gone
        and not other.is_symlink()
        and other.is_file()
        and is_knowledge_dataset(sqlite_table_names(other))
    ]
    if other_datasets:
        return own
    shared = [
        directory / name
        for name in sorted(_COMPANION_NAMES)
        if (directory / name).is_symlink() or (directory / name).is_file()
    ]
    return [*own, *shared]


def _companion_refusal(root: Path, path: Path) -> str | None:
    """Recheck each companion's own protection immediately before its deletion."""

    if path.is_symlink():
        return "the companion is a symbolic link; its deletion was refused"
    if not path.is_file() or root not in path.resolve().parents:
        return "the companion changed or left the coordination root; its deletion was refused"
    state, detail = _tracking(root, path)
    if state == "tracked":
        return "the companion is Git-tracked; its deletion was refused"
    if state == "unknown":
        return f"Git could not determine whether the companion is tracked; its deletion was refused ({detail})"
    return None


def _remove_empty_parents(root: Path, directory: Path) -> list[str]:
    """Remove the directories a deletion emptied, never above a provider-runtime or notes level."""

    removed: list[str] = []
    while directory != root and directory.name not in {_PROVIDER_RUNTIME, _NOTES, _WORKTREES}:
        try:
            directory.rmdir()
        except OSError:
            break
        removed.append(directory.relative_to(root).as_posix() + "/")
        directory = directory.parent
    return removed


def _totals(copies: list[Copy]) -> dict[str, Any]:
    by_rule: dict[str, dict[str, int]] = {}
    by_owner: dict[str, dict[str, int]] = {}
    for copy in copies:
        for table, key in ((by_rule, copy.rule), (by_owner, copy.owner)):
            row = table.setdefault(key, {"files": 0, "bytes": 0})
            row["files"] += 1
            row["bytes"] += copy.size
    return {
        "files": len(copies),
        "bytes": sum(copy.size for copy in copies),
        "byRule": dict(sorted(by_rule.items())),
        "byOwner": dict(sorted(by_owner.items())),
    }


def _document(
    plan: _Plan,
    *,
    mode: str,
    deleted: list[Copy],
    removed: list[str],
    failures: list[dict[str, str]],
) -> dict[str, Any]:
    gone = {copy.relative for copy in deleted}
    review = _review_list(plan) if mode == "dry-run" else None
    remaining = [copy for copy in plan.copies if copy.relative not in gone]
    kept_caches = (
        review["cachesKept"]
        if review
        else [
            {"path": cache.relative, "detail": cache.detail or "Git tracks the slot"}
            for cache in plan.caches
            if not cache.deleted_by_rule
        ]
    )
    return {
        "schema": CLEANUP_SCHEMA,
        "mode": mode,
        "coordinationRoot": plan.root.as_posix(),
        "copies": [copy.document() for copy in plan.copies],
        "wouldDelete": [copy.relative for copy in plan.copies if copy.deleted_by_rule],
        "wouldRemoveWithThem": review["companionsRemoved"] if review else [],
        "wouldKeepCompanions": review["companionsKept"] if review else [],
        "reviewDigest": _review_digest(review) if review else "",
        "deleted": [copy.relative for copy in deleted],
        "removedWithThem": removed,
        "keptCompanions": [],
        "failures": failures,
        "before": _totals(plan.copies),
        "after": _totals(remaining if mode == "apply" else plan.copies),
        "afterApply": _totals([copy for copy in plan.copies if not copy.deleted_by_rule]),
        "citationCaches": [cache.document() for cache in plan.caches],
        "wouldDeleteCaches": [entry["path"] for entry in review["caches"]] if review else [],
        "wouldKeepCaches": review["cachesKept"] if review else [],
        "deletedCaches": plan.deleted_caches,
        "keptCaches": kept_caches,
        "cacheTotals": {
            "slots": len(plan.caches),
            "bytes": sum(cache.size for cache in plan.caches),
        },
        "derivedIndexes": {
            "skipped": "by construction: the index cache directory is never entered, and a file "
            "with ix_* tables is never a copy",
            "foundOutsideTheCache": plan.derived_indexes,
            "bytes": plan.derived_index_bytes,
        },
        "unreadable": plan.unreadable,
    }
