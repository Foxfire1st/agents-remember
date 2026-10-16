"""Read-only effective status for each disposable sprint closeout projection."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from agents_remember.controlplane.closeout_queue_store import CloseoutQueueStore
from agents_remember.kernel.git_reference_files import packed_references
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.projection import (
    CloseoutCandidateNode,
    CloseoutProjectionProblemNode,
    CloseoutQueueNode,
)
from agents_remember.serving.projections.snapshots_impl._common import (
    _iter_task_document_payloads,
    _read_json,
    _task_doc_cache,
)
from agents_remember.tasks import TASK_DOCUMENT_SCHEMA, TaskDocument
from agents_remember.tasks.retired_rows import payload_is_sprint
from agents_remember.worktrees.modules.capture_baseline import _snapshot_entries
from agents_remember.worktrees.queue.closeout_projection import (
    _leaf_door_sources,
    _task_census,
    capture_projection_source,
)

#: Files walked for the tasks-tree digest; more means an unreadable digest and a full
#: rebuild (safe direction, bounded work).
_TASKS_DIGEST_FILE_CAP = 100000


#: First-level coordination subtrees a closeout live fingerprint can read (F09/L166).
#: A scoped CHANGE sample naming none of these (and no task file, per the unchanged
#: digest) proves no live input moved through a watched write: worktree checkouts,
#: their gitdirs and file snapshots live under ``worktrees/``, memory checkouts under
#: ``memory-repos/``, and contracts/documents under ``tasks/``. Source checkouts
#: outside the coordination tree are unwatched, so their moves were always
#: heartbeat/full-bounded, before and after this gate.
_LIVE_INPUT_ROOT_NAMES = ("tasks", "worktrees", "memory-repos")


def _sample_touches_live_inputs(coordination_root: Path, paths: tuple[str, ...]) -> bool:
    """Whether a scoped wake sample can name a live-fingerprint input (F09/L166).

    Pure string-prefix work, zero IO: each sample path is normalized and its
    first component under the coordination root is compared against
    :data:`_LIVE_INPUT_ROOT_NAMES`. Anything outside the tree (or the tree
    itself, or an un-normalizable path) fails OPEN to verification -- the gate
    only ever skips on positive evidence that no live input was named.
    """
    prefix = os.path.normpath(coordination_root.as_posix()) + os.sep
    for raw in paths:
        norm = os.path.normpath(raw)
        if not norm.startswith(prefix):
            return True
        first = norm[len(prefix) :].split(os.sep, 1)[0]
        if first in _LIVE_INPUT_ROOT_NAMES:
            return True
    return False


#: Entries walked for one worktree's live fingerprint; more means that sprint recomputes
#: (safe direction, bounded work per pass).
_LIVE_SNAPSHOT_ENTRY_CAP = 5000


def tasks_tree_digest(tasks_root: Path, *, paths: list[Path] | None = None) -> str | None:
    """One digest over every task-tree file's path, size, mtime and ctime (F09/F13).

    Task documents, enclosure contracts and queue store files all live under tasks/, so any
    write a queue is built from moves this digest. ``ctime`` is part of the identity
    (the F07 lesson): a rewrite whose ``(mtime_ns, size)`` was pinned via ``os.utime``
    still changes ctime, so without it the digest would call changed bytes unchanged
    and retain stale queues. A metadata-only change flips the digest but re-validates
    to equal overrides, so it re-lists without recomputing. ``None`` means the tree
    could not be walked: the caller rebuilds, exactly as without the gate.

    With ``paths``, no walk happens: exactly those files are stat-checked against the
    retained per-file stat map (deletions drop their rows) and the digest hashes the
    map (F09). Callers pass ``paths`` only from a complete wake path sample, which
    holds every watcher-accepted change including additions; the first call (no map
    yet) and a ``None`` sample walk and rebuild the map, which also prunes entries
    for files that left the tree.
    """
    if paths is None:
        return _walk_tasks_tree_digest(tasks_root)
    stats = _digest_file_stats.get(str(tasks_root))
    if stats is None:
        return _walk_tasks_tree_digest(tasks_root)
    for path in paths:
        try:
            relative = path.relative_to(tasks_root).as_posix()
        except ValueError:
            continue
        try:
            stamp = path.stat()
        except OSError:
            stats.pop(relative, None)
            continue
        stats[relative] = (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
    digest = hashlib.sha256()
    for name in sorted(stats):
        size, mtime_ns, ctime_ns = stats[name]
        digest.update(f"{name}\0{size}\0{mtime_ns}\0{ctime_ns}\0".encode())
    return digest.hexdigest()


#: Retained per-file task-tree stats by tasks root, LRU-bounded (F09): standalone
#: callers address temporary coordination trees during tests and diagnostics.
_digest_file_stats: dict[str, dict[str, tuple[int, int, int]]] = {}


def _walk_tasks_tree_digest(tasks_root: Path) -> str | None:
    """Full task-tree walk that rebuilds the retained per-file stat map (F09)."""
    try:
        found = _walk_files(tasks_root, _TASKS_DIGEST_FILE_CAP)
    except OSError:
        return None
    if found is None:
        return None
    names = sorted(str(path.relative_to(tasks_root)) for path in found)
    digest = hashlib.sha256()
    stats: dict[str, tuple[int, int, int]] = {}
    for name in names:
        try:
            stamp = (tasks_root / name).stat()
        except OSError:
            return None
        stats[name] = (stamp.st_size, stamp.st_mtime_ns, stamp.st_ctime_ns)
        digest.update(
            f"{name}\0{stamp.st_size}\0{stamp.st_mtime_ns}\0{stamp.st_ctime_ns}\0".encode()
        )
    _digest_file_stats[str(tasks_root)] = stats
    while len(_digest_file_stats) > 8:
        _digest_file_stats.pop(next(iter(_digest_file_stats)))
    return digest.hexdigest()


def _task_subtree_paths(tasks_root: Path, paths: tuple[str, ...]) -> list[Path]:
    """Wake paths below the task tree as root-joined absolute paths (F09)."""
    try:
        resolved_root = tasks_root.resolve()
    except OSError:
        resolved_root = tasks_root
    out: list[Path] = []
    for raw in paths:
        candidate = Path(raw)
        try:
            out.append(tasks_root / candidate.relative_to(tasks_root))
            continue
        except ValueError:
            pass
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        try:
            out.append(tasks_root / resolved.relative_to(resolved_root))
        except ValueError:
            continue
    return out


def _relist_task_documents(
    tasks_root: Path,
    named: list[Path],
    cache: CloseoutQueueCache,
) -> tuple[
    dict[TaskDocumentRef, TaskDocument],
    list[tuple[TaskDocumentRef, TaskDocument, Path]],
]:
    """Re-list task documents for exactly the named files, merging retained overrides (F09).

    Payload reads touch only named files (the stat-gated cache serves the rest from
    memory); only changed payloads re-validate, deletions drop their refs, and the
    sprint list rebuilds from the merged overrides exactly like the full reference.
    """
    fresh = _task_doc_cache.payloads(
        tasks_root, sorted(named), read_payload=_read_json, prune=False
    )
    current = {path for path, _ in _task_doc_cache.cached(tasks_root)}
    overrides = _merge_relisted_overrides(tasks_root, named, fresh, current, cache)
    cached_payloads = dict(_task_doc_cache.cached(tasks_root))
    return overrides, _sprint_entries(tasks_root, overrides, cached_payloads)


def _merge_relisted_overrides(
    tasks_root: Path,
    named: list[Path],
    fresh: list[tuple[Path, dict[str, object]]],
    current: set[Path],
    cache: CloseoutQueueCache,
) -> dict[TaskDocumentRef, TaskDocument]:
    """Merge fresh payloads into retained overrides, dropping vanished refs (F09)."""
    fresh_paths = {path for path, _ in fresh}
    overrides = dict(cache.overrides)
    for ref in [ref for ref in overrides if tasks_root / ref.repository / ref.path not in current]:
        del overrides[ref]
    for path, payload in fresh:
        validated = _validated_override(tasks_root, path, payload)
        if validated is not None:
            ref, doc = validated
            overrides[ref] = doc
    for path in named:
        if path not in fresh_paths:
            for ref in [ref for ref in overrides if tasks_root / ref.repository / ref.path == path]:
                del overrides[ref]
    return overrides


def _validated_override(
    tasks_root: Path, path: Path, payload: dict[str, object]
) -> tuple[TaskDocumentRef, TaskDocument] | None:
    """The override entry for one payload, None when it validates out (F09)."""
    if payload.get("schema") != TASK_DOCUMENT_SCHEMA:
        return None
    try:
        doc = TaskDocument.model_validate(payload)
    except ValueError:
        return None
    try:
        relative = path.relative_to(tasks_root / doc.repo)
    except ValueError:
        return None
    return TaskDocumentRef(repository=doc.repo, path=relative.as_posix()), doc


def _sprint_entries(
    tasks_root: Path,
    overrides: dict[TaskDocumentRef, TaskDocument],
    cached_payloads: dict[Path, dict[str, object]],
) -> list[tuple[TaskDocumentRef, TaskDocument, Path]]:
    """The sprint rows for merged overrides, exactly like the full reference (F09)."""
    sprints = []
    for ref, doc in overrides.items():
        path = tasks_root / ref.repository / ref.path
        payload = cached_payloads.get(path)
        if (
            doc.is_sprint
            and payload is not None
            and payload_is_sprint(payload)
            and payload.get("schema") == TASK_DOCUMENT_SCHEMA
        ):
            sprints.append((ref, doc, path))
    return sprints


def _walk_files(root: Path, cap: int) -> list[Path] | None:
    """Every file under ``root``, or ``None`` past the cap / on any error."""

    found: list[Path] = []
    stack = [root]
    while stack:
        try:
            with os.scandir(stack.pop()) as entries:
                children = sorted(entries, key=lambda entry: entry.name)
        except OSError:
            return None
        for child in children:
            try:
                if child.is_dir(follow_symlinks=False):
                    stack.append(Path(child.path))
                elif child.is_file(follow_symlinks=False):
                    found.append(Path(child.path))
                    if len(found) > cap:
                        return None
            except OSError:
                return None
    return found


@dataclass
class CloseoutQueueCache:
    """What the last closeout read computed, for per-sprint retention (F13)."""

    tasks_digest: str | None = None
    live: dict[str, str | None] = field(default_factory=dict)
    queues: list[CloseoutQueueNode] = field(default_factory=list)
    overrides: dict[TaskDocumentRef, TaskDocument] = field(default_factory=dict)


def refresh_closeout_queues(
    coordination_root: Path,
    *,
    now: datetime,
    cache: CloseoutQueueCache | None,
    paths: tuple[str, ...] | None = None,
) -> tuple[list[CloseoutQueueNode], CloseoutQueueCache]:
    """The sprint queues, recomputing exactly the sprints whose inputs moved (F09/F13).

    The tasks-tree digest (name/size/mtime/ctime per file) decides whether the task
    documents are re-listed at all: an unchanged digest reuses the retained overrides
    with no re-list and no re-validation, so a sweep's compaction under workspace/
    touches no task file on its tick. Only the per-sprint live fingerprints re-run --
    they are the touch detectors for code/memory edits and tip advances outside
    tasks/. When the digest moved, the shared payload cache re-reads only
    stat-changed files; each sprint then recomputes only when its own inputs moved
    (a changed live fingerprint or changed document inputs) and is otherwise
    retained byte for byte, invalid-empty responses included. A fingerprint this
    read cannot take recomputes, never retains.
    """

    if cache is None:
        cache = CloseoutQueueCache()
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return [], CloseoutQueueCache()
    named = (
        _task_subtree_paths(tasks_root, paths)
        if paths is not None and cache.tasks_digest is not None
        else None
    )
    digest = tasks_tree_digest(tasks_root, paths=named)
    if digest is not None and digest == cache.tasks_digest:
        overrides = cache.overrides
        sprints = [
            (ref, doc, tasks_root / ref.repository / ref.path)
            for ref, doc in overrides.items()
            if doc.is_sprint
        ]
    elif named:
        overrides, sprints = _relist_task_documents(tasks_root, named, cache)
    else:
        overrides, sprints = _relist_all_task_documents(tasks_root, now)
    if (
        paths
        and digest is not None
        and digest == cache.tasks_digest
        and not _sample_touches_live_inputs(coordination_root, paths)
    ):
        # A scoped CHANGE sample naming no task file (digest unchanged, so the
        # re-hash above read nothing) and no live-input subtree proves no live
        # fingerprint input moved through a watched write: retain the queues and
        # the fingerprints with zero closeout IO on this pass (F09/L166). Only a
        # non-empty scoped sample qualifies -- heartbeat (), full and incomplete
        # (None) always re-verify, which is what keeps out-of-band moves
        # (unwatched checkouts, watcher gaps) healing-bounded exactly as before.
        return list(cache.queues), CloseoutQueueCache(
            tasks_digest=digest,
            live=dict(cache.live),
            queues=list(cache.queues),
            overrides=cache.overrides,
        )
    queues, live = _recompute_queues(coordination_root, now, cache, overrides, sprints)
    return queues, CloseoutQueueCache(
        tasks_digest=digest, live=live, queues=queues, overrides=overrides
    )


def _relist_all_task_documents(
    tasks_root: Path, now: datetime
) -> tuple[
    dict[TaskDocumentRef, TaskDocument],
    list[tuple[TaskDocumentRef, TaskDocument, Path]],
]:
    """Full task-document re-list with validation, exactly as before (F09/F13)."""
    payloads = _iter_task_document_payloads(tasks_root, now=now)
    overrides: dict[TaskDocumentRef, TaskDocument] = {}
    sprints: list[tuple[TaskDocumentRef, TaskDocument, Path]] = []
    for path, payload in payloads:
        validated = _validated_override(tasks_root, path, payload)
        if validated is None:
            continue
        ref, doc = validated
        overrides[ref] = doc
        if payload_is_sprint(payload) and payload.get("schema") == TASK_DOCUMENT_SCHEMA:
            sprints.append((ref, doc, path))
    return overrides, sprints


def _recompute_queues(
    coordination_root: Path,
    now: datetime,
    cache: CloseoutQueueCache,
    overrides: dict[TaskDocumentRef, TaskDocument],
    sprints: list[tuple[TaskDocumentRef, TaskDocument, Path]],
) -> tuple[list[CloseoutQueueNode], dict[str, str | None]]:
    """Recompute exactly the sprints whose inputs moved, retaining the rest (F09/F13)."""
    inputs_moved = overrides != cache.overrides
    by_key = {queue.sprintRef.key: queue for queue in cache.queues}
    queues = []
    live: dict[str, str | None] = {}
    for ref, doc, path in sprints:
        fingerprint = sprint_live_fingerprint(coordination_root, ref, overrides)
        previous = by_key.get(ref.key)
        if (
            previous is not None
            and not inputs_moved
            and fingerprint is not None
            and fingerprint == cache.live.get(ref.key)
        ):
            queues.append(previous)
        else:
            queues.append(
                _project_queue(coordination_root, doc, path, now=now, overrides=overrides)
            )
        live[ref.key] = fingerprint
    return queues, live


def sprint_live_fingerprint(
    coordination_root: Path,
    sprint_ref: TaskDocumentRef,
    overrides: dict[TaskDocumentRef, TaskDocument] | None,
) -> str | None:
    """One sprint's live inputs as a digest, or ``None`` when any cannot be read (F13).

    Worktree heads, index stamps, file stats and source branch tips: everything a
    code/memory edit or a tip advance moves under a closeout queue. A sprint whose
    traversal fails recomputes; an absent worktree or branch is a stable fact, not a
    failure, and fingerprints as absent.
    """

    try:
        tasks = _task_census(coordination_root, sprint_ref, [], overrides)
    except Exception:
        return None
    digest = hashlib.sha256()
    try:
        for leaf, _master, _order in tasks.leafs:
            for source in _leaf_door_sources(coordination_root, leaf, []):
                digest.update(_door_source_live_parts(source).encode())
    except Exception:
        return None
    return digest.hexdigest()


def _door_source_live_parts(source: object) -> str:
    """The live fingerprint of one door source's contract, deterministically rendered."""

    contract = getattr(source, "contract", None)
    address = getattr(source, "address", "")
    if contract is None:
        return f"no-contract\0{address}\0"
    code_worktree = _worktree_live_parts(getattr(contract, "code_worktree", None), "code")
    memory_worktree = _worktree_live_parts(getattr(contract, "memory_worktree", None), "memory")
    code_tip = _branch_tip_parts(
        getattr(contract, "code_repo_path", None),
        getattr(contract, "code_source_branch", ""),
    )
    memory_tip = _branch_tip_parts(
        getattr(contract, "memory_repo_path", None),
        getattr(contract, "memory_source_branch", ""),
    )
    if code_worktree is None or memory_worktree is None or code_tip is None or memory_tip is None:
        raise _LiveFingerprintUnreadable
    return f"contract\0{address}\0{code_worktree}{memory_worktree}{code_tip}{memory_tip}"


