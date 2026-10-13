"""MIK-R110 rule 4: the quality rows' cases derived through the real tool paths.

``memory_quality_check`` sync, start, and poll run against a real leaf
fixture; the answers the payload builders project are proved against the
row (fields, next-step uses, reason/action preservation, no guard cut,
measured bounds, production-bound equality). The same module pins the
rule-4 not-ok envelopes (every not-ok answer carries a code, a one-sentence
error, and the next action) and the rule-4 blocking projection (the complete
checklist populations in repair order).
"""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

from agents_remember.application.memory_quality import controller
from agents_remember.application.memory_quality.answer import (
    quality_poll_answer as _quality_poll_answer,
)
from agents_remember.application.memory_quality.answer import (
    quality_result_answer as _quality_result_answer,
)
from agents_remember.application.memory_quality.answer import (
    quality_start_answer as _quality_start_answer,
)
from agents_remember.application.memory_quality.controller import (
    poll_memory_quality_request,
    run_memory_quality_request,
    start_memory_quality_request,
)
from agents_remember.application.memory_quality.runs import (
    _registry,
    poll_quality_run,
)
from agents_remember.errors import MemoryCandidatePairError, MemoryCandidatePairFailure
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
)
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.memory import (
    MemoryQualityPollRequest,
    MemoryQualityStartRequest,
    MemoryQualitySyncRequest,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from test_worktree_support import git, open_external_contract_fixture
from tool_answer_l110_rows import (
    assert_answer_survives_the_guard,
    assert_production_answer,
)

_TOOL = "memory_quality_check"


def _scoped_pair(root: Path) -> tuple[Any, McpRuntimeConfig, McpRuntimeConfig]:
    """One real leaf pair with a scoped runtime config over its worktrees."""

    contract = open_external_contract_fixture(root)
    config_path = root / "settings" / "mcp.json"
    configured = McpRuntimeConfig(
        config_path=config_path,
        coordination_root=contract.coordination_root,
        workspace_root=contract.worktree_group,
        transcript_root=contract.coordination_root / "logs" / "mcp",
        repositories={
            contract.repo_name: RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_repo_path,
                memory_root=contract.memory_repo_path,
            )
        },
    )
    scoped = McpRuntimeConfig(
        config_path=config_path,
        coordination_root=contract.coordination_root,
        workspace_root=contract.worktree_group,
        transcript_root=configured.transcript_root,
        repositories={
            contract.repo_name: RepositoryScope(
                repo_id=contract.repo_name,
                path=contract.code_worktree,
                memory_root=contract.memory_worktree,
                contract_path=contract.contract_path,
            )
        },
    )
    return contract, configured, scoped


def _sync_request(contract: Any) -> MemoryQualitySyncRequest:
    return MemoryQualitySyncRequest(
        mode="sync",
        repo_id=contract.repo_name,
        contract_path=contract.contract_path.as_posix(),
    )


def _write_broken_anchor_cards(onboarding: Path, count: int) -> None:
    """Author ``count`` real cards each citing a source range that never existed."""

    onboarding.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        (onboarding / f"cards-{index:02d}.md").write_text(
            f"# Card {index:02d}\n\n"
            "| Finding | Anchor | Source |\n"
            "| --- | --- | --- |\n"
            f"| Does things. | `run` | pkg/missing-{index:02d}.py:1-2 |\n",
            encoding="utf-8",
        )


def _write_missing_anchor_sidecars(onboarding: Path, count: int) -> None:
    """Author ``count`` file sidecars whose reference anchor names a missing file.

    Converted memory has no citation tables for the legacy anchor checks to read; the
    damage converted memory can have is a sidecar anchor naming a path the paired code
    tree does not hold, which the knowledge validator refuses (MIK-R22 rule 6). The
    paired Markdown carries the reference's marker so the markers rule stays silent and
    the anchor path is the one violation each sidecar contributes.
    """

    onboarding.mkdir(parents=True, exist_ok=True)
    for index in range(count):
        name = f"pkg/side-{index:02d}.py"
        (onboarding / name).parent.mkdir(parents=True, exist_ok=True)
        # The [1] marker in the paired Markdown satisfies the markers rule, so the
        # only violation left is the anchor's missing path.
        (onboarding / f"{name}.md").write_text(
            f"# Side {index:02d}\n\nThe file's anchor [1] names its source.\n",
            encoding="utf-8",
        )
        sidecar = onboarding / f"{name}.json"
        sidecar.write_text(
            canonical_text(
                {
                    "schema": "ar-onboarding-file/v1",
                    "path": name,
                    "references": {
                        "1": {
                            "targets": [
                                {
                                    "kind": "code",
                                    "anchor": {
                                        "path": f"pkg/missing-{index:02d}.py",
                                        "locator": {"kind": "symbol", "name": "run"},
                                        "blob": "0" * 40,
                                        "content": "sha256:" + "0" * 64,
                                    },
                                }
                            ]
                        }
                    },
                    "realizes": [],
                }
            ),
            encoding="utf-8",
        )


