"""The AR MCP boundary for role capsules and reusable skills.

Two surfaces, one module each, deliberately separate:

* :mod:`~agents_remember.application.skill_resources.capsule` is the narrow,
  read-only capsule operation. It takes an admitted task document plus a role and
  an operation, resolves the seat from the task layer, and returns the compiled
  capsule or the explanation of its refusal. It reads no caller-named path.
* :mod:`~agents_remember.application.skill_resources.catalog` is the SEP-2640
  skills transport's reading half: the host discovery registry and the bytes one
  ``skill://`` resource addresses, served with the origin and revision they carry.

The two are separate because they are separate things: a capsule is per-task
context composition, and a skill is a stable reusable instruction module. Nothing
here turns one into the other, and no resource read grants a tool permission.
"""

from __future__ import annotations

from .capsule import (
    COMPOSITION_MANIFEST,
    CapsuleCompileOutcome,
    CapsuleCompileRequest,
    CapsuleSeatAddress,
    CapsuleSourceSelectionRequest,
    admitted_tool_policy,
    capsule_payload,
    compile_task_capsule,
    routed_admission_for,
    routed_admission_request,
)
from .catalog import (
    ServedSkillFile,
    SkillCatalogError,
    SkillSourceTree,
    build_skill_catalog,
    index_resource_meta,
    read_served_file,
    require_servable,
    unreadable_skills,
)
from .frontmatter import SkillFrontmatter, SkillFrontmatterError, parse_skill_frontmatter
from .operation import (
    CapsuleOperationRequest,
    SkillCatalogRequest,
    role_capsule_compile_tool,
    skill_catalog_list_tool,
    skill_catalog_read_tool,
    skill_catalog_registry,
)
from .provider import (
    PACKAGED_COMPOSITION_MANIFEST,
    PACKAGED_SKILLS_DIRECTORY,
    SHIPPED_SKILL_ORIGIN,
    shipped_composition_corpus,
    shipped_skill_tree,
)
from .responses import (
    role_capsule_response,
    skill_catalog_list_response,
    skill_catalog_read_response,
)

__all__ = [
    "COMPOSITION_MANIFEST",
    "PACKAGED_COMPOSITION_MANIFEST",
    "PACKAGED_SKILLS_DIRECTORY",
    "SHIPPED_SKILL_ORIGIN",
    "CapsuleCompileOutcome",
    "CapsuleCompileRequest",
    "CapsuleOperationRequest",
    "CapsuleSeatAddress",
    "CapsuleSourceSelectionRequest",
    "ServedSkillFile",
    "SkillCatalogError",
    "SkillCatalogRequest",
    "SkillFrontmatter",
    "SkillFrontmatterError",
    "SkillSourceTree",
    "admitted_tool_policy",
    "build_skill_catalog",
    "capsule_payload",
    "compile_task_capsule",
    "index_resource_meta",
    "parse_skill_frontmatter",
    "read_served_file",
    "require_servable",
    "role_capsule_compile_tool",
    "role_capsule_response",
    "routed_admission_for",
    "routed_admission_request",
    "shipped_composition_corpus",
    "shipped_skill_tree",
    "skill_catalog_list_response",
    "skill_catalog_list_tool",
    "skill_catalog_read_response",
    "skill_catalog_read_tool",
    "skill_catalog_registry",
    "unreadable_skills",
]
