"""Composition contract tests for the app-scoped ConversationRuntime (260718-CHATS-L0)."""

from __future__ import annotations

import dataclasses
import inspect
import re
from pathlib import Path
from typing import Annotated, Any

import agents_remember.serving.conversation.authorization as authorization_module
import agents_remember.serving.conversation.dependencies as dependencies_module
import agents_remember.serving.conversation.router as conversation_router_module
import agents_remember.serving.conversation.runtime as runtime_module
import pytest
from agents_remember.errors import ConversationCompositionError
from agents_remember.kernel.harnesses import Harness
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.serving.app import create_app
from agents_remember.serving.conversation import ConversationRuntime, get_conversation_runtime
from agents_remember.serving.conversation.authorization import (
    LocalOperatorAuthorizationResolver,
)
from agents_remember.serving.conversation.router import register_conversation_routes
from agents_remember.serving.conversation.runtime import (
    CONVERSATION_RUNTIME_STATE_KEY,
    ConversationScope,
    conversation_runtime_from_app,
)
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_api import register_harness_control_routes
from agents_remember.serving.projector import ProjectionCadence
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    utc_now,
)
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient


class _NoSessionHost:
    """Minimal TerminalLivenessHost double: no live sessions."""

    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return False


def _empty_registry() -> list[Harness]:
    return []


def _runtime(
    workspace: Path,
    *,
    coordination: Path | None = None,
    catalog: TerminalCatalog | None = None,
    liveness_config: TerminalCatalogLivenessConfig | None = None,
) -> ConversationRuntime:
    return ConversationRuntime(
        scope=ConversationScope(
            workspace_root=workspace,
            coordination_root=coordination if coordination is not None else workspace,
        ),
        catalog=catalog or TerminalCatalog(workspace / "terminal-sessions.json"),
        host=_NoSessionHost(),
        harness_registry=_empty_registry,
        liveness_clock=utc_now,
        liveness_config=liveness_config or TerminalCatalogLivenessConfig(),
        capability_catalog=HarnessCapabilityCatalog(workspace),
        authorization=LocalOperatorAuthorizationResolver.for_workspace(workspace),
    )


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _request(app: FastAPI, *, client: tuple[str, int] | None) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "app": app,
    }
    if client is not None:
        scope["client"] = client
    return Request(scope)


def _add_runtime_probe(app: FastAPI) -> None:
    """A test-owned probe route on the test app, never on the shared child routers."""

    @app.get("/probe-runtime")
    def probe_runtime(
        runtime: Annotated[ConversationRuntime, Depends(get_conversation_runtime)],
    ) -> dict[str, str]:
        return {"workspace": str(runtime.scope.workspace_root)}


def test_production_composition_installs_one_typed_runtime(tmp_path: Path) -> None:
    app = FastAPI()
    catalog = TerminalCatalog(tmp_path / "terminal-sessions.json")
    liveness_config = TerminalCatalogLivenessConfig()
    register_harness_control_routes(
        app,
        _runtime(tmp_path, catalog=catalog, liveness_config=liveness_config),
    )

    runtime = conversation_runtime_from_app(app)
    assert isinstance(runtime, ConversationRuntime)
    assert runtime.scope == ConversationScope(workspace_root=tmp_path, coordination_root=tmp_path)
    assert runtime.catalog is catalog
    assert runtime.harness_registry is _empty_registry
    assert runtime.liveness_clock is utc_now
    assert runtime.liveness_config is liveness_config
    assert isinstance(runtime.capability_catalog, HarnessCapabilityCatalog)
    assert isinstance(runtime.authorization, LocalOperatorAuthorizationResolver)
    # One stable installation: retrieval returns the identical object.
    assert conversation_runtime_from_app(app) is runtime


def test_create_app_installs_runtime_from_live_composition(tmp_path: Path) -> None:
    app = create_app(_config(tmp_path), cadence=ProjectionCadence(interval=100))
    runtime = conversation_runtime_from_app(app)
    assert runtime.scope == ConversationScope(workspace_root=tmp_path, coordination_root=tmp_path)
    assert isinstance(runtime.authorization, LocalOperatorAuthorizationResolver)


