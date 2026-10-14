"""Rendered, read-only leaf documents inside the enclosure, outside its Git trees."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_bytes
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.tasks.document_refs import TaskDocumentTopology
from agents_remember.tasks.task_paths import (
    is_foreign_enclosure_contract,
    iter_leaf_enclosure_contracts,
)
from agents_remember.worktrees.task_leaf_binding import (
    TaskLeafBindingError,
    resolve_leaf_task_binding,
)
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

TASK_DOCUMENTS_DIRECTORY = "task-documents"

logger = logging.getLogger(__name__)

# Resource budgets (F01.20): generous, explicit, never silent. Requirement
# copies are Markdown-scale files; these bounds stop a pathological leaf
# from turning a launch or refresh into unbounded work, refusing loudly
# instead of skipping quietly.
COPY_FILE_MAX_BYTES = 16 * 1024 * 1024
COPY_TOTAL_MAX_BYTES = 64 * 1024 * 1024
# Status drift scans hash copies on every call; files over this budget are
# compared by size and mtime instead, and the note says so.
STATUS_HASH_MAX_BYTES = 4 * 1024 * 1024
# Linkage report files accumulate per enclosure; the writer keeps this many
# of the newest and reclaims older ones, best effort, never failing the call.
LINKAGE_REPORT_KEEP = 50
# Reconciliation scans visit at most this many copy-tree entries: enough for
# any honest enclosure, bounded against a hostile or huge tree.
COPY_SCAN_MAX_ENTRIES = 10_000
COPY_INDEX_MAX_BYTES = 8 * 1024 * 1024
COPY_STATUS_MAX_ROWS = 2_000
# A rollback snapshot is as large as a copy may be (F01.7/F01.20): every
# admitted copy is restorable, so a failure after the first write restores
# every file the transaction wrote, the new copy too. Anything still over
# budget is refused by _publish_copy_writes before the first write, never
# skipped silently.
COPY_ROLLBACK_FILE_MAX_BYTES = COPY_FILE_MAX_BYTES
COPY_ROLLBACK_TOTAL_MAX_BYTES = COPY_TOTAL_MAX_BYTES

# A file a leaf document names under ``requirements``: ``[text](target)`` in a
# string entry. Fragments never name a file; remote targets are not files.
_REQUIREMENT_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_REMOTE_TARGET_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:")


def prepare_task_document_copies(
    coordination_root: Path, group: Path, reference: TaskDocumentRef
) -> list[dict[str, Any]]:
    """Publish real rendered files and one index before the launch's first turn."""
    writes, rows = _copy_writes(coordination_root, group, reference)
    _publish_copy_writes(writes)
    root = group / TASK_DOCUMENTS_DIRECTORY
    return [{**row, "path": (root / row["path"]).as_posix()} for row in rows]


