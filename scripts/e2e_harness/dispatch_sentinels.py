"""Controlled negative proofs for the canonical dispatch advertisement contract."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from agents_remember.mcp.public_surface import (
    PublicSurfaceViolation,
    validate_dispatch_advertisement,
)


@dataclass(frozen=True)
class RejectionSentinel:
    name: str
    description: str
    input_schema: dict[str, Any]
    expected_failure: str


def dispatch_rejection_sentinels(
    *,
    tool_name: str,
    description: object,
    input_schema: object,
) -> list[dict[str, object]]:
    """Prove live description and schema defects fail at the canonical boundary."""

    if not isinstance(description, str) or not isinstance(input_schema, dict):
        raise RuntimeError("live dispatch advertisement cannot seed negative sentinels")
    missing_brief = _without_brief(input_schema)
    cases = (
        RejectionSentinel(
            name="missing-brief-property",
            description=description,
            input_schema=missing_brief,
            expected_failure="must advertise exactly",
        ),
        RejectionSentinel(
            name="missing-ambient-description",
            description=description.replace("ambient", ""),
            input_schema=input_schema,
            expected_failure="omits caller-boundary facts: ambient",
        ),
    )
    return [_prove_rejection(tool_name, case) for case in cases]


def _without_brief(input_schema: dict[str, Any]) -> dict[str, Any]:
    candidate = copy.deepcopy(input_schema)
    properties = candidate.get("properties")
    if not isinstance(properties, dict):
        raise RuntimeError("live dispatch schema has no properties for negative sentinel")
    properties.pop("brief", None)
    return candidate


def _prove_rejection(
    tool_name: str,
    case: RejectionSentinel,
) -> dict[str, object]:
    try:
        validate_dispatch_advertisement(
            name=tool_name,
            description=case.description,
            input_schema=case.input_schema,
        )
    except PublicSurfaceViolation as exc:
        return _rejection_evidence(case, exc)
    raise RuntimeError(
        f"dispatch sentinel {case.name!r} did not fail; "
        f"expected canonical rejection {case.expected_failure!r}"
    )


def _rejection_evidence(
    case: RejectionSentinel,
    exc: PublicSurfaceViolation,
) -> dict[str, object]:
    actual = f"{type(exc).__name__}: {exc}"
    if case.expected_failure not in str(exc):
        raise RuntimeError(
            f"dispatch sentinel {case.name!r} failed at the wrong boundary; "
            f"expected {case.expected_failure!r}, actual {actual!r}"
        ) from exc
    return {
        "kind": "negative-sentinel",
        "sentinel": case.name,
        "status": "passed",
        "expectedFailure": case.expected_failure,
        "actualFailure": actual,
        "owner": "canonical dispatch advertisement validator",
    }
