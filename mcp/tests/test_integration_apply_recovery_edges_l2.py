"""Focused finalization and series-source recovery edge forcing."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.models.lifecycles.operation import IntegrationPublicationIntent
from agents_remember.worktrees.integration import integration_claim_transfer
from agents_remember.worktrees.integration.integration_ref_transaction import IntegratedCommits
from agents_remember.worktrees.modules import integrate
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from integration_branch_authority_test_support import _authority_fixture


def test_completed_recovery_requires_proven_or_not_applicable_source_claim() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        contract = replace(
            _authority_fixture(Path(tmp)).leaf_contract,
            integration_status="completed",
        )
        intent = IntegrationPublicationIntent(
            operationKey="a" * 64,
            generation=1,
            preparedAt="2026-08-22T00:00:00+00:00",
            claimState="intent",
            sprintTaskDocument="tasks/repo/sprint/task.md",
            candidateTaskDocument="tasks/repo/leaf/task.md",
            doorGenerationId="b" * 64,
            sourceOperationKind="closeout",
            sourceOperationGeneration=1,
            sourceOperationFingerprint="c" * 64,
            sourceOperationKey="d" * 64,
            sourceJournalSha256="e" * 64,
        )

        with pytest.raises(RuntimeError, match="no proven source publication authority"):
            integration_claim_transfer.prove_recovery_publication_authority(
                contract,
                WorktreeArgs(),
                intent,
                commits=("code", "", ""),
            )


def test_recovery_publication_and_apply_recheck_current_contract() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _authority_fixture(Path(tmp))
        contract = fixture.leaf_contract
        with (
            mock.patch.object(
                integrate,
                "transfer_and_publish_integration_claim",
                side_effect=lambda _contract, _args, intent, **_kwargs: intent,
            ),
            mock.patch.object(
                integrate,
                "load_contract",
                return_value=replace(contract, cleanup="completed"),
            ),
            pytest.raises(RuntimeError, match="changed before recovery finalization"),
        ):
            integrate._recover_integration_under_authority(
                contract,
                WorktreeArgs(
                    operation_key="a" * 64,
                    operation_generation=1,
                    integration_publication=integrate.IntegrationPublicationIntent(
                        operationKey="a" * 64,
                        generation=1,
                        preparedAt="2026-08-22T00:00:00+00:00",
                        claimState="not-applicable",
                    ),
                ),
                cast(
                    Any,
                    SimpleNamespace(
                        codeCandidateCommit="code",
                        memoryContentCommit="",
                        ledgerCommit="",
                    ),
                ),
            )

        blocker = WorktreeCommandResult(2, {"state": "blocked"})
        series = fixture.master_contract
        with (
            mock.patch.object(
                integrate,
                "_prepare_integration_commits",
                return_value=(
                    IntegratedCommits("code", "", ""),
                    {},
                    None,
                    integrate.IntegrationBoundaryFacts(None, None, None),
                ),
            ),
            mock.patch.object(integrate, "load_contract", return_value=series),
            mock.patch.object(integrate, "require_series_contract_authority") as require,
            mock.patch.object(
                integrate,
                "_integration_source_state_block",
                return_value=blocker,
            ),
            mock.patch.object(
                integrate,
                "publish_series_integration_under_authority",
                side_effect=lambda _contract, publication: publication(),
            ),
            mock.patch.object(
                integrate,
                "transfer_and_publish_integration_claim",
                side_effect=lambda _contract, _args, intent, **_kwargs: intent,
            ),
            mock.patch.object(integrate, "protected_integration_decision", return_value=None),
        ):
            assert (
                integrate._apply_integration(
                    series,
                    WorktreeArgs(
                        operation_key="a" * 64,
                        operation_generation=1,
                        integration_publication=integrate.IntegrationPublicationIntent(
                            operationKey="a" * 64,
                            generation=1,
                            preparedAt="2026-08-22T00:00:00+00:00",
                            claimState="not-applicable",
                        ),
                    ),
                    cast(Any, SimpleNamespace()),
                    handover_warning=None,
                )
                is blocker
            )
        require.assert_called_once()
