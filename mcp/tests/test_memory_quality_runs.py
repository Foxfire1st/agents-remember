"""Bounded registry and canonical memory-quality controller tests."""

from __future__ import annotations

import threading
import time
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest import mock

from agents_remember.application import memory_scope, memory_tools
from agents_remember.application.memory_quality import census, controller, runs
from agents_remember.application.memory_scope import (
    MemoryScope,
    MemoryScopeIdentity,
)
from agents_remember.errors import MemoryCandidatePairError, MemoryCandidatePairFailure
from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.memory_quality.check import AVAILABLE_CHECKS
from agents_remember.models.certification.references import CertificateObjectReference
from agents_remember.models.core import ServingBuildPayload
from agents_remember.models.lifecycles.memory_candidate import MemoryCandidatePairIdentity
from agents_remember.models.lifecycles.preparation import build_prepared_closeout_output
from agents_remember.models.memory import MemoryQualityCheckResponse
from agents_remember.serving.build_info import process_serving_build
from agents_remember.worktrees.integration.closeout.preparation import code_view
from agents_remember.worktrees.modules import onboarding
from agents_remember.worktrees.modules.onboarding_acceptance import OnboardingBodyGateEvidence


def _pair() -> MemoryCandidatePairIdentity:
    return MemoryCandidatePairIdentity(
        repoId="canonical-repo",
        contractPath="/contract",
        contractDigest="9" * 64,
        codeRoot="/code",
        memoryRoot="/memory",
        codeSourceBranch="super",
        codeWorkBranch="ar/leaf",
        codeBaseCommit="a" * 40,
        memorySourceBranch="super",
        memoryWorkBranch="ar/leaf",
        memoryBaseCommit="b" * 40,
        onboardingRoot="/memory/onboarding",
        ledgerPath="/memory/memory.md",
    )


def _identity(
    label: str,
    *,
    repo_id: str = "repo",
    detail_limit: int = 50,
) -> runs.QualityRunIdentity:
    return runs.QualityRunIdentity(
        repo_id=repo_id,
        scope=MemoryScopeIdentity(
            authority="leaf",
            authority_path=f"/scope/{label}",
            code_root=f"/code/{label}",
            onboarding_root=f"/memory/{label}/onboarding",
        ),
        checks=("check",),
        detail_limit=detail_limit,
        publish_curator_report=False,
    )


def _reference(kind: Any, seed: str) -> CertificateObjectReference:
    return CertificateObjectReference(
        kind=kind,
        semanticDigest=seed * 64,
        contentSha256=seed * 64,
        sizeBytes=1,
    )


def _prepared_fixture() -> tuple[Any, Any, Any, Any, Any]:
    intent_reference = _reference("preparation-intent", "a")
    output_reference = _reference("prepared-output", "b")
    tree = "c" * 40
    parent = "d" * 40
    raw = (
        f"tree {tree}\n"
        f"parent {parent}\n"
        "author Fixture <fixture@example.invalid> 0 +0000\n"
        "committer Fixture <fixture@example.invalid> 0 +0000\n"
        "\nprepared\n"
    ).encode()
    output = build_prepared_closeout_output(raw, intent_reference, disposition="created")
    intent = SimpleNamespace(
        leg="code",
        operationKey="e" * 64,
        generation=1,
        intentDigest=intent_reference.semanticDigest,
        logicalRoot="/code",
        repositoryIdentity="/repo.git",
        logicalRef="refs/heads/ar/leaf",
        expectedOldCommit=parent,
        parentCommit=parent,
        admittedTree=tree,
        privateRoot="/prepared",
        normalizedMessage="prepared",
        hookPolicy="strict-code-no-verify",
    )
    selected = SimpleNamespace(leg="code", intent=intent_reference, output=output_reference)
    record = SimpleNamespace(
        operationKey=intent.operationKey,
        generation=intent.generation,
        preparation=SimpleNamespace(legs=(selected,)),
    )
    contract = SimpleNamespace(
        contract_path=Path("/contract"),
        worktree_group=Path("/group"),
        repo_name="canonical-repo",
    )
    return contract, record, intent, selected, output


class MemoryQualityRunRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(runs._registry.clear)

    def _poll_until_settled(self, run_id: str) -> runs.QualityRunSnapshot:
        deadline = time.monotonic() + 5
        snapshot = None
        while time.monotonic() < deadline:
            snapshot = runs.poll_quality_run("repo", run_id)
            if snapshot is not None and snapshot.status != "running":
                return snapshot
            time.sleep(0.01)
        raise AssertionError(f"run {run_id} did not settle: {snapshot}")

    def test_start_poll_completed_failed_and_unknown(self) -> None:
        completed = runs.start_quality_run(_identity("complete"), lambda: {"ok": True})
        assert completed.run_id is not None
        snapshot = self._poll_until_settled(completed.run_id)
        self.assertEqual(snapshot.status, "completed")
        self.assertEqual(snapshot.result, {"ok": True})

        def fail() -> dict[str, object]:
            raise RuntimeError("probe failure")

        failed = runs.start_quality_run(_identity("fail"), fail)
        assert failed.run_id is not None
        failure = self._poll_until_settled(failed.run_id)
        self.assertEqual(failure.status, "failed")
        self.assertIn("probe failure", failure.error or "")
        self.assertIsNone(runs.poll_quality_run("repo", "missing"))

    def test_launch_failure_rolls_back_the_admitted_slot(self) -> None:
        with (
            mock.patch.object(threading.Thread, "start", side_effect=RuntimeError("no thread")),
            self.assertRaisesRegex(RuntimeError, "no thread"),
        ):
            runs.start_quality_run(_identity("launch-failure"), lambda: {"ok": True})
        self.assertEqual(runs._registry, {})

    def test_wrong_repository_poll_never_discloses_any_run_state(self) -> None:
        for status in ("running", "completed", "failed"):
            run_id = f"run-{status}"
            runs._registry[run_id] = runs._QualityRun(
                run_id=run_id,
                identity=_identity(status, repo_id="repo-a"),
                status=status,
                completed_at=None if status == "running" else time.monotonic(),
                result={"secret": status} if status == "completed" else None,
                error="secret failure" if status == "failed" else None,
            )
            self.assertIsNone(runs.poll_quality_run("repo-b", run_id))
            self.assertIsNotNone(runs.poll_quality_run("repo-a", run_id))


class MemoryQualityControllerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.addCleanup(runs._registry.clear)
        self.scope = MemoryScope(
            repo_id="canonical-repo",
            identity=MemoryScopeIdentity(
                authority="leaf",
                authority_path="/canonical/enclosure/contract",
                code_root="/code",
                onboarding_root="/memory/onboarding",
            ),
            code_root=Path("/code"),
            onboarding_root=Path("/memory/onboarding"),
            context=mock.Mock(),
            curator_report_path=Path("/enclosure/reports/curator-memory-quality.md"),
        )
        pair = _pair()
        self.candidate_scope = MemoryScope(
            repo_id="canonical-repo",
            identity=MemoryScopeIdentity(
                authority="leaf",
                authority_path=pair.contractPath,
                code_root=pair.codeRoot,
                onboarding_root=pair.onboardingRoot,
                pair_identity=pair,
            ),
            code_root=Path(pair.codeRoot),
            onboarding_root=Path(pair.onboardingRoot),
            context=mock.Mock(),
            curator_report_path=Path("/enclosure/reports/curator-memory-quality.md"),
            contract=mock.Mock(),
            pair_identity=pair,
        )

    def test_pair_change_during_derived_evidence_refuses_before_curator_publication(self) -> None:
        pair = _pair()
        error = MemoryCandidatePairError(
            "memory-candidate-pair-stale",
            "candidate pair changed while memory quality was running",
            failure=MemoryCandidatePairFailure(
                field="pairIdentity",
                contract_path=pair.contractPath,
                next_action="worktree_sync",
            ),
        )
        execution = controller.MemoryQualityExecution(
            config=mock.Mock(),
            scope=self.candidate_scope,
            checks=tuple(sorted(AVAILABLE_CHECKS)),
            detail_limit=50,
            publish_curator_report=True,
        )
        with (
            mock.patch.object(
                controller,
                "revalidate_memory_candidate_scope",
                side_effect=[self.candidate_scope, self.candidate_scope, error],
            ),
            mock.patch.object(controller, "prepare_memory_census", return_value=None),
            mock.patch.object(
                controller,
                "run_memory_quality_check",
                return_value={"ok": True, "checks": {}, "findings": []},
            ) as scan,
            mock.patch.object(
                controller,
                "_curator_candidate_inputs",
                return_value=controller._CuratorCandidateInputs("a" * 40, "b" * 40),
            ),
            mock.patch.object(
                controller,
                "check_missing_onboarding",
                return_value=mock.Mock(findings=[]),
            ),
            mock.patch.object(
                controller,
                "build_route_indexes",
                return_value=mock.Mock(stale_indexes=[]),
            ),
            mock.patch.object(
                controller,
                "split_commit_owned_findings",
                return_value=([], []),
            ),
            mock.patch.object(controller, "write_curator_checklist") as publish,
        ):
            result = controller._execute_or_refuse(execution)
        scan.assert_called_once()
        publish.assert_not_called()
        self.assertEqual(result["status"], "scope-refused")
        self.assertEqual(result["pairStatus"], "memory-candidate-pair-stale")

    def test_refresh_attestations_share_sidecar_and_route_body_gates(self) -> None:
        sidecar_plan = {
            "required": [{"source_path": "changed.py", "onboarding_file": "/memory/changed.py.md"}],
            "missing": [],
            "unsupported": [],
            "unonboarded": ["committed.py"],
        }
        sidecar_gate = {"stale": [], "untraced": [], "attested_no_impact": ["changed.py"]}
        overview_plan = {"required": [], "missing_metadata": []}
        overview_gate = {
            "stale": [],
            "untraced": [],
            "attested_no_impact": ["."],
            "stamped_without_body_review": [],
        }
        context = mock.Mock()
        with (
            mock.patch.object(
                onboarding,
                "validate_onboarding_refresh_plan_for_context",
                return_value=sidecar_plan,
            ) as validate_sidecars,
            mock.patch.object(
                onboarding,
                "classify_sidecar_updates",
                return_value=sidecar_gate,
            ),
            mock.patch.object(
                onboarding,
                "validate_route_overview_refresh_plan_for_context",
                return_value=overview_plan,
            ) as validate_overviews,
            mock.patch.object(
                onboarding,
                "classify_route_overview_updates",
                return_value=overview_gate,
            ),
        ):
            result = onboarding.validate_memory_refresh_attestations(
                context,
                ["changed.py"],
                working_paths=["changed.py"],
                body_gate=OnboardingBodyGateEvidence(
                    memory_tree=Path("/memory"),
                    memory_verified_commit="a" * 40,
                    accepted_no_impact=frozenset({"changed.py"}),
                ),
                route_body_gate=OnboardingBodyGateEvidence(
                    memory_tree=Path("/memory"),
                    memory_verified_commit="a" * 40,
                    accepted_no_impact=frozenset({"."}),
                ),
            )

        validate_sidecars.assert_called_once()
        validate_overviews.assert_called_once()
        self.assertEqual(
            result,
            {
                "attested_sidecars": ["changed.py"],
                "attested_overviews": ["."],
                "stamped_overviews": [],
                "unonboarded_paths": ["committed.py"],
            },
        )

    def test_refresh_attestations_reports_both_body_gate_failures(self) -> None:
        with (
            mock.patch.object(
                onboarding,
                "validate_onboarding_refresh_plan_for_context",
                side_effect=RuntimeError("stale sidecar"),
            ) as validate_sidecars,
            mock.patch.object(
                onboarding,
                "validate_route_overview_refresh_plan_for_context",
                side_effect=RuntimeError("stale overview"),
            ) as validate_overviews,
            self.assertRaisesRegex(
                RuntimeError,
                "sidecar onboarding: stale sidecar; route overview: stale overview",
            ),
        ):
            onboarding.validate_memory_refresh_attestations(mock.Mock(), ["changed.py"])

        validate_sidecars.assert_called_once()
        validate_overviews.assert_called_once()

    def test_a_dead_governing_overview_reaches_the_gated_repair_set(self) -> None:
        """D3/D16's actual defect was the product's SILENCE, so the wiring is what gets pinned.

        A correct checker whose findings never reach `repair_findings` still produces a clean
        `curatorActionableCount` — which is exactly how 41 dead declarations passed every gate.
        This drives `_attach_curator_checklist` with a real onboarding tree holding one card whose
        body link resolves to nothing, and asserts the finding arrives in the checklist the
        curator's completion loop gates on.
        """

        with TemporaryDirectory() as temporary:
            memory_root = Path(temporary)
            onboarding_root = memory_root / "onboarding"
            package = onboarding_root / "pkg" / "nested"
            package.mkdir(parents=True)
            (onboarding_root / "overview.md").write_text("# overview\n", encoding="utf-8")
            (package / "dead.md").write_text(
                "# dead\n\n"
                "| Field | Value |\n| --- | --- |\n"
                "| governingOverview | `../../overview.md` |\n\n"
                "## Governing Overview\n\n"
                "[Overview](../../../overview.md)\n\n"
                "## Purpose\n\nSeeded dead link.\n",
                encoding="utf-8",
            )
            pair = _pair()
            scope = MemoryScope(
                repo_id="canonical-repo",
                identity=MemoryScopeIdentity(
                    authority="leaf",
                    authority_path=pair.contractPath,
                    code_root=pair.codeRoot,
                    onboarding_root=onboarding_root.as_posix(),
                    pair_identity=pair,
                ),
                code_root=Path(pair.codeRoot),
                onboarding_root=onboarding_root,
                context=mock.Mock(),
                curator_report_path=memory_root / "reports" / "curator-memory-quality.md",
                contract=mock.Mock(),
                pair_identity=pair,
            )
            execution = controller.MemoryQualityExecution(
                config=mock.Mock(),
                scope=scope,
                checks=tuple(sorted(AVAILABLE_CHECKS)),
                detail_limit=50,
                publish_curator_report=True,
            )
            candidate_inputs = controller._CuratorCandidateInputs("a" * 40, "b" * 40)
            census = SimpleNamespace(
                scope=SimpleNamespace(pair_identity=pair, working_paths=(), committed_paths=()),
                result=SimpleNamespace(rows=[], blockers=[]),
            )
            response: dict[str, object] = {"checks": {}}
            with (
                mock.patch.object(
                    controller, "revalidate_memory_candidate_scope", return_value=scope
                ),
                mock.patch.object(
                    controller, "_curator_candidate_inputs", return_value=candidate_inputs
                ),
                mock.patch.object(
                    controller,
                    "check_missing_onboarding",
                    return_value={"missingCount": 0, "missing": []},
                ),
                mock.patch.object(
                    controller,
                    "build_route_indexes",
                    return_value=mock.Mock(stale_indexes=[]),
                ),
                mock.patch.object(controller, "split_commit_owned_findings", return_value=([], [])),
                mock.patch.object(controller, "write_curator_checklist") as publish,
                mock.patch.object(controller, "_attach_coherence_readiness"),
                mock.patch.object(controller, "_catalog_checks", return_value={}),
            ):
                controller._attach_curator_checklist(
                    execution,
                    {"checks": {}},
                    response,
                    candidate_inputs=candidate_inputs,
                    census=cast(Any, census),
                )

        published = publish.call_args.args[0]
        codes = [
            row["code"]
            for row in published.repair_findings
            if row["check"] == "integrity.governing_overview_resolution"
        ]
        self.assertEqual(codes, ["governing-overview-link-unresolved"])
        self.assertEqual(
            [
                row["path"]
                for row in published.repair_findings
                if row["check"] == "integrity.governing_overview_resolution"
            ],
            ["pkg/nested/dead.md"],
        )
        self.assertEqual(
            response["governingOverviewResolution"]["unresolvedLinkCount"],  # type: ignore[index]
            1,
        )

    def test_curator_refresh_keeps_deleted_history_out_of_current_working_targets(self) -> None:
        sidecar_plan = {
            "required": [],
            "missing": [],
            "unsupported": [],
            "unonboarded": ["deleted.py"],
        }
        overview_plan = {"required": [], "missing_metadata": []}
        sidecar_gate = {"stale": [], "untraced": [], "attested_no_impact": []}
        overview_gate = {
            "stale": [],
            "untraced": [],
            "attested_no_impact": [],
            "stamped_without_body_review": [],
        }
        with TemporaryDirectory() as temporary:
            code_root = Path(temporary)
            (code_root / "current.py").write_text("VALUE = 1\n", encoding="utf-8")
            scope = cast(MemoryScope, SimpleNamespace(quality_code_root=code_root))
            current_paths = controller._current_working_code_paths(
                scope, ("deleted.py", "current.py")
            )
            with (
                mock.patch.object(
                    onboarding,
                    "validate_onboarding_refresh_plan_for_context",
                    return_value=sidecar_plan,
                ) as validate_sidecars,
                mock.patch.object(
                    onboarding, "classify_sidecar_updates", return_value=sidecar_gate
                ),
                mock.patch.object(
                    onboarding,
                    "validate_route_overview_refresh_plan_for_context",
                    return_value=overview_plan,
                ),
                mock.patch.object(
                    onboarding,
                    "classify_route_overview_updates",
                    return_value=overview_gate,
                ),
            ):
                onboarding.validate_memory_refresh_attestations(
                    mock.Mock(),
                    ["deleted.py", "current.py"],
                    working_paths=current_paths,
                )

        self.assertEqual(current_paths, ["current.py"])
        self.assertEqual(validate_sidecars.call_args.kwargs["working_paths"], ["current.py"])

    def test_selected_prepared_view_keeps_logical_pair_and_private_quality_root(self) -> None:
        contract, record, intent, _selected, output = _prepared_fixture()
        pair = _pair()
        store = mock.Mock()
        store.read.return_value = record
        capability = object()
        observation = SimpleNamespace(
            state="committed",
            raw_commit=(
                f"tree {intent.admittedTree}\n"
                f"parent {intent.parentCommit}\n"
                "author Fixture <fixture@example.invalid> 0 +0000\n"
                "committer Fixture <fixture@example.invalid> 0 +0000\n"
                "\nprepared\n"
            ).encode(),
        )
        with (
            mock.patch.object(code_view, "selected_preparation_intents", return_value=(intent,)),
            mock.patch.object(code_view, "require_preparation_logical_refs"),
            mock.patch.object(code_view, "certificate_store", return_value=mock.Mock()),
            mock.patch.object(code_view, "load_typed", return_value=output),
            mock.patch.object(code_view, "require_prepared_output_matches_intent"),
            mock.patch.object(code_view, "private_git_binding", return_value=mock.sentinel.binding),
            mock.patch.object(
                code_view, "admit_private_git_preparation", return_value=capability
            ) as admit,
            mock.patch.object(code_view, "inspect_git_preparation", return_value=observation),
            mock.patch.object(
                code_view,
                "observe_git_preparation_policy",
                return_value=mock.Mock(),
            ),
        ):
            view = code_view.observe_selected_prepared_code_view(contract, record, store, pair)
        self.assertEqual(view.logicalPair, pair)
        self.assertEqual(view.physicalCodeRoot, "/prepared")
        self.assertEqual(view.codeCommit, output.commit)
        admit.assert_called_once()

    def test_invalid_selected_prepared_view_refuses_with_recovery_reason(self) -> None:
        contract, record, _intent, _selected, _output = _prepared_fixture()
        pair = _pair()
        store = mock.Mock()
        store.read.return_value = record
        with (
            mock.patch.object(
                memory_scope,
                "located_lifecycle_operation_store",
                return_value=store,
            ),
            mock.patch(
                "agents_remember.application.memory_scope.observe_selected_prepared_code_view",
                side_effect=GitPreparationError(
                    "Git preparation policy differs from selected intent"
                ),
            ),
            self.assertRaises(MemoryCandidatePairError) as raised,
        ):
            memory_scope._resolve_prepared_code_source(contract, pair)
        self.assertEqual(raised.exception.status, "prepared-code-view-invalid")
        self.assertIn("policy differs", str(raised.exception.observed["reason"]))
        self.assertEqual(raised.exception.next_action, "recover")

    def test_stale_prepared_view_yields_to_current_logical_candidate(self) -> None:
        contract, _record, _intent, _selected, _output = _prepared_fixture()
        pair = _pair()
        selected = SimpleNamespace(leg="code", output=object())
        record = SimpleNamespace(preparation=SimpleNamespace(legs=(selected,)))
        store = mock.Mock()
        store.read.return_value = record
        stale_view = SimpleNamespace(codeTree="old-tree", codeCommit="p" * 40)
        current_candidate = SimpleNamespace(codeCandidateTree="new-tree")
        with (
            mock.patch.object(
                memory_scope, "located_lifecycle_operation_store", return_value=store
            ),
            mock.patch.object(
                memory_scope,
                "observe_selected_prepared_code_view",
                return_value=stale_view,
            ),
            mock.patch.object(
                memory_scope,
                "capture_future_code_candidate",
                return_value=current_candidate,
            ),
            mock.patch.object(
                memory_scope,
                "selected_prepared_code_history_commits",
                return_value=(stale_view.codeCommit,),
            ),
        ):
            view, history_commits = memory_scope._resolve_prepared_code_source(contract, pair)
        self.assertIsNone(view)
        self.assertEqual(history_commits, (stale_view.codeCommit,))

    def test_matching_prepared_tree_binds_census_and_curator_candidate(self) -> None:
        current_tree = "b" * 40
        prepared = SimpleNamespace(codeTree=current_tree, codeCommit="c" * 40)
        scope = replace(self.candidate_scope, prepared_code_view=prepared)
        with mock.patch.object(
            controller,
            "worktree_candidate_tree",
            side_effect=[current_tree, "d" * 40],
        ):
            candidate = controller._curator_candidate_inputs(scope)
        code_input = census._prepared_code_input(scope)
        assert code_input is not None
        self.assertEqual(candidate.code_tree, current_tree)
        self.assertEqual(code_input.targetCodeTree, candidate.code_tree)


