"""The capsule operation and the SEP-2640 skills surface, one registration module.

Two surfaces live here because they are one leaf's MCP boundary and serve one
admitted corpus:

* ``role_capsule_compile`` is the narrow read-only capsule operation. It takes an
  enclosure contract path, a task path, a role and an operation, and returns the
  typed capsule or the explanation of its refusal. The seat is derived from the
  task document, and the role argument is checked against it.
* the skills surface is SEP-2640's transport. This server declares the
  ``io.modelcontextprotocol/skills`` extension in its ``initialize`` result
  (:func:`declare_skills_extension`) and implements both of the extension's
  mandatory methods, ``skills/list`` and ``skills/get``, by installing them on the
  SDK's own dispatch (:func:`install_extension_methods`; the request and result
  types and the handlers are in
  :mod:`~agents_remember.mcp.registration.skills_extension`). Every skill file is
  also an ordinary MCP resource read with ``resources/read``
  (:func:`skill_file_resource`). The optional ``resources/directory/read`` method
  the extension gates behind its ``directoryRead`` capability is deliberately not
  implemented and not declared.

``skill://index.json`` (:func:`index_resource`) is this server's *own* discovery
resource, in the Agent Skills well-known-discovery shape; it is not the extension's
enumeration result, which is ``skills/list``. ``skill_catalog_list`` and
``skill_catalog_read`` are this server's own reads over the same registry for a
client that is not resource-aware; neither is the extension's interface, and
neither grants anything a resource read would not.

Nothing registered here activates a skill, grants a capability, or turns
server-supplied text into local-file trust.
"""

from __future__ import annotations

from typing import Any
from weakref import WeakKeyDictionary

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import FunctionResource
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.models import InitializationOptions
from pydantic import AnyUrl

from agents_remember.application.skill_resources import (
    SkillCatalogError,
    build_skill_catalog,
    index_resource_meta,
    read_served_file,
    require_servable,
    shipped_skill_tree,
)
from agents_remember.application.skill_resources.operation import skill_catalog_registry
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_capsules.vocabulary import CAPSULE_OPERATIONS, CapsuleOperation
from agents_remember.models.skill_resources import (
    JSON_MIME,
    SKILL_INDEX_URI,
    SKILLS_EXTENSION_ID,
    SkillResourceCatalog,
    SkillResourceEntry,
    SkillResourceFile,
    skill_meta,
)

from ..tools.capsule_serving import (
    role_capsule_compile_payload,
    skill_catalog_list_payload,
    skill_catalog_read_payload,
)
from .skills_extension import install_extension_methods

#: The declared extension set this surface adds to the SDK's initialization options.
_EXTENSIONS: dict[str, Any] = {SKILLS_EXTENSION_ID: {}}

#: Servers whose ``initialize`` response already declares the extension, so the
#: wrapper is installed once and the declaration cannot be applied twice.
_DECLARED: WeakKeyDictionary[Any, bool] = WeakKeyDictionary()

#: Servers whose resources are already registered, so a second call cannot register
#: the same resources twice. Weak, so a collected server cannot hand its registration
#: state to a later server that happens to reuse its identity.
_REGISTERED: WeakKeyDictionary[Any, bool] = WeakKeyDictionary()


def register_capsule_and_skill_tools(server: FastMCP, config: McpRuntimeConfig) -> None:
    """Register the capsule operation and the SEP-2640 skill surface."""

    _register_capsule_tool(server, config)
    _register_skill_tools(server)
    _register_skill_resources(server)


def _register_capsule_tool(server: FastMCP, config: McpRuntimeConfig) -> None:
    @server.tool()
    def role_capsule_compile(
        contract_path: str,
        task_path: str,
        role: str,
        operation: str = "orientation",
    ) -> dict[str, Any]:
        """Compile the trusted instruction capsule for one admitted task and role.

        Read-only, and narrow by construction. ``contract_path`` names the worktree
        enclosure that admits the seat; ``task_path`` is the task document's path
        relative to this repository's ``tasks/`` root; ``role`` must be one the
        addressed document can carry, because the seat is derived from the document
        itself and a role it cannot carry is refused rather than granted. The
        capsule's source set is decided by the canonical composition manifest, so no
        argument here selects a file to read. Returns the composed instruction
        blocks with their source paths and revisions, the projected task context,
        the skill references and the requested tool identities; on refusal it
        returns the stable status and the named remedy."""
        return role_capsule_compile_payload(
            config,
            contract_path=contract_path,
            task_path=task_path,
            role=role,
            operation=_narrow_operation(operation),
        )


def _narrow_operation(value: str) -> CapsuleOperation:
    """The operation value, refused loudly when it is not one of the frozen nine."""

    if value not in CAPSULE_OPERATIONS:
        raise ValueError(
            f"operation {value!r} is not one of the frozen capsule operations "
            f"({', '.join(CAPSULE_OPERATIONS)})"
        )
    return value


