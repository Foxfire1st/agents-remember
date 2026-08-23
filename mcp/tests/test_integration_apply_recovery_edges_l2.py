"""Focused finalization and series-source recovery edge forcing."""

from __future__ import annotations

import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

import pytest
from agents_remember.worktrees.integration.integration_ref_transaction import IntegratedCommits
from agents_remember.worktrees.modules import integrate
from agents_remember.worktrees.modules.args import WorktreeArgs
from agents_remember.worktrees.modules.models import WorktreeCommandResult
from integration_branch_authority_test_support import _authority_fixture


def test_recovery_publication_and_apply_recheck_current_contract() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        fixture = _authority_fixture(Path(tmp))
        contract = fixture.leaf_contract
        with (
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
                return_value=(IntegratedCommits("code", "", ""), {}, None, False),
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
        ):
            assert (
                integrate._apply_integration(
                    series,
                    WorktreeArgs(),
                    cast(Any, SimpleNamespace()),
                    handover_warning=None,
                )
                is blocker
            )
        require.assert_called_once()