def _copy_writes(
    coordination_root: Path, group: Path, reference: TaskDocumentRef
) -> tuple[dict[Path, bytes], list[dict[str, Any]]]:
    root = group / TASK_DOCUMENTS_DIRECTORY
    _require_real_destination(root, group)
    topology = TaskDocumentTopology(coordination_root)
    writes: dict[Path, bytes] = {}
    rows = []
    timestamp = datetime.now(UTC).isoformat()
    source = topology.path_for_ref(reference)
    destination = root / Path(reference.path).with_suffix(".md")
    _require_real_destination(destination, root)
    raw = source.with_suffix(".md").read_bytes()
    writes[destination] = raw
    rows.append(
        {
            "reference": reference.model_dump(mode="json"),
            "canonicalJsonSha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "copySha256": hashlib.sha256(raw).hexdigest(),
            "path": destination.relative_to(root).as_posix(),
            "copiedAt": timestamp,
        }
    )
    # MIK-R111 rule 3: beside the leaf, every file the leaf names under
    # ``requirements``, at the same relative path as under
    # ``tasks/<repository>/``. A named file that does not exist still gets a
    # row that says so; the start succeeds. References, masters and sprint
    # documents are never requirements.
    repository_tasks = coordination_root / "tasks" / reference.repository
    try:
        leaf_requirements = json.loads(source.read_bytes()).get("requirements", [])
    except ValueError:
        leaf_requirements = []
    if isinstance(leaf_requirements, list):
        copy_total = 0
        for target in requirement_copy_targets(repository_tasks, source, leaf_requirements):
            requirement_source = repository_tasks / target
            requirement_destination = root / target
            _require_real_destination(requirement_destination, root)
            if requirement_source.is_file() and not requirement_source.is_symlink():
                try:
                    requirement_size = requirement_source.stat().st_size
                except OSError:
                    requirement_size = 0
                if requirement_size > COPY_FILE_MAX_BYTES:
                    raise ValueError(
                        f"task document copy too large: {target} holds "
                        f"{requirement_size} bytes, over the {COPY_FILE_MAX_BYTES} "
                        "byte budget; shrink the requirement file or copy it by hand."
                    )
                requirement_raw = requirement_source.read_bytes()
                copy_total += len(requirement_raw)
                if copy_total > COPY_TOTAL_MAX_BYTES:
                    raise ValueError(
                        f"task document copies too large: {target} takes the leaf over "
                        f"the {COPY_TOTAL_MAX_BYTES} byte budget; name fewer files."
                    )
                writes[requirement_destination] = requirement_raw
                rows.append(
                    {
                        "kind": "requirement",
                        "reference": target,
                        "repository": reference.repository,
                        "sourceSha256": hashlib.sha256(requirement_raw).hexdigest(),
                        "copySha256": hashlib.sha256(requirement_raw).hexdigest(),
                        "path": target,
                        "copiedAt": timestamp,
                    }
                )
            else:
                rows.append(
                    {
                        "kind": "requirement",
                        "reference": target,
                        "repository": reference.repository,
                        "path": target,
                        "missing": True,
                        "copiedAt": timestamp,
                    }
                )
    index = root / "INDEX.json"
    _require_real_destination(index, root)
    writes[index] = (
        json.dumps({"schema": "ar-task-documents/v1", "documents": rows}, indent=2) + "\n"
    ).encode()
    return writes, rows


def requirement_copy_targets(
    repository_tasks: Path, leaf_json_path: Path, requirements: list[Any]
) -> list[str]:
    """The repo-relative files one leaf document names under ``requirements``.

    String entries name files through Markdown links, resolved against the
    leaf's own task folder; typed packet refs name their ``path`` the same
    way, whether they arrive as objects or as persisted dictionaries.
    References, masters and sprint documents are never requirements, so
    they never arrive here. Remote and absolute targets, and targets that
    escape ``tasks/<repository>/``, name nothing to copy.
    """

    leaf_dir = leaf_json_path.parent
    targets: list[str] = []
    seen: set[str] = set()
    for entry in requirements:
        if isinstance(entry, str):
            raw_targets = _REQUIREMENT_LINK_RE.findall(entry)
        elif isinstance(entry, Mapping):
            # A persisted approved packet ref (path/stableId/version): its
            # path names the file the same way a link does.
            path = entry.get("path")
            raw_targets = [path] if isinstance(path, str) else []
        elif hasattr(entry, "path"):
            raw_targets = [entry.path]
        else:
            raw_targets = []
        for raw in raw_targets:
            target = str(raw).strip().split("#", 1)[0].strip()
            if not target or _REMOTE_TARGET_RE.match(target):
                continue
            candidate = Path(target)
            if candidate.is_absolute():
                continue
            resolved = (leaf_dir / candidate).resolve(strict=False)
            try:
                relative = resolved.relative_to(repository_tasks.resolve(strict=False))
            except ValueError:
                continue
            relative_posix = relative.as_posix()
            # Linear dedup: membership in a set, not a scan of the list.
            if relative_posix not in seen:
                seen.add(relative_posix)
                targets.append(relative_posix)
    return targets


