"""Where one leaf's explainer environment lives (MIK-R186 rules 1, 2, 9; MIK-R211 v5 rule 12).

One environment per leaf, outside every worktree, memory repository and task folder, and everything
of it inside one folder, so removing it is one folder delete plus the Git worktree prune (D605):

``<coordination>/explainer/<repository>/<task>/<leaf>/``
    the workspace: ``binding.json``, ``state/``, ``instructions/``, ``reference/``, ``kit/``,
    ``revisions/<n>/``, ``trees/code/``, ``trees/memory/``, ``drafts/assets/``, ``renders/`` and
    ``scratch/``. Nothing of the leaf lies beside the folder;

``<leaf>/state/``
    the side files: the request ledger, the seat attempts and the leaf's one lock. Grades, the
    build, Update and Retire are written under that lock, and no other file lock is taken while it
    is held (MIK-R186 rule 2's lock order);

``<task>/notes/reports/review-frames/<leaf>/explainer/seat-generation``
    the seat-generation counter, kept with the task's explainer records beside ``revisions/`` and
    not in the workspace: removing the workspace never resets it, so a reopened explainer never
    takes an archived chat's id (MIK-R211 v5 rule 12).

Every path below the task's folder is admitted physically before it is read, written or removed: a
symlinked component, or a target that resolves outside the folder, is held and reported, never
followed. That is the same rule ``_Confinement`` applies inside an archived task
(``application/review_artifact_cleanup.py``), and this folder is a named departure from the R4
ruling because it lies outside every task folder (MIK-R186 rule 2).
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Mapping, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_worktrees import remove_checkout
from agents_remember.canvas.paths import PathRefused, guard_path, path_segment
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root
from agents_remember.tasks.task_paths import slugify

#: The frame a leaf's explanation pages live in (MIK-R103): one lineage per leaf.
EXPLAINER_FRAME = "explainer"

__all__ = [
    "BINDING_NAME",
    "DRAFTS_DIRECTORY",
    "EXPLAINER_DIRECTORY",
    "EXPLAINER_FRAME",
    "GENERATION_SUFFIX",
    "INSTRUCTIONS_DIRECTORY",
    "KIT_DIRECTORY",
    "LOCK_NAME",
    "LOCK_RESOURCE",
    "LOCK_SUFFIX",
    "REFERENCE_DIRECTORY",
    "RENDERS_DIRECTORY",
    "RETIRE_RECEIPTS_DIRECTORY",
    "REVISIONS_DIRECTORY",
    "SCRATCH_DIRECTORY",
    "SEAT_COUNTER_NAME",
    "STATE_DIRECTORY",
    "binding_path",
    "cleanup_workspace_tree",
    "compose_request_message",
    "copy_folder",
    "copy_revision",
    "ensure_leaf_workspace",
    "explained_task",
    "explainer_base",
    "explainer_records_root",
    "explainer_roots",
    "explainer_task_folder",
    "explainer_task_root",
    "first_nested_symlink",
    "generation_path",
    "guard",
    "guard_explainer_path",
    "leaf_lock_path",
    "leaf_lock_resource",
    "leaf_workspace",
    "lock_path",
    "read_workspace_id",
    "receipt_id_segment",
    "receipt_path",
    "reference_path_of",
    "remove_workspace",
    "repository_path",
    "retire_receipts_root",
    "seat_counter_path",
    "state_directory",
]

EXPLAINER_DIRECTORY = "explainer"
RETIRE_RECEIPTS_DIRECTORY = "explainer-retire"
GENERATION_SUFFIX = ".generation"
LOCK_SUFFIX = ".lock"

#: The lock names its resource in the kernel's diagnostics. The service's build, Update and Retire and
#: the seat's own grade writers name the same resource, because they hold the same file (MIK-R187 rule 6).
LOCK_RESOURCE = "explainer leaf"
BINDING_NAME = "binding.json"
INSTRUCTIONS_DIRECTORY = "instructions"
SCRATCH_DIRECTORY = "scratch"
DRAFTS_DIRECTORY = "drafts"
#: MIK-R211 v5 rule 12's layout (D605): everything of one explained leaf lives inside its folder.
STATE_DIRECTORY = "state"
REFERENCE_DIRECTORY = "reference"
KIT_DIRECTORY = "kit"
REVISIONS_DIRECTORY = "revisions"
RENDERS_DIRECTORY = "renders"
#: The lock's file name inside ``state/`` and the task-side seat counter's (MIK-R211 v5 rule 12).
LOCK_NAME = "leaf"
SEAT_COUNTER_NAME = "seat-generation"
#: The task's frame area, as ``application/review_frame_store.py`` names it.
FRAMES_DIRECTORY = "review-frames"


def explainer_base(config: McpRuntimeConfig) -> Path:
    """The one root every explainer environment is ordered under."""

    return config.coordination_root / EXPLAINER_DIRECTORY


def explainer_task_folder(coordination_root: Path, repository_id: str, task: str) -> Path:
    """The task's explainer folder, from the coordination root and two names alone.

    The archive hook holds a coordination root, a repository id and a task folder name, and no
    runtime configuration: the folder is a placement, not an authority, so it is derived here once
    (MIK-R186 rules 2 and 9).
    """

    return (
        coordination_root
        / EXPLAINER_DIRECTORY
        / path_segment(repository_id, name="repository")
        / path_segment(task, name="task")
    )


def explainer_roots(coordination_root: Path) -> list[Path]:
    """Every task folder that holds environments, sorted; a symlinked one is left out."""

    base = coordination_root / EXPLAINER_DIRECTORY
    if not base.is_dir() or base.is_symlink():
        return []
    roots: list[Path] = []
    for repository in sorted(base.iterdir()):
        if not repository.is_dir() or repository.is_symlink():
            continue
        for task in sorted(repository.iterdir()):
            if task.is_dir() and not task.is_symlink():
                roots.append(task)
    return roots


def explainer_task_root(config: McpRuntimeConfig, repository_id: str, task: str) -> Path:
    """The task's explainer folder, which is the admitted root of everything below it."""

    require_repo(config, repository_id)
    root = explainer_task_folder(config.coordination_root, repository_id, task)
    # The task folder may not exist yet: its parent is the admitted root until it does.
    parent = root.parent
    if parent.exists():
        guard_path(config.coordination_root, parent, label="explainer task folder")
    return root


