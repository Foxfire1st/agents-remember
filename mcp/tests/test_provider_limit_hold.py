"""MIK-R190 rules 9 and 10: the one send check, and a drain that waits for the reset.

A message to a limited recipient is held with the reason and the reset time, on
every send path; the drain pauses before it reads the host, so waiting for a limit
costs no bridge process. The one exception is rule 10's single host read for a
recipient the publication shows ``ended``.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from agents_remember.cli import provider_limit_hold as hold
from agents_remember.cli import role_held_messages as held
from agents_remember.cli.paseo_bridge import PaseoBridgeFailure
from agents_remember.cli.paseo_status import AgentReading
from agents_remember.cli.role_held_messages import HeldMessage, hold_message
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
RESET = NOW + timedelta(hours=2)
AGENT = "11111111-1111-5111-8111-111111111111"
# These cases patch ``provider_limits_path`` (or the bridge call) so nothing reads the
# runtime config; the placeholder keeps the product's own parameter type rather than
# widening it to a config the patched path cannot receive.
NO_CONFIG = cast(McpRuntimeConfig, None)


def _policy():
    """The module that owns rule 10's policy (imported here so a missing owner fails in-test)."""

    from agents_remember.cli import provider_limit_drain  # noqa: PLC0415

    return provider_limit_drain


def _limits(path: Path, *, until: datetime = RESET) -> None:
    path.write_text(
        json.dumps(
            {
                "schema": "ar-provider-limits/v1",
                "reports": {
                    "claude-max": {
                        "until": until.isoformat(),
                        "sourceLabel": "Claude",
                        "accountLabel": "Max 20x",
                        "windows": [
                            {"id": "five_hour", "label": "Session", "resetsAt": until.isoformat()}
                        ],
                        "observedAt": NOW.isoformat(),
                    }
                },
                "agents": {AGENT: ["claude-max"]},
            }
        ),
        encoding="utf-8",
    )


# ── rule 9: the one check ─────────────────────────────────────────────────────


def test_nothing_is_limited_without_a_runtime_or_a_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(hold, "provider_limits_path", lambda config: None)
    assert hold.provider_limit_hold(NO_CONFIG, AGENT, NOW) is None

    monkeypatch.setattr(hold, "provider_limits_path", lambda config: tmp_path / "absent.json")
    assert hold.provider_limit_hold(NO_CONFIG, AGENT, NOW) is None


