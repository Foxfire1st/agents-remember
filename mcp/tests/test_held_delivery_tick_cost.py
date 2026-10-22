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
from agents_remember.cli import role_held_messages as held
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.serving import _app_lifespan as life
from agents_remember.serving.projections import host_activity as activity

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)
AGENT = "11111111-1111-5111-8111-111111111111"


def _config(tmp_path: Path) -> SimpleNamespace:
    """A runtime whose Paseo home holds the two files the tick reads."""

    home = tmp_path / "home"
    (home / "agents-remember").mkdir(parents=True)
    return SimpleNamespace(paseo_runtime=SimpleNamespace(home=home))


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
        def record_usage(_config: Any, agent_ids: list[str]) -> None:
            self.reads.append(list(agent_ids))

        life._held_delivery_tick(
            cast(McpRuntimeConfig, self.config),
            self.seen,
            self.usage_seen,
            sweep=False,
            usage_read=record_usage,
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
        for _ in range(3):
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
