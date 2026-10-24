"""Where one leaf's explainer environment lives (MIK-R186 rules 1, 2, 9).

One environment per leaf, outside every worktree, memory repository and task folder:

``<coordination>/explainer/<repository>/<task>/``
    the task's one folder, which holds the leaf's workspace, its seat-generation counter and its
    one lock, all as siblings;

``<leaf>/``
    the workspace itself: ``instructions/``, ``scratch/``, ``drafts/``, ``snapshot-<n>/code/``,
    ``snapshot-<n>/memory/`` and ``binding.json`` (MIK-R186 rule 2);

``<leaf>.generation``
    the seat-generation counter. It only grows, and nothing removes it before master retirement;

``<leaf>.lock``
    the one lock of the leaf. Grades, the build, Update and Retire are written under it, and no
    other file lock is taken while it is held (MIK-R186 rule 2's lock order);

``<leaf>/retired.json``
    the marker a finished Retire leaves behind, naming its receipt.

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

from agents_remember.canvas.paths import PathRefused, guard_path, path_segment
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root

#: The frame a leaf's explanation pages live in (MIK-R103): one lineage per leaf.
EXPLAINER_FRAME = "explainer"
#: The highest revision number a read of the leaf's revisions looks at (MIK-R103 retention).
MAX_LISTED_REVISIONS = 32

__all__ = [
    "BINDING_NAME",
    "DRAFTS_DIRECTORY",
    "EXPLAINER_DIRECTORY",
    "EXPLAINER_FRAME",
    "GENERATION_SUFFIX",
    "INSTRUCTIONS_DIRECTORY",
    "LOCK_RESOURCE",
    "LOCK_SUFFIX",
    "MAX_LISTED_REVISIONS",
    "RETIRED_NAME",
    "RETIRE_RECEIPTS_DIRECTORY",
    "SCRATCH_DIRECTORY",
    "SNAPSHOT_PREFIX",
    "binding_path",
    "cleanup_workspace_tree",
    "compose_request_message",
    "ensure_leaf_workspace",
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
    "make_writable_tree",
    "read_workspace_id",
    "receipt_id_segment",
    "receipt_path",
    "reference_path_of",
    "remove_workspace_contents",
    "repository_path",
    "retire_receipts_root",
    "retired_marker",
    "snapshot_folder",
]

EXPLAINER_DIRECTORY = "explainer"
RETIRE_RECEIPTS_DIRECTORY = "explainer-retire"
GENERATION_SUFFIX = ".generation"
LOCK_SUFFIX = ".lock"

#: The lock names its resource in the kernel's diagnostics. The service's build, Update and Retire and
#: the seat's own grade writers name the same resource, because they hold the same file (MIK-R187 rule 6).
LOCK_RESOURCE = "explainer leaf"
BINDING_NAME = "binding.json"
RETIRED_NAME = "retired.json"
INSTRUCTIONS_DIRECTORY = "instructions"
SCRATCH_DIRECTORY = "scratch"
DRAFTS_DIRECTORY = "drafts"
SNAPSHOT_PREFIX = "snapshot-"


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


def leaf_workspace(config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str) -> Path:
    """``<leaf>/`` -- the workspace, and the only folder the explainer may write."""

    root = explainer_task_root(config, repository_id, task)
    target = root / path_segment(leaf_id, name="leaf")
    if root.exists() or root.is_symlink():
        guard_explainer_path(target, root=config.coordination_root, label="explainer workspace")
    return target


def generation_path(config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str) -> Path:
    """``<leaf>.generation`` -- the seat-generation counter, a sibling of the workspace."""

    root = explainer_task_root(config, repository_id, task)
    target = root / (path_segment(leaf_id, name="leaf") + GENERATION_SUFFIX)
    if root.exists() or root.is_symlink():
        guard_explainer_path(target, root=config.coordination_root, label="explainer generation")
    return target


def lock_path(config: McpRuntimeConfig, repository_id: str, task: str, leaf_id: str) -> Path:
    """``<leaf>.lock`` -- the leaf's one lock."""

    root = explainer_task_root(config, repository_id, task)
    target = root / (path_segment(leaf_id, name="leaf") + LOCK_SUFFIX)
    if root.exists() or root.is_symlink():
        guard_explainer_path(target, root=config.coordination_root, label="explainer lock")
    return target