def test_a_stored_fact_holds_the_message_with_its_reset_time(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "provider-limits.json"
    _limits(path)
    monkeypatch.setattr(hold, "provider_limits_path", lambda config: path)

    answer = hold.provider_limit_hold(NO_CONFIG, AGENT, NOW)

    assert answer is not None
    assert answer["heldReason"] == "provider-limit"
    assert answer["limitedUntil"] == RESET.isoformat()
    assert RESET.isoformat() in answer["detail"]


def test_a_reset_that_has_passed_is_no_limit(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "provider-limits.json"
    _limits(path)
    monkeypatch.setattr(hold, "provider_limits_path", lambda config: path)

    assert hold.provider_limit_hold(NO_CONFIG, AGENT, RESET) is None


# ── rule 10: the drain waits for the reset ────────────────────────────────────


def _batch(tmp_path: Path, folder: str) -> Any:
    return held._DrainBatch(
        config=NO_CONFIG,
        path=tmp_path / "receipt.json",
        receipt={},
        folder=folder,
        agent_id=AGENT,
        messages=[],
        claim="claim-1",
    )


def _store(tmp_path: Path) -> str:
    folder = tmp_path / "workspace"
    folder.mkdir()
    hold_message(
        folder,
        AGENT,
        HeldMessage(sender_line="From worker", sender_agent_id="s", text="t", reason="busy"),
    )
    return str(folder)


def test_a_limited_recipient_costs_no_host_read(monkeypatch, tmp_path: Path) -> None:
    drain_policy = _policy()
    reads: list[str] = []
    monkeypatch.setattr(
        drain_policy,
        "provider_limit_hold",
        lambda config, agent_id: {
            "heldReason": "provider-limit",
            "limitedUntil": RESET.isoformat(),
            "detail": "held until the reset",
        },
    )
    monkeypatch.setattr(drain_policy, "read_agent", lambda config, agent_id: reads.append(agent_id))
    monkeypatch.setattr(held, "_release_claim", lambda *args: None)
    monkeypatch.setattr(drain_policy, "ended_stamp", lambda config, agent_id: None)

    with pytest.raises(held._DrainPause) as paused:
        held._check_drain_readiness(_batch(tmp_path, _store(tmp_path)))

    assert paused.value.outcome["outcome"] == "provider-limit"
    assert paused.value.outcome["limitedUntil"] == RESET.isoformat()
    assert reads == []


def test_an_ended_limited_recipient_is_read_once_for_its_stamp(monkeypatch, tmp_path: Path) -> None:
    drain_policy = _policy()
    reads: list[str] = []
    reading = AgentReading(reachable=True, found=True, archived=False)
    monkeypatch.setattr(
        drain_policy,
        "provider_limit_hold",
        lambda config, agent_id: {
            "heldReason": "provider-limit",
            "limitedUntil": RESET.isoformat(),
            "detail": "held until the reset",
        },
    )
    monkeypatch.setattr(
        drain_policy, "read_agent", lambda config, agent_id: reads.append(agent_id) or reading
    )
    monkeypatch.setattr(held, "_release_claim", lambda *args: None)
    monkeypatch.setattr(drain_policy, "ended_stamp", lambda config, agent_id: "ended-stamp-1")
    folder = _store(tmp_path)

    for _ in range(3):
        with pytest.raises(held._DrainPause):
            held._check_drain_readiness(_batch(tmp_path, folder))

    assert reads == [AGENT]


def test_a_changed_ended_stamp_earns_one_more_read(monkeypatch, tmp_path: Path) -> None:
    drain_policy = _policy()
    reads: list[str] = []
    reading = AgentReading(reachable=True, found=True, archived=False)
    stamp = {"value": "ended-stamp-1"}
    monkeypatch.setattr(
        drain_policy,
        "provider_limit_hold",
        lambda config, agent_id: {
            "heldReason": "provider-limit",
            "limitedUntil": RESET.isoformat(),
            "detail": "held until the reset",
        },
    )
    monkeypatch.setattr(
        drain_policy, "read_agent", lambda config, agent_id: reads.append(agent_id) or reading
    )
    monkeypatch.setattr(held, "_release_claim", lambda *args: None)
    monkeypatch.setattr(drain_policy, "ended_stamp", lambda config, agent_id: stamp["value"])
    folder = _store(tmp_path)

    with pytest.raises(held._DrainPause):
        held._check_drain_readiness(_batch(tmp_path, folder))
    stamp["value"] = "ended-stamp-2"
    with pytest.raises(held._DrainPause):
        held._check_drain_readiness(_batch(tmp_path, folder))

    assert reads == [AGENT, AGENT]


def test_an_archived_limited_recipient_ends_its_messages(monkeypatch, tmp_path: Path) -> None:
    drain_policy = _policy()
    reading = AgentReading(reachable=True, found=True, archived=True)
    monkeypatch.setattr(
        drain_policy,
        "provider_limit_hold",
        lambda config, agent_id: {
            "heldReason": "provider-limit",
            "limitedUntil": RESET.isoformat(),
            "detail": "held until the reset",
        },
    )
    monkeypatch.setattr(drain_policy, "read_agent", lambda config, agent_id: reading)
    monkeypatch.setattr(held, "_release_claim", lambda *args: None)
    monkeypatch.setattr(drain_policy, "ended_stamp", lambda config, agent_id: "ended-stamp-1")

    with pytest.raises(held._DrainPause) as paused:
        held._check_drain_readiness(_batch(tmp_path, _store(tmp_path)))

    assert paused.value.outcome["outcome"] == "recipient-gone"
    assert paused.value.outcome["ended"]


# ── rule 9: the held row keeps its own reset time (L184-R1-F05) ───────────────


def test_a_provider_limited_row_persists_its_reset_time(tmp_path: Path) -> None:
    folder = tmp_path / "workspace"
    folder.mkdir()
    reset = datetime(2026, 10, 20, 20, 30, tzinfo=timezone(timedelta(hours=2)))

    held.hold_message(
        folder,
        AGENT,
        HeldMessage(
            sender_line="From worker",
            sender_agent_id="s",
            text="t",
            reason="provider-limit",
            limited_until=reset.isoformat(),
        ),
    )

    body = json.loads(held.store_path_for(folder, AGENT).read_text(encoding="utf-8"))
    row = body["messages"][0]
    assert row["reason"] == "provider-limit"
    assert row["limitedUntil"] == "2026-10-20T20:30:00+02:00"
    # Offset-aware: the row's own time still names its offset after a reload.
    assert datetime.fromisoformat(row["limitedUntil"]).utcoffset() == timedelta(hours=2)


def test_the_reset_time_survives_a_restart_and_is_read_back_from_the_row(tmp_path: Path) -> None:
    folder = tmp_path / "workspace"
    folder.mkdir()
    held.hold_message(
        folder,
        AGENT,
        HeldMessage(
            sender_line="From worker",
            sender_agent_id="s",
            text="t",
            reason="provider-limit",
            limited_until=RESET.isoformat(),
        ),
    )

    # A fresh read is what a restarted process does: no memory of the take.
    store = held._read_store_file(held.store_path_for(folder, AGENT), AGENT)
    assert store["messages"][0]["limitedUntil"] == RESET.isoformat()
    assert held.waiting_counts(store)[1] == 1


def test_the_take_reads_the_reset_time_back_for_the_answer(tmp_path: Path) -> None:
    folder = tmp_path / "workspace"
    folder.mkdir()

    taken = held.hold_message(
        folder,
        AGENT,
        HeldMessage(
            sender_line="From worker",
            sender_agent_id="s",
            text="t",
            reason="provider-limit",
            limited_until=RESET.isoformat(),
        ),
    )

    assert taken["limitedUntil"] == RESET.isoformat()


def test_the_send_path_hands_the_limit_and_its_reset_to_the_held_row(monkeypatch) -> None:
    from agents_remember.cli import paseo_role_tools as tools  # noqa: PLC0415
    from agents_remember.cli.paseo_role_tools import _MessageSend  # noqa: PLC0415

    captured: dict[str, Any] = {}
    monkeypatch.setattr(
        tools,
        "provider_limit_hold",
        lambda config, agent_id: {
            "heldReason": "provider-limit",
            "limitedUntil": RESET.isoformat(),
            "detail": "held until the reset",
        },
    )
    monkeypatch.setattr(
        tools, "_hold_for_seat", lambda send, **fields: captured.update(fields) or {"ok": True}
    )
    # A typed double: the send path reads the fields a real _MessageSend carries, and
    # this case only asserts what the hold hands back.
    send = cast(
        _MessageSend,
        SimpleNamespace(
            config=None,
            call=SimpleNamespace(wait=False),
            sender=None,
            recipient=SimpleNamespace(agent_id=AGENT),
            located=None,
            withdrawn=None,
            collection=None,
            filed=None,
        ),
    )

    tools._deliver_or_hold(send)

    assert captured["reason"] == "provider-limit"
    assert captured["limited_until"] == RESET.isoformat()


def test_a_default_hold_carries_no_reset_time(tmp_path: Path) -> None:
    folder = tmp_path / "workspace"
    folder.mkdir()

    held.hold_message(
        folder,
        AGENT,
        HeldMessage(sender_line="From worker", sender_agent_id="s", text="t", reason="busy"),
    )

    body = json.loads(held.store_path_for(folder, AGENT).read_text(encoding="utf-8"))
    assert "limitedUntil" not in body["messages"][0]


def test_the_twentieth_message_still_bounds_the_store(tmp_path: Path) -> None:
    folder = tmp_path / "workspace"
    folder.mkdir()
    for index in range(held.MAX_HELD_PER_RECIPIENT):
        held.hold_message(
            folder,
            AGENT,
            HeldMessage(
                sender_line="From worker",
                sender_agent_id="s",
                text=f"t{index}",
                reason="provider-limit",
                limited_until=RESET.isoformat(),
            ),
        )

    with pytest.raises(held.HeldStoreFull):
        held.hold_message(
            folder,
            AGENT,
            HeldMessage(
                sender_line="From worker",
                sender_agent_id="s",
                text="one too many",
                reason="provider-limit",
                limited_until=RESET.isoformat(),
            ),
        )

    store = held._read_store_file(held.store_path_for(folder, AGENT), AGENT)
    assert [row["text"] for row in store["messages"]] == [
        f"t{index}" for index in range(held.MAX_HELD_PER_RECIPIENT)
    ]


# ── rule 10 policy lives in its own module (L184-R1-F15) ──────────────────────


def test_the_new_limit_policy_has_an_owner_module_of_its_own() -> None:
    """MIK-R190 rule 19: new policy is a new module; the oversized one keeps call sites."""

    assert not hasattr(held, "_pause_for_provider_limit")
    assert not hasattr(held, "_ended_stamp")
    policy = _policy()
    assert callable(policy.pause_for_provider_limit)
    assert callable(policy.ended_stamp)
    assert callable(policy.claim_ended_stamp)


def test_the_owned_policy_is_what_the_readiness_check_reaches(monkeypatch, tmp_path: Path) -> None:
    drain_policy = _policy()
    reads: list[str] = []
    monkeypatch.setattr(
        drain_policy,
        "provider_limit_hold",
        lambda config, agent_id: {
            "heldReason": "provider-limit",
            "limitedUntil": RESET.isoformat(),
            "detail": "held until the reset",
        },
    )
    monkeypatch.setattr(drain_policy, "read_agent", lambda config, agent_id: reads.append(agent_id))
    monkeypatch.setattr(held, "_release_claim", lambda *args: None)
    monkeypatch.setattr(drain_policy, "ended_stamp", lambda config, agent_id: None)

    with pytest.raises(held._DrainPause) as paused:
        held._check_drain_readiness(_batch(tmp_path, _store(tmp_path)))

    assert paused.value.outcome["outcome"] == "provider-limit"
    assert reads == []


def test_the_owned_ended_stamp_still_earns_one_read(monkeypatch, tmp_path: Path) -> None:
    drain_policy = _policy()
    reads: list[str] = []
    reading = AgentReading(reachable=True, found=True, archived=False)
    monkeypatch.setattr(
        drain_policy,
        "provider_limit_hold",
        lambda config, agent_id: {
            "heldReason": "provider-limit",
            "limitedUntil": RESET.isoformat(),
            "detail": "held until the reset",
        },
    )
    monkeypatch.setattr(
        drain_policy, "read_agent", lambda config, agent_id: reads.append(agent_id) or reading
    )
    monkeypatch.setattr(held, "_release_claim", lambda *args: None)
    monkeypatch.setattr(drain_policy, "ended_stamp", lambda config, agent_id: "ended-stamp-1")
    folder = _store(tmp_path)

    for _ in range(3):
        with pytest.raises(held._DrainPause):
            held._check_drain_readiness(_batch(tmp_path, folder))

    assert reads == [AGENT]


# ── rule 7: one failed-read line per tick (L184-R1-F32) ───────────────────────


def _usage_config(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "agents-remember" / "provider-limits.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(hold, "provider_limits_path", lambda config: path)
    monkeypatch.setattr(
        hold,
        "read_role_agents",
        lambda home: {
            AGENT: {"role": "worker", "task": "repo/master/leaf.json", "reportPath": "r"}
        },
    )
    monkeypatch.setattr(
        hold, "record_usage_reads", lambda config, reads: {"recorded": sorted(reads)}
    )
    return path


def test_a_batch_of_failed_reads_writes_one_warning_line(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    other = "22222222-2222-5222-8222-222222222222"
    third = "33333333-3333-5333-8333-333333333333"
    _usage_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hold,
        "bridge_call",
        lambda config, command, payload: {
            "reports": [
                {"agentId": AGENT, "error": "rate limited"},
                {"agentId": other, "error": "Unknown agent: 22222222"},
                {"agentId": third, "error": "read timed out"},
                {"agentId": AGENT, "entries": []},
            ]
        },
    )

    recorded = hold.read_usage_for_agents(NO_CONFIG, [AGENT, other, third])

    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert AGENT in message and other in message and third in message
    # The one successful read of a failing batch is still its own fact.
    assert recorded == {"recorded": [AGENT]}


def test_a_timed_out_read_writes_one_warning_line(monkeypatch, tmp_path: Path, caplog) -> None:
    _usage_config(monkeypatch, tmp_path)

    def timeout(config, command, payload):
        raise PaseoBridgeFailure("paseo_bridge_timeout", "the bridge did not answer in 55 s")

    monkeypatch.setattr(hold, "bridge_call", timeout)

    assert hold.read_usage_for_agents(NO_CONFIG, [AGENT]) is None
    warnings = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "55 s" in warnings[0].getMessage()


def test_a_batch_without_failures_writes_no_warning(monkeypatch, tmp_path: Path, caplog) -> None:
    _usage_config(monkeypatch, tmp_path)
    monkeypatch.setattr(
        hold,
        "bridge_call",
        lambda config, command, payload: {"reports": [{"agentId": AGENT, "entries": []}]},
    )

    assert hold.read_usage_for_agents(NO_CONFIG, [AGENT]) == {"recorded": [AGENT]}
    assert [record for record in caplog.records if record.levelno == logging.WARNING] == []