class MeasuringBuildStampTests(unittest.TestCase):
    """Every memory-quality and citation response names the build that measured (D-33).

    D-33 measured that the MCP tool surface executes a FIXED serving build while the candidate
    under measurement carries different code, and that the two are indistinguishable in the
    output. For the items that change the measuring machinery itself, a tool-produced count can
    therefore never show the fix. These cases make the ruler nameable in the response, so a
    reader -- and the terminal report -- can say which build produced a count.
    """

    def test_every_memory_quality_entry_point_stamps_the_serving_build(self) -> None:
        """All three modes carry the resolved stamp, not one of them.

        Red if a wrapper is dropped, if a mode returns its body's payload unwrapped, or if the
        stamp stops being the process's resolved identity: an unstamped envelope is exactly the
        state D-33 recorded, where a count cannot be attributed to a ruler.
        """

        resolved = process_serving_build()
        cases = (
            ("run", controller.run_memory_quality_request, "run"),
            ("start", controller.start_memory_quality_request, "start"),
            ("poll", controller.poll_memory_quality_request, "poll"),
        )
        for label, entry, body_name in cases:
            with self.subTest(mode=label):
                with mock.patch.object(
                    controller, f"_{body_name}_memory_quality_request", return_value={"ok": True}
                ):
                    response = entry(mock.Mock(), mock.Mock())
                assert isinstance(response, dict)
                self.assertEqual(response["servingBuild"]["commit"], resolved.commit)
                self.assertEqual(response["servingBuild"]["sourceDigest"], resolved.source_digest)
                self.assertTrue(response["servingBuild"]["commit"])

    def test_the_stamp_is_declared_on_the_responses_that_carry_it(self) -> None:
        """The field is part of the declared contract, not tolerated drift.

        ``FlexibleToolResponse`` sets ``extra="allow"``, so an undeclared key would validate and
        still be invisible in the tool's own schema. Red if the declaration is removed, or if the
        stamp stops resolving to the shared boot payload.
        """

        for model in (MemoryQualityCheckResponse,):
            self.assertIn("servingBuild", model.model_fields)
        stamp = memory_tools.measuring_build_stamp()["servingBuild"]
        self.assertEqual(
            ServingBuildPayload.model_validate(stamp), process_serving_build().payload()
        )

    def test_the_citation_repair_response_names_its_ruler(self) -> None:
        """``citation_fix`` rewrites ranges with the serving build's rules.

        The repair engine is part of the measuring machinery this leaf changes, so its response
        must name the build too. Red if the tool's return stops including the stamp: the counts it
        reports would then be attributable to no build at all.
        """

        scope = SimpleNamespace(repo_id="repo", onboarding_root=Path("/memory/onboarding"))
        with (
            mock.patch.object(memory_tools, "_leaf_memory_writer_scope", return_value=scope),
            mock.patch.object(memory_tools, "_citation_trees", return_value=mock.Mock()),
            mock.patch.object(memory_tools.fixer, "fix_onboarding_root", return_value={"ok": True}),
        ):
            response = memory_tools.citation_fix_tool(
                mock.Mock(), repo_id="repo", contract_path="/contract"
            )
        self.assertEqual(
            response["servingBuild"], memory_tools.measuring_build_stamp()["servingBuild"]
        )
        self.assertEqual(response["servingBuild"]["commit"], process_serving_build().commit)