def stale_copy_line(coordination_root: Path, group: Path) -> str | None:
    """One line naming task-document copies that differ from their sources.

    MIK-R111 rule 3: ``worktree_status`` says in one line when a copied file
    differs from its source. ``None`` when the enclosure holds no index or
    every copy matches: a changed source, an edited copy, and a named file
    whose source now exists (or is gone) all count as differing. Never raises:
    a copy problem must not fail a status call.
    """

    try:
        return _stale_copy_line(coordination_root, group)
    except (OSError, ValueError):
        return None


def _index_size_note(index_path: Path) -> str | None:
    """A loud over-budget note, or ``None`` when the index reads normally."""
    try:
        size = index_path.stat().st_size
    except OSError:
        return None
    if size <= COPY_INDEX_MAX_BYTES:
        return None
    return (
        "task document copy index too large to check "
        f"({size} bytes over the {COPY_INDEX_MAX_BYTES} "
        "byte budget); copy drift is unknown, not absent."
    )


def _stale_copy_line(coordination_root: Path, group: Path) -> str | None:
    index_path = group / TASK_DOCUMENTS_DIRECTORY / "INDEX.json"
    if not index_path.is_file() or index_path.is_symlink():
        return None
    if (note := _index_size_note(index_path)) is not None:
        return note
    try:
        rows = json.loads(index_path.read_bytes()).get("documents", [])
    except (ValueError, OSError):
        return None
    if not isinstance(rows, list):
        return None
    examined = rows[:COPY_STATUS_MAX_ROWS]
    unchecked = len(rows) - len(examined)
    differing = [
        note
        for row in examined
        if isinstance(row, dict) and isinstance(row.get("path"), str)
        for note in _stale_row_notes(coordination_root, group, row)
    ]
    if unchecked > 0:
        # Loud truncation: the status call names how many rows it did not
        # examine rather than silently passing over them.
        differing.append(
            f"+{unchecked} further row(s) unchecked (status row budget {COPY_STATUS_MAX_ROWS})"
        )
    if not differing:
        return None
    return (
        f"task document copies differ from their sources: {'; '.join(differing)}. "
        "The next task_doc write on the leaf document refreshes them."
    )


def _row_source(coordination_root: Path, row: dict[str, Any], relative: str) -> Path | None:
    """The canonical source file one index row was copied from, if namable."""

    if row.get("kind") == "requirement":
        repository = row.get("repository")
        if not isinstance(repository, str) or not repository:
            return None
        return coordination_root / "tasks" / repository / relative
    reference = row.get("reference", {})
    if not isinstance(reference, dict) or "repository" not in reference:
        return None
    return coordination_root / "tasks" / reference["repository"] / relative


def _read_if_regular(path: Path) -> bytes | None:
    """The bytes of one regular file, or ``None`` when absent or unreadable."""

    if not path.is_file() or path.is_symlink():
        return None
    try:
        return path.read_bytes()
    except OSError:
        return None


def _stat_key(path: Path) -> tuple[int, float] | None:
    """The (size, mtime) of one regular file, or ``None`` when absent or unreadable."""

    if not path.is_file() or path.is_symlink():
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return (stat.st_size, stat.st_mtime)


def _stale_row_notes(coordination_root: Path, group: Path, row: dict[str, Any]) -> list[str]:
    """The ways one copy differs from its source; empty when it matches."""

    notes: list[str] = []
    relative = str(row["path"])
    source = _row_source(coordination_root, row, relative)
    if source is None:
        return notes
    copy = group / TASK_DOCUMENTS_DIRECTORY / relative
    if row.get("missing") is True:
        if _stat_key(source) is not None:
            notes.append(f"{relative} (named file now exists)")
        return notes
    if _stat_key(source) is None:
        notes.append(f"{relative} (source is gone)")
        return notes
    if _stat_key(copy) is None:
        notes.append(f"{relative} (copy is gone)")
        return notes
    if _over_hash_budget(source) or _over_hash_budget(copy):
        # Large files are never hashed: a status call must not reread
        # megabytes on every poll. A large pair matches when the sizes agree
        # and the source predates the copy; anything else is noted as
        # differing by size or time. Same size with a newer source still
        # counts as differing: the comparison is mtime-based, and the note
        # says so.
        if not _large_pair_matches(source, copy, row):
            notes.append(f"{relative} (large file differs by size or mtime)")
        return notes
    notes.extend(_hash_row_notes(source, copy, row, relative))
    return notes


