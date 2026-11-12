"""Survey, classification and owner release of leftover knowledge dataset copies.

The read side of the knowledge-copies command (MIK-R26 rule 6, MIK-R82 rules 2-3): content
probes, filesystem discovery, rule classification, task/generation census, and the
comparison-snapshot owner release. It imports nothing from
:mod:`agents_remember.application.legacy_dataset_copies` at runtime; that module (plan,
apply, report) imports the survey. Shared plan types are referenced under
:data:`typing.TYPE_CHECKING` only.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Final

import apsw

from agents_remember.application.review_comparison_generation import (
    COMPARISON_GENERATIONS_DIRECTORY,
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    generation_directory,
    read_history_deletion,
    read_manifest,
)
from agents_remember.application.review_comparison_reclamation import (
    discard_comparison_snapshots,
)
from agents_remember.errors import ComparisonReclamationError
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.tasks.task_paths import ARCHIVE_DIR

if TYPE_CHECKING:
    from agents_remember.application.legacy_dataset_copies import Copy, CopyRule, _Plan

__all__ = [
    "is_derived_index",
    "is_knowledge_dataset",
    "sqlite_table_names",
]

_SQLITE_HEADER: Final = b"SQLite format 3\x00"
# The tables every generation of the knowledge store has had: a file holding both is a dataset.
_KNOWLEDGE_TABLES: Final = frozenset({"invariant", "invariant_revision"})
_INDEX_TABLE_PREFIX: Final = "ix_"
_INDEX_CACHE: Final = ("runtime", "knowledge-index")
_PROVIDER_RUNTIME: Final = "provider-runtime"
_TASKS: Final = "tasks"
_WORKTREES: Final = "worktrees"
_NOTES: Final = "notes"


# The task document values that make an unarchived task's notes eligible (MIK-R82 rule 2):
# the JSON-primary task document's own DocStatus, read from the task folder's task.json.
_TERMINAL_DOC_STATUSES: Final = frozenset({"Completed", "abandoned"})


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


class _UnreadableControl(Exception):
    """A present control document that cannot be read: fail closed, naming it.

    A missing document is ordinary (a task with no ``task.json`` simply keeps its copies for
    its archive hook; a copy with no manifest beside it is an ordinary leftover). A document
    that exists but cannot be read or parsed is not: ownership can no longer be established,
    so the scan records it instead of classifying past it, and the apply refuses before any
    effect. ``detail`` already names the exact path and the refused action.
    """

    def __init__(self, path: Path, detail: str) -> None:
        super().__init__(detail)
        self.path = path
        self.detail = detail


def _record_unreadable(plan: _Plan, root: Path, path: Path, detail: str) -> None:
    """Record one unreadable control document once, by its root-relative path."""

    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.as_posix()
    if all(entry.get("path") != relative for entry in plan.unreadable):
        plan.unreadable.append({"path": relative, "detail": detail})


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


def _snapshot_generation(root: Path, copy_path: Path) -> tuple[Path, str, str] | None:
    """The comparison generation a completed-task copy is a retained snapshot of, if any.

    ``(task root, leaf id, generation id)`` when the nearest ``manifest.json`` between the
    copy and the task's notes reads back as a comparison generation published exactly where
    it was found, and names this exact file as one of its retained snapshot artifacts.
    No manifest, or a file no retained artifact names, is an ordinary leftover, unlinked
    directly. A manifest that is present but cannot be read is never an ordinary leftover:
    ownership cannot be established, so :exc:`_UnreadableControl` refuses past it.
    """

    try:
        parts = copy_path.relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 5 or parts[0] != _TASKS:
        return None
    task_root = root.joinpath(*parts[:3])
    notes = task_root / _NOTES
    directory = copy_path.parent
    while directory != notes and notes in directory.parents:
        manifest_path = directory / "manifest.json"
        if manifest_path.is_symlink() or not manifest_path.is_file():
            directory = directory.parent
            continue
        try:
            in_generation_tree = (
                COMPARISON_GENERATIONS_DIRECTORY in manifest_path.relative_to(root).parts
            )
        except ValueError:
            in_generation_tree = False
        if not in_generation_tree:
            # Another tool's evidence manifest: not a generation record, never ownership.
            return None
        return _manifest_snapshot_owner(task_root, directory, manifest_path, copy_path)
    return None


def _manifest_snapshot_owner(
    task_root: Path, directory: Path, manifest_path: Path, copy_path: Path
) -> tuple[Path, str, str] | None:
    """The generation a manifest releases this exact file through, or ``None``.

    ``None`` is only the ordinary case: no manifest beside the copy, or a manifest that
    reads back but names another file. A manifest that is present but cannot be read as a
    generation record raises :exc:`_UnreadableControl`: a malformed known operation record
    never falls through to ordinary unlinking.
    """

    try:
        manifest = read_manifest(manifest_path)
    except (KnowledgeStorageError, OSError, ValueError) as error:
        raise _UnreadableControl(
            manifest_path,
            f"the comparison generation manifest {manifest_path} is present but cannot be "
            f"read as a generation record ({error}); refusing: snapshot ownership cannot "
            "be established, so nothing it might own is deleted",
        ) from error
    if generation_directory(task_root, manifest.leaf_id, manifest.generation_id) != directory:
        return None
    for binding in manifest.knowledge:
        artifact = binding.artifact
        if (
            binding.state == "retained"
            and artifact is not None
            and directory / artifact.relative_path == copy_path
        ):
            return task_root, manifest.leaf_id, manifest.generation_id
    return None


def _completed_release_reason(task_root: Path) -> str:
    """Why a completed task's snapshots are released: the words the reopen repeats."""

    status = _task_document_status(task_root)
    return (
        f"the task document says {status}, so the finished task's comparison snapshots are "
        "released while its review pins stay until archive (MIK-R82 rule 2)"
    )


