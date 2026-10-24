"""Building, updating and removing one leaf's explainer environment (MIK-R186 rules 1, 2, 6, 8).

The environment is the explainer's whole world: a read-only copy of the two bound candidate trees,
the evidence packet and the grants, the capsule it was started with, and scratch and draft folders
it may write. This module owns the bytes:

* **build** writes the workspace, a fresh snapshot, ``instructions/`` and -- last of all --
  ``binding.json``. A failure before the last step leaves no binding, so nothing claims an
  environment that is not there;
* **update** writes exactly the same things for the next binding, binds it last, and then removes
  the snapshot it replaced. A turn that spanned the swap may read either snapshot; every answer
  names the binding it was made at (:mod:`agents_remember.canvas.protocol`), so a grade or a
  publish made at the old number is refused rather than silently relabelled;
* **retire** records the seat's process tree *before* it archives anything, ends every process by
  both counts, and removes the workspace while leaving a ``retired.json`` marker and a receipt in
  the task's records.

Nothing here talks to the host, reads a comparison or prepares a packet: the caller hands those in,
so the same code is exercised without a host. Every write and every removal is admitted against the
task's explainer folder first, and a symlinked component is held and reported rather than followed
(MIK-R186 rule 2).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_paths import (
    DRAFTS_DIRECTORY,
    INSTRUCTIONS_DIRECTORY,
    SCRATCH_DIRECTORY,
    cleanup_workspace_tree,
    ensure_leaf_workspace,
    first_nested_symlink,
    guard,
    guard_explainer_path,
    leaf_workspace,
    make_writable_tree,
    receipt_id_segment,
    remove_workspace_contents,
    snapshot_folder,
)
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    ExplainerRefused,
    ExplainerStateError,
    PlannedIdentity,
    RetireReceipt,
    agent_id,
    clear_retired,
    end_processes,
    find_pending_receipt,
    mark_retired,
    process_tree,
    read_binding,
    same_binding,
    write_binding,
    write_receipt,
)
from agents_remember.canvas.paths import PathRefused
from agents_remember.kernel.atomic_write import atomic_write_bytes, atomic_write_text
from agents_remember.kernel.git_command import read_git_blobs_bytes, run_git_bytes
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import FrameBinding

__all__ = [
    "EnvironmentBuild",
    "EnvironmentInputs",
    "EnvironmentRef",
    "EnvironmentRun",
    "EnvironmentUpdate",
    "RetireOutcome",
    "RetirePlace",
    "RetireRequest",
    "RetireStart",
    "SnapshotTotals",
    "SnapshotWrite",
    "build_environment",
    "count_tree",
    "next_snapshot_number",
    "plan_retire",
    "remove_snapshot",
    "remove_workspace_contents",
    "resume_retire",
    "retire_environment",
    "retire_workspace",
    "settle_retire",
    "update_environment",
    "write_snapshot",
]

SNAPSHOT_MANIFEST = ".snapshot.json"
ASSIGNMENT_NAME = "assignment.txt"
ASSIGNMENT_MD_NAME = "assignment.md"
EVIDENCE_NAME = "evidence.json"
GRANTS_NAME = "grants.json"
MAX_SNAPSHOT_FILE_BYTES = 256 * 1024 * 1024
MAX_SNAPSHOT_BATCH_BYTES = 4 * 1024 * 1024


class SnapshotTooLarge(ExplainerRefused):
    """One file is beyond the admitted size; no byte of it was written."""


@dataclass(frozen=True)
class SnapshotTotals:
    """What one snapshot write did, per tree, as counts a page can show (rule 10)."""

    folder: str
    files: int = 0
    written: int = 0
    linked: int = 0
    removed: int = 0
    bytes: int = 0
    symlinks: int = 0

    def as_record(self) -> dict[str, Any]:
        return {
            "folder": self.folder,
            "files": self.files,
            "written": self.written,
            "linked": self.linked,
            "removed": self.removed,
            "bytes": self.bytes,
            "symlinks": self.symlinks,
        }


@dataclass(frozen=True)
class EnvironmentRef:
    """Which leaf's environment: the runtime, its repository, its task and the leaf."""

    config: McpRuntimeConfig
    repository_id: str
    task: str
    leaf_id: str


@dataclass(frozen=True)
class EnvironmentRun:
    """One build or Update: what to write, when, and who wants to watch it happen."""

    ref: EnvironmentRef
    inputs: EnvironmentInputs
    now: str
    previous: ExplainerBinding | None = None
    preparation: Callable[[Path], tuple[EvidencePacket, bytes]] | None = None
    progress: Callable[[str, Mapping[str, Any]], None] | None = None
    in_turn: Callable[[], bool] | None = None


@dataclass(frozen=True)
class RetireRequest:
    """One Retire: which environment, which receipt, and how to archive its seat."""

    ref: EnvironmentRef
    receipts_root: Path
    receipt_id: str
    generation: int
    now: str
    archive_session: Callable[[str], None] | None = None