def _hash_row_notes(source: Path, copy: Path, row: dict[str, Any], relative: str) -> list[str]:
    """The ways one hashable copy differs from its source; empty when it matches."""
    if (current := _read_if_regular(source)) is None:
        return [f"{relative} (source is gone)"]
    if hashlib.sha256(current).hexdigest() != row.get("sourceSha256", row.get("copySha256")):
        return [f"{relative} (source changed)"]
    if (copied := _read_if_regular(copy)) is None:
        return [f"{relative} (copy is gone)"]
    if hashlib.sha256(copied).hexdigest() != row.get("copySha256"):
        return [f"{relative} (copy edited)"]
    return []


def _large_pair_matches(source: Path, copy: Path, row: dict[str, Any]) -> bool:
    """Whether a large source/copy pair reads as matching without hashing."""
    source_key = _stat_key(source)
    copy_key = _stat_key(copy)
    if source_key is None or copy_key is None:
        return False
    if source_key[0] != copy_key[0]:
        return False
    try:
        copied_at = datetime.fromisoformat(str(row.get("copiedAt", "")))
    except ValueError:
        return False
    if copied_at.tzinfo is None:
        copied_at = copied_at.replace(tzinfo=UTC)
    return source_key[1] <= copied_at.timestamp()


def _over_hash_budget(path: Path) -> bool:
    """Whether one file is over the status hash budget."""
    try:
        return path.stat().st_size > STATUS_HASH_MAX_BYTES
    except OSError:
        return False


def _stale_copy_files(root: Path, current: set[Path]) -> list[Path]:
    """Files under the copy root the current set no longer names.

    A requirement removed from the leaf, a renamed leaf, or a deleted
    source leaves its copy behind; the refresh reconciles the exact file
    set, so those orphans are named for removal. Directories are pruned
    after the files go, best effort. The scan itself is bounded: at most
    ``COPY_SCAN_MAX_ENTRIES`` entries are visited, so a hostile or huge
    tree cannot turn reconciliation into an unbounded walk. Every visited
    entry -- files, directories and links -- counts toward the budget, so
    empty directories cannot slip past it.
    """
    try:
        existing = []
        for visits, path in enumerate(root.rglob("*"), start=1):
            if visits > COPY_SCAN_MAX_ENTRIES:
                break
            if path.is_file() or path.is_symlink():
                existing.append(path)
    except OSError:
        return []
    return sorted(path for path in existing if path not in current)


_ROLLBACK_SKIPPED = object()
"""Marker for a rollback snapshot over budget: the transaction is refused
before the first write (see _publish_copy_writes), so rollback never meets
it silently."""


def _snapshot_for_rollback(path: Path, present: bool, budget: list[int]) -> bytes | object | None:
    """The bytes rollback restores for one path, or why it restores nothing.

    ``None`` means the path was absent (rollback deletes it); the skipped
    marker means the path was over budget (the transaction is refused
    before the first write, so rollback never restores around it). The
    running total in ``budget[0]`` caps all snapshots together.
    """
    if not present:
        return None
    if path.is_symlink() or not path.is_file():
        # Not a regular file: snapshot loudly like the old direct read
        # instead of rolling back over it (directories raise here).
        return path.read_bytes()
    size = path.stat().st_size
    if size > COPY_ROLLBACK_FILE_MAX_BYTES:
        return _ROLLBACK_SKIPPED
    if budget[0] + size > COPY_ROLLBACK_TOTAL_MAX_BYTES:
        return _ROLLBACK_SKIPPED
    budget[0] += size
    return path.read_bytes()