class CloseoutOwnedProvenanceRoutingTests(unittest.TestCase):
    """A closeout-owned row is REPORTED in the closeout-owned section, never dropped.

    ``claim_reopen`` publishes the rows no curator edit can discharge in its own bucket instead of
    the enforced set. The checklist is where that classification has to land -- in the section the
    report already keeps for the closing stamp -- and this case is the wire between the two. Red if
    the collection stops reading the bucket: the rows would then be in neither the repairable set
    nor the closeout-owned section, which is the silence the disposition rules forbid.
    """

    ROW: ClassVar[dict[str, Any]] = {
        "code": "citation_provenance_invalid",
        "path": "a.md",
        "closeoutOwned": True,
    }

    def checklist_sets(self, payload: dict) -> tuple[list[dict], list[dict]]:
        with mock.patch.object(controller, "split_commit_owned_findings", return_value=([], [])):
            return controller._checklist_finding_sets([], payload, Path("/memory/onboarding"))

    def test_the_checklists_commit_owned_set_collects_the_checks_own_bucket(self) -> None:
        payload = {
            "checks": {
                "style.citations.claim_reopen": {
                    "findings": [],
                    "closeoutOwnedFindings": [self.ROW],
                },
                "style.update_history.history_order": {"findings": []},
            }
        }
        self.assertEqual(self.checklist_sets(payload), ([], [self.ROW]))
        self.assertEqual(self.checklist_sets({}), ([], []))
        self.assertEqual(self.checklist_sets({"checks": []}), ([], []))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
