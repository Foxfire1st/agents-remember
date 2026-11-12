"""Content-addressed, fail-closed incremental memory dependency scope."""

from .affected_models import AffectedUnitResult
from .candidate import (
    ContractScopeAuthority,
    observe_contract_task,
    observe_contract_task_pair,
    observe_git_tree_delta,
)
from .errors import GateFiveClosureRefusedError, ScopeFailure, ScopeUnprovenError
from .models import ScopeCandidateIdentity
from .registry import checker_registry_version, checker_scope_registry
from .subresult_store import ContentAddressedSubresultStore, SubresultStorePolicy

__all__ = [
    "AffectedUnitResult",
    "ContentAddressedSubresultStore",
    "ContractScopeAuthority",
    "GateFiveClosureRefusedError",
    "ScopeCandidateIdentity",
    "ScopeFailure",
    "ScopeUnprovenError",
    "SubresultStorePolicy",
    "checker_registry_version",
    "checker_scope_registry",
    "observe_contract_task",
    "observe_contract_task_pair",
    "observe_git_tree_delta",
]