@dataclass(frozen=True)
class RetirePlace:
    """Where one environment lives, and how a Retire of it is carried out (rule 8).

    It names the workspace and its receipt folder rather than a runtime configuration, so the
    archive hook can retire an environment it has only a folder for.
    """

    workspace: Path
    receipts_root: Path
    now: str
    archive_session: Callable[[str], None] | None = None


@dataclass(frozen=True)
class RetireStart:
    """A Retire that has not begun: the place, the seat's generation and the receipt to write."""

    place: RetirePlace
    leaf_id: str
    generation: int
    receipt_id: str


@dataclass(frozen=True)
class EnvironmentInputs:
    """Everything one build or Update writes, already prepared by its caller."""

    binding: FrameBinding
    packet: EvidencePacket
    capsule: str
    code_repository: Path
    memory_repository: Path
    planned: PlannedIdentity | None = None
    record_digest: str | None = None
    design_notice: str | None = None


@dataclass(frozen=True)
class EnvironmentBuild:
    """One build's answer: the binding it made, its snapshot and the steps it took."""

    workspace: Path
    binding: ExplainerBinding
    snapshot: SnapshotTotals
    steps: tuple[str, ...] = ()
    code: SnapshotTotals | None = None
    memory: SnapshotTotals | None = None
    built: bool = True

    def as_record(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "binding": self.binding.model_dump(by_alias=True),
            "snapshot": self.snapshot.as_record(),
            "steps": list(self.steps),
            "code": None if self.code is None else self.code.as_record(),
            "memory": None if self.memory is None else self.memory.as_record(),
            "built": self.built,
        }


@dataclass(frozen=True)
class EnvironmentUpdate:
    """One Update's answer: what changed, per tree, and the binding it now serves."""

    workspace: Path
    binding: ExplainerBinding
    previous_number: int | None
    current: bool
    snapshot: SnapshotTotals | None = None
    code: SnapshotTotals | None = None
    memory: SnapshotTotals | None = None
    steps: tuple[str, ...] = ()
    #: The review blocks the comparison moved between n and n+1 (rule 6 step 3).
    blocks_changed: int | None = None
    #: The page's parts and the leaf's grades that read stale against the new binding (rule 6).
    stale_parts: int | None = 0
    stale_grades: int | None = 0
    removed_grades: int = 0
    counts_unavailable: str | None = None

    def counts(self) -> dict[str, Any]:
        """The counts the Update press shows, by the names the page reads (rule 6)."""

        files = sum(one.files for one in (self.code, self.memory) if one is not None)
        bytes_written = sum(one.bytes for one in (self.code, self.memory) if one is not None)
        counts: dict[str, Any] = {
            "files": files,
            "bytes": bytes_written,
            "parts": self.stale_parts,
            "grades": self.stale_grades,
            "removedGrades": self.removed_grades,
        }
        if self.blocks_changed is not None:
            counts["blocks"] = self.blocks_changed
        if self.counts_unavailable is not None:
            counts["unavailable"] = self.counts_unavailable
        return counts

    def as_record(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "binding": self.binding.model_dump(by_alias=True),
            "previousNumber": self.previous_number,
            "current": self.current,
            "snapshot": None if self.snapshot is None else self.snapshot.as_record(),
            "code": None if self.code is None else self.code.as_record(),
            "memory": None if self.memory is None else self.memory.as_record(),
            "steps": list(self.steps),
            "counts": self.counts(),
        }


@dataclass(frozen=True)
class RetireOutcome:
    """One Retire's answer: what ended, what stayed and what could not be finished."""

    receipt: RetireReceipt
    workspace_removed: bool
    held: tuple[str, ...] = ()
    detail: str = ""
    removed_grades: int = 0

    def as_record(self) -> dict[str, Any]:
        receipt_data = self.receipt.model_dump(mode="json")
        receipt_data["measuredMemoryBytes"] = self.receipt.measured_memory_bytes
        return {
            "receipt": receipt_data,
            "workspaceRemoved": self.workspace_removed,
            "held": list(self.held),
            "detail": self.detail,
            "removedGrades": self.removed_grades,
        }


@dataclass
class _Progress:
    """Collect the build's named steps, and tell a caller as each begins or ends."""

    sink: Callable[[str, Mapping[str, Any]], None] | None = None
    steps: list[str] = field(default_factory=list)

    def step(self, name: str, **facts: Any) -> None:
        self.steps.append(name)
        if self.sink is not None:
            self.sink(name, facts)


def count_tree(repository: Path, tree: str) -> int:
    """How many blobs one tree holds, by ``git ls-tree -r`` (rule 11's own measure)."""

    return len(_tree_entries(repository, tree))