class _LiveFingerprintUnreadable(Exception):
    """One live input could not be read; the sprint recomputes instead of retaining."""


def _worktree_live_parts(worktree: Path | None, _side: str) -> str | None:
    """Head content, index stamp and file stats of one worktree, or ``None`` to recompute."""

    if worktree is None:
        return "absent\0"
    gitdir = _worktree_gitdir(worktree)
    if gitdir is None:
        return "absent\0"
    try:
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
    except OSError:
        return "no-head\0"
    try:
        stamp = (gitdir / "index").stat()
        index = f"{stamp.st_size}\0{stamp.st_mtime_ns}\0{stamp.st_ctime_ns}\0"
    except OSError:
        index = "no-index\0"
    entries = _snapshot_entries(worktree, max_entries=_LIVE_SNAPSHOT_ENTRY_CAP)
    if entries is None:
        return None
    digest = hashlib.sha256(repr(entries).encode()).hexdigest()
    return f"head\0{head}\0index\0{index}entries\0{digest}\0"


def _worktree_gitdir(worktree: Path) -> Path | None:
    """The worktree's git directory, following a gitfile; ``None`` when absent."""

    dotgit = worktree / ".git"
    try:
        if dotgit.is_dir():
            return dotgit
        if dotgit.is_file():
            text = dotgit.read_text(encoding="utf-8").strip()
            if text.startswith("gitdir: "):
                target = Path(text[len("gitdir: ") :])
                return target if target.is_absolute() else worktree / target
    except OSError:
        return None
    return None


