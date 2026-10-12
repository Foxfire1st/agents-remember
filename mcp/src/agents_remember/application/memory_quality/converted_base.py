"""The ``knowledge.converted`` check's comparison base: one base for every consumer (MIK-R24 rule 7).

The memory-quality run validates the converted working tree against the memory worktree's ``HEAD``.
When ``HEAD`` is unconverted -- the converting leaf before its closeout commits the conversion, or a
line that has crossed -- the base is ``HEAD``'s conversion, exactly as the gate, the worklist and the
writer see it: :func:`~agents_remember.application.knowledge_writer.base_side.writer_bases`, read
through the same cache under the same key (the memory commit, the version, and K_B's own
``Code-Commit`` or the leaf's code base B). A carried reference to a file the candidate deleted or
moved is then reported, not refused as a newly written anchor.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_worklist.base_cache import (
    converted_base_files,
    default_base_cache_directory,
)
from agents_remember.application.knowledge_writer.base_side import BaseCode, writer_bases
from agents_remember.application.memory_scope import MemoryScope
from agents_remember.kernel.git_command import GitPreparationError
from agents_remember.memory.conversion.base import pinned_version
from agents_remember.memory_quality.converted_check import KnowledgeBasePort
from agents_remember.memory_quality.knowledge_validator.trees import (
    KnowledgeTree,
    KnowledgeTreeReadError,
    knowledge_tree_from_git,
)
from agents_remember.memory_quality.landing_report import (
    LandingBasePort,
    LeafBaseRequest,
    ResolvedLeafBase,
    git_base_code_paths,
    git_named_renames_worktree,
    git_tree_entries,
    is_landing_card,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract

__all__ = [
    "context_check_base",
    "converted_check_base",
    "leaf_base_port",
    "leaf_base_request",
    "leaf_base_request_for_context",
]


def _port(code: BaseCode) -> KnowledgeBasePort:
    def base(
        memory_root: Path, candidate: KnowledgeTree
    ) -> tuple[KnowledgeTree | None, str | None]:
        sides = writer_bases(memory_root, candidate, code)
        return sides.base, sides.problem

    return base


def converted_check_base(scope: MemoryScope, coordination_root: Path) -> KnowledgeBasePort:
    """The base port of ``scope``'s memory-quality run."""

    contract = scope.contract
    return _port(
        BaseCode(
            root=scope.quality_code_root,
            fallback=(contract.code_base_commit or None) if contract is not None else None,
            cache_directory=default_base_cache_directory(coordination_root),
        )
    )


def context_check_base(code_root: Path, context: Any) -> KnowledgeBasePort | None:
    """The base port of a run that holds a coordination context (the closeout's quality phases).

    B is the code base of the contract the context names, when it names one. A context without a
    coordination root (a bare diagnostic) gets no port: the check then uses a converted ``HEAD``.
    """

    coordination_root = getattr(context, "coordination_root", None)
    if not isinstance(coordination_root, Path):
        return None
    contract_path = getattr(context, "contract_path", None)
    fallback = None
    if isinstance(contract_path, Path) and contract_path.is_file():
        fallback = load_contract(contract_path).code_base_commit or None
    return _port(
        BaseCode(
            root=code_root,
            fallback=fallback,
            cache_directory=default_base_cache_directory(coordination_root),
        )
    )


def _usable_path(value: Any) -> Path | None:
    """``value`` as a path, or ``None`` when it names none.

    Test doubles carry mock attributes where a contract carries paths; those
    bind no leaf base instead of raising inside the check.
    """

    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value:
        return Path(value)
    if isinstance(value, os.PathLike):
        try:
            return Path(os.fspath(value))
        except TypeError:
            return None
    return None