def _write_converted_layout(memory_worktree: Path) -> None:
    """Mark the memory worktree converted so the run judges converted damage."""

    layout = memory_worktree / "knowledge" / "layout.json"
    layout.parent.mkdir(parents=True, exist_ok=True)
    layout.write_text(
        canonical_text({"schema": "ar-memory-layout/v2", "conversion": "1"}),
        encoding="utf-8",
    )


def _settle(repo_id: str, run_id: str) -> Any:
    deadline = time.monotonic() + HANG_GUARD_SECONDS
    while time.monotonic() < deadline:
        snapshot = poll_quality_run(repo_id, run_id)
        if snapshot is not None and snapshot.status != "running":
            return snapshot
        time.sleep(0.01)
    raise AssertionError(f"run {run_id} did not settle")


class QualityProductionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(_registry.clear)

    def test_sync_failure_derives_the_largest_case(self) -> None:
        """A real sync over 25 sidecars with missing-path anchors answers 54 findings."""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, configured, scoped = _scoped_pair(root)
            _write_missing_anchor_sidecars(contract.memory_worktree / "onboarding", 25)
            _write_converted_layout(contract.memory_worktree)
            # The cited top-level directory must exist for the missing files to
            # read as this repository's damage rather than a dependency's absence.
            package = contract.code_worktree / "pkg"
            package.mkdir(parents=True, exist_ok=True)
            (package / "real.py").write_text("print('real')\n", encoding="utf-8")
            # Tracked cards: findings on untracked cards are closeout-owned, so
            # the repair set the answer projects needs them in the index.
            git(contract.memory_worktree, "add", "-A")
            with patch(
                "agents_remember.worktrees.integration.configured_contract_authority.load_config",
                return_value=configured,
            ):
                whole = run_memory_quality_request(scoped, _sync_request(contract))
        answer = _quality_result_answer(dict(whole))
        with self.subTest("row"):
            measured = assert_production_answer(self, _TOOL, answer)
            self.assertLessEqual(measured, 1500)
            self.assertLessEqual(1500, 2 * measured)
        with self.subTest("guard"):
            assert_answer_survives_the_guard(_TOOL, dict(whole), answer)
        with self.subTest("blocking"):
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["status"], "failed")
            self.assertEqual(answer["code"], "action-required")
            self.assertEqual(
                answer["error"],
                "Memory quality found 54 curator-actionable findings; repair the "
                "blocking findings and rerun the check.",
            )
            self.assertEqual(answer["nextAction"], "memory_quality_check")
            self.assertGreaterEqual(answer["blockingCount"], 25)
            entries = answer["blocking"]
            self.assertEqual(len(entries), 10)
            for entry in entries:
                self.assertEqual(
                    sorted(entry),
                    ["check", "code", "document", "place", "reason"],
                )
                self.assertTrue(entry["reason"])
                self.assertLessEqual(len(entry["reason"]), 200)
            self.assertTrue(
                any(entry["document"] for entry in entries),
                "no blocking entry names its document",
            )

    def test_clean_sync_takes_over_the_missing_parent(self) -> None:
        """A real sync on a clean parentless leaf answers the take-over."""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, configured, scoped = _scoped_pair(root)
            onboarding = contract.memory_worktree / "onboarding"
            onboarding.mkdir(parents=True, exist_ok=True)
            with patch(
                "agents_remember.worktrees.integration.configured_contract_authority.load_config",
                return_value=configured,
            ):
                whole = run_memory_quality_request(scoped, _sync_request(contract))
        answer = _quality_result_answer(dict(whole))
        with self.subTest("row"):
            assert_production_answer(self, _TOOL, answer)
        with self.subTest("guard"):
            assert_answer_survives_the_guard(_TOOL, dict(whole), answer)
        with self.subTest("take-over"):
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["code"], "curator-coherence-task-missing")
            self.assertEqual(answer["nextAction"], "task_doc")
            error = answer["error"]
            self.assertTrue(error.endswith("."))
            self.assertNotIn(". ", error[:-1])

    def test_sync_and_start_and_poll_derive_the_running_and_completed_answers(
        self,
    ) -> None:
        """Real start admits a run, real polls answer running then completed."""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, configured, scoped = _scoped_pair(root)
            onboarding = contract.memory_worktree / "onboarding"
            onboarding.mkdir(parents=True, exist_ok=True)
            with patch(
                "agents_remember.worktrees.integration.configured_contract_authority.load_config",
                return_value=configured,
            ):
                started = start_memory_quality_request(
                    scoped,
                    MemoryQualityStartRequest(
                        mode="start",
                        repo_id=contract.repo_name,
                        contract_path=contract.contract_path.as_posix(),
                    ),
                )
                run_id = started["runId"]
                assert isinstance(run_id, str)
                start_answer = _quality_start_answer(
                    dict(started),
                    MemoryQualityStartRequest(
                        mode="start",
                        repo_id=contract.repo_name,
                        contract_path=contract.contract_path.as_posix(),
                    ),
                )
                polling = poll_memory_quality_request(
                    scoped,
                    MemoryQualityPollRequest(
                        mode="poll",
                        repo_id=contract.repo_name,
                        run_id=run_id,
                        contract_path=contract.contract_path.as_posix(),
                    ),
                )
                _settle(contract.repo_name, run_id)
                completed = poll_memory_quality_request(
                    scoped,
                    MemoryQualityPollRequest(
                        mode="poll",
                        repo_id=contract.repo_name,
                        run_id=run_id,
                        contract_path=contract.contract_path.as_posix(),
                    ),
                )
        with self.subTest("start"):
            self.assertEqual(start_answer["status"], "running")
            assert_production_answer(self, _TOOL, start_answer)
        with self.subTest("poll-running"):
            poll_running = _quality_poll_answer(
                dict(polling),
                MemoryQualityPollRequest(
                    mode="poll",
                    repo_id=contract.repo_name,
                    run_id=run_id,
                    contract_path=contract.contract_path.as_posix(),
                ),
            )
            self.assertEqual(poll_running["status"], "running")
            assert_production_answer(self, _TOOL, poll_running)
        with self.subTest("poll-completed"):
            poll_done = _quality_poll_answer(
                dict(completed),
                MemoryQualityPollRequest(
                    mode="poll",
                    repo_id=contract.repo_name,
                    run_id=run_id,
                    contract_path=contract.contract_path.as_posix(),
                ),
            )
            self.assertFalse(poll_done["ok"])
            assert_production_answer(self, _TOOL, poll_done)
            assert_answer_survives_the_guard(_TOOL, dict(completed), poll_done)

    def test_failed_answers_carry_code_error_and_next_action(self) -> None:
        """Every not-ok envelope answers its code, one error sentence, action."""

        start_request = MemoryQualityStartRequest(mode="start", repo_id="repo")
        poll_request = MemoryQualityPollRequest(mode="poll", repo_id="repo", run_id="r")
        with self.subTest("ordinary-failed"):
            whole = {
                "ok": False,
                "operation": "memory_quality_check",
                "repoId": "repo",
                "checklistStatus": "action-required",
                "curatorActionableCount": 2,
                "reportPath": "/group/reports/curator-memory-quality.md",
                "repairFindings": [
                    {
                        "check": "memory-census",
                        "code": "b",
                        "path": "p",
                        "message": "m",
                        "document": "p",
                        "place": "",
                        "reason": "m",
                    }
                ],
                "missingOnboardingRows": [],
                "staleRouteIndexes": [],
            }
            answer = _quality_result_answer(dict(whole))
            self.assertEqual(answer["code"], "action-required")
            self.assertEqual(
                answer["error"],
                "Memory quality found 2 curator-actionable findings; repair the "
                "blocking findings and rerun the check.",
            )
            self.assertEqual(answer["nextAction"], "memory_quality_check")
        with self.subTest("scope-refused"):
            error = MemoryCandidatePairError(
                "memory-candidate-pair-stale",
                "the candidate pair changed while memory quality was running",
                failure=MemoryCandidatePairFailure(
                    field="pairIdentity",
                    contract_path="/group/contract",
                    next_action="worktree_sync",
                ),
            )
            answer = _quality_result_answer(controller._pair_refusal("repo", error))
            self.assertEqual(answer["code"], "memory-candidate-pair-stale")
            self.assertEqual(
                answer["error"],
                "the candidate pair changed while memory quality was running",
            )
            self.assertEqual(answer["nextAction"], "worktree_sync")
        with self.subTest("capacity"):
            answer = _quality_start_answer(
                {
                    "ok": False,
                    "operation": "memory_quality_check",
                    "repoId": "repo",
                    "status": "capacity-reached",
                    "code": "capacity-reached",
                    "error": "The check server is at its run capacity; start the check again shortly.",
                    "guidance": "g",
                },
                start_request,
            )
            self.assertEqual(answer["nextAction"], "memory_quality_check")
            self.assertEqual(answer["nextArgs"]["mode"], "start")
            self.assertNotIn(". ", answer["error"][:-1])
        with self.subTest("run-not-found"):
            answer = _quality_poll_answer(
                {
                    "ok": False,
                    "operation": "memory_quality_check",
                    "repoId": "repo",
                    "status": "run-not-found",
                    "code": "run-not-found",
                    "error": "The run id is unknown; start the check again.",
                    "runId": "r",
                    "guidance": "g",
                },
                poll_request,
            )
            self.assertEqual(answer["nextAction"], "memory_quality_check")
            self.assertEqual(answer["nextArgs"]["mode"], "start")
        with self.subTest("run-failed"):
            answer = _quality_poll_answer(
                {
                    "ok": True,
                    "operation": "memory_quality_check",
                    "repoId": "repo",
                    "status": "failed",
                    "runId": "r",
                    "error": "probe failure",
                },
                poll_request,
            )
            self.assertFalse(answer["ok"])
            self.assertEqual(answer["code"], "quality-run-failed")
            self.assertEqual(answer["error"], "probe failure")
            self.assertEqual(answer["nextAction"], "memory_quality_check")

    def test_blocking_projects_the_populations_in_repair_order(self) -> None:
        """Missing-only, stale-only, census-only, and mixed blocking shapes."""

        base = {
            "ok": True,
            "operation": "memory_quality_check",
            "repoId": "repo",
            "checklistStatus": "action-required",
            "curatorActionableCount": 1,
            "reportPath": "/group/reports/curator-memory-quality.md",
        }
        with self.subTest("missing-only"):
            whole = dict(
                base,
                repairFindings=[],
                missingOnboardingRows=[
                    {
                        "sourceFile": "pkg/a.py",
                        "storageMode": "m",
                        "expectedOnboarding": "onboarding/pkg/a.py.md",
                        "state": "s",
                        "note": "Source pkg/a.py has no card.",
                    }
                ],
                staleRouteIndexes=[],
            )
            answer = _quality_result_answer(whole)
            self.assertEqual(answer["blockingCount"], 1)
            (entry,) = answer["blocking"]
            self.assertEqual(entry["check"], "missing-onboarding")
            self.assertEqual(entry["document"], "onboarding/pkg/a.py.md")
            self.assertEqual(entry["place"], "pkg/a.py")
            self.assertEqual(entry["reason"], "Source pkg/a.py has no card.")
        with self.subTest("stale-only"):
            whole = dict(
                base,
                repairFindings=[],
                missingOnboardingRows=[],
                staleRouteIndexes=["onboarding/overview.json"],
            )
            answer = _quality_result_answer(whole)
            (entry,) = answer["blocking"]
            self.assertEqual(entry["check"], "stale-route-index")
            self.assertEqual(entry["document"], "onboarding/overview.json")
            self.assertTrue(entry["reason"])
        with self.subTest("census-only"):
            whole = dict(
                base,
                repairFindings=[
                    {
                        "check": "memory-census",
                        "code": "b",
                        "path": "p",
                        "message": "m",
                        "document": "p",
                        "place": "s",
                        "reason": "m",
                    }
                ],
                missingOnboardingRows=[],
                staleRouteIndexes=[],
            )
            answer = _quality_result_answer(whole)
            (entry,) = answer["blocking"]
            self.assertEqual(entry["check"], "memory-census")
            self.assertEqual(entry["place"], "s")
        with self.subTest("mixed-repair-order"):
            whole = dict(
                base,
                curatorActionableCount=3,
                repairFindings=[
                    {
                        "check": "memory-census",
                        "code": "b",
                        "path": "p",
                        "message": "m",
                        "document": "p",
                        "place": "",
                        "reason": "m",
                    }
                ],
                missingOnboardingRows=[
                    {
                        "sourceFile": "s",
                        "storageMode": "m",
                        "expectedOnboarding": "e",
                        "state": "s",
                        "note": "n",
                    }
                ],
                staleRouteIndexes=["i"],
            )
            answer = _quality_result_answer(whole)
            self.assertEqual(
                [entry["check"] for entry in answer["blocking"]],
                ["memory-census", "missing-onboarding", "stale-route-index"],
            )
