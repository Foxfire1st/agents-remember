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
    CONTRACT_SCHEMA_VERSION,
    ContractError,
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


class ContractSchemaVersionTests(unittest.TestCase):
    """``schemaVersion`` in the front matter: unknown MAJOR refused, unknown minor accepted.

    The same rule, and the same :func:`schema_version_supported` call, the control-plane JSONL
    records are read under (260731-EFA-L5 R6) -- deliberately one policy rather than two that
    drift. The refusal matters more here than a missing cell would: a contract from a future major
    still PARSES, because the front matter is flat ``key: value`` lines, so without this check the
    tools would read every cell and answer questions about a document that means something else.
    """

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)

    def _write_with_version(self, version: str | None) -> Path:
        """Write a real contract, then set/remove its ``schemaVersion`` line."""
        contract = _contract(self.root, "LC-01H")
        lines = contract_to_text(contract).splitlines()
        index = next(i for i, line in enumerate(lines) if line.startswith("schemaVersion:"))
        if version is None:
            del lines[index]
        else:
            lines[index] = f"schemaVersion: {version}"
        contract.contract_path.parent.mkdir(parents=True, exist_ok=True)
        contract.contract_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return contract.contract_path

    def test_a_written_contract_carries_the_durable_record_version(self) -> None:
        self.assertIn(
            f"schemaVersion: {CONTRACT_SCHEMA_VERSION}", contract_to_text(_contract(self.root, ""))
        )

    def test_the_version_this_build_writes_round_trips(self) -> None:
        path = self._write_with_version(CONTRACT_SCHEMA_VERSION)
        self.assertEqual(load_contract(path).lifecycle_id, "LC-01H")

    def test_an_unknown_major_is_refused_and_the_refusal_names_the_version(self) -> None:
        path = self._write_with_version("2.0")

        with self.assertRaises(ContractError) as raised:
            load_contract(path)

        message = str(raised.exception)
        self.assertIn("unsupported series contract schemaVersion: 2.0", message)
        self.assertIn(str(path), message)

    def test_an_unparseable_version_is_refused_rather_than_assumed_current(self) -> None:
        """A contract that cannot say what it is cannot be trusted to be what we assume."""
        path = self._write_with_version("draft")

        with self.assertRaises(ContractError):
            load_contract(path)

    def test_an_unknown_minor_is_additive_and_still_loads(self) -> None:
        path = self._write_with_version(f"{CONTRACT_SCHEMA_VERSION.split('.')[0]}.99")
        self.assertEqual(load_contract(path).lifecycle_id, "LC-01H")

    def test_a_contract_written_before_the_field_existed_still_loads(self) -> None:
        """No version line means 1.0 by definition -- which is why no migration is needed."""
        path = self._write_with_version(None)
        self.assertEqual(load_contract(path).lifecycle_id, "LC-01H")


if __name__ == "__main__":
    unittest.main()