def leaf_base_request(
    contract: WorktreeContract | None, coordination_root: Path | None
) -> LeafBaseRequest | None:
    """The leaf's admitted memory base for the landing report, or ``None``.

    ``None`` where no leaf contract names a memory base commit: runs without
    one keep the run's comparison base for the landing report.
    """

    if contract is None:
        return None
    base_commit = contract.memory_base_commit
    if not isinstance(base_commit, str) or not base_commit:
        return None
    memory_repository = _usable_path(contract.memory_repo_path or contract.memory_worktree)
    if memory_repository is None:
        return None
    code_repository = _usable_path(contract.code_worktree or contract.code_repo_path)
    code_base_commit = contract.code_base_commit
    return LeafBaseRequest(
        memory_repository=memory_repository,
        base_commit=base_commit,
        code_repository=code_repository,
        code_base_commit=(
            code_base_commit if isinstance(code_base_commit, str) and code_base_commit else None
        ),
        cache_directory=(
            None if coordination_root is None else default_base_cache_directory(coordination_root)
        ),
    )


def leaf_base_request_for_context(context: Any) -> LeafBaseRequest | None:
    """The admitted base of the contract ``context`` names, or ``None``."""

    contract_path = getattr(context, "contract_path", None)
    if not isinstance(contract_path, Path) or not contract_path.is_file():
        return None
    try:
        contract = load_contract(contract_path)
    except (ValueError, OSError):
        return None
    coordination_root = getattr(context, "coordination_root", None)
    return leaf_base_request(
        contract, coordination_root if isinstance(coordination_root, Path) else None
    )


def _resolve_leaf_base_cards(
    request: LeafBaseRequest, candidate: KnowledgeTree
) -> tuple[KnowledgeTree | None, str | None]:
    """The request's base commit as the candidate compares with it.

    An already-converted base (or an unconverted candidate) is read as it
    stands; otherwise the base is converted exactly as the gate converts
    its own comparison base (MIK-R24 rule 7).
    """

    try:
        base = knowledge_tree_from_git(
            request.memory_repository,
            request.base_commit,
            label=f"landing base {request.base_commit}",
        )
    except (
        KnowledgeTreeReadError,
        GitPreparationError,
        KeyError,
        ValueError,
        OSError,
        subprocess.SubprocessError,
    ) as error:
        return None, (
            f"the leaf's admitted memory base {request.base_commit} cannot be read: {error}"
        )
    if base.converted or not candidate.converted:
        return base, None
    version = pinned_version(candidate)
    if version is None or request.code_repository is None:
        return None, (
            "the leaf's admitted memory base is unconverted and cannot be converted "
            "without the candidate's conversion version and paired code tree"
        )
    try:
        files = converted_base_files(
            request.memory_repository,
            request.base_commit,
            code=(request.code_repository, request.code_base_commit or "HEAD"),
            version=version,
            cache_directory=request.cache_directory,
        )
    except (ValueError, KeyError, OSError) as error:
        return None, (f"the converted base of {request.base_commit} cannot be built: {error}")
    return KnowledgeTree(label=f"converted landing base {request.base_commit}", files=files), None


def leaf_base_port(request: LeafBaseRequest) -> LandingBasePort:
    """Bind ``request`` as the landing report's leaf-base resolver."""

    def resolve(candidate: KnowledgeTree) -> ResolvedLeafBase:
        try:
            entries = git_tree_entries(request.memory_repository, request.base_commit)
        except (
            KnowledgeTreeReadError,
            GitPreparationError,
            KeyError,
            ValueError,
            OSError,
            subprocess.SubprocessError,
        ):
            return ResolvedLeafBase(
                None,
                f"the leaf's admitted memory base {request.base_commit} cannot be read",
            )
        cards, problem = _resolve_leaf_base_cards(request, candidate)
        code_paths: frozenset[str] | None = None
        renames: dict[str, str] = {}
        if request.code_repository is not None and request.code_base_commit:
            code_paths = git_base_code_paths(request.code_repository, request.code_base_commit)
            if cards is not None:
                renames = git_named_renames_worktree(
                    request.code_repository,
                    request.code_base_commit,
                    {path for path in cards.files if is_landing_card(path)},
                    {path for path in candidate.files if is_landing_card(path)},
                )
        return ResolvedLeafBase(
            cards=cards,
            problem=problem,
            paths=frozenset(entries),
            code_paths=code_paths,
            renames=renames,
        )

    return resolve