def _restore_rollback_snapshots(
    originals: dict[Path, bytes | object | None],
    removed: dict[Path, bytes | object | None],
) -> None:
    """Restore snapshotted originals and removed files.

    Every snapshot reaches this point: a skipped marker means a caller
    bypassed the prewrite refusal, so the incompleteness raises by name
    instead of leaving files silently unrestored.
    """
    skipped = sorted(
        str(path)
        for path, raw in list(originals.items()) + list(removed.items())
        if raw is _ROLLBACK_SKIPPED
    )
    for path, raw in originals.items():
        if raw is _ROLLBACK_SKIPPED:
            continue
        if raw is None:
            path.unlink(missing_ok=True)
        elif isinstance(raw, bytes):
            atomic_write_bytes(path, raw)
    for path, raw in removed.items():
        if raw is _ROLLBACK_SKIPPED or raw is None:
            continue
        if isinstance(raw, bytes):
            atomic_write_bytes(path, raw)
    if skipped:
        raise RuntimeError(
            "task document copy rollback is incomplete: no snapshot was kept for "
            + ", ".join(skipped)
            + "; those files keep their attempted content."
        )


def _snapshot_size(path: Path) -> str:
    """One path's byte size for a refusal, best effort."""
    try:
        return f"{path.stat().st_size} bytes"
    except OSError:
        return "unknown size"


def _refuse_snapshot_over_budget(
    originals: dict[Path, bytes | object | None],
    removed: dict[Path, bytes | object | None],
) -> None:
    """Refuse a change rollback cannot cover, before the first write.

    Names each over-budget path with its size and both snapshot budgets, so
    the caller can shrink the enclosed files or copy them by hand. Nothing
    has been written when this raises.
    """
    over = sorted(
        f"{path} ({_snapshot_size(path)})"
        for path, raw in list(originals.items()) + list(removed.items())
        if raw is _ROLLBACK_SKIPPED
    )
    if over:
        raise ValueError(
            "task document copies do not fit the rollback snapshot budgets "
            f"({COPY_ROLLBACK_FILE_MAX_BYTES} bytes per file, "
            f"{COPY_ROLLBACK_TOTAL_MAX_BYTES} bytes per change): "
            + "; ".join(over)
            + ". Shrink the enclosed files or copy them by hand; nothing was written."
        )


def _publish_copy_writes(writes: dict[Path, bytes], *, deletions: list[Path] | None = None) -> None:
    """Write every copy, then delete the stale ones, or refuse first.

    The snapshot total is checked BEFORE the first write (F01.7/F01.20): a
    change whose snapshots do not fit the budgets is refused by name with
    the sizes, and nothing is written. A failure after the first write
    restores every file the transaction wrote, the new copy too.
    """
    doomed = deletions or ()
    budget = [0]
    originals = {path: _snapshot_for_rollback(path, path.exists(), budget) for path in writes}
    removed = {
        path: _snapshot_for_rollback(path, path.is_file() and not path.is_symlink(), budget)
        for path in doomed
    }
    _refuse_snapshot_over_budget(originals, removed)
    try:
        for path, raw in writes.items():
            atomic_write_bytes(path, raw)
        for path in doomed:
            path.unlink(missing_ok=True)
    except BaseException as publish_error:
        try:
            _restore_rollback_snapshots(originals, removed)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"task document copy publication and rollback both failed: {rollback_error}"
            ) from publish_error
        raise


