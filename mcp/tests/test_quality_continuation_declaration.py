"""Continuation admission preserves the host-declaration schema and digest."""

from __future__ import annotations

from pathlib import Path

from agents_remember.certification import canonicalize_registry
from agents_remember.certification.diagnostics.store import (
    DiagnosticManifestStore,
    DiagnosticStorePolicy,
)
from agents_remember.certification.final_codex.store import (
    FinalCodexManifestStore,
    FinalCodexStorePolicy,
)
from agents_remember.certification.models import (
    ArtifactDeclaration,
    CanonicalRailRegistry,
    CertificationPlan,
    GateResultManifest,
    RailAdapterDefinition,
    RailApplicability,
    RailDefinition,
    RailEvidenceContract,
    RailRegistry,
    RailRuntimeInputs,
    RegistryProfile,
)
from agents_remember.certification.planning import compile_certification_plan
from agents_remember.worktrees.modules.quality.dagger_authority import (
    AUTHORITY_SCHEMA_VERSION,
    AdmittedDaggerAuthority,
    AuthorityRegistry,
    admit_dagger_authority,
    parse_host_declaration,
    release_dagger_authority,
)
from agents_remember.worktrees.modules.quality.diagnostic_executor import (
    DiagnosticEngineOptions,
    DiagnosticExecutionEngine,
    build_diagnostic_run_spec,
)
from agents_remember.worktrees.modules.quality.final_codex_executor import (
    FinalCodexEngineOptions,
    FinalCodexExecutionEngine,
    build_final_codex_run_spec,
)
from test_final_codex_models import (
    CANDIDATE,
    CERT_PROFILE,
    CLASS_BY_GATE,
    DECLARATION_V1,
    DIGEST,
    NOW,
    PREREQUISITES,
    FakeInspector,
    RailSpec,
    engine_environ,
    identity,
    manifest_for,
)

DIAG_PROFILE = "portable-diagnostic"


def _rail_with_diagnostic_profile(spec: RailSpec) -> RailDefinition:
    applicable = spec.gate != 5
    required = ("suite-data",) if spec.gate == 3 else ()
    outputs = (
        (
            ArtifactDeclaration(
                artifactId="suite-data",
                schemaVersion="suite/v1",
                mediaType="application/json",
            ),
        )
        if spec.gate == 2
        else ()
    )
    applicability = [
        RailApplicability(
            profileId=CERT_PROFILE,
            status="applicable" if applicable else "not-applicable",
            selectionIdentity=f"selection:{spec.rail_id}",
            population="exact population" if applicable else None,
            reason=None if applicable else "profile excludes gate-5 memory",
        )
    ]
    if spec.gate <= 4:
        applicability.append(
            RailApplicability(
                profileId=DIAG_PROFILE,
                status="applicable",
                selectionIdentity=f"selection:{spec.rail_id}",
                population="exact population",
                reason=None,
            )
        )
    return RailDefinition(
        identity=identity(spec.rail_id),
        gate=spec.gate,
        railClass=CLASS_BY_GATE[spec.gate],
        authority="memory-domain" if spec.gate == 5 else "repository-profile",
        ownerClass="portable-owner",
        correctiveOwner="portable-owner",
        posture=spec.posture,
        orderKey=spec.rail_id,
        prerequisites=tuple(
            identity(item) for item in (PREREQUISITES.get(spec.rail_id, ""),) if item
        ),
        requiredArtifacts=required,
        adapter=RailAdapterDefinition(
            adapterKind="process-adapter",
            adapterId=f"{spec.rail_id}-adapter",
            configurationDigest=DIGEST,
            executionEvidence=f"adapter://{spec.rail_id}",
        ),
        runtimeInputs=RailRuntimeInputs(runtimeIdentity="portable-runtime"),
        applicability=tuple(applicability),
        evidenceContract=(
            RailEvidenceContract(
                evidenceId=f"{spec.rail_id}-evidence",
                mediaType="application/json",
                maxBytes=256,
            ),
        ),
        outputArtifacts=outputs,
    )


def _dual_profile_registry() -> CanonicalRailRegistry:
    return canonicalize_registry(
        RailRegistry(
            registryId="portable-continuation-registry",
            repositoryId="sample-repository",
            profiles=(
                RegistryProfile(
                    profileId=CERT_PROFILE,
                    kind="certifying",
                    gates=(1, 2, 3, 4, 5),
                ),
                RegistryProfile(
                    profileId=DIAG_PROFILE,
                    kind="diagnostic",
                    gates=(1, 2, 3, 4),
                ),
            ),
            rails=tuple(
                _rail_with_diagnostic_profile(spec)
                for spec in (
                    RailSpec("advisory", 1, posture="report-only"),
                    RailSpec("lint", 1),
                    RailSpec("suite", 2),
                    RailSpec("coverage", 3),
                    RailSpec("e2e", 4),
                    RailSpec("memory", 5),
                )
            ),
        )
    )


