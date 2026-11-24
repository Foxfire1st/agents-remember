"""MIK-R117 rule 11: the role start publishes per-agent facts for the plugin.

The plugin wakes a role agent's parent from the record the start published
at ``<PASEO_HOME>/agents-remember/role-agents.json``. These tests cover the
publish side: the file shape, the registration boundary, the incomplete
record, and the hook in the start answer (best-effort, never failing).
"""

from __future__ import annotations

import json
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from agents_remember.cli import role_registry_recovery as recovery
from agents_remember.cli.role_activity import publish_role_agent_for_plugin
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA
from agents_remember.controlplane.held_store_discovery import EXECUTIONS_DIRECTORY
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.role_launcher import RoleDispatchRequest
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.serving.paseo.paseo_plugin_files import (
    RoleAgentReconciliation,
    publish_role_agent,
    read_role_agents,
    reconcile_role_agents,
    role_agents_path,
)
from agents_remember.serving.paseo.paseo_settings import PaseoRuntimeSettings
from agents_remember.tasks.document import TaskDocument
from paseo_runtime_test_support import write_runtime_block


@pytest.mark.parametrize("ended_count", [8, 256])
def test_registration_reclaims_only_explicitly_ended_roles(
    tmp_path: Path, ended_count: int
) -> None:
    home = tmp_path / "host"
    record = {"role": "worker", "task": "repo/leaf.json", "reportPath": "/report.md"}
    old = {**record, "registration": json.dumps(["old-boot", "2026-10-01T00:00:00Z"])}
    records = {f"ended-{index}": old for index in range(ended_count)}
    records.update(parent=record, child=record, closed=record, unlisted=record)
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
                    **{
                        f"ended-{index}": {
                            "state": "ended",
                            "archivedAt": "2026-10-02T00:00:00Z",
                        }
                        for index in range(ended_count)
                    },
                    "parent": {"state": "idle"},
                    "child": {"state": "in-turn"},
                    "closed": {"state": "closed"},
                },
            }
        )
    )
    assert publish_role_agent(home, "next", record)
    remaining = read_role_agents(home)
    assert set(remaining) == {"parent", "child", "closed", "unlisted", "next"}
    assert remaining["parent"] == remaining["child"] == record
    assert {key: remaining["next"][key] for key in record} == record
    assert path.stat().st_size < 1024
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
    # Archive age, across boots, is what establishes the order.
    registration = json.loads(read_role_agents(home)["ended-0"]["registration"])
    publication["bootId"] = "next-boot"
    publication["agents"]["ended-0"]["archivedAt"] = (
        datetime.fromisoformat(registration[1]) - timedelta(seconds=1)
    ).isoformat()
    publication_path.write_text(json.dumps(publication))
    publish_role_agent(home, "next", record)
    assert "ended-0" in read_role_agents(home)
    publication["agents"]["ended-0"]["archivedAt"] = (
        datetime.fromisoformat(registration[1]) + timedelta(seconds=1)
    ).isoformat()
    publication_path.write_text(json.dumps(publication))
    publish_role_agent(home, "next", record)
    assert "ended-0" not in read_role_agents(home)


def test_registration_keeps_a_legacy_end_without_archive_evidence(tmp_path: Path) -> None:
    home = tmp_path / "host"
    record = {"role": "worker", "task": "repo/leaf.json", "reportPath": "/report.md"}
    path = role_agents_path(home)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"agents": {"closed": record, "unlisted": record}}))
    path.with_name("host-activity.json").write_text(
        json.dumps(
            {
                "bootId": "previous-build",
                "complete": True,
                "ignoredEvents": 0,
                "agents": {"closed": {"state": "ended", "eventAt": "2026-11-23T12:00:00Z"}},
            }
        )
    )

    assert publish_role_agent(home, "next", record)
    assert set(read_role_agents(home)) == {"closed", "unlisted", "next"}


def test_concurrent_role_registrations_preserve_each_others_facts(tmp_path: Path) -> None:
    home = tmp_path / "host"
    record = {"role": "worker", "task": "repo/leaf.json", "reportPath": "/report.md"}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(
            pool.map(lambda index: publish_role_agent(home, str(index), record), range(16))
        )
    assert all(results)
    agents = read_role_agents(home)
    assert set(agents) == {str(index) for index in range(16)}
    assert all(_facts(value) == record for value in agents.values())


def test_reconciliation_preserves_registrations_made_during_host_reads(tmp_path: Path) -> None:
    home = tmp_path / "host"
    original = {"role": "worker", "task": "repo/leaf.json", "reportPath": "/report.md"}
    fresh = {**original, "reportPath": "/fresh.md"}
    for agent_id in ("gone", "archive", "refreshed", "unjudged"):
        publish_role_agent(home, agent_id, original)
    baseline = read_role_agents(home)
    observed_at = datetime.now(UTC)
    archived_at = datetime.now(UTC) + timedelta(seconds=1)

    publish_role_agent(home, "refreshed", original)
    publish_role_agent(home, "concurrent", fresh)
    assert reconcile_role_agents(
        home,
        RoleAgentReconciliation(
            baseline=baseline,
            recovered={"recovered": original, "concurrent": original},
            absent={"gone", "refreshed"},
            archived={"archive": archived_at},
            observed_at=observed_at,
        ),
    )

    agents = read_role_agents(home)
    assert set(agents) == {"recovered", "refreshed", "unjudged", "concurrent"}
    assert _facts(agents["recovered"]) == original
    assert json.loads(agents["recovered"]["registration"]) == [None, observed_at.isoformat()]
    assert _facts(agents["concurrent"]) == fresh
    assert agents["unjudged"] == baseline["unjudged"]
    assert agents["refreshed"]["registration"] != baseline["refreshed"]["registration"]


