"""L51-R2 and L51-R5 tests: launch binding confirmation, single retry, held re-confirm, and sender line."""

from __future__ import annotations

import os
import time
import unittest
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.application.agent_binding import (
    AGENT_ID_VARIABLE,
    REQUEST_ID_VARIABLE,
    TOOL_SERVER_NAME,
)
from agents_remember.cli import role_launch_confirmation
from agents_remember.cli.paseo_role_caller import _caller, sender_line, starting_agent
from agents_remember.cli.paseo_role_tools import send_role_message, start_leaf_set
from agents_remember.cli.role_launch_confirmation import confirm_seat_binding
from agents_remember.cli.role_launch_receipts import _read_receipt, _receipt_path
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_agents import RoleMessageCall, RoleStartSetCall
from agents_remember.models.role_launcher import RoleSelection
from agents_remember.serving import server_bindings
from agents_remember.serving.paseo.paseo_plugin_files import read_role_agents
from agents_remember.serving.server_bindings import (
    BoundIdentityPayload,
    ProcessAncestor,
    ServerBindingRecord,
    bindings_directory,
    process_start,
)
from paseo_launch_test_support import CATALOG
from paseo_role_tools_test_support import RoleToolsTestCase, binding, selection_of
from test_paseo_bridge_agents import TOOL_SERVER_START_MS, AgentCommandScriptTests
from test_paseo_launch import ROLE_REFS, PaseoLaunchTestCase

CLAUDE_CATALOG: dict[str, Any] = {
    **CATALOG,
    "providers": [
        *CATALOG["providers"],
        {
            "id": "claude",
            "label": "Claude",
            "modes": [],
            "models": [{"id": "sonnet", "label": "Sonnet", "features": [], "efforts": []}],
            "acceptsToolServers": True,
        },
    ],
}


def _write_fake_proc(proc_root: Path, entries: dict[int, tuple[int, str, bytes, bytes]]) -> None:
    """Populate a synthetic procfs tree: ``pid -> (ppid, starttime, environ, cmdline)``."""
    proc_root.mkdir(parents=True, exist_ok=True)
    for pid, (ppid, start, env_bytes, cmdline) in entries.items():
        pid_dir = proc_root / str(pid)
        pid_dir.mkdir(parents=True, exist_ok=True)
        fields = ["S", str(ppid)] + ["0"] * 17 + [start]
        (pid_dir / "stat").write_text(f"{pid} (proc) {' '.join(fields)}\n", encoding="utf-8")
        (pid_dir / "environ").write_bytes(env_bytes)
        (pid_dir / "cmdline").write_bytes(cmdline)


