"""Service ports the worktree lifecycle needs from packages above it.

Worktrees ranks below providers, memory_quality and code_quality, so the
lifecycle modules never import them. The composition layer binds one
``WorktreeServices`` bundle (provider lifecycle, memory-quality gate,
citation-cache guard, knowledge validator,
knowledge crossing) before invoking worktree operations.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from agents_remember.certification.models import RailDefinition
from agents_remember.models.task_document import CanonicalTaskObservation
from agents_remember.worktrees.worktree_contract import WorktreeContract

if TYPE_CHECKING:
    from agents_remember.certification.certificate_models import GateFiveSemanticInputs
    from agents_remember.worktrees.integration.closeout.certification.execution import (
        CloseoutCertificationHandoff,
    )
    from agents_remember.worktrees.integration.closeout.preparation.memory_port import (
        PreparedMemoryCertificationPort,
    )
    from agents_remember.worktrees.modules.models import WorktreeCommandResult


class TerminalGuard(Protocol):
    """The citation-cache terminal fence surface worktrees consume."""

    def preview(self) -> dict[str, object]: ...
    def complete(
        self,
        *,
        outcome: Literal["completed", "abandoned"],
        publish: Callable[[], None],
        rollback_publish: Callable[[], None],
    ) -> Any: ...


class CitationGuardPort(Protocol):
    def guard(
        self,
        contract: WorktreeContract,
        *,
        requested_contract_path: Path,
    ) -> AbstractContextManager[TerminalGuard]: ...


class ProviderLifecyclePort(Protocol):
    def load_settings(self, settings_path: Path) -> dict[str, Any] | None: ...
    def provider_enabled(self, settings: dict[str, Any], provider_id: str) -> bool: ...
    def settings_path(self, settings_path: Path) -> Path: ...
    def setup_request(self, *, spec: ProviderSetupRequestSpec) -> Any: ...
    def cgc_seed_options(
        self,
        *,
        source_coordination_root: Path,
        repo_id: str,
        source_repo_root: Path,
        target_repo_root: Path,
    ) -> Any: ...
    def isolated_cgc_options(self, *, runtime_root: Path) -> Any: ...
    def grepai_seed_options(
        self,
        *,
        source_coordination_root: Path,
        source_settings_path: Path,
        project_id: str,
        target_memory_root: Path | None,
    ) -> Any: ...
    def isolated_grepai_options(
        self,
        *,
        runtime_root: Path,
        project_id: str,
        target_memory_root: Path | None,
        allow_missing_roots: bool,
    ) -> Any: ...
    def run_setup(self, request: Any) -> dict[str, Any]: ...
    def launch_setup(
        self,
        *,
        request: Any,
        contract: WorktreeContract,
        write_state_file: Callable[[dict[str, Any]], Path],
        settings_cleanup: Path | None,
    ) -> dict[str, Any]: ...
    def setup_running(self, contract: WorktreeContract) -> bool: ...
    def setup_status(self, contract: WorktreeContract) -> dict[str, Any] | None: ...
    def teardown(self, contract: WorktreeContract, *, dry_run: bool) -> dict[str, Any]: ...
    def remove_tree(self, path: Path, *, dry_run: bool) -> dict[str, Any]: ...


class CertificationMemoryRailsPort(Protocol):
    """Bound provider of the R11 Gate-5 memory-domain rail population.

    The worktree layer may not import memory_quality; the composition layer
    (application/MCP/CLI) binds an adapter that derives the memory rails from
    the memory checker registries.  profile_id is the admitted selection
    identity the R11 registry profile freezes.
    """

    def memory_rails(self, profile_id: str) -> Sequence[RailDefinition]: ...


class MemoryQualityPort(Protocol):
    def observe_contract_task(self, contract: WorktreeContract) -> CanonicalTaskObservation: ...
    def check_groups(self) -> tuple[tuple[str, ...], tuple[str, ...]]: ...
    def drift_context(
        self,
        code_repository_root: Path,
        context: Any,
        detail_limit: int,
        *,
        unstamped_code_commit: str | None = None,
    ) -> Any: ...
    def run_check(
        self,
        onboarding_root: Path,
        *,
        checks: tuple[str, ...] | None,
        drift_context: Any,
    ) -> dict[str, Any]: ...


class KnowledgeValidationPort(Protocol):
    """The mandatory knowledge validator (MIK-R22), as a memory commit route calls it.

    The worktree layer may not import memory_quality; the composition layer binds
    ``memory_quality.knowledge_validator.commit_route.GitKnowledgeValidation``. It returns the
    refusal naming every violation, or ``None`` when the candidate tree may be committed.
    """

    def refusal(
        self,
        *,
        memory_repository: Path,
        candidate_tree: str,
        bases: Sequence[str],
        code_repository: Path,
        code_commit: str,
    ) -> str | None: ...


@dataclass(frozen=True)
class CrossingPlanView:
    """A crossing sync's knowledge half as the sync applies it (MIK-R24 rule 8).

    ``files`` maps every ``knowledge/`` and ``onboarding/`` path of the merged tree to its bytes
    (``None`` = absent); ``conflicts`` are ``(path, item, reason)`` triples for the curator, and
    ``conflict_versions`` the converted (base, own, incoming) bytes of each conflicted path.
    """

    files: dict[str, bytes | None]
    conflicts: tuple[tuple[str, str, str], ...]
    conflict_versions: dict[str, tuple[bytes | None, bytes | None, bytes | None]]
    report: dict[str, Any]


class CrossingStepFailed(RuntimeError):
    """A crossing step failed before the line was touched; the message names the step."""


@dataclass(frozen=True)
class CrossingRequest:
    """One crossing sync's three memory commits, its paired code commit, and who performs it."""

    memory_repository: Path
    sides: tuple[str, str, str]
    """(merge base, own side, incoming side) commits."""
    code_repository: Path
    code_commit: str
    owner_kind: Literal["leaf", "master"]
    owner_id: str


