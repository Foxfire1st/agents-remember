"""Authorization contract tests: server-resolved local operator authority (260718-CHATS-L0)."""

from __future__ import annotations

import inspect
import os
from pathlib import Path
from typing import Any

import pytest
from agents_remember.errors import AuthorityError
from agents_remember.kernel.harnesses import Harness
from agents_remember.serving.conversation import resolve_conversation_authorization
from agents_remember.serving.conversation.authorization import (
    LOCAL_OPERATOR_PRINCIPAL_PREFIX,
    ConversationAuthorizationResolver,
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.models import (
    ActiveConversationRef,
    ActiveCursorBinding,
    AuthorizationBinding,
    ConversationLibraryScope,
    operation_fingerprint,
)
from agents_remember.serving.conversation.runtime import (
    ConversationRuntime,
    ConversationScope,
    install_conversation_runtime,
)
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    utc_now,
)
from fastapi import FastAPI, Request


class _NoSessionHost:
    """Minimal TerminalLivenessHost double: no live sessions."""

    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return False


class _InjectedResolver:
    """Test/application seam double: a foreign principal/tenant identity."""

    def __init__(self, identity: AuthorizationBinding) -> None:
        self._identity = identity

    def resolve(self, *, client_host: str | None) -> AuthorizationBinding:
        del client_host  # the seam double does not enforce loopback; production does
        return self._identity

    def require(self, authorization: AuthorizationBinding) -> None:
        if authorization != self._identity:
            raise AuthorityError("injected resolver rejects the foreign principal")


def _empty_registry() -> list[Harness]:
    return []


def _runtime(
    workspace: Path, *, authorization: ConversationAuthorizationResolver
) -> ConversationRuntime:
    return ConversationRuntime(
        scope=ConversationScope(workspace_root=workspace, coordination_root=workspace),
        catalog=TerminalCatalog(workspace / "terminal-sessions.json"),
        host=_NoSessionHost(),
        harness_registry=_empty_registry,
        liveness_clock=utc_now,
        liveness_config=TerminalCatalogLivenessConfig(),
        capability_catalog=HarnessCapabilityCatalog(workspace),
        authorization=authorization,
    )


