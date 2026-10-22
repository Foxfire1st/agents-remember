"""MIK-R190: a limit comes from the host's usage report, and one file keeps it.

The cases here are the packet's evidence 3 and 5 for the product's part: what the
one status function does with a running provider subagent, what rule 6 makes of a
report, what rule 8 stores, and what rule 13 shows a task.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.observer.host_activity import PublishedAgentActivity, TaskAgentActivity
from agents_remember.observer.projection import TaskEntry, WorkspaceProjection
from agents_remember.observer.provider_limits import (
    PROVIDER_LIMITS_SCHEMA,
    LimitedSeat,
    ProviderLimit,
    ProviderLimits,
    limited_until,
    read_provider_limits,
    task_provider_limit,
)
from agents_remember.observer.task_entries import TaskLimitFacts, task_state
from agents_remember.serving._app_common import _ProjectionBodyCache as ProjectionBodyCache
from agents_remember.serving.delta import stable_projection_state
from agents_remember.serving.paseo import provider_limits as writer
from agents_remember.serving.projections.projection_store import LATEST_STATE, write_projection
from pydantic import ValidationError

NOW = datetime(2026, 10, 20, 18, 0, tzinfo=UTC)
# Rule 8's writer takes the runtime config, but these cases patch ``provider_limits_path``
# to a temporary file, so the path function never reads it. The placeholder keeps the
# product's own parameter type instead of widening it to a config the writer cannot use.
NO_CONFIG = cast(McpRuntimeConfig, None)
RESET = NOW + timedelta(hours=2)


def _window(
    window_id: str = "five_hour",
    label: str = "Session",
    *,
    used: float = 100.0,
    resets: datetime | None = None,
    summary: bool = True,
) -> dict[str, object]:
    window: dict[str, object] = {
        "id": window_id,
        "label": label,
        "usedPct": used,
        "remainingPct": 100.0 - used,
        "resetsAt": (resets or RESET).isoformat(),
    }
    if summary:
        window["summary"] = True
    return window


def _report(*windows: dict[str, object], details=None, balances=None) -> dict[str, object]:
    return {
        "status": "available",
        "windows": list(windows),
        "details": list(details or []),
        "balances": list(balances or []),
    }


def _entry(report: dict[str, object], **extra: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "claude-max",
        "sourceLabel": "Claude",
        "account": {"label": "Max 20x"},
        "fetchedAt": NOW.isoformat(),
        "report": report,
    }
    entry.update(extra)
    return entry


# ── rule 6: what a limit is ────────────────────────────────────────────────────


def test_an_exhausted_account_wide_window_holds_work_until_its_reset() -> None:
    judged = writer.judge_report(_report(_window()), NOW)

    assert judged is not None
    until, windows = judged
    assert until == RESET
    assert [window.id for window in windows] == ["five_hour"]


def test_a_scoped_window_does_not_hold_a_seat() -> None:
    """A "Weekly · Fable" at 100 % must not hold an Opus seat for days."""

    scoped = _window("weekly_fable", "Weekly · Fable", summary=False)

    assert writer.judge_report(_report(scoped), NOW) is None


def test_extra_usage_enabled_is_not_limited() -> None:
    details = [{"id": "extra_usage", "label": "Extra usage", "value": "Enabled"}]

    assert writer.judge_report(_report(_window(), details=details), NOW) is None


def test_codex_credits_remaining_is_not_limited() -> None:
    balances = [{"id": "credits", "label": "Credits", "remaining": 12.5}]

    assert writer.judge_report(_report(_window(), balances=balances), NOW) is None


def test_a_full_window_without_a_future_reset_is_not_limited() -> None:
    past = _window(resets=NOW - timedelta(minutes=1))
    missing = {"id": "five_hour", "label": "Session", "usedPct": 100.0, "summary": True}

    assert writer.judge_report(_report(past), NOW) is None
    assert writer.judge_report(_report(missing), NOW) is None


def test_an_unavailable_report_is_not_limited() -> None:
    unavailable = {"status": "unavailable", "windows": [], "problem": {"kind": "expired"}}

    assert writer.judge_report(unavailable, NOW) is None


def test_until_is_the_latest_reset_of_the_exhausted_windows() -> None:
    later = NOW + timedelta(hours=5)
    judged = writer.judge_report(
        _report(_window("five_hour", "Session"), _window("weekly", "Weekly", resets=later)), NOW
    )

    assert judged is not None
    assert judged[0] == later


# ── rule 7: which reads set or clear a fact ────────────────────────────────────


def test_an_available_report_that_is_not_exhausted_clears_the_fact() -> None:
    judged = writer.judge_read([_entry(_report(_window(used=34.0)))], NOW)

    assert judged == {"claude-max": None}


def test_an_unavailable_read_leaves_the_fact_alone() -> None:
    unavailable = _entry({"status": "unavailable", "windows": []})

    assert writer.judge_read([unavailable], NOW) == {}


def test_the_429_error_clears_nothing_and_is_judged_by_last_successful() -> None:
    error = _entry({"status": "error", "message": "Rate limited by Claude. Try again in 3m."})

    assert writer.judge_read([error], NOW) == {}

    carried = {
        **error,
        "lastSuccessful": {"fetchedAt": NOW.isoformat(), "report": _report(_window())},
    }
    judged = writer.judge_read([carried], NOW)

    assert judged["claude-max"] is not None
    assert judged["claude-max"].observedAt == NOW


def test_last_successful_that_is_not_exhausted_clears_the_fact() -> None:
    carried = {
        **_entry({"status": "error"}),
        "lastSuccessful": {
            "fetchedAt": NOW.isoformat(),
            "report": _report(_window(used=12.0)),
        },
    }

    assert writer.judge_read([carried], NOW) == {"claude-max": None}


# ── rule 7 / 0.12.0's nested lastSuccessful (L184-R1-F02) ─────────────────────


def test_the_host_valid_nested_last_successful_is_judged_with_its_own_fetch_time() -> None:
    """0.12.0 carries ``{fetchedAt, report}``; a flattened report is not the host's shape."""

    fetched = NOW - timedelta(minutes=5)
    carried = {
        **_entry({"status": "error", "error": "fetch failed"}),
        "fetchedAt": NOW.isoformat(),
        "lastSuccessful": {"fetchedAt": fetched.isoformat(), "report": _report(_window())},
    }

    judged = writer.judge_read([carried], NOW)

    assert judged["claude-max"] is not None
    assert judged["claude-max"].until == RESET
    assert judged["claude-max"].observedAt == fetched