def leaf_lock_path(workspace: Path) -> Path:
    """The physical ``<leaf>.lock`` file on disk for ``workspace`` (MIK-R186 rule 2)."""

    base = leaf_lock_resource(workspace)
    return base.with_name(path_segment(base.name, name="leaf") + LOCK_SUFFIX)


def leaf_lock_resource(workspace_or_lock: Path) -> Path:
    """The canonical lock resource ``<leaf>`` passed to :func:`exclusive_file_lock`.

    ``exclusive_file_lock`` appends ``.lock`` to the resource path it receives; normalizing both
    ``<leaf>`` and ``<leaf>.lock`` to ``<leaf>`` guarantees every caller locks ``<leaf>.lock`` on
    disk with no ``.lock.lock`` doubling (MIK-R186 rule 2, MIK-R187 rule 6).
    """

    target = Path(workspace_or_lock)
    if target.name.endswith(LOCK_SUFFIX):
        stem = target.name[: -len(LOCK_SUFFIX)]
        return target.with_name(path_segment(stem, name="leaf"))
    return target.with_name(path_segment(target.name, name="leaf"))


def binding_path(workspace: Path) -> Path:
    """``binding.json`` -- what the environment is bound to (MIK-R186 rule 4)."""

    return workspace / BINDING_NAME


def retired_marker(workspace: Path) -> Path:
    """``retired.json`` -- what a finished Retire leaves in the leaf's folder (rule 8)."""

    return workspace / RETIRED_NAME


def snapshot_folder(workspace: Path, number: int) -> Path:
    """``snapshot-<n>/`` -- one binding's read-only copy of the two candidate trees."""

    return workspace / f"{SNAPSHOT_PREFIX}{number}"


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
    for name in (INSTRUCTIONS_DIRECTORY, SCRATCH_DIRECTORY, DRAFTS_DIRECTORY):
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


def make_writable_tree(directory: Path) -> None:
    """Ensure non-symlink directories under ``directory`` are writable for removal.

    Regular files are intentionally not chmodded so hard-linked snapshot inodes remain read-only.
    """

    for root_dir, dirs, _files in os.walk(directory, followlinks=False):
        base = Path(root_dir)
        with suppress(OSError):
            if not base.is_symlink():
                base.chmod(0o700)
        for name in dirs:
            entry = base / name
            if not entry.is_symlink():
                with suppress(OSError):
                    entry.chmod(0o700)


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
    for title, items in (("Stale parts", stale_part_ids), ("Stale grades", stale_grades)):
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


def cleanup_workspace_tree(workspace: Path, *, root: Path | None = None) -> None:
    """Remove an unconfirmed or failed build workspace if it contains no symlinks."""

    if (
        not workspace.exists()
        or workspace.is_symlink()
        or first_nested_symlink(workspace) is not None
    ):
        return
    with suppress(PathRefused, OSError):
        guard_explainer_path(workspace, root=root or workspace.parent, label="explainer workspace")
        make_writable_tree(workspace)
        shutil.rmtree(workspace)


def remove_workspace_contents(workspace: Path, *, root: Path) -> list[str]:
    """Remove everything but the marker; a symlink or nested symlink is held and reported."""

    held: list[str] = []
    try:
        guard_explainer_path(workspace, root=root, label="explainer workspace")
    except PathRefused as refused:
        return [str(refused)]
    marker = retired_marker(workspace)
    for child in sorted(workspace.iterdir(), key=lambda item: item.name):
        if child == marker:
            continue
        try:
            guard(root, child, label="explainer entry")
        except PathRefused as refused:
            held.append(str(refused))
            continue
        if child.is_symlink():
            held.append(f"{child} is a symlink; it is held, not followed")
            continue
        if child.is_dir():
            nested = first_nested_symlink(child)
            if nested is not None:
                held.append(f"{nested} is a symlink; it is held, not followed")
                continue
            make_writable_tree(child)
            shutil.rmtree(child)
        else:
            with suppress(OSError):
                child.chmod(0o600)
            child.unlink()
    return held


def read_workspace_id(workspace: Path) -> str:
    """Read the host workspace ID from ``seat-provenance.json``, or fallback to ``workspace``."""

    provenance = workspace / INSTRUCTIONS_DIRECTORY / "seat-provenance.json"
    if provenance.is_file() and not provenance.is_symlink():
        with suppress(OSError, ValueError):
            payload = json.loads(provenance.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("workspaceId"), str):
                return payload["workspaceId"]
    return str(workspace) if workspace.is_dir() else ""