def _green_prerequisites(
    registry: CanonicalRailRegistry,
    cert_plan: CertificationPlan,
) -> tuple[GateResultManifest, ...]:
    return tuple(manifest_for(registry, cert_plan, gate) for gate in (1, 2, 3))


def _initial_authority(registry: AuthorityRegistry) -> AdmittedDaggerAuthority:
    return admit_dagger_authority(
        environ=engine_environ(DECLARATION_V1),
        inspector=FakeInspector(),
        registry=registry,
    )


def test_diagnostic_continuation_preserves_host_declaration_schema_and_digest(
    tmp_path: Path,
) -> None:
    rail_registry = _dual_profile_registry()
    cert_plan = compile_certification_plan(
        rail_registry,
        profile_id=CERT_PROFILE,
        candidate_identity=CANDIDATE,
    )
    spec = build_diagnostic_run_spec(
        rail_registry,
        profile_id=DIAG_PROFILE,
        certifying_plan=cert_plan,
        candidate_identity=CANDIDATE,
    )
    authority_registry = AuthorityRegistry(tmp_path / "authority")
    initial = _initial_authority(authority_registry)
    store = DiagnosticManifestStore(
        tmp_path / "diagnostics",
        DiagnosticStorePolicy(storeId="diagnostic"),
    )
    engine = DiagnosticExecutionEngine(
        store=store,
        registry=authority_registry,
        options=DiagnosticEngineOptions(
            environ=engine_environ(DECLARATION_V1),
            inspector=FakeInspector(),
            clock=lambda: NOW,
            nonce_source=lambda: "diagnostic-continuation-nonce",
        ),
    )

    attempt = engine.admit(
        spec,
        _green_prerequisites(rail_registry, cert_plan),
        continuation_snapshot=initial.snapshot,
    )
    try:
        declaration = attempt.admitted.declaration
        assert declaration.schema_version == AUTHORITY_SCHEMA_VERSION
        assert declaration.as_manifest()["schemaVersion"] == AUTHORITY_SCHEMA_VERSION
        assert declaration.as_manifest() == initial.declaration.as_manifest()
        assert declaration.declaration_digest == initial.declaration.declaration_digest
        assert declaration.source == f"snapshot:{initial.snapshot.snapshot_digest}"
        reparsed = parse_host_declaration(declaration.as_manifest(), source="continuation-check")
        assert reparsed.declaration_digest == initial.declaration.declaration_digest
    finally:
        release_dagger_authority(attempt.admitted, registry=authority_registry)


def test_final_codex_continuation_preserves_host_declaration_schema_and_digest(
    tmp_path: Path,
) -> None:
    rail_registry = _dual_profile_registry()
    cert_plan = compile_certification_plan(
        rail_registry,
        profile_id=CERT_PROFILE,
        candidate_identity=CANDIDATE,
    )
    spec = build_final_codex_run_spec(
        rail_registry,
        certifying_plan=cert_plan,
        candidate_identity=CANDIDATE,
    )
    authority_registry = AuthorityRegistry(tmp_path / "authority")
    initial = _initial_authority(authority_registry)
    store = FinalCodexManifestStore(
        tmp_path / "final-codex",
        FinalCodexStorePolicy(storeId="final-codex"),
    )
    engine = FinalCodexExecutionEngine(
        store=store,
        registry=authority_registry,
        options=FinalCodexEngineOptions(
            environ=engine_environ(DECLARATION_V1),
            inspector=FakeInspector(),
            clock=lambda: NOW,
            nonce_source=lambda: "final-codex-continuation-nonce",
        ),
    )

    attempt = engine.admit(
        spec,
        _green_prerequisites(rail_registry, cert_plan),
        continuation_snapshot=initial.snapshot,
    )
    try:
        declaration = attempt.admitted.declaration
        assert declaration.schema_version == AUTHORITY_SCHEMA_VERSION
        assert declaration.as_manifest()["schemaVersion"] == AUTHORITY_SCHEMA_VERSION
        assert declaration.as_manifest() == initial.declaration.as_manifest()
        assert declaration.declaration_digest == initial.declaration.declaration_digest
        assert declaration.source == f"snapshot:{initial.snapshot.snapshot_digest}"
        reparsed = parse_host_declaration(declaration.as_manifest(), source="continuation-check")
        assert reparsed.declaration_digest == initial.declaration.declaration_digest
    finally:
        release_dagger_authority(attempt.admitted, registry=authority_registry)