class KnowledgeCrossingPort(Protocol):
    """Rule 8 steps 1-4 over three memory commits; bound by the composition layer."""

    def plan(self, request: CrossingRequest) -> CrossingPlanView: ...


class CertificationContinuationPort(Protocol):
    """Composition-owned Gate 5 and finalization boundaries after exact code certificates."""

    def observe_memory(
        self, handoff: CloseoutCertificationHandoff
    ) -> GateFiveSemanticInputs | None:
        """Read and verify current memory authority; absence cannot authorize Gate-5 reuse."""
        ...

    def run_memory(self, handoff: CloseoutCertificationHandoff) -> WorktreeCommandResult: ...
    def finalize(self, handoff: CloseoutCertificationHandoff) -> WorktreeCommandResult: ...


@dataclass(frozen=True)
class WorktreeServices:
    provider_lifecycle: ProviderLifecyclePort
    memory_quality: MemoryQualityPort
    citation_guard: CitationGuardPort
    certification_memory_rails: CertificationMemoryRailsPort | None = None
    certification_continuation: CertificationContinuationPort | None = None
    prepared_memory_certification: PreparedMemoryCertificationPort | None = None
    knowledge_validation: KnowledgeValidationPort | None = None
    knowledge_crossing: KnowledgeCrossingPort | None = None


@dataclass(frozen=True)
class ProviderSetupRequestSpec:
    """Everything one provider setup request needs, as the worktree layer sees it.

    The provider-owned option payloads (cgc/grepai seed and isolation options)
    are opaque to worktrees: the port builds them from the primitive fields.
    """

    action: str
    coordination_root: Path
    settings_path: Path
    timeout: int
    dry_run: bool
    skip_grepai: bool
    cgc_seed: Any
    cgc_isolated: Any
    grepai_seed: Any
    grepai_isolated: Any


_current: WorktreeServices | None = None


class WorktreeServicesUnboundError(RuntimeError):
    """Raised when a worktree operation needs services that were never bound."""


def bind_worktree_services(services: WorktreeServices) -> None:
    """Bind the composition-provided services for this process."""
    global _current  # noqa: PLW0603  # module-level composition binding
    _current = services


def reset_worktree_services() -> None:
    """Clear the bound services (tests and process teardown)."""
    global _current  # noqa: PLW0603  # module-level composition binding
    _current = None


def worktree_services() -> WorktreeServices:
    if _current is None:
        raise WorktreeServicesUnboundError(
            "worktree services are not bound; the MCP/CLI composition must bind them"
        )
    return _current


__all__ = [
    "CertificationContinuationPort",
    "CertificationMemoryRailsPort",
    "CitationGuardPort",
    "CrossingPlanView",
    "CrossingRequest",
    "CrossingStepFailed",
    "KnowledgeCrossingPort",
    "KnowledgeValidationPort",
    "MemoryQualityPort",
    "ProviderLifecyclePort",
    "TerminalGuard",
    "WorktreeServices",
    "WorktreeServicesUnboundError",
    "bind_worktree_services",
    "reset_worktree_services",
    "worktree_services",
]
