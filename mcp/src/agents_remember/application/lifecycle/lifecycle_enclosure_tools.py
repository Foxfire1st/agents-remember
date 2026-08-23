"""Explicit public migration into canonical lifecycle-enclosure addressability."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agents_remember.kernel.authority import require_within_coordination
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.worktrees.integration.lifecycle.lifecycle_enclosure_adoption import (
    LifecycleEnclosureAdoptionPreview,
    apply_lifecycle_enclosure_adoption,
    preview_lifecycle_enclosure_adoption,
)
from agents_remember.worktrees.integration.lifecycle.lifecycle_operation_location import (
    LifecycleOperationLocationError,
)

from .lifecycle_operation_location import location_decision_payload


@dataclass(frozen=True)
class EnclosureAdoptionRequest:
    """One exact dry-run or approved enclosure-location adoption request."""

    contract_path: str
    expected_worktree_group: str
    rationale: str
    dry_run: bool = True
    approved: bool = False
    expected_publication_request_id: str | None = None


def worktree_enclosure_adopt_tool(
    config: McpRuntimeConfig,
    request: EnclosureAdoptionRequest,
) -> dict[str, Any]:
    """Explicitly adopt one readable pre-locator enclosure; never a reader fallback."""

    confined_contract = require_within_coordination(
        config,
        request.contract_path,
        "contract_path",
    )
    confined_group = require_within_coordination(
        config,
        request.expected_worktree_group,
        "expected_worktree_group",
    )
    preview: LifecycleEnclosureAdoptionPreview | None = None
    try:
        preview = preview_lifecycle_enclosure_adoption(
            confined_contract,
            expected_worktree_group=confined_group,
            rationale=request.rationale,
        )
        if request.dry_run:
            return {
                "ok": True,
                "operation": "worktree_enclosure_adopt",
                "dryRun": True,
                **preview.payload(),
                "nextAction": "adopt",
                "nextTool": "worktree_enclosure_adopt",
                "nextArgs": _apply_args(preview, request),
            }
        expected_request = (request.expected_publication_request_id or "").strip()
        if not request.approved or not expected_request:
            return _approval_required(preview, request)
        if expected_request != preview.receipt.publicationRequestId:
            return _changed_preview(preview, expected_request)
        location, receipt = apply_lifecycle_enclosure_adoption(preview)
    except LifecycleOperationLocationError as error:
        if (
            error.status
            in {
                "operation-location-adoption-interrupted",
                "operation-location-publication-interrupted",
            }
            and preview is not None
        ):
            return _interrupted_adoption(error, preview, request)
        return {
            "ok": False,
            "operation": "worktree_enclosure_adopt",
            **location_decision_payload(error),
        }
    return {
        "ok": True,
        "operation": "worktree_enclosure_adopt",
        "state": "enclosure-adopted",
        "status": "enclosure-adopted",
        "contractPath": location.contract_path.as_posix(),
        "worktreeGroup": location.worktree_group.as_posix(),
        "locatorPath": location.locator_path.as_posix(),
        "manifestPath": location.manifest_path.as_posix(),
        "publicationRequestId": receipt.publicationRequestId,
        "contractSha256": receipt.contractSha256,
        "manifestSha256": receipt.manifestSha256,
        "artifacts": [item.model_dump(mode="json") for item in receipt.artifacts],
        "removalCondition": _REMOVAL_CONDITION,
    }


_REMOVAL_CONDITION = (
    "remove worktree_enclosure_adopt only after the explicit inventory contains no "
    "readable enclosure without an addressable locator and root manifest"
)


def _approval_required(
    preview: LifecycleEnclosureAdoptionPreview,
    request: EnclosureAdoptionRequest,
) -> dict[str, object]:
    return {
        "ok": False,
        "operation": "worktree_enclosure_adopt",
        "state": "enclosure-adoption-approval-required",
        "status": "enclosure-adoption-approval-required",
        "detail": (
            "apply requires approved=true and the exact publicationRequestId returned by "
            "dry_run=true"
        ),
        "nextAction": "adopt",
        "nextTool": "worktree_enclosure_adopt",
        "nextArgs": _apply_args(preview, request),
    }


def _changed_preview(
    preview: LifecycleEnclosureAdoptionPreview,
    expected_request: str,
) -> dict[str, object]:
    return {
        "ok": False,
        "operation": "worktree_enclosure_adopt",
        "state": "enclosure-adoption-preview-changed",
        "status": "enclosure-adoption-preview-changed",
        "detail": "the approved enclosure-adoption preview no longer matches live bytes",
        "expected": {"publicationRequestId": expected_request},
        "observed": {
            "publicationRequestId": preview.receipt.publicationRequestId,
            "contractSha256": preview.receipt.contractSha256,
            "manifestSha256": preview.receipt.manifestSha256,
        },
        "nextAction": "developer-decision",
        "developerDecisionRequired": True,
        "decisionSurface": (
            "preview the changed explicit enclosure and approve its new request identity"
        ),
    }


def _interrupted_adoption(
    error: LifecycleOperationLocationError,
    preview: LifecycleEnclosureAdoptionPreview,
    request: EnclosureAdoptionRequest,
) -> dict[str, object]:
    return {
        "ok": False,
        "operation": "worktree_enclosure_adopt",
        "state": error.status,
        "status": error.status,
        "detail": error.detail,
        "expected": error.expected,
        "observed": error.observed,
        "nextAction": "adopt",
        "nextTool": "worktree_enclosure_adopt",
        "nextArgs": _apply_args(preview, request),
    }


def _apply_args(
    preview: LifecycleEnclosureAdoptionPreview,
    request: EnclosureAdoptionRequest,
) -> dict[str, object]:
    return {
        "contract_path": preview.artifacts.contract_path.as_posix(),
        "expected_worktree_group": preview.receipt.worktreeGroup,
        "rationale": request.rationale,
        "dry_run": False,
        "approved": True,
        "expected_publication_request_id": preview.receipt.publicationRequestId,
    }
