"""Pure wire-model validation and token finalization for tool-shaped responses."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from agents_remember.models.base import ResponseEnvelope
from agents_remember.models.tokens import finalize_payload_tokens
from agents_remember.models.tools.tool_registry import TOOL_RESPONSE_MODELS

ResponseEnricher = Callable[[ResponseEnvelope], None]


def finalize_tool_response(
    tool_name: str,
    payload: dict[str, Any],
    *,
    enrich: ResponseEnricher | None = None,
) -> dict[str, Any]:
    """Validate one declared response, apply optional caller enrichment, and count tokens."""

    response = TOOL_RESPONSE_MODELS[tool_name].model_validate(payload)
    if enrich is not None:
        enrich(response)
    return finalize_payload_tokens(response.model_dump(mode="json", exclude_none=True))
