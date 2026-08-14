"""Conformance tests for the served state contract (``serving.served_state``).

The sibling of ``test_tool_response_conformance.py``, for the surface that had no
equivalent: nothing anywhere validated ``/api/state``, the SSE ``snapshot`` event, or the
projection *as served*. Both keys of the serve-time tail -- ``servingBuild`` and
``agentNotifierHeartbeat`` (and its legacy ``supervisorHeartbeat`` alias during the rename
window) -- were injected into the dumped projection with nothing declaring
them, so the emitted body did not validate against any model, ``WorkspaceProjection``
(``extra="forbid"``) included.

These tests drive the REAL route and the real SSE generator and validate what comes back
against :class:`ServedWorkspaceProjection`, and they pin the three shapes the assembly is
allowed to take: the 200 body carries the tail, the 304 branch carries no body at all, and
a ``delta`` event is a bare projection node -- the asymmetry that stops the tail from being
a projection field.

They also drive them over a coordination root that has something in it. Built over an empty
temp directory -- which is what this file used to do -- ``lifecycles``, ``enclosures``,
``engineProcesses`` and ``providers`` all came back as ``[]``, so the 200 body validated
against ``ServedWorkspaceProjection`` without a single projection node ever being
constructed: the assertions covered the serve-time tail and the top-level key set, and drift
*inside* the dump could not be caught. :func:`_populate` writes the three inputs that make
those collections real -- two leaf enclosure contracts at different lifecycle positions, an
observer event log, and a provider snapshot -- and :meth:`_assert_populated` refuses a body
whose collections are empty, so a fixture that silently stops seeding cannot pass either.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.kernel.agentic_settings import agentic_settings_path
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.observer.events import Event
from agents_remember.observer.projection import (
    LifecycleProjection,
    WorkspaceProjection,
)
from agents_remember.observer.store import EventStore
from agents_remember.observer.ulid import new_ulid
from agents_remember.providers.current_state import current_state_path
from agents_remember.serving.agent_notifier_heartbeat import (
    AgentNotifierHeartbeatPayload,
    AgentNotifierHeartbeatStore,
)
from agents_remember.serving.app import create_app, stream_events
from agents_remember.serving.build_info import ServingBuild
from agents_remember.serving.delta import DeltaEvent
from agents_remember.serving.projections.paths import observer_root
from agents_remember.serving.projections.projection_store import write_projection
from agents_remember.serving.projector import ProjectionCadence, Projector
from agents_remember.serving.served_state import (
    SERVED_TAIL_FIELDS,
    ServedWorkspaceProjection,
    served_state_tail,
)
from agents_remember.worktrees.worktree_contract import (
    ContractCells,
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    amend_contract,
    default_contract,
    write_contract,
)
from pydantic import ValidationError

_TS = "2026-06-14T10:00:00Z"
_REPO = "repo-served"
# The two leaf enclosures the fixture writes, at different lifecycle positions so the
# projection has to reduce more than one shape: one still working, one landed and reclaimed.
_LEAVES = ("served-open", "served-landed")


def _config(tmp: Path) -> McpRuntimeConfig:
    """The served config. No ``providers`` entry on purpose.

    ``read_providers`` projects the ``current.json`` snapshot on disk, not the configured
    scopes, so the fixture reaches the provider surface by writing that file. Declaring a
    scope here would instead make the projection tick try to *refresh* it against a real
    provider runtime, which fails and degrades to the same snapshot -- the same served bytes,
    with a stack trace in the log for a collaborator this file is not testing.
    """
    return McpRuntimeConfig(
        config_path=tmp / "settings.json",
        coordination_root=tmp,
        workspace_root=tmp,
        transcript_root=tmp / "logs" / "mcp",
    )


def _write_enclosure(root: Path, worktree_name: str, *, landed: bool) -> None:
    """One leaf enclosure contract on disk, exactly as ``worktree_start`` would write it."""
    contract = default_contract(
        ContractTask(
            name="served-task",
            repo_name=_REPO,
            coordination_root=root,
            workflow_kind="light-task",
            memory_mode="disabled",
        ),
        leaf=LeafIdentity(worktree_name=worktree_name, lifecycle_id="LC1"),
        code=RepoBranchPlan(
            repo_path=root / _REPO,
            source_branch="main",
            work_branch=f"ar/{worktree_name}",
            base_commit="a" * 40,
        ),
    )
    if landed:
        contract = amend_contract(
            contract,
            ContractCells(
                human_review_status="approved",
                closeout_status="completed",
                integration_status="completed",
                cleanup="completed",
            ),
        )
    write_contract(contract.contract_path, contract)


def _populate(root: Path) -> None:
    """Give the projection something to project: enclosures, a lifecycle, and a provider.

    Without these the served body is a valid ``ServedWorkspaceProjection`` whose every node
    collection is empty, which validates whatever the projection models happen to say.
    """
    for worktree_name in _LEAVES:
        _write_enclosure(root, worktree_name, landed=worktree_name.endswith("landed"))
    EventStore(observer_root(_config(root))).append(
        Event(
            id=new_ulid(),
            ts=_TS,
            kind="lifecycle.started",
            trust="declared",
            actor="model",
            lifecycleId="LC1",
            data={"fleeting": False, "phase": "build"},
        )
    )
    provider_state = current_state_path(_config(root))
    provider_state.parent.mkdir(parents=True, exist_ok=True)
    provider_state.write_text(
        json.dumps(
            {
                "version": 1,
                "kind": "provider-current-state",
                "state": "ready",
                "ok": True,
                "checkedAt": _TS,
                "providers": {
                    "codegraphcontext-code": {
                        "id": "codegraphcontext-code",
                        "state": "ready",
                        "ok": True,
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _lifecycle(ident: str) -> LifecycleProjection:
    return LifecycleProjection(
        id=ident,
        state="running",
        phase="build",
        fleeting=False,
        startedAt=_TS,
        lastEventTs=_TS,
    )


def _projection(*lifecycles: LifecycleProjection) -> WorkspaceProjection:
    return WorkspaceProjection(generatedAt=_TS, lifecycles=list(lifecycles))


def _build() -> ServingBuild:
    return ServingBuild(
        version="9.9.9",
        commit="abc1234",
        booted_at="2026-07-07T05:00:00Z",
        dashboard_build="dashboard-123",
        dirty=True,
    )


def _heartbeat() -> AgentNotifierHeartbeatPayload:
    return AgentNotifierHeartbeatPayload(
        lastTickAt="2026-07-07T04:00:00Z",
        ageSeconds=3600.0,
        staleCutoffSeconds=60.0,
        stale=True,
        pendingInboxCount=2,
        redeliverableInboxCount=1,
        lastSweepDurationSeconds=0.25,
    )


class ServedStateTailTests(unittest.TestCase):
    """The assembled tail is exactly the served model's extension over the projection."""

    def test_tail_keys_are_the_declared_extension_over_the_projection(self) -> None:
        declared = set(ServedWorkspaceProjection.model_fields) - set(
            WorkspaceProjection.model_fields
        )
        self.assertEqual(declared, set(SERVED_TAIL_FIELDS))
        tail = served_state_tail(build=_build(), heartbeat=_heartbeat())
        self.assertEqual(set(tail), declared)

    def test_absent_halves_contribute_no_keys(self) -> None:
        # ``stream_events`` may be driven with neither (the unit-test path) or with only
        # one; a missing half is a missing key, never a null placeholder.
        self.assertEqual(served_state_tail(build=None, heartbeat=None), {})
        self.assertEqual(set(served_state_tail(build=_build(), heartbeat=None)), {"servingBuild"})
        self.assertEqual(
            set(served_state_tail(build=None, heartbeat=_heartbeat())),
            {"agentNotifierHeartbeat", "supervisorHeartbeat"},
        )

    def test_the_two_halves_serialize_under_opposite_null_rules(self) -> None:
        # The build stamp OMITS what it could not prove; the heartbeat REPORTS a never-ticked
        # agent-notifier as an explicit null. One shared ``exclude_none`` dump cannot do both,
        # which is why ``served_state_tail`` is two dumps.
        unstampable = ServingBuild(version="9.9.9", commit=None, booted_at="2026-07-07T05:00:00Z")
        never_ticked = AgentNotifierHeartbeatPayload(staleCutoffSeconds=60.0, stale=True)
        tail = served_state_tail(build=unstampable, heartbeat=never_ticked)
        self.assertNotIn("commit", tail["servingBuild"])
        self.assertNotIn("dirty", tail["servingBuild"])
        # Both the current key and the legacy alias carry the same payload during the window.
        self.assertEqual(tail["agentNotifierHeartbeat"], tail["supervisorHeartbeat"])
        self.assertIsNone(tail["agentNotifierHeartbeat"]["lastTickAt"])
        self.assertIsNone(tail["agentNotifierHeartbeat"]["ageSeconds"])

    def test_the_tail_is_json_native(self) -> None:
        # It is merged into a dict handed to ``JSONResponse``/``ServerSentEvent``, so it must
        # already be JSON -- a pydantic model in there would only fail at encode time.
        json.dumps(served_state_tail(build=_build(), heartbeat=_heartbeat()))

    def test_serving_only_fields_stay_out_of_the_persisted_projection(self) -> None:
        # The second consumer: ``latest-state.json`` is a ``WorkspaceProjection`` artifact.
        # Declaring the tail on the projection would have put serve-time facts into it.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_projection(root, _projection(_lifecycle("L1")))
            persisted = json.loads((root / "latest-state.json").read_text(encoding="utf-8"))
        self.assertEqual(set(persisted) & set(SERVED_TAIL_FIELDS), set())


