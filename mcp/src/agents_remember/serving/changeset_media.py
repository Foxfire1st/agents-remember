"""Where one side of a change-set file lives, for the bytes route.

The diff route already resolves a file's two sides from its selectors (scope, leaf, master,
comparison). The bytes route takes the same selectors and one side name, and this module
turns them into the exact place to read: a repository and a revision, or the working tree.
The client never names a commit or a revision -- a selector names the change-set and the
server finds its state itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.sidecar_pairing import confine_rel
from agents_remember.models.knowledge_files.shapes import require_repository_path
from agents_remember.serving.changeset import (
    ChangesetFileRef,
    _load_leaf_contract,
    _require_contract,
)
from agents_remember.serving.changeset_comparison import (
    _range,
    comparison_file_path,
    recorded_comparison,
)
from agents_remember.serving.changeset_endpoints import recorded_committed_range
from agents_remember.serving.changeset_reads import head_commit
from agents_remember.serving.master_net_generation import (
    MasterNetPins,
    load_master_contract,
    select_master_net,
)
from agents_remember.serving.media import MediaSource, MediaValidationError, working_media
from agents_remember.serving.media_sources import git_media, is_object_id
from agents_remember.serving.scope import FileScope


@dataclass(frozen=True)
class SideLocation:
    """One side of a changed file: ``revision`` None means the live working tree."""

    repository: Path
    revision: str | None
    path: str


def scope_side_location(scope: FileScope, kind: str, path: str, side: str) -> SideLocation:
    """The side of a file in one active enclosure's ``base -> worktree`` range."""

    contract = _require_contract(scope)
    memory = kind == "memory"
    root = contract.memory_worktree if memory else scope.code_root
    if root is None:
        raise FileNotFoundError(path)
    base = contract.memory_base_commit if memory else contract.code_base_commit
    return SideLocation(root, base, path) if side == "before" else SideLocation(root, None, path)


def selector_side_location(
    config: McpRuntimeConfig, ref: ChangesetFileRef, side: str
) -> SideLocation:
    """The side of a file selected by ``leaf``, ``master`` or ``comparison``.

    The three selectors resolve exactly as the diff route resolves them, so the bytes route
    can never draw a different revision from the one the diff showed.
    """

    if ref.comparison is not None:
        return _comparison_location(config, ref, side)
    if ref.leaf:
        return _leaf_location(config, ref, side)
    if ref.master:
        return _master_location(config, ref, side)
    raise FileNotFoundError(ref.path)


def _comparison_location(
    config: McpRuntimeConfig, ref: ChangesetFileRef, side: str
) -> SideLocation:
    contract = _load_leaf_contract(config, ref.repo, ref.master, ref.leaf)
    if contract is None:
        raise FileNotFoundError(f"no leaf contract for {ref.leaf!r}")
    assert ref.comparison is not None
    record = recorded_comparison(contract, ref.comparison)
    repository, base, tip = _range(record, ref.kind == "memory")
    rel = comparison_file_path(ref.path, memory=ref.kind == "memory")
    return SideLocation(repository, base if side == "before" else tip, rel)


def _leaf_location(config: McpRuntimeConfig, ref: ChangesetFileRef, side: str) -> SideLocation:
    contract = _load_leaf_contract(config, ref.repo, ref.master, ref.leaf)
    if contract is None:
        raise FileNotFoundError(f"no leaf contract for {ref.leaf!r}")
    memory = ref.kind == "memory"
    if ref.mode == "working":
        worktree = contract.memory_worktree if memory else contract.code_worktree
        if worktree is None or not worktree.exists():
            raise FileNotFoundError("no live worktree for the working change-set")
        revision = head_commit(worktree, "HEAD") if side == "before" else None
        return SideLocation(worktree, revision, ref.path)
    recorded = recorded_committed_range(contract, memory=memory)
    revision = recorded.base_commit if side == "before" else recorded.head_commit
    return SideLocation(recorded.repository, revision, ref.path)


def _master_location(config: McpRuntimeConfig, ref: ChangesetFileRef, side: str) -> SideLocation:
    pins = MasterNetPins(
        code_base=ref.code_base,
        code_tip=ref.code_tip,
        memory_base=ref.memory_base,
        memory_tip=ref.memory_tip,
    )
    selection = select_master_net(config, ref.repo, ref.master, pins)
    contract = load_master_contract(config, ref.repo, ref.master)
    if selection is None or contract is None:
        raise FileNotFoundError(f"no series contract for {ref.master!r}")
    endpoints = selection.endpoints
    if ref.kind == "memory":
        repository = contract.memory_repo_path
        base, tip = endpoints.memory_base, endpoints.memory_tip
    else:
        repository = contract.code_repo_path
        base, tip = endpoints.code_base, endpoints.code_tip
    if repository is None or not base or not tip:
        raise FileNotFoundError(ref.path)
    return SideLocation(repository, base if side == "before" else tip, ref.path)


def require_selector(ref: ChangesetFileRef, side: str) -> None:
    """Refuse a side name or a client-supplied pin that is not a plain value.

    A pin is an object id: anything else (including text shaped like a Git option) is a bad
    request rather than a value handed to Git. The side is one of the two names the diff
    route publishes.
    """

    if side not in ("before", "after"):
        raise MediaValidationError("side must be before or after")
    if ref.kind not in ("code", "memory"):
        raise MediaValidationError("kind must be code or memory")
    if ref.mode and ref.mode not in ("working", "committed"):
        raise MediaValidationError("mode must be working or committed")
    for pin in (ref.code_base, ref.code_tip, ref.memory_base, ref.memory_tip):
        if pin and not is_object_id(pin):
            raise MediaValidationError(f"pin {pin!r} is not an object id")


def media_for_location(location: SideLocation) -> MediaSource:
    """The media source of one resolved side; a working-tree side is read from disk."""

    if location.revision is None:
        # The working tree is a live filesystem: resolve aliases and confine to its root.
        rel = confine_rel(location.repository, location.path)
        if rel.startswith("-"):
            raise MediaValidationError(f"path {rel!r} is not a plain repository-relative path")
        return working_media(location.repository / rel)
    # A historical side is a path inside an immutable tree. Confine it lexically: today's
    # filesystem aliases (or a symlink added later in the working tree) must not decide which
    # object a revision's path names.
    rel = require_repository_path(location.path)
    return git_media(location.repository, location.revision, rel)
