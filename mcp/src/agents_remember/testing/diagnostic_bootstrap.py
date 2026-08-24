"""Capability-minimal composition for an eligible direct diagnostic process."""

from __future__ import annotations

from dataclasses import dataclass

from agents_remember.testing.eligibility import direct_selection_is_current
from agents_remember.testing.hermetic_bootstrap import (
    CandidateTestProcess,
    candidate_test_process,
)
from agents_remember.testing.selection_contract import EligibleDirectSelection


class DiagnosticBootstrapError(RuntimeError):
    """An eligible selection became invalid before diagnostic startup."""


@dataclass(frozen=True)
class DiagnosticPytestBootstrap:
    """A candidate-bound diagnostic setup with no admission or publisher capability."""

    process: CandidateTestProcess
    selection: EligibleDirectSelection


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
