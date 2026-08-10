"""MCP response adapters for terminal-session application operations."""

from __future__ import annotations

from typing import Any

from agents_remember.application.terminal_tools import (
    DEFAULT_SPAWN_SEAT,
    NO_RETIRED_INPUTS,
    NO_SPAWN_OVERRIDES,
    UNATTRIBUTED_SPAWN,
    RetiredSpawnInputs,
    SpawnedBy,
    SpawnOverrides,
    SpawnSeat,
    attach_terminal_session_to_leaf_tool,
    session_rename_tool,
    session_retire_tool,
    spawn_agent_session_tool,
)
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

from .base import _tool_payload


def attach_terminal_session_to_leaf_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    leaf_key: str,
    role: str | None = None,
    host: Any = None,
) -> dict[str, Any]:
    return _tool_payload(
        "attach_terminal_session_to_leaf",
        attach_terminal_session_to_leaf_tool(
            config,
            session_id=session_id,
            leaf_key=leaf_key,
            role=role,
            host=host,
        ),
    )


def spawn_agent_session_payload(
    config: McpRuntimeConfig,
    *,
    seat: SpawnSeat = DEFAULT_SPAWN_SEAT,
    retired: RetiredSpawnInputs = NO_RETIRED_INPUTS,
    spawned_by: SpawnedBy = UNATTRIBUTED_SPAWN,
    overrides: SpawnOverrides = NO_SPAWN_OVERRIDES,
) -> dict[str, Any]:
    return _tool_payload(
        "spawn_agent_session",
        spawn_agent_session_tool(
            config,
            seat=seat,
            retired=retired,
            spawned_by=spawned_by,
            overrides=overrides,
        ),
    )


def session_retire_payload(
    config: McpRuntimeConfig,
    *,
    actor_session_id: str,
    session_id: str,
    reason: str = "manual retire",
    host: Any = None,
) -> dict[str, Any]:
    return _tool_payload(
        "session_retire",
        session_retire_tool(
            config,
            actor_session_id=actor_session_id,
            session_id=session_id,
            reason=reason,
            host=host,
        ),
    )


def session_rename_payload(
    config: McpRuntimeConfig,
    *,
    session_id: str,
    label: str,
) -> dict[str, Any]:
    return _tool_payload(
        "session_rename",
        session_rename_tool(config, session_id=session_id, label=label),
    )
