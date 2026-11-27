"""Focused behavioral tests for window-scoped provider-limit suppression (L60).

Covers mixed-version v1 compatibility without sidecar, per-window ``recordedAt``
preservation across re-reads, multi-window ``until`` recomputation, single-window
suppression triggering ``clearedSeats`` and stopping periodic rechecks, causal
turn-start guard (``eventAt > recordedAt``, sibling race rejection, missing/idle/
incomplete/legacy fail-closed, ``permission`` state), non-blocking async MCP hook,
expired sidecar pruning across two population sizes, and ``provider-limits`` CLI
``status``, ``suppress``, and ``unsuppress`` subcommands.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import anyio
import pytest
from agents_remember.application.agent_binding import (
    AGENT_ID_VARIABLE,
    REPORT_PATH_VARIABLE,
    REQUEST_ID_VARIABLE,
    ROLE_VARIABLE,
    AgentBinding,
)
from agents_remember.cli.__main__ import main as cli_main
from agents_remember.cli.provider_limit_hold import provider_limit_hold
from agents_remember.kernel.primitives.paseo_host_contract import PASEO_VERSION
from agents_remember.kernel.primitives.paseo_runtime_settings import parse_paseo_runtime_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, load_config
from agents_remember.mcp.server import create_server
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.provider_limits import (
    PROVIDER_LIMITS_SCHEMA,
    ProviderLimits,
    limited_until,
    read_provider_limits,
)
from agents_remember.serving.paseo import provider_limits as writer
from agents_remember.serving.paseo.provider_limit_suppressions import (
    PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
    SuppressedLimitWindow,
    SuppressionSelector,
    provider_limit_suppressions_path,
    read_provider_limit_suppressions,
)
from mcp.types import CallToolResult
from paseo_runtime_test_support import write_runtime_block

NOW = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)
RESET_5H = NOW + timedelta(hours=2)
RESET_WEEKLY = NOW + timedelta(days=4)
WORKER = "11111111-1111-5111-8111-111111111111"
REVIEWER = "22222222-2222-5222-8222-222222222222"
MANAGER = "33333333-3333-5333-8333-333333333333"
NO_CONFIG = cast(McpRuntimeConfig, None)


def _window(
    window_id: str = "weekly",
    label: str = "Weekly",
    *,
    used: float = 100.0,
    resets: datetime = RESET_WEEKLY,
) -> dict[str, object]:
    return {
        "id": window_id,
        "label": label,
        "summary": True,
        "usedPct": used,
        "remainingPct": 100.0 - used,
        "resetsAt": resets.isoformat(),
    }


def _entry(
    report_id: str,
    *windows: dict[str, object],
    fetched_at: datetime = NOW,
) -> dict[str, object]:
    return {
        "id": report_id,
        "sourceLabel": "Claude",
        "account": {"label": "Max 20x"},
        "fetchedAt": fetched_at.isoformat(),
        "report": {"status": "available", "windows": list(windows)},
    }


def _write_role_agents(folder: Path, seats: dict[str, str]) -> None:
    (folder / "role-agents.json").write_text(
        json.dumps(
            {
                "schema": "ar-role-agents/v1",
                "agents": {
                    agent_id: {
                        "role": role,
                        "task": "repo/master/leaf.json",
                        "reportPath": f"/tmp/{agent_id}.md",
                    }
                    for agent_id, role in seats.items()
                },
            }
        ),
        encoding="utf-8",
    )


def _write_host_activity(
    folder: Path,
    agents: dict[str, dict[str, Any]],
    *,
    complete: bool = True,
) -> None:
    (folder / "host-activity.json").write_text(
        json.dumps(
            {
                "bootId": "boot-1",
                "complete": complete,
                "agents": agents,
                "ignoredEvents": 0,
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def limits_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    folder = tmp_path / "agents-remember"
    folder.mkdir()
    _write_role_agents(folder, {WORKER: "worker", REVIEWER: "reviewer", MANAGER: "manager"})
    path = folder / "provider-limits.json"
    monkeypatch.setattr(writer, "provider_limits_path", lambda _config: path)
    return path


def _settings_file(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "coordination"
    (root / "tasks" / "agents-remember" / "master").mkdir(parents=True, exist_ok=True)
    (root / "repo").mkdir(exist_ok=True)
    (root / "memory").mkdir(exist_ok=True)
    paseo_home = tmp_path / "paseo-home"
    (paseo_home / "agents-remember").mkdir(parents=True, exist_ok=True)
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "coordinationRoot": str(root),
                "workspaceRoot": str(root / "workspace"),
                "transcriptRoot": str(root / "transcripts"),
                "repositories": {
                    "agents-remember": {
                        "path": str(root / "repo"),
                        "memoryRoot": str(root / "memory"),
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    runtime = parse_paseo_runtime_settings(
        {
            "installPrefix": (tmp_path / "prefix").as_posix(),
            "home": paseo_home.as_posix(),
            "listen": "127.0.0.1:6835",
            "version": PASEO_VERSION,
            "providers": {},
            "embed": [],
        }
    )
    assert runtime is not None
    write_runtime_block(settings_path, runtime)
    return settings_path, paseo_home


# ── Rule 1: v1 compatibility, recordedAt preservation, sidecar pruning ────────


def test_v1_provider_limits_without_sidecar_reads_as_live_limit(limits_path: Path) -> None:
    limits_path.write_text(
        json.dumps(
            {
                "schema": PROVIDER_LIMITS_SCHEMA,
                "reports": {
                    "claude-max": {
                        "until": RESET_WEEKLY.isoformat(),
                        "sourceLabel": "Claude",
                        "accountLabel": "Max 20x",
                        "windows": [
                            {
                                "id": "weekly",
                                "label": "Weekly",
                                "resetsAt": RESET_WEEKLY.isoformat(),
                            }
                        ],
                        "observedAt": NOW.isoformat(),
                    }
                },
                "agents": {WORKER: ["claude-max"]},
                "updatedAt": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert not provider_limit_suppressions_path(limits_path).exists()

    stored = read_provider_limits(limits_path)
    assert limited_until(stored, WORKER, NOW) == RESET_WEEKLY
    status = writer.provider_limits_status(limits_path, now=NOW)
    assert status.exit_code == 0
    assert status.payload["reports"][0]["reportId"] == "claude-max"
    assert status.payload["reports"][0]["recordedAt"] is None
    assert status.payload["reports"][0]["windows"][0]["recordedAt"] is None


def test_recorded_at_is_preserved_across_rereads_and_v1_schema_stays_clean(
    limits_path: Path,
) -> None:
    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("claude-max", _window("weekly"))]}, now=NOW
    )
    sidecar_path = provider_limit_suppressions_path(limits_path)
    first_sidecar = read_provider_limit_suppressions(sidecar_path)
    assert len(first_sidecar.recordedWindows) == 1
    assert first_sidecar.recordedWindows[0].recordedAt == NOW

    # A 5-minute recheck and sibling turn end re-confirm the same window without bumping recordedAt.
    later = NOW + timedelta(minutes=5)
    writer.record_usage_reads(
        NO_CONFIG,
        {
            WORKER: [_entry("claude-max", _window("weekly"), fetched_at=later)],
            REVIEWER: [_entry("claude-max", _window("weekly"), fetched_at=later)],
        },
        now=later,
    )
    second_sidecar = read_provider_limit_suppressions(sidecar_path)
    assert second_sidecar.recordedWindows[0].recordedAt == NOW

    # provider-limits.json stays strictly v1 with zero unknown keys (extra="forbid").
    raw_limits = json.loads(limits_path.read_text(encoding="utf-8"))
    assert raw_limits["schema"] == PROVIDER_LIMITS_SCHEMA
    assert set(raw_limits) <= {"schema", "reports", "agents", "clearedSeats", "updatedAt"}
    assert ProviderLimits.model_validate(raw_limits).reports["claude-max"].until == RESET_WEEKLY


@pytest.mark.parametrize("count", [2, 20], ids=["small", "scaled"])
def test_expired_sidecar_entries_prune_in_usage_reads_and_periodic_update(
    limits_path: Path, count: int
) -> None:
    sidecar_path = provider_limit_suppressions_path(limits_path)
    expired_time = NOW - timedelta(seconds=1)
    live_time = RESET_WEEKLY
    entries = [
        SuppressedLimitWindow(
            reportId=f"report-{idx}",
            windowId="weekly",
            resetsAt=expired_time if idx < count - 1 else live_time,
            suppressedAt=NOW - timedelta(hours=1),
            source="operator",
        )
        for idx in range(count)
    ]
    sidecar_path.write_text(
        json.dumps(
            {
                "schema": PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
                "recordedWindows": [],
                "suppressedWindows": [
                    entry.model_dump(mode="json", exclude_none=True) for entry in entries
                ],
                "updatedAt": (NOW - timedelta(hours=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    # _periodic_update / claim_due_reports prunes expired sidecar entries even with empty reports.
    writer.claim_due_reports(NO_CONFIG, now=NOW)
    after_periodic = read_provider_limit_suppressions(sidecar_path)
    assert len(after_periodic.suppressedWindows) == 1
    assert after_periodic.suppressedWindows[0].reportId == f"report-{count - 1}"

    # After the last entry expires, record_usage_reads prunes it too.
    writer.record_usage_reads(NO_CONFIG, {}, now=live_time)
    after_read = read_provider_limit_suppressions(sidecar_path)
    assert after_read.suppressedWindows == ()


# ── Rule 2: multi-window until recomputation & single-window clearedSeats ───


def test_multi_window_suppression_recomputes_until_and_keeps_other_window_live(
    limits_path: Path,
) -> None:
    writer.record_usage_reads(
        NO_CONFIG,
        {
            WORKER: [
                _entry(
                    "claude-max",
                    _window("five_hour", "Session", resets=RESET_5H),
                    _window("weekly", "Weekly", resets=RESET_WEEKLY),
                )
            ]
        },
        now=NOW,
    )
    assert read_provider_limits(limits_path).reports["claude-max"].until == RESET_WEEKLY

    result = writer.suppress_provider_limit_windows(
        limits_path,
        SuppressionSelector(report_id="claude-max", window_id="weekly"),
        now=NOW + timedelta(minutes=1),
    )
    assert result.exit_code == 0
    assert result.payload["clearedSeats"] == []

    stored = read_provider_limits(limits_path)
    assert stored.reports["claude-max"].until == RESET_5H
    assert [w.id for w in stored.reports["claude-max"].windows] == ["five_hour"]
    assert limited_until(stored, WORKER, NOW + timedelta(minutes=1)) == RESET_5H
    assert stored.clearedSeats == {}

    # A subsequent usage read still reporting both at 100% keeps weekly suppressed and five_hour live.
    after_reread = writer.record_usage_reads(
        NO_CONFIG,
        {
            WORKER: [
                _entry(
                    "claude-max",
                    _window("five_hour", "Session", resets=RESET_5H),
                    _window("weekly", "Weekly", resets=RESET_WEEKLY),
                    fetched_at=NOW + timedelta(minutes=6),
                )
            ]
        },
        now=NOW + timedelta(minutes=6),
    )
    assert after_reread is not None
    assert after_reread.reports["claude-max"].until == RESET_5H
    assert [w.id for w in after_reread.reports["claude-max"].windows] == ["five_hour"]


def test_single_window_suppression_drops_report_sets_cleared_seats_and_stops_polling(
    limits_path: Path,
) -> None:
    writer.record_usage_reads(
        NO_CONFIG,
        {
            WORKER: [_entry("claude-max", _window("weekly"))],
            REVIEWER: [_entry("claude-max", _window("weekly"))],
        },
        now=NOW,
    )
    suppressed_at = NOW + timedelta(minutes=1)
    result = writer.suppress_provider_limit_windows(
        limits_path, SuppressionSelector(report_id="claude-max"), now=suppressed_at
    )
    assert result.exit_code == 0
    assert result.payload["clearedSeats"] == [WORKER, REVIEWER]
    assert result.payload["remainingReports"] == []

    stored = read_provider_limits(limits_path)
    assert stored.reports == {}
    assert limited_until(stored, WORKER, suppressed_at) is None
    assert stored.clearedSeats[WORKER].clearedAt == suppressed_at
    assert stored.clearedSeats[WORKER].previousUntil == RESET_WEEKLY
    assert stored.clearedSeats[REVIEWER].clearedAt == suppressed_at

    # Periodic rechecks stop because no live report remains (INV-KM9V3K).
    _, due = writer.claim_due_reports(NO_CONFIG, now=NOW + timedelta(minutes=10))
    assert due == ()

    # If a DIFFERENT window (five_hour) exhausts later while weekly stays suppressed, it holds seats.
    later = NOW + timedelta(minutes=15)
    after_5h = writer.record_usage_reads(
        NO_CONFIG,
        {
            WORKER: [
                _entry(
                    "claude-max",
                    _window("five_hour", "Session", resets=RESET_5H),
                    _window("weekly", "Weekly", resets=RESET_WEEKLY),
                    fetched_at=later,
                )
            ]
        },
        now=later,
    )
    assert after_5h is not None
    assert after_5h.reports["claude-max"].until == RESET_5H
    assert [w.id for w in after_5h.reports["claude-max"].windows] == ["five_hour"]
    assert limited_until(after_5h, WORKER, later) == RESET_5H


def test_periodic_update_emits_clear_markers_when_dropping_sidecar_suppressed_report(
    limits_path: Path,
) -> None:
    """L60-R1-F01: claim_due_reports dropping a sidecar-suppressed report emits clearedSeats."""

    writer.record_usage_reads(
        NO_CONFIG,
        {
            WORKER: [
                _entry("claude-max", _window("weekly"), fetched_at=NOW - timedelta(minutes=5))
            ],
            REVIEWER: [
                _entry("claude-max", _window("weekly"), fetched_at=NOW - timedelta(minutes=5))
            ],
        },
        now=NOW - timedelta(minutes=5),
    )
    sidecar_path = provider_limit_suppressions_path(limits_path)
    sidecar_path.write_text(
        json.dumps(
            {
                "schema": PROVIDER_LIMIT_SUPPRESSIONS_SCHEMA,
                "recordedWindows": [],
                "suppressedWindows": [
                    SuppressedLimitWindow(
                        reportId="claude-max",
                        windowId="weekly",
                        resetsAt=RESET_WEEKLY,
                        suppressedAt=NOW - timedelta(minutes=1),
                        source="operator",
                    ).model_dump(mode="json", exclude_none=True)
                ],
                "updatedAt": (NOW - timedelta(minutes=1)).isoformat(),
            }
        ),
        encoding="utf-8",
    )

    updated, due = writer.claim_due_reports(NO_CONFIG, now=NOW)
    assert due == ()
    assert updated.reports == {}
    assert limited_until(updated, WORKER, NOW) is None
    assert updated.clearedSeats[WORKER].clearedAt == NOW
    assert updated.clearedSeats[WORKER].previousUntil == RESET_WEEKLY
    assert updated.clearedSeats[REVIEWER].clearedAt == NOW
    assert updated.clearedSeats[REVIEWER].previousUntil == RESET_WEEKLY
    persisted = read_provider_limits(limits_path)
    assert persisted.reports == {}
    assert set(persisted.clearedSeats) == {WORKER, REVIEWER}


def test_suppression_write_rolls_back_sidecar_when_limits_write_fails(
    limits_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """L60-R1-F01: two-file suppression mutation rolls back sidecar when limits write fails."""

    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("claude-max", _window("weekly"))]}, now=NOW
    )
    sidecar_path = provider_limit_suppressions_path(limits_path)
    before_sidecar_bytes = sidecar_path.read_bytes()
    before_limits_bytes = limits_path.read_bytes()

    def failing_limits_write(_path: Path, _limits: ProviderLimits) -> None:
        raise OSError("simulated limits write failure")

    monkeypatch.setattr(writer, "_write_limits", failing_limits_write)
    with pytest.raises(OSError, match="simulated limits write failure"):
        writer.suppress_provider_limit_windows(
            limits_path, SuppressionSelector(report_id="claude-max"), now=NOW + timedelta(minutes=1)
        )
    assert sidecar_path.read_bytes() == before_sidecar_bytes
    assert limits_path.read_bytes() == before_limits_bytes

    # Also verify rollback when no sidecar existed beforehand (legacy v1 file).
    sidecar_path.unlink()
    with pytest.raises(OSError, match="simulated limits write failure"):
        writer.suppress_provider_limit_windows(
            limits_path, SuppressionSelector(report_id="claude-max"), now=NOW + timedelta(minutes=2)
        )
    assert not sidecar_path.exists()
    assert limits_path.read_bytes() == before_limits_bytes


# ── Rule 3: causal turn-start guard & non-blocking async MCP hook ───────────


@pytest.mark.parametrize(
    ("activity_agents", "complete"),
    [
        ({}, True),
        ({WORKER: {"state": "idle", "eventAt": (NOW + timedelta(seconds=30)).isoformat()}}, True),
        ({WORKER: {"state": "in-turn", "eventAt": (NOW - timedelta(seconds=5)).isoformat()}}, True),
        ({WORKER: {"state": "in-turn", "eventAt": NOW.isoformat()}}, True),
        ({WORKER: {"state": "in-turn"}}, True),
        (
            {WORKER: {"state": "in-turn", "eventAt": (NOW + timedelta(seconds=30)).isoformat()}},
            False,
        ),
    ],
    ids=[
        "missing-agent",
        "idle",
        "mid-turn-earlier",
        "equal-timestamp",
        "no-eventAt",
        "incomplete",
    ],
)
def test_auto_suppression_fails_closed_without_strict_post_limit_active_turn(
    limits_path: Path,
    activity_agents: dict[str, dict[str, Any]],
    complete: bool,
) -> None:
    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("claude-max", _window("weekly"))]}, now=NOW
    )
    _write_host_activity(limits_path.parent, activity_agents, complete=complete)

    assert (
        writer.suppress_provider_limits_for_live_tool_call(
            NO_CONFIG, WORKER, now=NOW + timedelta(minutes=1)
        )
        is None
    )
    assert limited_until(read_provider_limits(limits_path), WORKER, NOW + timedelta(minutes=1)) == (
        RESET_WEEKLY
    )


def test_auto_suppression_fails_closed_on_missing_or_malformed_host_activity_and_legacy_record(
    limits_path: Path,
) -> None:
    # 1. Missing host-activity.json fails closed.
    writer.record_usage_reads(
        NO_CONFIG, {WORKER: [_entry("claude-max", _window("weekly"))]}, now=NOW
    )
    assert (
        writer.suppress_provider_limits_for_live_tool_call(
            NO_CONFIG, WORKER, now=NOW + timedelta(minutes=1)
        )
        is None
    )

    # 2. Malformed host-activity.json fails closed.
    (limits_path.parent / "host-activity.json").write_text("{not-json", encoding="utf-8")
    assert (
        writer.suppress_provider_limits_for_live_tool_call(
            NO_CONFIG, WORKER, now=NOW + timedelta(minutes=1)
        )
        is None
    )

    # 3. Legacy provider-limits.json without recordedAt fails closed even with valid in-turn activity.
    provider_limit_suppressions_path(limits_path).unlink()
    _write_host_activity(
        limits_path.parent,
        {WORKER: {"state": "in-turn", "eventAt": (NOW + timedelta(seconds=30)).isoformat()}},
    )
    assert (
        writer.suppress_provider_limits_for_live_tool_call(
            NO_CONFIG, WORKER, now=NOW + timedelta(minutes=1)
        )
        is None
    )
    assert limited_until(read_provider_limits(limits_path), WORKER, NOW + timedelta(minutes=1)) == (
        RESET_WEEKLY
    )

    # Operator CLI still works on legacy records with recordedAt is None.
    op_result = writer.suppress_provider_limit_windows(
        limits_path, SuppressionSelector(report_id="claude-max"), now=NOW + timedelta(minutes=2)
    )
    assert op_result.exit_code == 0
    assert (
        limited_until(read_provider_limits(limits_path), WORKER, NOW + timedelta(minutes=2)) is None
    )


@pytest.mark.parametrize("state", ["in-turn", "permission"])
def test_auto_suppression_succeeds_for_strictly_later_active_turn_and_records_evidence(
    limits_path: Path, state: str
) -> None:
    writer.record_usage_reads(
        NO_CONFIG,
        {
            WORKER: [_entry("claude-max", _window("weekly"))],
            REVIEWER: [_entry("claude-max", _window("weekly"))],
        },
        now=NOW,
    )
    turn_started = NOW + timedelta(seconds=15)
    call_at = NOW + timedelta(seconds=20)
    _write_host_activity(
        limits_path.parent,
        {WORKER: {"state": state, "eventAt": turn_started.isoformat()}},
    )

    updated = writer.suppress_provider_limits_for_live_tool_call(NO_CONFIG, WORKER, now=call_at)
    assert updated is not None
    assert updated.reports == {}
    assert updated.clearedSeats[WORKER].clearedAt == call_at
    assert updated.clearedSeats[REVIEWER].clearedAt == call_at

    sidecar = read_provider_limit_suppressions(provider_limit_suppressions_path(limits_path))
    assert len(sidecar.suppressedWindows) == 1
    entry = sidecar.suppressedWindows[0]
    assert entry.source == "auto-tool-call"
    assert entry.agentId == WORKER
    assert entry.turnStartedAt == turn_started
    assert entry.callAt == call_at
    assert entry.recordedAt == NOW


def test_mcp_server_hook_runs_off_event_loop_suppresses_and_contains_io_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path, paseo_home = _settings_file(tmp_path)
    ar_dir = paseo_home / "agents-remember"
    _write_role_agents(ar_dir, {WORKER: "worker"})
    config = load_config(str(settings_path))

    now = datetime.now(UTC)
    recorded_at = now - timedelta(minutes=2)
    turn_started = now - timedelta(minutes=1)
    reset_future = now + timedelta(days=3)
    writer.record_usage_reads(
        config,
        {
            WORKER: [
                _entry("claude-max", _window("weekly", resets=reset_future), fetched_at=recorded_at)
            ]
        },
        now=recorded_at,
    )
    _write_host_activity(
        ar_dir, {WORKER: {"state": "in-turn", "eventAt": turn_started.isoformat()}}
    )
    assert provider_limit_hold(config, WORKER, now) is not None

    binding_env = AgentBinding(
        agent_id=WORKER,
        role="worker",
        request_id="9f8e7d6c-5b4a-4392-8170-6f5e4d3c2b1a",
        report_path=str(tmp_path / "worker.md"),
        sprint_ref=TaskDocumentRef(repository="agents-remember", path="master/task.json"),
        master_ref=TaskDocumentRef(repository="agents-remember", path="task.json"),
        task_ref=TaskDocumentRef(repository="agents-remember", path="master/leaf.json"),
    ).environment()
    for key, value in binding_env.items():
        monkeypatch.setenv(key, value)
    server = create_server(config)
    # Remove env vars after create_server to prove _bound_agent_id was snapshotted once.
    for key in binding_env:
        monkeypatch.delenv(key, raising=False)
    assert server._bound_agent_id == WORKER

    loop_thread_id: int | None = None
    hook_thread_ids: list[int] = []
    real_suppress = writer.suppress_provider_limits_for_live_tool_call

    def wrapped_suppress(cfg: McpRuntimeConfig, agent_id: str, **kwargs: Any) -> Any:
        hook_thread_ids.append(threading.get_ident())
        return real_suppress(cfg, agent_id, **kwargs)

    monkeypatch.setattr(writer, "suppress_provider_limits_for_live_tool_call", wrapped_suppress)

    async def invoke() -> dict[str, Any]:
        nonlocal loop_thread_id
        loop_thread_id = threading.get_ident()
        result = await server.call_tool("server_info", {})
        assert isinstance(result, CallToolResult)
        assert result.structured_content is not None
        return result.structured_content

    payload = anyio.run(invoke)
    assert payload["ok"] is True
    assert len(hook_thread_ids) == 1
    assert loop_thread_id is not None
    assert hook_thread_ids[0] != loop_thread_id
    assert provider_limit_hold(config, WORKER, datetime.now(UTC)) is None

    # Limit-file I/O errors are suppressed and never fail an MCP tool call.
    def failing_suppress(_cfg: McpRuntimeConfig, _agent_id: str, **_kwargs: Any) -> Any:
        raise OSError("disk unavailable")

    monkeypatch.setattr(writer, "suppress_provider_limits_for_live_tool_call", failing_suppress)
    payload_after_err = anyio.run(invoke)
    assert payload_after_err["ok"] is True


def test_mcp_server_unbound_and_malformed_bindings_snapshot_none_and_skip_hook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings_path, _ = _settings_file(tmp_path)
    config = load_config(str(settings_path))
    for key in (AGENT_ID_VARIABLE, ROLE_VARIABLE, REQUEST_ID_VARIABLE, REPORT_PATH_VARIABLE):
        monkeypatch.delenv(key, raising=False)

    unbound = create_server(config)
    assert unbound._bound_agent_id is None

    called: list[str] = []
    monkeypatch.setattr(
        writer,
        "suppress_provider_limits_for_live_tool_call",
        lambda _cfg, agent_id, **_kw: called.append(agent_id),
    )
    result = anyio.run(unbound.call_tool, "server_info", {})
    assert isinstance(result, CallToolResult)
    assert result.structured_content is not None
    payload = result.structured_content
    assert payload["ok"] is True
    assert called == []

    monkeypatch.setenv("AR_PASEO_AGENT_ID", "not-a-valid-binding")
    malformed = create_server(config)
    assert malformed._bound_agent_id is None
    with pytest.raises(Exception, match="AR_PASEO_AGENT_ID"):
        anyio.run(malformed.call_tool, "server_info", {})
    assert called == []


# ── Rule 4: provider-limits CLI status|suppress|unsuppress ──────────────────


def _run_cli(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    code = cli_main(argv)
    out = capsys.readouterr().out
    return code, json.loads(out)


def _seed_cli_limits(
    tmp_path: Path,
) -> tuple[Path, Path, McpRuntimeConfig, datetime, datetime, datetime]:
    settings_path, paseo_home = _settings_file(tmp_path)
    ar_dir = paseo_home / "agents-remember"
    _write_role_agents(ar_dir, {WORKER: "worker", REVIEWER: "reviewer"})
    config = load_config(str(settings_path))
    now = datetime.now(UTC)
    reset_5h = now + timedelta(hours=2)
    reset_weekly = now + timedelta(days=4)
    writer.record_usage_reads(
        config,
        {
            WORKER: [
                _entry(
                    "claude-max",
                    _window("five_hour", "Session", resets=reset_5h),
                    _window("weekly", "Weekly", resets=reset_weekly),
                    fetched_at=now,
                )
            ],
            REVIEWER: [
                _entry(
                    "codex-pro",
                    _window("weekly", "Weekly", resets=reset_weekly),
                    fetched_at=now,
                )
            ],
        },
        now=now,
    )
    return settings_path, ar_dir, config, now, reset_5h, reset_weekly


def test_provider_limits_cli_status_and_refusals(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_path, _ar_dir, _config, now, _reset_5h, _reset_weekly = _seed_cli_limits(tmp_path)

    # status lists live reports with recordedAt, windows, mapped seats, and active suppressions.
    code, status = _run_cli(["provider-limits", "status", "--config", str(settings_path)], capsys)
    assert code == 0
    assert status["ok"] is True
    by_id = {row["reportId"]: row for row in status["reports"]}
    assert set(by_id) == {"claude-max", "codex-pro"}
    assert by_id["claude-max"]["mappedSeats"] == [WORKER]
    assert by_id["claude-max"]["recordedAt"] == now.isoformat()
    assert {w["id"] for w in by_id["claude-max"]["windows"]} == {"five_hour", "weekly"}

    # Multi-window --report without --window refuses cleanly (exit 2).
    code, refusal = _run_cli(
        ["provider-limits", "suppress", "--config", str(settings_path), "--report", "claude-max"],
        capsys,
    )
    assert code == 2
    assert refusal["ok"] is False
    assert refusal["error"]["code"] == "window_required"

    # Non-live report and non-live window refuse by name (exit 2).
    code, bad_report = _run_cli(
        ["provider-limits", "suppress", "--config", str(settings_path), "--report", "missing-id"],
        capsys,
    )
    assert code == 2
    assert bad_report["error"]["code"] == "report_not_live"
    assert "missing-id" in bad_report["error"]["message"]

    code, bad_window = _run_cli(
        [
            "provider-limits",
            "suppress",
            "--config",
            str(settings_path),
            "--report",
            "claude-max",
            "--window",
            "nonexistent",
        ],
        capsys,
    )
    assert code == 2
    assert bad_window["error"]["code"] == "window_not_live"
    assert "nonexistent" in bad_window["error"]["message"]


def test_provider_limits_cli_suppress_and_unsuppress(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    settings_path, ar_dir, config, now, reset_5h, reset_weekly = _seed_cli_limits(tmp_path)
    limits_file = ar_dir / "provider-limits.json"

    # Suppress one window on multi-window report (--report + --window) recomputes until.
    code, sup_win = _run_cli(
        [
            "provider-limits",
            "suppress",
            "--config",
            str(settings_path),
            "--report",
            "claude-max",
            "--window",
            "weekly",
        ],
        capsys,
    )
    assert code == 0
    assert sup_win["ok"] is True
    assert read_provider_limits(limits_file).reports["claude-max"].until == reset_5h

    # Suppress by --agent resolves mapped reports and clears seat.
    code, sup_agent = _run_cli(
        ["provider-limits", "suppress", "--config", str(settings_path), "--agent", REVIEWER],
        capsys,
    )
    assert code == 0
    assert sup_agent["clearedSeats"] == [REVIEWER]
    assert "codex-pro" not in read_provider_limits(limits_file).reports

    # status now shows both active operator suppressions.
    code, status_after = _run_cli(
        ["provider-limits", "--config", str(settings_path), "status"], capsys
    )
    assert code == 0
    assert len(status_after["suppressedWindows"]) == 2
    assert {s["source"] for s in status_after["suppressedWindows"]} == {"operator"}

    # unsuppress removes suppression so next usage read judges window normally.
    code, unsup = _run_cli(
        [
            "provider-limits",
            "unsuppress",
            "--config",
            str(settings_path),
            "--report",
            "codex-pro",
        ],
        capsys,
    )
    assert code == 0
    assert len(unsup["removed"]) == 1
    assert unsup["remainingSuppressions"] == 1

    # Re-reading codex-pro now re-establishes the limit.
    reread = writer.record_usage_reads(
        config,
        {REVIEWER: [_entry("codex-pro", _window("weekly", resets=reset_weekly), fetched_at=now)]},
        now=now + timedelta(seconds=5),
    )
    assert reread is not None
    assert "codex-pro" in reread.reports

    # Unsuppressing a non-existent suppression refuses cleanly (exit 2).
    code, missing_unsup = _run_cli(
        [
            "provider-limits",
            "unsuppress",
            "--config",
            str(settings_path),
            "--report",
            "codex-pro",
        ],
        capsys,
    )
    assert code == 2
    assert missing_unsup["error"]["code"] == "suppression_not_found"
