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

from pathlib import Path
from typing import Any

from agents_remember.application.knowledge_worklist.base_cache import default_base_cache_directory
from agents_remember.application.knowledge_writer.base_side import BaseCode, writer_bases
from agents_remember.application.memory_scope import MemoryScope
from agents_remember.memory_quality.converted_check import KnowledgeBasePort
from agents_remember.memory_quality.knowledge_validator.trees import KnowledgeTree
from agents_remember.worktrees.worktree_contract import load_contract

__all__ = ["context_check_base", "converted_check_base"]


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
