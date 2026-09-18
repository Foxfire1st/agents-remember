"""Application entry points for the capsule operation and the skill surface.

The MCP registration layer calls these; each one validates its typed request,
composes the owner that decides the answer, and returns the response contract the
wire declares. Nothing here formats protocol output or reads MCP types.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.kernel.coordination_context.models import EnclosureSelector
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsule_resources import (
    RoleCapsuleResponse,
    SkillCatalogListResponse,
    SkillCatalogReadResponse,
)
from agents_remember.models.role_capsules.vocabulary import CapsuleOperation
from agents_remember.models.skill_resources import SkillResourceCatalog

from .capsule import CapsuleCompileRequest, CapsuleSourceSelectionRequest, compile_task_capsule
from .catalog import (
    SkillSourceTree,
    build_skill_catalog,
    read_served_file,
    require_servable,
    unreadable_skills,
)
from .provider import SHIPPED_SKILL_ORIGIN, shipped_skill_tree
from .responses import (
    role_capsule_response,
    skill_catalog_list_response,
    skill_catalog_read_response,
)


@dataclass(frozen=True, slots=True)
class CapsuleOperationRequest:
    """What a caller asks the capsule operation for, as typed values.

    The enclosure and the task path are separate because the enclosure is what
    admits the seat and the task path is what the seat is addressed at; the task
    layer resolves the reference key from the enclosure's own repository identity.
    """

    enclosure_contract_path: str
    task_path: str
    role: str
    operation: CapsuleOperation
    code_repository_root: str | None = None


@dataclass(frozen=True, slots=True)
class SkillCatalogRequest:
    """Which canonical skills tree to serve, and under which server identity.

    Both default to the shipped corpus. A caller supplies them only to serve a
    different tree, which is how a test drives a synthetic one; the shipped
    defaults are what a registered server uses.
    """

    origin: str = SHIPPED_SKILL_ORIGIN
    root: Path | None = None


def role_capsule_compile_tool(
    config: McpRuntimeConfig,
    request: CapsuleOperationRequest,
    *,
    sources: CapsuleSourceSelectionRequest | None = None,
) -> RoleCapsuleResponse:
    """Compile one admitted capsule, or explain why it was refused."""

    outcome = compile_task_capsule(
        config,
        CapsuleCompileRequest(
            enclosure=EnclosureSelector(contract_path=Path(request.enclosure_contract_path)),
            task_path=request.task_path,
            operation=request.operation,
            role=request.role,
            code_repository_root=(
                None if request.code_repository_root is None else Path(request.code_repository_root)
            ),
        ),
        sources=sources,
    )
    return role_capsule_response(outcome)


def skill_catalog_list_tool(request: SkillCatalogRequest | None = None) -> SkillCatalogListResponse:
    """Discovery metadata for every skill the served tree publishes."""

    return skill_catalog_list_response(_catalog(request))


def skill_catalog_read_tool(
    uri: str, request: SkillCatalogRequest | None = None
) -> SkillCatalogReadResponse:
    """One served skill file, with the origin and revision it is published under."""

    catalog = _catalog(request)
    require_servable(catalog, action="reading a skill file")
    served = read_served_file(catalog, uri)
    root = served.entry.root
    return skill_catalog_read_response(served, skill_revision="" if root is None else root.revision)


def skill_catalog_registry(request: SkillCatalogRequest | None = None) -> SkillResourceCatalog:
    """The built catalog itself, for a caller that needs the registry as a value."""

    return _catalog(request)


def _catalog(request: SkillCatalogRequest | None) -> SkillResourceCatalog:
    selected = request or SkillCatalogRequest()
    with shipped_skill_tree() as tree:
        root = tree.root if selected.root is None else selected.root
        return build_skill_catalog(SkillSourceTree(root=root, origin=selected.origin))


__all__ = [
    "CapsuleOperationRequest",
    "SkillCatalogRequest",
    "role_capsule_compile_tool",
    "skill_catalog_list_tool",
    "skill_catalog_read_tool",
    "skill_catalog_registry",
    "unreadable_skills",
]
