"""Permanent validation for the advertised MCP surface.

The public inventory, response registry, live FastMCP registration, and dispatch schema
are separate artifacts.  This validator makes their agreement one executable contract
without reaching into FastMCP private state: callers pass the result of its public
``list_tools`` API.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, get_args

from agents_remember.mcp.tools.base import PUBLIC_TOOLS, RESERVED_TOOLS
from agents_remember.models.structural.agent import DispatchAgentResponse, StructuralRole
from agents_remember.models.tools.tool_registry import (
    INTERNAL_COMPAT_TOOL_NAMES,
    PUBLIC_TOOL_RESPONSE_MODELS,
    TOOL_RESPONSE_MODELS,
)

DISPATCH_AGENT_INPUT_FIELDS = frozenset({"task_document_ref", "role", "brief", "label"})
_DISPATCH_REQUIRED = frozenset({"task_document_ref", "role", "brief"})
_STRUCTURAL_ROLES = frozenset(get_args(StructuralRole))
_DESCRIPTION_FACTS = (
    "AR_HOSTED_SESSION_ID",
    "plane",
    "ambient",
    "brief",
    "never falls back",
)


class AdvertisedTool(Protocol):
    """The public FastMCP tool fields consumed by the validator."""

    @property
    def name(self) -> str: ...

    @property
    def description(self) -> str | None: ...

    @property
    def inputSchema(self) -> Mapping[str, Any]: ...


class PublicSurfaceViolation(RuntimeError):
    """The independently declared public-surface authorities disagree."""


@dataclass(frozen=True)
class PublicSurfaceEvidence:
    """Content-addressed proof of one validated live advertisement."""

    tool_names: tuple[str, ...]
    dispatch_schema_digest: str
    dispatch_response_model: str


def _schema_node(root: Mapping[str, Any], node: object) -> Mapping[str, Any]:
    if not isinstance(node, Mapping):
        raise PublicSurfaceViolation("dispatch_agent input schema contains a malformed node")
    ref = node.get("$ref")
    if ref is None:
        return node
    if not isinstance(ref, str) or not ref.startswith("#/"):
        raise PublicSurfaceViolation("dispatch_agent input schema contains an external reference")
    resolved: object = root
    for part in ref[2:].split("/"):
        if not isinstance(resolved, Mapping) or part not in resolved:
            raise PublicSurfaceViolation(
                f"dispatch_agent input schema has an unresolved ref: {ref}"
            )
        resolved = resolved[part]
    if not isinstance(resolved, Mapping):
        raise PublicSurfaceViolation(f"dispatch_agent input schema ref is not an object: {ref}")
    return resolved


def _schema_types(root: Mapping[str, Any], node: object) -> frozenset[str]:
    resolved = _schema_node(root, node)
    direct = resolved.get("type")
    if isinstance(direct, str):
        return frozenset({direct})
    variants = resolved.get("anyOf")
    if isinstance(variants, Sequence) and not isinstance(variants, str | bytes):
        return frozenset(value for item in variants for value in _schema_types(root, item))
    return frozenset()


def _dispatch_properties(schema: Mapping[str, Any]) -> Mapping[str, Any]:
    """Validate the dispatch envelope and return its exact property map."""

    if schema.get("additionalProperties") is not False:
        raise PublicSurfaceViolation("dispatch_agent schema must reject undeclared inputs")
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or frozenset(properties) != DISPATCH_AGENT_INPUT_FIELDS:
        raise PublicSurfaceViolation(
            "dispatch_agent must advertise exactly task_document_ref, role, brief, and label"
        )
    required = schema.get("required")
    if not isinstance(required, Sequence) or isinstance(required, str | bytes):
        raise PublicSurfaceViolation("dispatch_agent must declare its required inputs")
    if frozenset(required) != _DISPATCH_REQUIRED:
        raise PublicSurfaceViolation(
            "dispatch_agent must require task_document_ref, role, and brief only"
        )
    return properties


def _validate_task_ref_schema(schema: Mapping[str, Any], node: object) -> None:
    task_ref = _schema_node(schema, node)
    if task_ref.get("additionalProperties") is not False:
        raise PublicSurfaceViolation("dispatch_agent task_document_ref must reject extra identity")
    task_properties = task_ref.get("properties")
    if not isinstance(task_properties, Mapping) or frozenset(task_properties) != {
        "repository",
        "path",
    }:
        raise PublicSurfaceViolation(
            "dispatch_agent task_document_ref must be the canonical repository/path address"
        )
    task_required = task_ref.get("required")
    if not isinstance(task_required, Sequence) or frozenset(task_required) != {
        "repository",
        "path",
    }:
        raise PublicSurfaceViolation(
            "dispatch_agent task_document_ref must require repository and path"
        )


def _validate_dispatch_values(schema: Mapping[str, Any], properties: Mapping[str, Any]) -> None:
    role = _schema_node(schema, properties["role"])
    if frozenset(role.get("enum", ())) != _STRUCTURAL_ROLES:
        raise PublicSurfaceViolation("dispatch_agent role enum drifted from StructuralRole")
    if _schema_types(schema, properties["brief"]) != {"string"}:
        raise PublicSurfaceViolation("dispatch_agent brief must be a string")
    if _schema_types(schema, properties["label"]) != {"string", "null"}:
        raise PublicSurfaceViolation("dispatch_agent label must be string-or-null")


def _validate_dispatch_schema(schema: Mapping[str, Any]) -> None:
    properties = _dispatch_properties(schema)
    _validate_task_ref_schema(schema, properties["task_document_ref"])
    _validate_dispatch_values(schema, properties)


def _require_authority(condition: bool, message: str) -> None:
    if not condition:
        raise PublicSurfaceViolation(message)


def _validate_inventory_authorities() -> None:
    """Require every static public-inventory authority to agree."""

    _require_authority(
        len(PUBLIC_TOOLS) == len(set(PUBLIC_TOOLS)),
        "PUBLIC_TOOLS contains a duplicate",
    )
    _require_authority(
        len(RESERVED_TOOLS) == len(set(RESERVED_TOOLS)),
        "RESERVED_TOOLS contains a duplicate",
    )
    _require_authority(
        not set(PUBLIC_TOOLS).intersection(RESERVED_TOOLS),
        "public and reserved tool inventories overlap",
    )
    _require_authority(
        tuple(PUBLIC_TOOL_RESPONSE_MODELS)
        == tuple(name for name in TOOL_RESPONSE_MODELS if name not in INTERNAL_COMPAT_TOOL_NAMES),
        "public response-model registry is not a stable projection",
    )
    _require_authority(
        set(PUBLIC_TOOL_RESPONSE_MODELS) == set(PUBLIC_TOOLS),
        "public tools and response-model registry disagree",
    )
    _require_authority(
        TOOL_RESPONSE_MODELS.get("dispatch_agent") is DispatchAgentResponse,
        "dispatch_agent is not mapped to DispatchAgentResponse",
    )
    _require_authority(
        "spawn_agent_session" not in PUBLIC_TOOLS and "spawn_agent_session" in TOOL_RESPONSE_MODELS,
        "spawn_agent_session must remain internal and retain its internal response model",
    )


def _validated_live_names(live_tools: Sequence[AdvertisedTool]) -> tuple[str, ...]:
    names = tuple(tool.name for tool in live_tools)
    if names != PUBLIC_TOOLS:
        raise PublicSurfaceViolation("live tool advertisement differs from PUBLIC_TOOLS order")
    return names


def _validated_dispatch_schema(live_tools: Sequence[AdvertisedTool]) -> Mapping[str, Any]:
    dispatch = next(tool for tool in live_tools if tool.name == "dispatch_agent")
    description = dispatch.description or ""
    missing_facts = [fact for fact in _DESCRIPTION_FACTS if fact not in description]
    if missing_facts:
        raise PublicSurfaceViolation(
            "dispatch_agent description omits caller-boundary facts: " + ", ".join(missing_facts)
        )
    schema = dict(dispatch.inputSchema)
    _validate_dispatch_schema(schema)
    return schema


def validate_public_surface(live_tools: Sequence[AdvertisedTool]) -> PublicSurfaceEvidence:
    """Validate one public ``list_tools`` result against every local authority."""

    _validate_inventory_authorities()
    names = _validated_live_names(live_tools)
    schema = _validated_dispatch_schema(live_tools)
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return PublicSurfaceEvidence(
        tool_names=names,
        dispatch_schema_digest=f"sha256:{hashlib.sha256(encoded).hexdigest()}",
        dispatch_response_model=DispatchAgentResponse.__name__,
    )