def test_a_flat_last_successful_is_not_the_host_schema_and_clears_nothing() -> None:
    flat = {
        **_entry({"status": "error", "error": "fetch failed"}),
        "lastSuccessful": {"fetchedAt": NOW.isoformat(), **_report(_window())},
    }

    assert writer.judge_read([flat], NOW) == {}


def test_a_nested_last_successful_that_is_not_exhausted_clears_the_fact() -> None:
    carried = {
        **_entry({"status": "error", "error": "fetch failed"}),
        "lastSuccessful": {
            "fetchedAt": NOW.isoformat(),
            "report": _report(_window(used=12.0)),
        },
    }

    assert writer.judge_read([carried], NOW) == {"claude-max": None}


def test_an_error_without_fallback_or_an_unavailable_read_retains_the_fact(
    tmp_path: Path, monkeypatch
) -> None:
    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry(_report(_window()))]}, now=NOW)

    error_only = {
        **_entry({"status": "error", "error": "fetch failed"}),
        "fetchedAt": NOW.isoformat(),
    }
    unavailable = _entry({"status": "unavailable", "windows": []})
    after = writer.record_usage_reads(
        NO_CONFIG, {WORKER: [error_only, unavailable]}, now=NOW + timedelta(minutes=1)
    )

    assert after is not None
    assert after.reports["claude-max"].until == RESET
    assert limited_until(after, WORKER, NOW + timedelta(minutes=1)) == RESET


# ── rule 8: the agent row is its last successful read (L184-R1-F03) ───────────


