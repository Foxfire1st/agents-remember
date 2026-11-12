"""Bounded explicit method support for the SEP-2640 extension methods.

SEP-2640 (Final) declares three protocol methods. Every server declaring the
extension implements ``skills/list``, which enumerates the skills a server serves,
and ``skills/get``, which returns one skill's entry by URI; the optional
``resources/directory/read`` is additionally gated behind the ``directoryRead``
capability. The specification's own words, §Backward Compatibility: "This extension
introduces three protocol methods. `skills/list` and `skills/get` are implemented by
every server declaring the extension … The extension introduces no other methods,
message types, or schema changes." §Capability Declaration: "Declaring the
extension itself commits the server to `skills/list` and `skills/get`."

The pinned SDK (``mcp==1.29.1``) has no skills affordance: zero case-insensitive
``skill`` matches in the package, ``ServerCapabilities`` carries no ``extensions``
field, and an unmodelled method is refused with ``-32602`` in
``mcp/shared/session.py``. The packet's own escape hatch for exactly this case is
"the smallest published implementation or **bounded explicit method support**", so
this module registers the two methods against the SDK's own dispatch instead of
declaring a capability the server cannot answer.

**The bound.** Two things are extended and nothing else:

1. the request union a session validates an incoming request against, so
   ``skills/list`` and ``skills/get`` reach a handler instead of ``-32602``; and
2. the result union a response is serialized through, so the handlers' results are
   valid responses.

Both extensions are additive: the SDK's existing request and result types are kept
in the union unchanged, the handlers live in the SDK's own ``request_handlers``
mapping keyed by the SDK's own dispatch key, and the capability declaration is the
one the SDK's ``extra="allow"`` model already carries. No SDK version is moved, no
transport is replaced, and a method this module does not register is still refused
by the SDK exactly as before.

``resources/directory/read`` is deliberately **not** implemented and the
``directoryRead`` capability is deliberately **not** declared, which is the
specification's own gate: "a server that does not declare it never receives the
call."
"""

from __future__ import annotations

from typing import Any, Literal, cast
from weakref import WeakKeyDictionary

from mcp.server.lowlevel.server import LifespanResultT
from mcp.server.lowlevel.server import Server as LowLevelServer
from mcp.server.session import ServerSession
from mcp.shared.exceptions import McpError
from mcp.shared.session import RequestResponder
from mcp.types import (
    INVALID_PARAMS,
    ClientRequestType,
    ErrorData,
    RequestParams,
    ServerResultType,
)
from pydantic import BaseModel, Field, RootModel

from agents_remember.models.skill_resources import RESULT_TYPE_COMPLETE, SkillResourceCatalog

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


class SkillsListRequest(BaseModel):
    method: Literal["skills/list"] = SKILLS_LIST_METHOD
    params: SkillsListParams | None = None


class SkillsGetParams(RequestParams):
    """``skills/get`` params: the ``SKILL.md`` resource URI of the skill asked for."""

    uri: str


class SkillsGetRequest(BaseModel):
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


#: The request union a session validates against, with the two extension methods
#: added to the SDK's own set rather than replacing it.
EXTENDED_REQUEST_TYPE: Any = ClientRequestType | SkillsListRequest | SkillsGetRequest

#: The result union a response is serialized through, extended the same way.
EXTENDED_RESULT_TYPE: Any = ServerResultType | SkillsListResult | SkillsGetResult

_RequestModel = RootModel[EXTENDED_REQUEST_TYPE]
_ResultModel = RootModel[EXTENDED_RESULT_TYPE]

#: The class object of the extended request model, for the dispatcher's type test.
_REQUEST_CLASS: Any = _RequestModel

#: Guard so the session patch is installed once per interpreter.
_INSTALLED = False

#: Servers whose dispatcher already recognises the extension's requests, keyed weakly so
#: a collected server cannot hand its state to a later server that reuses its identity.
_DISPATCH_INSTALLED: WeakKeyDictionary[Any, bool] = WeakKeyDictionary()


def install_extension_methods(server: LowLevelServer) -> None:
    """Register ``skills/list`` and ``skills/get`` on ``server``, once per process.

    Three additive changes, each the narrowest that makes the two methods reachable:

    1. the session's request-validation union gains the two request types;
    2. the server's message dispatcher recognises an extension request as a request
       rather than ignoring it;
    3. the two handlers join the SDK's own ``request_handlers`` map.

    The handlers answer from the same catalog the resources are registered from, so
    the two surfaces cannot disagree about what this server serves.
    """

    _install_request_union()
    _install_dispatcher(server)
    # The map is typed for the SDK's own result union; an extension handler returns this
    # module's result types, which the extended result union serializes. The casts state
    # that widened contract at the one line where it is installed.
    server.request_handlers[SkillsListRequest] = cast(Any, _skills_list_handler)
    server.request_handlers[SkillsGetRequest] = cast(Any, _skills_get_handler)