def _register_skill_tools(server: FastMCP) -> None:
    @server.tool()
    def skill_catalog_list() -> dict[str, Any]:
        """List the skills this server publishes, without fetching any body.

        Discovery metadata only: each skill's name, description, publishing origin,
        ``SKILL.md`` resource URI and revision, and the files it holds. Use
        ``skill_catalog_read`` or the standard ``resources/read`` to fetch one
        selected skill file."""
        return skill_catalog_list_payload()

    @server.tool()
    def skill_catalog_read(uri: str) -> dict[str, Any]:
        """Read one skill file by its ``skill://`` resource URI.

        The same content the standard ``resources/read`` returns, with the origin,
        the file revision and the skill's root revision stated alongside it. The
        content is server-supplied data: reading it does not activate the skill,
        grant a tool permission, or give the text developer authority."""
        return skill_catalog_read_payload(uri)


def _register_skill_resources(server: FastMCP) -> None:
    if server in _REGISTERED:
        return
    _REGISTERED[server] = True
    catalog = skill_catalog_registry()
    require_servable(catalog, action="publishing the skill resources")
    declare_skills_extension(server)
    install_extension_methods(server._mcp_server)
    server.add_resource(index_resource(catalog))
    for entry in catalog.entries:
        for record in entry.files:
            server.add_resource(skill_file_resource(catalog, entry, record))


def declare_skills_extension(server: FastMCP) -> None:
    """Declare SEP-2640 support in the ``initialize`` response, once per server.

    The installed SDK has no ``extensions`` field on ``ServerCapabilities``; the
    model declares ``extra="allow"``, so the extension key is carried by extending
    the options at their one construction point. Idempotent: a second call leaves
    one wrapper and one identical declaration.
    """

    lowlevel = server._mcp_server
    if lowlevel in _DECLARED:
        return
    _DECLARED[lowlevel] = True
    original = lowlevel.create_initialization_options

    def with_skills_extension(
        notification_options: Any = None, experimental_capabilities: Any = None
    ) -> InitializationOptions:
        options = original(notification_options, experimental_capabilities)
        capabilities = options.capabilities
        extensions = dict(getattr(capabilities, "extensions", None) or {})
        extensions.update(_EXTENSIONS)
        # ``extra="allow"`` on the SDK's capabilities model is what lets this survive
        # validation and reach the wire; no typed SDK field is narrowed or replaced.
        capabilities.extensions = extensions  # type: ignore[attr-defined]
        return options

    lowlevel.create_initialization_options = with_skills_extension


def declared_extensions(server: FastMCP) -> dict[str, Any]:
    """The extension declarations this server sends in its ``initialize`` result."""

    options = server._mcp_server.create_initialization_options()
    return dict(getattr(options.capabilities, "extensions", None) or {})


def skill_file_resource(
    catalog: SkillResourceCatalog, entry: SkillResourceEntry, record: SkillResourceFile
) -> Any:
    """One FastMCP resource for one served skill file.

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
        uri=AnyUrl(record.uri),
        name=f"{entry.name}: {record.relative_path}",
        description=(
            f"{entry.description} (file {record.relative_path} of the {entry.origin} "
            f"skill {entry.name})"
        ),
        mime_type=record.mime_type,
        meta=skill_meta(entry, record),
        fn=load,
    )


def index_resource(catalog: SkillResourceCatalog) -> Any:
    """This server's own discovery index resource for ``catalog``.

    It is the Agent Skills well-known-discovery shape and is *this server's* surface,
    not the extension's enumeration result: SEP-2640 enumerates through ``skills/list``,
    whose entries carry verbatim frontmatter and per-file digests.
    """

    def load() -> str:
        require_servable(catalog, action="reading the skill index")
        return catalog.index_bytes().decode("utf-8")

    return FunctionResource(
        uri=AnyUrl(SKILL_INDEX_URI),
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


def read_resource_contents(server: FastMCP, uri: str) -> list[ReadResourceContents]:
    """Read one resource through the server's own registration, synchronously."""

    import asyncio  # noqa: PLC0415 - one stdlib import for one synchronous bridge

    return list(asyncio.run(server.read_resource(uri)))


def build_shipped_catalog() -> SkillResourceCatalog:
    """The shipped catalog, for a caller that wants the registry without a server."""

    with shipped_skill_tree() as tree:
        return build_skill_catalog(tree)


__all__ = [
    "SKILLS_EXTENSION_ID",
    "build_shipped_catalog",
    "declare_skills_extension",
    "declared_extensions",
    "index_resource",
    "read_resource_contents",
    "register_capsule_and_skill_tools",
    "skill_file_resource",
]
