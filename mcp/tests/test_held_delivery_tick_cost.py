"""MIK-R190 rule 7: what one held-delivery tick costs when nothing has changed.

The tick reads the host's publication and the role record beside it, and it reads them
for two decisions. The finding this file guards (L184-R1-F04) was a second reader that
parsed both files on every 3 s tick even when the host had written nothing: the stat-
bound parse the store's turn-end reader already kept was bypassed. These cases count the
file reads and stats the tick actually makes, and the bridge process it does or does not
start, for one seat and for a fleet.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.cli import provider_limit_hold as hold
from agents_remember.cli import role_held_messages as held
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_status import AgentReading
from agents_remember.cli.role_launch_receipts import RECEIPT_SCHEMA
from agents_remember.controlplane.held_store_discovery import receipt_holder
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.provider_limits import read_provider_limits
from agents_remember.serving import _app_lifespan as life
from agents_remember.serving.paseo import provider_limits as writer
from agents_remember.serving.projections import host_activity as activity

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
AGENT = "11111111-1111-5111-8111-111111111111"


def _config(tmp_path: Path) -> SimpleNamespace:
    """A runtime whose Paseo home holds the two files the tick reads."""

    home = tmp_path / "home"
    (home / "agents-remember").mkdir(parents=True)
    return SimpleNamespace(paseo_runtime=SimpleNamespace(home=home), coordination_root=tmp_path)


def _path(config: SimpleNamespace, name: str) -> Path:
    return config.paseo_runtime.home / "agents-remember" / name


def _publish(config: SimpleNamespace, agents: dict[str, dict[str, Any]]) -> None:
    _path(config, "host-activity.json").write_text(
        json.dumps(
            {
                "bootId": "boot-1",
                "complete": True,
                "agents": agents,
                "ignoredEvents": 0,
            }
        ),
        encoding="utf-8",
    )


def _register(config: SimpleNamespace, agent_ids: list[str]) -> None:
    _path(config, "role-agents.json").write_text(
        json.dumps(
            {
                "schema": "ar-role-agents/v1",
                "agents": {
                    agent_id: {
                        "role": "worker",
                        "task": "repo/master/leaf.json",
                        "reportPath": f"/tmp/{agent_id}.md",
                    }
                    for agent_id in agent_ids
                },
            }
        ),
        encoding="utf-8",
    )


class _Reads:
    """How many times the tick read and stat'd the two files it shares."""

    def __init__(self, config: SimpleNamespace) -> None:
        self.publication = _path(config, "host-activity.json")
        self.registration = _path(config, "role-agents.json")
        self.reads = 0
        self.stats = 0
        self._read_text = Path.read_text
        self._stat = Path.stat

    def __enter__(self) -> _Reads:
        def read_text(path: Path, *args: Any, **kwargs: Any) -> str:
            if path in {self.publication, self.registration}:
                self.reads += 1
            return self._read_text(path, *args, **kwargs)

        def stat(path: Path, *args: Any, **kwargs: Any) -> Any:
            if path in {self.publication, self.registration}:
                self.stats += 1
            return self._stat(path, *args, **kwargs)

        Path.read_text = read_text  # type: ignore[method-assign]
        Path.stat = stat  # type: ignore[method-assign]
        return self

    def __exit__(self, *_: object) -> None:
        Path.read_text = self._read_text  # type: ignore[method-assign]
        Path.stat = self._stat  # type: ignore[method-assign]


class _Ticker:
    """The tick's own two memories, kept across calls exactly as the loop keeps them."""

    def __init__(self, config: SimpleNamespace) -> None:
        self.config = config
        self.seen: dict[str, str] = {}
        self.usage_seen: dict[str, str] = {}
        self.reads: list[list[str]] = []

    def __call__(self) -> None:
        def record_usage(_config: McpRuntimeConfig, batch: activity.UsageReadBatch) -> None:
            self.reads.append(batch.stamped_agents)

        life._held_delivery_tick(
            cast(McpRuntimeConfig, self.config),
            self.seen,
            self.usage_seen,
            sweep=False,
            collaborators=life._DeliveryCollaborators(usage_read=record_usage),
        )