def _write_binding_file(config: McpRuntimeConfig, filename: str, **fields: Any) -> Path:
    live_pid = os.getpid() if fields.get("pid") is None else int(fields["pid"])
    resolved_start = process_start(live_pid) if "proc_start" not in fields else fields["proc_start"]
    binding_state = fields.get("binding_state", "bound")
    payload = (
        BoundIdentityPayload(
            agentId=fields.get("agent_id") or str(uuid.uuid4()),
            requestId=fields.get("request_id") or str(uuid.uuid4()),
            role=fields.get("role", "worker"),
        )
        if binding_state == "bound"
        else None
    )
    record = ServerBindingRecord(
        pid=live_pid,
        processStart=resolved_start,
        startedAt="2026-10-02T00:00:00Z",
        configPath=str(config.config_path),
        parentPid=int(fields.get("parent_pid", 0)),
        parentProcessStart=fields.get("parent_start"),
        ancestors=fields.get("ancestors") or [],
        bindingState=binding_state,
        binding=payload,
        unreadableReason=fields.get("unreadable_reason"),
    )
    directory = bindings_directory(config)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(record.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _seed_harness(proc_root: Path, harness_pid: int, harness_start: str, seat_id: str) -> int:
    live_pid = os.getpid()
    _write_fake_proc(
        proc_root,
        {
            live_pid: (harness_pid, "self-start", b"", b"python -m agents_remember.mcp"),
            harness_pid: (1, harness_start, f"PASEO_AGENT_ID={seat_id}\x00".encode(), b"codex"),
        },
    )
    return live_pid


class _LaunchFixture(PaseoLaunchTestCase):
    """Fast-polling launch fixture with full control over binding files and procfs."""

    def setUp(self) -> None:
        super().setUp()
        self.runtime.write_server_bindings = False
        self.replace(role_launch_confirmation, "BINDING_CONFIRM_WAIT_S", 0.08)
        self.replace(role_launch_confirmation, "BINDING_CONFIRM_POLL_S", 0.01)

    def _live_agents(self) -> list[str]:
        return [aid for aid, agent in self.runtime.agents.items() if not agent.get("archivedAt")]


class TestForeignBindingAndSingleRetry(_LaunchFixture):
    """Required Test #1: foreign binding archives attempt 1, retries once under same requestId."""

    def test_foreign_binding_retries_once_and_succeeds_when_retry_confirms(self) -> None:
        proc_root = self.root / "fake-proc"
        self.replace(server_bindings, "PROC_ROOT", proc_root)
        foreign_id = str(uuid.uuid4())
        created_attempts: list[tuple[str, str]] = []

        def on_launch(command: str, payload: dict[str, Any]) -> None:
            if command != "agent-create" or "prompt" in payload:
                return
            aid, req_id = payload["agentId"], payload["labels"]["ar.request-id"]
            attempt_no = len(created_attempts) + 1
            created_attempts.append((aid, req_id))
            hpid, hstart = 40000 + attempt_no, f"start-{attempt_no}"
            live_pid = _seed_harness(proc_root, hpid, hstart, aid)
            anc = [ProcessAncestor(pid=hpid, processStart=hstart)]
            if attempt_no == 1:
                _write_binding_file(
                    self.config,
                    "attempt-1.json",
                    pid=live_pid,
                    proc_start="self-start",
                    parent_pid=hpid,
                    parent_start=hstart,
                    ancestors=anc,
                    agent_id=foreign_id,
                    request_id=str(uuid.uuid4()),
                )
            else:
                (bindings_directory(self.config) / "attempt-1.json").unlink(missing_ok=True)
                _write_binding_file(
                    self.config,
                    "attempt-2.json",
                    pid=live_pid,
                    proc_start="self-start",
                    parent_pid=hpid,
                    parent_start=hstart,
                    ancestors=anc,
                    agent_id=aid,
                    request_id=req_id,
                )

        self.runtime.observer = on_launch
        request = self.request("worker")
        status, public = self.dispatch(request)
        receipt = self.receipt(request)

        self.assertEqual((status, public["status"]), (200, "running"))
        self.assertEqual(len(created_attempts), 2)
        attempt1_id, req1 = created_attempts[0]
        attempt2_id, req2 = created_attempts[1]
        self.assertNotEqual(attempt1_id, attempt2_id)
        self.assertEqual((req1, req2), (str(request.request_id), str(request.request_id)))
        self.assertEqual(self._live_agents(), [attempt2_id])
        self.assertIsNotNone(self.runtime.agents[attempt1_id]["archivedAt"])

        creates = [payload for cmd, payload in self.runtime.calls if cmd == "agent-create"]
        self.assertEqual(len(creates), 3)
        self.assertNotIn("prompt", creates[0])
        self.assertNotIn("prompt", creates[1])
        self.assertTrue(creates[1]["idempotencyKey"].endswith(":retry-1"))
        self.assertEqual(
            creates[1]["mcpServers"][TOOL_SERVER_NAME]["env"][AGENT_ID_VARIABLE], attempt2_id
        )
        self.assertEqual(
            creates[1]["mcpServers"][TOOL_SERVER_NAME]["env"][REQUEST_ID_VARIABLE],
            str(request.request_id),
        )
        self.assertEqual(creates[2]["agentId"], attempt2_id)
        self.assertEqual(creates[2]["prompt"], self.first_message(request))
        self.assertIs(creates[2]["skipToolServerWait"], True)

        self.assertEqual(
            (receipt["agentId"], receipt["execution"]["agentId"]), (attempt2_id, attempt2_id)
        )
        self.assertEqual(receipt["toolServer"]["environment"][AGENT_ID_VARIABLE], attempt2_id)
        confirmation = receipt["bindingConfirmation"]
        self.assertEqual(confirmation["status"], "confirmed")
        self.assertIs(confirmation["retried"], True)
        self.assertIs(confirmation["ancestryChecked"], True)
        self.assertEqual(confirmation["archivedAgentIds"], [attempt1_id])
        self.assertEqual(confirmation["priorFailureReason"], "foreign-binding")
        self.assertIsInstance(confirmation["waitMs"], int)
        assert self.config.paseo_runtime is not None
        self.assertIn(attempt2_id, read_role_agents(self.config.paseo_runtime.home))

    def test_foreign_binding_on_both_attempts_refuses_with_zero_live_seats(self) -> None:
        proc_root = self.root / "fake-proc"
        self.replace(server_bindings, "PROC_ROOT", proc_root)
        foreign_id = str(uuid.uuid4())
        attempts: list[str] = []

        def on_launch(command: str, payload: dict[str, Any]) -> None:
            if command != "agent-create" or "prompt" in payload:
                return
            aid = payload["agentId"]
            attempts.append(aid)
            hpid, hstart = 41000 + len(attempts), f"start-{len(attempts)}"
            live_pid = _seed_harness(proc_root, hpid, hstart, aid)
            _write_binding_file(
                self.config,
                f"foreign-{len(attempts)}.json",
                pid=live_pid,
                proc_start="self-start",
                parent_pid=hpid,
                parent_start=hstart,
                ancestors=[ProcessAncestor(pid=hpid, processStart=hstart)],
                agent_id=foreign_id,
                request_id=str(uuid.uuid4()),
            )

        self.runtime.observer = on_launch
        request = self.request("worker")
        status, public = self.dispatch(request)
        receipt = self.receipt(request)

        self.assertEqual((status, public["status"]), (502, "rejected"))
        self.assertEqual(len(attempts), 2)
        self.assertEqual(self._live_agents(), [])
        self.assertIs(receipt["hostAgentExists"], False)
        self.assertIn("foreign-binding", public["detail"])
        for aid in attempts:
            self.assertIn(aid, public["detail"])
        confirmation = receipt["bindingConfirmation"]
        self.assertEqual(
            (confirmation["status"], confirmation["reason"]), ("failed", "foreign-binding")
        )
        self.assertEqual(confirmation["archivedAgentIds"], attempts)
        creates = [payload for cmd, payload in self.runtime.calls if cmd == "agent-create"]
        self.assertTrue(len(creates) == 2 and all("prompt" not in p for p in creates))


class TestTimeoutAndMultipleServers(_LaunchFixture):
    """Required Tests #2 and #3: timeout double-failure and two bound servers under one harness."""

    def test_missing_registration_retries_once_and_refuses_with_zero_live_seats(self) -> None:
        request = self.request("worker")
        status, public = self.dispatch(request)
        receipt = self.receipt(request)

        creates = [p for cmd, p in self.runtime.calls if cmd == "agent-create"]
        archives = [p["agentId"] for cmd, p in self.runtime.calls if cmd == "agent-archive"]
        self.assertEqual((status, public["status"]), (502, "rejected"))
        self.assertEqual(len(creates), 2)
        self.assertEqual(
            creates[0]["labels"]["ar.request-id"], creates[1]["labels"]["ar.request-id"]
        )
        self.assertEqual((len(archives), self._live_agents()), (2, []))
        self.assertIn("binding-not-registered", public["detail"])
        for aid in archives:
            self.assertIn(aid, public["detail"])
        self.assertEqual(receipt["bindingConfirmation"]["reason"], "binding-not-registered")
        self.assertEqual(receipt["bindingConfirmation"]["archivedAgentIds"], archives)

    def test_two_bound_servers_under_one_harness_refuse_immediately(self) -> None:
        proc_root = self.root / "fake-proc"
        self.replace(server_bindings, "PROC_ROOT", proc_root)
        self.replace(role_launch_confirmation, "BINDING_CONFIRM_WAIT_S", 5.0)
        seat_id, req_id, foreign_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        hpid, hstart = 42001, "harness-start"
        live_pid = _seed_harness(proc_root, hpid, hstart, seat_id)
        anc = [ProcessAncestor(pid=hpid, processStart=hstart)]
        _write_binding_file(
            self.config,
            "own.json",
            pid=live_pid,
            proc_start="self-start",
            parent_pid=hpid,
            parent_start=hstart,
            ancestors=anc,
            agent_id=seat_id,
            request_id=req_id,
        )
        _write_binding_file(
            self.config,
            "foreign.json",
            pid=live_pid,
            proc_start="self-start",
            parent_pid=hpid,
            parent_start=hstart,
            ancestors=anc,
            agent_id=foreign_id,
            request_id=str(uuid.uuid4()),
        )
        with patch.object(role_launch_confirmation.time, "sleep") as sleep_mock:
            result = confirm_seat_binding(self.config, seat_id, req_id)
        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason, "multiple-bound-servers")
        self.assertEqual(result.foreign_agent_ids, (foreign_id,))
        sleep_mock.assert_not_called()

    def test_two_bound_servers_sharing_parent_pid_without_harness_root_refuse_immediately(
        self,
    ) -> None:
        proc_root = self.root / "fake-proc"
        self.replace(server_bindings, "PROC_ROOT", proc_root)
        self.replace(role_launch_confirmation, "BINDING_CONFIRM_WAIT_S", 5.0)
        seat_id, req_id, foreign_id = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
        live_pid = os.getpid()
        _write_fake_proc(proc_root, {live_pid: (55555, "self-start", b"", b"python")})
        _write_binding_file(
            self.config,
            "own.json",
            pid=live_pid,
            proc_start="self-start",
            parent_pid=55555,
            parent_start="p-start",
            agent_id=seat_id,
            request_id=req_id,
        )
        _write_binding_file(
            self.config,
            "foreign.json",
            pid=live_pid,
            proc_start="self-start",
            parent_pid=55555,
            parent_start="p-start",
            agent_id=foreign_id,
            request_id=str(uuid.uuid4()),
        )
        result = confirm_seat_binding(self.config, seat_id, req_id)
        self.assertFalse(result.confirmed)
        self.assertEqual(result.reason, "multiple-bound-servers")

    def test_unbound_server_allowed_and_unreadable_server_refused_under_same_harness(self) -> None:
        proc_root = self.root / "fake-proc"
        self.replace(server_bindings, "PROC_ROOT", proc_root)
        seat_id, req_id = str(uuid.uuid4()), str(uuid.uuid4())
        hpid, hstart = 42009, "harness-start"
        live_pid = _seed_harness(proc_root, hpid, hstart, seat_id)
        anc = [ProcessAncestor(pid=hpid, processStart=hstart)]
        _write_binding_file(
            self.config,
            "own.json",
            pid=live_pid,
            proc_start="self-start",
            parent_pid=hpid,
            parent_start=hstart,
            ancestors=anc,
            agent_id=seat_id,
            request_id=req_id,
        )
        _write_binding_file(
            self.config,
            "unbound.json",
            pid=live_pid,
            proc_start="self-start",
            parent_pid=hpid,
            parent_start=hstart,
            ancestors=anc,
            binding_state="unbound",
        )
        ok = confirm_seat_binding(self.config, seat_id, req_id)
        self.assertTrue(ok.confirmed and ok.ancestry_checked)

        _write_binding_file(
            self.config,
            "broken.json",
            pid=live_pid,
            proc_start="self-start",
            parent_pid=hpid,
            parent_start=hstart,
            ancestors=anc,
            binding_state="unreadable",
            unreadable_reason="malformed AR_PASEO_AGENT_ID",
        )
        bad = confirm_seat_binding(self.config, seat_id, req_id)
        self.assertFalse(bad.confirmed)
        self.assertEqual(bad.reason, "unreadable-binding")
        self.assertIn("malformed AR_PASEO_AGENT_ID", bad.detail or "")


