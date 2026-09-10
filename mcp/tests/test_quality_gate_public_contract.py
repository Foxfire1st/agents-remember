"""Focused public contracts for immutable quality results and closeout readiness."""

from __future__ import annotations

import tempfile
import unittest
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import patch

from agents_remember.application.lifecycle import terminal_rail_failure
from agents_remember.certification import (
    READINESS_SURFACES,
    build_rail_result,
    canonicalize_registry,
    compile_certification_plan,
    compile_closeout_readiness,
    compile_finalization_authority,
    compile_gate_certificate,
    compile_gate_result_manifest,
    readiness_projection_bytes,
)
from agents_remember.certification.certificate_admission import gate_semantic_digest
from agents_remember.certification.certificate_models import (
    AdmissionGateIdentity,
    CertificationAdmissionManifest,
    CertificationAdmissionSemanticEnvelope,
    CoherenceSubrecordIdentity,
    CreationProvenance,
    FinalizationCertificateAuthority,
    FinalizationCurrentInputs,
    GateCertificate,
    GateCertificateIssuanceContext,
    GateFiveSemanticInputs,
)
from agents_remember.certification.digests import content_digest
from agents_remember.certification.models import (
    ArtifactDeclaration,
    CandidateIdentity,
    CanonicalRailRegistry,
    CertificationPlan,
    GateResultAdmission,
    GateResultManifest,
    RailAdapterDefinition,
    RailApplicability,
    RailArtifactResult,
    RailClass,
    RailDefinition,
    RailEvidenceContract,
    RailEvidenceReference,
    RailPosture,
    RailRegistry,
    RailRuntimeInputs,
    RailTerminalObservation,
    RegistryProfile,
)
from agents_remember.certification.readiness_models import (
    CloseoutReadinessInput,
    DiagnosticReadinessObservation,
    GateReadinessObservation,
    LifecycleReadinessObservation,
    ProfileReadinessObservation,
    ReadinessRevision,
)
from agents_remember.certification.repository_profiles.models import (
    RepositoryGatePlan,
    RepositoryProfilePlan,
    repository_gate_plan_digest,
)
from agents_remember.errors import FinalCertificationError
from agents_remember.memory_quality.incremental_scope.errors import (
    GateFiveClosureRefusedError,
    ScopeFailure,
)
from agents_remember.models.certification.base import GateId, RailIdentity
from integration_certification_test_support import selected_code_fixture