def _owner(parts: tuple[str, ...]) -> str:
    """The task folder or worktree group a path belongs to, or ``-``."""

    if len(parts) > 3 and parts[0] == _TASKS:
        return parts[3] if parts[2] == ARCHIVE_DIR and len(parts) > 4 else parts[2]
    if len(parts) > 2 and parts[0] == _WORKTREES:
        return parts[2]
    return "-"


def _task_document_status(task_root: Path) -> str | None:
    """The task folder's own ``task.json`` document status, or ``None`` when it says nothing.

    Only the JSON-primary task document counts: ``task.json`` is what light and master tasks
    write beside ``task.md``. A missing file, or a document without a string ``status``, is
    not a completed task -- its copies stay for the archive hook. A file that exists but
    cannot be read as JSON is not missing: it raises :exc:`_UnreadableControl`, because a
    present unreadable task document is an admission failure, never a default.
    """

    path = task_root / "task.json"
    try:
        if path.is_symlink() or not path.is_file():
            return None
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise _UnreadableControl(
            path,
            f"the task document {path} is present but cannot be read as JSON ({error}); "
            "refusing: the task's completion state cannot be established, so no copy "
            "under its notes is deleted",
        ) from error
    status = loaded.get("status") if isinstance(loaded, dict) else None
    return status if isinstance(status, str) and status else None


