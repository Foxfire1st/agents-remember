"""Immutable candidate identity for task-bound lifecycle operations."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from agents_remember.models.lifecycles.operation import (
    IntegrationOperationAuthority,
    LifecycleOperationInput,
)
from agents_remember.worktrees.closeout_input import CloseoutCandidateSnapshot


@dataclass(frozen=True)
class LifecycleOperationCandidate:
    state: str
    tree: str | None
    fingerprint: str


def fingerprint_payload(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def lifecycle_operation_candidate(
    operation_input: LifecycleOperationInput,
    *,
    candidate_state: str,
    candidate_tree: str | None = None,
    closeout_candidate: CloseoutCandidateSnapshot | None = None,
    integration_authority: IntegrationOperationAuthority | None,
) -> LifecycleOperationCandidate:
    """Bind durable input and every route-specific candidate fact to one fingerprint."""
    resolved_tree = (
        closeout_candidate.candidate_tree if closeout_candidate is not None else candidate_tree
    )
    payload = {
        "input": operation_input.model_dump(mode="json"),
        "candidateState": candidate_state,
        "candidateTree": resolved_tree,
        "integrationAuthority": (
            integration_authority.model_dump(mode="json")
            if integration_authority is not None
            else None
        ),
    }
    if closeout_candidate is not None:
        payload.update(
            {
                "candidateHead": closeout_candidate.head_commit,
                "candidateHeadTree": closeout_candidate.head_tree,
            }
        )
    return LifecycleOperationCandidate(
        candidate_state,
        resolved_tree,
        fingerprint_payload(payload),
    )
