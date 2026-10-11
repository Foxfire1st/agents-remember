"""Rendered, read-only leaf documents inside the enclosure, outside its Git trees."""

from __future__ import annotations

import hashlib
import json
import logging
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
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

TASK_DOCUMENTS_DIRECTORY = "task-documents"

logger = logging.getLogger(__name__)


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
    index = root / "INDEX.json"
    _require_real_destination(index, root)
    writes[index] = (
        json.dumps({"schema": "ar-task-documents/v1", "documents": rows}, indent=2) + "\n"
    ).encode()
    return writes, rows


def _publish_copy_writes(writes: dict[Path, bytes]) -> None:
    originals = {path: path.read_bytes() if path.exists() else None for path in writes}
    try:
        for path, raw in writes.items():
            atomic_write_bytes(path, raw)
    except BaseException as publish_error:
        try:
            for path, raw in originals.items():
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic_write_bytes(path, raw)
        except BaseException as rollback_error:
            raise RuntimeError(
                f"task document copy publication and rollback both failed: {rollback_error}"
            ) from publish_error
        raise


def refresh_task_document_copies(
    coordination_root: Path, repository: str, written: list[tuple[Path, Path]]
) -> None:
    """A written task document refreshes its own copies and nothing else.

    Only the enclosures of the task folders holding a written document are
    walked. A contract is fully loaded only once its raw ``task_artifact``
    cell names a written document, or when it carries no such cell at all:
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
            selection = _select_refresh_contract(contract_path, needles)
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
    _publish_copy_writes(work.writes)


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

    def finish(self) -> None:
        """Fail closed when nothing own refreshed past unparseable files."""
        if self.unrecognized and not self.matched_own:
            raise self.unrecognized[0][1]
        _log_refresh_skips(self.skipped_foreign, self.unrecognized, self.repository_tasks)


def _select_refresh_contract(contract_path: Path, needles: set[str]) -> str:
    """Decide a walked contract's role without fully parsing it.

    Returns ``"foreign"`` (skip and name), ``"unrelated"`` (skip silently),
    ``"candidate"`` (load it; a parse failure raises), or ``"unparsed"``
    (load it; a parse failure is recorded for the end-of-refresh decision:
    skipped and named past a refreshed own contract, raised when none was).
    A file that cannot be read at all counts as unparsed, per the architect
    ruling C1-01: only a refreshed own contract excuses it.
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
    return "unrelated"


def _stage_refresh_contract(
    work: _RefreshWork, contract_path: Path, contract: WorktreeContract
) -> bool:
    """Stage copy writes for one loaded contract; True when they were staged.

    A foreign contract is appended to the work's skip list instead. A loaded
    sibling or a gone worktree is skipped silently.
    """

    if is_foreign_enclosure_contract(contract_path, contract.task_artifact):
        work.skipped_foreign.append(contract_path)
        return False
    group = contract.worktree_group
    if not group.is_dir():
        return False
    leaf_path = contract.task_artifact.with_suffix(".json")
    leaf = TaskDocumentRef(
        repository=work.repository,
        path=leaf_path.relative_to(work.repository_tasks).as_posix(),
    )
    if leaf_path.resolve() not in work.changed:
        return False
    root = group / TASK_DOCUMENTS_DIRECTORY
    for worktree in (contract.code_worktree, contract.memory_worktree):
        if worktree is not None and root.resolve().is_relative_to(worktree.resolve()):
            raise ValueError(f"Task document copies must be outside Git worktrees: {root}")
    group_writes, _rows = _copy_writes(work.coordination_root, group, leaf)
    work.writes.update(group_writes)
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
