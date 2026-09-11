"""Public rendering of complete typed certification admission findings."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agents_remember.errors import CertificationContractError
from agents_remember.worktrees.route_review import route_review_refusal_projection
from agents_remember.worktrees.worktree_contract import WorktreeContract


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_value(item) for item in value]
    if isinstance(value, bytes):
        return {"encoding": "hex", "value": value.hex()}
    return value


def certification_admission_refusal(
    operation: str,
    error: CertificationContractError,
    *,
    contract: WorktreeContract | None = None,
) -> dict[str, object]:
    """Preserve expected/observed facts without dropping all but the first failure."""
    result: dict[str, object] = {
        "ok": False,
        "operation": operation,
        "state": "refused",
        "status": "certification-admission-refused",
        "detail": str(error),
        "findings": [_json_value(finding) for finding in error.findings],
        "gateStarts": 0,
    }
    route_finding = next(
        (
            finding
            for finding in error.findings
            if finding.get("path") == "routeReview" and isinstance(finding.get("code"), str)
        ),
        None,
    )
    if route_finding is not None:
        route = route_review_refusal_projection(
            str(route_finding["code"]),
            str(route_finding.get("detail", error)),
            contract=contract,
        )
        result.update(route)
    return result