def _tree_entries(repository: Path, tree: str) -> list[tuple[str, str, int, str]]:
    """``(path, blob, size, mode)`` for every blob of one tree, NUL-delimited (MIK-R186 rule 2)."""

    result = run_git_bytes(repository, ["ls-tree", "-r", "-l", "-z", tree])
    if result.returncode:
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"the tree {tree} of {repository} cannot be listed: {stderr}",
        )
    entries: list[tuple[str, str, int, str]] = []
    for raw_record in result.stdout.split(b"\x00"):
        if not raw_record:
            continue
        meta_bytes, sep, path_bytes = raw_record.partition(b"\t")
        if not sep:
            continue
        parts = meta_bytes.decode("ascii", errors="replace").split()
        if len(parts) < 4:
            continue
        mode, kind, blob, size = parts[0], parts[1], parts[2], parts[3]
        if kind != "blob":
            continue
        path = path_bytes.decode("utf-8", errors="surrogateescape")
        if mode == "120000":
            entries.append((path, blob, -1, mode))
            continue
        entries.append((path, blob, int(size) if size.isdigit() else 0, mode))
    return entries


def _safe_bound_snapshot_number(workspace: Path) -> int | None:
    with suppress(ExplainerStateError):
        bound = read_binding(workspace)
        if bound is not None and bound.snapshot.startswith("snapshot-"):
            suffix = bound.snapshot.removeprefix("snapshot-")
            if suffix.isdigit():
                return int(suffix)
    return None


def next_snapshot_number(workspace: Path) -> int:
    """One past the highest bound ``snapshot-<n>``, after reclaiming interrupted orphans."""

    if not workspace.is_dir():
        return 1
    bound_number = _safe_bound_snapshot_number(workspace)
    highest = 0
    for child in sorted(workspace.iterdir(), key=lambda item: item.name):
        name = child.name
        if not name.startswith("snapshot-"):
            continue
        suffix = name.removeprefix("snapshot-")
        if not suffix.isdigit():
            continue
        num = int(suffix)
        if bound_number is None or num > bound_number:
            with suppress(PathRefused, OSError):
                remove_snapshot(child, root=workspace.parent)
            continue
        highest = max(highest, num)
    return highest + 1


@dataclass(frozen=True)
class SnapshotWrite:
    """One tree to write into one snapshot folder, and what to link from."""

    repository: Path
    tree: str
    destination: Path
    root: Path
    previous: Path | None = None
    progress: Callable[[int, int], None] | None = None


def write_snapshot(request: SnapshotWrite) -> SnapshotTotals:
    """Write one tree's blobs below ``destination``, hard-linking unchanged files from ``previous``."""

    repository, tree, destination = request.repository, request.tree, request.destination
    root, previous = request.root, request.previous
    entries = _tree_entries(repository, tree)
    if not entries:
        raise ExplainerRefused(
            "evidence-source-unavailable", f"the tree {tree} of {repository} holds no file"
        )
    for relative, _blob, size, _mode in entries:
        if size > MAX_SNAPSHOT_FILE_BYTES:
            raise SnapshotTooLarge(
                "evidence-source-unavailable",
                f"{relative} is {size} bytes, beyond the {MAX_SNAPSHOT_FILE_BYTES}-byte ceiling",
            )
    guard_explainer_path(destination, root=root, label="snapshot folder")
    if destination.exists():
        _empty_directory(destination, root=root)
    destination.mkdir(parents=True, exist_ok=True)
    guard_explainer_path(destination, root=root, label="snapshot folder")
    previous_index = _read_snapshot_manifest(previous)
    written = _Written()
    manifest: dict[str, dict[str, Any]] = {}
    _write_snapshot_batches(
        request=request,
        entries=entries,
        previous_index=previous_index,
        written=written,
        manifest=manifest,
    )
    manifest_target = destination / SNAPSHOT_MANIFEST
    guard_explainer_path(manifest_target, root=root, label="snapshot manifest")
    if manifest_target.exists():
        manifest_target.chmod(0o600)
    atomic_write_text(
        manifest_target,
        json.dumps({"tree": tree, "files": manifest}, sort_keys=True) + "\n",
    )
    manifest_target.chmod(0o444)
    removed = len(set(previous_index) - set(manifest)) if previous_index else 0
    return SnapshotTotals(
        folder=destination.name,
        files=len(manifest),
        written=written.written,
        linked=written.linked,
        removed=removed,
        bytes=written.total_bytes,
        symlinks=written.symlinks,
    )


