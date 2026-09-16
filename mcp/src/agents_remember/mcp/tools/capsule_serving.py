"""Payload builders for the capsule operation and the skill surface."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents_remember.application.skill_resources.operation import (
    CapsuleOperationRequest,
    SkillCatalogRequest,
    role_capsule_compile_tool,
    skill_catalog_list_tool,
    skill_catalog_read_tool,
)
from agents_remember.application.skill_resources.provider import SHIPPED_SKILL_ORIGIN
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.vocabulary import CapsuleOperation

from .base import _tool_payload


def role_capsule_compile_payload(
    config: McpRuntimeConfig,
    *,
    contract_path: str,
    task_path: str,
    role: str,
    operation: CapsuleOperation,
) -> dict[str, Any]:
    return _tool_payload(
        "role_capsule_compile",
        role_capsule_compile_tool(
            config,
            CapsuleOperationRequest(
                enclosure_contract_path=contract_path,
                task_path=task_path,
                role=role,
                operation=operation,
            ),
        ).to_payload(),
    )


def skill_catalog_list_payload(
    *, origin: str | None = None, root: Path | None = None
) -> dict[str, Any]:
    request = (
        None
        if origin is None and root is None
        else SkillCatalogRequest(origin=origin or SHIPPED_SKILL_ORIGIN, root=root)
    )
    return _tool_payload("skill_catalog_list", skill_catalog_list_tool(request).to_payload())


def skill_catalog_read_payload(
    uri: str, *, origin: str | None = None, root: Path | None = None
) -> dict[str, Any]:
    request = (
        None
        if origin is None and root is None
        else SkillCatalogRequest(origin=origin or SHIPPED_SKILL_ORIGIN, root=root)
    )
    return _tool_payload("skill_catalog_read", skill_catalog_read_tool(uri, request).to_payload())
