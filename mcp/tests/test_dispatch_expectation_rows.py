"""Atomic write-with-dispatch tests (R2, 260707-HFX2-L1): every named dispatch surface -- spawn,
gate open, signal post -- writes its durable expectation row in the SAME call, never a forgettable
follow-up step."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane.expectation_rows import ExpectationRowStore
from agents_remember.mcp.config import McpRuntimeConfig
from agents_remember.mcp.tools import gates as gate_tools
from agents_remember.mcp.tools import operator_inbox as inbox_tools
from agents_remember.mcp.tools.terminal import spawn_agent_session_payload
from agents_remember.observer import observer_root, reset_ambient
from agents_remember.tasks import TaskDocument, write_task_doc
from test_spawn_agent_session import _FakeHost, _FakePaster


def _config(root: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=root / "settings.json",
        coordination_root=root,
        workspace_root=root,
        transcript_root=root / "logs" / "mcp",
    )


def _detected(_command: str) -> str | None:
    return "/usr/bin/harness"


def _write_leaf_task(coordination_root: Path) -> None:
    task_root = coordination_root / "tasks" / "repo" / "master"
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "MASTER",
                "slug": "task",
                "title": "Master",
                "kind": "master",
                "repo": "repo",
                "createdAt": "2026-07-07T10:00",
                "subTasks": [
                    {
                        "number": "leaf-1",
                        "name": "Leaf",
                        "file": "leaf-1.md",
                        "status": "inProgress",
                    }
                ],
            }
        ),
    )
    write_task_doc(
        task_root,
        TaskDocument.model_validate(
            {
                "id": "leaf-1",
                "slug": "leaf-1",
                "title": "Leaf",
                "kind": "subTask",
                "repo": "repo",
                "createdAt": "2026-07-07T10:01",
                "master": "task.md",
            }
        ),
    )


class SpawnExpectationRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _config(self.tmp)
        _write_leaf_task(self.tmp)
        self.host = _FakeHost()
        reset_ambient()

    def tearDown(self) -> None:
        reset_ambient()

    def test_spawn_starts_no_assignment_clocks(self) -> None:
        payload = spawn_agent_session_payload(
            self.config,
            session_id="worker-1",
            leaf_key="repo/master/leaf-1",
            env={"AR_SPAWN_ROLE": "worker"},
            host=self.host,  # type: ignore[arg-type]
            which=_detected,
            paster=_FakePaster(),  # type: ignore[arg-type]
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(ExpectationRowStore(observer_root(self.config)).pending(), [])

    def test_a_bare_command_chat_gets_no_assignment_clock(self) -> None:
        payload = spawn_agent_session_payload(
            self.config,
            session_id="chat-1",
            host=self.host,  # type: ignore[arg-type]
            which=_detected,
        )
        self.assertEqual(payload["status"], "spawned-unbriefed")
        self.assertEqual(ExpectationRowStore(observer_root(self.config)).pending(), [])


class GateExpectationRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _config(self.tmp)

    def test_gate_create_writes_a_verdict_by_row(self) -> None:
        created = gate_tools.gate_create_payload(
            self.config,
            kind="plan-approval",
            lifecycle_id="L1",
        )
        rows = ExpectationRowStore(observer_root(self.config)).pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "verdict-by")
        self.assertEqual(rows[0].sourceId, created["gateId"])

    def test_gate_decide_meets_the_verdict_by_row(self) -> None:
        created = gate_tools.gate_create_payload(
            self.config,
            kind="plan-approval",
            lifecycle_id="L1",
        )
        gate_tools.gate_decide_payload(
            self.config,
            gate_id=created["gateId"],
            lifecycle_id="L1",
            decision="approve",
            decided_by="developer",
            decided_via="dashboard",
        )
        rows = ExpectationRowStore(observer_root(self.config)).current()
        self.assertEqual(len(rows), 1)
        row = next(iter(rows.values()))
        self.assertEqual(row.state, "met")


class InboxExpectationRowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.config = _config(self.tmp)

    def test_post_writes_an_ack_by_row(self) -> None:
        posted = inbox_tools.operator_inbox_post_payload(
            self.config,
            lifecycle_id="L1",
            agent_id="agent-a",
            ask="Continue?",
            response="Yes.",
            created_by="developer",
            created_via="dashboard",
            recipient_role="worker",
            deliver_to_hosted=False,
        )
        rows = ExpectationRowStore(observer_root(self.config)).pending()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].kind, "ack-by")
        self.assertEqual(rows[0].sourceId, posted["entryId"])

    def test_consume_meets_the_ack_by_row(self) -> None:
        posted = inbox_tools.operator_inbox_post_payload(
            self.config,
            lifecycle_id="L1",
            agent_id="agent-a",
            ask="Continue?",
            response="Yes.",
            created_by="developer",
            created_via="dashboard",
            recipient_role="worker",
            deliver_to_hosted=False,
        )
        inbox_tools.operator_inbox_consume_payload(
            self.config,
            entry_id=posted["entryId"],
            consumed_by="model",
            consumed_via="cli",
        )
        rows = ExpectationRowStore(observer_root(self.config)).current()
        self.assertEqual(len(rows), 1)
        row = next(iter(rows.values()))
        self.assertEqual(row.state, "met")


if __name__ == "__main__":
    unittest.main()