class TestProcAbsentAndLazyHarness(_LaunchFixture):
    """Architect /proc-absent fallback and lazy-harness skip."""

    def test_proc_absent_confirms_by_identity_only_and_records_skipped_ancestry(self) -> None:
        self.replace(server_bindings, "PROC_ROOT", self.root / "no-such-proc")
        _write_binding_file(
            self.config,
            "unrelated-foreign.json",
            agent_id=str(uuid.uuid4()),
            request_id=str(uuid.uuid4()),
            parent_pid=9999,
            proc_start=None,
        )

        def on_launch(command: str, payload: dict[str, Any]) -> None:
            if command == "agent-create" and "prompt" not in payload:
                _write_binding_file(
                    self.config,
                    "own.json",
                    agent_id=payload["agentId"],
                    request_id=payload["labels"]["ar.request-id"],
                    parent_pid=9999,
                    proc_start=None,
                )

        self.runtime.observer = on_launch
        request = self.request("worker")
        status, public = self.dispatch(request)
        confirmation = self.receipt(request)["bindingConfirmation"]

        self.assertEqual((status, public["status"]), (200, "running"))
        self.assertEqual(confirmation["status"], "confirmed")
        self.assertIs(confirmation["ancestryChecked"], False)
        self.assertEqual(confirmation["ancestrySkippedReason"], "proc-unavailable")

    def test_lazy_harness_claude_skips_step_2_confirmation(self) -> None:
        self.replace(self.runtime, "_catalog", return_value=CLAUDE_CATALOG)
        request = self.request("worker", agentId="claude", modelId="sonnet")
        status, public = self.dispatch(request)
        receipt = self.receipt(request)

        self.assertEqual((status, public["status"]), (200, "running"))
        creates = [p for cmd, p in self.runtime.calls if cmd == "agent-create"]
        self.assertEqual(len(creates), 1)
        self.assertIn("prompt", creates[0])
        self.assertNotIn("skipToolServerWait", creates[0])
        self.assertEqual(
            receipt["bindingConfirmation"],
            {
                "status": "skipped",
                "reason": "provider-starts-server-after-create",
                "provider": "claude",
            },
        )