@pytest.fixture(autouse=True)
def _fresh_caches() -> None:
    activity._ENDINGS.clear()
    getattr(activity, "_ROLE_RECORDS", {}).clear()


@pytest.mark.parametrize("seats", [1, 40])
def test_unchanged_quiet_ticks_parse_both_inputs_once(
    tmp_path: Path, monkeypatch, seats: int
) -> None:
    """Three ticks over one unchanged publication: one parse each, no bridge process."""

    config = _config(tmp_path)
    agent_ids = [f"{index:08d}-1111-5111-8111-111111111111" for index in range(seats)]
    _publish(config, {agent_id: {"state": "idle"} for agent_id in agent_ids})
    _register(config, agent_ids)
    monkeypatch.setattr(held, "deliver_ended_turns", lambda *args: None)
    tick = _Ticker(config)

    with _Reads(config) as counted:
        for minutes in (0, 5, 10):
            monkeypatch.setattr(
                activity,
                "claim_due_reports",
                lambda config, elapsed=minutes: writer.claim_due_reports(
                    config, now=NOW + timedelta(minutes=elapsed)
                ),
            )
            tick()
        first = counted.reads

    # An unstamped idle publication triggers nothing: no usage read and no bridge process.
    assert tick.reads == []
    # Both inputs are read exactly once for the three ticks, whatever the fleet size.
    assert first == 2
    assert counted.stats <= 3 * 3


def test_a_reload_reads_once_and_an_unstamped_idle_end_triggers_nothing(
    tmp_path: Path, monkeypatch
) -> None:
    """A freshly started process reads its inputs once; an idle agent without a stamp is quiet."""

    config = _config(tmp_path)
    _publish(config, {AGENT: {"state": "idle"}})
    _register(config, [AGENT])
    monkeypatch.setattr(held, "deliver_ended_turns", lambda *args: None)
    tick = _Ticker(config)

    with _Reads(config) as counted:
        tick()
        tick()

    assert tick.reads == []
    assert counted.reads == 2


def test_a_new_stamped_end_still_makes_exactly_one_usage_read(tmp_path: Path, monkeypatch) -> None:
    """The signal survives the shared parse: a new stamp reads usage once, with its agent."""

    config = _config(tmp_path)
    _publish(config, {AGENT: {"state": "idle", "eventAt": NOW.isoformat()}})
    _register(config, [AGENT])
    monkeypatch.setattr(held, "deliver_ended_turns", lambda *args: None)
    tick = _Ticker(config)

    tick()
    tick()

    assert tick.reads == [[AGENT]]


