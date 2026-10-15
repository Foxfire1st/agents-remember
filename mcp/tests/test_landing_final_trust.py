"""MIK-R81 rule 6 (F16): only a genuinely successful matching final reads as history.

A landing-final file is trusted only when every row validates as a LandingRefNode
(including optional reducer field types), the kind set is exactly the contract's
expected participants, the feat/mem labels match the contract's branches, and every
fact is observed. Anything else -- a PR-only row, all-missing rows, a foreign
branch, a stale participant set, corrupt optionals -- reads as honest missing so it
neither serves invalid history nor suppresses the one eligible finishing attempt.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.projections.landing_state import (
    LandingStateRefresher,
    _load_final,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    default_contract,
    write_contract,
)

NOW = datetime(2026, 10, 13, 12, 0, 0, tzinfo=UTC)


class TerminalSweepBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_startup_success_failure_final_loss_and_reopen_keep_four_admissions(self) -> None:
        for successful, observed_live in (
            (True, False),
            (False, False),
            (True, True),
            (False, True),
        ):
            with (
                self.subTest(successful=successful, observed_live=observed_live),
                tempfile.TemporaryDirectory() as tmp,
            ):
                root = Path(tmp)
                contracts = [
                    replace(
                        default_contract(
                            ContractTask("task", "repo", root, "light-task", "external"),
                            leaf=LeafIdentity(f"leaf-{i}"),
                            code=RepoBranchPlan(root / "code", "main", "work", "a" * 40),
                            memory=RepoBranchPlan(root / "memory", "main", "work", "a" * 40),
                        ),
                        cleanup="pending" if observed_live else "completed",
                        closeout_status="completed",
                    )
                    for i in range(5)
                ]
                for contract in contracts:
                    write_contract(contract.contract_path, contract)
                seen = []

                def observe(contract, *, seen=seen, successful=successful):
                    seen.append(contract.contract_path)
                    if not successful:
                        raise OSError("isolated unavailable observation")
                    return [
                        _row("origin-feat", "origin/main"),
                        _row("origin-main", "origin/main", state="planned"),
                        _row("origin-mem-main", "origin/main"),
                        _row("pr", "PR #128", state="merged"),
                    ]

                refresher = LandingStateRefresher(_config(root), observe=observe)
                now = datetime.now(UTC)
                owner = "agents_remember.serving.projections.landing_state"
                with mock.patch(
                    owner + ".iter_leaf_enclosure_contracts",
                    return_value=[c.contract_path for c in contracts],
                ):
                    if observed_live:
                        await refresher.refresh_once(now=now)
                        self.assertEqual(len(seen), 5)
                        contracts = [replace(c, cleanup="completed") for c in contracts]
                        for contract in contracts:
                            write_contract(contract.contract_path, contract)
                        seen.clear()
                        now = datetime.now(UTC)
                    await refresher.refresh_once(now=now)
                    self.assertEqual(len(seen), 4)
                    await refresher.refresh_once(now=now)
                    self.assertEqual(len(seen), 5)
                    self.assertEqual(len(set(seen)), 5)
                    for contract in contracts:
                        final = contract.contract_path.parent / "landing-final.json"
                        self.assertEqual(final.exists(), successful)
                        final.unlink(missing_ok=True)
                    for _ in range(3):
                        await refresher.refresh_once(now=now)
                    self.assertEqual(len(seen), 5)
                    # A real live arc resets only its own one-attempt token.
                    reopened = replace(
                        contracts[0], cleanup="reopened", closeout_status="not-started"
                    )
                    write_contract(reopened.contract_path, reopened)
                    await refresher.refresh_once(now=now)
                    self.assertEqual(len(seen), 5)
                    write_contract(contracts[0].contract_path, contracts[0])
                    await refresher.refresh_once(now=datetime.now(UTC))
                    self.assertEqual(len(seen), 6)
                    self.assertEqual(seen.count(contracts[0].contract_path), 2)
                    await refresher.refresh_once(now=now)
                    self.assertEqual(len(seen), 6)


def _contract(contract_path: Path, *, external_memory: bool = False) -> Any:
    return SimpleNamespace(
        contract_path=contract_path,
        cleanup="completed",
        code_source_branch="feat-x",
        memory_mode="external" if external_memory else "local",
        memory_repo_path=contract_path.parent / "memory" if external_memory else None,
        memory_source_branch="mem-main",
    )


def _row(kind: str, label: str, **overrides: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "kind": kind,
        "label": label,
        "state": "pushed",
        "factState": "observed",
        "detail": None,
        "observedAt": NOW.isoformat(),
        "lastAttemptAt": NOW.isoformat(),
        "staleSeconds": None,
    }
    row.update(overrides)
    return row


def _good_facts(*, external_memory: bool = False) -> list[dict[str, Any]]:
    facts = [
        _row("origin-feat", "origin/feat-x"),
        _row("origin-main", "origin/main", state="planned"),
        _row("pr", "PR #128", state="merged", at=NOW.isoformat()),
    ]
    if external_memory:
        facts.insert(1, _row("origin-mem-main", "origin/mem-main"))
    return facts


def _write_final(contract_path: Path, facts: list[dict[str, Any]]) -> Path:
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text("{}", encoding="utf-8")
    path = contract_path.parent / "landing-final.json"
    path.write_text(json.dumps({"frozenAt": NOW.isoformat(), "facts": facts}), encoding="utf-8")
    return path


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


class LandingFinalTrustTests(unittest.TestCase):
    def test_warm_final_rechecks_current_contract_expectations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "tasks" / "r" / "t1" / "contract.json"
            contract = _contract(contract_path)
            final = _write_final(contract_path, _good_facts())
            os.utime(contract_path, (NOW.timestamp(), NOW.timestamp()))
            initial_final = final.read_bytes()
            refresher = LandingStateRefresher(_config(root))
            with mock.patch(
                "agents_remember.serving.projections.landing_state._load_final",
                wraps=_load_final,
            ) as loader:
                self.assertIsNotNone(refresher._frozen_rows(contract))
                self.assertIsNone(
                    refresher._frozen_rows(_contract(contract_path, external_memory=True))
                )
                contract.code_source_branch = "different-arc"
                self.assertIsNone(refresher._frozen_rows(contract))
                contract.code_source_branch = "feat-x"
                self.assertIsNotNone(refresher._frozen_rows(contract))
                self.assertEqual(loader.call_count, 1)
            self.assertEqual(final.read_bytes(), initial_final)

    def test_genuinely_successful_matching_final_is_trusted(self) -> None:
        """A freezer-shaped successful final for this contract loads with its rows."""
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = Path(tmp) / "tasks" / "r" / "t1" / "contract.json"
            contract = _contract(contract_path)
            path = _write_final(contract_path, _good_facts())
            loaded = _load_final(path, contract)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            frozen_at, rows = loaded
            self.assertEqual(frozen_at, NOW)
            self.assertEqual(len(rows), 3)

    def test_legacy_extra_keys_still_strip_to_trusted(self) -> None:
        """Files from earlier builds carrying a frozen marker still load."""
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = Path(tmp) / "tasks" / "r" / "t1" / "contract.json"
            contract = _contract(contract_path)
            facts = [{**row, "frozen": True} for row in _good_facts()]
            path = _write_final(contract_path, facts)
            loaded = _load_final(path, contract)
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertTrue(all("frozen" not in row for row in loaded[1]))

    def test_untrusted_finals_read_as_honest_missing(self) -> None:
        """PR-only, missing, foreign, stale and corrupt finals are honest missing."""
        pr_only = [_row("pr", "PR #128", state="merged")]
        all_missing = [
            _row("origin-feat", "origin/feat-x", factState="missing"),
            _row("origin-main", "origin/main", factState="missing"),
            _row("pr", "PR #128", factState="missing"),
        ]
        foreign_branch = [
            _row("origin-feat", "origin/someone-else"),
            _row("origin-main", "origin/main"),
            _row("pr", "PR #128"),
        ]
        stale_participants = [
            _row("origin-feat", "origin/feat-x"),
            _row("origin-main", "origin/main"),
        ]
        extra_kind = [
            *_good_facts(),
            _row("origin-feat", "origin/feat-x"),
        ]
        bad_detail = [
            _row("origin-feat", "origin/feat-x", detail=42),
            _row("origin-main", "origin/main"),
            _row("pr", "PR #128"),
        ]
        bad_stale_seconds = [
            _row("origin-feat", "origin/feat-x"),
            _row("origin-main", "origin/main", staleSeconds="soon"),
            _row("pr", "PR #128"),
        ]
        unexpected_mem = [
            *_good_facts(),
            _row("origin-mem-main", "origin/mem-main"),
        ]
        bad_pr_label = [
            _row("origin-feat", "origin/feat-x"),
            _row("origin-main", "origin/main"),
            _row("pr", "http://example.invalid/pr", state="merged"),
        ]
        one_planned = [
            _row("origin-feat", "origin/feat-x"),
            _row("origin-main", "origin/main", factState="planned"),
            _row("pr", "PR #128"),
        ]
        cases = {
            "pr-only": pr_only,
            "all-missing": all_missing,
            "foreign-branch": foreign_branch,
            "stale-participants": stale_participants,
            "extra-kind": extra_kind,
            "non-string-detail": bad_detail,
            "non-numeric-staleSeconds": bad_stale_seconds,
            "unexpected-mem-row": unexpected_mem,
            "bad-pr-label": bad_pr_label,
            "one-planned": one_planned,
            "empty": [],
        }
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = Path(tmp) / "tasks" / "r" / "t1" / "contract.json"
            contract = _contract(contract_path)
            for name, facts in cases.items():
                with self.subTest(final=name):
                    path = _write_final(contract_path, facts)
                    self.assertIsNone(_load_final(path, contract), f"{name} must read as missing")

    def test_expected_mem_row_validates_against_external_contract(self) -> None:
        """An external-memory contract trusts its mem row; a local one refuses it."""
        with tempfile.TemporaryDirectory() as tmp:
            contract_path = Path(tmp) / "tasks" / "r" / "t1" / "contract.json"
            external = _contract(contract_path, external_memory=True)
            path = _write_final(contract_path, _good_facts(external_memory=True))
            self.assertIsNotNone(_load_final(path, external))
            local = _contract(contract_path)
            self.assertIsNone(_load_final(path, local))
            missing_mem = [
                row for row in _good_facts(external_memory=True) if row["kind"] != "origin-mem-main"
            ]
            _write_final(contract_path, missing_mem)
            self.assertIsNone(_load_final(path, external))

    def test_pr_only_file_does_not_suppress_the_finishing_attempt(self) -> None:
        """_frozen_rows is None for an untrusted file: the repair stays eligible."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            contract_path = root / "tasks" / "r" / "t1" / "contract.json"
            contract = _contract(contract_path)
            _write_final(contract_path, [_row("pr", "PR #128", state="merged")])
            refresher = LandingStateRefresher(_config(root))
            self.assertIsNone(refresher._frozen_rows(contract))
            _write_final(contract_path, _good_facts())
            # Pin the contract mtime to the frozen stamp: the file is written at real
            # test time while the fixture stamp is a fixed constant, and the
            # frozen-before-contract guard must not see a future contract here.
            stamp = NOW.timestamp()
            os.utime(contract_path, (stamp, stamp))
            rows = refresher._frozen_rows(contract)
            self.assertIsNotNone(rows)
            assert rows is not None
            self.assertEqual(len(rows), 3)