def repository_path(config: McpRuntimeConfig, repository_id: str, *, memory: bool = False) -> Path:
    """One repository's code checkout, or its memory root, as this runtime holds it.

    A runtime that names no such path cannot bind a comparison or read a tree, so the read is refused
    by name rather than resolving a relative path against the process's own working folder.
    """

    try:
        repository = config.repositories[repository_id]
    except KeyError as missing:
        raise PathRefused(
            f"this runtime names no repository {repository_id!r}; a leaf's paths are read from it"
        ) from missing
    path = repository.memory_root if memory else repository.path
    if path is None:
        side = "memory root" if memory else "code checkout"
        raise PathRefused(
            f"repository {repository_id!r} has no {side} in this runtime; nothing is read from "
            "the process's own folder in its place"
        )
    return Path(path)


def guard(root: Path, target: Path, *, label: str = "explainer path") -> Path:
    """Admit one path below the task's explainer folder, or hold it.

    A caller that has not created ``root`` yet passes the folder it intends to create; once the
    folder exists this refuses a symlinked component on the way to ``target`` exactly as
    ``_Confinement`` does (MIK-R186 rule 2).
    """

    return guard_path(root, target, label=label)


def guard_explainer_path(
    target: Path, *, root: Path | None = None, label: str = "explainer path"
) -> Path:
    """Admit ``target`` and every ancestor under its coordination/task root (MIK-R186 rule 2)."""

    target_path = Path(target)
    authority = root
    for parent in target_path.parents:
        if parent.name in {EXPLAINER_DIRECTORY, "tasks"} and parent.parent != parent:
            authority = parent.parent
            break
    if authority is None:
        authority = target_path.parent
    guard_path(authority, target_path, label=label)
    if root is not None and root != authority:
        guard_path(root, target_path, label=label)
    return target_path


def copy_folder(source: Path, destination: Path, *, root: Path) -> int:
    """Copy one folder's files into ``destination`` as plain writable files; answer how many.

    Modes are not carried: a published revision may be read-only in the task, and its copy in the
    workspace is the explainer's to change (MIK-R211 v5 rule 3). A symlink is not followed.
    """

    copied = 0
    for item in sorted(source.rglob("*")):
        if item.is_symlink() or not item.is_file():
            continue
        target = destination / item.relative_to(source)
        guard_explainer_path(target, root=root, label="explainer copied document")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(item, target)
        copied += 1
    return copied


