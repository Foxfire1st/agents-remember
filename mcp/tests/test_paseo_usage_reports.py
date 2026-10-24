"""MIK-R190 rule 7: the bridge reads Paseo 0.12.0's usage reports as the SDK answers them.

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
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from paseo_runtime_test_support import write_runtime_block

SERVER_ID = "srv_configured"
WORKER = "f3c1a2b4-5d6e-4f70-8a91-b2c3d4e5f607"
REVIEWER = "0a1b2c3d-4e5f-4a6b-8c7d-9e0f1a2b3c4d"


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
  // The pinned 0.12.0 shape: {requestId, reports}, and the SDK's own selector refusal.
  async listUsageReports(options = {}) {
    record({ via: 'listUsageReports', options })
    if (options.agentId !== undefined && options.reportIds !== undefined) {
      throw new Error('agentId and reportIds cannot be combined')
    }
    if (options.reportIds !== undefined) {
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
            "agents_remember.cli.paseo_bridge.product_node",
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
        path = self.root / "scenario.json"
        base = {"url": "ws://127.0.0.1:6835/ws", "serverId": SERVER_ID}
        path.write_text(json.dumps({**base, **scenario}), encoding="utf-8")
        (self.root / "record.jsonl").write_text("", encoding="utf-8")
        environment = {
            "FAKE_PASEO_SCENARIO": path.as_posix(),
            "FAKE_PASEO_RECORD": (self.root / "record.jsonl").as_posix(),
        }
        with patch.dict(os.environ, environment):
            return bridge_call(self.config, "usage-reports", payload)

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


if __name__ == "__main__":
    unittest.main()
