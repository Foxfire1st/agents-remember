"""The worktree contract carries a lifecycle anchor (slice 2c).

``lifecycle_id`` is an additive field on schema v1: it round-trips through
``contract_to_text``/``load_contract``, and a contract written before the field
existed (no ``lifecycle:`` section) loads with ``lifecycle_id == ""``.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    contract_to_text,
    default_contract,
    load_contract,
    write_contract,
)


def _contract(root: Path, lifecycle_id: str) -> WorktreeContract:
    return default_contract(
        ContractTask(
            name="Observe Lifecycle",
            repo_name="repo-a",
            coordination_root=root,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name="observe-lifecycle", lifecycle_id=lifecycle_id),
        code=RepoBranchPlan(
            repo_path=root / "repo-a",
            source_branch="main",
            work_branch="ar/observe-lifecycle",
            base_commit="0" * 40,
        ),
    )


class ContractLifecycleAnchorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def _write(self, contract: WorktreeContract) -> Path:
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        write_contract(contract.contract_path, contract)
        return contract.contract_path

    def test_default_lifecycle_id_is_empty(self) -> None:
        self.assertEqual(_contract(self.root, "").lifecycle_id, "")

    def test_text_carries_a_lifecycle_section(self) -> None:
        text = contract_to_text(_contract(self.root, "LC-01H"))
        self.assertIn("lifecycle:", text)
        self.assertIn("id: LC-01H", text)

    def test_lifecycle_id_round_trips(self) -> None:
        path = self._write(_contract(self.root, "LC-01H"))
        self.assertEqual(load_contract(path).lifecycle_id, "LC-01H")

    def test_legacy_contract_without_section_defaults_empty(self) -> None:
        contract = _contract(self.root, "LC-01H")
        lines = contract_to_text(contract).splitlines()
        index = lines.index("lifecycle:")
        del lines[index : index + 2]  # drop the `lifecycle:` + `  id:` block
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract.contract_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertEqual(load_contract(contract.contract_path).lifecycle_id, "")


if __name__ == "__main__":
    unittest.main()
