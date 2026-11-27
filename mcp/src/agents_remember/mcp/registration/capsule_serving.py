"""The SEP-2640 skills surface, built as an MCP 2.x ``Extension``.

This module builds the :class:`~agents_remember.mcp.registration.skills_extension.SkillsExtension`
passed to ``MCPServer(extensions=[...])`` at construction time:

- declares ``io.modelcontextprotocol/skills`` in ``ServerCapabilities.extensions``;
- binds ``skills/list`` and ``skills/get`` through ``MethodBinding``;
- registers ``skill://index.json`` and every served skill file through
  ``ResourceBinding``.

The optional ``resources/directory/read`` method gated behind ``directoryRead``
is deliberately not implemented and not declared. Nothing here activates a
skill, grants a capability, or turns server-supplied text into local-file trust.
"""

from __future__ import annotations

from typing import Any

from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.mcpserver import MCPServer, ResourceBinding
from mcp.server.mcpserver.resources import FunctionResource

from agents_remember.application.skill_resources import (
    SkillCatalogError,
    build_skill_catalog,
    index_resource_meta,
    read_served_file,
    require_servable,
    shipped_skill_tree,
)
from agents_remember.application.skill_resources.operation import skill_catalog_registry
from agents_remember.models.skill_resources import (
    JSON_MIME,
    SKILL_INDEX_URI,
    SKILLS_EXTENSION_ID,
    SkillResourceCatalog,
    SkillResourceEntry,
    SkillResourceFile,
    skill_meta,
)
from mcp import Client

from .skills_extension import SkillsExtension


def build_skills_extension(catalog: SkillResourceCatalog | None = None) -> SkillsExtension:
    """Build the SEP-2640 extension (capability, methods, and skill resources) before server init."""

    resolved = skill_catalog_registry() if catalog is None else catalog
    require_servable(resolved, action="publishing the skill resources")
    bindings: list[ResourceBinding] = [ResourceBinding(index_resource(resolved))]
    for entry in resolved.entries:
        for record in entry.files:
            bindings.append(ResourceBinding(skill_file_resource(resolved, entry, record)))
    return SkillsExtension(bindings)


def declared_extensions(server: MCPServer[Any]) -> dict[str, Any]:
    """The extension declarations this server advertises to an MCP client."""

    import asyncio  # noqa: PLC0415 - one stdlib import for one synchronous bridge

    async def _query() -> dict[str, Any]:
        async with Client(server) as client:
            capabilities = client.server_capabilities
            if capabilities is None or not capabilities.extensions:
                return {}
            return dict(capabilities.extensions)

    return asyncio.run(_query())


def skill_file_resource(
    catalog: SkillResourceCatalog, entry: SkillResourceEntry, record: SkillResourceFile
) -> FunctionResource:
    """One MCPServer resource for one served skill file.

    The resource is data: ``resources/read`` returns the file's bytes with its
    origin and revision in ``_meta``. There is no path from here to a tool
    permission, a skill activation, or a read outside the skill's own directory,
    and ``read_served_file`` proves containment before it reads.
    """

    def load() -> str:
        served = read_served_file(catalog, record.uri)
        text = served.text
        if text is None:
            raise SkillCatalogError(
                f"skill resource {record.uri!r} is not UTF-8 text; an MCP text resource "
                "cannot carry it"
            )
        return text

    return FunctionResource(
        uri=record.uri,
        name=f"{entry.name}: {record.relative_path}",
        description=(
            f"{entry.description} (file {record.relative_path} of the {entry.origin} "
            f"skill {entry.name})"
        ),
        mime_type=record.mime_type,
        meta=skill_meta(entry, record),
        fn=load,
    )


def index_resource(catalog: SkillResourceCatalog) -> FunctionResource:
    """This server's own discovery index resource for ``catalog``.

    It is the Agent Skills well-known-discovery shape and is *this server's* surface,
    not the extension's enumeration result: SEP-2640 enumerates through ``skills/list``,
    whose entries carry verbatim frontmatter and per-file digests.
    """

    def load() -> str:
        require_servable(catalog, action="reading the skill index")
        return catalog.index_bytes().decode("utf-8")

    return FunctionResource(
        uri=SKILL_INDEX_URI,
        name="skill index",
        description=(
            "This server's own discovery index: name, description and SKILL.md resource URI "
            "for each skill, with the publishing origin and revision in _meta. SEP-2640 "
            "enumeration is the skills/list method"
        ),
        mime_type=JSON_MIME,
        meta=index_resource_meta(catalog),
        fn=load,
    )


def read_resource_contents(server: MCPServer[Any], uri: str) -> list[ReadResourceContents]:
    """Read one resource through the server's own registration, synchronously."""

    import asyncio  # noqa: PLC0415 - one stdlib import for one synchronous bridge

    result = asyncio.run(server.read_resource(uri))
    if isinstance(result, list):
        return result
    return list(result)  # type: ignore[arg-type]


def build_shipped_catalog() -> SkillResourceCatalog:
    """The shipped catalog, for a caller that wants the registry without a server."""

    with shipped_skill_tree() as tree:
        return build_skill_catalog(tree)


__all__ = [
    "SKILLS_EXTENSION_ID",
    "SkillsExtension",
    "build_shipped_catalog",
    "build_skills_extension",
    "declared_extensions",
    "index_resource",
    "read_resource_contents",
    "skill_file_resource",
]
