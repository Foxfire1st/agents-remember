"""The stall clock the projection inputs read leaves a seat with a declared wait alone.

While the seat's wait has its check to come, the native attention reader counts no silence for
its task; once the check time has passed, the clock starts as for any quiet seat.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents_remember.observer.host_activity import (
    HostActivitySnapshot,
    PublishedAgentActivity,
    TaskAgentActivity,
)
from agents_remember.observer.task_entries import build_task_entries
from agents_remember.serving.projections.projection_inputs import ProjectionInputState
from test_attention_native_inputs import _publication
from test_attention_native_inputs import _task as _source
from test_paseo_role_agents import runtime_config

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)


def test_the_stall_clock_leaves_a_waiting_seat_alone_until_its_check_passes(tmp_path) -> None:
    """The stall watch counts no silence while a seat's declared wait has its check to come."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    reader = ProjectionInputState(
        host_activity_reconcile=lambda _config: {"publication": {"bootId": "boot-a"}},
        host_supervisor_armed=lambda: True,
    )._native_attention_reader
    due = NOW + timedelta(minutes=3)
    seat = PublishedAgentActivity.model_validate(
        {
            "state": "idle",
            "eventAt": NOW,
            "declaredWait": {"waitsFor": ["background"], "since": NOW, "checkDueAt": due},
        }
    )
    key = "repo/master/leaf.json"
    host = HostActivitySnapshot(
        known=True,
        agents=(TaskAgentActivity(key, seat, agent_id="seat", role="worker"),),
        boot_id="boot-a",
    )

    def read(at: datetime):
        entries = build_task_entries([_source()], [], host, at)
        return entries, reader.read(config, host=host, task_entries=entries, now=at)

    entries, waiting = read(NOW + timedelta(minutes=1))
    assert entries.tasks[0].activity == "waiting"
    assert waiting.stall_clocks == {}, "no silence is counted while the check is to come"
    after = due + timedelta(minutes=2)
    entries, lapsed = read(after)
    assert entries.tasks[0].activity == "paused"
    assert lapsed.stall_clocks == {key: after.isoformat()}, (
        "the clock starts when the check has passed"
    )
