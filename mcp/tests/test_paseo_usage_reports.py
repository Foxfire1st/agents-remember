"""The bridge reads Paseo's usage reports as the pinned SDK answers them.

The real pinned client answers ``listUsageReports`` with a ``{requestId, reports}``
envelope, and it refuses a call that selects both by ``agentId`` and by ``reportIds``
("agentId and reportIds cannot be combined"). The finding this file guards
(L184-R1-F01) was a bridge that read the answer as a bare array and refreshed with both
selectors: the envelope read as no entries at all, and the refresh could never run.

The fake client below keeps the pinned shape and the SDK's own refusal, so a bridge that
drifts from either fails here rather than on a live host.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

from agents_remember.cli.paseo_bridge import bridge_call
from agents_remember.cli.provider_limit_hold import provider_limit_hold, read_usage_for_agents
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving.paseo.paseo_plugin_files import publish_role_agent
from agents_remember.serving.projections.host_activity import UsageReadBatch
from paseo_runtime_test_support import write_runtime_block

SERVER_ID = "srv_configured"
WORKER = "f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607"
REVIEWER = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"
MANAGER = "e3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607"
STAMPED_MANAGER = "a3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607"


# Timestamps are taken when a case runs, never at import (MIK-R206 item 1). The
# bridge judges freshness against its own clock when the case calls it, so a stamp
# frozen at import ages by however long the module sat imported before the case
# ran and turns a fresh entry stale — the whole-lane failure. The 60-second
# production threshold and the fresh/stale contrast are unchanged.
def _stamp(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def _fresh() -> str:
    return _stamp(datetime.now(UTC))


def _stale() -> str:
    return _stamp(datetime.now(UTC) - timedelta(minutes=5))


def _reset() -> str:
    return _stamp(datetime.now(UTC) + timedelta(hours=2))


FAKE_DAEMON_CLIENT = """
import { appendFileSync, readFileSync } from 'node:fs'
export const scenario = JSON.parse(readFileSync(process.env.FAKE_PASEO_SCENARIO, 'utf8'))
function record(call) {
  appendFileSync(process.env.FAKE_PASEO_RECORD, JSON.stringify(call) + '\\n')
}
export class DaemonClient {
  constructor(config) { this.config = config; this.state = { status: 'idle' } }
  async connect() {
    if (this.config.url !== scenario.url) throw new Error('unexpected url ' + this.config.url)
    this.state = { status: 'connected' }
  }
  async close() { this.state = { status: 'disposed' } }
  getConnectionState() { return this.state }
  getLastServerInfoMessage() { return { serverId: scenario.serverId, version: this.config.appVersion } }
  // The pinned shape (0.12.0 to 0.16.2): {requestId, reports}, and the SDK's own selector refusal.
  async listUsageReports(options = {}) {
    record({ via: 'listUsageReports', options })
    if (options.agentId !== undefined && options.reportIds !== undefined) {
      throw new Error('agentId and reportIds cannot be combined')
    }
    if (options.reportIds !== undefined) {
      const failure = options.reportIds.map((id) => scenario.refreshErrors?.[id]).find(Boolean)
      if (failure) throw new Error(failure)
      const reports = options.reportIds
        .map((id) => scenario.refresh && scenario.refresh[id])
        .filter((entry) => entry !== undefined && entry !== null)
        .map((entry) => ({ ...entry, fetchedAt: scenario.refreshedAt ?? new Date().toISOString() }))
      return { requestId: 'req-refresh', reports }
    }
    const agentId = options.agentId
    const failure = scenario.errors && scenario.errors[agentId]
    if (failure) throw new Error(failure)
    return { requestId: 'req-cached', reports: (scenario.entries && scenario.entries[agentId]) || [] }
  }
}
"""
FAKE_CLIENT_ROOT = """
import { DaemonClient, scenario } from './daemon-client.js'
export { DaemonClient }
export function createPaseoApi() {
  return { dispose: async () => {}, config: { get: async () => ({ requestId: 'r', config: {} }) } }
}
"""


def _entry(
    report_id: str,
    *,
    fetched_at: str | None = None,
    used: float = 100.0,
    status: str = "available",
) -> dict[str, Any]:
    fetched_at = _stale() if fetched_at is None else fetched_at
    report: dict[str, Any] = {"status": status}
    if status == "available":
        report["windows"] = [
            {
                "id": "five_hour",
                "label": "Session",
                "summary": True,
                "usedPct": used,
                "remainingPct": 100.0 - used,
                "resetsAt": _reset(),
            }
        ]
    else:
        report["error"] = "Rate limited by Claude"
    return {
        "id": report_id,
        "account": {"label": "Max 20x", "email": "nobody@example.invalid"},
        "sourceId": "claude",
        "sourceLabel": "Claude",
        "fetchedAt": fetched_at,
        "report": report,
    }


@unittest.skipUnless(shutil.which("node"), "the bridge script needs Node.js")
class UsageReportsScriptTests(unittest.TestCase):
    """The real script against a fake client with the pinned envelope and selectors."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        settings = parse_paseo_runtime_settings(
            {
                "installPrefix": (self.root / "prefix").as_posix(),
                "home": (self.root / "home").as_posix(),
                "listen": "127.0.0.1:6835",
                "version": PASEO_VERSION,
                "providers": {},
                "embed": [],
            }
        )
        assert settings is not None
        settings.home.mkdir(parents=True)
        (settings.home / "server-id").write_text(SERVER_ID + "\n", encoding="utf-8")
        write_runtime_block(self.root / "settings" / "mcp.json", settings)
        self.config = McpRuntimeConfig(
            config_path=self.root / "settings" / "mcp.json",
            coordination_root=self.root / "coordination",
            workspace_root=self.root / "projects",
            transcript_root=self.root / "coordination" / "logs" / "mcp",
        )
        fixture_node = self.root / "fixture-node" / "bin" / "node"
        fixture_node.parent.mkdir(parents=True, exist_ok=True)
        executable = shutil.which("node")
        assert executable is not None
        fixture_node.symlink_to(executable)
        node_patch = patch(
            "agents_remember.serving.paseo.paseo_running_node.product_node",
            return_value=type("FixtureNode", (), {"node": fixture_node})(),
        )
        node_patch.start()
        self.addCleanup(node_patch.stop)
        package = self.root / "prefix" / "node_modules" / "@getpaseo" / "client"
        (package / "dist").mkdir(parents=True)
        exports = {
            ".": {"default": "./dist/index.js"},
            "./internal/daemon-client": {"default": "./dist/daemon-client.js"},
        }
        (package / "package.json").write_text(
            json.dumps({"name": "@getpaseo/client", "type": "module", "exports": exports}),
            encoding="utf-8",
        )
        (package / "dist" / "daemon-client.js").write_text(FAKE_DAEMON_CLIENT, encoding="utf-8")
        (package / "dist" / "index.js").write_text(FAKE_CLIENT_ROOT, encoding="utf-8")

    def call(self, payload: dict[str, Any], **scenario: Any) -> dict[str, Any]:
        with patch.dict(os.environ, self.scenario_environment(**scenario)):
            return bridge_call(self.config, "usage-reports", payload)

    def scenario_environment(self, **scenario: Any) -> dict[str, str]:
        path = self.root / "scenario.json"
        base = {"url": "ws://127.0.0.1:6835/ws", "serverId": SERVER_ID}
        path.write_text(json.dumps({**base, **scenario}), encoding="utf-8")
        (self.root / "record.jsonl").write_text("", encoding="utf-8")
        return {
            "FAKE_PASEO_SCENARIO": path.as_posix(),
            "FAKE_PASEO_RECORD": (self.root / "record.jsonl").as_posix(),
        }

    def recorded(self) -> list[dict[str, Any]]:
        lines = (self.root / "record.jsonl").read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

    def rows(self, reply: dict[str, Any]) -> dict[str, Any]:
        return {row["agentId"]: row for row in reply["reports"]}

    def test_a_cached_read_consumes_the_envelope_and_projects_ids_and_labels_only(self) -> None:
        reply = self.call(
            {"agentIds": [WORKER]},
            entries={WORKER: [_entry("claude-max")]},
        )

        row = self.rows(reply)[WORKER]
        assert [entry["id"] for entry in row["entries"]] == ["claude-max"]
        assert row["entries"][0]["account"] == {"label": "Max 20x"}
        assert "email" not in json.dumps(row)

    def test_a_stale_leaf_entry_is_refreshed_by_report_id_alone(self) -> None:
        fresh = _fresh()
        reply = self.call(
            {"agentIds": [WORKER], "leafAgentIds": [WORKER]},
            entries={WORKER: [_entry("claude-max")]},
            refresh={"claude-max": _entry("claude-max", used=40.0)},
            refreshedAt=fresh,
        )

        forced = [call for call in self.recorded() if "reportIds" in call["options"]]
        assert len(forced) == 1
        assert forced[0]["options"] == {"forceRefresh": True, "reportIds": ["claude-max"]}
        assert "agentId" not in forced[0]["options"]
        # The refreshed report is what the product judges, not the stale cache row.
        assert self.rows(reply)[WORKER]["entries"][0]["report"]["windows"][0]["usedPct"] == 40.0
        assert self.rows(reply)[WORKER]["entries"][0]["fetchedAt"] == fresh

    def test_a_fresh_entry_is_not_refreshed(self) -> None:
        reply = self.call(
            {"agentIds": [WORKER], "leafAgentIds": [WORKER]},
            entries={WORKER: [_entry("claude-max", fetched_at=_fresh())]},
        )

        assert [call for call in self.recorded() if "reportIds" in call["options"]] == []
        assert [entry["id"] for entry in self.rows(reply)[WORKER]["entries"]] == ["claude-max"]

    def test_a_non_leaf_entry_is_never_refreshed(self) -> None:
        self.call(
            {"agentIds": [WORKER], "leafAgentIds": []},
            entries={WORKER: [_entry("claude-max")]},
            refresh={"claude-max": _entry("claude-max", used=40.0)},
        )

        assert [call for call in self.recorded() if "reportIds" in call["options"]] == []

    def test_an_error_entry_is_never_forced(self) -> None:
        """A forced read after a 429 is refused and would turn the shared entry into an error."""

        self.call(
            {"agentIds": [WORKER], "leafAgentIds": [WORKER]},
            entries={WORKER: [_entry("claude-max", status="error")]},
            refresh={"claude-max": _entry("claude-max", used=40.0)},
        )

        assert [call for call in self.recorded() if "reportIds" in call["options"]] == []

    def test_two_seats_sharing_one_report_refresh_it_once(self) -> None:
        reply = self.call(
            {"agentIds": [WORKER, REVIEWER], "leafAgentIds": [WORKER, REVIEWER]},
            entries={
                WORKER: [_entry("claude-max")],
                REVIEWER: [_entry("claude-max")],
            },
            refresh={"claude-max": _entry("claude-max", used=40.0)},
            refreshedAt=_fresh(),
        )

        forced = [call for call in self.recorded() if "reportIds" in call["options"]]
        assert len(forced) == 1
        assert [
            self.rows(reply)[agent]["entries"][0]["report"]["windows"][0]["usedPct"]
            for agent in (WORKER, REVIEWER)
        ] == [40.0, 40.0]

    def test_one_agents_failure_is_its_own_row(self) -> None:
        reply = self.call(
            {"agentIds": [WORKER, REVIEWER], "leafAgentIds": [WORKER, REVIEWER]},
            entries={REVIEWER: [_entry("claude-max")]},
            errors={WORKER: "Unknown agent: f3c1a2b4"},
        )

        rows = self.rows(reply)
        assert "error" in rows[WORKER]
        assert "Unknown agent" in rows[WORKER]["error"]
        assert "error" not in rows[REVIEWER]
        assert [entry["id"] for entry in rows[REVIEWER]["entries"]] == ["claude-max"]

    def test_a_refresh_that_answers_nothing_leaves_the_cached_entry_in_place(self) -> None:
        stale = _stale()
        reply = self.call(
            {"agentIds": [WORKER], "leafAgentIds": [WORKER]},
            entries={WORKER: [_entry("claude-max", fetched_at=stale)]},
            refresh={},
        )

        entry = self.rows(reply)[WORKER]["entries"][0]
        assert entry["id"] == "claude-max"
        assert entry["fetchedAt"] == stale

    def test_no_agent_asks_for_no_read(self) -> None:
        reply = self.call({"agentIds": []})

        assert reply["reports"] == []
        assert self.recorded() == []

    def test_a_periodic_read_skips_an_unknown_agent_and_forces_the_next_agent_entries(self) -> None:
        reply = self.call(
            {"periodicCandidates": {"claude-max": [WORKER, MANAGER]}},
            errors={WORKER: "Unknown agent: f3c1a2b4"},
            entries={MANAGER: [_entry("claude-max"), _entry("side-effect")]},
            refresh={
                "claude-max": _entry("claude-max", used=40.0),
                "side-effect": _entry("side-effect", used=25.0),
            },
        )

        assert [call["options"] for call in self.recorded()] == [
            {"agentId": WORKER},
            {"agentId": MANAGER},
            {"forceRefresh": True, "reportIds": ["claude-max"]},
            {"forceRefresh": True, "reportIds": ["side-effect"]},
        ]
        assert set(self.rows(reply)) == {MANAGER}
        assert reply["periodic"] == [{"reportId": "claude-max", "agentId": MANAGER}]
        assert [
            entry["report"]["windows"][0]["usedPct"]
            for entry in self.rows(reply)[MANAGER]["entries"]
        ] == [40.0, 25.0]

    def test_a_periodic_read_stops_after_all_candidates_are_unknown(self) -> None:
        reply = self.call(
            {"periodicCandidates": {"claude-max": [WORKER, MANAGER]}},
            errors={
                WORKER: "Unknown agent: f3c1a2b4",
                MANAGER: "Request failed: Unknown agent requestType=list_usage_reports_request",
            },
        )

        assert [call["options"] for call in self.recorded()] == [
            {"agentId": WORKER},
            {"agentId": MANAGER},
        ]
        assert reply["reports"] == []
        [outcome] = reply["periodic"]
        assert outcome["reportId"] == "claude-max"
        assert "no mapped agent" in outcome["error"]

    def test_a_periodic_read_does_not_try_another_agent_after_a_non_unknown_failure(self) -> None:
        reply = self.call(
            {"periodicCandidates": {"claude-max": [WORKER, MANAGER]}},
            errors={WORKER: "Unknown agent provider: unconfigured"},
            entries={MANAGER: [_entry("claude-max")]},
        )

        assert [call["options"] for call in self.recorded()] == [{"agentId": WORKER}]
        assert reply["reports"] == []
        assert reply["periodic"] == [
            {
                "reportId": "claude-max",
                "agentId": WORKER,
                "error": "Unknown agent provider: unconfigured",
            }
        ]

    def test_a_mixed_batch_shares_refreshes_and_keeps_stamped_only_managers_cached(self) -> None:
        reply = self.call(
            {
                "agentIds": [WORKER, STAMPED_MANAGER, MANAGER],
                "leafAgentIds": [WORKER],
                "periodicCandidates": {"claude-max": [MANAGER], "codex-week": [MANAGER]},
            },
            entries={
                WORKER: [_entry("claude-max")],
                STAMPED_MANAGER: [_entry("codex-week"), _entry("stamp-only")],
                MANAGER: [_entry("claude-max"), _entry("codex-week")],
            },
            refresh={
                "claude-max": _entry("claude-max", used=40.0),
                "codex-week": _entry("codex-week", used=25.0),
                "stamp-only": _entry("stamp-only", used=10.0),
            },
        )

        calls = [call["options"] for call in self.recorded()]
        assert [call["agentId"] for call in calls if "agentId" in call] == [
            WORKER,
            STAMPED_MANAGER,
            MANAGER,
        ]
        assert [call["reportIds"] for call in calls if "reportIds" in call] == [
            ["claude-max"],
            ["codex-week"],
        ]
        assert reply["periodic"] == [
            {"reportId": "claude-max", "agentId": MANAGER},
            {"reportId": "codex-week", "agentId": MANAGER},
        ]
        assert {
            agent: [entry["report"]["windows"][0]["usedPct"] for entry in row["entries"]]
            for agent, row in self.rows(reply).items()
        } == {WORKER: [40.0], STAMPED_MANAGER: [25.0, 100.0], MANAGER: [40.0, 25.0]}

    def test_a_periodic_force_failure_keeps_the_cache_and_marks_its_report_unusable(self) -> None:
        stale = _stale()
        for refresh_errors in ({}, {"claude-max": "provider timed out"}):
            with self.subTest(refresh_errors=refresh_errors):
                reply = self.call(
                    {
                        "agentIds": [STAMPED_MANAGER],
                        "periodicCandidates": {"claude-max": [MANAGER]},
                    },
                    entries={
                        MANAGER: [_entry("claude-max", fetched_at=stale, used=30.0)],
                        STAMPED_MANAGER: [_entry("claude-max", fetched_at=stale, used=30.0)],
                    },
                    refreshErrors=refresh_errors,
                )
                for row in self.rows(reply).values():
                    assert row["failedRefreshReportIds"] == ["claude-max"]
                    assert row["entries"][0]["fetchedAt"] == stale
                [outcome] = reply["periodic"]
                assert outcome["reportId"] == "claude-max"
                assert outcome["agentId"] == MANAGER
                assert outcome["error"] == refresh_errors.get(
                    "claude-max", "the host returned no refreshed report"
                )

    def test_a_periodic_list_that_omits_its_target_does_not_publish_a_mapping(self) -> None:
        reply = self.call(
            {"periodicCandidates": {"claude-max": [MANAGER, WORKER]}},
            entries={MANAGER: [_entry("new-account")], WORKER: [_entry("claude-max")]},
        )

        assert [call["options"] for call in self.recorded()] == [{"agentId": MANAGER}]
        assert reply["reports"] == []
        assert reply["periodic"] == [
            {
                "reportId": "claude-max",
                "agentId": MANAGER,
                "error": "the host did not return the mapped report",
            }
        ]

    def test_missing_periodic_target_retains_its_mapping_when_a_sibling_clears(self) -> None:
        assert publish_role_agent(
            self.root / "home",
            MANAGER,
            {"role": "manager", "task": "repo/master/leaf.json", "reportPath": "r"},
        )
        account_a, account_b = "account-a", "account-b"
        entries = {MANAGER: [_entry(account_a), _entry(account_b)]}
        with patch.dict(os.environ, self.scenario_environment(entries=entries)):
            original = read_usage_for_agents(self.config, UsageReadBatch([MANAGER], {}))
        assert original is not None
        assert set(original.reports) == {account_a, account_b}
        assert set(original.agents[MANAGER]) == {account_a, account_b}

        environment = self.scenario_environment(
            entries={MANAGER: [_entry(account_b)]},
            refresh={account_b: _entry(account_b, used=40.0)},
        )
        batch = UsageReadBatch([], {account_a: (MANAGER,), account_b: (MANAGER,)})
        with patch.dict(os.environ, environment):
            result = read_usage_for_agents(self.config, batch)

        assert [call["options"] for call in self.recorded()] == [
            {"agentId": MANAGER},
            {"forceRefresh": True, "reportIds": [account_b]},
        ]
        assert result is not None
        assert result.reports == {account_a: original.reports[account_a]}
        assert set(result.agents[MANAGER]) == {account_a, account_b}
        assert MANAGER not in result.clearedSeats
        message = provider_limit_hold(self.config, MANAGER)
        assert message is not None
        assert message["limitedUntil"] == original.reports[account_a].until.isoformat()

    def test_a_periodic_error_entry_is_returned_without_forcing_it(self) -> None:
        failed = _entry("claude-max", status="error")
        last_successful = _entry("claude-max", used=35.0)
        failed["lastSuccessful"] = {
            "fetchedAt": last_successful["fetchedAt"],
            "report": last_successful["report"],
        }
        reply = self.call(
            {"periodicCandidates": {"claude-max": [MANAGER]}},
            entries={MANAGER: [failed]},
        )

        assert [call["options"] for call in self.recorded()] == [{"agentId": MANAGER}]
        row = self.rows(reply)[MANAGER]
        assert row["entries"][0]["lastSuccessful"] == failed["lastSuccessful"]
        assert "failedRefreshReportIds" not in row
        [outcome] = reply["periodic"]
        assert outcome["reportId"] == "claude-max"
        assert outcome["agentId"] == MANAGER
        assert "in error" in outcome["error"]


if __name__ == "__main__":
    unittest.main()