def copy_revision(source: Path, workspace: Path, number: int) -> int:
    """Copy the task's published revision ``number`` into the workspace's ``revisions/<n>/``.

    The task stays the source of truth: a build copies every published revision from it, and a
    publication copies the revision it has just made (the Manager's 16:40Z ruling), so the
    workspace's revisions/ never states less than is published. A copy already under that number
    is replaced whole, so the folder holds the published bytes and nothing else.
    """

    destination = workspace / REVISIONS_DIRECTORY / str(number)
    guard_explainer_path(destination, root=workspace.parent, label="explainer revision copy")
    if destination.is_dir() and not destination.is_symlink():
        shutil.rmtree(destination)
    return copy_folder(source, destination, root=workspace.parent)


def leaf_workspace(config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str) -> Path:
    """``<leaf>/`` -- the workspace, and the only folder the explainer may write."""

    root = explainer_task_root(config, repository_id, task)
    target = root / path_segment(leaf_id, name="leaf")
    if root.exists() or root.is_symlink():
        guard_explainer_path(target, root=config.coordination_root, label="explainer workspace")
    return target


def generation_path(config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str) -> Path:
    """The leaf's seat-generation counter, kept with the task's records (MIK-R211 v5 rule 12)."""

    return seat_counter_path(leaf_workspace(config, repository_id, task, leaf_id))


def seat_counter_path(workspace: Path) -> Path:
    """Where the counter a workspace's seat ids are derived from lies: with the task, not the workspace.

    The workspace is ``<coordination>/explainer/<repository>/<task>/<leaf>``, so the task it explains
    is ``<coordination>/tasks/<repository>/<task>``; its explainer records lie beside ``revisions/``
    in the leaf's frame area. A counter the workspace's old layout left beside the folder
    (``<leaf>.generation``) is moved there the first time it is read, so no generation is reused. A
    world with no such task folder keeps the counter beside the workspace, where it always was.
    """

    legacy = workspace.with_name(workspace.name + GENERATION_SUFFIX)
    task = explained_task(workspace)
    if task is None:
        return legacy
    counter = (
        durable_reports_root(task)
        / FRAMES_DIRECTORY
        / slugify(workspace.name)
        / EXPLAINER_FRAME
        / SEAT_COUNTER_NAME
    )
    guard_explainer_path(counter, root=task, label="explainer seat counter")
    if not counter.exists() and legacy.is_file() and not legacy.is_symlink():
        counter.parent.mkdir(parents=True, exist_ok=True)
        guard_explainer_path(counter, root=task, label="explainer seat counter")
        counter.write_bytes(legacy.read_bytes())
        legacy.unlink()
    return counter


def explained_task(workspace: Path) -> Path | None:
    """The task folder a workspace explains, or ``None`` when it is not where the layout puts it.

    ``<coordination>/explainer/<repository>/<task>/<leaf>`` explains
    ``<coordination>/tasks/<repository>/<task>``.
    """

    parents = workspace.parents
    if len(parents) < 4 or parents[2].name != EXPLAINER_DIRECTORY:
        return None
    task = parents[3] / "tasks" / parents[1].name / parents[0].name
    return task if task.is_dir() and not task.is_symlink() else None


def state_directory(workspace: Path) -> Path:
    """``<leaf>/state/`` -- the ledger, the seat attempts and the lock (MIK-R211 v5 rule 12)."""

    return workspace / STATE_DIRECTORY


def lock_path(config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str) -> Path:
    """``<leaf>/state/leaf.lock`` -- the leaf's one lock."""

    return leaf_lock_path(leaf_workspace(config, repository_id, task, leaf_id))


def leaf_lock_path(workspace: Path) -> Path:
    """The physical lock file on disk for ``workspace`` (MIK-R186 rule 2)."""

    return leaf_lock_resource(workspace).with_name(LOCK_NAME + LOCK_SUFFIX)


