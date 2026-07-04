"""Tests for orchestration communication helpers."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

MCP_SRC = Path(__file__).resolve().parents[1] / "src"
MCP_TESTS = Path(__file__).resolve().parent
sys.path.insert(0, str(MCP_SRC))
sys.path.insert(0, str(MCP_TESTS))

from agents_remember.controlplane.orchestration_artifacts import (
    escalation_packet,
    turn_report_artifact,
)
from agents_remember.controlplane.orchestration_nudges import (
    OrchestrationNudgeRecord,
    OrchestrationNudgeStore,
    missing_artifact,
)
from agents_remember.mcp.config import McpRuntimeConfig, load_config
from agents_remember.mcp.tools.orchestration import orchestration_nudge_manager_payload
from agents_remember.observer import observer_root
from agents_remember.observer.store import EventStore
from test_config import settings_payload


def _config(root: Path) -> McpRuntimeConfig:
    path = root / "mcp-settings.json"
    path.write_text(json.dumps(settings_payload(root)), encoding="utf-8")
    return load_config(path)


class ArtifactHelperTests(unittest.TestCase):
    def test_turn_report_artifact_uses_series_notes_reports(self) -> None:
        artifact = turn_report_artifact(
            Path("/tasks/series"),
            "260703-L3",
            "Agent comms",
            Path("/runtime"),
        )
        self.assertEqual(artifact.path, "/tasks/series/notes/reports/260703-L3-worker-report.md")
        self.assertEqual(
            artifact.templatePath,
            "/runtime/skills/l-02-agent-orchestration/templates/turn-report.md",
        )

    def test_escalation_moves_one_rung_only(self) -> None:
        packet = escalation_packet(
            from_role="worker",
            reason="blocked",
            subject="260703-L3",
            summary="Cannot prove push delivery.",
        )
        self.assertEqual(packet.toRole, "manager")


class NudgePolicyTests(unittest.TestCase):
    def test_missing_artifact_flags_absent_or_empty_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            report = Path(tmp_dir) / "report.md"
            self.assertTrue(missing_artifact(report))
            report.write_text("", encoding="utf-8")
            self.assertTrue(missing_artifact(report))
            report.write_text("done", encoding="utf-8")
            self.assertFalse(missing_artifact(report))

    def test_store_rate_limits_same_target_subject_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = OrchestrationNudgeStore(Path(tmp_dir))
            first = store.record(
                OrchestrationNudgeRecord(
                    id="N1",
                    ts="2026-07-04T10:00:00+00:00",
                    state="sent",
                    reason="inactive",
                    targetAgentId="manager",
                    subjectAgentId="worker",
                    message="nudge",
                ),
                rate_limit_seconds=900,
            )
            second = store.record(
                OrchestrationNudgeRecord(
                    id="N2",
                    ts="2026-07-04T10:05:00+00:00",
                    state="sent",
                    reason="inactive",
                    targetAgentId="manager",
                    subjectAgentId="worker",
                    message="nudge",
                ),
                rate_limit_seconds=900,
            )
            self.assertEqual(first.state, "sent")
            self.assertEqual(second.state, "rate-limited")


class NudgeToolTests(unittest.TestCase):
    def test_nudge_payload_records_event_and_queues_inbox_message(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            config = _config(root)
            payload = orchestration_nudge_manager_payload(
                config,
                reason="missing-turn-report",
                subject="worker L3",
                manager_agent_id="manager-a",
                subject_agent_id="worker-a",
                artifact_path="notes/reports/L3-worker-report.md",
            )

            self.assertTrue(payload["ok"])
            self.assertEqual(payload["status"], "sent")
            self.assertEqual(payload["deliveryState"], "no-hosted-session")
            events = EventStore(observer_root(config)).read(None)
            self.assertEqual(events[0].kind, "orchestration.nudge")
            self.assertEqual(events[0].data["reason"], "missing-turn-report")


if __name__ == "__main__":
    unittest.main()