def _refresh_candidates(coordination_root: Path, repository: str, contract: Any) -> set[Path]:
    """The document one enclosure refresh matches: the enclosure's exact leaf.

    A leaf root holds the leaf's copies only, never master content in the
    leaf's place (a leaf publication carries exactly its leaf: no
    ``master/task.md`` beside it). The master artifact is only the fallback
    for contracts without leaf binding info, so a parent publication never
    selects the master over the bound leaf.
    """
    leaf_id = getattr(contract, "leaf_id", None)
    task_root = getattr(contract, "task_root", None)
    if leaf_id and task_root is not None:
        try:
            binding = resolve_leaf_task_binding(
                coordination_root,
                repository,
                task_root,
                leaf_id,
                task_name=getattr(contract, "task_name", None),
            )
            if binding.leaf is not None:
                return {binding.leaf_json_path}
        except TaskLeafBindingError:
            pass
    return {contract.task_artifact.with_suffix(".json")}


def _union_copy_writes(
    coordination_root: Path,
    group: Path,
    docs: list[tuple[Path, TaskDocumentRef]],
    changed: set[Path],
) -> tuple[dict[Path, bytes], list[dict[str, Any]], list[Path]]:
    """Recompute the enclosure's bound document; merge writes, rows and deletions.

    One document's refresh never evicts another's copies: the deletion set
    covers only files absent from the freshly recomputed set. A document
    that fails to recompute keeps its files (fail-soft) unless it is the
    written document, whose failure raises as before. The merged index
    carries the recomputed document's rows.
    """
    root = group / TASK_DOCUMENTS_DIRECTORY
    union: dict[Path, bytes] = {}
    rows: list[dict[str, Any]] = []
    for doc_path, ref in docs:
        try:
            group_writes, doc_rows = _copy_writes(coordination_root, group, ref)
        except (OSError, ValueError):
            if doc_path.resolve() in changed:
                raise
            return {}, [], []
        group_writes.pop(root / "INDEX.json", None)
        union.update(group_writes)
        rows.extend(doc_rows)
    union[root / "INDEX.json"] = (
        json.dumps({"schema": "ar-task-documents/v1", "documents": rows}, indent=2) + "\n"
    ).encode()
    return (
        union,
        rows,
        [path for path in _copy_tree_files(root) if path not in union],
    )


def _copy_tree_files(root: Path) -> list[Path]:
    """Every file under the copy root, bounded like the stale scan."""
    try:
        existing = []
        for visits, path in enumerate(root.rglob("*"), start=1):
            if visits > COPY_SCAN_MAX_ENTRIES:
                break
            if path.is_file() or path.is_symlink():
                existing.append(path)
    except OSError:
        return []
    return sorted(existing)


def refresh_task_document_copies(
    coordination_root: Path, repository: str, written: list[tuple[Path, Path]]
) -> None:
    """A written task document refreshes its own copies and nothing else.

    Only the enclosures of the task folders holding a written document are
    walked. A contract is fully loaded once its raw ``task_artifact`` cell
    names a written document, once its bound leaf (parent-row binding, raw
    cells only) names one, or when it carries no such cell at all:
    sibling contracts in the same task folder are never loaded, so a broken
    sibling cannot roll back this write (MIK-R134 rule 1). A contract whose
    task document lies outside the repository's tasks folder is skipped and
    named in a warning instead of failing the write, whether or not its
    worktree group still exists (rule 2); nothing of it is read as an
    enclosure. A contract that cannot be read at all, and a malformed
    contract naming a written document, still raise when no own contract
    refreshed, so the publication restores the written sources (rule 4 and
    the architect ruling C1-01). A readable file that names no task
    document and fails to parse is skipped with a warning while at least one
    own contract refreshed; when nothing own refreshed it raises, so a
    destroyed own contract still fails closed.
    """

    repository_tasks = coordination_root / "tasks" / repository
    changed = {path.resolve() for path, _markdown in written}
    needles: set[str] = set()
    for json_path, markdown_path in written:
        for candidate in (json_path, markdown_path):
            needles.add(candidate.as_posix())
            needles.add(candidate.resolve().as_posix())
    task_roots = sorted({path.parent for path, _markdown in written})
    work = _RefreshWork(
        coordination_root=coordination_root,
        repository=repository,
        repository_tasks=repository_tasks,
        changed=changed,
    )
    for task_root in task_roots:
        for contract_path in iter_leaf_enclosure_contracts(task_root):
            selection = _select_refresh_contract(
                contract_path,
                needles,
                coordination_root=coordination_root,
                repository=repository,
                repository_tasks=repository_tasks,
            )
            if selection == "foreign":
                work.skipped_foreign.append(contract_path)
                continue
            if selection == "unrelated":
                continue
            try:
                contract = load_contract(contract_path)
            except (ContractError, OSError) as error:
                if selection == "unparsed":
                    work.unrecognized.append((contract_path, error))
                    continue
                raise
            if _stage_refresh_contract(work, contract_path, contract):
                work.matched_own = True
    work.finish()
    _publish_copy_writes(work.writes, deletions=work.removals)


