"""Deterministic complete final catalog plan and executed attestation (CCR-R08).

The complete Gate-5 catalog is the exhaustion surface: every applicable memory check
returns pass, fail, blocked, or not-applicable, and the final attestation proves it ran
exactly the planned population -- never a weakened incremental-only acceptance. The
catalog spans the existing memory checkers (drift + style), the missing-onboarding and
route-index alignment owners, the R07 affected-closure plan, the canonical
curator-coherence record, and the exact code/memory candidate pair.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn

from agents_remember.certification.digests import content_digest
from agents_remember.certification.final_certification_models import (
    FinalCatalogItemIdentity,
    FinalCatalogItemResult,
    FinalItemStatus,
)
from agents_remember.errors import FinalCertificationError
from agents_remember.memory_quality.check import AVAILABLE_CHECKS, DRIFT_CHECK_NAME
from agents_remember.memory_quality.converted_check import CONVERTED_KNOWLEDGE_CHECK
from agents_remember.memory_quality.incremental_scope.registry import (
    checker_registry_version,
    checker_scope_registry,
)
from agents_remember.memory_quality.style.document_shape import (
    diff_markers,
    entity_catalog_alignment,
    tables,
)

FINAL_FULL_CATALOG_VERSION = "1.0.0"

MISSING_ONBOARDING_ITEM_ID = "integrity.missing_onboarding"
ROUTE_INDEX_ALIGNMENT_ITEM_ID = "route_index.alignment"
AFFECTED_CLOSURE_ITEM_ID = "affected.closure"
COHERENCE_RECORD_ITEM_ID = "coherence.record"
CANDIDATE_PAIR_ITEM_ID = "candidate.code_memory_pair"

_STANDARD_CHECK_IDS: tuple[str, ...] = (
    DRIFT_CHECK_NAME,
    diff_markers.CHECK_NAME,
    entity_catalog_alignment.CHECK_NAME,
    tables.CHECK_NAME,
)
_STANDARD_CHECK_ID_SET = frozenset(_STANDARD_CHECK_IDS)


@dataclass(frozen=True)
class ReadinessProjectionInput:
    """The repair-loop view of one full run; never certification-eligible."""

    executed_checks: Mapping[str, Mapping[str, Any]]
    missing_onboarding_count: int = 0
    stale_route_index_count: int = 0
    coherence_status: str | None = None
    coherence_record_digest: str | None = None
    candidate_pair_authority_digest: str = "0" * 64
    affected_closure_plan_digest: str | None = None
    memory_tree: str | None = None


def complete_final_catalog() -> tuple[FinalCatalogItemIdentity, ...]:
    """Return the closed deterministic Gate-5 catalog population (canonical order)."""

    declared = {policy.checker for policy in checker_scope_registry()}
    if declared != set(AVAILABLE_CHECKS):
        raise FinalCertificationError(
            "gate-five-catalog-incomplete",
            "the final full catalog must cover the complete current memory checker registry",
            expected={"checkerRegistry": sorted(AVAILABLE_CHECKS)},
            observed={"checkerRegistry": sorted(declared)},
        )
    items = [
        *(
            FinalCatalogItemIdentity(itemId=check_id, version=FINAL_FULL_CATALOG_VERSION)
            for check_id in _STANDARD_CHECK_IDS
        ),
        FinalCatalogItemIdentity(
            itemId=MISSING_ONBOARDING_ITEM_ID, version=FINAL_FULL_CATALOG_VERSION
        ),
        FinalCatalogItemIdentity(
            itemId=ROUTE_INDEX_ALIGNMENT_ITEM_ID, version=FINAL_FULL_CATALOG_VERSION
        ),
        FinalCatalogItemIdentity(
            itemId=AFFECTED_CLOSURE_ITEM_ID, version=FINAL_FULL_CATALOG_VERSION
        ),
        FinalCatalogItemIdentity(
            itemId=COHERENCE_RECORD_ITEM_ID, version=FINAL_FULL_CATALOG_VERSION
        ),
        FinalCatalogItemIdentity(itemId=CANDIDATE_PAIR_ITEM_ID, version=FINAL_FULL_CATALOG_VERSION),
    ]
    return tuple(sorted(items, key=lambda item: item.key))


def final_catalog_readiness(inputs: ReadinessProjectionInput) -> dict[str, object]:
    """Deterministic Gate-5 surface projection for the repair-loop entry point.

    The interactive memory_quality_check full run is the execution surface; it cannot
    hold the R21 certificates or the R07 affected-closure plan, so it projects the exact
    complete catalog population and every item's status without claiming certification
    eligibility. blocked items name the exact missing authority. The projection is
    byte-deterministic so the certification executor can compare it with the attested
    final catalog.
    """

    catalog = complete_final_catalog()
    items: list[dict[str, object]] = []
    for item in catalog:
        item_id = item.itemId
        if item_id in _STANDARD_CHECK_ID_SET:
            executed = _executed_result(inputs.executed_checks, item_id)
            items.append(_project_item(_standard_result(item, executed or {})))
        elif item_id == MISSING_ONBOARDING_ITEM_ID:
            items.append(
                _project_item(
                    _count_result(item, inputs.missing_onboarding_count, "missing-onboarding")
                )
            )
        elif item_id == ROUTE_INDEX_ALIGNMENT_ITEM_ID:
            items.append(
                _project_item(
                    _count_result(item, inputs.stale_route_index_count, "stale-route-index")
                )
            )
        elif item_id == AFFECTED_CLOSURE_ITEM_ID:
            if inputs.affected_closure_plan_digest is None:
                items.append(
                    _project_item(
                        _authority_result(
                            item,
                            "blocked",
                            "0" * 64,
                            blocked_by=("affected-closure-plan-not-provided",),
                        )
                    )
                )
            else:
                items.append(
                    _project_item(
                        _authority_result(
                            item,
                            "pass",
                            inputs.affected_closure_plan_digest,
                            blocked_by=(),
                        )
                    )
                )
        elif item_id == COHERENCE_RECORD_ITEM_ID:
            current = (
                inputs.coherence_status == "current" and inputs.coherence_record_digest is not None
            )
            status: FinalItemStatus = "pass" if current else "blocked"
            items.append(
                _project_item(
                    _authority_result(
                        item,
                        status,
                        inputs.coherence_record_digest or "0" * 64,
                        blocked_by=(() if current else ("coherence-record-not-current",)),
                    )
                )
            )
        elif item_id == CANDIDATE_PAIR_ITEM_ID:
            items.append(
                _project_item(
                    _authority_result(
                        item,
                        "pass",
                        inputs.candidate_pair_authority_digest,
                        blocked_by=(),
                    )
                )
            )
        else:
            _refuse_unknown_item(item_id)
    return {
        "schemaVersion": "memory-final-full-catalog-readiness/v1",
        "catalog": [item.model_dump(mode="json") for item in catalog],
        "items": items,
        "checkerRegistryDigest": checker_registry_version(),
        "candidatePairAuthorityDigest": inputs.candidate_pair_authority_digest,
        "affectedClosurePlanDigest": inputs.affected_closure_plan_digest,
        "memoryTree": inputs.memory_tree,
        "coherenceRecordDigest": inputs.coherence_record_digest,
        "finalizationEligible": False,
        "fullFinalRequired": True,
    }


def _executed_result(
    executed_checks: Mapping[str, Mapping[str, Any]], item_id: str
) -> Mapping[str, Any] | None:
    """One standard check's executed result, or ``None`` when the run did not execute it.

    On a converted tree the drift slot runs the knowledge validator and stores its result as
    ``knowledge.converted`` (MIK-R24 rule 5); that result is the drift item's. Looked up under the
    drift check's own name it was absent, so the item read ``fail`` with no finding (L37 P1c, C2).
    """

    result = executed_checks.get(item_id)
    if result is None and item_id == DRIFT_CHECK_NAME:
        return executed_checks.get(CONVERTED_KNOWLEDGE_CHECK)
    return result


def _standard_result(
    item: FinalCatalogItemIdentity,
    result: Mapping[str, Any],
) -> FinalCatalogItemResult:
    finding_count = int(result.get("findingCount", 0) or 0)
    ok = bool(result.get("ok", False))
    status: FinalItemStatus = "pass" if ok else "fail"
    return FinalCatalogItemResult(
        item=item,
        status=status,
        findingCount=finding_count,
        subresultDigest=content_digest(dict(result)),
        reference=result.get("reportPath"),
    )


def _count_result(
    item: FinalCatalogItemIdentity,
    count: int,
    code: str,
) -> FinalCatalogItemResult:
    status: FinalItemStatus = "pass" if count == 0 else "fail"
    return FinalCatalogItemResult(
        item=item,
        status=status,
        findingCount=count,
        subresultDigest=content_digest({"count": count, "item": item.itemId, "code": code}),
    )


def _authority_result(
    item: FinalCatalogItemIdentity,
    status: FinalItemStatus,
    authority_digest: str,
    *,
    blocked_by: tuple[str, ...],
) -> FinalCatalogItemResult:
    if status == "blocked":
        return FinalCatalogItemResult(
            item=item,
            status="blocked",
            findingCount=0,
            subresultDigest=content_digest(
                {"item": item.itemId, "authorityDigest": authority_digest, "status": "blocked"}
            ),
            blockedBy=blocked_by,
        )
    return FinalCatalogItemResult(
        item=item,
        status=status,
        findingCount=0 if status == "pass" else 1,
        subresultDigest=content_digest(
            {"item": item.itemId, "authorityDigest": authority_digest, "status": status}
        ),
    )


def _project_item(result: FinalCatalogItemResult) -> dict[str, object]:
    return result.model_dump(mode="json")


def _refuse_unknown_item(item_id: str) -> NoReturn:
    raise FinalCertificationError(
        "gate-five-catalog-item-unknown",
        f"the final catalog does not own item {item_id}",
        next_action="memory_quality_check",
    )


__all__ = [
    "AFFECTED_CLOSURE_ITEM_ID",
    "CANDIDATE_PAIR_ITEM_ID",
    "COHERENCE_RECORD_ITEM_ID",
    "FINAL_FULL_CATALOG_VERSION",
    "MISSING_ONBOARDING_ITEM_ID",
    "ROUTE_INDEX_ALIGNMENT_ITEM_ID",
    "ReadinessProjectionInput",
    "complete_final_catalog",
    "final_catalog_readiness",
]
