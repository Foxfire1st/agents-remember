"""MIK-R117 rule 11: the role start publishes per-agent facts for the plugin.

The plugin wakes a role agent's parent from the record the start published
at ``<PASEO_HOME>/agents-remember/role-agents.json``. These tests cover the
publish side: the file shape, the idempotent republish, the incomplete
record, and the hook in the start answer (best-effort, never failing).
"""

from __future__ import annotations

import json
import unittest
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from agents_remember.cli.paseo_role_tools import _publish_role_agent_for_plugin
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.paseo.paseo_plugin_files import (
    publish_role_agent,
    read_role_agents,
    role_agents_path,
)
from agents_remember.serving.paseo.paseo_settings import PaseoRuntimeSettings
from paseo_runtime_test_support import write_shared_runtime


def runtime_config(root: Path, home: Path | None) -> McpRuntimeConfig:
    settings = (
        PaseoRuntimeSettings(
            install_prefix=root / "prefix",
            home=home,
            listen="127.0.0.1:6820",
            providers={},
            embed=(),
        )
        if home is not None
        else None
    )
    write_shared_runtime(root, settings)
    return McpRuntimeConfig(
        config_path=root / "settings" / "ar.json",
        coordination_root=root / "coordination",
        workspace_root=root / "projects",
        transcript_root=root / "coordination" / "logs" / "mcp",
    )


def dispatch_request() -> RoleDispatchRequest:
    return RoleDispatchRequest(
        role="worker",
        requestId=uuid.uuid4(),
        taskDocumentRef=TaskDocumentRef(repository="agents-remember", path="master/01_leaf.json"),
    )


class RoleAgentPublishTests(unittest.TestCase):
    def test_publish_then_read_round_trips_one_record(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw) / "paseo-home"
            record = {
                "role": "worker",
                "task": "agents-remember:master/01_leaf.json",
                "reportPath": "/notes/reports/worker/aaa/report.md",
            }
            self.assertTrue(publish_role_agent(home, "agent-1", record))
            self.assertEqual(read_role_agents(home), {"agent-1": record})
            document = json.loads(role_agents_path(home).read_text())
            self.assertEqual(document["agents"]["agent-1"], record)

    def test_republish_of_the_same_record_writes_nothing(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw) / "paseo-home"
            record = {
                "role": "worker",
                "task": "agents-remember:master/01_leaf.json",
                "reportPath": "/notes/reports/worker/aaa/report.md",
            }
            self.assertTrue(publish_role_agent(home, "agent-1", record))
            before = role_agents_path(home).stat().st_mtime_ns
            self.assertFalse(publish_role_agent(home, "agent-1", record))
            self.assertEqual(role_agents_path(home).stat().st_mtime_ns, before)

    def test_publish_merges_without_dropping_other_agents(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw) / "paseo-home"
            first = {"role": "worker", "task": "t", "reportPath": "/a.md"}
            second = {"role": "manager", "task": "t", "reportPath": "/b.md"}
            publish_role_agent(home, "agent-1", first)
            publish_role_agent(home, "agent-2", second)
            self.assertEqual(read_role_agents(home), {"agent-1": first, "agent-2": second})

    def test_read_skips_incomplete_records_and_missing_files(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw) / "paseo-home"
            self.assertEqual(read_role_agents(home), {})
            role_agents_path(home).parent.mkdir(parents=True, exist_ok=True)
            role_agents_path(home).write_text(
                json.dumps(
                    {
                        "agents": {
                            "whole": {
                                "role": "worker",
                                "task": "t",
                                "reportPath": "/a.md",
                            },
                            "half": {"role": "worker"},
                        }
                    }
                )
            )
            self.assertEqual(
                read_role_agents(home),
                {
                    "whole": {
                        "role": "worker",
                        "task": "t",
                        "reportPath": "/a.md",
                    }
                },
            )


class RoleStartPublishesForPluginTests(unittest.TestCase):
    def test_a_start_publishes_role_task_and_report(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = runtime_config(root, root / "paseo-home")
            published = _publish_role_agent_for_plugin(
                config, dispatch_request(), "agent-1", "/notes/reports/worker/aaa/report.md"
            )
            self.assertTrue(published)
            self.assertEqual(
                read_role_agents(root / "paseo-home"),
                {
                    "agent-1": {
                        "role": "worker",
                        "task": "agents-remember/master/01_leaf.json",
                        "reportPath": "/notes/reports/worker/aaa/report.md",
                    }
                },
            )

    def test_a_repeat_start_publishes_nothing_new(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            config = runtime_config(root, root / "paseo-home")
            request = dispatch_request()
            self.assertTrue(
                _publish_role_agent_for_plugin(
                    config, request, "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )
            self.assertFalse(
                _publish_role_agent_for_plugin(
                    config, request, "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )

    def test_no_runtime_or_no_report_publishes_nothing(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            bare = runtime_config(root, None)
            self.assertFalse(
                _publish_role_agent_for_plugin(
                    bare, dispatch_request(), "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )
            homed = runtime_config(root, root / "paseo-home")
            self.assertFalse(
                _publish_role_agent_for_plugin(homed, dispatch_request(), "agent-1", None)
            )
            self.assertEqual(read_role_agents(root / "paseo-home"), {})


if __name__ == "__main__":
    unittest.main()
