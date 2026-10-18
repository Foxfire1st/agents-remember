"""MIK-R110 rules 5-7: the packaging rows' cases derived through the real tool paths.

``route_index_refresh``, ``drift_check``,
``memory_init``, ``memory_baseline_status``, ``memory_baseline_adopt``,
``memory_carryover_plan``, ``memory_carryover_apply``,
``knowledge_integrity_check``, and ``curator_coherence`` (status, prepare,
publish, validate) run against real fixtures; every produced answer is proved
against its row (fields, next-step uses, reason/action preservation, no guard
cut, measured bounds, production-bound equality).
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest.mock import patch

import pytest
from agents_remember.application.memory_tools import (
    drift_check_tool,
    memory_baseline_adopt_tool,
    memory_baseline_status_tool,
    memory_carryover_plan_tool,
    memory_init_tool,
    route_index_refresh_tool,
)
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
    RepositoryScope,
    load_config,
)
from agents_remember.kernel.primitives.tool_reports import write_tool_report
from agents_remember.mcp.tools.knowledge import (
    IntegrityCheckRequest,
    _integrity_answer,
    _integrity_check_result,
    knowledge_integrity_check_payload,
)
from agents_remember.mcp.tools.memory import (
    _drift_answer,
    _route_index_answer,
    compact_carryover_payload,
)
from agents_remember.models.declared_caller import DeclaredCaller
from agents_remember.models.knowledge_files.canonical import canonical_text
from agents_remember.models.knowledge_files.documents import record_path
from agents_remember.models.lifecycles.curator_coherence import (
    CuratorCoherenceJudgment,
    CuratorCoherenceRequest,
)
from agents_remember.models.tools.tool_answer import bound_for
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)
from curator_coherence_test_support import (
    prepared_publish_inputs,
    write_curator_evidence,
)
from knowledge_index_test_support import commit_all, init_repository, write_review_tree
from review_curator_fixture_support import build_recorded_fixture
from test_converted_carryover import _converted_world
from test_worktree_support import git, init_repo, open_external_contract_fixture
from tool_answer_l110_rows import (
    assert_answer_survives_the_guard,
    assert_production_answer,
)

REPO_ID = "repo-a"


# R1-05 (v2): row bounds govern authored dimensions, so the row cases author
# their fixture root width instead of inheriting ambient TMPDIR length.
# mkdtemp appends 8 random chars: constant width, unique per case. The XDG
# roots the answers embed are patched to the same fixture. The asserted
# bytes are exactly what the producers emitted — no rewriting, no raised
# bounds, no capped roots; the density floors still forbid hiding a real
# overflow behind shrinking.
_ROW_CASE_ROOT_PREFIX = "rowcase-ambient-width-fixed-for-row-bounds-"


@contextlib.contextmanager
def _fixed_width_case_root() -> Iterator[Path]:
    """A hermetic fixture root of authored, constant width under /tmp."""
    root = Path(tempfile.mkdtemp(prefix=_ROW_CASE_ROOT_PREFIX, dir=tempfile.gettempdir()))
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _case_xdg(root: Path) -> Any:
    """Point the XDG roots the answers embed at the same owned fixture."""
    xdg = root / "xdg"
    xdg.mkdir(exist_ok=True)
    return patch.dict(
        os.environ,
        {
            "XDG_CONFIG_HOME": xdg.as_posix(),
            "XDG_DATA_HOME": xdg.as_posix(),
            "XDG_CACHE_HOME": xdg.as_posix(),
        },
    )


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


def _world(tmp_path: Path) -> tuple[Path, Path]:
    """A code repository, a coordination root, and a settings file outside both."""

    root = tmp_path / "world"
    code = root / REPO_ID
    init_repo(code, "main")
    (code / "pkg").mkdir(parents=True, exist_ok=True)
    (code / "pkg" / "alpha.py").write_text("print('alpha')\n", encoding="utf-8")
    git(code, "add", "-A")
    git(code, "commit", "-qm", "init")
    (root / "coordination").mkdir(parents=True, exist_ok=True)
    path = root / "settings.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "coordinationRoot": (root / "coordination").as_posix(),
                "workspaceRoot": root.as_posix(),
                "repositories": {REPO_ID: {}},
            }
        ),
        encoding="utf-8",
    )
    return code, path


class RouteProductionTests(unittest.TestCase):
    def test_applied_and_stale_paths_derive_the_row_cases(self) -> None:
        """A real refresh writes 25 indexes; a dry run over them names the stale."""

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            contract, configured, scoped = _scoped_pair(root)
            onboarding = contract.memory_worktree / "onboarding"
            for index in range(25):
                route_dir = onboarding / f"route-{index:02d}"
                route_dir.mkdir(parents=True, exist_ok=True)
                (route_dir / "overview.md").write_text(f"# Route {index:02d}\n", encoding="utf-8")
            with patch(
                "agents_remember.worktrees.integration.configured_contract_authority.load_config",
                return_value=configured,
            ):
                fresh = route_index_refresh_tool(
                    scoped,
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                    dry_run=True,
                )
                route_index_refresh_tool(
                    scoped,
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                    dry_run=False,
                )
                steady = route_index_refresh_tool(
                    scoped,
                    repo_id=contract.repo_name,
                    contract_path=contract.contract_path.as_posix(),
                    dry_run=False,
                )
        with self.subTest("ordinary"):
            answer = _route_index_answer(steady)
            self.assertTrue(answer["ok"])
            self.assertEqual(answer["routes"], 25)
            self.assertEqual(answer["staleCount"], 0)
            assert_production_answer(self, "route_index_refresh", answer)
            assert_answer_survives_the_guard("route_index_refresh", steady, answer)
        with self.subTest("largest"):
            answer = _route_index_answer(fresh)
            self.assertEqual(answer["staleCount"], 25)
            self.assertEqual(len(answer["staleIndexes"]), 20)
            measured = assert_production_answer(self, "route_index_refresh", answer)
            self.assertLessEqual(measured, 400)
            self.assertLessEqual(400, 2 * measured)
            assert_answer_survives_the_guard("route_index_refresh", fresh, answer)


class DriftProductionTests(unittest.TestCase):
    def test_clean_and_flagged_paths_derive_the_row_cases(self) -> None:
        """A real drift check over a converted world, clean then flagged.

        MIK-R112 retired ``drift_check`` as a registered tool, so there is
        no delivery path to survive; the row proof below carries the
        projection (slim entries, ten-entry caps, counts) and its bound.
        """

        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            _code, path = _world(root)
            config = load_config(path)
            memory_init_tool(config, repo_id=REPO_ID, initial_branch="main")
            memory = load_config(path).repositories[REPO_ID].memory_root
            assert memory is not None
            clean = drift_check_tool(config, repo_id=REPO_ID, detail_limit=10)
            for index in range(25):
                target = f"pkg/gone-{index:02d}.py"
                document = {
                    "path": target,
                    "schema": "ar-onboarding-file/v1",
                    "realizes": [],
                    "references": {
                        "1": {
                            "targets": [
                                {
                                    "kind": "code",
                                    "anchor": {
                                        "blob": f"{index:040d}",
                                        "locator": {"kind": "file"},
                                        "content": f"sha256:{index:064d}",
                                    },
                                }
                            ]
                        }
                    },
                }
                sidecar = memory / "onboarding" / f"{target}.json"
                sidecar.parent.mkdir(parents=True, exist_ok=True)
                sidecar.write_text(json.dumps(document), encoding="utf-8")
                (memory / "onboarding" / f"{target}.md").write_text(
                    "# card\n\nSee [1].\n", encoding="utf-8"
                )
            flagged = drift_check_tool(config, repo_id=REPO_ID, detail_limit=50)
        with self.subTest("ordinary"):
            clean_answer = _drift_answer(clean)
            self.assertEqual(clean_answer["status"], "checked")
            assert_production_answer(self, "drift_check", clean_answer)
        with self.subTest("largest"):
            answer = _drift_answer(flagged)
            self.assertGreaterEqual(answer["actionableCount"], 20)
            self.assertEqual(len(answer["actionableSample"]), 10)
            self.assertEqual(
                sorted(answer["actionableSample"][0]),
                ["field", "path", "state", "target"],
            )
            self.assertEqual(len(answer["violationSample"]), 10)
            self.assertEqual(sorted(answer["violationSample"][0]), ["message", "path", "rule"])
            measured = assert_production_answer(self, "drift_check", answer)
            self.assertLessEqual(measured, 1500)
            self.assertLessEqual(1500, 2 * measured)


class InitBaselineProductionTests(unittest.TestCase):
    def test_init_and_baseline_paths_derive_the_row_cases(self) -> None:
        """A real init scaffolds, status reads ready, adopt records the ledger."""

        with _fixed_width_case_root() as root, _case_xdg(root):
            _code, path = _world(root)
            preview = memory_init_tool(
                load_config(path), repo_id=REPO_ID, initial_branch="main", dry_run=True
            )
            applied = memory_init_tool(load_config(path), repo_id=REPO_ID, initial_branch="main")
            config = load_config(path)
            ready = memory_baseline_status_tool(config, repo_id=REPO_ID)
            adopted = memory_baseline_adopt_tool(config, repo_id=REPO_ID)
        with self.subTest("init"):
            measured = assert_production_answer(self, "memory_init", applied)
            self.assertLessEqual(800, 2 * measured)
            assert_answer_survives_the_guard("memory_init", applied, applied)
            assert_production_answer(self, "memory_init", preview)
        with self.subTest("baseline-status"):
            self.assertEqual(ready["state"], "ready")
            measured = assert_production_answer(self, "memory_baseline_status", ready)
            self.assertLessEqual(600, 2 * measured)
            assert_answer_survives_the_guard("memory_baseline_status", ready, ready)
        with self.subTest("baseline-adopt"):
            measured = assert_production_answer(self, "memory_baseline_adopt", adopted)
            self.assertLessEqual(700, 2 * measured)
            assert_answer_survives_the_guard("memory_baseline_adopt", adopted, adopted)


class CarryoverProductionTests(unittest.TestCase):
    def test_plan_and_apply_derive_the_row_cases(self) -> None:
        """A real carryover plan and apply over the landed-branch world."""

        with _fixed_width_case_root() as root, _case_xdg(root):
            fixture, _contract, selection = _converted_world(root)
            planned = memory_carryover_plan_tool(fixture.cfg, selection)
            planned_report = write_tool_report(
                fixture.cfg.coordination_root, "memory_carryover_plan", planned, label="plan"
            )
            planned_answer = compact_carryover_payload(planned, planned_report.as_posix())
            # MIK-R56's converted pair replaces the retired legacy-only plan fixture.
            # Unconverted apply refusal and the cutover lock stay in their unchanged assertions.
        with self.subTest("plan"):
            measured = assert_production_answer(self, "memory_carryover_plan", planned_answer)
            self.assertLessEqual(500, 2 * measured)
            assert_answer_survives_the_guard(
                "memory_carryover_plan", planned_answer, planned_answer
            )

    def test_forty_path_population_passes_through_whole(self) -> None:
        """D140: the passed 40-path population survives with no per-tool cap.

        The same 40 source paths come back whole both with a report (apply)
        and without one (plan carries no reportPath key).
        """
        candidates = [
            {"decision": "keep", "source_path": f"pkg/file-{index:02d}.py"} for index in range(40)
        ]
        expected = [candidate["source_path"] for candidate in candidates]
        full: dict[str, Any] = {"ok": True, "candidates": candidates}
        without = compact_carryover_payload(full, None)
        self.assertEqual(without["decisions"], {"keep": expected})
        self.assertNotIn("reportPath", without)
        filed = compact_carryover_payload(full, "r/report.json")
        self.assertEqual(filed["decisions"], {"keep": expected})
        self.assertEqual(filed["reportPath"], "r/report.json")


class IntegrityProductionTests(unittest.TestCase):
    def test_reported_and_refused_paths_derive_the_row_cases(self) -> None:
        """A real integrity check reports the validator's listing; legacy refuses."""

        with _fixed_width_case_root() as root, _case_xdg(root):
            memory = root / "memory"
            init_repository(memory)
            write_review_tree(memory)
            family_path = memory / record_path("family", "FAM-CMPBND", "comparison-bound-context")
            family = json.loads(family_path.read_text(encoding="utf-8"))
            family["routes"] = list(family.get("routes", [])) + [
                f"missing-{index:03d}/src" for index in range(20)
            ]
            family_path.write_text(canonical_text(family), encoding="utf-8")
            commit_all(memory)
            code = root / "code"
            code.mkdir()
            request = IntegrityCheckRequest(memoryRoot=str(memory), codeRoot=str(code))
            whole = _integrity_check_result(request)
            reported = knowledge_integrity_check_payload(request)
            legacy = knowledge_integrity_check_payload(
                IntegrityCheckRequest(memoryRoot=str(code), codeRoot=str(code))
            )
        with self.subTest("reported"):
            validation = whole["validation"]
            self.assertEqual(reported["state"], "reported")
            listing = validation["violations"]
            count = listing["count"] if isinstance(listing, dict) else len(listing)
            self.assertGreaterEqual(count, 15)
            shaped = _integrity_answer(whole)
            envelope = shaped["validation"]["violations"]
            self.assertEqual(envelope["count"], count)
            self.assertEqual(len(envelope["items"]), min(10, count))
            self.assertEqual(envelope["truncated"], count > 10)
            measured = assert_production_answer(self, "knowledge_integrity_check", shaped)
            bound = bound_for("knowledge_integrity_check")
            self.assertLessEqual(measured, bound)
            self.assertLessEqual(bound, 2 * measured)
            assert_answer_survives_the_guard("knowledge_integrity_check", whole, shaped)
        with self.subTest("refused"):
            self.assertEqual(legacy["state"], "refused")
            assert_production_answer(self, "knowledge_integrity_check", legacy)


