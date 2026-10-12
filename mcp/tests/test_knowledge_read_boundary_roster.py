"""MIK-R74 rule 6: the complete protected-read roster with per-route outcomes.

Split whole from ``test_knowledge_read_boundary.py`` under the Owner line-bound
disposition: every case, assertion and negative control is preserved; only the address
changes. The oracle lives in ``read_boundary_oracle_test_support``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.kernel.primitives.runtime_config import RepositoryScope
from agents_remember.serving._app_common import Projector, _ServingRuntime, stream_events
from agents_remember.serving._app_routes import _state_response, _task_document_response
from agents_remember.serving._app_terminal_routes import _register_terminal_session_routes
from agents_remember.serving.agent_notifier_heartbeat import AgentNotifierHeartbeatStore
from agents_remember.serving.build_info import ServingBuild
from agents_remember.serving.changeset import register_changeset_routes
from agents_remember.serving.conversation.authorization import LocalOperatorAuthorizationResolver
from agents_remember.serving.conversation.runtime import ConversationRuntime, ConversationScope
from agents_remember.serving.events import stream_raw_events
from agents_remember.serving.files import register_files_routes
from agents_remember.serving.harness_capability_catalog import HarnessCapabilityCatalog
from agents_remember.serving.harness_control_api import register_harness_control_routes
from agents_remember.serving.harness_control_client import ControlPlaneClient
from agents_remember.serving.requirements import register_requirements_routes
from agents_remember.serving.review import register_review_routes
from agents_remember.serving.review_summary import register_review_summary_route
from agents_remember.serving.review_trees import register_review_trees_route
from agents_remember.serving.terminal import TerminalHost
from agents_remember.serving.terminal_catalog import TerminalCatalog
from agents_remember.serving.terminal_liveness import (
    TerminalCatalogLivenessConfig,
    TerminalCatalogLivenessSweeper,
    utc_now,
)
from agents_remember.serving.terminal_observer_health import TerminalObserverHealthPublisher
from agents_remember.serving.terminal_paste import TerminalPaster
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from read_boundary_oracle_test_support import _classify, _snapshot
from test_knowledge_review_source_endpoints import build_endpoint_fixture


class _NoLiveSessions:
    """TerminalLivenessHost double: no live sessions, so per-session GETs refuse by name."""

    def has_session(self, tmux_name: str) -> bool:
        del tmux_name
        return False


def test_the_protected_read_roster_names_every_route_with_its_outcome(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the complete protected-read roster: constructor, call, outcome.

    Every read-described route below is constructed exactly as the composition root
    builds it, called once, and its actual outcome recorded -- a served body or the
    legitimate refusal the product declares for that input. Nothing here invents a 200:
    unwired ports refuse 503 by name and unknown repos/scopes/paths refuse by name.
    The cockpit projection boundary lives in the next test. None of the calls writes:
    the closing classification says so.
    """

    endpoints = build_endpoint_fixture(tmp_path / "roster")
    config = dataclasses.replace(
        endpoints.config,
        repositories={
            "fixture-code": RepositoryScope(
                repo_id="fixture-code", path=endpoints.contract.code_repo_path
            )
        },
    )
    app = FastAPI()
    register_requirements_routes(app, config)
    register_files_routes(app, config)
    register_changeset_routes(app, config)
    register_review_routes(app, config, port=None, entries_port=None, source_content_port=None)
    register_review_summary_route(app, None)
    register_review_trees_route(app, None)
    client = TestClient(app)
    roots = {"enclosure": tmp_path / "roster"}
    before = _snapshot(roots)

    context = {"repo": "fixture-code", "master": endpoints.master}
    requirements = client.get("/api/requirements/list", params=context)
    assert requirements.status_code == 400, requirements.text[:400]
    assert requirements.json()["status"] == "bad-context", requirements.text[:400]
    requirement = client.get("/api/requirements/read", params={**context, "path": "no/such.md"})
    assert requirement.status_code == 400, requirement.text[:400]

    forward = client.get("/api/files/onboarding", params={"repo": "fixture-code", "path": ""})
    assert forward.status_code == 200, forward.text[:400]
    assert forward.json()["status"] == "missing", forward.text[:400]
    reverse = client.get(
        "/api/files/onboarding",
        params={"repo": "fixture-code", "path": "", "direction": "reverse"},
    )
    assert reverse.status_code == 200, reverse.text[:400]
    assert reverse.json()["kind"] == "none", reverse.text[:400]

    scoped = client.get("/api/changeset/task", params=context)
    assert scoped.status_code == 404, scoped.text[:400]
    assert scoped.json()["status"] == "not-found", scoped.text[:400]
    leaf = client.get(
        "/api/changeset/task",
        params={**context, "leaf": "no-such-leaf", "mode": "committed"},
    )
    assert leaf.status_code == 404, leaf.text[:400]
    net = client.get(
        "/api/changeset/master", params={"repo": "fixture-code", "master": "no-such-master"}
    )
    assert net.status_code == 200, net.text[:400]
    assert net.json()["leaves"] == [], net.text[:400]

    for path in (
        "/api/review/intent/entries",
        "/api/review/intent/source-content",
        "/api/review/intent/summary",
        "/api/review/trees",
    ):
        unwired = client.get(
            path, params={"repo": "fixture-code", "master": endpoints.master, "leaf": "x"}
        )
        assert unwired.status_code == 503, (path, unwired.text[:400])
        assert unwired.json()["status"] == "unavailable", (path, unwired.text[:400])

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], findings