def _branch_tip_parts(repo_path: Path | None, branch: str) -> str | None:
    """One source branch tip's content, ``absent`` when unborn, ``None`` to recompute."""

    if repo_path is None or not branch:
        return "absent\0"
    gitdir = _worktree_gitdir(repo_path)
    if gitdir is None:
        return "absent\0"
    try:
        commondir_text = (gitdir / "commondir").read_text(encoding="utf-8").strip()
        common = (gitdir / commondir_text).resolve(strict=False)
    except OSError:
        common = gitdir
    ref = f"refs/heads/{branch}"
    try:
        content = (common / ref).read_text(encoding="utf-8").strip()
        if content:
            return f"tip\0{content}\0"
    except OSError:
        pass
    try:
        value = packed_references(common).get(ref)
    except OSError:
        return "absent\0"
    return f"tip\0{value}\0" if value else "absent\0"


def read_closeout_queues(coordination_root: Path, *, now: datetime) -> list[CloseoutQueueNode]:
    """Project every sprint master's closeout queue from its durable artifact.

    One validated task-document set is shared by every sprint of the refresh
    (MIK-R81 rule 3): each sprint census resolves the same documents, so
    validating each file once per refresh instead of once per sprint keeps the
    cost with the changed files, not with sprints times files.
    """
    tasks_root = coordination_root / "tasks"
    if not tasks_root.is_dir():
        return []
    payloads = _iter_task_document_payloads(tasks_root, now=now)
    overrides: dict[TaskDocumentRef, TaskDocument] = {}
    for path, payload in payloads:
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        try:
            relative = path.relative_to(tasks_root / doc.repo)
        except ValueError:
            continue
        overrides[TaskDocumentRef(repository=doc.repo, path=relative.as_posix())] = doc
    queues: list[CloseoutQueueNode] = []
    for path, payload in payloads:
        if not payload_is_sprint(payload) or payload.get("schema") != TASK_DOCUMENT_SCHEMA:
            continue
        try:
            doc = TaskDocument.model_validate(payload)
        except ValueError:
            continue
        queues.append(_project_queue(coordination_root, doc, path, now=now, overrides=overrides))
    return queues