def _task_rule(root: Path, parts: tuple[str, ...]) -> tuple[CopyRule, str]:
    """A copy under ``tasks/<project>/``: archived, completed, or still awaiting its archive.

    ``tasks/<project>/0_archive/<task>/notes/…`` is the notes of an archived task, whether the
    task was archived before this run or after an earlier one: the folder's place decides.
    Under the notes of a task that is not archived, the task document decides: ``Completed``
    or ``abandoned`` names the completed-task rule (MIK-R82 rule 2), anything else -- a live
    status, a missing document -- stays for that task's archive hook (D17). A document that
    is present but unreadable never reaches this decision: it raises first.
    """

    archived = parts[2] == ARCHIVE_DIR
    inside = parts[4:] if archived else parts[3:]
    if len(inside) > 1 and inside[0] == _NOTES:
        if archived:
            return "archived-task-notes", ""
        status = _task_document_status(root.joinpath(*parts[:3]))
        if status in _TERMINAL_DOC_STATUSES:
            return "completed-task-notes", f"the task document says {status}"
        return "unarchived-task-notes", ""
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
        rule, why = _task_rule(root, parts)
        return rule, owner, why
    if len(parts) > 3 and parts[0] == _WORKTREES:
        rule, why = _worktree_rule(root, parts, state, detail)
        return rule, owner, why
    return "other", owner, ""


def _release_snapshot_groups(plan: _Plan) -> tuple[set[str], dict[str, str]]:
    """Release every generation a completed-task snapshot names, through its deletion owner.

    One discard per generation: the owner removes that generation's retained snapshots and
    writes each half's unavailable-history record, which is what a later reopen reads to say
    the history was deleted and why. A copy whose manifest no longer names this exact file
    is not released: the run then did not do what was reviewed, so it is a failure.
    """

    removed: set[str] = set()
    failed: dict[str, str] = {}
    groups: dict[tuple[str, str, str], list[Copy]] = {}
    for copy in plan.copies:
        if copy.rule != "completed-task-notes" or copy.relative not in plan.snapshot_owners:
            continue
        groups.setdefault(plan.snapshot_owners[copy.relative], []).append(copy)
    for key in sorted(groups):
        _release_one_group(plan, key, groups[key], removed, failed)
    return removed, failed


def _release_one_group(
    plan: _Plan,
    key: tuple[str, str, str],
    members: list[Copy],
    removed: set[str],
    failed: dict[str, str],
) -> None:
    """Release one generation's snapshots through their deletion owner; record what that did.

    The owner removes the verified snapshot bytes first and then writes each half's
    unavailable-history record. Before it runs, its entire removal population -- every
    retained snapshot the generation manifest names -- is validated against the reviewed
    plan, tracking and protected-index rules: a generation that also retains an unreviewed
    or protected half (for example a Git-tracked sibling the review never listed) is
    refused whole, with nothing deleted. Every owner failure is retained with the exact
    failing artifact and action: an absent file after a failed release is never reported as
    removed, and a release is not complete until every half's history is durably recorded.
    A failure between the owner's removal and its history writes is repaired once through
    the owner itself in this same request, because its retry converges (absent snapshots
    are recorded, missing history is written) instead of failing.
    """

    task_root = Path(key[0])
    live = _live_group_members(plan, key, members, failed)
    if live is None:
        return
    refusal = _generation_release_refusal(plan, key, live)
    if refusal is not None:
        for member in live:
            failed[member.relative] = refusal
        return
    try:
        reason = _completed_release_reason(task_root)
    except _UnreadableControl as unreadable:
        for member in live:
            failed[member.relative] = unreadable.detail
        return
    if not _run_owner_release(key, live, reason, failed):
        return
    for member in live:
        if not member.path.exists() and not member.path.is_symlink():
            removed.add(member.relative)
        else:
            failed[member.relative] = "the owner release left the file in place"


def _live_group_members(
    plan: _Plan,
    key: tuple[str, str, str],
    members: list[Copy],
    failed: dict[str, str],
) -> list[Copy] | None:
    """The members still naming their reviewed generation, or ``None`` when refused whole.

    A changed manifest refuses the whole generation: when any member went stale, the review
    no longer describes this population, so the remaining live members are failed with it
    instead of being released alone.
    """

    task_root = Path(key[0])
    try:
        stale = [
            member
            for member in members
            if _snapshot_generation(plan.root, member.path) != (task_root, key[1], key[2])
        ]
    except _UnreadableControl as unreadable:
        for member in members:
            failed[member.relative] = unreadable.detail
        return None
    for member in stale:
        failed[member.relative] = (
            "the file is no longer a retained snapshot of its reviewed generation"
        )
    live = [member for member in members if member not in stale]
    if stale:
        for member in live:
            failed[member.relative] = (
                "the generation changed since the reviewed dry run; refusing the whole "
                "release with nothing deleted"
            )
        return None
    return live or None


