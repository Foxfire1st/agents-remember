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
* ``unarchived-task-notes``: left for that task's archive hook (D17).
* ``other``: left, and named, because rule 6 names no category for it.
* ``undetermined``: left, and named: Git could not say whether the file is tracked.

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
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Literal

import apsw

from agents_remember.kernel.git_command import run_git
from agents_remember.tasks.task_paths import ARCHIVE_DIR

__all__ = [
    "CLEANUP_SCHEMA",
    "Copy",
    "CopyRule",
    "is_derived_index",
    "is_knowledge_dataset",
    "plan_cleanup",
    "run_cleanup",
    "sqlite_table_names",
]

CLEANUP_SCHEMA: Final = "ar-knowledge-copy-cleanup/v1"
_SQLITE_HEADER: Final = b"SQLite format 3\x00"
# The tables every generation of the knowledge store has had: a file holding both is a dataset.
_KNOWLEDGE_TABLES: Final = frozenset({"invariant", "invariant_revision"})
_INDEX_TABLE_PREFIX: Final = "ix_"
_INDEX_CACHE: Final = ("runtime", "knowledge-index")
_PROVIDER_RUNTIME: Final = "provider-runtime"
_TASKS: Final = "tasks"
_WORKTREES: Final = "worktrees"
_NOTES: Final = "notes"
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
    "unarchived-task-notes",
    "other",
    "undetermined",
]
_DELETED_RULES: Final = frozenset({"provider-runtime", "worktree-gone", "archived-task-notes"})
_RULE_TEXT: Final[dict[str, str]] = {
    "git-tracked": "skipped: Git tracks it, so it leaves with its branch or at archive",
    "provider-runtime": "deleted: a copy in a provider-runtime directory",
    "worktree-gone": "deleted: a copy in a worktree that no longer exists",
    "archived-task-notes": "deleted: a copy under the notes of an archived task",
    "unarchived-task-notes": (
        "left: under the notes of a task that is not archived; its archive hook deletes it (D17)"
    ),
    "other": "left: MIK-R26 rule 6 names no category for this location",
    "undetermined": "left: Git could not say whether the file is tracked",
}


def sqlite_table_names(path: Path) -> frozenset[str] | None:
    """The table names of the SQLite file at ``path``, or ``None`` when it is not one.

    The one content probe: ``mode=ro`` and ``immutable=1``, so SQLite creates no journal, WAL or
    lock file beside it and cannot write it. A symbolic link is not probed.
    """

    try:
        if path.is_symlink() or not path.is_file():
            return None
        with path.open("rb") as handle:
            if handle.read(len(_SQLITE_HEADER)) != _SQLITE_HEADER:
                return None
        connection = apsw.Connection(
            f"{path.resolve().as_uri()}?mode=ro&immutable=1",
            flags=apsw.SQLITE_OPEN_READONLY | apsw.SQLITE_OPEN_URI,
        )
    except (OSError, apsw.Error):
        return None
    try:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return frozenset(str(row[0]) for row in rows)
    except apsw.Error:
        return None
    finally:
        connection.close()


def is_knowledge_dataset(tables: frozenset[str] | None) -> bool:
    """Whether a file's tables are the knowledge store's (a dataset, or an index of one)."""

    return tables is not None and tables >= _KNOWLEDGE_TABLES


def is_derived_index(tables: frozenset[str] | None) -> bool:
    """Whether a knowledge dataset is a derived index: it holds the index's own ``ix_*`` tables."""

    return is_knowledge_dataset(tables) and any(
        name.startswith(_INDEX_TABLE_PREFIX) for name in tables or ()
    )


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


def _walk(plan: _Plan) -> list[Path]:
    """Every regular file under the root, links never followed, the index cache never entered."""

    index_cache = plan.root.joinpath(*_INDEX_CACHE)
    found: list[Path] = []
    stack = [plan.root]
    while stack:
        directory = stack.pop()
        if directory == index_cache:
            continue
        try:
            entries = list(os.scandir(directory))
        except OSError as error:
            plan.unreadable.append({"path": directory.as_posix(), "detail": str(error)})
            continue
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if entry.name != ".git":
                    stack.append(Path(entry.path))
            elif entry.is_file(follow_symlinks=False):
                found.append(Path(entry.path))
    return sorted(found)


