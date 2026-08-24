"""Capability-minimal composition for an eligible direct diagnostic process."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from agents_remember.testing.eligibility import direct_selection_is_current
from agents_remember.testing.hermetic_bootstrap import (
    CandidateTestProcess,
    candidate_test_process,
    hermetic_pytest_environment,
)
from agents_remember.testing.selection_contract import EligibleDirectSelection


class DiagnosticBootstrapError(RuntimeError):
    """An eligible selection became invalid before diagnostic startup."""


@dataclass(frozen=True)
class DiagnosticPytestBootstrap:
    """A candidate-bound diagnostic setup with no admission or publisher capability."""

    process: CandidateTestProcess
    selection: EligibleDirectSelection


DAGGER_ATTESTATION_ENV = "AR_DAGGER_TEST_ATTESTATION"


def prepare_diagnostic_pytest_bootstrap(
    selection: EligibleDirectSelection,
) -> DiagnosticPytestBootstrap:
    """Bind a still-current eligible decision without consulting Dagger admission."""

    if not isinstance(selection, EligibleDirectSelection):
        raise DiagnosticBootstrapError("diagnostic bootstrap requires an eligible selection")
    if not direct_selection_is_current(selection):
        raise DiagnosticBootstrapError(
            "candidate changed after classification; recompute direct eligibility"
        )
    return DiagnosticPytestBootstrap(
        candidate_test_process(selection.candidate_root),
        selection,
    )


def diagnostic_pytest_environment(
    bootstrap: DiagnosticPytestBootstrap,
    environ: Mapping[str, str],
    *,
    cache_root: Path,
) -> dict[str, str]:
    """Build a hermetic child environment without copying the Dagger secret."""

    result = hermetic_pytest_environment(
        bootstrap.process,
        environ,
        cache_root=cache_root,
    )
    result.pop(DAGGER_ATTESTATION_ENV, None)
    return result