def _run_owner_release(
    key: tuple[str, str, str],
    live: list[Copy],
    reason: str,
    failed: dict[str, str],
) -> bool:
    """Run the generation's deletion owner with same-request repair; whether it completed.

    Every owner failure is retained with the exact failing artifact and action: an absent
    file after a failed release is never reported as removed, and a release is not complete
    until every half's history is durably recorded. A failure between the owner's removal
    and its history writes is repaired once through the owner itself, because its retry
    converges (absent snapshots are recorded, missing history is written) instead of
    failing.
    """

    task_root = Path(key[0])
    try:
        discard_comparison_snapshots(task_root, key[1], key[2], reason=reason)
    except (ComparisonReclamationError, KnowledgeStorageError, OSError) as error:
        return _repair_owner_release(key, live, reason, failed, first=str(error))
    return _verify_owner_history(key, live, reason, failed)


def _repair_owner_release(
    key: tuple[str, str, str],
    live: list[Copy],
    reason: str,
    failed: dict[str, str],
    *,
    first: str,
) -> bool:
    """One same-request repair through the owner after its first failure; whether whole."""

    task_root = Path(key[0])
    try:
        discard_comparison_snapshots(task_root, key[1], key[2], reason=reason)
    except (ComparisonReclamationError, KnowledgeStorageError, OSError) as retried:
        missing = _missing_history(task_root, key)
        for member in live:
            failed[member.relative] = (
                f"the owner release of generation {key[2]} failed ({first}); the "
                f"same-request repair through its owner failed as well ({retried}): "
                f"its history is incomplete ({missing}) until "
                f"discard_comparison_snapshots is re-run for leaf {key[1]} "
                f"generation {key[2]}"
            )
        return False
    return _verify_owner_history(key, live, reason, failed)


def _verify_owner_history(
    key: tuple[str, str, str],
    live: list[Copy],
    reason: str,
    failed: dict[str, str],
) -> bool:
    """Whether every half's history is durably recorded, repairing once when it is not."""

    task_root = Path(key[0])
    missing = _missing_history(task_root, key)
    if missing is None:
        return True
    try:
        discard_comparison_snapshots(task_root, key[1], key[2], reason=reason)
        missing = _missing_history(task_root, key)
    except (ComparisonReclamationError, KnowledgeStorageError, OSError) as error:
        missing = f"the repair release failed ({error})"
    if missing is None:
        return True
    for member in live:
        failed[member.relative] = (
            f"the owner release of generation {key[2]} left {missing}; refusing "
            "to report a complete release until the owner has durably recorded "
            "the required history"
        )
    return False


def _generation_release_refusal(
    plan: _Plan, key: tuple[str, str, str], live: list[Copy]
) -> str | None:
    """Why a generation's owner release must not run, or ``None`` when it may.

    The owner acts on the generation's whole retained population, not on the one caller
    member the review listed. Every retained snapshot the manifest names must therefore be
    a reviewed, still-eligible member of this exact release: still a regular file inside
    the root, still a knowledge dataset and not a derived index, still under the
    completed-task rule (so still untracked and unprotected), and still in the reviewed
    deletion list. The first half that is not is an actionable refusal of the whole
    generation, naming its exact path.
    """

    task_root = Path(key[0])
    directory = generation_directory(task_root, key[1], key[2])
    manifest_path = directory / COMPARISON_MANIFEST_NAME
    refusal: str | None
    if manifest_path.is_symlink() or not manifest_path.is_file():
        refusal = (
            f"the generation manifest {manifest_path} is no longer present; refusing the "
            "whole release with nothing deleted"
        )
    else:
        try:
            manifest = read_manifest(manifest_path)
        except (KnowledgeStorageError, OSError, ValueError) as error:
            refusal = (
                f"the comparison generation manifest {manifest_path} is present but cannot be "
                f"read as a generation record ({error}); refusing the whole release with "
                "nothing deleted"
            )
        else:
            refusal = _generation_population_refusal(plan, live, task_root, directory, manifest)
    return refusal


