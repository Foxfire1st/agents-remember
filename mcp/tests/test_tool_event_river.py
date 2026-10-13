"""Tests for the MIK-R114 Event River feed on the host path.

Every tool call of an agent is written to the workspace river from the agent's
first call, with the caller and the facts of the answer and nothing else. The
stream deletes nothing, and the writer keeps the river bounded with the stream
offset kept right.

The packet's two-load live proof lives beside this file in
mcp/tests/l114_e4_river_scale.py: four concurrent writers with a stream
connected during writing and cuts at 20,000 and 200,000 total events, with its
retained result JSON bound in the successor bundle manifest.

Each case below protects one distinct behavior or failure of that feed; see
``docs/design/python-pytest-bootstrap.md`` for the case policy.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.application.agent_binding import AgentBinding
from agents_remember.application.runtime.startup import initialize_mcp_application
from agents_remember.application.tool_answer_log import tool_call_context
from agents_remember.application.tool_caller import (
    derive_harness,
    snapshot_tool_caller,
)
from agents_remember.application.tool_response import complete_tool_response
from agents_remember.cli import paseo_role_tools
from agents_remember.cli.paseo_launch import StartingAgent
from agents_remember.cli.paseo_role_tools import _Recipient
from agents_remember.kernel.primitives.observer_paths import observer_logs_root
from agents_remember.kernel.primitives.runtime_config import (
    McpRuntimeConfig,
)
from agents_remember.mcp.tools import role_agents
from agents_remember.models.role_agents import RoleMessageCall
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer import (
    AmbientLifecycle,
    EventStore,
    install_ambient,
    reset_ambient,
)
from agents_remember.observer import event_retention as retention_module
from agents_remember.observer.event_retention import (
    WORKSPACE_EVENT_MAX_BYTES,
    WORKSPACE_EVENT_TRIM_TO_BYTES,
    RiverSizeBound,
    admit_workspace_river_row,
    compact_workspace_river,
    prune_expired_lifecycle_event_logs,
)
from agents_remember.observer.events import Event
from agents_remember.observer.store import workspace_base_offset
from agents_remember.observer.tool_events import (
    TOOL_EVENT_VALUE_MAX_CHARS,
    ToolCaller,
    ToolEventWriter,
    build_tool_event,
    call_target_role,
    install_tool_writer,
    reset_tool_writer,
)
from agents_remember.serving.events import (
    last_event_ts,
    read_new_events,
    stream_raw_events,
)
from agents_remember.serving.response_contract import StreamReadyMarker
from l114_e4_river_scale import _drain_once

REPO = "agents-remember"

# The message tests address recipients by agent id; the product validates the
# shape (MIK-R117), so the fixture ids are UUIDs, one per test recipient.
_AGENT_2 = "22222222-2222-4222-8222-222222222222"


def _leaf_binding() -> AgentBinding:
    return AgentBinding(
        agent_id=str(uuid.uuid4()),
        role="worker",
        request_id=str(uuid.uuid4()),
        report_path="/scratch/report.md",
        sprint_ref=TaskDocumentRef(repository=REPO, path="sprint/task.json"),
        master_ref=TaskDocumentRef(repository=REPO, path="master/task.json"),
        task_ref=TaskDocumentRef(repository=REPO, path="master/leaf.json"),
    )


def _server_info_payload() -> dict:
    # The smallest call of a server (MIK-R112: `ping` is removed, `server_info`
    # answers the same and more): the fields its response model requires.
    return {
        "ok": True,
        "server": "Agents Remember",
        "version": "1",
        "transport": "stdio",
        "configPath": "c",
        "coordinationRoot": "r",
        "workspaceRoot": "w",
        "transcriptRoot": "t",
        "servingBuild": {"version": "1", "bootedAt": "2026-10-10T00:00:00+00:00"},
    }


class ToolEventRiverTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.root = Path(self._dir.name)
        self.store = EventStore(self.root)

    def tearDown(self) -> None:
        reset_tool_writer()
        reset_ambient()
        call_target_role.set(None)
        self._dir.cleanup()

    def _install(self, caller: ToolCaller) -> ToolEventWriter:
        writer = ToolEventWriter(self.store, caller)
        install_tool_writer(writer)
        return writer

    def _workspace_lines(self) -> list[dict]:
        path = self.root / "workspace" / "events.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    # -- rule 1: every call is written from the first call --------------------

    def test_bound_first_call_writes_one_workspace_event_with_caller_and_facts(self) -> None:
        caller = snapshot_tool_caller(
            _leaf_binding(),
            coordination_root=self.root,
            config_path=self.root / "mcp" / "settings.json",
        )
        self.assertEqual("worker", caller.role)
        # No task document exists under the scratch root: the event names the
        # document by its path and the server would still start.
        self.assertEqual(f"{REPO}/master/leaf.json", caller.task_path)
        self._install(caller)

        log_config = McpRuntimeConfig(
            config_path=self.root / "settings.json",
            coordination_root=self.root,
            workspace_root=self.root,
            transcript_root=self.root / "logs" / "mcp",
        )
        with tool_call_context("server_info", {}, config=log_config):
            answered = complete_tool_response(
                "server_info", _server_info_payload(), config=log_config
            )

        self.assertTrue(answered["ok"])
        rows = self._workspace_lines()
        self.assertEqual(1, len(rows))
        event = rows[0]
        self.assertEqual("tool.completed", event["kind"])
        self.assertEqual("observed", event["trust"])
        self.assertEqual("ar-observer-event/v1", event["schema"])
        data = event["data"]
        self.assertEqual("server_info", data["tool"])
        self.assertTrue(data["ok"])
        # The delivered answer carries no token metadata; the count
        # lives on the log line (guard/retry-adjusted) and in river facts.
        self.assertNotIn("tokens", answered)
        log_files = sorted((self.root / "logs" / "mcp").glob("ar-tools-*.jsonl"))
        self.assertEqual(1, len(log_files))
        log_line = json.loads(log_files[0].read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(log_line["answerTokens"], data["tokens"])
        self.assertEqual("worker", data["role"])
        self.assertEqual(caller.agent_id, data["agentId"])
        self.assertEqual(f"{REPO}/master/leaf.json", data["taskPath"])

    def test_caller_is_read_once_at_start(self) -> None:
        binding = _leaf_binding()
        resolved = SimpleNamespace(
            document=SimpleNamespace(id="260928-MIK-L114", title="River Leaf")
        )
        with mock.patch("agents_remember.application.tool_caller.TaskDocumentTopology") as topology:
            topology.return_value.resolve.return_value = resolved
            caller = snapshot_tool_caller(
                binding,
                coordination_root=self.root,
                config_path=self.root / "mcp" / "settings.json",
            )
            self._install(caller)
            complete_tool_response("server_info", _server_info_payload())
            complete_tool_response("server_info", _server_info_payload())
            topology.return_value.resolve.assert_called_once_with(binding.task_ref)

    def test_bound_task_title_is_read_once_at_snapshot(self) -> None:
        binding = _leaf_binding()
        resolved = SimpleNamespace(
            document=SimpleNamespace(id="260928-MIK-L114", title="River Leaf")
        )
        with mock.patch("agents_remember.application.tool_caller.TaskDocumentTopology") as topology:
            topology.return_value.resolve.return_value = resolved
            caller = snapshot_tool_caller(
                binding,
                coordination_root=self.root,
                config_path=self.root / "mcp" / "settings.json",
            )
        self.assertEqual("260928-MIK-L114", caller.task_id)
        self.assertEqual("River Leaf", caller.task_title)
        self.assertIsNone(caller.task_path)
        event = build_tool_event("server_info", {"ok": True}, caller, delivered_tokens=1)
        self.assertEqual("260928-MIK-L114", event.data["taskId"])
        self.assertEqual("River Leaf", event.data["taskTitle"])
        self.assertNotIn("taskPath", event.data)

    def test_unbound_server_writes_a_chat_without_a_role_with_its_harness(self) -> None:
        caller = snapshot_tool_caller(
            None,
            coordination_root=self.root,
            config_path=Path("/harness/.codex/mcp/settings.json"),
        )
        self.assertEqual(".codex", caller.harness)
        self._install(caller)

        complete_tool_response("server_info", _server_info_payload())

        (data,) = [row["data"] for row in self._workspace_lines()]
        self.assertEqual("chat", data["role"])
        self.assertEqual(".codex", data["harness"])
        self.assertNotIn("agentId", data)

    def test_harness_falls_back_to_the_settings_stem(self) -> None:
        self.assertEqual("custom", derive_harness(Path("/etc/ar/custom.json")))
        self.assertIsNone(derive_harness(None))

    # -- rule 3: facts and no content -----------------------------------------

    def test_no_argument_answer_or_message_text_reaches_an_event(self) -> None:
        self._install(ToolCaller(role="worker", agent_id="agent-1"))
        marker = "MARKER-SECRET"
        complete_tool_response(
            "role_message",
            {
                "ok": False,
                "operation": "role_message",
                "status": "refused",
                "detail": f"carries {marker} in the answer",
                "senderLine": f"From worker says {marker}",
                "text": marker,
                "refusal": "recipient-busy",
                "recipientAgentId": "some-agent",
            },
        )
        raw = (self.root / "workspace" / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(marker, raw)
        (data,) = [row["data"] for row in self._workspace_lines()]
        self.assertEqual("recipient-busy", data["code"])
        self.assertEqual("refused", data["state"])
        self.assertNotIn("detail", data)
        self.assertNotIn("senderLine", data)
        self.assertNotIn("text", data)
        self.assertNotIn("recipientAgentId", data)

    def test_a_value_past_120_characters_is_left_out(self) -> None:
        caller = ToolCaller(role="worker", agent_id="agent-1")
        event = build_tool_event(
            "worktree_status",
            {"ok": True, "status": "s" * (TOOL_EVENT_VALUE_MAX_CHARS + 1)},
            caller,
            delivered_tokens=3,
        )
        self.assertNotIn("state", event.data)
        self.assertEqual("worktree_status", event.data["tool"])

    def test_role_start_and_role_message_name_the_started_or_addressed_role(self) -> None:
        self._install(ToolCaller(role="manager", agent_id="agent-1"))
        token = call_target_role.set("worker")
        try:
            complete_tool_response(
                "role_start",
                {
                    "ok": True,
                    "operation": "role_start",
                    "status": "running",
                    "detail": "started",
                    "role": "worker",
                },
            )
        finally:
            call_target_role.reset(token)
        token = call_target_role.set("reviewer")
        try:
            complete_tool_response(
                "role_message",
                {
                    "ok": True,
                    "operation": "role_message",
                    "status": "accepted",
                    "detail": "sent",
                    "recipientAgentId": "agent-2",
                },
            )
        finally:
            call_target_role.reset(token)
        rows = self._workspace_lines()
        self.assertEqual(["worker", "reviewer"], [row["data"]["targetRole"] for row in rows])

    # -- rule 1 second half: one event, never two --------------------------------

    def test_active_lifecycle_writes_its_event_as_today_and_no_second_one(self) -> None:
        self._install(ToolCaller(role="worker", agent_id="agent-1"))
        ambient = AmbientLifecycle(self.store)
        install_ambient(ambient)
        current = ambient.start()

        complete_tool_response("server_info", _server_info_payload())

        lifecycle_rows = self.store.read(current.id)
        tool_rows = [row for row in lifecycle_rows if row.kind == "tool.completed"]
        self.assertEqual(1, len(tool_rows))
        self.assertFalse((self.root / "workspace" / "events.jsonl").exists())

    # -- rule 6: a failed write is counted, never breaking -----------------------

    def test_failed_write_answers_as_written_and_next_events_carry_then_clear(self) -> None:
        writer = self._install(ToolCaller(role="worker", agent_id="agent-1"))
        # A tool server that is started anew starts its count at 0.
        self.assertEqual(0, writer.dropped)
        with mock.patch.object(
            retention_module, "admit_workspace_river_row", side_effect=OSError("disk refused")
        ):
            answered = complete_tool_response("server_info", _server_info_payload())
            self.assertTrue(answered["ok"])
            self.assertFalse(writer.record("server_info", {"ok": True}, delivered_tokens=1))
        self.assertEqual(2, writer.dropped)

        complete_tool_response("server_info", _server_info_payload())
        complete_tool_response("server_info", _server_info_payload())

        first, second = [row["data"] for row in self._workspace_lines()]
        self.assertEqual(2, first["dropped"])
        self.assertNotIn("dropped", second)
        self.assertEqual(0, writer.dropped)

    def test_failed_removal_counts_and_leaves_the_file_ungrown(self) -> None:
        writer = self._install(ToolCaller(role="worker", agent_id="agent-1"))
        path = self.root / "workspace" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x\n" * 2500)
        before = path.stat().st_size
        self.assertGreater(before, 4096)
        with (
            mock.patch.object(retention_module, "WORKSPACE_EVENT_MAX_BYTES", 4096),
            mock.patch.object(retention_module, "WORKSPACE_EVENT_TRIM_TO_BYTES", 1024),
            mock.patch.object(
                retention_module, "_compact_under_lock", side_effect=OSError("trim failed")
            ),
        ):
            self.assertFalse(writer.record("server_info", {"ok": True}, delivered_tokens=1))
        self.assertEqual(1, writer.dropped)
        self.assertEqual(before, path.stat().st_size)
        # The next append tries the removal again and the file fits afterwards.
        with (
            mock.patch.object(retention_module, "WORKSPACE_EVENT_MAX_BYTES", 4096),
            mock.patch.object(retention_module, "WORKSPACE_EVENT_TRIM_TO_BYTES", 1024),
        ):
            self.assertTrue(writer.record("server_info", {"ok": True}, delivered_tokens=1))
        self.assertLessEqual(path.stat().st_size, 4096)
        last = json.loads(path.read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(1, last["data"]["dropped"])

    # -- rule 5: the bound holds at every append ----------------------------------

    def test_every_append_lands_at_or_under_the_ceiling(self) -> None:
        self._install(ToolCaller(role="worker", agent_id="agent-1"))
        with (
            mock.patch.object(retention_module, "WORKSPACE_EVENT_MAX_BYTES", 4096),
            mock.patch.object(retention_module, "WORKSPACE_EVENT_TRIM_TO_BYTES", 1024),
        ):
            for _ in range(200):
                complete_tool_response("server_info", _server_info_payload())
            path = self.root / "workspace" / "events.jsonl"
            # Without the cut the file would hold ~200 rows (~43 KiB).
            self.assertLess(path.stat().st_size, 200 * 200)
            self.assertLessEqual(path.stat().st_size, 4096)
            rows = self._workspace_lines()
            self.assertLess(len(rows), 200)
            self.assertTrue(rows)

        events, offsets = read_new_events(self.root, {})
        self.assertEqual(
            [row["id"] for row in self._workspace_lines()],
            [event.payload["id"] for event in events],
        )
        again, _ = read_new_events(self.root, offsets)
        self.assertEqual([], again)
        # No second file of events beside the river.
        self.assertEqual(
            [self.root / "workspace" / "events.jsonl"], sorted(self.root.rglob("*.jsonl"))
        )

    def test_live_drain_pass_collects_each_row_once(self) -> None:
        # The incremental drain the retained E4 procedure runs while writers
        # write: one pass collects every row, the next collects nothing new.
        path = self.root / "workspace" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        for index in range(3):
            row = dict(json.loads(self._young_line()), id=f"drain-{index}")
            path.write_bytes(
                (path.read_bytes() if path.exists() else b"") + (json.dumps(row).encode() + b"\n")
            )
        seen: set[str] = set()
        offsets, repeats = _drain_once(self.root, {}, seen)
        self.assertEqual({"drain-0", "drain-1", "drain-2"}, seen)
        self.assertEqual(0, repeats)
        offsets, repeats = _drain_once(self.root, offsets, seen)
        self.assertEqual(0, repeats)
        self.assertEqual({"drain-0", "drain-1", "drain-2"}, seen)

    def test_a_row_that_does_not_fit_after_removal_is_not_written(self) -> None:
        with (
            mock.patch.object(retention_module, "WORKSPACE_EVENT_MAX_BYTES", 4096),
            mock.patch.object(retention_module, "WORKSPACE_EVENT_TRIM_TO_BYTES", 1024),
        ):
            self.assertTrue(admit_workspace_river_row(self.root, b'{"id": "small"}'))
            oversized = b"x" * 5000
            self.assertFalse(admit_workspace_river_row(self.root, oversized))
            path = self.root / "workspace" / "events.jsonl"
            self.assertLessEqual(path.stat().st_size, 4096)
            self.assertNotIn(oversized, path.read_bytes())

    def test_bound_values_are_the_packets(self) -> None:
        self.assertEqual(16777216, WORKSPACE_EVENT_MAX_BYTES)
        self.assertEqual(8388608, WORKSPACE_EVENT_TRIM_TO_BYTES)

    def test_one_function_serves_the_age_cut_and_the_size_cut(self) -> None:
        path = self.root / "workspace" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        old = {
            "schema": "ar-observer-event/v1",
            "id": "old",
            "ts": "2020-01-01T00:00:00+00:00",
            "kind": "tool.completed",
            "trust": "observed",
            "actor": "model",
            "data": {"tool": "server_info", "ok": True},
        }
        fresh = dict(old, id="fresh", ts="2026-10-08T19:00:00+00:00")
        lines = [json.dumps(old)] + [json.dumps(dict(fresh, id=f"fresh-{i}")) for i in range(4)]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.assertGreater(path.stat().st_size, 600)

        reclaimed = compact_workspace_river(
            self.root,
            now=datetime(2026, 10, 8, 19, 30, tzinfo=UTC),
            ttl_seconds=3600,
            bound=RiverSizeBound(max_bytes=600, trim_to_bytes=300),
        )

        self.assertGreater(reclaimed, 0)
        rows = self._workspace_lines()
        self.assertNotIn("old", [row["id"] for row in rows])
        self.assertLessEqual(path.stat().st_size, 300)

    # -- rule 7: the stream deletes nothing ---------------------------------------

    def test_connected_stream_changes_no_file_and_names_the_last_feed(self) -> None:
        coord = self.root / "coord"
        observer = coord / "logs" / "observer"
        old_ts = "2020-01-01T00:00:00+00:00"
        lifecycle_dir = observer / "lifecycles" / "lc-old"
        lifecycle_dir.mkdir(parents=True)
        (lifecycle_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "schema": "ar-observer-event/v1",
                    "id": "e-old",
                    "ts": old_ts,
                    "kind": "lifecycle.started",
                    "trust": "declared",
                    "actor": "model",
                    "lifecycleId": "lc-old",
                    "data": {"fleeting": True},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        workspace_dir = observer / "workspace"
        workspace_dir.mkdir(parents=True)
        (observer / "workspace" / "events.lock").write_text("", encoding="utf-8")
        (workspace_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "schema": "ar-observer-event/v1",
                    "id": "e-ws",
                    "ts": old_ts,
                    "kind": "tool.completed",
                    "trust": "observed",
                    "actor": "model",
                    "data": {"tool": "server_info", "ok": True},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        before = {
            path: (path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(observer.rglob("*"))
            if path.is_file()
        }
        config = McpRuntimeConfig(
            config_path=coord / "settings.json",
            coordination_root=coord,
            workspace_root=coord,
            transcript_root=coord / "logs" / "mcp",
        )

        async def _consume_ready() -> Any:
            agen = stream_raw_events(config, interval=0.001)
            try:
                async for sse in agen:
                    if sse.event == "ready":
                        return sse
            finally:
                await agen.aclose()
            raise AssertionError("the stream never became ready")

        ready = asyncio.run(_consume_ready())

        self.assertTrue(lifecycle_dir.is_dir())
        after = {
            path: (path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(observer.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertEqual(old_ts, last_event_ts(observer))
        self.assertEqual({"ready": True, "lastEventTs": old_ts}, ready.data)
        # The route's own marker declares the shape it emits.
        self.assertEqual(old_ts, StreamReadyMarker.model_validate(ready.data).last_event_ts)

    # -- R1-F01: a healthy crossing append cuts, then writes ---------------------

    def _young_line(self) -> bytes:
        return (
            json.dumps(
                {
                    "schema": "ar-observer-event/v1",
                    "id": "young",
                    "ts": datetime.now(UTC).isoformat(),
                    "kind": "tool.completed",
                    "trust": "observed",
                    "actor": "model",
                    "data": {"tool": "server_info", "ok": True},
                }
            ).encode("utf-8")
            + b"\n"
        )

    def test_crossing_append_cuts_oldest_rows_and_writes_the_row(self) -> None:
        path = self.root / "workspace" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = self._young_line()
        path.write_bytes(line * (WORKSPACE_EVENT_MAX_BYTES // len(line)))
        before = path.stat().st_size
        self.assertLess(before, WORKSPACE_EVENT_MAX_BYTES)
        writer = self._install(ToolCaller(role="worker", agent_id="agent-1"))

        for _ in range(5):
            self.assertTrue(writer.record("server_info", {"ok": True}, delivered_tokens=1))
            self.assertLessEqual(path.stat().st_size, WORKSPACE_EVENT_MAX_BYTES)
        self.assertEqual(0, writer.dropped)

        after = path.stat().st_size
        self.assertLessEqual(after, WORKSPACE_EVENT_TRIM_TO_BYTES + 4096)
        self.assertGreater(workspace_base_offset(self.root), 0)
        rows = self._workspace_lines()
        self.assertTrue(rows)
        last = rows[-1]
        self.assertEqual("server_info", last["data"]["tool"])
        self.assertEqual("agent-1", last["data"]["agentId"])
        events, offsets = read_new_events(self.root, {})
        self.assertEqual([row["id"] for row in rows], [event.payload["id"] for event in events])
        again, _ = read_new_events(self.root, offsets)
        self.assertEqual([], again)

    # -- R1-F02: sibling workspace appends obey the same bound -------------------

    def test_sibling_workspace_append_never_leaves_the_river_over_the_ceiling(self) -> None:
        path = self.root / "workspace" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        line = self._young_line()
        path.write_bytes(line * (WORKSPACE_EVENT_MAX_BYTES // len(line)))
        event = Event(
            id="sibling-row",
            ts=datetime.now(UTC).isoformat(),
            kind="seat.example",
            trust="observed",
            actor="system",
            data={"fact": "x"},
        )
        EventStore(self.root).append(event)
        self.assertLessEqual(path.stat().st_size, WORKSPACE_EVENT_MAX_BYTES)
        rows = self._workspace_lines()
        self.assertEqual("sibling-row", rows[-1]["id"])
        self.assertEqual("seat.example", rows[-1]["kind"])

    def test_sibling_lifecycle_and_heartbeat_branches_are_unchanged(self) -> None:
        store = EventStore(self.root)
        event = Event(
            id="lc-row",
            ts=datetime.now(UTC).isoformat(),
            kind="tool.completed",
            trust="observed",
            actor="model",
            lifecycleId="lc-1",
            data={"tool": "server_info", "ok": True},
        )
        store.append(event)
        lifecycle_path = self.root / "lifecycles" / "lc-1" / "events.jsonl"
        self.assertTrue(lifecycle_path.is_file())
        self.assertEqual(1, len(store.read("lc-1")))

    # -- R1-F03: id-addressed messages carry the resolved role -------------------

    def _role_message_config(self) -> McpRuntimeConfig:
        coord = self.root / "coord"
        return McpRuntimeConfig(
            config_path=coord / "settings.json",
            coordination_root=coord,
            workspace_root=coord,
            transcript_root=coord / "logs" / "mcp",
        )

    def _send_as(
        self,
        call: RoleMessageCall,
        receipt: dict[str, Any] | BaseException,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        self._install(ToolCaller(role="manager", agent_id="agent-1"))
        resolve: Any = (
            receipt if isinstance(receipt, BaseException) else _Recipient(_AGENT_2, receipt)
        )
        # Since MIK-R71/R117 the send path reads the caller's launch binding
        # (role plus task references) and addresses the sender line from the
        # started agent (role, subject, agent id): the fakes carry the current
        # shapes, a taskless manager serving Projects.
        caller_binding = AgentBinding(
            agent_id="agent-1",
            role="manager",
            request_id="agent-1-request",
            report_path="/scratch/report.md",
        )
        caller_sender = StartingAgent(agent_id="agent-1", role="manager", subject="Projects")
        with (
            mock.patch.object(paseo_role_tools, "_caller", return_value=caller_binding),
            mock.patch.object(paseo_role_tools, "starting_agent", return_value=caller_sender),
            mock.patch.object(paseo_role_tools, "_resolve_recipient", return_value=resolve)
            if not isinstance(receipt, BaseException)
            else mock.patch.object(paseo_role_tools, "_resolve_recipient", side_effect=receipt),
            mock.patch.object(paseo_role_tools, "_deliver", return_value=payload),
        ):
            return role_agents.role_message_payload(self._role_message_config(), call)

    def test_id_addressed_message_records_the_receipt_role(self) -> None:
        marker = "MARKER-SECRET"
        answered = self._send_as(
            RoleMessageCall(agent_id=_AGENT_2, text=f"hello {marker}"),
            {"agentId": _AGENT_2, "role": "worker", "status": "running"},
            {
                "ok": True,
                "operation": "role_message",
                "status": "accepted",
                "detail": marker,
            },
        )
        self.assertTrue(answered["ok"])
        (data,) = [row["data"] for row in self._workspace_lines()]
        self.assertEqual("worker", data["targetRole"])
        raw = (self.root / "workspace" / "events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(marker, raw)

    def test_selection_addressed_message_records_the_selection_role(self) -> None:
        answered = self._send_as(
            RoleMessageCall(role="reviewer", text="hello"),
            {"agentId": _AGENT_2, "role": "reviewer", "status": "running"},
            {
                "ok": True,
                "operation": "role_message",
                "status": "accepted",
                "detail": "sent",
            },
        )
        self.assertTrue(answered["ok"])
        (data,) = [row["data"] for row in self._workspace_lines()]
        self.assertEqual("reviewer", data["targetRole"])

    def test_unresolved_recipient_stays_literal_without_a_target_role(self) -> None:
        answered = self._send_as(
            RoleMessageCall(agent_id="agent-9", text="hello"),
            paseo_role_tools._Refused("recipient-not-found", "gone", "start again"),
            {"ok": False, "operation": "role_message", "status": "refused"},
        )
        self.assertFalse(answered["ok"])
        rows = self._workspace_lines()
        if rows:
            self.assertNotIn("targetRole", rows[-1]["data"])

    # -- R1-F07: the stream creates no observer file ------------------------------

    def _consume_ready(self, config: McpRuntimeConfig) -> Any:
        async def _run() -> Any:
            agen = stream_raw_events(config, interval=0.001)
            try:
                async for sse in agen:
                    if sse.event == "ready":
                        return sse
            finally:
                await agen.aclose()
            raise AssertionError("the stream never became ready")

        return asyncio.run(_run())

    def test_stream_with_missing_lock_creates_no_observer_file(self) -> None:
        coord = self.root / "coord"
        observer = coord / "logs" / "observer"
        workspace_dir = observer / "workspace"
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "schema": "ar-observer-event/v1",
                    "id": "e-ws",
                    "ts": "2026-10-08T20:00:00+00:00",
                    "kind": "tool.completed",
                    "trust": "observed",
                    "actor": "model",
                    "data": {"tool": "server_info", "ok": True},
                }
            )
            + "\n",
            encoding="utf-8",
        )
        config = McpRuntimeConfig(
            config_path=coord / "settings.json",
            coordination_root=coord,
            workspace_root=coord,
            transcript_root=coord / "logs" / "mcp",
        )
        before = sorted(
            str(path.relative_to(observer)) for path in observer.rglob("*") if path.is_file()
        )
        ready = self._consume_ready(config)
        after = sorted(
            str(path.relative_to(observer)) for path in observer.rglob("*") if path.is_file()
        )
        self.assertEqual(before, after)
        self.assertNotIn("workspace/events.lock", after)
        self.assertTrue(ready.data["ready"])
        events, _ = read_new_events(observer, {})
        self.assertEqual(["e-ws"], [event.payload["id"] for event in events])

    def test_stream_with_existing_lock_leaves_content_untouched(self) -> None:
        coord = self.root / "coord"
        observer = coord / "logs" / "observer"
        workspace_dir = observer / "workspace"
        workspace_dir.mkdir(parents=True)
        (workspace_dir / "events.jsonl").write_bytes(b'{"id": "e-ws"}\n')
        (workspace_dir / "events.lock").write_bytes(b"")
        config = McpRuntimeConfig(
            config_path=coord / "settings.json",
            coordination_root=coord,
            workspace_root=coord,
            transcript_root=coord / "logs" / "mcp",
        )
        before = {
            path: (path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(observer.rglob("*"))
            if path.is_file()
        }
        self._consume_ready(config)
        after = {
            path: (path.stat().st_mtime_ns, path.stat().st_size)
            for path in sorted(observer.rglob("*"))
            if path.is_file()
        }
        self.assertEqual(before, after)

    def test_protected_lifecycle_survives_past_the_former_cleanup_interval(self) -> None:
        old_ts = "2020-01-01T00:00:00+00:00"
        now = datetime(2020, 1, 1, 3, 0, 0, tzinfo=UTC)
        for lifecycle_id in ("lc-keep", "lc-drop"):
            lifecycle_dir = self.root / "lifecycles" / lifecycle_id
            lifecycle_dir.mkdir(parents=True)
            (lifecycle_dir / "events.jsonl").write_text(
                json.dumps(
                    {
                        "schema": "ar-observer-event/v1",
                        "id": f"e-{lifecycle_id}",
                        "ts": old_ts,
                        "kind": "lifecycle.started",
                        "trust": "declared",
                        "actor": "model",
                        "lifecycleId": lifecycle_id,
                        "data": {},
                    }
                )
                + "\n",
                encoding="utf-8",
            )
        removed = prune_expired_lifecycle_event_logs(
            self.root, now=now, protected_lifecycle_ids=frozenset({"lc-keep"})
        )
        self.assertTrue((self.root / "lifecycles" / "lc-keep" / "events.jsonl").is_file())
        self.assertFalse((self.root / "lifecycles" / "lc-drop").exists())
        self.assertEqual(1, len(removed))

    # -- R1-F08: the registered startup path and a fresh process -------------------

    _BINDING_ENV_KEYS = (
        "AR_PASEO_AGENT_ID",
        "AR_ROLE",
        "AR_REQUEST_ID",
        "AR_REPORT_PATH",
        "AR_SPRINT_REF",
        "AR_MASTER_REF",
        "AR_TASK_REF",
    )

    def _clear_binding_env(self) -> None:
        for key in self._BINDING_ENV_KEYS:
            os.environ.pop(key, None)

    def _bound_env(self, agent_id: str) -> dict[str, str]:
        ref = json.dumps({"repository": REPO, "path": "master/leaf.json"})
        return {
            "AR_PASEO_AGENT_ID": agent_id,
            "AR_ROLE": "worker",
            "AR_REQUEST_ID": str(uuid.uuid4()),
            "AR_REPORT_PATH": "/scratch/report.md",
            "AR_SPRINT_REF": json.dumps({"repository": REPO, "path": "sprint/task.json"}),
            "AR_MASTER_REF": json.dumps({"repository": REPO, "path": "master/task.json"}),
            "AR_TASK_REF": ref,
        }

    def _observer_lines(self, base: Path) -> list[dict]:
        path = observer_logs_root(base) / "workspace" / "events.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def _startup_config(self) -> McpRuntimeConfig:
        return McpRuntimeConfig(
            config_path=self.root / "mcp" / "settings.json",
            coordination_root=self.root,
            workspace_root=self.root,
            transcript_root=self.root / "logs" / "mcp",
        )

    def test_registered_startup_writes_a_bound_first_call(self) -> None:
        agent_id = str(uuid.uuid4())
        with mock.patch.dict(os.environ, clear=False):
            self._clear_binding_env()
            os.environ.update(self._bound_env(agent_id))
            # The exact startup create_server runs: no manual writer install.
            initialize_mcp_application(self._startup_config())
            answered = complete_tool_response("server_info", _server_info_payload())
        self.assertTrue(answered["ok"])
        (event,) = self._observer_lines(self.root)
        data = event["data"]
        self.assertEqual("server_info", data["tool"])
        self.assertEqual("worker", data["role"])
        self.assertEqual(agent_id, data["agentId"])
        self.assertEqual(f"{REPO}/master/leaf.json", data["taskPath"])

    def test_registered_startup_without_a_binding_writes_chat(self) -> None:
        with mock.patch.dict(os.environ, clear=False):
            self._clear_binding_env()
            initialize_mcp_application(self._startup_config())
            complete_tool_response("server_info", _server_info_payload())
        (event,) = self._observer_lines(self.root)
        self.assertEqual("chat", event["data"]["role"])

    def test_registered_startup_snapshot_stays_fixed_while_running(self) -> None:
        agent_id = str(uuid.uuid4())
        with mock.patch.dict(os.environ, clear=False):
            self._clear_binding_env()
            os.environ.update(self._bound_env(agent_id))
            initialize_mcp_application(self._startup_config())
            os.environ["AR_ROLE"] = "reviewer"
            complete_tool_response("server_info", _server_info_payload())
            complete_tool_response("server_info", _server_info_payload())
        rows = self._observer_lines(self.root)
        self.assertEqual(2, len(rows))
        for row in rows:
            self.assertEqual("worker", row["data"]["role"])
            self.assertEqual(agent_id, row["data"]["agentId"])

    def test_fresh_process_writes_its_first_call_through_startup(self) -> None:
        agent_id = str(uuid.uuid4())
        coord = self.root / "child"
        child = (
            "import json, os, sys\n"
            "from pathlib import Path\n"
            f"sys.path.insert(0, {str(MCP_SRC)!r})\n"
            "from agents_remember.application.runtime.startup import initialize_mcp_application\n"
            "from agents_remember.application.tool_response import complete_tool_response\n"
            "from agents_remember.kernel.primitives.checkout_coordination import declare_test_process\n"
            "from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig\n"
            "declare_test_process()\n"
            "root = Path(os.environ['L114_CHILD_ROOT'])\n"
            "config = McpRuntimeConfig(config_path=root / 'mcp' / 'settings.json', coordination_root=root, workspace_root=root, transcript_root=root / 'logs' / 'mcp')\n"
            "initialize_mcp_application(config)\n"
            "first = complete_tool_response('server_info', {'ok': True, 'server': 'Agents Remember', 'version': '1', 'transport': 'stdio', 'configPath': 'c', 'coordinationRoot': 'r', 'workspaceRoot': 'w', 'transcriptRoot': 't', 'servingBuild': {'version': '1', 'bootedAt': '2026-10-10T00:00:00+00:00'}})\n"
            "os.environ['AR_ROLE'] = 'reviewer'\n"
            "second = complete_tool_response('server_info', {'ok': True, 'server': 'Agents Remember', 'version': '1', 'transport': 'stdio', 'configPath': 'c', 'coordinationRoot': 'r', 'workspaceRoot': 'w', 'transcriptRoot': 't', 'servingBuild': {'version': '1', 'bootedAt': '2026-10-10T00:00:00+00:00'}})\n"
            "assert first['ok'] and second['ok']\n"
            "print('child-wrote-two-rows')\n        "
        )
        env = dict(os.environ)
        for key in self._BINDING_ENV_KEYS:
            env.pop(key, None)
        env.update(self._bound_env(agent_id))
        env["L114_CHILD_ROOT"] = str(coord)
        env["PYTHONPATH"] = str(MCP_SRC) + os.pathsep + env.get("PYTHONPATH", "")
        completed = subprocess.run(
            [sys.executable, "-c", child],
            capture_output=True,
            text=True,
            timeout=120,
            env=env,
            cwd=str(self.root),
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr[-2000:])
        self.assertIn("child-wrote-two-rows", completed.stdout)
        rows = [
            json.loads(line)
            for line in (observer_logs_root(coord) / "workspace" / "events.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        self.assertEqual(2, len(rows))
        for row in rows:
            self.assertEqual("tool.completed", row["kind"])
            self.assertEqual("worker", row["data"]["role"])
            self.assertEqual(agent_id, row["data"]["agentId"])


if __name__ == "__main__":
    unittest.main()
