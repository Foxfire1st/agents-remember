"""Where a task, its frames and a presenter's working folders live (MIK-R105 rules 17, 18).

One module for the paths, because each one is a decision:

* a **task root** is ``<coordination>/tasks/<repository>/<task>`` and nothing else; the repository is
  checked against the configured authority and the task name is one path segment;
* a **published frame** lives under the task's durable reports (``review-frames/<leaf>/<frame>``),
  so a closed workspace removes no page (:mod:`agents_remember.application.review_frame_store`);
* a **presenter working folder** is ordered as the task folders are -- repository, task, leaf, page,
  attempt -- and is the only place a presenter may write; it is outside the task, memory and code;

Every one of those paths is checked *physically* before the caller reads or writes through it: a
symlinked component below the authority root, or a target that resolves outside it, is refused
rather than followed (MIK-R105 rules 4, 5, 17).
"""

from __future__ import annotations

from pathlib import Path

from agents_remember.canvas.presenter_files import create_directory
from agents_remember.kernel.authority import require_repo
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.durable_evidence import durable_reports_root

__all__ = [
    "PRESENTER_DIRECTORY",
    "PRESENTER_WORK_DIRECTORY",
    "TASK_SEGMENT",
    "PathRefused",
    "ensure_directory",
    "guard_path",
    "make_directory",
    "path_segment",
    "presenter_root",
    "presenter_work_base",
    "presenter_working_root",
    "task_root",
]

PRESENTER_DIRECTORY = "review-presenter"
PRESENTER_WORK_DIRECTORY = "presenter"
TASK_SEGMENT = "tasks"


class PathRefused(ValueError):
    """A path was asked for outside the admitted authority."""


def path_segment(value: str, *, name: str) -> str:
    """One path segment, refusing anything that could leave the directory it names."""

    if not value or "/" in value or "\\" in value or value.startswith(".") or value in {".", ".."}:
        raise PathRefused(f"{name} {value!r} is not one path segment")
    return value


def guard_path(root: Path, target: Path, *, label: str = "path") -> Path:
    """Refuse a target with a symlinked component below ``root`` or one resolving outside it.

    The check is made on the *physical* path before any read or write: the lexical target must be
    below ``root``, every component below ``root`` that exists must be a real directory (not a
    link), and the resolved target must stay under the resolved root. ``root`` itself is the
    configured authority and is allowed to be reached through the environment's own links.
    """

    root_path = Path(root)
    target_path = Path(target)
    try:
        relative = target_path.relative_to(root_path)
    except ValueError as error:
        raise PathRefused(
            f"the {label} {target_path} is not inside its admitted root {root_path}"
        ) from error
    resolved_root = root_path.resolve()
    resolved = target_path.resolve()
    if resolved != resolved_root and resolved_root not in resolved.parents:
        raise PathRefused(
            f"the {label} {target_path} resolves outside {resolved_root}; a link or escape is "
            "held, not followed"
        )
    probe = root_path
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink():
            raise PathRefused(
                f"the {label} {target_path} has a symlinked component at {probe}; it is held, "
                "not followed"
            )
    return target_path


def make_directory(root: Path, target: Path, *, label: str = "directory") -> Path:
    """Create one new directory only after its physical path was admitted."""

    guard_path(root, target, label=label)
    create_directory(root, target, exist_ok=False)
    return guard_path(root, target, label=label)


def ensure_directory(root: Path, target: Path, *, label: str = "directory") -> Path:
    """Create a directory if it is absent, after admitting its physical path."""

    guard_path(root, target, label=label)
    create_directory(root, target, exist_ok=True)
    return guard_path(root, target, label=label)


def task_root(config: McpRuntimeConfig, repository_id: str, task: str) -> Path:
    """The one task directory a frame or an attempt may belong to."""

    require_repo(config, repository_id)
    root = config.coordination_root / TASK_SEGMENT / repository_id / path_segment(task, name="task")
    if not root.is_dir():
        raise PathRefused(f"the task {repository_id}/{task} has no directory at {root}")
    return root


def presenter_root(task: Path) -> Path:
    """Where a task's presenter attempts and conversation are durable, beside its other reports."""

    return durable_reports_root(task) / PRESENTER_DIRECTORY


def presenter_work_base(config: McpRuntimeConfig) -> Path:
    """The one root every presenter working folder is ordered under (rule 17)."""

    return config.coordination_root / PRESENTER_WORK_DIRECTORY


def presenter_working_root(  # noqa: PLR0913 - one folder's own ordering segments
    config: McpRuntimeConfig,
    *,
    repository_id: str,
    task: str,
    leaf_id: str,
    frame_id: str,
    attempt_id: str,
) -> Path:
    """The innermost working folder of one page attempt: repository, task, leaf, page, attempt."""

    require_repo(config, repository_id)
    base = presenter_work_base(config)
    path = (
        base
        / path_segment(repository_id, name="repository")
        / path_segment(task, name="task")
        / path_segment(leaf_id, name="leaf")
        / path_segment(frame_id, name="frame")
        / path_segment(attempt_id, name="attempt")
    )
    return guard_path(config.coordination_root, path, label="presenter working folder")