def leaf_lock_resource(workspace_or_lock: Path) -> Path:
    """The canonical lock resource ``<leaf>/state/leaf`` passed to :func:`exclusive_file_lock`.

    ``exclusive_file_lock`` appends ``.lock`` to the resource path it receives. A workspace, its
    lock file and a lock path of the old sibling layout (``<leaf>.lock``) all name the same
    resource, so the build, Update, Retire and the seat's grade writer hold one file (MIK-R186 rule
    2, MIK-R187 rule 6).
    """

    target = Path(workspace_or_lock)
    if target.name == LOCK_NAME + LOCK_SUFFIX and target.parent.name == STATE_DIRECTORY:
        return target.with_name(LOCK_NAME)
    if target.name.endswith(LOCK_SUFFIX):
        stem = target.name[: -len(LOCK_SUFFIX)]
        target = target.with_name(path_segment(stem, name="leaf"))
    return target.with_name(path_segment(target.name, name="leaf")) / STATE_DIRECTORY / LOCK_NAME


def binding_path(workspace: Path) -> Path:
    """``binding.json`` -- what the environment is bound to (MIK-R186 rule 4)."""

    return workspace / BINDING_NAME


def explainer_records_root(task: Path) -> Path:
    """Where one task's explainer receipts lie, beside its other durable reports."""

    return durable_reports_root(task) / EXPLAINER_DIRECTORY


def receipt_id_segment(receipt_id: str) -> str:
    """A receipt id names one file, so it is one path segment (MIK-R186 rule 8).

    The id arrives from a request or from the architect's tool, and it is used as a file name beside the
    leaf's other receipts. Anything that could leave that directory -- a separator, a traversal, an
    absolute path, a hidden name -- is refused before any effect, by the same guard a leaf id gets.
    """

    return path_segment(receipt_id, name="receipt id")


def receipt_path(root: Path, receipt_id: str) -> Path:
    """One receipt's file, validated as one path segment inside the leaf's receipt folder."""

    return root / f"{receipt_id_segment(receipt_id)}.json"


def retire_receipts_root(task: Path, leaf_id: str) -> Path:
    """Where one leaf's retire receipts lie (MIK-R186 rule 8 step 2)."""

    return (
        explainer_records_root(task)
        / RETIRE_RECEIPTS_DIRECTORY
        / path_segment(leaf_id, name="leaf")
    )


def ensure_leaf_workspace(
    config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str
) -> Path:
    """Create the leaf's workspace if it is absent, after admitting the folder it lands in.

    A symlinked component below the task's explainer folder is refused here, before anything is
    created (:class:`agents_remember.canvas.paths.PathRefused`).
    """

    root = explainer_task_root(config, repository_id, task)
    guard_explainer_path(root, root=config.coordination_root, label="explainer task folder")
    root.mkdir(parents=True, exist_ok=True)
    guard_explainer_path(root, root=config.coordination_root, label="explainer task folder")
    workspace = root / path_segment(leaf_id, name="leaf")
    if workspace.exists() and workspace.is_symlink():
        raise PathRefused(
            f"the explainer workspace {workspace} is a symlink; it is held, not followed"
        )
    guard_explainer_path(workspace, root=config.coordination_root, label="explainer workspace")
    workspace.mkdir(exist_ok=True)
    guard_explainer_path(workspace, root=config.coordination_root, label="explainer workspace")
    for name in (STATE_DIRECTORY, INSTRUCTIONS_DIRECTORY, SCRATCH_DIRECTORY, DRAFTS_DIRECTORY):
        folder = workspace / name
        guard_explainer_path(folder, root=config.coordination_root, label=f"explainer {name}")
        folder.mkdir(exist_ok=True)
        guard_explainer_path(folder, root=config.coordination_root, label=f"explainer {name}")
    return workspace


def first_nested_symlink(directory: Path) -> Path | None:
    """Return the first symlink inside ``directory`` without following links, or ``None``."""

    for root_dir, dirs, files in os.walk(directory, followlinks=False):
        base = Path(root_dir)
        for name in (*dirs, *files):
            candidate = base / name
            if candidate.is_symlink():
                return candidate
    return None


def _format_pick_item(item: Mapping[str, Any] | str) -> str | None:
    if isinstance(item, str):
        text = item.strip()
        return f"- {text}" if text else None
    if not isinstance(item, Mapping):
        return None
    label = str(
        item.get("reference")
        or item.get("path")
        or item.get("id")
        or item.get("title")
        or item.get("text")
        or ""
    ).strip()
    note = str(item.get("note") or "").strip()
    if label and note and label != note:
        return f"- {label}: {note}"
    return f"- {label or note}" if (label or note) else None