class QualityGatePublicContractTests(unittest.TestCase):
    def test_recovery_refuses_same_id_decoder_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            selected = selected_code_fixture(Path(tmp))
            original = selected.terminals[-1]
            changed = replace(
                original.publication,
                result_decoder=original.publication.result_decoder.model_copy(
                    update={"passedValue": "accepted"}
                ),
            )
            forged = replace(
                selected,
                terminals=(
                    *selected.terminals[:-1],
                    replace(original, publication=changed),
                ),
            )
            self.assertEqual(changed.generation, original.publication.generation)
            with self.assertRaisesRegex(ValueError, "dependencies do not match"):
                forged.render()

    def test_terminal_projection_preserves_prepared_memory_catalog_failure(self) -> None:
        error = FinalCertificationError(
            "prepared-memory-certification-red",
            "at least one final catalog item failed",
            next_action="memory_quality_check",
        )
        result = _terminal_result(
            _prepared_catalog_payload(),
            status="failed",
            exit_code=1,
            error=error,
        )
        terminal = cast(dict[str, object], result["terminalResult"])
        counts = cast(dict[str, int], result["counts"])
        rails = cast(dict[str, list[dict[str, object]]], result["rails"])
        error_payload = cast(dict[str, object], result["error"])

        self.assertEqual(
            terminal,
            {"disposition": "red", "status": "failed", "exitCode": 1},
        )
        self.assertEqual(
            counts,
            {"passed": 10, "failed": 2, "blocked": 0, "skipped": 0, "notApplicable": 0},
        )
        self.assertEqual(
            {row["railId"]: row["findingCount"] for row in rails["failed"]},
            {
                "integrity.missing_onboarding": 10,
                "integrity.onboarding_drift_check.summary": 135,
            },
        )
        self.assertTrue(all("reference" not in row for row in rails["failed"]))
        self.assertEqual(error_payload["type"], "FinalCertificationError")
        self.assertEqual(error_payload["status"], "prepared-memory-certification-red")
        self.assertEqual(error_payload["detail"], "at least one final catalog item failed")
        self.assertEqual(error_payload["nextAction"], "memory_quality_check")
        self.assertEqual(
            result["evidence"],
            [{"path": "clean-quality-results.json", "sha256": "r" * 64, "size": 22192}],
        )

    def test_terminal_projection_accepts_green_prepared_memory_catalog(self) -> None:
        payload = _prepared_catalog_payload()
        certification = cast(dict[str, object], payload["certification"])
        attestation = cast(dict[str, object], certification["attestation"])
        catalog = cast(list[dict[str, object]], attestation["catalog"])
        for item in catalog:
            item["status"] = "pass"
            item["findingCount"] = 0
            item["reference"] = None
        payload["status"] = "passed"
        payload["exitCode"] = 0
        certification["state"] = "green"
        certification["reason"] = None
        attestation["statusCounts"] = {"blocked": 0, "fail": 0, "not-applicable": 0, "pass": 12}

        result = _terminal_result(payload, status="passed", exit_code=0)
        terminal = cast(dict[str, object], result["terminalResult"])
        counts = cast(dict[str, int], result["counts"])
        self.assertEqual(terminal["disposition"], "green")
        self.assertEqual(terminal["status"], "passed")
        self.assertEqual(counts["passed"], 12)
        self.assertEqual(counts["failed"], 0)

    def test_terminal_projection_keeps_ordinary_gate_catalog_shape(self) -> None:
        payload = {
            "status": "passed",
            "exitCode": 0,
            "gates": [
                {
                    "rails": [
                        {
                            "identity": {"railId": "ordinary-lint", "version": "1.0.0"},
                            "status": "pass",
                            "posture": "enforcing",
                        }
                    ]
                }
            ],
        }
        result = _terminal_result(payload, status="passed", exit_code=0)
        terminal = cast(dict[str, object], result["terminalResult"])
        counts = cast(dict[str, int], result["counts"])
        self.assertEqual(terminal["disposition"], "green")
        self.assertEqual(counts["passed"], 1)
        self.assertEqual(counts["failed"], 0)

    def test_terminal_projection_preserves_incremental_scope_failure_context(self) -> None:
        error = GateFiveClosureRefusedError(
            ScopeFailure(
                code="checker-execution-failed",
                detail=(
                    "incremental checker failed before publishing a result: "
                    "ValueError: selected document is unreadable"
                ),
                checker="citation-range",
                node="memory:onboarding/example.md",
            )
        )
        with patch.object(
            terminal_rail_failure,
            "_read_published_report",
            return_value=terminal_rail_failure.PublishedReportRead(state="missing"),
        ):
            result = terminal_rail_failure.terminal_worker_failure_result(
                operation_kind="closeout",
                generation=1,
                candidate_tree="c" * 40,
                error=error,
            )

        self.assertEqual(
            result["error"],
            {
                "type": "GateFiveClosureRefusedError",
                "status": "gate-5-closure-refused",
                "stage": "memory-scope",
                "reason": "checker-execution-failed",
                "detail": (
                    "incremental checker failed before publishing a result: "
                    "ValueError: selected document is unreadable"
                ),
                "checker": "citation-range",
                "node": "memory:onboarding/example.md",
            },
        )


_CANDIDATE = CandidateIdentity(kind="git-tree", value="c" * 40)
_PROFILE = "portable-ci"
_PROFILE_DIGEST = "d" * 64
_REVISION = ReadinessRevision(generationId="generation-9", revision=4)
_DIGEST = "a" * 64
_CLASS_BY_GATE: dict[GateId, RailClass] = {
    1: "pre-test-quality",
    2: "ordinary-test-suite",
    3: "post-test-quality",
    4: "integration-test",
    5: "memory-quality",
}