class TestHeldSeatReconfirmation(RoleToolsTestCase):
    """Required Test #4: held seat confirmed at create AND re-confirmed before opening message."""

    def setUp(self) -> None:
        super().setUp()
        self.replace(role_launch_confirmation, "BINDING_CONFIRM_WAIT_S", 0.08)
        self.replace(role_launch_confirmation, "BINDING_CONFIRM_POLL_S", 0.01)

    def receipt_for(self, role: str) -> dict[str, Any]:
        selection = RoleSelection.model_validate({"role": role, **ROLE_REFS[role]})
        loaded = _read_receipt(_receipt_path(self.config, selection))
        assert loaded is not None
        return loaded

    def test_held_seat_confirmed_at_create_and_reconfirmed_at_open_with_skip_wait(self) -> None:
        manager = binding("manager")
        confirmed_ids: list[str] = []
        real_confirm = role_launch_confirmation.confirm_seat_binding

        def spy(config: McpRuntimeConfig, agent_id: str, request_id: str, **kw: Any):
            confirmed_ids.append(agent_id)
            return real_confirm(config, agent_id, request_id, **kw)

        self.replace(role_launch_confirmation, "confirm_seat_binding", spy)
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        self.assertEqual(confirmed_ids.count(reviewer_id), 1)
        self.assertEqual(self.receipt_for("reviewer")["bindingConfirmation"]["status"], "confirmed")

        self.runtime.calls.clear()
        opened = self.message(manager, agent_id=reviewer_id, text="Review plan.")
        self.assertEqual((opened["ok"], opened["openedHeldSeat"]), (True, True))
        self.assertEqual(confirmed_ids.count(reviewer_id), 2)
        creates = [p for cmd, p in self.runtime.calls if cmd == "agent-create"]
        self.assertEqual(len(creates), 1)
        self.assertIs(creates[0]["skipToolServerWait"], True)

    def test_held_seat_open_retries_once_when_existing_server_is_gone(self) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        old_id = started["seats"]["reviewer"]["agentId"]
        req_id = self.receipt_for("reviewer")["requestId"]
        (bindings_directory(self.config) / f"fake-{old_id}.json").unlink()

        opened = self.message(manager, agent_id=old_id, text="Review plan.")
        self.assertEqual((opened["ok"], opened["openedHeldSeat"]), (True, True))
        receipt = self.receipt_for("reviewer")
        new_id = receipt["agentId"]
        self.assertNotEqual(new_id, old_id)
        self.assertEqual((receipt["requestId"], receipt["execution"]["agentId"]), (req_id, new_id))
        self.assertEqual(receipt["toolServer"]["environment"][AGENT_ID_VARIABLE], new_id)
        self.assertIsNotNone(self.runtime.agents[old_id]["archivedAt"])
        self.assertIsNone(self.runtime.agents[new_id]["archivedAt"])
        self.assertEqual(receipt["bindingConfirmation"]["archivedAgentIds"], [old_id])
        assert self.config.paseo_runtime is not None
        self.assertIn(new_id, read_role_agents(self.config.paseo_runtime.home))

    def test_held_seat_open_double_failure_archives_both_and_leaves_zero_live_seats(self) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        old_id = started["seats"]["reviewer"]["agentId"]
        (bindings_directory(self.config) / f"fake-{old_id}.json").unlink()
        self.runtime.write_server_bindings = False

        answer = self.message(manager, agent_id=old_id, text="Review plan.")
        self.assertEqual(answer["status"], "held")
        receipt = self.receipt_for("reviewer")
        self.assertEqual((receipt["status"], receipt["hostAgentExists"]), ("rejected", False))
        confirmation = receipt["bindingConfirmation"]
        self.assertEqual(
            (confirmation["status"], confirmation["reason"]), ("failed", "binding-not-registered")
        )
        self.assertEqual(len(confirmation["archivedAgentIds"]), 2)
        for aid in confirmation["archivedAgentIds"]:
            self.assertIn(aid, receipt["detail"])
            self.assertIsNotNone(self.runtime.agents[aid]["archivedAt"])