def _request(
    app: FastAPI,
    *,
    client: tuple[str, int] | None,
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": headers or [],
        "app": app,
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


def _foreign_binding() -> AuthorizationBinding:
    return AuthorizationBinding(principal_id="remote-user:mallory", tenant_id="/elsewhere")


def test_server_resolves_one_local_operator_workspace_identity(tmp_path: Path) -> None:
    resolver = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    binding = resolver.resolve(client_host="127.0.0.1")
    assert binding.principal_id.startswith(LOCAL_OPERATOR_PRINCIPAL_PREFIX)
    getuid = getattr(os, "getuid", None)
    if getuid is not None:
        assert binding.principal_id == f"{LOCAL_OPERATOR_PRINCIPAL_PREFIX}{getuid()}"
    assert binding.tenant_id == str(tmp_path.resolve())
    # The identity is constant for the resolver's lifetime (immutable composition value).
    assert resolver.resolve(client_host="127.0.0.1") == binding


@pytest.mark.parametrize("peer", ["127.0.0.1", "127.42.0.9", "::1"])
def test_loopback_peers_resolve(tmp_path: Path, peer: str) -> None:
    resolver = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    assert resolver.resolve(client_host=peer).principal_id.startswith(
        LOCAL_OPERATOR_PRINCIPAL_PREFIX
    )


@pytest.mark.parametrize(
    "peer",
    [
        "10.0.0.5",
        "192.168.1.20",
        "172.16.0.1",
        "8.8.8.8",
        "::ffff:10.0.0.5",
        "testclient",
        "localhost",
    ],
)
def test_non_loopback_peers_fail_closed(tmp_path: Path, peer: str) -> None:
    resolver = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    with pytest.raises(AuthorityError, match="loopback-only"):
        resolver.resolve(client_host=peer)


def test_unknown_peer_fails_closed(tmp_path: Path) -> None:
    resolver = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    with pytest.raises(AuthorityError, match="loopback-only"):
        resolver.resolve(client_host=None)


def test_resolution_has_no_principal_or_tenant_input_channel() -> None:
    for resolver_type in (ConversationAuthorizationResolver, LocalOperatorAuthorizationResolver):
        params = inspect.signature(resolver_type.resolve).parameters
        assert list(params) == ["self", "client_host"]
        assert params["client_host"].kind is inspect.Parameter.KEYWORD_ONLY


def test_browser_identity_claims_are_never_read(tmp_path: Path) -> None:
    app = FastAPI()
    install_conversation_runtime(
        app,
        _runtime(
            tmp_path, authorization=LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
        ),
    )
    request = _request(
        app,
        client=("127.0.0.1", 5000),
        headers=[
            (b"x-principal-id", b"remote-user:mallory"),
            (b"x-tenant-id", b"/elsewhere"),
            (b"x-forwarded-for", b"10.0.0.5"),
            (b"authorization", b"Bearer forged"),
        ],
    )
    binding = resolve_conversation_authorization(request)
    assert binding == LocalOperatorAuthorizationResolver.for_workspace(tmp_path).resolve(
        client_host="127.0.0.1"
    )


def test_request_dependency_fails_closed_off_loopback(tmp_path: Path) -> None:
    app = FastAPI()
    install_conversation_runtime(
        app,
        _runtime(
            tmp_path, authorization=LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
        ),
    )
    with pytest.raises(AuthorityError, match="loopback-only"):
        resolve_conversation_authorization(_request(app, client=("10.0.0.5", 5000)))
    with pytest.raises(AuthorityError, match="loopback-only"):
        resolve_conversation_authorization(_request(app, client=None))


def _active_ref() -> ActiveConversationRef:
    return ActiveConversationRef(
        harness_id="claude",
        vendor_conversation_id="conv-1",
        project_scope="project",
        identity_digest="digest",
        ar_session_id="session-1",
        bridge_epoch="epoch-1",
    )


def test_cross_principal_cursor_binding_rejected(tmp_path: Path) -> None:
    resolver = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    own = resolver.resolve(client_host="127.0.0.1")
    own_cursor = ActiveCursorBinding(
        authorization=own,
        purpose="active-page",
        identity=_active_ref(),
        projector_generation="gen-1",
    )
    resolver.require(own_cursor.authorization)

    foreign_cursor = ActiveCursorBinding(
        authorization=_foreign_binding(),
        purpose="active-page",
        identity=_active_ref(),
        projector_generation="gen-1",
    )
    with pytest.raises(AuthorityError, match="does not name"):
        resolver.require(foreign_cursor.authorization)


def test_cross_principal_scope_binding_rejected(tmp_path: Path) -> None:
    resolver = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    own_scope = ConversationLibraryScope(
        authorization=resolver.resolve(client_host="127.0.0.1"),
        harness_id="codex",
        canonical_project_scope=str(tmp_path),
        query_digest="digest",
    )
    resolver.require(own_scope.authorization)

    foreign_scope = ConversationLibraryScope(
        authorization=_foreign_binding(),
        harness_id="codex",
        canonical_project_scope=str(tmp_path),
        query_digest="digest",
    )
    with pytest.raises(AuthorityError, match="does not name"):
        resolver.require(foreign_scope.authorization)


def test_cross_principal_operations_have_distinct_fingerprints(tmp_path: Path) -> None:
    resolver = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    own = resolver.resolve(client_host="127.0.0.1")
    foreign = _foreign_binding()
    payload = {"session": "session-1", "mode": "immediate"}
    assert operation_fingerprint("interrupt", own, payload) != operation_fingerprint(
        "interrupt", foreign, payload
    )
    with pytest.raises(AuthorityError, match="does not name"):
        resolver.require(foreign)


def test_injected_resolver_proves_cross_principal_rejection(tmp_path: Path) -> None:
    production = LocalOperatorAuthorizationResolver.for_workspace(tmp_path)
    injected_identity = AuthorizationBinding(
        principal_id="injected-principal:test", tenant_id="injected-tenant:test"
    )
    injected = _InjectedResolver(injected_identity)
    app = FastAPI()
    install_conversation_runtime(app, _runtime(tmp_path, authorization=injected))

    # The injected seam drives request-level resolution (the test/application seam).
    request = _request(app, client=("127.0.0.1", 5000))
    assert resolve_conversation_authorization(request) == injected_identity

    # Cross-principal rejection holds in both directions; same-principal is accepted.
    production_binding = production.resolve(client_host="127.0.0.1")
    with pytest.raises(AuthorityError):
        injected.require(production_binding)
    with pytest.raises(AuthorityError):
        production.require(injected_identity)
    injected.require(injected_identity)
    production.require(production_binding)
