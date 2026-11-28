"""Build compact direct ``CallToolResult`` objects in the MCP adapter boundary.

Constructs ``CallToolResult`` directly inside each registered tool wrapper:
JSON text blocks are serialized without pretty-printing indentation, while
``structured_content`` follows each tool's declared return annotation without
touching private SDK metadata.
"""

from __future__ import annotations

import functools
import inspect
import json
import operator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from itertools import chain
from types import GenericAlias, UnionType
from typing import Annotated, Any, Union, get_args, get_origin, get_type_hints

import pydantic_core
from mcp.server.mcpserver import Audio, Image
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
from pydantic import BaseModel, PydanticUserError, TypeAdapter
from typing_extensions import is_typeddict

_CONTENT_BLOCK_TYPES = (
    TextContent,
    ImageContent,
    AudioContent,
    ResourceLink,
    EmbeddedResource,
)
_CONTENT_TYPES = (*_CONTENT_BLOCK_TYPES, Image, Audio)
_CONTENT_SEQUENCE_ORIGINS = (list, tuple, Sequence)
_WRAPPED_PRIMITIVES = (str, int, float, bool, bytes, type(None))


@dataclass(frozen=True, slots=True)
class StructuredOutputSpec:
    """Structured-output conversion plan derived from a tool's return annotation."""

    output_model: Any
    wrap_output: bool = False


def compact_json_text(value: Any) -> str:
    """Serialize ``value`` as compact UTF-8 JSON without pretty-printing indentation."""

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


def _is_input_required_type(annotation: Any) -> bool:
    return isinstance(annotation, type) and issubclass(annotation, InputRequiredResult)


def _is_union_origin(origin: Any) -> bool:
    return origin is Union or origin is UnionType


def _unwrap_annotated(annotation: Any) -> tuple[Any, tuple[Any, ...]]:
    metadata: list[Any] = []
    current = annotation
    while get_origin(current) is Annotated:
        args = get_args(current)
        current = args[0]
        metadata.extend(args[1:])
    return current, tuple(metadata)


def _returns_content(annotation: Any) -> bool:
    base, _ = _unwrap_annotated(annotation)
    origin = get_origin(base)
    if origin is None:
        return isinstance(base, type) and issubclass(base, _CONTENT_TYPES)
    if _is_union_origin(origin) or origin in _CONTENT_SEQUENCE_ORIGINS:
        return any(_returns_content(arg) for arg in get_args(base))
    return False


def _strip_input_required_union(annotation: Any, base: Any) -> tuple[Any, Any] | None:
    if not _is_union_origin(get_origin(base)):
        return annotation, base
    args = get_args(base)
    residual = tuple(arg for arg in args if not _is_input_required_type(arg))
    if not residual:
        return None
    if len(residual) == len(args):
        return annotation, base
    effective = residual[0] if len(residual) == 1 else functools.reduce(operator.or_, residual)
    next_base, _ = _unwrap_annotated(effective)
    return effective, next_base


def _classify_class_output(
    effective_annotation: Any, return_type: type[Any]
) -> StructuredOutputSpec | None:
    if issubclass(return_type, BaseModel) or is_typeddict(return_type):
        return StructuredOutputSpec(output_model=return_type, wrap_output=False)
    if return_type in _WRAPPED_PRIMITIVES:
        return StructuredOutputSpec(output_model=effective_annotation, wrap_output=True)
    try:
        hints = get_type_hints(return_type)
    except Exception:
        hints = {}
    return StructuredOutputSpec(output_model=return_type, wrap_output=False) if hints else None


def _classify_output_type(
    effective_annotation: Any, return_type: Any
) -> StructuredOutputSpec | None:
    if isinstance(return_type, GenericAlias):
        args = get_args(return_type)
        if get_origin(return_type) is dict and len(args) == 2 and args[0] is str:
            return StructuredOutputSpec(output_model=return_type, wrap_output=False)
        return StructuredOutputSpec(output_model=effective_annotation, wrap_output=True)
    if isinstance(return_type, type):
        return _classify_class_output(effective_annotation, return_type)
    return StructuredOutputSpec(output_model=effective_annotation, wrap_output=True)


def _unwrap_call_tool_result(
    effective_annotation: Any, base: Any, metadata: tuple[Any, ...]
) -> tuple[Any, Any] | None:
    if not (isinstance(base, type) and issubclass(base, CallToolResult)):
        return effective_annotation, base
    if not metadata:
        return None
    inner = metadata[0]
    rebuilt = Annotated[(inner, *metadata[1:])] if len(metadata) >= 2 else inner
    return rebuilt, inner


def _resolve_output_annotation(
    fn: Callable[..., Any], *, structured_output: bool | None
) -> tuple[Any, Any] | None:
    try:
        return_annotation = inspect.signature(fn, eval_str=True).return_annotation
    except NameError:
        return None
    if return_annotation is inspect.Parameter.empty:
        return None
    base, metadata = _unwrap_annotated(return_annotation)
    if _is_input_required_type(base):
        return None
    stripped = _strip_input_required_union(return_annotation, base)
    if stripped is None:
        return None
    unwrapped = _unwrap_call_tool_result(stripped[0], stripped[1], metadata)
    if unwrapped is None or (structured_output is None and _returns_content(unwrapped[1])):
        return None
    return unwrapped


def _schema_serializable(output_model: Any) -> bool:
    try:
        TypeAdapter(output_model).json_schema()
    except (
        PydanticUserError,
        NameError,
        TypeError,
        ValueError,
        pydantic_core.SchemaError,
        pydantic_core.ValidationError,
    ):
        return False
    return True


def structured_output_spec(
    fn: Callable[..., Any],
    *,
    structured_output: bool | None = None,
) -> StructuredOutputSpec | None:
    """Derive whether ``fn`` produces structured output from its return annotation."""

    if structured_output is False:
        return None
    resolved = _resolve_output_annotation(fn, structured_output=structured_output)
    if resolved is None:
        return None
    spec = _classify_output_type(*resolved)
    if spec is None or not _schema_serializable(spec.output_model):
        return None
    return spec


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


def build_call_tool_result(
    spec: StructuredOutputSpec | None, result: Any
) -> CallToolResult | InputRequiredResult:
    """Convert one tool's Python return value into a compact ``CallToolResult``."""

    if isinstance(result, InputRequiredResult | CallToolResult):
        return result

    unstructured = compact_content_blocks(result)
    if spec is None:
        return CallToolResult(content=unstructured)

    dumped = _validate_and_dump(spec.output_model, result)
    structured = {"result": dumped} if spec.wrap_output else dumped
    return CallToolResult(content=unstructured, structured_content=structured)


def build_early_refusal_result(payload: dict[str, Any]) -> CallToolResult:
    """Return an early-return guard refusal matching the indented dict-return wire shape."""

    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2))],
        structured_content=payload,
    )


__all__ = [
    "StructuredOutputSpec",
    "build_call_tool_result",
    "build_early_refusal_result",
    "compact_content_blocks",
    "compact_json_text",
    "structured_output_spec",
]