def test_two_seats_keep_the_shared_report_mapping_before_the_account_exhausts(
    tmp_path: Path, monkeypatch
) -> None:
    """A non-exhausted read still names its account, so a later exhaustion reaches it."""

    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)

    calm = writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry(_report(_window(used=20.0)))]}, now=NOW
    )
    assert calm is not None
    assert calm.reports == {}
    assert calm.agents == {WORKER: ("claude-max",)}
    assert limited_until(calm, WORKER, NOW) is None

    exhausted = writer.record_usage_reads(
        NO_CONFIG, {REVIEWER: [_entry(_report(_window()))]}, now=NOW
    )

    assert exhausted is not None
    assert limited_until(exhausted, WORKER, NOW) == RESET
    assert limited_until(exhausted, REVIEWER, NOW) == RESET


def test_a_successful_read_that_names_no_report_replaces_the_mapping(
    tmp_path: Path, monkeypatch
) -> None:
    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry(_report(_window(used=20.0)))]}, now=NOW)

    empty = writer.record_usage_reads(NO_CONFIG, {WORKER: []}, now=NOW)

    assert empty is not None
    assert empty.agents == {}


def test_a_failed_read_preserves_that_agents_mapping(tmp_path: Path, monkeypatch) -> None:
    """An agent whose read failed this tick is not a read that named nothing."""

    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry(_report(_window(used=20.0)))]}, now=NOW)

    after = writer.record_usage_reads(NO_CONFIG, {}, now=NOW)

    assert after is not None
    assert after.agents == {WORKER: ("claude-max",)}


# ── rule 8: one record of the fact ─────────────────────────────────────────────


def _limits_file(tmp_path: Path) -> Path:
    directory = tmp_path / "agents-remember"
    directory.mkdir(parents=True)
    (directory / "role-agents.json").write_text(
        json.dumps(
            {
                "schema": "ar-role-agents/v1",
                "agents": {
                    "11111111-1111-5111-8111-111111111111": {
                        "role": "worker",
                        "task": "repo/master/leaf.json",
                        "reportPath": "/tmp/report.md",
                    },
                    "22222222-2222-5222-8222-222222222222": {
                        "role": "reviewer",
                        "task": "repo/master/leaf.json",
                        "reportPath": "/tmp/report2.md",
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    return directory / "provider-limits.json"


WORKER = "11111111-1111-5111-8111-111111111111"
REVIEWER = "22222222-2222-5222-8222-222222222222"


def test_one_exhausted_account_holds_every_seat_on_it(tmp_path: Path, monkeypatch) -> None:
    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    entry = _entry(_report(_window()))

    limits = writer.record_usage_reads(NO_CONFIG, {WORKER: [entry], REVIEWER: [entry]}, now=NOW)

    assert limits is not None
    assert set(limits.agents) == {WORKER, REVIEWER}
    assert limited_until(limits, WORKER, NOW) == RESET
    assert limited_until(limits, REVIEWER, NOW) == RESET
    stored = read_provider_limits(path)
    assert stored.reports["claude-max"].until == RESET
    assert stored.reports["claude-max"].accountLabel == "Max 20x"


def test_a_later_non_exhausted_read_clears_the_fact(tmp_path: Path, monkeypatch) -> None:
    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry(_report(_window()))]}, now=NOW)

    cleared = writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry(_report(_window(used=34.0)))]}, now=NOW
    )

    assert cleared is not None
    assert cleared.reports == {}
    # The seat's row still names the account its last read named; only the fact is gone.
    assert cleared.agents == {WORKER: ("claude-max",)}
    assert limited_until(cleared, WORKER, NOW) is None


def test_an_expired_fact_is_dropped_on_the_next_write(tmp_path: Path, monkeypatch) -> None:
    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry(_report(_window()))]}, now=NOW)

    later = writer.record_usage_reads(NO_CONFIG, {}, now=RESET + timedelta(minutes=1))

    assert later is not None
    assert later.reports == {}


