"""Application entry points for the capsule operation and the skill surface.

Typed request objects and the shared catalog builder behind skill serving. The
model-facing tools that used to call these left the surface (MIK-R112); role
start compiles capsules through ``role_capsules.launch``, and skills are read
through the ``skill://`` resources and the skills extension. Nothing here
formats protocol output or reads MCP types.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.models.role_capsules.vocabulary import CapsuleOperation
from agents_remember.models.skill_resources import SkillResourceCatalog

from .catalog import SkillSourceTree, build_skill_catalog
from .provider import SHIPPED_SKILL_ORIGIN, shipped_skill_tree


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
    "skill_catalog_registry",
]