@pytest.mark.evidence_unit
@pytest.mark.usefixtures("worktree_services")
def test_coherence_paths_derive_the_row_cases() -> None:
    """Real status, prepare, publish, and validate answers fit the coherence row."""

    parent = Path(tempfile.gettempdir())
    # Author the actual fixture width, not answer padding or an ambient TMPDIR-dependent floor.
    stem = "coherence-row-cases-converted-memory-publication-evidence-"
    padding = max(0, 160 - len(os.fsencode(parent)) - 1 - len(stem) - 8)
    root = Path(tempfile.mkdtemp(prefix=stem + "x" * padding, dir=parent))
    try:
        with (
            _case_xdg(root),
        ):
            recorded = build_recorded_fixture(root)
            kinds = ("source-change", "new-card", "removed-anchor")
            candidates = [
                {
                    "sourceFile": f"pkg/candidate-{index:04d}.py",
                    "onboardingFile": f"onboarding/pkg/candidate-{index:04d}.py.md",
                    "classification": kinds[index % len(kinds)],
                }
                for index in range(40)
            ]
            write_curator_evidence(
                recorded.contract, caller_ref=recorded.sprint, source_candidates=candidates
            )
            prepared = curator_coherence_action(
                recorded.contract,
                CuratorCoherenceRequest(
                    action="prepare", contract_path=recorded.contract.contract_path.as_posix()
                ),
            )
            assert_production_answer(None, "curator_coherence", prepared)
            assert_answer_survives_the_guard("curator_coherence", prepared, prepared)

            evidence = recorded.contract.task_root / "evidence.md"
            evidence.write_text(
                "# fixture evidence\n\nthe record this judgment cites\n", encoding="utf-8"
            )
            judgments = [
                CuratorCoherenceJudgment(
                    sourceFile=candidate["sourceFile"],
                    onboardingFile=candidate["onboardingFile"],
                    classification=candidate["classification"],
                    disposition="reconciled",
                    rationale="The fixture candidate has one explicit reconciliation judgment.",
                    evidenceRef="task:evidence.md",
                )
                for candidate in candidates
            ]
            published = curator_coherence_action(
                recorded.contract,
                CuratorCoherenceRequest(
                    action="publish",
                    contract_path=recorded.contract.contract_path.as_posix(),
                    semantic_requirement_revision="MIK-R110@v1",
                    delivery_attempt="L110-A001",
                    judgments=judgments,
                    caller=DeclaredCaller(role="architect", task_document_ref=recorded.sprint),
                    **prepared_publish_inputs(prepared),
                ),
            )
            measured = assert_production_answer(None, "curator_coherence", published)
            assert 2 * measured >= 850, f"coherence bound slack: {measured}"
            assert_answer_survives_the_guard("curator_coherence", published, published)

            status = curator_coherence_action(
                recorded.contract,
                CuratorCoherenceRequest(
                    action="status", contract_path=recorded.contract.contract_path.as_posix()
                ),
            )
            assert_production_answer(None, "curator_coherence", status)
            validated = curator_coherence_action(
                recorded.contract,
                CuratorCoherenceRequest(
                    action="validate", contract_path=recorded.contract.contract_path.as_posix()
                ),
            )
            assert_production_answer(None, "curator_coherence", validated)
    finally:
        shutil.rmtree(root)