class ServedStateRouteConformanceTests(unittest.TestCase):
    """``/api/state``, for real: the 200 body validates, the 304 branch carries none."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        # The app's own agent-notifier loop ticks the heartbeat on startup, which would make the
        # served age whatever the sweep last wrote. Disabled, the row is entirely this test's
        # to write -- the route still reads and serves it exactly as in production.
        settings = agentic_settings_path(self.tmp)
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(
            json.dumps({"orchestration": {"agentNotifier": {"enabled": False}}}), encoding="utf-8"
        )
        _populate(self.tmp)

    def _client(self) -> TestClient:
        # interval=100: prime publishes once and the loop never ticks again, so the ETag is
        # stable for the 304 leg.
        return TestClient(create_app(_config(self.tmp), cadence=ProjectionCadence(interval=100)))

    def _tick_agent_notifier(self, *, age: timedelta) -> None:
        AgentNotifierHeartbeatStore(observer_root(_config(self.tmp))).tick(
            now=datetime.now(UTC) - age
        )

    def _assert_populated(self, served: ServedWorkspaceProjection) -> None:
        """The projection inside the served body is a real one, not an empty scaffold.

        Every assertion in this file about the served *shape* is worth exactly as much as the
        projection it was made over. Empty collections validate against any node model at all,
        which is how a file that drives the real route end to end could still have covered
        only the tail and the top-level keys.
        """
        self.assertEqual({node.id for node in served.lifecycles}, {"LC1"})
        self.assertEqual(
            {Path(node.enclosure).parent.name for node in served.enclosures}, set(_LEAVES)
        )
        self.assertEqual({node.repoName for node in served.enclosures}, {_REPO})
        # The landed leaf and the open one reduce to different lifecycle positions, so this
        # would fail on a projection that collapsed the contract's state cells.
        self.assertEqual({node.cleanup for node in served.enclosures}, {"pending", "completed"})
        # The Engine Room admits only worktree groups whose lifecycle is still live, so the
        # landed leaf is correctly absent -- asserting the id keeps that a statement about
        # admission rather than a count that any non-empty list would satisfy.
        self.assertEqual(
            {Path(node.enclosure).parent.name for node in served.analytics.engineProcesses},
            {"served-open"},
        )
        self.assertIn("codegraphcontext-code", {node.id for node in served.providers})

    def test_state_body_validates_against_the_served_model(self) -> None:
        self._tick_agent_notifier(age=timedelta(hours=6))
        with self._client() as client:
            response = client.get("/api/state")
        self.assertEqual(response.status_code, 200)
        body: dict[str, Any] = response.json()
        # (a) It IS a served projection.
        served = ServedWorkspaceProjection.model_validate(body)
        self.assertIsNotNone(served.servingBuild)
        assert served.agentNotifierHeartbeat is not None
        self.assertTrue(served.agentNotifierHeartbeat.stale)  # six hours past a 60s cutoff
        # The legacy alias is present and identical during the rename window.
        self.assertEqual(body["supervisorHeartbeat"], body["agentNotifierHeartbeat"])
        # (b) It is NOT a bare projection -- the whole reason the served model exists. This
        # is the assertion that used to be unmakeable, because nothing declared the tail.
        with self.assertRaises(ValidationError):
            WorkspaceProjection.model_validate(body)
        # (c) It carries no key beyond what the served model declares.
        self.assertEqual(set(body) - set(ServedWorkspaceProjection.model_fields), set())
        # (d) ...and (a) validated a projection with nodes in it.
        self._assert_populated(served)

    def test_a_never_ticked_agent_notifier_still_serves_a_valid_body(self) -> None:
        # No heartbeat row at all: the nulls are reported, not omitted, and still validate.
        with self._client() as client:
            body = client.get("/api/state").json()
        served = ServedWorkspaceProjection.model_validate(body)
        assert served.agentNotifierHeartbeat is not None
        self.assertIsNone(served.agentNotifierHeartbeat.lastTickAt)
        self.assertTrue(served.agentNotifierHeartbeat.stale)  # never ticked reads as stale
        self.assertIn("lastTickAt", body["agentNotifierHeartbeat"])  # reported, not dropped
        self.assertEqual(body["supervisorHeartbeat"], body["agentNotifierHeartbeat"])

    def test_the_304_branch_serves_the_etag_and_no_body(self) -> None:
        # The change gate must survive the tail being declared: the heartbeat is volatile and
        # deliberately outside the content revision, so an unchanged projection still 304s
        # even though its age moved.
        self._tick_agent_notifier(age=timedelta(hours=6))
        with self._client() as client:
            first = client.get("/api/state")
            etag = first.headers["etag"]
            self._tick_agent_notifier(age=timedelta(hours=9))  # the volatile half moves...
            cached = client.get("/api/state", headers={"If-None-Match": etag})
        self.assertEqual(cached.status_code, 304)
        self.assertEqual(cached.headers["etag"], etag)  # ...and the revision does not
        self.assertEqual(cached.content, b"")


class ServedSnapshotConformanceTests(unittest.IsolatedAsyncioTestCase):
    """The SSE side: ``snapshot`` is a served projection, ``delta`` is a bare node."""

    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.tmp = Path(self._dir.name)
        self.addCleanup(self._dir.cleanup)
        _populate(self.tmp)

    async def test_snapshot_validates_and_delta_carries_no_tail(self) -> None:
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        await projector.prime()
        gen = stream_events(projector, build=_build(), agent_notifier_heartbeat=_heartbeat())
        try:
            snapshot = await asyncio.wait_for(gen.__anext__(), timeout=1)
            self.assertEqual(snapshot.event, "snapshot")
            assert isinstance(snapshot.data, dict)
            served = ServedWorkspaceProjection.model_validate(snapshot.data)
            self.assertIsNotNone(served.servingBuild)
            self.assertIsNotNone(served.agentNotifierHeartbeat)
            self.assertEqual(
                set(snapshot.data) - set(ServedWorkspaceProjection.model_fields), set()
            )
            # The SSE snapshot carries the same populated projection the route serves, so
            # this leg is measuring a real dump too.
            self.assertEqual({node.id for node in served.lifecycles}, {"LC1"})
            self.assertEqual(len(served.enclosures), len(_LEAVES))

            pending = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.02)
            projector._broadcast((1, DeltaEvent("lifecycle", _lifecycle("L1"))))
            delta = await asyncio.wait_for(pending, timeout=1)
        finally:
            await gen.aclose()
        # A delta is one projection node: it is not a state body, so the whole-workspace
        # tail has nothing to be a field of and must not appear on it.
        self.assertEqual(delta.event, "lifecycle")
        assert isinstance(delta.data, dict)
        self.assertEqual(set(delta.data) & set(SERVED_TAIL_FIELDS), set())
        LifecycleProjection.model_validate(delta.data)

    async def test_a_snapshot_without_a_tail_is_still_a_valid_served_body(self) -> None:
        # Both tail fields are optional on the served model precisely because this path
        # exists (``stream_events`` driven with neither collaborator).
        projector = Projector(_config(self.tmp), cadence=ProjectionCadence(interval=100))
        await projector.prime()
        gen = stream_events(projector)
        try:
            snapshot = await asyncio.wait_for(gen.__anext__(), timeout=1)
        finally:
            await gen.aclose()
        assert isinstance(snapshot.data, dict)
        self.assertEqual(set(snapshot.data) & set(SERVED_TAIL_FIELDS), set())
        served = ServedWorkspaceProjection.model_validate(snapshot.data)
        self.assertIsNone(served.servingBuild)
        self.assertIsNone(served.agentNotifierHeartbeat)


if __name__ == "__main__":
    unittest.main()
