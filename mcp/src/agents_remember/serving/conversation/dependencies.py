"""The narrow request-level dependencies for conversation child routers (260718-CHATS-L0).

Child leaves consume the installed :class:`ConversationRuntime` (or derive
their child-owned ports from it) exclusively through ``Depends`` on these two
functions; they never reach into ``app.state`` themselves and never re-bind the
composition. Retrieval fails closed with a typed composition error when the
root composition did not install the runtime.
"""

from __future__ import annotations

from fastapi import Request

from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
)
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    conversation_runtime_from_app,
)


def get_conversation_runtime(request: Request) -> ConversationRuntime:
    """Return the one app-scoped runtime installed by the root composition."""
    return conversation_runtime_from_app(request.app)


def resolve_conversation_authorization(request: Request) -> AuthorizationBinding:
    """Resolve the server-side local operator binding for this request.

    The only request fact consulted is the ASGI TCP peer address; request
    metadata and any browser-supplied principal/tenant claim are never read, so
    identity cannot be forged from the wire. Non-loopback peers fail closed in
    the bound resolver.
    """
    runtime = conversation_runtime_from_app(request.app)
    client = request.client
    return runtime.authorization.resolve(client_host=client.host if client is not None else None)


__all__ = ["get_conversation_runtime", "resolve_conversation_authorization"]