def test_an_agent_that_left_the_role_record_keeps_no_row(tmp_path: Path, monkeypatch) -> None:
    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry(_report(_window()))]}, now=NOW)
    document = json.loads((path.parent / "role-agents.json").read_text(encoding="utf-8"))
    del document["agents"][WORKER]
    (path.parent / "role-agents.json").write_text(json.dumps(document), encoding="utf-8")

    after = writer.record_usage_reads(NO_CONFIG, {}, now=NOW)

    assert after is not None
    assert after.agents == {}


def test_the_stored_file_is_the_schema_the_plugin_reads(tmp_path: Path, monkeypatch) -> None:
    path = _limits_file(tmp_path)
    monkeypatch.setattr(writer, "provider_limits_path", lambda config: path)
    writer.record_usage_reads(NO_CONFIG, {WORKER: [_entry(_report(_window()))]}, now=NOW)

    body = json.loads(path.read_text(encoding="utf-8"))

    assert body["schema"] == PROVIDER_LIMITS_SCHEMA
    assert set(body["reports"]) == {"claude-max"}
    assert isinstance(body["agents"][WORKER], list)


# ── rule 4: works means in a turn, or a running subagent ───────────────────────


def _activity(state: str, **fields: object) -> PublishedAgentActivity:
    return PublishedAgentActivity.model_validate({"state": state, **fields})


def test_an_idle_agent_with_a_running_subagent_works_with_its_own_age() -> None:
    started = NOW - timedelta(seconds=40)
    state = task_state(
        "inProgress",
        [_activity("idle", runningSubagents=1, subagentsSince=started)],
        host_known=True,
    )

    assert state.activity == "works"
    assert state.eventAt == started.isoformat()


def test_an_ended_agent_with_a_stale_count_does_not_work() -> None:
    state = task_state(
        "inProgress",
        [_activity("ended", runningSubagents=3, subagentsSince=NOW - timedelta(minutes=5))],
        host_known=True,
    )

    assert state.activity == "paused"


def test_an_agent_in_a_turn_keeps_its_turns_start() -> None:
    turn_started = NOW - timedelta(minutes=9)
    state = task_state(
        "inProgress",
        [_activity("in-turn", eventAt=turn_started, runningSubagents=1, subagentsSince=NOW)],
        host_known=True,
    )

    assert state.activity == "works"
    assert state.eventAt == turn_started.isoformat()


def test_the_published_model_admits_the_two_subagent_fields_and_no_third() -> None:
    admitted = _activity("idle", runningSubagents=2, subagentsSince=NOW)

    assert admitted.runningSubagents == 2
    with pytest.raises(ValidationError):
        PublishedAgentActivity.model_validate({"state": "idle", "subagentCount": 2})


# ── rule 13: the task's word ──────────────────────────────────────────────────


def _limits(*, until: datetime = RESET) -> ProviderLimits:
    return ProviderLimits.model_validate(
        {
            "schema": PROVIDER_LIMITS_SCHEMA,
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
            "agents": {WORKER: ["claude-max"]},
        }
    )


def test_a_paused_task_with_a_limited_seat_carries_the_limit_and_its_seats() -> None:
    seats = (
        TaskAgentActivity(
            "repo/master/leaf.json", _activity("idle"), agent_id=WORKER, role="worker"
        ),
    )
    state = task_state(
        "inProgress",
        [_activity("idle")],
        host_known=True,
        limited=TaskLimitFacts(_limits(), seats, NOW),
    )

    assert state.activity == "paused"
    assert state.providerLimit is not None
    assert state.providerLimit.until == RESET
    seat = state.providerLimit.seats[0]
    assert (seat.role, seat.agentId, seat.source, seat.account, seat.window) == (
        "worker",
        WORKER,
        "Claude",
        "Max 20x",
        "Session",
    )
    assert seat.resetsAt == RESET


def test_a_working_task_shows_no_limit() -> None:
    seats = (
        TaskAgentActivity(
            "repo/master/leaf.json", _activity("in-turn"), agent_id=WORKER, role="worker"
        ),
    )
    state = task_state(
        "inProgress",
        [_activity("in-turn", eventAt=NOW)],
        host_known=True,
        limited=TaskLimitFacts(_limits(), seats, NOW),
    )

    assert state.activity == "works"
    assert state.providerLimit is None


