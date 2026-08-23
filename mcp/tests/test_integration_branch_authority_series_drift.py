"""Atomic-series source-drift forcing for protected integration refs."""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from agents_remember.models.lifecycles.operation import IntegrateOperationInput
from agents_remember.worktrees.integration.lifecycle import lifecycle_operations
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_store import (
    operation_record_path,
)
from agents_remember.worktrees.worktree_contract import write_contract
from integration_branch_authority_test_support import (
    _acquire_atomic_blocker,
    _authority_fixture,
    _complete_atomic_master,
)
from test_source_lineage import _commit_on, _git


class IntegrationBranchAuthoritySeriesDriftTests(unittest.TestCase):
    def test_atomic_series_source_drift_cannot_open_an_ambient_leaf_replay(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fixture = _authority_fixture(root)
            _acquire_atomic_blocker(fixture)
            _commit_on(fixture.code_repo, "ar/master", "atomic-candidate.txt")
            candidate = _git(fixture.code_repo, "rev-parse", "refs/heads/ar/master")
            _complete_atomic_master(fixture)
            series = replace(
                fixture.master_contract,
                closeout_status="completed",
                approved_for_commit=True,
                human_review_status="approved",
                code_commit=candidate,
            )
            write_contract(series.contract_path, series)
            _commit_on(fixture.code_repo, "super", "parallel-super.txt")
            record_path = operation_record_path(series.worktree_group, "integrate")

            with self.assertRaisesRegex(RuntimeError, "cannot open a leaf conflict worktree"):
                lifecycle_operations.start_or_observe_operation(
                    IntegrateOperationInput(
                        configPath=fixture.config_path.as_posix(),
                        contractPath=series.contract_path.as_posix(),
                        strategy="replay",
                    ),
                    series,
                    launcher=lambda *_: None,
                )
            self.assertFalse(record_path.exists())
