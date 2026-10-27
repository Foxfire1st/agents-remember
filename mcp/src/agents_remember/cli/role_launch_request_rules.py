"""Two request rules a role launch applies before it writes anything.

MIK-R196 rule 1 puts the free seat on the caller's own harness, and MIK-R111 rule 3 keeps a start
succeeding while naming requirement files the leaf does not hold. Both are rules about the request
and its answer, not about routing, so they live here and the route module keeps the calls — which
is also what keeps that already-large module from growing.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from agents_remember.application.caller_harness import provider_for_caller
from agents_remember.cli.paseo_bridge import (
    BRIDGE_TIMEOUT,
    RUNTIME_NOT_CONFIGURED,
    PaseoBridgeFailure,
)
from agents_remember.cli.paseo_catalog import launcher_catalog
from agents_remember.kernel.primitives.named_choices import named as match_choice
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import RoleAgentOverride, RoleDispatchRequest

BRIDGE_FAILURE_STATUS = {RUNTIME_NOT_CONFIGURED: 503, BRIDGE_TIMEOUT: 504}


def bridge_http_error(error: PaseoBridgeFailure) -> HTTPException:
    """A failed bridge call as a route error whose text names the state (the launcher shows it)."""

    return HTTPException(status_code=BRIDGE_FAILURE_STATUS.get(error.code, 502), detail=str(error))


def missing_copies_receipt_warning(
    document_files: object, step_up_warning: str | None = None
) -> dict[str, str]:
    """The receipt warning for requirement files the leaf names but lacks.

    MIK-R111 rule 3, failure behavior: the start succeeds, and INDEX.json, the answer and the
    handover name the missing file. The handover names it through
    ``taskDocumentFiles.missingCopies``; this carries it onto the receipt, from which the start
    answer reads its warning.
    """

    missing = document_files.get("missingCopies") if isinstance(document_files, dict) else None
    if not missing:
        return {"warning": step_up_warning} if step_up_warning else {}
    names = ", ".join(str(name) for name in missing)
    return {
        "warning": (
            f"{step_up_warning + ' ' if step_up_warning else ''}Task document copies missing: {names}. The start succeeds; the "
            "next task_doc write on the leaf document refreshes the copies once "
            "the files exist."
        )
    }


def with_caller_harness(
    config: McpRuntimeConfig, request: RoleDispatchRequest
) -> RoleDispatchRequest:
    """Give the free seat the caller's own harness, or refuse with the harnesses it can run.

    MIK-R196 rule 1: the seat starts on the harness of the chat that made the call, at that
    harness's default model and effort, unless the call names an agent itself. The identity comes
    from the MCP client's own name (:mod:`agents_remember.application.caller_harness`); a client
    that names nothing, or a harness the host does not offer, is refused rather than replaced by
    the first provider the host lists.
    """

    if canonical_role(request.role) != "bootstrap" or not request.chat_started:
        return request
    override: Any = request.agent_override
    if override is not None and override.agent_id:
        return request
    try:
        catalog = launcher_catalog(config)
        offered = [agent["id"] for agent in catalog.agents]
    except PaseoBridgeFailure as error:
        raise bridge_http_error(error) from error
    selected = (
        match_choice(catalog.agents, request.caller_harness) if request.caller_harness else None
    )
    if catalog.discovery.get("source") == "unknown":
        provider = request.caller_harness
    else:
        provider = (
            selected["id"]
            if selected is not None
            else provider_for_caller(request.caller_harness, offered)
        )
    if provider is None:
        named = (
            f"the caller named its harness {request.caller_harness!r}"
            if request.caller_harness
            else ("the caller's MCP client named no harness")
        )
        raise HTTPException(
            status_code=409,
            detail=(
                f"This host cannot be given the caller's own harness: {named}. "
                f"It can run: {', '.join(offered) or 'no provider'}. "
                "Name one with `agent` on this call, or install that harness's CLI and retry."
            ),
        )
    fields = override.model_dump(exclude_none=True) if override is not None else {}
    return request.model_copy(
        update={"agent_override": RoleAgentOverride.model_validate({**fields, "agentId": provider})}
    )