def test_a_close_releases_the_turn_wait_without_triggering_a_usage_read(
    tmp_path: Path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _register(config, [AGENT])
    _publish(
        config, {AGENT: {"state": "closed", "eventAt": NOW.isoformat(), "runningSubagents": 1}}
    )
    deliveries: list[list[str]] = []
    monkeypatch.setattr(held, "deliver_ended_turns", lambda _config, ids: deliveries.append(ids))
    tick = _Ticker(config)

    tick()
    tick()

    assert deliveries == [[AGENT]]
    assert tick.reads == []
    assert activity.running_subagents(cast(McpRuntimeConfig, config), AGENT) == 0


def test_a_written_publication_is_read_again(tmp_path: Path, monkeypatch) -> None:
    """The cache is bound to the content: a new publication is a new input."""

    config = _config(tmp_path)
    _publish(config, {AGENT: {"state": "idle"}})
    _register(config, [AGENT])
    monkeypatch.setattr(held, "deliver_ended_turns", lambda *args: None)
    tick = _Ticker(config)

    with _Reads(config) as counted:
        tick()
        _publish(
            config,
            {AGENT: {"state": "idle", "eventAt": (NOW + timedelta(seconds=3)).isoformat()}},
        )
        tick()

    assert counted.reads == 3
    # The unstamped idle end of the first tick read nothing; the stamped one after the
    # rewrite is a new input and earns its one read.
    assert tick.reads == [[AGENT]]


def test_a_new_registration_is_read_again(tmp_path: Path, monkeypatch) -> None:
    """A seat added to the role record is an input change, not a stale cache hit."""

    config = _config(tmp_path)
    _publish(
        config,
        {
            AGENT: {"state": "idle", "eventAt": NOW.isoformat()},
            "22222222-2222-5222-8222-222222222222": {
                "state": "idle",
                "eventAt": NOW.isoformat(),
            },
        },
    )
    _register(config, [AGENT])
    monkeypatch.setattr(held, "deliver_ended_turns", lambda *args: None)
    tick = _Ticker(config)

    tick()
    _register(config, [AGENT, "22222222-2222-5222-8222-222222222222"])
    tick()

    assert tick.reads == [[AGENT], ["22222222-2222-5222-8222-222222222222"]]


def _store_report_facts(
    config: SimpleNamespace, now: datetime, agents: dict[str, list[str]]
) -> Path:
    path = _path(config, "provider-limits.json")
    reports = {
        report: {
            "until": (now + timedelta(days=3)).isoformat(),
            "observedAt": (now - timedelta(minutes=5)).isoformat(),
        }
        for report in dict.fromkeys(report for ids in agents.values() for report in ids)
    }
    path.write_text(json.dumps({"reports": reports, "agents": agents}), encoding="utf-8")
    return path


def _queue_held_work(config: SimpleNamespace, workspace: Path) -> None:
    workspace.mkdir()
    path = receipt_holder(config.coordination_root) / "receipt.json"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(
            {
                "schema": RECEIPT_SCHEMA,
                "status": "running",
                "agentId": AGENT,
                "requestId": "33333333-3333-5333-8333-333333333333",
                "execution": {"agentId": AGENT},
                "workspace": {"path": str(workspace)},
            }
        ),
        encoding="utf-8",
    )
    held.hold_message(
        workspace,
        AGENT,
        held.HeldMessage(
            sender_line="From manager",
            sender_agent_id="manager",
            text="Work held through the provider reset",
            reason="provider-limit",
        ),
    )


