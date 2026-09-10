"""Resolve the one canonical memory/code scope used by memory-facing tools."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal, NoReturn

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
    admit_configured_contract,
    project_configured_contract_refusal,
)
from agents_remember.errors import (
    AuthorityError,
    MemoryCandidatePairError,
    MemoryCandidatePairFailure,
)
from agents_remember.kernel.authority import require_repo, require_within_coordination
from agents_remember.kernel.coordination_context.models import CoordinationRequest
from agents_remember.kernel.coordination_context_resolver import (
    CoordinationContext,
    CoordinationHints,
    EnclosureSelector,
    resolve_coordination_context,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.memory_quality.curator_checklist import report_path_for
from agents_remember.memory_quality.future_code_candidate import (
    capture_future_code_candidate,
)
from agents_remember.memory_quality.memory_candidate_pair import (
    resolve_memory_candidate_pair,
)
from agents_remember.memory_quality.style.citations import source_index_cache
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.lifecycles.prepared_memory import PreparedCodeExecutionView
from agents_remember.worktrees.git_worktree_manager import contract_context
from agents_remember.worktrees.integration.closeout.preparation.code_view import (
    observe_selected_prepared_code_view,
)
from agents_remember.worktrees.integration.closeout.preparation_selection import (
    selected_prepared_code_history_commits,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    located_lifecycle_operation_store,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location_errors import (
    LifecycleOperationLocationError,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationReadError,
)
from agents_remember.worktrees.modules.contract_reader import WorktreeContractReader
from agents_remember.worktrees.worktree_contract import WorktreeContract, load_contract


@dataclass(frozen=True)
class MemoryScopeIdentity:
    """Canonical authority and resolved trees that distinguish one quality scope."""

    authority: Literal["official", "leaf"]
    authority_path: str
    code_root: str
    onboarding_root: str
    unstamped_code_commit: str | None = None
    pair_identity: MemoryCandidatePairIdentity | None = None


@dataclass(frozen=True)
class MemoryScope:
    """One resolved memory tree, its measured code tree, and stable request identity."""

    repo_id: str
    identity: MemoryScopeIdentity
    code_root: Path
    onboarding_root: Path
    context: CoordinationContext
    cache_authority: source_index_cache.ManagedCacheAuthority | None = None
    unstamped_code_commit: str | None = None
    curator_report_path: Path | None = None
    contract: WorktreeContract | None = None
    pair_identity: MemoryCandidatePairIdentity | None = None
    prepared_code_view: PreparedCodeExecutionView | None = None
    prepared_code_history_commits: tuple[str, ...] = ()

    @property
    def quality_code_root(self) -> Path:
        """Return the proved code root for quality reads, preserving pair identity."""

        if self.prepared_code_view is None:
            return self.code_root
        return Path(self.prepared_code_view.physicalCodeRoot)

    @property
    def quality_context(self) -> CoordinationContext:
        """Use the selected physical source only for quality/coherence reads."""

        if self.prepared_code_view is None:
            return self.context
        return replace(self.context, code_repository_root=self.quality_code_root)


def resolve_memory_scope(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str | None,
) -> MemoryScope:
    """Resolve official or enclosure-local memory through configured authority."""

    repo = require_repo(config, repo_id)
    if contract_path is not None:
        return resolve_leaf_memory_scope(config, repo, contract_path)
    if repo.memory_root is None:
        raise ValueError(f"repo_id {repo_id!r} does not have a memory root")
    onboarding_root = repo.memory_root / "onboarding"
    return MemoryScope(
        repo_id=repo.repo_id,
        identity=MemoryScopeIdentity(
            authority="official",
            authority_path=(repo.contract_path or onboarding_root).resolve().as_posix(),
            code_root=repo.path.resolve().as_posix(),
            onboarding_root=onboarding_root.resolve().as_posix(),
        ),
        code_root=repo.path,
        onboarding_root=onboarding_root,
        context=resolve_coordination_context(
            code_repository_name=repo.repo_id,
            workspace_root=config.workspace_root,
            code_repository_root=repo.path,
            request=CoordinationRequest(
                hints=CoordinationHints(
                    coordination_root=config.coordination_root,
                    onboarding_root=onboarding_root,
                ),
                selector=EnclosureSelector(contract_path=repo.contract_path),
                contract_reader=WorktreeContractReader(),
            ),
        ),
    )


def resolve_leaf_memory_scope(
    config: McpRuntimeConfig,
    repo: RepositoryScope,
    contract_path: str,
) -> MemoryScope:
    """Resolve one leaf enclosure without falling back to official memory."""

    path = require_within_coordination(config, contract_path, "contract_path")
    contract = load_contract(path)
    if contract.kind != "leaf":
        raise AuthorityError(
            f"contract_path must name a leaf worktree contract, not {contract.kind!r}"
        )
    if contract.repo_name != repo.repo_id:
        raise AuthorityError(
            f"contract_path names repo {contract.repo_name!r} but repo_id is {repo.repo_id!r}; "
            f"pass the repo_id this contract was started for ({path.as_posix()})"
        )
    if contract.memory_worktree is None:
        raise ValueError(
            f"contract {path.as_posix()} carries no memory worktree (memory_mode is "
            f"{contract.memory_mode!r}), so it has no memory tree of its own to check; drop "
            "contract_path to check the official memory repo deliberately"
        )
    return _leaf_scope(repo, contract)


def resolve_memory_candidate_scope(
    config: McpRuntimeConfig,
    *,
    repo_id: str,
    contract_path: str,
) -> MemoryScope:
    """Resolve one acceptance-eligible pair through configured leaf authority."""

    repo = require_repo(config, repo_id)
    configured = admit_configured_contract(
        config,
        contract_path,
        # The configured boundary still binds repository roots and enclosure
        # ownership; the shared pair validator owns live candidate identity.
        require_candidate_identity=False,
    )
    if isinstance(configured, ConfiguredContractRefused):
        _raise_configured_pair_refusal(configured, requested_contract_path=contract_path)
    pair = resolve_memory_candidate_pair(
        configured.contract,
        requested_contract_path=contract_path,
        requested_repo_id=repo.repo_id,
    )
    prepared_code_view, prepared_code_history_commits = _resolve_prepared_code_source(
        configured.contract, pair
    )
    return _leaf_scope(
        repo,
        configured.contract,
        pair_identity=pair,
        prepared_code_view=prepared_code_view,
        prepared_code_history_commits=prepared_code_history_commits,
    )


def revalidate_memory_candidate_scope(
    config: McpRuntimeConfig,
    scope: MemoryScope,
) -> MemoryScope:
    """Reread the same contract-addressed pair and reject any changed identity."""

    pair = scope.pair_identity
    if pair is None:
        return scope
    current = resolve_memory_candidate_scope(
        config,
        repo_id=scope.repo_id,
        contract_path=pair.contractPath,
    )
    if current.pair_identity != pair:
        raise MemoryCandidatePairError(
            "memory-candidate-pair-stale",
            "the exact contract-addressed code/memory pair changed after admission",
            failure=MemoryCandidatePairFailure(
                field="pairIdentity",
                contract_path=pair.contractPath,
                expected={"pairIdentity": pair.model_dump(mode="json")},
                observed={
                    "pairIdentity": (
                        None
                        if current.pair_identity is None
                        else current.pair_identity.model_dump(mode="json")
                    )
                },
                next_action="worktree_sync",
                next_args={"contract_path": pair.contractPath, "dry_run": True},
            ),
        )
    if (
        current.prepared_code_view != scope.prepared_code_view
        or current.prepared_code_history_commits != scope.prepared_code_history_commits
    ):
        raise MemoryCandidatePairError(
            "memory-quality-prepared-source-changed",
            "the selected prepared code source changed after quality admission",
            failure=MemoryCandidatePairFailure(
                field="preparedCodeView",
                contract_path=pair.contractPath,
                expected={
                    "preparedCodeView": (
                        None
                        if scope.prepared_code_view is None
                        else scope.prepared_code_view.model_dump(mode="json")
                    ),
                    "preparedCodeHistoryCommits": scope.prepared_code_history_commits,
                },
                observed={
                    "preparedCodeView": (
                        None
                        if current.prepared_code_view is None
                        else current.prepared_code_view.model_dump(mode="json")
                    ),
                    "preparedCodeHistoryCommits": current.prepared_code_history_commits,
                },
                next_action="recover",
                next_args={
                    "repo_id": scope.repo_id,
                    "contract_path": pair.contractPath,
                    "operation": "closeout",
                },
            ),
        )
    return current


def _leaf_scope(
    repo: RepositoryScope,
    contract: WorktreeContract,
    *,
    pair_identity: MemoryCandidatePairIdentity | None = None,
    prepared_code_view: PreparedCodeExecutionView | None = None,
    prepared_code_history_commits: tuple[str, ...] = (),
) -> MemoryScope:
    assert contract.memory_worktree is not None
    onboarding_root = contract.memory_worktree / "onboarding"
    if not onboarding_root.is_dir():
        raise ValueError(
            f"contract {contract.contract_path.as_posix()} names memory worktree "
            f"{contract.memory_worktree.as_posix()}, which has no onboarding tree at "
            f"{onboarding_root.as_posix()}; the worktree was removed or never opened"
        )
    return MemoryScope(
        repo_id=repo.repo_id,
        identity=MemoryScopeIdentity(
            authority="leaf",
            authority_path=contract.contract_path.as_posix(),
            code_root=contract.code_worktree.resolve().as_posix(),
            onboarding_root=onboarding_root.resolve().as_posix(),
            unstamped_code_commit=contract.code_base_commit,
            pair_identity=pair_identity,
        ),
        code_root=contract.code_worktree,
        onboarding_root=onboarding_root,
        context=replace(contract_context(contract), code_repository_root=contract.code_worktree),
        cache_authority=source_index_cache.managed_cache_authority(
            coordination_root=contract.coordination_root,
            contract_path=contract.contract_path,
            code_root=contract.code_worktree,
            memory_root=contract.memory_worktree,
            lifecycle_id=contract.lifecycle_id,
        ),
        unstamped_code_commit=contract.code_base_commit,
        curator_report_path=report_path_for(contract.worktree_group),
        contract=contract,
        pair_identity=pair_identity,
        prepared_code_view=prepared_code_view,
        prepared_code_history_commits=prepared_code_history_commits,
    )


def _resolve_prepared_code_source(
    contract: WorktreeContract,
    pair: MemoryCandidatePairIdentity,
) -> tuple[PreparedCodeExecutionView | None, tuple[str, ...]]:
    """Resolve the selected closeout output into the existing prepared-view contract.

    The closeout journal and certificate object store are the only source of a prepared
    root.  A leaf without a selected code output remains on the ordinary logical working
    tree; once a code intent is selected, an incomplete or moved output is a typed refusal.
    """

    try:
        try:
            store = located_lifecycle_operation_store(contract, "closeout")
            record = store.read()
        except LifecycleOperationLocationError as error:
            if error.status == "operation-location-adoption-required":
                return None, ()
            raise
        if record is None or record.preparation is None:
            return None, ()
        if record.preparation.legs[0].leg != "code":
            raise ValueError("selected preparation does not begin with its code leg")
        if record.preparation.legs[0].output is None:
            raise ValueError("selected code preparation has no retained output")
        view = observe_selected_prepared_code_view(
            contract,
            record,
            store,
            pair,
        )
        history_commits = selected_prepared_code_history_commits(contract, record)
        current = capture_future_code_candidate(contract)
        if view.codeTree != current.codeCandidateTree:
            return None, history_commits
        return view, history_commits
    except (LifecycleOperationLocationError, LifecycleOperationReadError) as error:
        raise _prepared_code_view_error(contract, error) from error
    except (OSError, ValueError) as error:
        raise _prepared_code_view_error(contract, error) from error


def _prepared_code_view_error(
    contract: WorktreeContract,
    error: BaseException,
) -> MemoryCandidatePairError:
    status = "prepared-code-view-invalid"
    detail = "the selected prepared code source could not be proved for memory quality"
    expected: dict[str, object] = {"state": "selected-prepared-code-output"}
    observed: dict[str, object] = {
        "errorType": type(error).__name__,
        "reason": str(error),
    }
    if isinstance(error, LifecycleOperationLocationError):
        status = error.status
        detail = error.detail
        expected = dict(error.expected)
        observed = dict(error.observed)
    elif isinstance(error, LifecycleOperationReadError):
        status = "prepared-code-view-journal-unreadable"
        expected = dict(error.expected)
        observed = dict(error.observed)
    else:
        findings = getattr(error, "findings", ())
        if findings:
            finding = findings[0]
            code = str(finding.get("code", "invalid"))
            status = f"prepared-code-view-{code}"
            detail = f"selected prepared code proof refused: {code}"
            expected_value = finding.get("expected")
            observed_value = finding.get("observed")
            if isinstance(expected_value, Mapping):
                expected = dict(expected_value)
            elif expected_value is not None:
                expected = {"value": expected_value}
            if isinstance(observed_value, Mapping):
                observed = dict(observed_value)
            elif observed_value is not None:
                observed = {"value": observed_value}
    return MemoryCandidatePairError(
        status,
        detail,
        failure=MemoryCandidatePairFailure(
            field="preparedCodeView",
            contract_path=contract.contract_path.as_posix(),
            expected=expected,
            observed=observed,
            next_action="recover",
            next_args={
                "repo_id": contract.repo_name,
                "contract_path": contract.contract_path.as_posix(),
                "operation": "closeout",
            },
        ),
    )


def _raise_configured_pair_refusal(
    refusal: ConfiguredContractRefused,
    *,
    requested_contract_path: str,
) -> NoReturn:
    projected = project_configured_contract_refusal(
        refusal,
        operation="memory_quality_check",
    )
    # The canonical projector owns this public schema. Missing required fields are an
    # implementation defect and must remain loud instead of being reconstructed here.
    raise MemoryCandidatePairError(
        projected["status"],
        projected["detail"],
        failure=MemoryCandidatePairFailure(
            field="contractPath",
            contract_path=requested_contract_path,
            expected=projected["expected"],
            observed=projected["observed"],
            next_action=projected["nextAction"],
            next_args=projected.get("nextArgs"),
        ),
    )


__all__ = [
    "MemoryScope",
    "MemoryScopeIdentity",
    "resolve_memory_candidate_scope",
    "resolve_memory_scope",
    "revalidate_memory_candidate_scope",
]