def _project_queue(
    coordination_root: Path,
    doc: TaskDocument,
    path: Path,
    *,
    now: datetime,
    overrides: dict[TaskDocumentRef, TaskDocument] | None = None,
) -> CloseoutQueueNode:
    sprint_ref = TaskDocumentRef(
        repository=doc.repo,
        path=path.relative_to(path.parents[1]).as_posix(),
    )
    timestamp = now.replace(microsecond=0).isoformat()
    source = capture_projection_source(
        coordination_root, sprint_ref, timestamp=timestamp, overrides=overrides
    )
    state = CloseoutQueueStore(coordination_root, sprint_ref).read_effective(
        timestamp=timestamp,
        source=source.identity,
    )
    return CloseoutQueueNode(
        sprintRef=sprint_ref,
        revision=state.revision,
        serviceCondition=state.serviceCondition,
        sourceClassification=state.sourceClassification,
        sourceFingerprint=state.sourceFingerprint,
        sourceProblems=[
            CloseoutProjectionProblemNode(**problem.model_dump(mode="json"))
            for problem in state.sourceProblems
        ],
        members=[_candidate_node(member) for member in state.members],
    )


def _candidate_node(candidate) -> CloseoutCandidateNode:
    return CloseoutCandidateNode(
        generationId=candidate.generationId,
        taskDocumentRef=candidate.taskDocumentRef,
        owningMaster=candidate.owningMaster,
        classification=candidate.classification,
        priority=candidate.priority,
        order=candidate.order,
        reasons=candidate.reasons,
    )
