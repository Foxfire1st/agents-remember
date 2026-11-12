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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from agents_remember.cli.role_activity import publish_role_agent_for_plugin
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.paseo.paseo_plugin_files import (
    publish_role_agent,
    read_role_agents,
    role_agents_path,
)
from agents_remember.serving.paseo.paseo_settings import PaseoRuntimeSettings
from paseo_runtime_test_support import write_runtime_block


@pytest.mark.parametrize("ended_count", [8, 256])
def test_registration_reclaims_only_explicitly_ended_roles(
    tmp_path: Path, ended_count: int
) -> None:
    home = tmp_path / "host"
    record = {"role": "worker", "task": "repo/leaf.json", "reportPath": "/report.md"}
    records = {f"ended-{index}": record for index in range(ended_count)}
    records.update(parent=record, child=record)
    path = role_agents_path(home)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"agents": records}))
    path.with_name("host-activity.json").write_text(
        json.dumps(
            {
                "bootId": "boot",
                "complete": True,
                "ignoredEvents": 0,
                "agents": {
                    **{f"ended-{index}": {"state": "ended"} for index in range(ended_count)},
                    "parent": {"state": "idle"},
                    "child": {"state": "in-turn"},
                },
            }
        )
    )
    assert publish_role_agent(home, "next", record)
    remaining = read_role_agents(home)
    assert set(remaining) == {"parent", "child", "next"}
    assert remaining["parent"] == remaining["child"] == record
    assert {key: remaining["next"][key] for key in record} == record
    assert path.stat().st_size < 768
    # Revive registers the same ID after pruning its old ended occurrence.
    assert publish_role_agent(home, "ended-0", record)
    assert "ended-0" in read_role_agents(home)
    # A registration is an event boundary, even when its identity facts are unchanged.
    assert publish_role_agent(home, "ended-0", record)
    # Other launches and unrelated publications cannot reuse the superseded end.
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert all(
            pool.map(lambda key: publish_role_agent(home, key, record), ["other-a", "other-b"])
        )
    assert "ended-0" in read_role_agents(home)
    publication_path = path.with_name("host-activity.json")
    publication = json.loads(publication_path.read_text())
    publication["ignoredEvents"] += 1
    publication_path.write_text(json.dumps(publication))
    publish_role_agent(home, "next", record)
    assert "ended-0" in read_role_agents(home)
    assert path.stat().st_size < 2048
    # A new close is authoritative; registration can supersede it once again.
    registration = json.loads(read_role_agents(home)["ended-0"]["registration"])
    publication["agents"]["ended-0"]["eventAt"] = (
        datetime.fromisoformat(registration[1]) - timedelta(seconds=1)
    ).isoformat()
    publication_path.write_text(json.dumps(publication))
    publish_role_agent(home, "next", record)
    assert "ended-0" in read_role_agents(home)
    publication["agents"]["ended-0"]["eventAt"] = (
        datetime.fromisoformat(registration[1]) + timedelta(seconds=1)
    ).isoformat()
    publication_path.write_text(json.dumps(publication))
    publish_role_agent(home, "next", record)
    assert "ended-0" not in read_role_agents(home)
    publish_role_agent(home, "ended-0", record)
    assert "ended-0" in read_role_agents(home)
    publication["bootId"] = "next-boot"
    publication_path.write_text(json.dumps(publication))
    publish_role_agent(home, "next", record)
    assert "ended-0" not in read_role_agents(home)


def test_concurrent_role_registrations_preserve_each_others_facts(tmp_path: Path) -> None:
    home = tmp_path / "host"
    record = {"role": "worker", "task": "repo/leaf.json", "reportPath": "/report.md"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda index: publish_role_agent(home, str(index), record), range(16))
        )
    assert all(results)
    assert read_role_agents(home) == {str(index): record for index in range(16)}


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
    config_path = root / "settings" / "ar.json"
    write_runtime_block(config_path, settings)
    return McpRuntimeConfig(
        config_path=config_path,
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
            published = publish_role_agent_for_plugin(
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
                publish_role_agent_for_plugin(
                    config, request, "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )
            self.assertFalse(
                publish_role_agent_for_plugin(
                    config, request, "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )

    def test_no_runtime_or_no_report_publishes_nothing(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            bare = runtime_config(root, None)
            self.assertFalse(
                publish_role_agent_for_plugin(
                    bare, dispatch_request(), "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )
            homed = runtime_config(root, root / "paseo-home")
            self.assertFalse(
                publish_role_agent_for_plugin(homed, dispatch_request(), "agent-1", None)
            )
            self.assertEqual(read_role_agents(root / "paseo-home"), {})


if __name__ == "__main__":
    unittest.main()