def _write_snapshot_batches(
    *,
    request: SnapshotWrite,
    entries: list[tuple[str, str, int, str]],
    previous_index: dict[str, dict[str, Any]],
    written: _Written,
    manifest: dict[str, dict[str, Any]],
) -> None:
    total = len(entries)
    batch: list[tuple[int, str, str, int, str, Path | None]] = []
    batch_bytes = 0
    for index, (relative, blob, size, mode) in enumerate(entries, start=1):
        if size < 0:
            written.symlinks += 1
            if request.progress is not None and (index % 200 == 0 or index == total):
                request.progress(index, total)
            continue
        source = _linkable(request.previous, previous_index, relative, blob, size)
        batch.append((index, relative, blob, size, mode, source))
        if source is None:
            batch_bytes += max(1, size)
        if batch_bytes >= MAX_SNAPSHOT_BATCH_BYTES or index == total:
            _flush_snapshot_batch(request, batch, total, written, manifest)
            batch = []
            batch_bytes = 0
    if batch:
        _flush_snapshot_batch(request, batch, total, written, manifest)


def _flush_snapshot_batch(
    request: SnapshotWrite,
    batch: list[tuple[int, str, str, int, str, Path | None]],
    total: int,
    written: _Written,
    manifest: dict[str, dict[str, Any]],
) -> None:
    needed_blobs = [blob for _idx, _rel, blob, _sz, _mode, src in batch if src is None]
    blobs = read_git_blobs_bytes(request.repository, needed_blobs) if needed_blobs else {}
    for index, relative, blob, size, mode, source in batch:
        target = request.destination / relative
        if source is not None and _place_linked(source, target, root=request.root, mode=mode):
            written.linked += 1
            written.total_bytes += size
            manifest[relative] = {"blob": blob, "bytes": size}
        else:
            payload = _payload(blobs, relative, blob, size, request.tree)
            _place_written(target, payload, root=request.root, mode=mode)
            written.written += 1
            written.total_bytes += len(payload)
            manifest[relative] = {"blob": blob, "bytes": len(payload)}
        if request.progress is not None and (index % 200 == 0 or index == total):
            request.progress(index, total)


@dataclass
class _Written:
    """The running counts of one snapshot write."""

    written: int = 0
    linked: int = 0
    total_bytes: int = 0
    symlinks: int = 0


def _payload(blobs: dict[str, bytes], relative: str, blob: str, size: int, tree: str) -> bytes:
    """One file's admitted bytes, refusing a blob the tree's own size contradicts."""

    if size > MAX_SNAPSHOT_FILE_BYTES:
        raise SnapshotTooLarge(
            "evidence-source-unavailable",
            f"{relative} is {size} bytes, beyond the {MAX_SNAPSHOT_FILE_BYTES}-byte ceiling",
        )
    payload = blobs.get(blob)
    if payload is None:
        raise ExplainerRefused(
            "evidence-source-unavailable", f"the blob of {relative} at {tree} could not be read"
        )
    if len(payload) != size:
        raise ExplainerRefused(
            "evidence-source-unavailable",
            f"{relative} reads {len(payload)} bytes where the tree says {size}",
        )
    return payload


def _linkable(
    previous: Path | None,
    index: dict[str, dict[str, Any]],
    relative: str,
    blob: str,
    size: int,
) -> Path | None:
    """The previous snapshot's file for this path when it holds the same blob, else ``None``."""

    if previous is None or index.get(relative, {}).get("blob") != blob:
        return None
    candidate = previous / relative
    if candidate.is_file() and not candidate.is_symlink():
        with suppress(OSError):
            if candidate.stat().st_size == size:
                return candidate
    return None


def _readonly_mode(mode: str) -> int:
    return 0o555 if mode == "100755" else 0o444


def _place_linked(source: Path, target: Path, *, root: Path, mode: str) -> bool:
    file_mode = _readonly_mode(mode)
    guard_explainer_path(target, root=root, label="snapshot file")
    target.parent.mkdir(parents=True, exist_ok=True)
    guard_explainer_path(target, root=root, label="snapshot file")
    try:
        st_mode = source.lstat().st_mode
        if not stat.S_ISREG(st_mode):
            return False
        if (st_mode & 0o222) != 0:
            os.chmod(source, file_mode)
        if target.exists() or target.is_symlink():
            target.unlink()
        os.link(source, target)
        if (target.lstat().st_mode & 0o222) != 0:
            os.chmod(target, file_mode)
        return True
    except OSError:
        return False


def _place_written(target: Path, payload: bytes, *, root: Path, mode: str) -> None:
    file_mode = _readonly_mode(mode)
    guard_explainer_path(target, root=root, label="snapshot file")
    target.parent.mkdir(parents=True, exist_ok=True)
    guard_explainer_path(target, root=root, label="snapshot file")
    if target.exists() and not target.is_symlink():
        with suppress(OSError):
            target.chmod(0o600)
    atomic_write_bytes(target, payload)
    os.chmod(target, file_mode)


def _read_snapshot_manifest(folder: Path | None) -> dict[str, dict[str, Any]]:
    if folder is None:
        return {}
    manifest = folder / SNAPSHOT_MANIFEST
    if not manifest.is_file():
        return {}
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return {}
    files = payload.get("files")
    return files if isinstance(files, dict) else {}