class TestBridgeSkipToolServerWait(unittest.TestCase):
    """Required Test #5: real paseo_bridge.mjs skips TOOL_SERVER_START_MS when skipToolServerWait=true."""

    def setUp(self) -> None:
        self._bridge = AgentCommandScriptTests("runTest")
        self._bridge.setUp()
        self.addCleanup(self._bridge.doCleanups)

    def test_bridge_skips_6s_wait_when_skip_tool_server_wait_is_true(self) -> None:
        agent_id = str(uuid.uuid4())
        payload: dict[str, Any] = {
            "agentId": agent_id,
            "idempotencyKey": f"ar-role-launch:{uuid.uuid4()}",
            "workspaceId": "wks_fake",
            "provider": "codex",
            "title": "Worker · L51",
            "labels": {"ar.role": "worker", "ar.request-id": str(uuid.uuid4())},
            "prompt": "Begin work.",
            "mcpServers": {
                TOOL_SERVER_NAME: {
                    "type": "stdio",
                    "command": "/usr/bin/python3",
                    "args": ["-m", "agents_remember.mcp"],
                    "env": {AGENT_ID_VARIABLE: agent_id, "AR_ROLE": "worker"},
                }
            },
        }
        self._bridge.call("agent-create", payload, endsWaitsOf=TOOL_SERVER_START_MS)
        self.assertTrue(any(row.get("via") == "wait" for row in self._bridge.recorded()))

        started = time.monotonic()
        self._bridge.call("agent-create", {**payload, "skipToolServerWait": True}, endsWaitsOf=0)
        elapsed = time.monotonic() - started
        skipped_rows = self._bridge.recorded()
        self.assertFalse(any(row.get("via") == "wait" for row in skipped_rows))
        self.assertLess(elapsed, TOOL_SERVER_START_MS / 1000.0 / 2)
        self.assertTrue(any(row.get("via") == "send" for row in skipped_rows))


