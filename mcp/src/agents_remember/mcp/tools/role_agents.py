"""Payload builders for the tools through which role agents start, message and retire role agents."""

from __future__ import annotations

from typing import Any

from agents_remember.cli.paseo_role_tools import (
    runnable_harnesses,
    send_role_message,
    start_leaf_set,
    start_role,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import (
    RoleMessageCall,
    RoleStartCall,
    RoleStartSetCall,
)
from agents_remember.models.structural.agent import RetireChildRequest
from agents_remember.observer.tool_events import call_target_role

from .base import _tool_payload


def role_start_set_payload(
    config: McpRuntimeConfig,
    call: RoleStartSetCall,
) -> dict[str, Any]:
    # A set starts three seats; no single target role rides the river for it.
    return _tool_payload("role_start_set", start_leaf_set(config, call))


def role_start_payload(config: McpRuntimeConfig, call: RoleStartCall) -> dict[str, Any]:
    # The river names the started role: the choke point sees the answer, not the
    # call, so the role rides a context variable around it (never the answer).
    token = call_target_role.set(call.role)
    try:
        return _tool_payload("role_start", start_role(config, call))
    finally:
        call_target_role.reset(token)


def role_message_payload(config: McpRuntimeConfig, call: RoleMessageCall) -> dict[str, Any]:
    # Same as above for the addressed role. ``call.role`` is absent when the
    # recipient is addressed by agent id; then ``send_role_message`` carries
    # the role from the resolved recipient's durable receipt instead.
    token = call_target_role.set(call.role)
    try:
        result = send_role_message(config, call)
        # A held answer carries its own facts -- why, the permission, the
        # waiting counts and the issued held id (MIK-R100 rules 4a/4b): give
        # the projector that mapping explicitly so the shaped row keeps
        # them. Withdrawal and the issued id ride inside it, unchanged; the
        # guard, the log line and the whole detail still apply downstream.
        # Completion stays inside the scoped role so the observer event
        # keeps the recipient role; reset follows, even on exception.
        held = isinstance(result, dict) and result.get("status") == "held"
        return _tool_payload("role_message", result, answer=result if held else None)
    finally:
        call_target_role.reset(token)


def user_settings_init_payload(
    config: McpRuntimeConfig,
    *,
    harness: str | None,
    roles: dict[str, str] | None,
    dry_run: bool,
    target: str,
) -> dict[str, Any]:
    """Preview or write the one role-harness settings file (MIK-R196 rule 11).

    ``target="role-settings"`` writes the harness choice of each role. Only an absent destination
    is created; an existing one is read and reported, never completed. A refusal is an answer, not
    an exception: it names its code, its reason and the next action, and nothing was written.

    ``target="host-authority"`` is refused by name: the ``paseoRuntime`` block belongs to the
    installed MCP settings file, not to this user settings file (MIK-R137), and the host
    preparation stage owns that write.
    """

    from agents_remember.application.user_settings_init import (  # noqa: PLC0415 - one family
        SettingsInitRefused,
        SettingsInitRequest,
        initialize_role_settings,
        settings_path_without_read,
    )

    if target == "host-authority":
        answer = _host_authority_not_here(config, settings_path_without_read(config), target)
    else:
        try:
            written = initialize_role_settings(
                config,
                SettingsInitRequest(harness=harness, roles=roles, dry_run=dry_run),
                runnable_harnesses=runnable_harnesses,
            )
            answer = {**_previewed(written, dry_run), "target": target}
        except SettingsInitRefused as refused:
            # The refusal is answered from the refusal itself. Nothing here re-enters the reader
            # or the host catalog that refused (L188-B07): an unreadable, misplaced or colliding
            # file cannot raise a second time, no write is attempted, and no file content is
            # printed -- the path is resolved without reading the destination at all.
            answer = {
                # The refusal shape is the model's: ``state="refused"`` with ``code``, ``detail``
                # and ``nextAction``. A ``status`` key here is not declared and makes the answer
                # fail validation, which is how the census first caught it.
                "ok": False,
                "state": "refused",
                "code": refused.code,
                "detail": refused.detail,
                "nextAction": refused.next_action,
                "path": settings_path_without_read(config),
                "target": target,
            }
    return _tool_payload("user_settings_init", answer)


def _host_authority_not_here(config: McpRuntimeConfig, path: str, target: str) -> dict[str, Any]:
    """The refusal of a user-file host block, naming the file that does own it."""

    return {
        "ok": False,
        "state": "refused",
        "code": "host-authority-not-in-user-settings",
        "detail": f"The paseoRuntime block belongs to the installed MCP settings file "
        f"({config.config_path.as_posix()}); the user settings file carries role harness "
        "choices only, and this tool writes no host block.",
        "nextAction": "Configure paseoRuntime in the MCP settings file, or let the host "
        "preparation stage establish it; then call again with target role-settings.",
        "path": path,
        "target": target,
    }


def _previewed(answer: dict[str, Any], dry_run: bool) -> dict[str, Any]:
    """One answer whose state reads ``preview`` when the call wrote nothing."""

    return {**answer, "state": "preview" if dry_run else answer["state"]}


def retire_child_payload(
    config: McpRuntimeConfig,
    request: RetireChildRequest,
    **overrides: Any,
) -> dict[str, Any]:
    # MIK-R100 rule 8: the starter retires the receipt-tracked agent in its seat. An
    # empty id is refused by name inside the retirement.
    from agents_remember.cli.role_retire import retire_native_seat  # noqa: PLC0415

    payload = retire_native_seat(config, request, environment=overrides.get("environ"))
    return _tool_payload("retire_child", payload)


__all__ = [
    "retire_child_payload",
    "role_message_payload",
    "role_start_payload",
    "role_start_set_payload",
]