def test_duplicate_installation_fails_closed(tmp_path: Path) -> None:
    app = FastAPI()
    register_conversation_routes(app, _runtime(tmp_path))
    with pytest.raises(ConversationCompositionError, match="already installed"):
        register_conversation_routes(app, _runtime(tmp_path))


def test_second_harness_control_registration_fails_closed(tmp_path: Path) -> None:
    app = FastAPI()
    runtime = _runtime(tmp_path)
    register_harness_control_routes(app, runtime)
    with pytest.raises(ConversationCompositionError, match="already installed"):
        register_harness_control_routes(app, runtime)


def test_missing_installation_fails_closed(tmp_path: Path) -> None:
    app = FastAPI()
    with pytest.raises(ConversationCompositionError, match="not installed"):
        conversation_runtime_from_app(app)
    request = _request(app, client=("127.0.0.1", 5000))
    with pytest.raises(ConversationCompositionError, match="not installed"):
        get_conversation_runtime(request)


def test_foreign_state_binding_fails_closed() -> None:
    app = FastAPI()
    setattr(app.state, CONVERSATION_RUNTIME_STATE_KEY, object())
    with pytest.raises(ConversationCompositionError, match="does not hold"):
        conversation_runtime_from_app(app)


def test_missing_authority_fails_at_construction(tmp_path: Path) -> None:
    with pytest.raises(ConversationCompositionError, match="authorization"):
        ConversationRuntime(
            scope=ConversationScope(workspace_root=tmp_path, coordination_root=tmp_path),
            catalog=TerminalCatalog(tmp_path / "terminal-sessions.json"),
            host=_NoSessionHost(),
            harness_registry=_empty_registry,
            liveness_clock=utc_now,
            liveness_config=TerminalCatalogLivenessConfig(),
            capability_catalog=HarnessCapabilityCatalog(tmp_path),
            authorization=None,  # type: ignore[arg-type] - intentional missing-binding proof
        )


def test_runtime_and_scope_are_immutable(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(runtime, "catalog", None)  # noqa: B010 - deliberate frozen-mutation attempt
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(  # noqa: B010 - deliberate frozen-mutation attempt
            runtime.scope, "workspace_root", tmp_path / "elsewhere"
        )


def test_no_import_time_mutable_singleton() -> None:
    for module in (
        runtime_module,
        authorization_module,
        dependencies_module,
        conversation_router_module,
    ):
        assert not any(
            isinstance(value, ConversationRuntime | LocalOperatorAuthorizationResolver)
            for value in vars(module).values()
        ), module.__name__
    assert getattr(FastAPI().state, CONVERSATION_RUNTIME_STATE_KEY, None) is None


def test_child_composition_is_isolated_per_app(tmp_path: Path) -> None:
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    app_a, app_b = FastAPI(), FastAPI()
    register_conversation_routes(app_a, _runtime(workspace_a))
    register_conversation_routes(app_b, _runtime(workspace_b))
    _add_runtime_probe(app_a)
    _add_runtime_probe(app_b)

    with TestClient(app_a) as client_a, TestClient(app_b) as client_b:
        assert client_a.get("/probe-runtime").json() == {"workspace": str(workspace_a)}
        assert client_b.get("/probe-runtime").json() == {"workspace": str(workspace_b)}


def test_production_composition_accepts_no_injected_identity() -> None:
    params = set(inspect.signature(register_harness_control_routes).parameters)
    assert params.isdisjoint(
        {"authorization", "resolver", "principal", "tenant", "identity", "operator"}
    )
    # The production composition mints its own resolver in create_app; no caller ever supplies one.
    assert "LocalOperatorAuthorizationResolver" in inspect.getsource(create_app)


def test_production_modules_have_no_fixture_pty_or_browser_identity_reliance() -> None:
    forbidden = (
        re.compile(r"\bfixtures?\b"),
        re.compile(r"\bpty\b", re.IGNORECASE),
        re.compile(r"\btmux\b"),
        re.compile(r"request\.headers"),
        re.compile(r"scope\[.headers.\]"),
        re.compile(r"x-principal|x-tenant|x-user|x-forwarded-for", re.IGNORECASE),
    )
    for module in (
        runtime_module,
        authorization_module,
        dependencies_module,
        conversation_router_module,
    ):
        source = inspect.getsource(module)
        assert all(pattern.search(source) is None for pattern in forbidden), module.__name__
