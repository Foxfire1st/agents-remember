"""Server-resolved local single-user operator authorization (260718-CHATS-L0).

Binding ruling (leaf R4 and the reviewed runtime-composition design delta):

- the production resolver represents ONE local operator/workspace authority; it
  does not claim that identity is authenticated;
- identity is resolved on the server from the OS and the canonical workspace
  scope, never accepted from a browser-supplied principal/tenant field;
- non-loopback serving is unsupported and fails closed: a request whose TCP
  peer is not a loopback address is never mapped onto the local identity;
- injected resolvers exist only as a test/application seam and must prove that
  cursor, operation, and scope bindings reject cross-principal reuse;
- any remote or multi-user requirement invalidates this local ruling and
  requires a separately designed authentication subsystem, not a fallback here.
"""

from __future__ import annotations

import getpass
import os
from dataclasses import dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Protocol

from agents_remember.errors import AuthorityError
from agents_remember.models.conversations.identity import (
    AuthorizationBinding,
)

LOCAL_OPERATOR_PRINCIPAL_PREFIX = "local-operator:"


class ConversationAuthorizationResolver(Protocol):
    """The explicit authorization seam every :class:`ConversationRuntime` binds.

    ``resolve`` mints the server-resolved operator binding for one request or
    fails closed by raising. Its only input is the TCP peer address; no
    principal or tenant value may enter resolution, so a browser identity claim
    has no channel to ride. ``require`` verifies an externally supplied binding
    (cursor, operation, or scope) against this resolver's exact identity and
    rejects anything else — the cross-principal rejection contract.
    """

    def resolve(self, *, client_host: str | None) -> AuthorizationBinding: ...

    def require(self, authorization: AuthorizationBinding) -> None: ...


def _is_loopback_peer(client_host: str) -> bool:
    """Only a literal loopback IP peer is local; unresolved names fail closed."""
    try:
        return ip_address(client_host).is_loopback
    except ValueError:
        return False


def _local_operator_principal() -> str:
    """The OS-resolved local operator id; never an environment or network claim."""
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        return f"{LOCAL_OPERATOR_PRINCIPAL_PREFIX}{getuid()}"
    try:
        return f"{LOCAL_OPERATOR_PRINCIPAL_PREFIX}{getpass.getuser()}"
    except (OSError, KeyError) as error:
        raise AuthorityError(
            "cannot resolve the local operator identity on this platform"
        ) from error


@dataclass(frozen=True)
class LocalOperatorAuthorizationResolver:
    """The production resolver: one local operator bound to one canonical workspace.

    The identity is resolved once at composition from the OS user and the
    resolved workspace root. Request-time resolution then only proves the
    request arrived over loopback; there is no identity input to forge.
    """

    _identity: AuthorizationBinding

    @classmethod
    def for_workspace(cls, workspace_root: Path) -> LocalOperatorAuthorizationResolver:
        """Bind the server-resolved local operator to the canonical workspace scope."""
        return cls(
            _identity=AuthorizationBinding(
                principal_id=_local_operator_principal(),
                tenant_id=str(workspace_root.resolve()),
            )
        )

    def resolve(self, *, client_host: str | None) -> AuthorizationBinding:
        """Return the local operator binding for a loopback peer; fail closed otherwise."""
        if client_host is None or not _is_loopback_peer(client_host):
            raise AuthorityError(
                "conversation authorization is loopback-only: refusing to map a "
                "non-loopback or unknown peer onto the local operator identity"
            )
        return self._identity

    def require(self, authorization: AuthorizationBinding) -> None:
        """Reject any binding that does not name this exact local operator/workspace."""
        if authorization != self._identity:
            raise AuthorityError(
                "conversation binding does not name the server-resolved local "
                "operator/workspace authority"
            )


__all__ = [
    "LOCAL_OPERATOR_PRINCIPAL_PREFIX",
    "ConversationAuthorizationResolver",
    "LocalOperatorAuthorizationResolver",
]
