"""The launch override one ``role_start`` call hands to the selection owner (MIK-R196 rule 1).

A start names its launch choices as an agent, a model, an effort and feature values. A model or
an effort belongs to the agent it is chosen for, so a generic start refuses one given without an
agent: the selection owner binds every field to one provider, and a model it cannot bind to a
provider the call named would be a guess.

The free agent is the one exception the requirement admits: a bootstrap start made from a chat
names only a model, and the seat's provider is the caller's own harness. So the override keeps
the model (and an effort) without an agent, and the launch's caller-harness binding supplies the
provider before the selection is resolved.

On a configured host, :func:`bound_to_caller_harness` validates every Bootstrap agent/model/effort
choice before dispatch prepares anything. An explicit agent is preserved; otherwise the binding
owner supplies the caller's provider. The existing selection owner refuses choices it cannot offer,
and every other role keeps the refusal a model without its agent already gets.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import HTTPException

from agents_remember.cli.paseo_catalog import resolve_agent_selection
from agents_remember.models.role_agents import RoleStartCall
from agents_remember.models.role_identity import canonical_role
from agents_remember.models.role_launcher import RoleDispatchRequest

FREE_AGENT_ROLE = "bootstrap"

# The launch's caller-harness binding: the owner that maps the calling chat's harness to the
# provider the host runs (``role_launch_request_rules.with_caller_harness``).
CallerHarnessBinding = Callable[[Any, RoleDispatchRequest], RoleDispatchRequest]


def agent_override(call: RoleStartCall) -> dict[str, Any] | None:
    """The launch choices of one start, or ``None`` when the call names none.

    A model-only (or effort-only) choice is admitted for the free agent alone: its provider is
    the calling chat's harness, supplied by the binding, so the override carries no agent id and
    the binding's provider is the one the model is validated against.
    """

    names_a_model = call.model is not None or call.effort is not None
    if call.agent is None and names_a_model and not _binds_caller_harness(call):
        raise ValueError("a model or an effort is given without the agent it belongs to")
    choices: dict[str, Any] = {
        key: value
        for key, value in (
            ("agentId", call.agent),
            ("modelId", call.model),
            ("effortId", call.effort),
        )
        if value is not None
    }
    if call.feature_values:
        choices["featureValues"] = call.feature_values
    return choices or None


def bound_to_caller_harness(
    config: Any, request: RoleDispatchRequest, bind: CallerHarnessBinding
) -> tuple[RoleDispatchRequest, str | None]:
    """The request a free-agent override carries, and why it cannot be honoured.

    Preserve an explicit agent, or bind a model/effort-only choice to the caller's own harness.
    Validate that selection against the configured host's catalog before any preparation
    and return the refusal's own sentence for an invalid choice.

    A cold root is the one case this cannot decide: the providers a host offers exist only once it
    runs, so there the binding and the model check stay where the launch makes them, after the
    preparation, and the first-run report names what that preparation already did.
    """

    override = request.agent_override
    if (
        canonical_role(request.role) != FREE_AGENT_ROLE
        or override is None
        or (override.agent_id is None and override.model_id is None and override.effort_id is None)
        or not _host_is_configured(config)
    ):
        return request, None
    try:
        bound = request if override.agent_id is not None else bind(config, request)
    except HTTPException as refused:
        return request, str(refused.detail)
    selected = bound.agent_override
    if selected is None or selected.agent_id is None:
        return bound, None
    try:
        resolve_agent_selection(config, _free_seat_defaults(), (), selected)
    except ValueError as error:
        return request, str(error)
    return bound, None


def _binds_caller_harness(call: RoleStartCall) -> bool:
    """Whether this start's provider comes from the calling chat's own harness (rule 1)."""

    return canonical_role(call.role) == FREE_AGENT_ROLE


def _host_is_configured(config: Any) -> bool:
    """Whether the settings already name a host this process can ask about its providers."""

    try:
        return config.paseo_runtime is not None
    except ValueError:
        return False


def _free_seat_defaults() -> dict[str, Any]:
    """The role defaults the free seat's own override leaves unused.

    The override names its agent and any chosen model/effort, so these defaults contribute no
    selection: omitted choices keep the host's defaults.
    """

    return {"role": FREE_AGENT_ROLE, "agent": None, "model": None, "effort": None, "hostRole": {}}


__all__ = ["FREE_AGENT_ROLE", "CallerHarnessBinding", "agent_override", "bound_to_caller_harness"]