def _empty_directory(directory: Path, *, root: Path) -> None:
    """Remove a snapshot folder's contents, refusing to follow a symlink inside it."""

    for child in directory.iterdir():
        guard(root, child, label="snapshot entry")
        if child.is_symlink():
            raise PathRefused(f"the snapshot holds the symlink {child}; it is held, not followed")
        if child.is_dir():
            nested = first_nested_symlink(child)
            if nested is not None:
                raise PathRefused(
                    f"the snapshot holds the symlink {nested}; it is held, not followed"
                )
            make_writable_tree(child)
            shutil.rmtree(child)
        else:
            with suppress(OSError):
                child.chmod(0o600)
            child.unlink()


def remove_snapshot(folder: Path, *, root: Path) -> bool:
    """Remove one old snapshot; a symlinked one is held rather than followed."""

    guard(root, folder, label="snapshot folder")
    if not folder.exists() and not folder.is_symlink():
        return False
    if folder.is_symlink():
        raise PathRefused(f"the snapshot {folder} is a symlink; it is held, not followed")
    nested = first_nested_symlink(folder)
    if nested is not None:
        raise PathRefused(f"the snapshot holds the symlink {nested}; it is held, not followed")
    make_writable_tree(folder)
    shutil.rmtree(folder)
    return True


def build_environment(run: EnvironmentRun) -> EnvironmentBuild:
    """Build one leaf's environment, then bind it last (rules 1, 2, 4, 10)."""

    reporter = _Progress(run.progress)
    ref, inputs = run.ref, run.inputs
    workspace = ensure_leaf_workspace(ref.config, ref.repository_id, ref.task, ref.leaf_id)
    root = workspace.parent
    reporter.step("workspace", path=str(workspace))
    clear_retired(workspace)
    try:
        number = next_snapshot_number(workspace)
        destination = snapshot_folder(workspace, number)
        guard_explainer_path(destination, root=root, label="snapshot folder")
        destination.mkdir()
        reporter.step("binding", owner=inputs.binding.comparison_owner, number=number)

        def code_progress(done: int, total: int) -> None:
            reporter.step("snapshot-code", done=done, total=total)

        code = write_snapshot(
            SnapshotWrite(
                repository=inputs.code_repository,
                tree=inputs.binding.code_candidate_tree,
                destination=destination / "code",
                root=root,
                progress=code_progress,
            )
        )
        reporter.step("snapshot-code", done=code.files, total=code.files)
        memory = write_snapshot(
            SnapshotWrite(
                repository=inputs.memory_repository,
                tree=inputs.binding.memory_candidate_tree,
                destination=destination / "memory",
                root=root,
            )
        )
        reporter.step("snapshot-memory", done=memory.files, total=memory.files)
        packet_bytes = _stage_and_commit_instructions(
            workspace=workspace,
            run=run,
            snapshot_name=destination.name,
            reporter=reporter,
        )
        binding = ExplainerBinding(
            binding=inputs.binding,
            planned=inputs.planned,
            snapshot=destination.name,
            prepared_at=run.now,
            packet_digest=_digest(packet_bytes),
            record_digest=inputs.record_digest,
        )
        write_binding(workspace, binding)
        reporter.step("bound", snapshot=destination.name)
    except BaseException:
        cleanup_workspace_tree(workspace, root=root)
        raise
    return EnvironmentBuild(
        workspace=workspace,
        binding=binding,
        snapshot=code,
        steps=tuple(reporter.steps),
        code=code,
        memory=memory,
    )