def _enclosing_checkout(root: Path, path: Path) -> Path | None:
    """The nearest directory below ``root`` that holds a ``.git`` entry, or ``None``."""

    directory = path.parent
    while directory != root and root in directory.parents:
        if (directory / ".git").exists():
            return directory
        directory = directory.parent
    return None


def _git_dir_gone(checkout: Path) -> bool:
    """Whether a linked worktree's ``.git`` file names a Git directory that no longer exists."""

    marker = checkout / ".git"
    if not marker.is_file():
        return False
    try:
        text = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return False
    if not text.startswith("gitdir:"):
        return False
    target = Path(text.removeprefix("gitdir:").strip())
    if not target.is_absolute():
        target = checkout / target
    return not target.exists()


def _tracking(root: Path, path: Path) -> tuple[str, str]:
    """``(state, detail)``: ``tracked``, ``untracked``, ``no-checkout``, ``gone`` or ``unknown``."""

    checkout = _enclosing_checkout(root, path)
    if checkout is None:
        return "no-checkout", ""
    if _git_dir_gone(checkout):
        return "gone", f"the worktree {checkout.relative_to(root).as_posix()} is pruned"
    relative = path.relative_to(checkout).as_posix()
    try:
        listed = run_git(checkout, ["ls-files", "--error-unmatch", "--", relative])
    except (OSError, subprocess.SubprocessError) as error:
        return "unknown", str(error)
    if listed.returncode == 0:
        return "tracked", ""
    answered = run_git(checkout, ["rev-parse", "--is-inside-work-tree"])
    if answered.returncode == 0 and answered.stdout.strip() == "true":
        return "untracked", ""
    return "unknown", (listed.stderr or answered.stderr).strip()


def _group_has_live_worktree(group: Path) -> bool:
    try:
        return any(
            (child / ".git").exists() and not _git_dir_gone(child)
            for child in group.iterdir()
            if child.is_dir() and not child.is_symlink()
        )
    except OSError:
        return True


def _owner(parts: tuple[str, ...]) -> str:
    """The task folder or worktree group a path belongs to, or ``-``."""

    if len(parts) > 3 and parts[0] == _TASKS:
        return parts[3] if parts[2] == ARCHIVE_DIR and len(parts) > 4 else parts[2]
    if len(parts) > 2 and parts[0] == _WORKTREES:
        return parts[2]
    return "-"


def _task_rule(parts: tuple[str, ...]) -> tuple[CopyRule, str]:
    """A copy under ``tasks/<project>/``: archived or not, and whether it is under the notes.

    ``tasks/<project>/0_archive/<task>/notes/…`` is the notes of an archived task, whether the
    task was archived before this run or after an earlier one: the folder's place decides.
    """

    archived = parts[2] == ARCHIVE_DIR
    inside = parts[4:] if archived else parts[3:]
    if len(inside) > 1 and inside[0] == _NOTES:
        return ("archived-task-notes" if archived else "unarchived-task-notes"), ""
    return "other", "under a task folder, outside its notes"


def _worktree_rule(
    root: Path, parts: tuple[str, ...], state: str, detail: str
) -> tuple[CopyRule, str]:
    """A copy under ``worktrees/<project>/<group>/``: is its worktree gone."""

    if state == "gone":
        return "worktree-gone", detail
    if state == "no-checkout" and not _group_has_live_worktree(root.joinpath(*parts[:3])):
        return "worktree-gone", "its worktree group holds no worktree any more"
    if state == "untracked":
        return "other", "untracked inside a worktree that still exists"
    return "other", "beside worktrees that still exist"


def _classify(root: Path, path: Path) -> tuple[CopyRule, str, str]:
    """The rule that decides ``path``, the task or worktree group it belongs to, and a detail.

    Tracking is asked first, so a Git-tracked file is skipped wherever it lies; a
    ``provider-runtime`` directory is next, wherever it lies; then the task and worktree places.
    """

    parts = path.relative_to(root).parts
    owner = _owner(parts)
    state, detail = _tracking(root, path)
    if state == "tracked":
        return "git-tracked", owner, ""
    if state == "unknown":
        return "undetermined", owner, detail
    if _PROVIDER_RUNTIME in parts[:-1]:
        return "provider-runtime", owner, ""
    if len(parts) > 3 and parts[0] == _TASKS:
        rule, why = _task_rule(parts)
        return rule, owner, why
    if len(parts) > 3 and parts[0] == _WORKTREES:
        rule, why = _worktree_rule(root, parts, state, detail)
        return rule, owner, why
    return "other", owner, ""