@dataclass
class _RefreshWork:
    """The mutable per-refresh state: inputs, staged writes and skip lists."""

    coordination_root: Path
    repository: str
    repository_tasks: Path
    changed: set[Path]
    writes: dict[Path, bytes] = field(default_factory=dict)
    skipped_foreign: list[Path] = field(default_factory=list)
    unrecognized: list[tuple[Path, Exception]] = field(default_factory=list)
    matched_own: bool = False
    removals: list[Path] = field(default_factory=list)

    def finish(self) -> None:
        """Fail closed when nothing own refreshed past unparseable files."""
        if self.unrecognized and not self.matched_own:
            raise self.unrecognized[0][1]
        _log_refresh_skips(self.skipped_foreign, self.unrecognized, self.repository_tasks)


def _select_refresh_contract(
    contract_path: Path,
    needles: set[str],
    *,
    coordination_root: Path,
    repository: str,
    repository_tasks: Path,
) -> str:
    """Decide a walked contract's role without fully parsing it.

    Returns ``"foreign"`` (skip and name), ``"unrelated"`` (skip silently),
    ``"candidate"`` (load it; a parse failure raises), or ``"unparsed"``
    (load it; a parse failure is recorded for the end-of-refresh decision:
    skipped and named past a refreshed own contract, raised when none was).
    A file that cannot be read at all counts as unparsed, per the architect
    ruling C1-01: only a refreshed own contract excuses it.

    A contract whose artifact cell names the master still matches when the
    written document is the enclosure's bound leaf: the leaf id and task
    root cells resolve through the parent row without parsing the contract
    (MIK-R111 rule 3), so a master-artifact enclosure refreshes its leaf
    while an unrelated sibling still never loads.
    """

    hint_ok, hint = _read_task_artifact_cell(contract_path)
    if not hint_ok:
        return "unparsed"
    if hint and is_foreign_enclosure_contract(contract_path, Path(hint)):
        return "foreign"
    if hint is None:
        return "unparsed"
    if hint in needles:
        return "candidate"
    if hint and _contract_binding_matches_written(
        contract_path,
        needles,
        coordination_root=coordination_root,
        repository=repository,
        repository_tasks=repository_tasks,
    ):
        return "candidate"
    return "unrelated"


def _contract_binding_matches_written(
    contract_path: Path,
    needles: set[str],
    *,
    coordination_root: Path,
    repository: str,
    repository_tasks: Path,
) -> bool:
    """True when the contract's bound leaf names a written document.

    Reads only the raw ``leaf_id`` and ``task_root`` cells -- never a full
    contract parse, so a broken file cannot raise here. The task root must
    lie inside the repository's tasks folder, and the parent-row binding
    must resolve to a written leaf; anything else is not a match.
    """
    try:
        text = contract_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    cells: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("leaf_id:", "task_root:")):
            key, _, value = stripped.partition(":")
            cells.setdefault(key, value.strip())
    leaf_id = cells.get("leaf_id")
    task_root = cells.get("task_root")
    if not leaf_id or not task_root:
        return False
    try:
        root = Path(task_root)
        if not root.resolve().is_relative_to(repository_tasks.resolve()):
            return False
        binding = resolve_leaf_task_binding(coordination_root, repository, root, leaf_id)
    except (TaskLeafBindingError, OSError, ValueError, RuntimeError):
        return False
    if binding.leaf is None:
        return False
    leaf_json = binding.leaf_json_path
    return leaf_json.as_posix() in needles or leaf_json.resolve().as_posix() in needles