def _generation_population_refusal(
    plan: _Plan,
    live: list[Copy],
    task_root: Path,
    directory: Path,
    manifest: ComparisonGenerationManifest,
) -> str | None:
    """Why the generation's retained population is not this reviewed release, or ``None``."""

    if generation_directory(task_root, manifest.leaf_id, manifest.generation_id) != directory:
        return (
            f"the manifest at {directory / COMPARISON_MANIFEST_NAME} no longer publishes "
            "this generation where it was reviewed; refusing the whole release with "
            "nothing deleted"
        )
    planned = {member.relative for member in live}
    for binding in manifest.knowledge:
        if binding.state != "retained" or binding.artifact is None:
            continue
        retained = directory / binding.artifact.relative_path
        try:
            relative = retained.relative_to(plan.root).as_posix()
        except ValueError:
            return (
                f"the generation also retains {retained}, outside the coordination root; "
                "refusing the whole release with nothing deleted"
            )
        if relative not in planned:
            return (
                f"the generation also retains {relative}, which {_unplanned_reason(plan.root, retained)}; "
                "refusing the whole release with nothing deleted, so no "
                "unreviewed or protected half is removed or rewritten"
            )
        problem = _retained_release_problem(plan.root, retained)
        if problem is not None:
            return (
                f"the generation retains {relative}, which {problem}; refusing the whole "
                "release with nothing deleted"
            )
    return None


def _unplanned_reason(root: Path, path: Path) -> str:
    """Why a retained snapshot is outside the reviewed deletion list, for the refusal."""

    if path.is_symlink() or not path.is_file():
        return "is no longer present, outside the reviewed deletion list"
    tables = sqlite_table_names(path)
    if not is_knowledge_dataset(tables):
        return "is not a knowledge dataset copy, outside the reviewed deletion list"
    if is_derived_index(tables):
        return "is a derived index that stays, outside the reviewed deletion list"
    try:
        rule, _owner, _detail = _classify(root, path)
    except _UnreadableControl as unreadable:
        return f"cannot be classified ({unreadable.detail})"
    return f"falls under {rule}, outside the reviewed deletion list"


def _retained_release_problem(root: Path, path: Path) -> str | None:
    """Why a retained snapshot is no longer releasable, or ``None`` when it still is."""
    try:
        if path.is_symlink() or not path.is_file() or root not in path.resolve().parents:
            return "is no longer a regular file inside the coordination root"
    except OSError:
        return "cannot be resolved inside the coordination root"
    tables = sqlite_table_names(path)
    if not is_knowledge_dataset(tables) or is_derived_index(tables):
        return "is no longer a releasable knowledge dataset copy (derived indexes stay)"
    try:
        rule, _owner, _detail = _classify(root, path)
    except _UnreadableControl as unreadable:
        return f"cannot be classified ({unreadable.detail})"
    if rule != "completed-task-notes":
        return f"now falls under {rule}"
    return None


def _missing_history(task_root: Path, key: tuple[str, str, str]) -> str | None:
    """The first retained half with no durably recorded history, or ``None`` when whole."""

    try:
        manifest = read_manifest(
            generation_directory(task_root, key[1], key[2]) / COMPARISON_MANIFEST_NAME
        )
    except (KnowledgeStorageError, OSError, ValueError) as error:
        return f"the generation manifest cannot be re-read ({error})"
    for binding in manifest.knowledge:
        if binding.state != "retained" or binding.artifact is None:
            continue
        target = "knowledge-before" if binding.side == "before" else "knowledge-after"
        try:
            recorded = read_history_deletion(task_root, key[1], key[2], target)
        except (KnowledgeStorageError, OSError, ValueError) as error:
            return f"the {target} history record cannot be read ({error})"
        if recorded is None:
            return f"the {target} history has no deletion record"
    return None