def update_environment(run: EnvironmentRun) -> EnvironmentUpdate:
    """Rebind one environment to the next comparison, writing the new binding last (rule 6)."""

    ref, inputs = run.ref, run.inputs
    previous = run.previous
    if previous is None:
        raise ExplainerRefused("explainer-missing", "an Update needs the binding it replaces")
    reporter = _Progress(run.progress)
    workspace = leaf_workspace(ref.config, ref.repository_id, ref.task, ref.leaf_id)
    root = workspace.parent
    if not workspace.is_dir():
        raise ExplainerRefused(
            "explainer-missing", f"the leaf {ref.leaf_id} has no environment at {workspace}"
        )
    if same_binding(previous.binding, inputs.binding):
        reporter.step("current", number=previous.binding.comparison_number)
        return EnvironmentUpdate(
            workspace=workspace,
            binding=previous,
            previous_number=previous.binding.comparison_number,
            current=True,
            steps=tuple(reporter.steps),
        )
    old_snapshot = workspace / previous.snapshot
    number = next_snapshot_number(workspace)
    destination = snapshot_folder(workspace, number)
    guard_explainer_path(destination, root=root, label="snapshot folder")
    destination.mkdir()
    reporter.step("snapshot", number=number)
    try:
        code = write_snapshot(
            SnapshotWrite(
                repository=inputs.code_repository,
                tree=inputs.binding.code_candidate_tree,
                destination=destination / "code",
                root=root,
                previous=old_snapshot / "code",
            )
        )
        memory = write_snapshot(
            SnapshotWrite(
                repository=inputs.memory_repository,
                tree=inputs.binding.memory_candidate_tree,
                destination=destination / "memory",
                root=root,
                previous=old_snapshot / "memory",
            )
        )
        reporter.step("snapshot", code=code.as_record(), memory=memory.as_record())
        packet_bytes = _stage_and_commit_instructions(
            workspace=workspace,
            run=run,
            snapshot_name=destination.name,
            reporter=None,
            bind_with=lambda digest: write_binding(
                workspace,
                ExplainerBinding(
                    binding=inputs.binding,
                    planned=inputs.planned,
                    snapshot=destination.name,
                    prepared_at=run.now,
                    packet_digest=digest,
                    record_digest=inputs.record_digest,
                ),
            ),
        )
        reporter.step("instructions")
        binding = ExplainerBinding(
            binding=inputs.binding,
            planned=inputs.planned,
            snapshot=destination.name,
            prepared_at=run.now,
            packet_digest=_digest(packet_bytes),
            record_digest=inputs.record_digest,
        )
        reporter.step("bound", snapshot=destination.name)
    except BaseException:
        with suppress(PathRefused, OSError):
            remove_snapshot(destination, root=root)
        raise
    _reclaim_replaced_snapshots(workspace, destination, old_snapshot, run.in_turn, reporter)
    return EnvironmentUpdate(
        workspace=workspace,
        binding=binding,
        previous_number=previous.binding.comparison_number,
        current=False,
        snapshot=code,
        code=code,
        memory=memory,
        steps=tuple(reporter.steps),
    )


def _reclaim_replaced_snapshots(
    workspace: Path,
    destination: Path,
    old_snapshot: Path,
    in_turn: Callable[[], bool] | None,
    reporter: _Progress,
) -> None:
    root = workspace.parent
    turn_active = False
    if in_turn is not None:
        try:
            turn_active = bool(in_turn())
        except Exception:
            turn_active = True
    if turn_active:
        if old_snapshot.exists():
            reporter.step("snapshot-kept-in-turn", folder=old_snapshot.name)
        return
    for child in sorted(workspace.iterdir(), key=lambda item: item.name):
        if child == destination or not child.name.startswith("snapshot-"):
            continue
        suffix = child.name.removeprefix("snapshot-")
        if not suffix.isdigit():
            continue
        try:
            remove_snapshot(child, root=root)
            reporter.step("snapshot-removed", folder=child.name)
        except PathRefused as held:
            reporter.step("snapshot-held", detail=str(held))


def _backup_live_instructions(live_folder: Path, root: Path) -> dict[str, bytes]:
    guard_explainer_path(live_folder, root=root, label="explainer instructions")
    for name in (EVIDENCE_NAME, ASSIGNMENT_NAME, ASSIGNMENT_MD_NAME, GRANTS_NAME):
        guard_explainer_path(live_folder / name, root=root, label=f"explainer {name}")
    backup: dict[str, bytes] = {}
    if live_folder.is_dir() and not live_folder.is_symlink():
        for name in (EVIDENCE_NAME, ASSIGNMENT_NAME, ASSIGNMENT_MD_NAME, GRANTS_NAME):
            candidate = live_folder / name
            if candidate.is_file() and not candidate.is_symlink():
                backup[name] = candidate.read_bytes()
    return backup


def _restore_live_instructions(live_folder: Path, root: Path, backup: dict[str, bytes]) -> None:
    if not backup or not live_folder.is_dir() or live_folder.is_symlink():
        return
    for name, raw in backup.items():
        with suppress(PathRefused, OSError):
            target = live_folder / name
            guard_explainer_path(target, root=root, label=f"explainer {name}")
            atomic_write_bytes(target, raw)


