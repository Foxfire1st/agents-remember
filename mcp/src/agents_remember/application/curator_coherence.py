"""Configured-contract boundary for the curator-coherence lifecycle API."""

from __future__ import annotations

from agents_remember.application.lifecycle.configured_contract_admission import (
    ConfiguredContractRefused,
    admit_configured_contract,
    execute_configured_contract_operation,
    project_configured_contract_refusal,
)
from agents_remember.errors import CuratorCoherenceError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.lifecycles.curator_coherence import CuratorCoherenceRequest
from agents_remember.worktrees.integration.closeout.curator_coherence_publication import (
    curator_coherence_action,
)


def curator_coherence_tool(
    config: McpRuntimeConfig,
    request: CuratorCoherenceRequest,
) -> dict[str, object]:
    """Resolve one exact leaf contract and execute its single coherence authority API."""

    configured = admit_configured_contract(
        config,
        request.contract_path,
        require_candidate_identity=False,
    )
    if isinstance(configured, ConfiguredContractRefused):
        return _configured_refusal(request, configured)
    try:
        execution = execute_configured_contract_operation(
            configured,
            lambda: curator_coherence_action(configured.contract, request),
        )
    except CuratorCoherenceError as exc:
        return _domain_refusal(request, exc)
    if isinstance(execution, ConfiguredContractRefused):
        return _configured_refusal(request, execution)
    return execution


def _domain_refusal(
    request: CuratorCoherenceRequest,
    error: CuratorCoherenceError,
) -> dict[str, object]:
    result: dict[str, object] = {
        "ok": False,
        "operation": "curator_coherence",
        "action": request.action,
        "state": "refused",
        "summary": error.detail,
        "contractPath": request.contract_path,
        **error.response_fields(),
    }
    return result


def _configured_refusal(
    request: CuratorCoherenceRequest,
    refusal: ConfiguredContractRefused,
) -> dict[str, object]:
    projected = project_configured_contract_refusal(refusal, operation="curator_coherence")
    status = str(projected.get("status") or projected.get("state") or refusal.status)
    detail = str(projected.get("detail") or "configured leaf contract admission failed")
    result: dict[str, object] = {
        "ok": False,
        "operation": "curator_coherence",
        "action": request.action,
        "state": "refused",
        "summary": detail,
        "contractPath": request.contract_path,
        "status": status,
        "detail": detail,
        "nextAction": projected.get("nextAction") or "resolve_context",
    }
    for key in ("expected", "observed"):
        if key in projected:
            result[key] = projected[key]
    return result


__all__ = ["curator_coherence_tool"]