def test_a_stale_limit_is_no_limit() -> None:
    seats = (
        TaskAgentActivity(
            "repo/master/leaf.json", _activity("idle"), agent_id=WORKER, role="worker"
        ),
    )

    assert task_provider_limit(_limits(), seats, RESET) is None


def _limited_projection() -> WorkspaceProjection:
    """One projection holding a paused task with a limited seat: rule 13's served shape."""

    limit = ProviderLimit(
        until=RESET,
        seats=(
            LimitedSeat(
                role="worker",
                agentId=WORKER,
                source="Claude",
                account="Max 20x",
                window="Session",
                resetsAt=RESET,
            ),
        ),
    )
    return WorkspaceProjection(
        generatedAt=NOW.isoformat(),
        tasks=[
            TaskEntry(
                key="repo/master/leaf.json",
                id="LEAF",
                title="A limited leaf",
                stage="started",
                activity="paused",
                providerLimit=limit,
            )
        ],
    )


# Three writers turn the projection into JSON: the tick's file, the served body and the stable
# form the delta streams. Each dumped the datetimes of `providerLimit` unchanged, so one limited
# task broke all three: the tick raised on every interval and `/api/state` answered 500.
def test_a_limited_task_is_written_to_the_projection_file_as_json(tmp_path: Path) -> None:
    projection = _limited_projection()

    write_projection(tmp_path, projection)

    document = json.loads((tmp_path / LATEST_STATE).read_text(encoding="utf-8"))
    assert isinstance(document["tasks"][0]["providerLimit"]["until"], str)
    assert isinstance(document["tasks"][0]["providerLimit"]["seats"][0]["resetsAt"], str)
    written = WorkspaceProjection.model_validate(document).tasks[0]
    assert written.providerLimit == projection.tasks[0].providerLimit


def test_the_served_body_of_a_limited_projection_is_json() -> None:
    body = ProjectionBodyCache().body(_limited_projection())

    assert isinstance(body["tasks"][0]["providerLimit"]["until"], str)
    json.dumps(body)  # the served route's own encode; raised before the fix
    assert body["tasks"][0]["providerLimit"]["seats"][0]["agentId"] == WORKER


def test_the_stable_form_of_a_limited_projection_is_json() -> None:
    state = stable_projection_state(_limited_projection())

    task = state.tasks["repo/master/leaf.json"]
    assert isinstance(task["providerLimit"]["until"], str)
    json.dumps(task)  # the delta's own encode; raised before the fix


# The two report shapes below are the ones the developer's installation actually served in the one
# read-only `listUsageReports` read F17 requires (L184-S3-f17-installation-usage-read.log): Claude
# available at 5 % / 83 % with the account-wide windows marked `summary: true`, one scoped window
# unmarked, and an `extra_usage` detail whose value is the string "Disabled"; Codex available at
# 20 % with one summary window and a `credits` balance row present whose `remaining` is not
# reported. Neither is a limit, and the fixtures in this file are shaped from that read rather than
# invented. `remaining` is the field the pinned `UsageBalance` carries — `value` belongs to
# `UsageDetail`, not to a balance.
def test_the_recorded_claude_report_is_not_a_limit() -> None:
    report = _report(
        _window("five_hour", "Session", used=5.0),
        _window("weekly", "Weekly", used=83.0, resets=NOW + timedelta(days=4)),
        _window("weekly_model_fable", "Weekly · Fable", used=0.0, summary=False),
        details=[{"id": "extra_usage", "label": "Extra usage", "value": "Disabled"}],
    )

    assert writer.judge_report(report, NOW) is None


def test_the_recorded_codex_report_is_not_a_limit() -> None:
    report = _report(
        _window("weekly", "Weekly", used=20.0, resets=NOW + timedelta(days=6)),
        balances=[{"id": "credits", "label": "Credits", "remaining": None}],
    )

    assert writer.judge_report(report, NOW) is None