def test_a_due_provider_clear_releases_held_work_without_a_turn_end(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    now = datetime.now(UTC)
    _publish(config, {})
    _register(config, [AGENT])
    limits_path = _store_report_facts(config, now, {AGENT: ["weekly"]})
    workspace = tmp_path / "workspace"
    _queue_held_work(config, workspace)
    sent: list[str] = []

    def usage(_config, _command, _payload):
        return {
            "reports": [
                {"agentId": AGENT, "entries": [{"id": "weekly", "report": {"status": "available"}}]}
            ],
            "periodic": [{"reportId": "weekly", "agentId": AGENT}],
        }

    def send(_config, _command, payload):
        sent.append(payload["text"])
        return {"delivery": {"delivered": True, "taken": "started"}}

    monkeypatch.setattr(hold, "bridge_call", usage)
    monkeypatch.setattr(held, "bridge_call", send)
    monkeypatch.setattr(
        held, "read_agent", lambda *_: AgentReading(reachable=True, found=True, archived=False)
    )
    life._held_delivery_tick(
        cast(McpRuntimeConfig, config),
        {},
        {},
        sweep=True,
        collaborators=life._DeliveryCollaborators(usage_read=hold.read_usage_for_agents),
    )

    assert read_provider_limits(limits_path).reports == {}
    assert len(sent) == 1 and "Work held through the provider reset" in sent[0]
    assert held.waiting_total(workspace, AGENT) == 0


def test_two_due_reports_and_a_stamped_end_share_one_usage_batch(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    other = "22222222-2222-5222-8222-222222222222"
    _publish(config, {other: {"state": "idle", "eventAt": NOW.isoformat()}})
    _register(config, [AGENT, other])
    limits_path = _store_report_facts(config, NOW, {AGENT: ["weekly", "daily"]})
    monkeypatch.setattr(
        activity, "claim_due_reports", lambda config: writer.claim_due_reports(config, now=NOW)
    )
    monkeypatch.setattr(held, "deliver_ended_turns", lambda *_: None)
    calls: list[dict[str, Any]] = []

    def usage(_config, _command, payload):
        calls.append(payload)
        return {
            "reports": [
                {
                    "agentId": AGENT,
                    "entries": [_exhausted_entry("weekly"), _exhausted_entry("daily")],
                },
                {"agentId": other, "entries": [_exhausted_entry("stamped")]},
            ],
            "periodic": [{"reportId": report, "agentId": AGENT} for report in ("weekly", "daily")],
        }

    monkeypatch.setattr(hold, "bridge_call", usage)
    monkeypatch.setattr(
        hold,
        "record_usage_reads",
        lambda config, reads, **_: writer.record_usage_reads(config, reads, now=NOW),
    )
    seen: dict[str, str] = {}
    for _ in range(3):
        life._held_delivery_tick(
            cast(McpRuntimeConfig, config),
            {},
            seen,
            sweep=False,
            collaborators=life._DeliveryCollaborators(usage_read=hold.read_usage_for_agents),
        )

    assert calls == [
        {
            "agentIds": [other],
            "leafAgentIds": [other],
            "periodicCandidates": {"weekly": (AGENT,), "daily": (AGENT,)},
        }
    ]
    assert set(read_provider_limits(limits_path).reports) == {"weekly", "daily", "stamped"}


def _exhausted_entry(report: str) -> dict[str, Any]:
    return {
        "id": report,
        "fetchedAt": NOW.isoformat(),
        "report": {
            "status": "available",
            "windows": [
                {
                    "summary": True,
                    "usedPct": 100,
                    "resetsAt": (NOW + timedelta(days=3)).isoformat(),
                }
            ],
        },
    }


def test_due_report_without_a_registered_seat_logs_once_per_period(
    tmp_path, monkeypatch, caplog
) -> None:
    config = _config(tmp_path)
    _publish(config, {})
    _register(config, [])
    path = _store_report_facts(config, NOW, {AGENT: ["weekly"]})
    clock = [NOW]
    monkeypatch.setattr(
        activity, "claim_due_reports", lambda config: writer.claim_due_reports(config, now=clock[0])
    )

    def unexpected_bridge(*_):
        pytest.fail("a due report without a mapped seat started a bridge")

    monkeypatch.setattr(hold, "bridge_call", unexpected_bridge)
    for minutes in (0, 1, 4, 5, 6):
        clock[0] = NOW + timedelta(minutes=minutes)
        life._held_delivery_tick(
            cast(McpRuntimeConfig, config),
            {},
            {},
            sweep=False,
            collaborators=life._DeliveryCollaborators(usage_read=hold.read_usage_for_agents),
        )

    assert sum("weekly: no mapped seat" in record.message for record in caplog.records) == 2
    assert set(read_provider_limits(path).reports) == {"weekly"}


@pytest.mark.parametrize("failure", ["unavailable", "error", "timeout"])
def test_a_failed_periodic_read_retains_the_fact_and_mapping(
    tmp_path, monkeypatch, caplog, failure: str
) -> None:
    config = _config(tmp_path)
    _publish(config, {})
    _register(config, [AGENT])
    path = _store_report_facts(config, datetime.now(UTC), {AGENT: ["weekly"]})
    original = read_provider_limits(path)

    def usage(*_):
        if failure == "timeout":
            raise PaseoBridgeFailure("paseo_bridge_timeout", "the bridge did not answer")
        return {
            "reports": [
                {"agentId": AGENT, "entries": [{"id": "weekly", "report": {"status": failure}}]}
            ],
            "periodic": [{"reportId": "weekly", "agentId": AGENT, "error": failure}],
        }

    monkeypatch.setattr(hold, "bridge_call", usage)
    life._held_delivery_tick(
        cast(McpRuntimeConfig, config),
        {},
        {},
        sweep=False,
        collaborators=life._DeliveryCollaborators(usage_read=hold.read_usage_for_agents),
    )

    current = read_provider_limits(path)
    assert current.agents == original.agents
    fact = current.reports["weekly"]
    assert fact.until == original.reports["weekly"].until
    assert fact.observedAt == original.reports["weekly"].observedAt
    assert sum("provider usage read failed" in record.message for record in caplog.records) == 1
