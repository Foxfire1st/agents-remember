"""Automatic code-tree pairing and observation for ``knowledge_read``.

Resolves which code checkout a read observes with explicit precedence:

1. Caller-supplied ``repositoryRoot`` (or ``codeTreeId`` / ``working-tree``) wins unchanged.
2. Leaf ``contractPath`` or enclosure sibling code worktree (so leaf anchors are judged against
   the leaf's own code worktree, never the official branch checkout).
3. Configured ``repo_id`` or ``memoryRoot`` matched against ``config.repositories`` (enabling
   zero-flag pairing on Projects-scoped mounts where ``workspace_root`` is not a Git repository).
4. Existing ``workspace_root`` fallback when it is a readable Git checkout; unconfigured memory
   paths never refuse and never guess by bare directory-name coincidence.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_currentness import CodeTree
from agents_remember.application.worktree_code_state import (
    CODE_STATE_COMMIT,
    CODE_STATE_WORKING_TREE,
    WORKING_TREE_SELECTOR,
    CodeState,
    WorktreeCapture,
    capture_worktree,
    hold_observed_code_tree,
)
from agents_remember.kernel.git_command import git_environment
from agents_remember.kernel.primitives.runtime_config import RepositoryScope

_GIT_TIMEOUT_SECONDS = 5
_ENCLOSURE_MANIFEST_PARTS = (".lifecycle", "enclosure-manifest.json")


class NoCodeTreeError(ValueError):
    """Raised when an explicit ``repositoryRoot`` (or ``working-tree``) has no readable tree."""

    def __init__(self, repository_root: str) -> None:
        super().__init__(
            f"repositoryRoot {repository_root} resolves no code tree to read anchors at (it has no "
            "commit, or it is not a Git repository); name a repository with a commit, add "
            "codeTreeId, or omit repositoryRoot"
        )


@dataclass(frozen=True)
class PairingContext:
    """Configuration context used to pair a memory checkout with its code repository."""

    repositories: Mapping[str, RepositoryScope] | None = None
    workspace_root: str | None = None


@dataclass
class ObservedCodeSource:
    """The code source one read observes, and any working-tree capture held for its duration."""

    repository_root: str | None
    code_tree_id: str | None
    code_state: CodeState | None
    capture: WorktreeCapture | None = None

    def close(self) -> None:
        if self.capture is not None:
            self.capture.close()
            self.capture = None


def resolve_paired_code_root(
    request: Any,
    *,
    context: PairingContext | None = None,
) -> str | None:
    """Resolve the paired code repository root for ``request`` without mutating anything."""

    explicit_root: str | None = getattr(request, "repository_root", None)
    if explicit_root is not None:
        return explicit_root
    memory_root: str = getattr(request, "memory_root", "")
    contract_path: str | None = getattr(request, "contract_path", None)
    repo_id: str | None = getattr(request, "repo_id", None)
    pairing = context if context is not None else PairingContext()

    leaf_root = _leaf_code_worktree(memory_root, contract_path=contract_path)
    if leaf_root is not None:
        return str(leaf_root)
    configured_root = _configured_code_root(
        memory_root, repo_id=repo_id, repositories=pairing.repositories
    )
    if configured_root is not None:
        return str(configured_root)
    return pairing.workspace_root


def observe_code_source(
    request: Any,
    *,
    repositories: Mapping[str, RepositoryScope] | None = None,
    workspace_root: str | None = None,
) -> ObservedCodeSource:
    """Resolve and observe the code tree for ``request`` (capturing dirty worktrees when needed)."""

    named_tree: str | None = getattr(request, "code_tree_id", None)
    explicit_root: str | None = getattr(request, "repository_root", None)
    paired_root = resolve_paired_code_root(
        request,
        context=PairingContext(repositories=repositories, workspace_root=workspace_root),
    )

    if named_tree == WORKING_TREE_SELECTOR:
        if paired_root is None:
            raise NoCodeTreeError("the working-tree selector with no repository root")
        capture = capture_worktree(Path(paired_root))
        return ObservedCodeSource(paired_root, capture.tree_id, CODE_STATE_WORKING_TREE, capture)

    if named_tree is not None:
        if paired_root is None:
            return ObservedCodeSource(None, None, None)
        return ObservedCodeSource(paired_root, named_tree, CODE_STATE_COMMIT)

    return _observe_unpinned_root(paired_root, explicit_root=explicit_root)


def _observe_unpinned_root(
    paired_root: str | None, *, explicit_root: str | None
) -> ObservedCodeSource:
    if paired_root is None:
        return ObservedCodeSource(None, None, None)
    observed, capture = hold_observed_code_tree(Path(paired_root))
    if observed is None:
        if explicit_root is not None:
            raise NoCodeTreeError(explicit_root)
        return ObservedCodeSource(None, None, None)
    return ObservedCodeSource(paired_root, observed.tree_id, observed.code_state, capture)


def code_tree_of(source: ObservedCodeSource | None) -> CodeTree | None:
    """Convert an observed code source into a ``CodeTree`` preserving dirty ``object_directory``."""

    if source is None or source.repository_root is None or source.code_tree_id is None:
        return None
    object_directory = None if source.capture is None else source.capture.object_directory
    return CodeTree(Path(source.repository_root), source.code_tree_id, object_directory)


def _leaf_code_worktree(memory_root: str, *, contract_path: str | None) -> Path | None:
    if contract_path:
        loaded = _code_worktree_from_contract(Path(contract_path))
        if loaded is not None:
            return loaded
    try:
        resolved_memory = Path(memory_root).resolve()
    except OSError:
        return None
    manifest_path = resolved_memory.parent.joinpath(*_ENCLOSURE_MANIFEST_PARTS)
    if manifest_path.is_file():
        loaded = _code_worktree_from_manifest(manifest_path, resolved_memory)
        if loaded is not None:
            return loaded
    if resolved_memory.name.startswith("memory-"):
        slug = resolved_memory.name.removeprefix("memory-")
        if slug:
            sibling = resolved_memory.parent / slug
            if sibling.is_dir() and (sibling / ".git").exists():
                return sibling
    return None


def _code_worktree_from_manifest(manifest_path: Path, resolved_memory: Path) -> Path | None:
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    contract_ref = raw.get("contractPath")
    if not isinstance(contract_ref, str) or not contract_ref.strip():
        return None
    return _code_worktree_from_contract(Path(contract_ref), expected_memory=resolved_memory)


def _code_worktree_from_contract(
    contract_path: Path, *, expected_memory: Path | None = None
) -> Path | None:
    try:
        module = import_module("agents_remember.worktrees.worktree_contract")
        contract = module.load_contract(contract_path)
    except Exception:
        return None
    if expected_memory is not None:
        memory_wt = getattr(contract, "memory_worktree", None)
        if memory_wt is None:
            return None
        try:
            if Path(memory_wt).resolve() != expected_memory:
                return None
        except OSError:
            return None
    code_wt = getattr(contract, "code_worktree", None)
    if code_wt is None:
        return None
    code_path = Path(code_wt)
    return code_path if code_path.is_dir() else None


def _configured_code_root(
    memory_root: str,
    *,
    repo_id: str | None,
    repositories: Mapping[str, RepositoryScope] | None,
) -> Path | None:
    if not repositories:
        return None
    if repo_id is not None:
        scope = repositories.get(repo_id)
        if scope is not None and scope.path.is_dir():
            return scope.path
    try:
        resolved_memory = Path(memory_root).resolve()
    except OSError:
        return None
    return _match_memory_path(resolved_memory, repositories) or _match_memory_common_dir(
        resolved_memory, repositories
    )


def _match_memory_path(
    resolved_memory: Path, repositories: Mapping[str, RepositoryScope]
) -> Path | None:
    for scope in repositories.values():
        if scope.memory_root is None:
            continue
        try:
            if Path(scope.memory_root).resolve() == resolved_memory and scope.path.is_dir():
                return scope.path
        except OSError:
            continue
    return None


def _match_memory_common_dir(
    resolved_memory: Path, repositories: Mapping[str, RepositoryScope]
) -> Path | None:
    common_dir = _git_common_dir(resolved_memory)
    if common_dir is None:
        return None
    for scope in repositories.values():
        if scope.memory_root is None:
            continue
        scope_common = _git_common_dir(scope.memory_root)
        if scope_common is not None and scope_common == common_dir and scope.path.is_dir():
            return scope.path
    return None


def _git_common_dir(directory: Path) -> Path | None:
    if not (directory / ".git").exists():
        return None
    try:
        completed = subprocess.run(
            ["git", "-C", str(directory), "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_TIMEOUT_SECONDS,
            env=git_environment(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    raw = completed.stdout.strip()
    if not raw:
        return None
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = directory / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


__all__ = [
    "NoCodeTreeError",
    "ObservedCodeSource",
    "PairingContext",
    "code_tree_of",
    "observe_code_source",
    "resolve_paired_code_root",
]
