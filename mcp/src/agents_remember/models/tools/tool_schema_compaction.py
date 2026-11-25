"""Lossless JSON-Schema-aware compaction for advertised MCP tool schemas."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

__all__ = ["compact_tool_schema"]

_SCHEMA_MAP_KEYS = frozenset({"properties", "patternProperties", "dependentSchemas"})
_NESTED_SCHEMA_KEYS = frozenset({"items", "additionalProperties", "not", "if", "then", "else"})
_SCHEMA_LIST_KEYS = frozenset({"items", "anyOf", "oneOf", "allOf", "prefixItems"})


def _should_drop_keyword(
    key: str,
    value: Any,
    *,
    strip_description: bool,
) -> bool:
    if key == "title":
        return True
    if key == "default" and value is None:
        return True
    return strip_description and key == "description"


def _compact_schema_map(
    mapping: Mapping[str, Any],
    *,
    strip_entry_description: bool,
) -> dict[str, Any]:
    return {
        name: (
            _compact_schema_node(entry, strip_description=strip_entry_description)
            if isinstance(entry, Mapping)
            else copy.deepcopy(entry)
        )
        for name, entry in mapping.items()
    }


def _compact_schema_list(items: list[Any]) -> list[Any]:
    return [
        _compact_schema_node(item) if isinstance(item, Mapping) else copy.deepcopy(item)
        for item in items
    ]


def _compact_keyword_value(key: str, value: Any, *, is_root: bool) -> Any:
    if isinstance(value, Mapping):
        if key == "$defs":
            return _compact_schema_map(value, strip_entry_description=is_root)
        if key in _SCHEMA_MAP_KEYS:
            return _compact_schema_map(value, strip_entry_description=False)
        if key in _NESTED_SCHEMA_KEYS:
            return _compact_schema_node(value)
    elif isinstance(value, list) and key in _SCHEMA_LIST_KEYS:
        return _compact_schema_list(value)
    return copy.deepcopy(value)


def _compact_schema_node(
    node: Mapping[str, Any],
    *,
    is_root: bool = False,
    strip_description: bool = False,
) -> dict[str, Any]:
    compacted: dict[str, Any] = {}
    for key, value in node.items():
        if _should_drop_keyword(key, value, strip_description=strip_description):
            continue
        compacted[key] = _compact_keyword_value(key, value, is_root=is_root)
    return compacted


def compact_tool_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return a lossless compacted copy of one MCP tool JSON Schema.

    Strips schema-level ``title``, redundant ``default: null``, and top-level
    ``$defs`` model-level ``description`` strings while preserving every
    property name (including properties literally named ``title``, ``default``,
    or ``description``), required list, enum/const, anyOf, $ref, and constraint.
    """

    return _compact_schema_node(schema, is_root=True)
