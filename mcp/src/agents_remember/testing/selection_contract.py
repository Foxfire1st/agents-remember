"""Typed contract returned by the one direct-test eligibility owner."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypeAlias

from agents_remember.models.test_evidence import CandidateBinding


class UnsafeEffectFamily(StrEnum):
    """Closed effect families that Candidate A keeps in Dagger."""

    GIT_WORKTREE = "git-worktree"
    PROCESS_CONTROL = "process-control"
    SOCKET_SERVICE = "socket-service"
    PROVIDER_CONTAINER = "provider-container"
    BROWSER_EXTERNAL = "browser-external"
    MACHINE_STATE = "machine-state"
    MUTABLE_GLOBAL_STATE = "mutable-global-state"
    DURABILITY_INTEGRATION = "durability-integration"


class DirectRefusalCode(StrEnum):
    """Stable, actionable refusal meanings for a direct selection."""

    INVALID_CANDIDATE = "invalid-candidate"
    EMPTY_SELECTION = "empty-selection"
    OVERSIZED_SELECTION = "oversized-selection"
    DUPLICATE_TARGET = "duplicate-target"
    UNSUPPORTED_TARGET = "unsupported-target"
    TARGET_OUTSIDE_TEST_ROOT = "target-outside-test-root"
    TARGET_MISSING = "target-missing"
    TARGET_AMBIGUOUS = "target-ambiguous"
    PARAMETRIZED_TARGET = "parametrized-target"
    NOT_IN_COHORT = "not-in-cohort"
    UNRESOLVED_DEPENDENCY = "unresolved-dependency"
    DYNAMIC_DEPENDENCY = "dynamic-dependency"
    UNSUPPORTED_COLLECTION = "unsupported-collection"
    UNSUPPORTED_FIXTURE = "unsupported-fixture"
    UNSAFE_EFFECT = "unsafe-effect"
    MIXED_SELECTION = "mixed-selection"
    CANDIDATE_CHANGED = "candidate-changed"


@dataclass(frozen=True)
class DependencyObservation:
    """One source-backed reason in a dependency/effect closure."""

    path: str
    line: int
    symbol: str
    detail: str
    family: UnsafeEffectFamily | None = None


@dataclass(frozen=True)
class ResolvedDependencyClosure:
    """Exact files and observations traversed for one admitted selection."""

    paths: tuple[str, ...]
    observations: tuple[DependencyObservation, ...]


@dataclass(frozen=True)
class EligibleDirectSelection:
    """The complete exact node set proved eligible as one immutable question."""

    candidate_root: Path
    nodes: tuple[str, ...]
    closure: ResolvedDependencyClosure
    binding: CandidateBinding


@dataclass(frozen=True)
class RefusedDirectSelection:
    """A controlled whole-request refusal; no selected test may run."""

    code: DirectRefusalCode
    message: str
    target: str | None = None
    dependency: DependencyObservation | None = None
    refused_nodes: tuple[str, ...] = ()


DirectSelectionDecision: TypeAlias = EligibleDirectSelection | RefusedDirectSelection