def _stage_refresh_contract(
    work: _RefreshWork, contract_path: Path, contract: WorktreeContract
) -> bool:
    """Stage copy writes for one loaded contract; True when they were staged.

    A foreign contract is appended to the work's skip list instead. A loaded
    sibling or a gone worktree is skipped silently. Candidates are the
    enclosure's bound document -- the exact leaf resolved through the
    parent row, or the master artifact only when no leaf binding exists
    (MIK-R111 rule 3); their writes merge with removals reconciled, and
    the transactional publisher restores every file on failure (F01.7).
    """

    if is_foreign_enclosure_contract(contract_path, contract.task_artifact):
        work.skipped_foreign.append(contract_path)
        return False
    group = contract.worktree_group
    if not group.is_dir():
        return False
    docs = []
    for leaf_path in sorted(_refresh_candidates(work.coordination_root, work.repository, contract)):
        try:
            relative = leaf_path.relative_to(work.repository_tasks).as_posix()
        except ValueError:
            continue
        docs.append((leaf_path, TaskDocumentRef(repository=work.repository, path=relative)))
    if not any(leaf_path.resolve() in work.changed for leaf_path, _ref in docs):
        return False
    root = group / TASK_DOCUMENTS_DIRECTORY
    for worktree in (contract.code_worktree, contract.memory_worktree):
        if worktree is not None and root.resolve().is_relative_to(worktree.resolve()):
            raise ValueError(f"Task document copies must be outside Git worktrees: {root}")
    group_writes, _rows, group_removals = _union_copy_writes(
        work.coordination_root, group, docs, work.changed
    )
    work.writes.update(group_writes)
    work.removals.extend(group_removals)
    return True


def _log_refresh_skips(
    skipped_foreign: list[Path],
    unrecognized: list[tuple[Path, Exception]],
    repository_tasks: Path,
) -> None:
    if unrecognized:
        logger.warning(
            "refresh_task_document_copies: skipping %d enclosure contract(s) that "
            "name no task document and failed to parse: %s",
            len(unrecognized),
            ", ".join(path.as_posix() for path, _error in unrecognized),
        )
    if skipped_foreign:
        logger.warning(
            "refresh_task_document_copies: skipping %d enclosure contract(s) whose task "
            "document lies outside %s: %s",
            len(skipped_foreign),
            repository_tasks.as_posix(),
            ", ".join(path.as_posix() for path in skipped_foreign),
        )


def _read_task_artifact_cell(contract_path: Path) -> tuple[bool, str | None]:
    """Read one contract's raw ``task_artifact`` cell without parsing it.

    Returns ``(True, value)`` when the file is readable and carries the cell,
    ``(True, None)`` when it is readable but carries no such cell (not an
    enclosure of any task), and ``(False, None)`` when it cannot be read at
    all (the caller treats it as unparsed: named, and raised unless an own
    contract refreshed).
    """

    try:
        text = contract_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return (False, None)
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("task_artifact:"):
            return (True, stripped.split(":", 1)[1].strip())
    return (True, None)


def _require_real_destination(path: Path, root: Path) -> None:
    if not path.is_relative_to(root):
        raise ValueError(f"Task document copy escapes its working folder: {path}")
    current = path
    while current.is_relative_to(root):
        if current.is_symlink():
            raise ValueError(f"Task document copies require real files/directories: {current}")
        if current == root:
            break
        current = current.parent