def _facts(record: dict[str, str]) -> dict[str, str]:
    return {key: record[key] for key in ("role", "task", "reportPath")}


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
            saved = read_role_agents(home)["agent-1"]
            self.assertEqual(_facts(saved), record)
            document = json.loads(role_agents_path(home).read_text())
            self.assertEqual(document, {"agents": {"agent-1": saved}})
            boot_id, registered_at = json.loads(saved["registration"])
            self.assertIsNone(boot_id)
            self.assertIsNotNone(datetime.fromisoformat(registered_at).tzinfo)

    def test_republish_stamps_a_new_registration_before_any_activity(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw) / "paseo-home"
            record = {
                "role": "worker",
                "task": "agents-remember:master/01_leaf.json",
                "reportPath": "/notes/reports/worker/aaa/report.md",
            }
            self.assertTrue(publish_role_agent(home, "agent-1", record))
            before = read_role_agents(home)["agent-1"]["registration"]
            self.assertTrue(publish_role_agent(home, "agent-1", record))
            self.assertNotEqual(read_role_agents(home)["agent-1"]["registration"], before)

    def test_publish_merges_without_dropping_other_agents(self) -> None:
        with TemporaryDirectory() as raw:
            home = Path(raw) / "paseo-home"
            first = {"role": "worker", "task": "t", "reportPath": "/a.md"}
            second = {"role": "manager", "task": "t", "reportPath": "/b.md"}
            publish_role_agent(home, "agent-1", first)
            publish_role_agent(home, "agent-2", second)
            self.assertEqual(
                {key: _facts(record) for key, record in read_role_agents(home).items()},
                {"agent-1": first, "agent-2": second},
            )

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
                _facts(read_role_agents(root / "paseo-home")["agent-1"]),
                {
                    "role": "worker",
                    "task": "agents-remember/master/01_leaf.json",
                    "reportPath": "/notes/reports/worker/aaa/report.md",
                },
            )

    def test_a_repeat_start_preserves_one_identity_and_refreshes_its_registration(self) -> None:
        with TemporaryDirectory() as raw:
            root = Path(raw)
            home = root / "paseo-home"
            config = runtime_config(root, home)
            request = dispatch_request()
            self.assertTrue(
                publish_role_agent_for_plugin(
                    config, request, "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )
            before = read_role_agents(home)["agent-1"]
            self.assertTrue(
                publish_role_agent_for_plugin(
                    config, request, "agent-1", "/notes/reports/worker/aaa/report.md"
                )
            )
            agents = read_role_agents(home)
            self.assertEqual(set(agents), {"agent-1"})
            self.assertEqual(_facts(agents["agent-1"]), _facts(before))
            self.assertNotEqual(agents["agent-1"]["registration"], before["registration"])

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


HEALED = "00000000-0000-4000-8000-000000000001"
FINISHED = "00000000-0000-4000-8000-000000000002"
ACTIVE = "00000000-0000-4000-8000-000000000003"
PENDING = "00000000-0000-4000-8000-000000000004"
GONE = "00000000-0000-4000-8000-000000000005"
UNRECORDED = "00000000-0000-4000-8000-000000000006"
UNAVAILABLE = "00000000-0000-4000-8000-000000000007"
ROLE = {"role": "worker", "task": "repo/master/leaf.json", "reportPath": "/from-receipt.md"}


def _agent(agent_id: str) -> dict[str, str | None]:
    # The role on the host is a discovery hint. Only a launch receipt supplies the facts.
    return {"id": agent_id, "archivedAt": None, "role": "manager"}


def _host_view(*agents: str) -> dict[str, object]:
    return {"serverId": "host", "complete": True, "agents": [_agent(agent) for agent in agents]}


def _receipt(
    config: McpRuntimeConfig, agent_id: str, slug: str, *, status: str = "running"
) -> None:
    directory = config.coordination_root / "notes" / "reports" / EXECUTIONS_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": RECEIPT_SCHEMA,
        "agentId": agent_id,
        "status": status,
        "selection": {
            "role": "worker",
            "taskDocumentRef": {"repository": "repo", "path": f"master/{slug}.json"},
        },
        "report": {"path": "/from-receipt.md"},
    }
    (directory / f"{agent_id}.json").write_text(json.dumps(payload))


def _publication(home: Path, boot: str) -> None:
    path = role_agents_path(home).with_name("host-activity.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"bootId": boot, "complete": False, "agents": {}, "ignoredEvents": 0})
    )


def test_dashboard_heals_from_receipts_before_snapshot_and_does_not_probe_finished_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, "first")
    for agent_id, slug in [(HEALED, "leaf"), (FINISHED, "done"), (ACTIVE, "open")]:
        _receipt(config, agent_id, slug)
    for agent_id in (PENDING, GONE, UNAVAILABLE):
        publish_role_agent(home, agent_id, ROLE)
    _receipt(config, PENDING, "pending", status="starting")

    def task_doc(path: Path) -> TaskDocument:
        return TaskDocument.model_construct(
            status="Completed" if path.stem == "done" else "inProgress"
        )

    monkeypatch.setattr(recovery, "read_task_doc", task_doc)
    calls: list[str] = []

    def bridge(
        _config: McpRuntimeConfig, command: str, payload: dict[str, object]
    ) -> dict[str, object]:
        calls.append(command)
        if command == "host-activity-snapshot":
            registered = read_role_agents(home)
            assert set(registered) == {HEALED, ACTIVE, PENDING, UNAVAILABLE}
            assert {key: registered[HEALED][key] for key in ROLE} == ROLE
            return {"serverId": "host", "publication": {"bootId": "first"}}
        if not payload:
            return _host_view(HEALED, UNRECORDED)
        agent_ids = payload["agentIds"]
        assert isinstance(agent_ids, list)
        assert set(agent_ids) == {ACTIVE, GONE, PENDING, UNAVAILABLE}
        return {
            **_host_view(ACTIVE, UNAVAILABLE),
            "absent": [GONE, PENDING],
            "exactReads": 4,
        }

    monkeypatch.setattr(recovery, "bridge_call", bridge)
    answer = recovery.RoleRegistryActivityReader()(config)
    assert calls == ["role-registry", "role-registry", "host-activity-snapshot"]
    assert answer["registryRecovery"]["exactReads"] == 4
    assert answer["registryRecovery"]["unjudgedAgents"] == 1