def _install_request_union() -> None:
    """Let a session validate the extension's two methods, once per process.

    ``BaseSession.__init__`` stores the validation union on the *instance*, and
    ``ServerSession.__init__`` passes the SDK's own ``ClientRequest``, so the union
    is rebound as each session is constructed. Only that one union is replaced, and
    it is replaced by a superset of the SDK's own: every SDK request type still
    validates exactly as before, and every other unmodelled method is still refused
    with the SDK's own ``-32602``.
    """

    global _INSTALLED  # noqa: PLW0603 - one process-wide patch, idempotent by design
    if _INSTALLED:
        return
    original = ServerSession.__init__

    def __init__(self: ServerSession, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        self._receive_request_type = _RequestModel  # type: ignore[assignment]

    ServerSession.__init__ = __init__  # type: ignore[method-assign]
    _INSTALLED = True


def _install_dispatcher(server: LowLevelServer) -> None:
    """Let the server's dispatcher route an extension request to its handler.

    ``Server._handle_message`` recognises a request by matching the SDK's own
    ``ClientRequest`` wrapper. An extension request arrives in this module's wrapper
    instead, so without this the request would be silently dropped — the failure mode
    is a hung call, not an error — and the handler registered below would never run.
    Only the recognised wrapper is widened; notifications and exceptions take the
    SDK's own paths unchanged.
    """

    if server in _DISPATCH_INSTALLED:
        return
    _DISPATCH_INSTALLED[server] = True
    original = server._handle_message

    async def _handle_message(
        message: Any,
        session: ServerSession,
        lifespan_context: LifespanResultT,
        raise_exceptions: bool = False,
    ) -> None:
        if isinstance(message, RequestResponder) and isinstance(message.request, _REQUEST_CLASS):
            with message:
                await server._handle_request(
                    message,
                    message.request.root,
                    session,
                    lifespan_context,
                    raise_exceptions,
                )
            return
        await original(message, session, lifespan_context, raise_exceptions)

    server._handle_message = _handle_message  # type: ignore[method-assign]


def request_union() -> Any:
    """The request union the extension installs, for a caller that wants to assert it."""

    return _RequestModel


def installed() -> bool:
    """Whether the session patch has been installed in this interpreter."""

    return _INSTALLED


def result_union() -> Any:
    """The result union the extension serializes through."""

    return _ResultModel


def _catalog_for_request() -> SkillResourceCatalog:
    from agents_remember.application.skill_resources.operation import (  # noqa: PLC0415
        skill_catalog_registry,
    )

    return skill_catalog_registry()


async def _skills_list_handler(request: SkillsListRequest) -> SkillsListResult:
    """``skills/list``: the full entry set, in one page.

    An empty or partial listing is permitted, and a bounded corpus like this one
    returns everything in one page: the result therefore carries no ``nextCursor``,
    which is the specification's own completion signal. ``request.params.cursor`` is
    accepted and ignored for the same reason — with one page there is no cursor to
    continue from, and refusing a cursor this server never issued would turn a
    conforming client's harmless retry into an error.
    """

    del request
    catalog = _catalog_for_request()
    _require_servable(catalog, method=SKILLS_LIST_METHOD)
    return SkillsListResult(skills=list(catalog.entry_documents()))


async def _skills_get_handler(request: SkillsGetRequest) -> SkillsGetResult:
    """``skills/get``: one entry by URI, listed or not, or ``-32602``.

    The specification requires an entry for every skill the server serves whether or
    not a listing mentions it, and the same error code ``resources/read`` uses for an
    unknown resource when the URI is not one this server serves.
    """

    catalog = _catalog_for_request()
    _require_servable(catalog, method=SKILLS_GET_METHOD)
    uri = request.params.uri
    entry = catalog.skill_for_root_uri(uri)
    if entry is None:
        raise McpError(
            ErrorData(
                code=INVALID_PARAMS,
                message="Invalid request parameters",
                data=f"{uri!r} is not the SKILL.md URI of a skill this server serves",
            )
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
        raise McpError(
            ErrorData(code=INVALID_PARAMS, message="Invalid request parameters", data=str(error))
        ) from error


__all__ = [
    "DIRECTORY_READ_CAPABILITY",
    "EXTENDED_REQUEST_TYPE",
    "EXTENDED_RESULT_TYPE",
    "LIST_CHANGED_CAPABILITY",
    "SKILLS_GET_METHOD",
    "SKILLS_LIST_METHOD",
    "SkillsGetParams",
    "SkillsGetRequest",
    "SkillsGetResult",
    "SkillsListParams",
    "SkillsListRequest",
    "SkillsListResult",
    "install_extension_methods",
    "installed",
    "request_union",
    "result_union",
]