def _stage_and_commit_instructions(
    *,
    workspace: Path,
    run: EnvironmentRun,
    snapshot_name: str,
    reporter: _Progress | None,
    bind_with: Callable[[str], None] | None = None,
) -> bytes:
    """Stage evidence, assignment and grants, then commit them coherently with ``binding.json``."""

    root = workspace.parent
    live_folder = workspace / INSTRUCTIONS_DIRECTORY
    backup = _backup_live_instructions(live_folder, root)
    staging = workspace / f".instructions-{snapshot_name}"
    guard_explainer_path(staging, root=root, label="explainer staged instructions")
    if staging.exists():
        _empty_directory(staging, root=root)
        staging.rmdir()
    staging.mkdir(exist_ok=True)
    try:
        _packet, packet_bytes = _prepare_instructions(
            workspace, run.inputs, run.preparation, folder=staging
        )
        if reporter is not None:
            reporter.step("packet", digest=_digest(packet_bytes))
        _write_capsule(
            workspace,
            run.inputs,
            folder=staging,
            snapshot_name=snapshot_name,
            packet_digest=_digest(packet_bytes),
        )
        if reporter is not None:
            reporter.step("instructions")
        live_folder.mkdir(exist_ok=True)
        guard_explainer_path(live_folder, root=root, label="explainer instructions")
        for item in sorted(staging.iterdir(), key=lambda entry: entry.name):
            target = live_folder / item.name
            guard_explainer_path(target, root=root, label=f"explainer {item.name}")
            atomic_write_bytes(target, item.read_bytes())
        if bind_with is not None:
            bind_with(_digest(packet_bytes))
    except BaseException:
        _restore_live_instructions(live_folder, root, backup)
        raise
    finally:
        if staging.exists() and not staging.is_symlink():
            with suppress(PathRefused, OSError):
                _empty_directory(staging, root=root)
                staging.rmdir()
    return packet_bytes


def _prepare_instructions(
    workspace: Path,
    inputs: EnvironmentInputs,
    preparation: Callable[[Path], tuple[EvidencePacket, bytes]] | None,
    *,
    folder: Path | None = None,
) -> tuple[EvidencePacket, bytes]:
    target_folder = workspace / INSTRUCTIONS_DIRECTORY if folder is None else folder
    guard_explainer_path(target_folder, root=workspace.parent, label="explainer instructions")
    target_folder.mkdir(exist_ok=True)
    guard_explainer_path(target_folder, root=workspace.parent, label="explainer instructions")
    target = target_folder / EVIDENCE_NAME
    guard_explainer_path(target, root=workspace.parent, label="explainer evidence")
    if preparation is not None:
        packet, payload = preparation(target_folder)
        guard_explainer_path(target, root=workspace.parent, label="explainer evidence")
        if not target.is_file():
            atomic_write_bytes(target, payload)
        return packet, payload
    payload = json.dumps(inputs.packet.model_dump(by_alias=True), indent=2).encode()
    atomic_write_bytes(target, payload)
    return inputs.packet, payload


def _write_capsule(
    workspace: Path,
    inputs: EnvironmentInputs,
    *,
    folder: Path | None = None,
    snapshot_name: str | None = None,
    packet_digest: str | None = None,
) -> None:
    """Write the capsule, the evidence and the grants the tool server re-reads on each call."""

    target_folder = workspace / INSTRUCTIONS_DIRECTORY if folder is None else folder
    guard_explainer_path(target_folder, root=workspace.parent, label="explainer instructions")
    target_folder.mkdir(exist_ok=True)
    guard_explainer_path(target_folder, root=workspace.parent, label="explainer instructions")
    assignment_target = target_folder / ASSIGNMENT_NAME
    assignment_md_target = target_folder / ASSIGNMENT_MD_NAME
    grants_target = target_folder / GRANTS_NAME
    for path in (assignment_target, assignment_md_target, grants_target):
        guard_explainer_path(path, root=workspace.parent, label="explainer instructions file")
    atomic_write_text(assignment_target, inputs.capsule)
    atomic_write_text(assignment_md_target, inputs.capsule)
    chosen_snapshot = snapshot_name if snapshot_name is not None else _snapshot_name(workspace)
    grants: dict[str, Any] = {
        "schema": "ar-explainer-grants/v1",
        "workspace": str(workspace),
        "binding": inputs.binding.model_dump(mode="json"),
        "evidence": EVIDENCE_NAME,
        "assignment": ASSIGNMENT_NAME,
        "snapshot": chosen_snapshot,
        "designNotice": inputs.design_notice,
        "drafts": DRAFTS_DIRECTORY,
        "scratch": SCRATCH_DIRECTORY,
    }
    if packet_digest is not None:
        grants["evidenceDigest"] = packet_digest
        grants["assignmentDigest"] = _digest(inputs.capsule.encode("utf-8"))
    atomic_write_text(grants_target, json.dumps(grants, indent=2) + "\n")


def _snapshot_name(workspace: Path) -> str | None:
    highest: tuple[int, str] | None = None
    for child in workspace.iterdir():
        if not child.is_dir() or not child.name.startswith("snapshot-"):
            continue
        suffix = child.name.removeprefix("snapshot-")
        if suffix.isdigit() and (highest is None or int(suffix) > highest[0]):
            highest = (int(suffix), child.name)
    return None if highest is None else highest[1]


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def plan_retire(ref: EnvironmentRef, *, generation: int) -> dict[str, Any]:
    """What a retire would do, without touching a seat, a process or a file (rule 9)."""

    workspace = leaf_workspace(ref.config, ref.repository_id, ref.task, ref.leaf_id)
    return {
        "leaf": ref.leaf_id,
        "workspace": str(workspace),
        "exists": workspace.is_dir(),
        "agentId": agent_id(workspace, generation),
        "generation": generation,
    }