def test_the_cockpit_boundary_names_its_outcomes_without_a_live_lifetime(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the cockpit projection boundary: idle outcomes and the live edge.

    The two projection handlers are called against a projector with no snapshot and
    raise the declared 503 through the real code path. The raw event river is read
    bounded to its ready marker against an empty observer store. The delta stream is
    attempted against a real projector with no ticking lifetime and yields no frame:
    that timeout is the proved boundary, not a waiver -- the live-projection branches
    need a serving lifetime the admitted suite cannot start. None of this writes.
    """

    endpoints = build_endpoint_fixture(tmp_path / "cockpit")
    roots = {"enclosure": tmp_path / "cockpit"}
    before = _snapshot(roots)

    idle = SimpleNamespace(
        projector=SimpleNamespace(current=lambda: (0, None)), config=endpoints.config
    )
    # A projector with no snapshot is the honest idle lifetime: both handlers raise
    # the declared 503 through the real code path, touching nothing else.
    with pytest.raises(HTTPException) as state_refusal:
        _state_response(cast(Any, idle), None)
    assert state_refusal.value.status_code == 503
    with pytest.raises(HTTPException) as document_refusal:
        _task_document_response(cast(Any, idle), "tasks/fixture-code/m1/sprint.json")
    assert document_refusal.value.status_code == 503

    async def _first_raw_frames() -> list:
        stream = stream_raw_events(endpoints.config, interval=0.01)
        frames = []
        try:
            async with asyncio.timeout(8):
                async for event in stream:
                    frames.append(event.event)
                    if event.event == "ready" or len(frames) > 4:
                        break
        finally:
            await stream.aclose()
        return frames

    assert asyncio.run(_first_raw_frames()) == ["ready"]

    async def _no_delta_without_a_ticking_lifetime() -> None:
        stream = stream_events(Projector(endpoints.config))
        try:
            async with asyncio.timeout(3):
                await anext(stream)
        finally:
            await stream.aclose()

    with pytest.raises(TimeoutError):
        asyncio.run(_no_delta_without_a_ticking_lifetime())

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], findings


def test_terminal_and_harness_gets_read_without_live_actors(
    tmp_path: Path, worktree_services: Any
) -> None:
    """Rule 6 over the five live-surface GETs: real calls, seam-composed lifetimes.

    The terminal bundle composes from an idle host (bound but never touched: no
    session exists to act on), a file catalog and tmp-backed stores; the harness
    bundle composes from the production local resolver, an empty registry and a
    no-session host double. Sessions list empty, harnesses list detected installs,
    and unknown harnesses/sessions refuse by name. Nothing starts: no PTY, no tmux,
    no driver. Both lifetimes live inside the single snapshotted root, so the
    closing classification covers every file either bundle could touch.
    """

    endpoints = build_endpoint_fixture(tmp_path / "live-gets")
    observer = tmp_path / "live-gets" / "observer"
    observer.mkdir(exist_ok=True)
    scope = tmp_path / "live-gets" / "conversation"
    scope.mkdir(exist_ok=True)
    catalog = TerminalCatalog(tmp_path / "live-gets" / "terminal-sessions.json")
    host = TerminalHost()
    serving = _ServingRuntime(
        config=endpoints.config,
        projector=Projector(endpoints.config),
        host=host,
        catalog=catalog,
        paster=TerminalPaster(),
        liveness_clock=utc_now,
        liveness_config=TerminalCatalogLivenessConfig(),
        liveness_sweeper=TerminalCatalogLivenessSweeper(catalog, host),
        build=ServingBuild(version="test", commit=None, booted_at="2026-10-10T00:00:00Z"),
        heartbeat_store=AgentNotifierHeartbeatStore(observer),
        observer_health=TerminalObserverHealthPublisher(observer, utc_now),
        register_inbox_execution_evidence=None,
        interval=1.0,
    )
    app = FastAPI()
    _register_terminal_session_routes(app, serving)
    client = TestClient(app)
    roots = {"enclosure": tmp_path / "live-gets"}
    before = _snapshot(roots)

    sessions = client.get("/api/terminal/sessions")
    assert sessions.status_code == 200, sessions.text[:400]
    assert sessions.json() == {"sessions": []}, sessions.text[:400]
    harnesses = client.get("/api/harnesses")
    assert harnesses.status_code == 200, harnesses.text[:400]
    assert isinstance(harnesses.json().get("harnesses"), list), harnesses.text[:400]

    conversation = ConversationRuntime(
        scope=ConversationScope(workspace_root=scope, coordination_root=scope),
        catalog=TerminalCatalog(scope / "terminal-sessions.json"),
        control_plane=ControlPlaneClient(),
        host=_NoLiveSessions(),
        harness_registry=lambda: [],
        liveness_clock=utc_now,
        liveness_config=TerminalCatalogLivenessConfig(),
        capability_catalog=HarnessCapabilityCatalog(scope),
        authorization=LocalOperatorAuthorizationResolver.for_workspace(scope),
    )
    control = FastAPI()
    register_harness_control_routes(control, conversation)
    control_client = TestClient(control)
    capability = control_client.get("/api/harnesses/no-such-harness/capabilities")
    assert capability.status_code == 404, capability.text[:400]
    assert capability.json()["status"] == "capability-unavailable", capability.text[:400]
    for path in (
        "/api/terminal/no-such-session/capabilities",
        "/api/terminal/no-such-session/submission-authority",
    ):
        missing = control_client.get(path)
        assert missing.status_code == 404, (path, missing.text[:400])
        assert missing.json()["status"] == "unknown-session", (path, missing.text[:400])

    after = _snapshot(roots)
    findings = _classify(
        before,
        after,
        coordination_roots=("enclosure/ar-coordination",),
        reviewed_prefixes=("enclosure/diff",),
    )
    assert findings == [], findings
