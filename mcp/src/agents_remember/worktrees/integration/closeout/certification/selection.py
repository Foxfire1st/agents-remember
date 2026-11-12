"""Publish/read back certification objects, then select them through the operation CAS."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents_remember.certification.certificate_models import (
    GateCertificate,
)
from agents_remember.certification.certificate_store import ContentAddressedCertificateStore
from agents_remember.certification.frozen_run.models import FrozenCertificationRun
from agents_remember.certification.models import GateResultAdmission, GateResultManifest
from agents_remember.certification.results import compile_gate_result_manifest
from agents_remember.models.certification.references import CertificateObjectReference
from agents_remember.models.lifecycles.certification import (
    RetainedCertificationBytes,
    SelectedGateTerminal,
)
from agents_remember.worktrees.modules.quality.certification_evidence import (
    verify_publication_authority,
    verify_result_evidence,
    verify_terminal_publication_authority,
)
from agents_remember.worktrees.modules.quality.certification_terminal import (
    RecordedGateTerminal,
)
from agents_remember.worktrees.modules.quality.published_manifest import (
    parse_published_quality_manifest,
    published_manifest_payload,
)

from .observation import refuse


def load_typed[Object](
    store: ContentAddressedCertificateStore,
    reference: CertificateObjectReference,
    model: type[Object],
) -> Object:
    value = store.load_reference(reference)
    if not isinstance(value, model):
        refuse("selected-certification-object-kind", model.__name__, reference.kind)
    return value


def load_selected_terminal(
    reports: Path,
    store: ContentAddressedCertificateStore,
    current_run: FrozenCertificationRun,
    selected: SelectedGateTerminal,
) -> RecordedGateTerminal:
    result = load_typed(store, selected.result, GateResultManifest)
    certificate = (
        load_typed(store, selected.certificate, GateCertificate)
        if selected.certificate is not None
        else None
    )
    publication = parse_published_quality_manifest(json.loads(selected.publication.canonicalBytes))
    run = (
        load_typed(store, selected.reusedFrom.frozenRun, FrozenCertificationRun)
        if selected.reusedFrom is not None
        else current_run
    )
    verify_terminal_publication_authority(run, result, publication)
    verify_result_evidence(reports, publication, result.railResults)
    gate_plan = run.certificationPlan.gates[selected.gate - 1]
    rebuilt = compile_gate_result_manifest(
        run.registry,
        run.certificationPlan,
        gate_plan,
        result.railResults,
        GateResultAdmission(
            candidateIdentity=run.repositoryPlan.candidateIdentity,
            profileId=run.repositoryPlan.selectionId,
            altitude=result.altitude,
        ),
    )
    if rebuilt != result:
        refuse("selected-gate-result-mismatch", rebuilt, result)
    if certificate is not None:
        verify_publication_authority(certificate, result, publication)
    # Recompilation binds the selected gate; publication validation binds the certificate
    # gate and result digest. A selected certificate must still name a green result.
    if certificate is not None and result.disposition != "green":
        refuse("selected-gate-terminal-mismatch", selected.gate, result.gate)
    return RecordedGateTerminal(
        result, selected.result, publication, certificate, selected.certificate
    )


def terminal_selection(terminal: RecordedGateTerminal) -> SelectedGateTerminal:
    encoded = json.dumps(
        published_manifest_payload(terminal.publication),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return SelectedGateTerminal(
        gate=terminal.result.gate,
        result=terminal.resultReference,
        certificate=terminal.certificateReference,
        publication=RetainedCertificationBytes(
            canonicalBytes=encoded,
            contentSha256=hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        ),
    )