def retire_environment(request: RetireRequest) -> RetireOutcome:
    """Retire one environment: record the tree, archive, end every process, remove the workspace."""

    admitted_id = receipt_id_segment(request.receipt_id)
    ref = request.ref
    workspace = leaf_workspace(ref.config, ref.repository_id, ref.task, ref.leaf_id)
    place = RetirePlace(
        workspace=workspace,
        receipts_root=request.receipts_root,
        now=request.now,
        archive_session=request.archive_session,
    )
    existing = find_pending_receipt(request.receipts_root, admitted_id)
    if existing is not None:
        if existing.state == "finished":
            return RetireOutcome(
                receipt=existing,
                workspace_removed=True,
                detail="the environment was already retired",
            )
        return resume_retire(place, existing)
    if not workspace.is_dir():
        raise ExplainerRefused(
            "explainer-missing", f"the leaf {ref.leaf_id} has no environment at {workspace}"
        )
    return retire_workspace(
        RetireStart(
            place=place,
            leaf_id=ref.leaf_id,
            generation=request.generation,
            receipt_id=admitted_id,
        )
    )


def retire_workspace(start: RetireStart) -> RetireOutcome:
    """Retire the environment lying at the start's workspace (rule 8's steps 1 to 6)."""

    place = start.place
    admitted_id = receipt_id_segment(start.receipt_id)
    existing = find_pending_receipt(place.receipts_root, admitted_id)
    if existing is not None and existing.state != "finished":
        return resume_retire(place, existing)
    workspace = place.workspace
    seat = agent_id(workspace, start.generation)
    tree = process_tree(workspace, seat)
    receipt = RetireReceipt(
        receipt_id=admitted_id,
        leaf_id=start.leaf_id,
        agent_id=seat,
        generation=start.generation,
        tree=tree,
        state="pending",
        step="tree-recorded",
        detail=f"{len(tree.processes)} process(es) recorded",
        measured_memory_bytes=tree.measured_memory_bytes,
        recorded_at=place.now,
    )
    write_receipt(place.receipts_root, receipt)
    return settle_retire(place, receipt)


def resume_retire(place: RetirePlace, receipt: RetireReceipt) -> RetireOutcome:
    """Finish a retire left pending, by the agent id, generation and tree its receipt recorded."""

    return settle_retire(place, receipt)


def _pending_outcome(
    place: RetirePlace,
    receipt: RetireReceipt,
    *,
    step: str,
    detail: str,
    held: tuple[str, ...] = (),
) -> RetireOutcome:
    pending = receipt.model_copy(
        update={"state": "pending", "step": step, "detail": detail, "held": held}
    )
    write_receipt(place.receipts_root, pending)
    return RetireOutcome(receipt=pending, workspace_removed=False, held=held, detail=detail)


def settle_retire(place: RetirePlace, receipt: RetireReceipt) -> RetireOutcome:
    """Archive the receipt's seat, end its processes, remove the workspace and finish the receipt."""

    workspace = place.workspace
    if place.archive_session is not None and receipt.step != "archived":
        try:
            place.archive_session(receipt.agent_id)
        except Exception as error:
            return _pending_outcome(place, receipt, step="archive", detail=str(error))
        receipt = receipt.model_copy(update={"state": "pending", "step": "archived"})
        write_receipt(place.receipts_root, receipt)
    try:
        by_marker, by_tree, remaining = end_processes(
            receipt.tree, workspace=workspace, agent=receipt.agent_id
        )
    except Exception as error:
        return _pending_outcome(place, receipt, step="end-processes", detail=str(error))
    receipt = receipt.model_copy(
        update={"ended_by_marker": by_marker, "ended_by_tree": by_tree, "remaining": remaining}
    )
    if remaining:
        return _pending_outcome(
            place,
            receipt,
            step="processes-remain",
            detail=f"process(es) {list(remaining)} still answer alive",
        )
    try:
        held = (
            remove_workspace_contents(workspace, root=workspace.parent)
            if workspace.is_dir()
            else []
        )
    except Exception as error:
        return _pending_outcome(place, receipt, step="workspace-remove", detail=str(error))
    if held:
        return _pending_outcome(
            place, receipt, step="workspace-held", detail="; ".join(held), held=tuple(held)
        )
    if workspace.is_dir():
        mark_retired(workspace, receipt.receipt_id, at=place.now)
    finished = receipt.model_copy(
        update={
            "state": "finished",
            "step": "finished",
            "remaining": (),
            "held": (),
            "finished_at": place.now,
            "detail": "the workspace was removed; pages and grades stay with the task",
        }
    )
    write_receipt(place.receipts_root, finished)
    return RetireOutcome(receipt=finished, workspace_removed=True, held=(), detail=finished.detail)