_L38_PREPARED_CATALOG: tuple[dict[str, Any], ...] = (
    {
        "item": {"itemId": "affected.closure", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "ee39b23bc9e8920c29785fe8e4dff262722fed4cef15135bb1fc67689510ff5f",
        "reference": None,
    },
    {
        "item": {"itemId": "candidate.code_memory_pair", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "93dec059bdc627c2fbeb7886e09b7b2c3c285c120fd04a2d1e90750f3da8e244",
        "reference": None,
    },
    {
        "item": {"itemId": "coherence.record", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "85a63f4d9689a85d77dc44a1d7d8b67a520ca27e5ce168539ec2ae65623d2622",
        "reference": None,
    },
    {
        "item": {"itemId": "integrity.missing_onboarding", "version": "1.0.0"},
        "status": "fail",
        "findingCount": 10,
        "subresultDigest": "675e741eca30235d6c210f18709c901a7f6fe5b52c3708f4e3074834415b36f4",
        "reference": None,
    },
    {
        "item": {
            "itemId": "integrity.onboarding_drift_check.summary",
            "version": "1.0.0",
        },
        "status": "fail",
        "findingCount": 135,
        "subresultDigest": "a59991ed46ae0ad380e16ad69d2337a9db41dfa1a54ac412bf1610e809c132e6",
        "reference": "/home/firefox/projects/ar-coordination/temp/drift-reports/code/code_HEAD_drift-report.md",
    },
    {
        "item": {"itemId": "route_index.alignment", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "4f67e1f2d39fc7f1f205320282c7dd754ecb01d4e0bdf54d8103f056cb7eefc6",
        "reference": None,
    },
    {
        "item": {"itemId": "style.citations.claim_reopen", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "20ba8ba4d57d72a74bc8ab1157aa851c83f921aaf7f08be22fd9b2bfec1955f7",
        "reference": None,
    },
    {
        "item": {"itemId": "style.citations.range_resolution", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "3e03909af65774855c7670b1a0f6e2529514e98f701dc9dfb1c6e863f5879f53",
        "reference": None,
    },
    {
        "item": {"itemId": "style.document_shape.diff_markers", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "d7ce3428c91d25c2234e86f96815bc53b909e2b4b36b71aee3dce9f1e4e40d19",
        "reference": None,
    },
    {
        "item": {
            "itemId": "style.document_shape.entity_catalog_alignment",
            "version": "1.0.0",
        },
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "c45e4483c00cd75ad296de9a2a3afedf95f3a0b66c2e6602a0f276d93007f31b",
        "reference": None,
    },
    {
        "item": {"itemId": "style.document_shape.tables", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "8bbe3c2b1d6a8bbf6eb8204844f76ae4a3e731dbe7404869d6e980d0f509e8ec",
        "reference": None,
    },
    {
        "item": {"itemId": "style.update_history.history_order", "version": "1.0.0"},
        "status": "pass",
        "findingCount": 0,
        "subresultDigest": "7854789c723f3825d302186b46d55130a484ece7e1df47f0508b59028fbbb273",
        "reference": None,
    },
)


def _prepared_catalog_payload() -> dict[str, object]:
    return {
        "status": "failed",
        "exitCode": 1,
        "producer": "prepared-memory-certification",
        "preparedCandidate": {},
        "certification": {
            "state": "red",
            "reason": "at least one final catalog item failed",
            "attestation": {
                "statusCounts": {"blocked": 0, "fail": 2, "not-applicable": 0, "pass": 10},
                "catalog": [deepcopy(item) for item in _L38_PREPARED_CATALOG],
            },
        },
    }


def _published_report(
    payload: dict[str, object], *, status: str, exit_code: int
) -> terminal_rail_failure.PublishedReportRead:
    manifest = cast(
        Any,
        SimpleNamespace(
            profile_digest="p" * 64,
            profile_plan_digest="q" * 64,
            profile_selection_id="atomic-leaf-closeout-targeted",
            executor_adapter_id="certifying-dagger",
            result_decoder=SimpleNamespace(
                model_dump=lambda **_kwargs: {"decoderId": "quality-terminal-result"}
            ),
            runtime_authority_digest=None,
            candidate_tree="c" * 40,
            generation=98,
            files={"clean-quality-results.json": SimpleNamespace(sha256="r" * 64, size=22192)},
        ),
    )
    return terminal_rail_failure.PublishedReportRead(
        state="published",
        manifest=manifest,
        payload=payload,
        status=status,
        exit_code=exit_code,
    )


def _terminal_result(
    payload: dict[str, object],
    *,
    status: str,
    exit_code: int,
    error: BaseException | None = None,
) -> dict[str, object]:
    with patch.object(
        terminal_rail_failure,
        "_read_published_report",
        return_value=_published_report(payload, status=status, exit_code=exit_code),
    ):
        return terminal_rail_failure.terminal_worker_failure_result(
            operation_kind="closeout",
            generation=1,
            candidate_tree="c" * 40,
            error=error,
        )


@dataclass(frozen=True)
class _ReadinessScenario:
    registry: CanonicalRailRegistry
    plan: CertificationPlan
    repository_plan: RepositoryProfilePlan
    admission: CertificationAdmissionManifest
    manifests: tuple[GateResultManifest, ...]
    certificates: tuple[GateCertificate, ...]
    finalization_inputs: FinalizationCurrentInputs
    finalization: FinalizationCertificateAuthority


def _readiness_identity(name: str) -> RailIdentity:
    return RailIdentity(railId=name, version="1.0.0")


def _readiness_rail(
    name: str,
    gate: GateId,
    *,
    prerequisite: str | None = None,
    posture: RailPosture = "enforcing",
    applicable: bool = True,
) -> RailDefinition:
    output = (
        (
            ArtifactDeclaration(
                artifactId="suite-data",
                schemaVersion="suite/v1",
                mediaType="application/json",
            ),
        )
        if gate == 2
        else ()
    )
    return RailDefinition(
        identity=_readiness_identity(name),
        gate=gate,
        railClass=_CLASS_BY_GATE[gate],
        authority="memory-domain" if gate == 5 else "repository-profile",
        ownerClass="portable-owner",
        correctiveOwner="portable-owner",
        posture=posture,
        orderKey=name,
        prerequisites=(_readiness_identity(prerequisite),) if prerequisite else (),
        requiredArtifacts=("suite-data",) if gate == 3 else (),
        adapter=RailAdapterDefinition(
            adapterKind="process-adapter",
            adapterId=f"{name}-adapter",
            configurationDigest=_DIGEST,
            executionEvidence=f"adapter://{name}",
        ),
        runtimeInputs=RailRuntimeInputs(runtimeIdentity="portable-runtime"),
        applicability=(
            RailApplicability(
                profileId=_PROFILE,
                status="applicable" if applicable else "not-applicable",
                selectionIdentity=f"selection:{name}",
                population="exact population" if applicable else None,
                reason=None if applicable else "profile excludes memory validation",
            ),
        ),
        evidenceContract=(
            RailEvidenceContract(
                evidenceId=f"{name}-evidence",
                mediaType="application/json",
                maxBytes=256,
            ),
        ),
        outputArtifacts=output,
    )


def _readiness_registry(*, diagnostic: bool = False) -> RailRegistry:
    rails = (
        _readiness_rail("advisory", 1, posture="report-only"),
        _readiness_rail("lint", 1),
        _readiness_rail("suite", 2, prerequisite="lint"),
        _readiness_rail("coverage", 3, prerequisite="suite"),
        _readiness_rail("e2e", 4, prerequisite="coverage"),
        _readiness_rail("memory", 5, prerequisite="e2e", applicable=False),
    )
    return RailRegistry(
        registryId="portable-readiness",
        repositoryId="sample-repository",
        profiles=(
            RegistryProfile(
                profileId=_PROFILE,
                kind="diagnostic" if diagnostic else "certifying",
                gates=(1,) if diagnostic else (1, 2, 3, 4, 5),
            ),
        ),
        rails=rails[:2] if diagnostic else rails,
    )


def _readiness_repository_gate(
    plan: CertificationPlan,
    gate: GateId,
) -> RepositoryGatePlan:
    gate_plan = plan.gates[gate - 1]
    payload = {
        "schemaVersion": "repository-gate-plan/v1",
        "profileDigest": _PROFILE_DIGEST,
        "candidateIdentity": _CANDIDATE.model_dump(mode="json"),
        "selectionId": _PROFILE,
        "purpose": "closeout",
        "mode": "targeted",
        "gate": gate,
        "gatePrerequisites": list(range(1, gate)),
        "applicability": "applicable",
        "reason": None,
        "rails": [rail.model_dump(mode="json") for rail in gate_plan.rails],
        "semanticInputs": [],
        "waves": [
            [identity.model_dump(mode="json") for identity in wave] for wave in gate_plan.waves
        ],
    }
    return RepositoryGatePlan(**payload, planDigest=repository_gate_plan_digest(payload))


def _readiness_repository_plan(plan: CertificationPlan) -> RepositoryProfilePlan:
    gates = tuple(_readiness_repository_gate(plan, gate) for gate in (1, 2, 3, 4))
    payload = {
        "schemaVersion": "repository-profile-plan/v1",
        "profileDigest": _PROFILE_DIGEST,
        "candidateIdentity": _CANDIDATE.model_dump(mode="json"),
        "selectionId": _PROFILE,
        "purpose": "closeout",
        "mode": "targeted",
        "gates": [gate.model_dump(mode="json") for gate in gates],
        "sourceSelection": None,
    }
    return RepositoryProfilePlan(**payload, planDigest=content_digest(payload))


def _readiness_provenance() -> CreationProvenance:
    return CreationProvenance(
        createdAt="2026-09-02T00:00:00+00:00",
        producer="readiness-test",
        evidenceRef="evidence://readiness-test",
    )


def _readiness_admission(
    plan: CertificationPlan,
    repository_plan: RepositoryProfilePlan,
) -> CertificationAdmissionManifest:
    envelope = CertificationAdmissionSemanticEnvelope(
        repositoryId="sample-repository",
        candidateCodeTree=_CANDIDATE,
        profileId=_PROFILE,
        certificationPlanDigest=plan.planDigest,
        admittedProfileDigest=_PROFILE_DIGEST,
        registryDigest=plan.registryDigest,
        gates=tuple(
            AdmissionGateIdentity(
                gate=gate.gate,
                gatePlanDigest=gate.planDigest,
                gateSemanticDigest=gate_semantic_digest(gate),
                repositoryGatePlanDigest=(
                    repository_plan.gates[gate.gate - 1].planDigest if gate.gate <= 4 else None
                ),
                semanticInputs=(),
            )
            for gate in plan.gates
        ),
    )
    return CertificationAdmissionManifest(
        semanticEnvelope=envelope,
        admissionDigest=content_digest(envelope),
        provenance=_readiness_provenance(),
    )


def _readiness_manifest(
    registry: CanonicalRailRegistry,
    plan: CertificationPlan,
    gate: GateId,
    *,
    enforcing_failure: bool = False,
) -> GateResultManifest:
    gate_plan = plan.gates[gate - 1]
    results = []
    for rail in gate_plan.rails:
        status = "not-applicable" if rail.applicability.status == "not-applicable" else "pass"
        if enforcing_failure and rail.posture == "enforcing":
            status = "fail"
        if rail.posture == "report-only":
            status = "fail"
        artifacts = tuple(
            RailArtifactResult(
                artifactId=item.artifactId,
                sha256=content_digest({"artifact": item.artifactId}),
                size=32,
                evidenceRef=f"artifact://{item.artifactId}",
            )
            for item in rail.outputArtifacts
            if status == "pass"
        )
        observation = RailTerminalObservation(
            rail=rail.identity,
            status=status,
            code=f"{rail.identity.railId}-{status}",
            artifacts=artifacts,
            evidence=tuple(
                RailEvidenceReference(
                    evidenceId=item.evidenceId,
                    sha256=content_digest({"evidence": item.evidenceId}),
                    size=32,
                    reference=f"evidence://{item.evidenceId}",
                )
                for item in rail.evidenceContract
            ),
        )
        results.append(build_rail_result(gate_plan, observation))
    return compile_gate_result_manifest(
        registry,
        plan,
        gate_plan,
        results,
        GateResultAdmission(
            profileId=_PROFILE,
            candidateIdentity=_CANDIDATE,
            altitude=plan.profileKind,
        ),
    )


def _readiness_memory_inputs() -> GateFiveSemanticInputs:
    return GateFiveSemanticInputs(
        memoryTree=CandidateIdentity(kind="git-tree", value="m" * 40),
        affectedClosurePlanDigest=content_digest({"closure": "exact"}),
        memoryCheckerRegistryDigest=content_digest({"registry": "memory"}),
        coherenceSubrecords=(
            CoherenceSubrecordIdentity(
                subrecordId="route-entity",
                contentDigest=content_digest({"coherence": "current"}),
            ),
        ),
        candidatePairAuthorityDigest=content_digest({"pair": "current"}),
    )


def _readiness_scenario() -> _ReadinessScenario:
    registry = canonicalize_registry(_readiness_registry())
    plan = compile_certification_plan(
        registry,
        profile_id=_PROFILE,
        candidate_identity=_CANDIDATE,
    )
    repository_plan = _readiness_repository_plan(plan)
    admission = _readiness_admission(plan, repository_plan)
    manifests = tuple(_readiness_manifest(registry, plan, gate) for gate in (1, 2, 3, 4, 5))
    memory_inputs = _readiness_memory_inputs()
    certificates: list[GateCertificate] = []
    for gate, manifest in enumerate(manifests, 1):
        certificates.append(
            compile_gate_certificate(
                admission,
                plan.gates[gate - 1],
                manifest,
                certificates,
                GateCertificateIssuanceContext(
                    provenance=_readiness_provenance(),
                    gateFiveInputs=memory_inputs if gate == 5 else None,
                ),
            )
        )
    finalization_inputs = FinalizationCurrentInputs(
        gateFiveInputs=memory_inputs,
        taskIntentAuthorityDigest=content_digest({"task": "current"}),
        journalAuthorityDigest=content_digest({"journal": "current"}),
    )
    finalization = compile_finalization_authority(
        admission,
        certificates,
        finalization_inputs,
        _readiness_provenance(),
    )
    return _ReadinessScenario(
        registry,
        plan,
        repository_plan,
        admission,
        manifests,
        tuple(certificates),
        finalization_inputs,
        finalization,
    )


def _complete_readiness_input(scenario: _ReadinessScenario) -> CloseoutReadinessInput:
    return CloseoutReadinessInput(
        revision=_REVISION,
        repositoryId="sample-repository",
        certificationPlan=scenario.plan,
        profile=ProfileReadinessObservation(
            revision=_REVISION,
            state="admitted-current",
            repositoryPlan=scenario.repository_plan,
        ),
        lifecycle=LifecycleReadinessObservation(revision=_REVISION, state="finalized"),
        gates=tuple(
            GateReadinessObservation(
                revision=_REVISION,
                gate=gate,
                state="passed",
                resultManifest=scenario.manifests[gate - 1],
                certificateState="current-green",
                certificate=scenario.certificates[gate - 1],
            )
            for gate in (1, 2, 3, 4, 5)
        ),
        admission=scenario.admission,
        gateFiveInputs=scenario.finalization_inputs.gateFiveInputs,
        finalizationAuthority=scenario.finalization,
        finalizationInputs=scenario.finalization_inputs,
    )


def test_closeout_readiness_is_lossless_on_every_surface() -> None:
    source = _complete_readiness_input(_readiness_scenario())
    projection = compile_closeout_readiness(source)

    assert projection.certificationReady is True
    assert projection.lifecycle.state == "finalized"
    assert [gate.state for gate in projection.gates] == ["passed"] * 5
    assert projection.gates[0].rails[0].state == "report-only-fail"
    assert projection.gates[4].rails[0].state == "not-applicable"
    rendered = {
        readiness_projection_bytes(source, surface=surface) for surface in READINESS_SURFACES
    }
    assert len(rendered) == 1
    assert source.finalizationAuthority is not None
    assert source.finalizationAuthority.authorityDigest.encode() in next(iter(rendered))


def test_diagnostic_readiness_stays_non_certifying_with_matching_rails() -> None:
    scenario = _readiness_scenario()
    source = _complete_readiness_input(scenario)
    diagnostic_registry = canonicalize_registry(_readiness_registry(diagnostic=True))
    diagnostic_plan = compile_certification_plan(
        diagnostic_registry,
        profile_id=_PROFILE,
        candidate_identity=_CANDIDATE,
    )
    diagnostic = DiagnosticReadinessObservation(
        revision=_REVISION,
        plan=diagnostic_plan,
        resultManifest=_readiness_manifest(diagnostic_registry, diagnostic_plan, 1),
    )
    projection = compile_closeout_readiness(
        source.model_copy(update={"diagnostics": (diagnostic,)})
    )

    assert projection.diagnostics[0].rails == projection.gates[0].rails
    assert projection.diagnostics[0].certificationPlanDigest != projection.certificationPlanDigest
    assert projection.certificationReady is True


def test_stale_certificates_and_invalid_profile_remain_non_green() -> None:
    scenario = _readiness_scenario()
    source = _complete_readiness_input(scenario)
    invalidated = tuple(
        gate.model_copy(
            update={
                "state": "invalidated",
                "resultManifest": None,
                "certificateState": "stale" if gate.gate == 2 else "invalidated",
            }
        )
        for gate in source.gates[1:]
    )
    stale_source = source.model_copy(
        update={
            "lifecycle": LifecycleReadinessObservation(revision=_REVISION, state="admitted"),
            "gates": (source.gates[0], *invalidated),
            "finalizationAuthority": None,
            "finalizationInputs": None,
        }
    )
    stale = compile_closeout_readiness(stale_source)
    assert stale.certificationReady is False
    assert stale.gates[1].state == "invalidated"
    assert stale.gates[1].certificateState == "stale"

    empty_gates = tuple(
        GateReadinessObservation(revision=_REVISION, gate=gate, state="not-started")
        for gate in (1, 2, 3, 4, 5)
    )
    invalid = compile_closeout_readiness(
        stale_source.model_copy(
            update={
                "profile": ProfileReadinessObservation(revision=_REVISION, state="invalid"),
                "gates": empty_gates,
            }
        )
    )
    assert invalid.profile.state == "invalid"
    assert invalid.certificationReady is False


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