def plan_cleanup(coordination_root: Path) -> dict[str, Any]:
    """The dry run: every copy under the root with the rule that decides it. Nothing is changed."""

    plan = _scan(_require_coordination_root(coordination_root))
    return _document(plan, mode="dry-run", deleted=[], removed=[], failures=[])


def run_cleanup(coordination_root: Path, reviewed_digest: str) -> dict[str, Any]:
    """Delete exactly what the reviewed dry run listed; refuse, deleting nothing, if the scan differs.

    ``reviewed_digest`` is the ``reviewDigest`` of a dry run. Eligible copies and removable
    companions are deleted. A companion the review listed as kept is kept and reported as kept;
    any other refusal is a failure, because the run then did not do what was reviewed.
    """

    plan = _scan(_require_coordination_root(coordination_root))
    review = _review_list(plan)
    current = _review_digest(review)
    if current != reviewed_digest:
        raise ValueError(
            f"the scan differs from the reviewed dry run (reviewed {reviewed_digest}, now {current});"
            " nothing was deleted: run the dry run again and review its list"
        )
    reviewed_kept = {entry["path"] for entry in review["companionsKept"]}
    deleted: list[Copy] = []
    removed: list[str] = []
    kept: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    gone: set[Path] = set()
    for copy in plan.copies:
        if not copy.deleted_by_rule:
            continue
        refusal = _recheck(plan.root, copy)
        if refusal is not None:
            failures.append({"path": copy.relative, "detail": refusal})
            continue
        try:
            companions = _companions(copy.path, gone)
            copy.path.unlink()
            gone.add(copy.path)
            deleted.append(copy)
            for companion in companions:
                relative = companion.relative_to(plan.root).as_posix()
                refusal = _companion_refusal(plan.root, companion)
                if refusal is not None:
                    outcome = kept if relative in reviewed_kept else failures
                    outcome.append({"path": relative, "detail": refusal})
                    continue
                companion.unlink()
                removed.append(relative)
            removed.extend(_remove_empty_parents(plan.root, copy.path.parent))
        except OSError as error:
            failures.append({"path": copy.relative, "detail": str(error)})
    document = _document(plan, mode="apply", deleted=deleted, removed=removed, failures=failures)
    document["reviewDigest"] = current
    document["keptCompanions"] = kept
    return document


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
    return {"copies": copies, "companionsRemoved": removes, "companionsKept": keeps}


def _review_digest(review: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(review, sort_keys=True).encode("utf-8")).hexdigest()


def _scan(root: Path) -> _Plan:
    plan = _Plan(root=root)
    for path in _walk(plan):
        tables = sqlite_table_names(path)
        if not is_knowledge_dataset(tables):
            continue
        size = path.stat().st_size
        if is_derived_index(tables):
            plan.derived_indexes += 1
            plan.derived_index_bytes += size
            continue
        rule, owner, detail = _classify(root, path)
        plan.copies.append(
            Copy(
                path=path,
                relative=path.relative_to(root).as_posix(),
                size=size,
                rule=rule,
                owner=owner,
                detail=detail,
            )
        )
    return plan


def _recheck(root: Path, copy: Copy) -> str | None:
    """Why a planned deletion is not carried out after all, or ``None``: the file is re-read."""

    path = copy.path
    if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
        return "the file changed since it was listed"
    tables = sqlite_table_names(path)
    if not is_knowledge_dataset(tables) or is_derived_index(tables):
        return "the file is no longer a knowledge dataset copy"
    rule, _owner, _detail = _classify(root, path)
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
        "derivedIndexes": {
            "skipped": "by construction: the index cache directory is never entered, and a file "
            "with ix_* tables is never a copy",
            "foundOutsideTheCache": plan.derived_indexes,
            "bytes": plan.derived_index_bytes,
        },
        "unreadable": plan.unreadable,
    }
