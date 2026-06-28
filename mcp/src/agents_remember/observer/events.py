"""The observer event envelope: ``ar-observer-event/v1``.

An event is an append-only, durable, replayable record of something that
happened in (or to) a lifecycle. The envelope is a Pydantic model -- not because
the write path is hot (it is IO-scale), but because events are a *persisted,
versioned contract* that the projection layer, replay, and the dashboard parse
back from disk. Read-side ``model_validate`` plus Literal-typed ``trust`` /
``actor`` keep the audit trail honest, and ``model_dump_json(by_alias=True)``
renders the camelCase wire form the rest of the package already uses.

This is intentionally *not* a public MCP response model: it carries no token
fields and is never returned by a tool, so it is not registered in
``PUBLIC_TOOL_RESPONSE_MODELS``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

OBSERVER_EVENT_SCHEMA = "ar-observer-event/v1"

# Event provenance. The rendering rule is "never pretend declared is observed":
# `declared` = a model's claim, `observed` = a fact the server saw, `inferred` =
# the reducer reasoned it, `approved` = a developer gate action.
Trust = Literal["declared", "observed", "inferred", "approved"]
# Who caused it. For developer actions, `data["via"]` carries chat|dashboard|cli
# -- "who" and "through what" never share a field.
Actor = Literal["model", "system", "developer"]


def now_iso() -> str:
    """Event timestamp: ISO 8601 with offset (UTC)."""
    return datetime.now(UTC).isoformat()


class Event(BaseModel):
    """One ``ar-observer-event/v1`` record.

    Fields are camelCase to match the package's response-model convention, so
    construction, ``model_validate``, and ``model_dump`` all agree on the wire
    keys without per-field aliases (which also keeps the synthesized ``__init__``
    honest for static checkers). ``schema_version`` is the single alias, because
    the wire key ``schema`` is an awkward Python attribute name. Always dump with
    ``model_dump_json(by_alias=True, exclude_none=True)`` so it renders as
    ``schema``.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(default=OBSERVER_EVENT_SCHEMA, alias="schema")
    id: str
    ts: str
    kind: str
    trust: Trust
    actor: Actor
    data: dict[str, Any] = Field(default_factory=dict)
    lifecycleId: str | None = None
    enclosure: str | None = None
    repoId: str | None = None
    sessionId: str | None = None
    spanId: str | None = None
