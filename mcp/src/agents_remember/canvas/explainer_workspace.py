"""Building, updating and removing one leaf's explainer environment (MIK-R186 rules 1, 2, 6, 8).

The environment is the explainer's whole world: two ordinary detached Git worktrees at the bound candidate trees,
the evidence packet and the grants, the capsule it was started with, and scratch and draft folders
it may write. This module owns the bytes:

* **build** checks out the two worktrees, then writes ``instructions/`` and -- last of all --
  ``binding.json``. A failure before the last step leaves no binding, so nothing claims an
  environment that is not there;
* **update** moves those same worktrees with Git checkout and binds last. While either tree is
  moving, no binding is served; a failed update remains unbound until a successful rebuild.
* **retire** records the seat's process tree *before* it archives anything, ends every process by
  both counts, and removes the workspace completely -- its Git worktrees removed and pruned, its
  folder deleted -- leaving only a receipt in the task's records (MIK-R211 v5 rule 12).

A build also copies in what the explainer needs, the way a task agent's workspace gets it (MIK-R211
v5 rule 3): the task's reference notes for the leaf (``reference/``), the pinned library files
(``kit/``), and every published revision of the leaf (``revisions/<n>/``), as plain writable folders.
The draft starts as a copy of the latest of them.

Nothing here talks to the host, reads a comparison or prepares a packet: the caller hands those in,
so the same code is exercised without a host. Every write and every removal is admitted against the
task's explainer folder first, and a symlinked component is held and reported rather than followed
(MIK-R186 rule 2).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_paths import (
    DRAFTS_DIRECTORY,
    EXPLAINER_FRAME,
    FRAMES_DIRECTORY,
    INSTRUCTIONS_DIRECTORY,
    KIT_DIRECTORY,
    REFERENCE_DIRECTORY,
    REVISIONS_DIRECTORY,
    SCRATCH_DIRECTORY,
    copy_folder,
    copy_revision,
    ensure_leaf_workspace,
    first_nested_symlink,
    guard,
    guard_explainer_path,
    leaf_workspace,
    receipt_id_segment,
    remove_workspace,
    repository_path,
)
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    ExplainerRefused,
    PlannedIdentity,
    RetireReceipt,
    end_processes,
    find_pending_receipt,
    process_tree,
    same_binding,
    seat_for_workspace,
    write_binding,
    write_receipt,
)
from agents_remember.canvas.explainer_worktrees import (
    CheckoutTotals,
    checkout_tree,
    prune_worktrees,
)
from agents_remember.canvas.kit import (
    KIT_CONTRACT_PREFIX,
    KIT_MANIFEST_NAME,
    KIT_ROOT,
    kit_assets,
    kit_files,
)
from agents_remember.canvas.paths import PathRefused, task_root
from agents_remember.kernel.atomic_write import atomic_write_bytes, atomic_write_text
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.models.knowledge.review_evidence import EvidencePacket
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.tasks.task_paths import slugify

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
    "build_environment",
    "plan_retire",
    "resume_retire",
    "retire_environment",
    "retire_workspace",
    "settle_retire",
    "update_environment",
]

ASSIGNMENT_NAME = "assignment.txt"
ASSIGNMENT_MD_NAME = "assignment.md"
EVIDENCE_NAME = "evidence.json"
GRANTS_NAME = "grants.json"


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
    #: The repositories whose worktree registrations the removal prunes (MIK-R211 v5 rule 12).
    repositories: tuple[Path, ...] = ()


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
    """One build's answer: the binding it made, its checkouts and the steps it took."""

    workspace: Path
    binding: ExplainerBinding
    steps: tuple[str, ...] = ()
    code: CheckoutTotals | None = None
    memory: CheckoutTotals | None = None
    built: bool = True

    def as_record(self) -> dict[str, Any]:
        return {
            "workspace": str(self.workspace),
            "binding": self.binding.model_dump(by_alias=True),
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
    code: CheckoutTotals | None = None
    memory: CheckoutTotals | None = None
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


def _empty_directory(directory: Path, *, root: Path) -> None:
    """Clear an owned instructions staging directory without following links."""

    for child in directory.iterdir():
        guard(root, child, label="instructions entry")
        if child.is_symlink() or (child.is_dir() and first_nested_symlink(child) is not None):
            raise PathRefused(f"the instructions hold a symlink at {child}")
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()


def _checkout_inputs(
    run: EnvironmentRun, workspace: Path, reporter: _Progress
) -> tuple[CheckoutTotals, CheckoutTotals]:
    destination = workspace / "trees"
    guard_explainer_path(destination, root=workspace.parent, label="explainer trees")
    destination.mkdir(exist_ok=True)
    results: list[CheckoutTotals] = []
    for side, repository, tree in (
        ("code", run.inputs.code_repository, run.inputs.binding.code_candidate_tree),
        (
            "memory",
            run.inputs.memory_repository,
            run.inputs.binding.memory_candidate_tree,
        ),
    ):
        reporter.step(f"checkout-{side}", state="running")
        checked = checkout_tree(repository, tree, destination / side, root=workspace.parent)
        reporter.step(f"checkout-{side}", state="done", **checked.as_record())
        results.append(checked)
    return results[0], results[1]


def _environment_binding(run: EnvironmentRun, packet_bytes: bytes) -> ExplainerBinding:
    return ExplainerBinding(
        binding=run.inputs.binding,
        planned=run.inputs.planned,
        prepared_at=run.now,
        packet_digest=_digest(packet_bytes),
        record_digest=run.inputs.record_digest,
    )


def _published_revisions(task: Path, leaf_id: str) -> list[tuple[int, Path]]:
    """The leaf's published revisions in the task, oldest first: one folder per revision."""

    folder = (
        durable_reports_root(task)
        / FRAMES_DIRECTORY
        / slugify(leaf_id)
        / EXPLAINER_FRAME
        / REVISIONS_DIRECTORY
    )
    if not folder.is_dir() or folder.is_symlink():
        return []
    return sorted(
        (int(item.name), item)
        for item in folder.iterdir()
        if item.is_dir() and not item.is_symlink() and item.name.isdecimal()
    )


def _copy_kit(workspace: Path, *, root: Path) -> int:
    """Write every pinned library file into ``kit/``, verified against the kit's own manifest."""

    destination = workspace / KIT_DIRECTORY
    carried = kit_assets(tuple(kit_files()))
    for contract_path, body in carried.items():
        target = destination / contract_path.removeprefix(KIT_CONTRACT_PREFIX)
        guard_explainer_path(target, root=root, label="explainer kit")
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_bytes(target, body)
    manifest = destination / KIT_MANIFEST_NAME
    guard_explainer_path(manifest, root=root, label="explainer kit")
    atomic_write_bytes(manifest, (KIT_ROOT / KIT_MANIFEST_NAME).read_bytes())
    return len(carried)


def _copy_documents(ref: EnvironmentRef, workspace: Path, reporter: _Progress) -> None:
    """Copy in what the explainer needs, as a task agent's documents are (MIK-R211 v5 rule 3).

    The task's reference notes for the leaf, the kit files and every published revision are plain
    folder copies. The draft starts as a copy of the latest revision when it holds nothing yet, so a
    new revision starts from the published one rather than from an empty page.
    """

    root = workspace.parent
    kit = _copy_kit(workspace, root=root)
    try:
        task = task_root(ref.config, ref.repository_id, ref.task)
    except PathRefused:
        reporter.step("documents", reference=0, kit=kit, revisions=0)
        return
    reference = task / "notes" / "reference" / ref.leaf_id
    notes = (
        copy_folder(reference, workspace / REFERENCE_DIRECTORY, root=root)
        if reference.is_dir() and not reference.is_symlink()
        else 0
    )
    revisions = _published_revisions(task, ref.leaf_id)
    for number, folder in revisions:
        copy_revision(folder, workspace, number)
    draft = workspace / DRAFTS_DIRECTORY / "assets"
    started = False
    if revisions and not any(item.is_file() for item in draft.rglob("*")):
        latest = workspace / REVISIONS_DIRECTORY / str(revisions[-1][0]) / "assets"
        copy_folder(latest, draft, root=root)
        started = True
    reporter.step(
        "documents",
        reference=notes,
        kit=kit,
        revisions=len(revisions),
        draftFrom=revisions[-1][0] if started else None,
    )


def build_environment(run: EnvironmentRun) -> EnvironmentBuild:
    """Create two plain Git worktrees and publish their binding last (L201 F9)."""

    reporter = _Progress(run.progress)
    ref = run.ref
    workspace = ensure_leaf_workspace(ref.config, ref.repository_id, ref.task, ref.leaf_id)
    reporter.step("workspace", path=str(workspace))
    # A failed attempt may have left useful worktrees; moving them again is Git's job.
    guard_explainer_path(workspace / "binding.json", root=workspace.parent, label="binding")
    (workspace / "binding.json").unlink(missing_ok=True)
    try:
        code, memory = _checkout_inputs(run, workspace, reporter)
        packet_bytes = _stage_and_commit_instructions(
            workspace=workspace, run=run, snapshot_name="trees", reporter=reporter
        )
        _copy_documents(ref, workspace, reporter)
        binding = _environment_binding(run, packet_bytes)
        write_binding(workspace, binding)
        reporter.step("bound", trees="trees")
    except BaseException:
        (workspace / "binding.json").unlink(missing_ok=True)
        raise
    return EnvironmentBuild(
        workspace=workspace,
        binding=binding,
        steps=tuple(reporter.steps),
        code=code,
        memory=memory,
    )


def update_environment(run: EnvironmentRun) -> EnvironmentUpdate:
    """Move the same two worktrees, then bind; a failed update stays unbound."""

    previous = run.previous
    ref = run.ref
    reporter = _Progress(run.progress)
    workspace = leaf_workspace(ref.config, ref.repository_id, ref.task, ref.leaf_id)
    if not workspace.is_dir():
        raise ExplainerRefused("explainer-missing", f"no environment at {workspace}")
    if previous is not None and same_binding(previous.binding, run.inputs.binding):
        reporter.step("current", number=previous.binding.comparison_number)
        return EnvironmentUpdate(
            workspace=workspace,
            binding=previous,
            previous_number=previous.binding.comparison_number,
            current=True,
            steps=tuple(reporter.steps),
        )
    guard_explainer_path(workspace / "binding.json", root=workspace.parent, label="binding")
    (workspace / "binding.json").unlink(missing_ok=True)
    try:
        code, memory = _checkout_inputs(run, workspace, reporter)
        packet_bytes = _stage_and_commit_instructions(
            workspace=workspace, run=run, snapshot_name="trees", reporter=reporter
        )
        binding = _environment_binding(run, packet_bytes)
        write_binding(workspace, binding)
        reporter.step("bound", trees="trees")
    except BaseException:
        (workspace / "binding.json").unlink(missing_ok=True)
        raise
    return EnvironmentUpdate(
        workspace=workspace,
        binding=binding,
        previous_number=None if previous is None else previous.binding.comparison_number,
        current=False,
        code=code,
        memory=memory,
        steps=tuple(reporter.steps),
    )


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
    chosen_snapshot = "trees" if snapshot_name is None else snapshot_name
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


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def plan_retire(ref: EnvironmentRef, *, generation: int) -> dict[str, Any]:
    """What a retire would do, without touching a seat, a process or a file (rule 9)."""

    workspace = leaf_workspace(ref.config, ref.repository_id, ref.task, ref.leaf_id)
    return {
        "leaf": ref.leaf_id,
        "workspace": str(workspace),
        "exists": workspace.is_dir(),
        "agentId": seat_for_workspace(workspace, generation),
        "generation": generation,
    }


def _repositories(ref: EnvironmentRef) -> tuple[Path, ...]:
    """The code and memory repositories whose worktrees an environment of ``ref`` registered."""

    found: list[Path] = []
    for memory in (False, True):
        with suppress(PathRefused):
            found.append(repository_path(ref.config, ref.repository_id, memory=memory))
    return tuple(found)


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
        repositories=_repositories(ref),
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
    # A seat in any state is removed: live, closed, archived, or with no workspace left on disk. A
    # missing workspace still has its chat archived and its worktree registrations pruned, because
    # a folder deleted by hand leaves both behind (MIK-R211 v5 rule 12).
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
    seat = seat_for_workspace(workspace, start.generation)
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
        update={
            "ended_by_marker": by_marker,
            "ended_by_tree": by_tree,
            "remaining": remaining,
        }
    )
    if remaining:
        return _pending_outcome(
            place,
            receipt,
            step="processes-remain",
            detail=f"process(es) {list(remaining)} still answer alive",
        )
    try:
        held = remove_workspace(workspace, root=workspace.parent)
        for repository in place.repositories:
            prune_worktrees(repository)
    except Exception as error:
        return _pending_outcome(place, receipt, step="workspace-remove", detail=str(error))
    if held:
        return _pending_outcome(
            place,
            receipt,
            step="workspace-held",
            detail="; ".join(held),
            held=tuple(held),
        )
    finished = receipt.model_copy(
        update={
            "state": "finished",
            "step": "finished",
            "remaining": (),
            "held": (),
            "finished_at": place.now,
            "detail": (
                "the chat was archived and the workspace removed completely, its Git worktrees "
                "pruned; the published revisions and grades stay with the task"
            ),
        }
    )
    write_receipt(place.receipts_root, finished)
    return RetireOutcome(receipt=finished, workspace_removed=True, held=(), detail=finished.detail)
