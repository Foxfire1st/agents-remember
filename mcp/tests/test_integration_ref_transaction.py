"""Crash-cut forcing for the protected named-ref transaction owner."""

from __future__ import annotations

import unittest

from agents_remember.application.lifecycle.lifecycle_operation_worker import OperationRuntime
from agents_remember.models.lifecycles.operation import (
    IntegrateOperationInput,
    IntegrationPublicationIntent,
    LifecycleOperationRecoveryCommits,
)
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    LifecycleOperationStore,
    operation_record_path,
)
from test_source_lineage import _git


class IntegrationRefTransactionTests(unittest.TestCase):
    @staticmethod
    def _land_external_recovery_pair(fixture, contract):
        lifecycle_operations.start_or_observe_operation(
            IntegrateOperationInput(
                configPath=fixture.config_path.as_posix(),
                contractPath=contract.contract_path.as_posix(),
            ),
            contract,
            launcher=lambda *_: None,
        )
        store = LifecycleOperationStore(operation_record_path(contract.worktree_group, "integrate"))
        runtime = OperationRuntime(store)
        running = runtime.start()
        authority = running.integrationAuthority
        assert authority is not None and contract.memory_repo_path is not None
        recovery = LifecycleOperationRecoveryCommits(
            codeCommit=contract.code_commit,
            memoryContentCommit=contract.memory_content_commit,
            ledgerCommit=contract.ledger_commit,
        )
        running = runtime.progress(
            "source-merge",
            {
                "current_command": "recover exact external integration pair",
                "irreversible_boundary": True,
                "recovery_commits": recovery.model_dump(mode="json"),
                "integration_publication": IntegrationPublicationIntent(
                    operationKey=running.operationKey,
                    generation=running.generation,
                    preparedAt="2026-08-22T00:00:00+00:00",
                    claimState="not-applicable",
                ).model_dump(mode="json"),
            },
        )
        _git(
            fixture.code_repo,
            "update-ref",
            f"refs/heads/{authority.codeSourceBranch}",
            recovery.codeCommit,
            authority.codeSourceCommit,
        )
        _git(
            contract.memory_repo_path,
            "update-ref",
            f"refs/heads/{authority.memorySourceBranch}",
            recovery.ledgerCommit,
            authority.memorySourceCommit,
        )
        durable = store.read()
        assert durable is not None
        return durable, recovery