def _format_list_section(title: str, values: Sequence[str] | None) -> str | None:
    if not values:
        return None
    cleaned = [str(val).strip() for val in values if str(val).strip()]
    if not cleaned:
        return None
    return f"{title}:\n" + "\n".join(f"- {val}" for val in cleaned)


def compose_request_message(
    message: str,
    *,
    picks: Sequence[Mapping[str, Any] | str] | None,
    stale_part_ids: Sequence[str] | None,
    stale_grades: Sequence[str] | None,
) -> str:
    """Compose a first-turn or follow-up message with attached picks and stale references."""

    sections: list[str] = []
    if picks:
        pick_lines = [line for item in picks if (line := _format_pick_item(item)) is not None]
        if pick_lines:
            sections.append("Attached references:\n" + "\n".join(pick_lines))
    for title, items in (
        ("Stale parts", stale_part_ids),
        ("Stale grades", stale_grades),
    ):
        sec = _format_list_section(title, items)
        if sec is not None:
            sections.append(sec)
    body = message.strip()
    if sections and body:
        return "\n\n".join([*sections, body])
    if sections:
        return "\n\n".join(sections)
    return message


def reference_path_of(reference: str) -> str | None:
    """Extract the repository-relative file path from a stored grade reference, if it carries one."""

    if reference.startswith("{"):
        with suppress(ValueError):
            parsed = json.loads(reference)
            if isinstance(parsed, dict) and isinstance(parsed.get("path"), str):
                return str(parsed["path"])
    for prefix in ("hunk:", "file:"):
        if reference.startswith(prefix):
            body = reference.removeprefix(prefix)
            path, sep, _rest = body.partition("@")
            return path if sep and path else None
    if reference.startswith("inv:") and "/" in reference:
        parts = reference.split("/", 2)
        if len(parts) == 3 and parts[2]:
            return parts[2].split(":", 1)[0]
    return None


def _remove_checkout_pair(workspace: Path, *, root: Path) -> None:
    trees = workspace / "trees"
    guard_explainer_path(trees, root=root, label="explainer trees")
    for side in ("code", "memory"):
        remove_checkout(trees / side, root=root)
    if trees.exists():
        trees.rmdir()


def cleanup_workspace_tree(workspace: Path, *, root: Path | None = None) -> None:
    """Clean an unconfirmed workspace, unregistering its Git worktrees before files."""

    if not workspace.exists():
        return
    authority = root or workspace.parent
    guard_explainer_path(workspace, root=authority, label="explainer workspace")
    _remove_checkout_pair(workspace, root=authority)
    if first_nested_symlink(workspace) is not None:
        return
    shutil.rmtree(workspace)


def remove_workspace(workspace: Path, *, root: Path) -> list[str]:
    """Remove the leaf's whole folder: its Git worktrees through Git first, then every file in it.

    Nothing is kept -- no marker, no lock, no ledger -- because a removed workspace leaves no residue
    under the explainer folder (MIK-R211 v5 rule 12). A symlink anywhere inside is held and named,
    and then nothing is removed, because a link is never followed out of the folder.
    """

    if not workspace.exists() and not workspace.is_symlink():
        return []
    try:
        guard_explainer_path(workspace, root=root, label="explainer workspace")
        _remove_checkout_pair(workspace, root=root)
    except (PathRefused, OSError, ValueError) as refused:
        return [str(refused)]
    nested = first_nested_symlink(workspace)
    if nested is not None:
        return [f"{nested} is a symlink; it is held, not followed"]
    try:
        shutil.rmtree(workspace)
    except OSError as refused:
        return [str(refused)]
    return []


def read_workspace_id(workspace: Path) -> str:
    """Read the host workspace ID from ``seat-provenance.json``, or fallback to ``workspace``."""

    provenance = workspace / INSTRUCTIONS_DIRECTORY / "seat-provenance.json"
    if provenance.is_file() and not provenance.is_symlink():
        with suppress(OSError, ValueError):
            payload = json.loads(provenance.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("workspaceId"), str):
                return payload["workspaceId"]
    return str(workspace) if workspace.is_dir() else ""
