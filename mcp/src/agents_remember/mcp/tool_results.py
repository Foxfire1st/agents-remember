"""Build compact direct ``CallToolResult`` objects in the MCP adapter boundary.

Replaces the 1.x monkeypatch of ``func_metadata._convert_to_content`` by
constructing ``CallToolResult`` directly in ``AgentsRememberMCP``'s call path:
JSON text blocks are serialized without pretty-printing indentation, while
``structured_content`` follows each tool's declared output schema unchanged.
"""

from __future__ import annotations

import json
from itertools import chain
from typing import Any

import pydantic_core
from mcp.server.mcpserver.tools import Tool
from mcp.server.mcpserver.utilities.types import Audio, Image
from mcp.types import (
    AudioContent,
    CallToolResult,
    ContentBlock,
    EmbeddedResource,
    ImageContent,
    InputRequiredResult,
    ResourceLink,
    TextContent,
)
from pydantic import BaseModel, TypeAdapter

_CONTENT_BLOCK_TYPES = (
    TextContent,
    ImageContent,
    AudioContent,
    ResourceLink,
    EmbeddedResource,
)


def compact_json_text(value: Any) -> str:
    """Serialize ``value`` as compact UTF-8 JSON matching the start tree's wire bytes."""

    raw = pydantic_core.to_json(value, fallback=str).decode("utf-8")
    return json.dumps(json.loads(raw), ensure_ascii=False, separators=(",", ":"))


def compact_content_blocks(result: Any) -> list[ContentBlock]:
    """Convert a Python tool result into compact MCP content blocks."""

    if result is None:
        return []
    if isinstance(result, _CONTENT_BLOCK_TYPES):
        return [result]
    if isinstance(result, Image):
        return [result.to_image_content()]
    if isinstance(result, Audio):
        return [result.to_audio_content()]
    if isinstance(result, list | tuple):
        return list(chain.from_iterable(compact_content_blocks(item) for item in result))
    text = result if isinstance(result, str) else compact_json_text(result)
    return [TextContent(type="text", text=text)]


def _validate_and_dump(output_model: Any, payload: Any) -> Any:
    if isinstance(output_model, type) and issubclass(output_model, BaseModel):
        return output_model.model_validate(payload, by_alias=True, by_name=True).model_dump(
            mode="json", by_alias=True
        )
    adapter = TypeAdapter(output_model)
    validated = adapter.validate_python(payload, by_alias=True, by_name=True)
    if isinstance(validated, BaseModel):
        return validated.model_dump(mode="json", by_alias=True)
    return adapter.dump_python(validated, mode="json", by_alias=True)


def _output_spec(tool: Tool | None) -> tuple[Any, bool]:
    fn_metadata = tool.fn_metadata if tool is not None else None
    if fn_metadata is None or fn_metadata.output_schema is None:
        return None, False
    return fn_metadata.output_model, fn_metadata.wrap_output


def build_call_tool_result(tool: Tool | None, result: Any) -> CallToolResult | InputRequiredResult:
    """Convert one tool's Python return value into a compact ``CallToolResult``."""

    if isinstance(result, InputRequiredResult):
        return result
    output_model, wrap_output = _output_spec(tool)
    if isinstance(result, CallToolResult):
        if output_model is not None and not result.is_error:
            _validate_and_dump(output_model, result.structured_content)
        return result

    unstructured = compact_content_blocks(result)
    if output_model is None:
        return CallToolResult(content=unstructured)

    payload = {"result": result} if wrap_output else result
    return CallToolResult(
        content=unstructured,
        structured_content=_validate_and_dump(output_model, payload),
    )


def build_early_refusal_result(payload: dict[str, Any]) -> CallToolResult:
    """Return an early-return guard refusal matching the start tree's dict-return wire shape."""

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
        structured_content=payload,
    )


__all__ = [
    "build_call_tool_result",
    "build_early_refusal_result",
    "compact_content_blocks",
    "compact_json_text",
]
