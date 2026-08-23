"""Candidate publication must recheck contract identity under closeout authority."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.worktrees.closeout_input import CloseoutCandidateSnapshot
from agents_remember.worktrees.modules import closeout
from closeout_input_test_support import MutationEvidenceRecorder, closeout_worktree_args
from integration_branch_authority_test_support import _authority_fixture


def test_closeout_candidate_publication_rechecks_the_contract(tmp_path: Path) -> None:
    fixture = _authority_fixture(tmp_path)
    contract = fixture.leaf_contract
    changed = replace(contract, cleanup="completed")
    with mock.patch(
        "agents_remember.worktrees.closeout_input.capture_closeout_candidate",
        return_value=CloseoutCandidateSnapshot("tree", "a" * 40, "tree"),
    ):
        args = closeout_worktree_args(
            contract,
            approved=True,
            approval_note="approved",
            candidate_tree="tree",
            operation_progress=MutationEvidenceRecorder(),
        )
    with (
        mock.patch.object(closeout, "report_operation_progress"),
        mock.patch.object(
            closeout,
            "_closeout_contract",
            return_value=(contract.contract_path, contract),
        ),
        mock.patch.object(closeout, "_recover_closeout_finalization", return_value=None),
        mock.patch.object(closeout, "_validate_closeout_source_state"),
        mock.patch.object(closeout, "refuse_series_workbench_commit"),
        mock.patch.object(closeout, "require_current_route_review", return_value=object()),
        mock.patch.object(closeout, "_refuse_unsatisfied_closeout_gate"),
        mock.patch.object(
            closeout,
            "closeout_changed_paths",
            return_value={"all": [], "working": [], "committed": []},
        ),
        mock.patch.object(closeout, "code_change_present", return_value=False),
        mock.patch.object(
            closeout,
            "_closeout_attestations",
            return_value=closeout._CloseoutAttestations(),
        ),
        mock.patch.object(closeout, "_closeout_quality_preflight", return_value=({}, {}, False)),
        mock.patch.object(closeout, "_revalidate_reviewed_candidate"),
        mock.patch.object(closeout, "claim_queue_candidate_for_closeout"),
        mock.patch.object(closeout, "load_contract", return_value=changed),
        mock.patch.object(
            closeout,
            "publish_closeout_under_authority",
            side_effect=lambda _contract, publication: publication(),
        ),
        pytest.raises(RuntimeError, match="changed before candidate commit"),
    ):
        closeout.closeout_result(args, contract)