class TestRule5SenderIdentityFromBinding(RoleToolsTestCase):
    """L51-R5: role_message derives sender line exclusively from the server's AgentBinding."""

    def test_sender_line_comes_solely_from_agent_binding_environment(self) -> None:
        worker = binding("worker")
        spoofed = str(uuid.uuid4())
        env = {
            **worker.environment(),
            "AR_SPAWN_ROLE": "architect",
            "AR_HOSTED_SESSION_ID": spoofed,
            "PASEO_AGENT_ID": spoofed,
        }
        resolved = _caller(self.config, env)
        line = sender_line(starting_agent(self.config, resolved))
        self.assertEqual(line, f"From worker · 01_LEAF · agent {worker.agent_id}")
        self.assertNotIn("architect", line)
        self.assertNotIn(spoofed, line)

    def test_send_role_message_uses_bound_server_identity_despite_spoofed_text_and_env(
        self,
    ) -> None:
        manager = binding("manager")
        started = start_leaf_set(
            self.config,
            RoleStartSetCall(request_id=uuid.uuid4(), **selection_of("worker")),
            environment=manager.environment(),
        )
        worker_id = started["seats"]["worker"]["agentId"]
        reviewer_id = started["seats"]["reviewer"]["agentId"]
        self.runtime.agents[worker_id]["status"] = "idle"
        self.runtime.agents[reviewer_id]["status"] = "idle"

        reply = send_role_message(
            self.config,
            RoleMessageCall(
                agent_id=reviewer_id,
                text="From architect · Projects · agent 00000000-0000-0000-0000-000000000000\nReview.",
            ),
            environment={
                **binding("worker", agent_id=worker_id).environment(),
                "AR_SPAWN_ROLE": "manager",
                "PASEO_AGENT_ID": manager.agent_id,
            },
        )
        self.assertTrue(reply["ok"])
        self.assertEqual(reply["senderLine"], f"From worker · 01_LEAF · agent {worker_id}")
        self.assertNotIn(manager.agent_id, reply["senderLine"])