def test_dashboard_does_not_read_receipts_without_recovery_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, "one")
    publish_role_agent(home, HEALED, ROLE)

    def no_receipts(_config: McpRuntimeConfig) -> None:
        pytest.fail(
            "a pass with neither a missing receipt-backed seat nor an exact null reads no receipts"
        )

    monkeypatch.setattr(recovery, "_line_receipts", no_receipts)
    commands: list[str] = []
    boot = "one"

    def bridge(
        _config: McpRuntimeConfig, command: str, payload: dict[str, object]
    ) -> dict[str, object]:
        commands.append(command)
        assert payload == {}
        return (
            _host_view(HEALED)
            if command == "role-registry"
            else {"serverId": "host", "publication": {"bootId": boot}}
        )

    monkeypatch.setattr(recovery, "bridge_call", bridge)
    reader = recovery.RoleRegistryActivityReader()
    assert reader(config)["registryRecovery"]["exactReads"] == 0
    assert "registryRecovery" not in reader(config)
    boot = "two"
    _publication(home, boot)
    assert reader(config)["registryRecovery"]["exactReads"] == 0
    assert commands == [
        "role-registry",
        "host-activity-snapshot",
        "host-activity-snapshot",
        "role-registry",
        "host-activity-snapshot",
    ]


@pytest.mark.parametrize("exact_status", ["complete", "cut", "malformed"])
def test_exact_archive_requires_a_complete_usable_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exact_status: str
) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish_role_agent(home, GONE, ROLE)
    publish_role_agent(home, PENDING, ROLE)
    archived = {
        **_agent(GONE),
        "archivedAt": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
    }

    def bridge(
        _config: McpRuntimeConfig, command: str, payload: dict[str, object]
    ) -> dict[str, object]:
        assert command == "role-registry"
        if not payload:
            return _host_view()
        assert payload == {"agentIds": [PENDING, GONE]}
        return {
            **_host_view(PENDING),
            "agents": [_agent(PENDING), archived],
            "absent": [],
            "exactReads": 2,
            "complete": exact_status == "complete" if exact_status != "malformed" else "false",
        }

    monkeypatch.setattr(recovery, "bridge_call", bridge)
    result = recovery.recover_role_registry(config)
    if exact_status == "complete":
        assert result.unjudgedAgents == 0
        assert set(read_role_agents(home)) == {PENDING}
    else:
        assert result.unjudgedAgents == 2
        assert set(read_role_agents(home)) == {GONE, PENDING}


def test_exact_registry_reads_require_canonical_agent_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    for agent_id in ("", GONE[:12], GONE):
        publish_role_agent(home, agent_id, ROLE)
    lookups: list[object] = []

    def bridge(
        _config: McpRuntimeConfig, command: str, payload: dict[str, object]
    ) -> dict[str, object]:
        assert command == "role-registry"
        if not payload:
            return _host_view()
        lookups.append(payload["agentIds"])
        return {**_host_view(GONE), "absent": [], "exactReads": 1}

    monkeypatch.setattr(recovery, "bridge_call", bridge)
    assert recovery.recover_role_registry(config).unjudgedAgents == 2
    assert lookups == [[GONE]]