def _census_task_documents(root: Path, plan: _Plan) -> None:
    """Record every present task document that cannot be read, even with no copy beside it.

    Classification consults a task's document only while a copy lies under its notes; a task
    with no copy at all would otherwise hide its unreadable document. The known
    task-document locations are ``tasks/<project>/<task>/task.json`` and, for archived
    tasks, ``tasks/<project>/0_archive/<task>/task.json``. Missing documents stay ordinary;
    symlinks are never trusted. The status itself is discarded: this census only proves the
    document reads.
    """

    tasks = root / _TASKS
    if tasks.is_symlink() or not tasks.is_dir():
        return
    try:
        projects = sorted(tasks.iterdir())
    except OSError as error:
        _record_unreadable(plan, root, tasks, f"the tasks directory cannot be listed: {error}")
        return
    candidates: list[tuple[Path, Path]] = []
    for project in projects:
        candidates.extend(_project_task_documents(plan, root, project))
    for task_root, document in candidates:
        try:
            _task_document_status(task_root)
        except _UnreadableControl as unreadable:
            _record_unreadable(plan, root, document, unreadable.detail)


def _project_task_documents(plan: _Plan, root: Path, project: Path) -> list[tuple[Path, Path]]:
    """The ``(task root, task.json)`` candidates directly under one project directory."""

    if project.is_symlink() or not project.is_dir():
        return []
    try:
        children = sorted(project.iterdir())
    except OSError as error:
        _record_unreadable(plan, root, project, f"the project directory cannot be listed: {error}")
        return []
    candidates: list[tuple[Path, Path]] = []
    for child in children:
        if child.is_symlink() or not child.is_dir():
            continue
        if child.name == ARCHIVE_DIR:
            candidates.extend(_archived_task_documents(plan, root, child))
        else:
            candidates.append((child, child / "task.json"))
    return candidates


def _archived_task_documents(plan: _Plan, root: Path, archive: Path) -> list[tuple[Path, Path]]:
    """The ``(task root, task.json)`` candidates directly under one ``0_archive`` directory."""

    try:
        archived = sorted(archive.iterdir())
    except OSError as error:
        _record_unreadable(plan, root, archive, f"the archive directory cannot be listed: {error}")
        return []
    return [
        (task, task / "task.json") for task in archived if not task.is_symlink() and task.is_dir()
    ]


def _census_generation_manifests(root: Path, plan: _Plan, found: list[Path]) -> None:
    """Record every present comparison generation manifest that cannot be read.

    Classification consults a manifest only while a copy lies beside it; a generation whose
    snapshots are all gone (or were never copies) would otherwise hide its malformed record.
    Only files named like a generation manifest inside a ``comparison-generations`` tree are
    generation-shaped; anything else named ``manifest.json`` belongs to another tool and is
    not read here. Symlinks are never trusted.
    """

    for path in found:
        if path.name != COMPARISON_MANIFEST_NAME:
            continue
        try:
            parts = path.relative_to(root).parts
        except ValueError:
            continue
        if COMPARISON_GENERATIONS_DIRECTORY not in parts:
            continue
        if path.is_symlink() or not path.is_file():
            continue
        try:
            read_manifest(path)
        except (KnowledgeStorageError, OSError, ValueError) as error:
            _record_unreadable(
                plan,
                root,
                path,
                f"the comparison generation manifest {path} is present but cannot be "
                f"read as a generation record ({error}); refusing: snapshot ownership "
                "cannot be established, so nothing it might own is deleted",
            )
