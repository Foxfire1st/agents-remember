"""L6 closeout coverage tests for reopen helpers and unclaimed-entity ranking."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.memory_quality.integrity.onboarding_drift_check import unclaimed_entities
from agents_remember.worktrees import reopen
from agents_remember.worktrees.worktree_contract import WorktreeContract


class TestClearFrozenLanding:
    def test_absent(self, tmp_path: Path) -> None:
        contract = cast(
            WorktreeContract, SimpleNamespace(contract_path=tmp_path / "series-contract.md")
        )
        assert reopen._clear_frozen_landing(contract, dry_run=False) == "absent"

    def test_dry_run_and_deleted(self, tmp_path: Path) -> None:
        (tmp_path / "landing-final.json").write_text("{}", encoding="utf-8")
        contract = cast(
            WorktreeContract, SimpleNamespace(contract_path=tmp_path / "series-contract.md")
        )
        assert reopen._clear_frozen_landing(contract, dry_run=True) == "would-delete"
        assert reopen._clear_frozen_landing(contract, dry_run=False) == "deleted"
        assert not (tmp_path / "landing-final.json").exists()

    def test_delete_failed(self, tmp_path: Path) -> None:
        target = tmp_path / "landing-final.json"
        target.write_text("{}", encoding="utf-8")
        contract = cast(
            WorktreeContract, SimpleNamespace(contract_path=tmp_path / "series-contract.md")
        )
        with mock.patch.object(Path, "unlink", side_effect=OSError("nope")):
            assert reopen._clear_frozen_landing(contract, dry_run=False) == "delete-failed"


class TestReopenBlockers:
    def test_blockers(self, tmp_path: Path) -> None:
        contract = cast(
            WorktreeContract,
            SimpleNamespace(
                kind="leaf",
                closeout_status="completed",
                integration_status="completed",
                cleanup="completed",
                code_worktree=tmp_path / "code",
                memory_worktree=tmp_path / "memory",
            ),
        )
        assert reopen._reopen_blockers(contract) == []
        (tmp_path / "code").mkdir()
        assert any("code worktree still exists" in b for b in reopen._reopen_blockers(contract))
        contract = cast(
            WorktreeContract,
            SimpleNamespace(
                kind="master",
                closeout_status="pending",
                integration_status="pending",
                cleanup="pending",
                code_worktree=None,
                memory_worktree=None,
            ),
        )
        blockers = reopen._reopen_blockers(contract)
        assert blockers and "not a leaf enclosure" in blockers[0]


class TestUnclaimedEntities:
    def test_declaration_signals(self, tmp_path: Path) -> None:
        py = tmp_path / "a.py"
        py.write_text(
            "\n".join(
                [
                    "MY_CONTRACT = 'ar-durable-store/1.0'",
                    "BAD_CONTRACT = 'not-a-contract'",
                    "SCHEMA_VERSION = '1'",
                    "MY_SCHEMA = 'x'",
                    "owner = StoreOwnership(role='mcp')",
                ]
            ),
            encoding="utf-8",
        )
        signal = unclaimed_entities.declaration_signals(py, "a.py")
        assert signal is not None
        assert "MY_CONTRACT=ar-durable-store/1.0" in signal.versioned_contracts
        assert "owner" in signal.authority_declarations
        assert signal.schema_declarations == ("MY_SCHEMA=x", "SCHEMA_VERSION=1")
        txt = tmp_path / "a.txt"
        txt.write_text("x", encoding="utf-8")
        assert unclaimed_entities.declaration_signals(txt, "a.txt") is None
        empty = tmp_path / "empty.py"
        empty.write_text("x = 1\n", encoding="utf-8")
        assert unclaimed_entities.declaration_signals(empty, "empty.py") is None

    def test_rank_key_tiers(self) -> None:
        contract_authority = unclaimed_entities.UnclaimedEntitySource("a.py", ("c",), ("o",), ())
        contract = unclaimed_entities.UnclaimedEntitySource("b.py", ("c",), (), ())
        authority = unclaimed_entities.UnclaimedEntitySource("c.py", (), ("o",), ())
        schema = unclaimed_entities.UnclaimedEntitySource("d.py", (), (), ("s",))
        ranked = sorted(
            [schema, authority, contract, contract_authority], key=unclaimed_entities._rank_key
        )
        assert [one.path for one in ranked] == ["a.py", "b.py", "c.py", "d.py"]

    def test_rank_report(self, tmp_path: Path) -> None:
        (tmp_path / "mod.py").write_text("X_CONTRACT = 'ar-thing/2'\n", encoding="utf-8")
        (tmp_path / "plain.py").write_text("Y = 1\n", encoding="utf-8")
        catalog = tmp_path / "entities.md"
        catalog.write_text("", encoding="utf-8")
        report = unclaimed_entities.rank_unclaimed_entity_sources(
            tmp_path,
            catalog,
            source_inventory=["mod.py", "plain.py"],
        )
        assert report.source_count == 2
        assert report.unclaimed_source_count == 2
        assert report.ranked[0].path == "mod.py"
