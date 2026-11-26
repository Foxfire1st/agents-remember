"""Default WorktreeServices composition for the MCP/CLI entry points."""

from __future__ import annotations

from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

from agents_remember.application.file_briefs.triggers import refresh_written_memory_folder
from agents_remember.application.knowledge_gate import KnowledgeGate
from agents_remember.application.knowledge_worklist import LeafWorklistRecompute
from agents_remember.application.memory_quality.converted_base import (
    context_check_base,
    leaf_base_port,
    leaf_base_request_for_context,
)
from agents_remember.application.review_artifact_cleanup import ReviewArtifactCleanup
from agents_remember.memory.conversion.base import GitBaseConverter
from agents_remember.memory.conversion.crossing_port import GitKnowledgeCrossing
from agents_remember.memory_quality import check as memory_quality_check_api
from agents_remember.memory_quality.gate_five_rails import gate_five_memory_rails
from agents_remember.memory_quality.incremental_scope.candidate import observe_contract_task
from agents_remember.memory_quality.knowledge_validator.commit_route import (
    GitKnowledgeValidation,
)
from agents_remember.memory_quality.style.citations import (
    source_index_cache as citation_cache_api,
)
from agents_remember.models.task_document import CanonicalTaskObservation
from agents_remember.worktrees.knowledge_validation import PairedCode
from agents_remember.worktrees.services import (
    LeafAgentArchivePort,
    TerminalGuard,
    WorktreeServices,
    bind_worktree_services,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract


class CertificationMemoryRailsAdapter:
    """memory_quality-backed implementation of :class:."""

    def memory_rails(self, profile_id: str):
        return gate_five_memory_rails(profile_id)


class MemoryQualityAdapter:
    """memory_quality-backed implementation of :class:`MemoryQualityPort`."""

    def observe_contract_task(self, contract: WorktreeContract) -> CanonicalTaskObservation:
        return observe_contract_task(contract)

    def check_groups(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return (
            tuple(memory_quality_check_api.BEFORE_METADATA_REFRESH_CHECKS),
            tuple(memory_quality_check_api.AFTER_METADATA_REFRESH_CHECKS),
        )

    def drift_context(
        self,
        code_repository_root: Path,
        context: Any,
        detail_limit: int,
        *,
        unstamped_code_commit: str | None = None,
    ) -> Any:
        leaf_request = leaf_base_request_for_context(context)
        return memory_quality_check_api.DriftCheckContext(
            code_repository_root=code_repository_root,
            context=context,
            detail_limit=detail_limit,
            unstamped_code_commit=unstamped_code_commit,
            knowledge_base=context_check_base(code_repository_root, context),
            landing_base=(None if leaf_request is None else leaf_base_port(leaf_request)),
        )

    def run_check(
        self,
        onboarding_root: Path,
        *,
        checks: tuple[str, ...] | None,
        drift_context: Any,
    ) -> dict[str, Any]:
        return memory_quality_check_api.run_memory_quality_check(
            onboarding_root,
            checks=list(checks) if checks is not None else None,
            drift_context=drift_context,
        )


class CitationGuardAdapter:
    """memory_quality-backed implementation of :class:`CitationGuardPort`."""

    def guard(
        self,
        contract: WorktreeContract,
        *,
        requested_contract_path: Path,
        completed_contract_with_remains: bool = False,
    ) -> AbstractContextManager[TerminalGuard]:
        return citation_cache_api.terminal_namespace_guard(
            contract,
            requested_contract_path=requested_contract_path,
            completed_contract_with_remains=completed_contract_with_remains,
        )


def _file_briefs_problem(
    memory_root: Path, paired_code: PairedCode, coordination_root: Path
) -> str | None:
    writing = refresh_written_memory_folder(memory_root, paired_code, coordination_root)
    return writing if isinstance(writing, str) else None


def build_default_worktree_services(
    *, leaf_agent_archive: LeafAgentArchivePort | None = None
) -> WorktreeServices:
    return WorktreeServices(
        memory_quality=MemoryQualityAdapter(),
        citation_guard=CitationGuardAdapter(),
        certification_memory_rails=CertificationMemoryRailsAdapter(),
        knowledge_validation=GitKnowledgeValidation(base_converter=GitBaseConverter()),
        knowledge_crossing=GitKnowledgeCrossing(),
        knowledge_worklist=LeafWorklistRecompute(),
        knowledge_gate=KnowledgeGate(),
        review_artifact_cleanup=ReviewArtifactCleanup(),
        leaf_agent_archive=leaf_agent_archive,
        file_briefs=_file_briefs_problem,
    )


__all__ = [
    "CertificationMemoryRailsAdapter",
    "CitationGuardAdapter",
    "MemoryQualityAdapter",
    "bind_worktree_services",
    "build_default_worktree_services",
]
