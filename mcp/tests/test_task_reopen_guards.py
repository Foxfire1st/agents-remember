from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import cast

from agents_remember.models.worktree import SourceLineageProjection
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    lifecycle_operation_locator_path,
)
from agents_remember.worktrees.reopen import reopen_task
from agents_remember.worktrees.worktree_contract import write_contract
from test_task_reopen import _completed_leaf_contract, _leaf_doc, _master_doc
from test_worktree_support import git


class ReopenGuardTests(unittest.TestCase):
    def test_refuses_before_task_reset_when_terminal_predecessor_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            _master_doc(contract.task_root)
            contract_before = contract.contract_path.read_bytes()
            doc_before = doc_path.read_bytes()
            lifecycle_operation_locator_path(
                contract.coordination_root,
                contract.contract_path,
            ).unlink()

            result = reopen_task(contract.contract_path)

            self.assertEqual(
                (result.returncode, result.payload["state"]),
                (2, "operation-location-terminal-predecessor-missing"),
            )
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), doc_before)

    def test_refuses_a_leaf_that_is_not_fully_landed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            write_contract(
                contract.contract_path,
                replace(contract, closeout_status="not-started", cleanup="pending"),
            )
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(result.payload["state"], "blocked")
            blockers = " ".join(cast("list[str]", result.payload["blockers"]))
            self.assertIn("closeout", blockers)
            self.assertIn("cleanup", blockers)

    def test_refuses_a_non_leaf_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            write_contract(contract.contract_path, replace(contract, kind="series"))
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn(
                "not a leaf enclosure", " ".join(cast("list[str]", result.payload["blockers"]))
            )

    def test_refuses_when_a_worktree_still_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            contract.code_worktree.mkdir(parents=True)
            result = reopen_task(contract.contract_path)
            self.assertEqual(result.returncode, 2)
            self.assertIn("still exists", " ".join(cast("list[str]", result.payload["blockers"])))

    def test_moved_super_refuses_before_reopen_mutates_task_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            contract = _completed_leaf_contract(Path(tmp))
            doc_path = _leaf_doc(contract.task_root)
            _master_doc(contract.task_root)
            contract_before = contract.contract_path.read_bytes()
            doc_before = doc_path.read_bytes()
            git(contract.code_repo_path, "checkout", "super")
            marker = contract.code_repo_path / "super-moved.txt"
            marker.write_text("new super\n", encoding="utf-8")
            git(contract.code_repo_path, "add", marker.name)
            git(contract.code_repo_path, "commit", "-m", "move super")

            result = reopen_task(contract.contract_path)

            self.assertEqual(result.returncode, 2)
            lineage = SourceLineageProjection.model_validate(result.payload["source_lineage"])
            self.assertEqual(lineage.state, "blocked")
            self.assertEqual(contract.contract_path.read_bytes(), contract_before)
            self.assertEqual(doc_path.read_bytes(), doc_before)
