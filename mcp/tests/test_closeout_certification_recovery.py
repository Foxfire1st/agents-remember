"""Owner-derived R05 recovery decisions through real plan/result/certificate compilers."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from unittest import mock

import pytest
from agents_remember.certification.certificate_authority import compile_gate_certificate
from agents_remember.certification.certificate_invalidation import (
    plan_certificate_reuse,
)
from agents_remember.certification.certificate_models import (
    GateCertificate,
    GateCertificateIssuanceContext,
)
from agents_remember.certification.digests import content_digest
from agents_remember.certification.models import (
    GateResultAdmission,
    GateResultManifest,
    RailResult,
)
from agents_remember.certification.results import compile_gate_result_manifest
from agents_remember.worktrees.modules.quality import certification_records
from agents_remember.worktrees.modules.quality.execution import retained_reports
from agents_remember.worktrees.modules.quality.execution.models import RetainedGateExecution
from agents_remember.worktrees.modules.quality.published_manifest import (
    parse_published_quality_manifest,
    published_manifest_payload,
    quality_generation_digest,
    quality_report_dependencies,
)
from test_code_certification_execution import selected_execution


def _inventory_snapshot(publication, files):
    """Build digest-valid untrusted metadata using the canonical manifest owner."""
    payload = published_manifest_payload(publication)
    payload["files"] = files
    identity = {
        key: payload[key]
        for key in (
            "profileDigest",
            "profilePlanDigest",
            "profileSelectionId",
            "executorAdapterId",
            "resultDecoder",
        )
    }
    dependencies = quality_report_dependencies(
        publication.candidate_tree,
        files,
        publication.attestation,
        identity,
        publication.runtime_authority_digest,
    ).model_dump(mode="json")
    payload["dependencies"] = dependencies
    payload["generation"] = quality_generation_digest(
        {
            "candidateTree": publication.candidate_tree,
            **identity,
            "files": files,
            "dependencies": dependencies,
            "runtimeAuthorityDigest": publication.runtime_authority_digest,
        }
    )
    return parse_published_quality_manifest(payload)


def _retained_inventory_case(tmp_path: Path, fault: str):
    selected, request, _profile = selected_execution(tmp_path)
    run = selected.run
    store = certification_records.certificate_store(request.worktree_group)
    results = [
        store.load(GateResultManifest, item.semanticEnvelope.resultManifestDigest)
        for item in selected.certificates
    ]
    publications = [item.publication for item in selected.retained]
    changes: dict[int, tuple[str, str, str, int]] = {}
    if fault in {"duplicate", "conflict"}:
        path = "clean-quality-results.json"
        original = publications[0].require_file(path)
        changes[0] = ("evidence", path, original.sha256, original.size)
        changes[1] = (
            "evidence",
            path,
            "f" * 64 if fault == "conflict" else original.sha256,
            original.size,
        )
    elif fault in {"undeclared", "wrong-producer"}:
        path = "undeclared-producer.log" if fault == "undeclared" else "coverage.json"
        original = results[0].railResults[0].evidence[0]
        changes[0] = ("evidence", path, original.sha256, original.size)
    elif fault == "size":
        path = "coverage.json"
        declared = next(
            item for item in run.repositoryProfile.profile.publishedArtifacts if item.path == path
        )
        original = publications[1].require_file(path)
        changes[1] = ("artifacts", path, original.sha256, declared.maxBytes + 1)
    elif fault == "inventory":
        path = results[0].railResults[0].evidence[0].reference
        files = published_manifest_payload(publications[0])["files"]
        assert isinstance(files, dict)
        files[path] = {**files[path], "sha256": "f" * 64}
        publications[0] = _inventory_snapshot(publications[0], files)
    else:
        raise AssertionError(f"unknown inventory fault: {fault}")
    for gate_index, (field, path, digest, size) in changes.items():
        rails = list(results[gate_index].railResults)
        rail_index = next(
            index
            for index, rail in enumerate(rails)
            if field == "evidence" or any(item.evidenceRef == path for item in rail.artifacts)
        )
        payload = rails[rail_index].model_dump(mode="json")
        member = (
            payload[field][0]
            if field == "evidence"
            else next(item for item in payload[field] if item["evidenceRef"] == path)
        )
        member.update(
            {
                "reference" if field == "evidence" else "evidenceRef": path,
                "sha256": digest,
                "size": size,
            }
        )
        payload["resultDigest"] = content_digest(
            {key: value for key, value in payload.items() if key != "resultDigest"}
        )
        rails[rail_index] = RailResult.model_validate(payload)
        results[gate_index] = compile_gate_result_manifest(
            run.registry,
            run.certificationPlan,
            run.certificationPlan.gates[gate_index],
            rails,
            GateResultAdmission(
                profileId=run.certificationPlan.profileId,
                candidateIdentity=run.certificationPlan.candidateIdentity,
                altitude=run.certificationPlan.profileKind,
            ),
        )
        files = published_manifest_payload(publications[gate_index])["files"]
        assert isinstance(files, dict)
        files[path] = {"sha256": digest, "size": size}
        publications[gate_index] = _inventory_snapshot(publications[gate_index], files)
    certificates: list[GateCertificate] = []
    for gate_plan, result in zip(run.certificationPlan.gates, results, strict=False):
        certificate = compile_gate_certificate(
            run.admission,
            gate_plan,
            result,
            certificates,
            GateCertificateIssuanceContext(provenance=run.provenance),
        )
        store.publish(result)
        store.publish(certificate)
        certificates.append(certificate)
    repaired = replace(
        selected,
        certificates=tuple(certificates),
        reuse_plan=plan_certificate_reuse(run.admission, certificates, selected.input_changes),
        retained=tuple(
            RetainedGateExecution(certificates[index], results[index], publication)
            for index, publication in enumerate(publications)
        ),
    )
    # Every deliberate input reaches the actual transport guard as a schema-valid,
    # compiler-issued prefix; no selection validator or filesystem resolver is replaced.
    repaired.validate()
    return repaired, request


@pytest.mark.parametrize(
    "fault,detail",
    [
        ("undeclared", "exact frozen producer publication"),
        ("wrong-producer", "exact frozen producer publication"),
        ("size", "frozen publication byte bound"),
        ("inventory", "original publication inventory"),
        ("conflict", "conflicting producer paths"),
    ],
)
def test_retained_report_inventory_refuses_mismatched_evidence_before_any_source_read(
    tmp_path: Path, fault: str, detail: str
) -> None:
    selected, request = _retained_inventory_case(tmp_path, fault)
    destination = tmp_path / "rejected-retained"
    with (
        mock.patch.object(
            retained_reports,
            "published_report_path_from_manifest",
            wraps=retained_reports.published_report_path_from_manifest,
        ) as open_report,
        pytest.raises(ValueError, match=detail),
    ):
        retained_reports.snapshot_retained_reports(
            selected,
            reports=request.worktree_group / "reports",
            destination=destination,
        )
    open_report.assert_not_called()
    assert not destination.exists()
