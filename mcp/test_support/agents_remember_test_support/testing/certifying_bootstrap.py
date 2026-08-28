"""Composition root for certifying pytest startup."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember_test_support.testing.dagger_admission import (
    DaggerAdmission,
    require_dagger_admission,
)
from agents_remember_test_support.testing.hermetic_bootstrap import (
    CandidateTestProcess,
    candidate_test_process,
)


@dataclass(frozen=True)
class CertifyingPytestBootstrap:
    """Candidate setup paired with the capability required for certification."""

    process: CandidateTestProcess
    admission: DaggerAdmission


def prepare_certifying_pytest_bootstrap(
    candidate_root: Path,
    *,
    environ: Mapping[str, str] | None = None,
    attestation_path: Path | None = None,
) -> CertifyingPytestBootstrap:
    """Admit first, then resolve any collection/bootstrap state."""

    admission = require_dagger_admission(
        environ=environ,
        attestation_path=attestation_path,
    )
    return CertifyingPytestBootstrap(candidate_test_process(candidate_root), admission)
