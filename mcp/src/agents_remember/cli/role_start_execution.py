"""Prepare and reserve one start, before the launch route publishes its receipt."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agents_remember.application.role_launch_context import (
    selection_binding,
)
from agents_remember.cli import role_launch_routes as route
from agents_remember.cli.role_launch_liveness import _Replacement
from agents_remember.cli.role_step_up_runtime import step_up_request
from agents_remember.models.role_identity import TASKLESS_ROLES
from agents_remember.tasks.launch_review_read import tolerate_unreadable_review

if TYPE_CHECKING:
    from fastapi.responses import JSONResponse

    from agents_remember.cli.paseo_launch import StartingAgent
    from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
    from agents_remember.models.role_launcher import RoleDispatchRequest


@tolerate_unreadable_review()
def start_execution(
    config: McpRuntimeConfig,
    request: RoleDispatchRequest,
    started_by: StartingAgent | None = None,
    *,
    held: bool = False,
    opening_message: str | None = None,
) -> JSONResponse:
    # Keep the route's public seams patchable by existing launch-boundary tests.

    context = route.resolve_role_launch_context(config, request)
    binding = selection_binding(request)
    request_digest = route._request_digest(context, request)
    if request.role in TASKLESS_ROLES:
        route._migrate_taskless_legacy_receipt(config, request)
    path = route._receipt_path(
        config, request, request.request_id if request.role in TASKLESS_ROLES else None
    )
    if route._receipt_carries_request(path, request):
        route._refuse_another_starter(path, request, started_by)
        prior = route._reconcile_prior_execution(
            config,
            path,
            request,
            request_digest,
            _Replacement(
                started_by,
                request.replaces_agent_id,
                request.replaces_interrupt_turn,
                str(request.request_id),
            ),
        )
        if prior is not None:
            return prior
    route._refuse_reused_request_id(config, path, request)
    handover_args = {"step_up": request.step_up} if request.step_up is not None else {}
    role_handover = route.prepare_role_handover(
        config,
        context,
        agent_override=request.agent_override,
        request_id=request.request_id,
        started_by=started_by,
        chat_started=request.chat_started,
        **handover_args,
    )
    request = step_up_request(request, role_handover.session_options, route._read_receipt(path))
    prepared = role_handover.handover
    prepared["stepUp"] = role_handover.session_options.get("stepUp")
    route._refuse_another_starter(path, request, started_by)
    projection, replaces, created, prior = route._reserve_message_binding_projection(
        config, route._BindingReservation(path, request, request_digest, prepared, started_by)
    )
    if prior is not None:
        return prior
    start = route._PreparedRoleStart(
        config=config,
        request=request,
        context=context,
        binding=binding,
        request_digest=request_digest,
        receipt_path=path,
        role_handover=role_handover,
        message_binding_projection=projection,
        replaces_agent_id=replaces,
        started_by=started_by,
        created_message_binding=created,
        held=held,
        opening_message=opening_message,
    )
    return route._launch_prepared_role_session(start, prompt=prepared["prompt"])


__all__ = ["start_execution"]
