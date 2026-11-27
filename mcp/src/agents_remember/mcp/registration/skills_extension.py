"""SEP-2640 skills extension methods declared through the MCP 2.x Extension API.

SEP-2640 (Final) declares three protocol methods. Every server declaring the
``io.modelcontextprotocol/skills`` extension implements ``skills/list`` (which
enumerates the skills the server serves) and ``skills/get`` (which returns one
skill's entry by URI). The optional ``resources/directory/read`` method gated
behind ``directoryRead`` is deliberately not implemented and not declared.

The SDK's SEP-2133 ``Extension`` interface (``MethodBinding`` and
``ResourceBinding``, passed at ``MCPServer(extensions=[...])`` construction)
registers these methods and resources per server without mutating any SDK class
process-wide.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from mcp.server.context import CallNext, HandlerResult, ServerMiddleware, ServerRequestContext
from mcp.server.extension import Extension, MethodBinding, ResourceBinding
from mcp.shared.exceptions import MCPError
from mcp.types import INVALID_PARAMS, Request, RequestParams
from pydantic import BaseModel, Field

from agents_remember.models.skill_resources import (
    RESULT_TYPE_COMPLETE,
    SKILLS_EXTENSION_ID,
    SkillResourceCatalog,
)

#: The optional capability SEP-2640 gates ``resources/directory/read`` behind. Not
#: declared: this server publishes concrete resources, so it never receives the call.
DIRECTORY_READ_CAPABILITY = "directoryRead"

#: The optional capability a server may declare to offer per-skill enable/disable.
LIST_CHANGED_CAPABILITY = "listChanged"

SKILLS_LIST_METHOD = "skills/list"
SKILLS_GET_METHOD = "skills/get"


class SkillsListParams(RequestParams):
    """``skills/list`` params: an optional pagination cursor, per the specification."""

    cursor: str | None = None


class SkillsListRequest(Request[SkillsListParams | None, Literal["skills/list"]]):
    method: Literal["skills/list"] = SKILLS_LIST_METHOD
    params: SkillsListParams | None = None


class SkillsGetParams(RequestParams):
    """``skills/get`` params: the ``SKILL.md`` resource URI of the skill asked for."""

    uri: str


class SkillsGetRequest(Request[SkillsGetParams, Literal["skills/get"]]):
    method: Literal["skills/get"] = SKILLS_GET_METHOD
    params: SkillsGetParams


class SkillsListResult(BaseModel):
    """The ``skills/list`` result: one full entry per skill, in stable order."""

    resultType: Literal["complete"] = RESULT_TYPE_COMPLETE
    skills: list[dict[str, Any]] = Field(default_factory=list)
    nextCursor: str | None = None
    ttlMs: int | None = None
    cacheScope: str | None = None


class SkillsGetResult(BaseModel):
    """The ``skills/get`` result: exactly one skill entry."""

    resultType: Literal["complete"] = RESULT_TYPE_COMPLETE
    skill: dict[str, Any]


def _catalog_for_request() -> SkillResourceCatalog:
    from agents_remember.application.skill_resources.operation import (  # noqa: PLC0415
        skill_catalog_registry,
    )

    return skill_catalog_registry()


async def _skills_list_handler(
    _ctx: ServerRequestContext[Any, Any], _params: SkillsListParams
) -> SkillsListResult:
    """``skills/list``: the full entry set, in one page.

    A bounded corpus returns everything in one page, so ``nextCursor`` stays
    absent and any supplied cursor is accepted and ignored.
    """

    catalog = _catalog_for_request()
    _require_servable(catalog, method=SKILLS_LIST_METHOD)
    return SkillsListResult(skills=list(catalog.entry_documents()))


async def _skills_get_handler(
    _ctx: ServerRequestContext[Any, Any], params: SkillsGetParams
) -> SkillsGetResult:
    """``skills/get``: one entry by URI, listed or not, or ``-32602``."""

    catalog = _catalog_for_request()
    _require_servable(catalog, method=SKILLS_GET_METHOD)
    uri = params.uri
    entry = catalog.skill_for_root_uri(uri)
    if entry is None:
        raise MCPError(
            code=INVALID_PARAMS,
            message="Invalid request parameters",
            data=f"{uri!r} is not the SKILL.md URI of a skill this server serves",
        )
    return SkillsGetResult(skill=entry.entry_document())


def _require_servable(catalog: SkillResourceCatalog, *, method: str) -> None:
    from agents_remember.application.skill_resources.catalog import (  # noqa: PLC0415
        SkillCatalogError,
        require_servable,
    )

    try:
        require_servable(catalog, action=f"answering {method}")
    except SkillCatalogError as error:
        raise MCPError(
            code=INVALID_PARAMS,
            message="Invalid request parameters",
            data=str(error),
        ) from error


class SkillsExtension(Extension):
    """SEP-2640 skills extension: capability ad, ``skills/list``/``skills/get``, and resources."""

    identifier = SKILLS_EXTENSION_ID

    def __init__(self, resource_bindings: Sequence[ResourceBinding] = ()) -> None:
        self._resource_bindings = tuple(resource_bindings)

    def settings(self) -> dict[str, Any]:
        return {}

    def resources(self) -> Sequence[ResourceBinding]:
        return self._resource_bindings

    def methods(self) -> Sequence[MethodBinding]:
        return (
            MethodBinding(SKILLS_LIST_METHOD, SkillsListParams, _skills_list_handler),
            MethodBinding(SKILLS_GET_METHOD, SkillsGetParams, _skills_get_handler),
        )


def preserve_handshake_extensions(
    extensions: Sequence[Extension],
) -> ServerMiddleware[Any]:
    """Keep start-tree ``capabilities`` fields on pre-2026 ``initialize`` responses.

    ``mcp 2.3.0`` validates handshake-era ``initialize`` results against the
    pre-2026 ``ServerCapabilities`` schema (``extra="ignore"``), which strips
    ``capabilities.extensions`` and omits the empty ``experimental`` mapping
    before bytes reach handshake clients such as Claude Code, Codex, and Paseo.
    Wrapping ``initialize`` via the SDK's documented ``middleware`` hook restores
    both fields without patching any SDK class.
    """

    declared = {extension.identifier: extension.settings() for extension in extensions}

    async def middleware(ctx: ServerRequestContext[Any, Any], call_next: CallNext) -> HandlerResult:
        result = await call_next(ctx)
        if ctx.method != "initialize" or not isinstance(result, dict):
            return result
        capabilities = result.get("capabilities")
        if not isinstance(capabilities, dict):
            return result
        updated = dict(capabilities)
        if declared and "extensions" not in updated:
            updated["extensions"] = dict(declared)
        if "experimental" not in updated:
            updated["experimental"] = {}
        if updated != capabilities:
            return {**result, "capabilities": updated}
        return result

    return middleware


__all__ = [
    "DIRECTORY_READ_CAPABILITY",
    "LIST_CHANGED_CAPABILITY",
    "SKILLS_GET_METHOD",
    "SKILLS_LIST_METHOD",
    "SkillsExtension",
    "SkillsGetParams",
    "SkillsGetRequest",
    "SkillsGetResult",
    "SkillsListParams",
    "SkillsListRequest",
    "SkillsListResult",
    "preserve_handshake_extensions",
]
