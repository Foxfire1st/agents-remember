"""Every attention entry is dismissed once per occurrence (MIK-R200@v2 rule 7 and rule 2a).

These cases drive the real projection pass and the real acknowledgement store over files the
product itself writes: a row is published, the developer acknowledges it, the next pass after a
page reload and after a service restart still hides it, and the acknowledgement leaves exactly when
its own occurrence ends -- a new prompt, a delivered message, a resumed turn, a distinct notice.

``test_attention_native_inputs`` owns how every fact arrives and ``test_attention_native_kinds``
owns how every row reads. This module owns the acknowledgement: what it hides, how long it hides
it, and what reclaims it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from agents_remember.controlplane.attention_dismissals import (
    MAX_DISMISSAL_BYTES,
    MAX_DISMISSALS,
    AttentionDismissalRecord,
    AttentionDismissalStore,
)
from agents_remember.controlplane.attention_notices import (
    PendingTerminalNotice,
    TerminalKind,
    begin_terminal_notice,
    confirm_terminal_notice,
    discard_terminal_notice,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.host_activity import HOST_ACTIVITY_FILE
from agents_remember.observer.projection import (
    Analytics,
    AttentionItem,
    TaskEntry,
    WorkspaceProjection,
)
from agents_remember.observer.reducer import AnalyticalInputs, build_attention_queue
from agents_remember.serving.actions import ActionEvaluationContext, evaluate_action
from agents_remember.serving.paseo.paseo_plugin_files import publish_role_agent
from agents_remember.serving.projections.projection_inputs import (
    ProjectionDomain,
    ProjectionInputs,
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)
from test_paseo_role_agents import runtime_config

NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
BOOT = "boot-a"
TASK_KEY = "repo/master/leaf.json"
AGENT = "11111111-1111-4111-8111-111111111111"
HELD_AGENT = "22222222-2222-4222-8222-222222222222"
SENDER = "33333333-3333-4333-8333-333333333333"

DOC: dict[str, Any] = {
    "schema": "ar-task-document/v1",
    "id": "L1",
    "slug": "leaf",
    "title": "Leaf",
    "kind": "subTask",
    "status": "inProgress",
    "repo": "repo",
    "createdAt": "2026-10-22T07:00:00+00:00",
}


# --- the world a pass reads ---------------------------------------------------


def _world(root: Path) -> tuple[Any, Path]:
    """A coordination root with one started leaf and one host this build can observe."""

    root.mkdir(parents=True, exist_ok=True)
    home = root / "host"
    config = runtime_config(root, home)
    _task_document(config)
    return config, home


def _task_document(config) -> None:
    path = config.coordination_root / "tasks" / "repo" / "master" / "leaf.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DOC), encoding="utf-8")


def _publication(home: Path, agents: dict[str, object], *, complete: bool = True) -> None:
    directory = home / "agents-remember"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / HOST_ACTIVITY_FILE).write_text(
        json.dumps({"bootId": BOOT, "complete": complete, "agents": agents, "ignoredEvents": 0}),
        encoding="utf-8",
    )


def _seat(home: Path, agent_id: str, *, task: str | None = TASK_KEY) -> None:
    publish_role_agent(
        home,
        agent_id,
        {"role": "worker", "task": task or "", "reportPath": "/r"},
    )


def _state() -> ProjectionInputState:
    """One projection process: a new instance is what a service restart leaves behind."""

    return ProjectionInputState(
        host_activity_reconcile=lambda _config: {"publication": {"bootId": BOOT, "reconcile": 1}},
        host_supervisor_armed=lambda: True,
    )


def _pass(state: ProjectionInputState, config, *, now: datetime = NOW) -> ProjectionInputs:
    return state.read(
        config,
        ProjectionReaders(repo_surfaces=lambda _config, _now: ([], [])),
        observer_root=config.coordination_root / "logs" / "observer",
        pass_=RefreshPass(
            now=now,
            refresh=ProjectionRefresh.change(
                ProjectionRefresh.full().domains | {ProjectionDomain.HOST_ACTIVITY}
            ),
        ),
    )


def _tasks(result: ProjectionInputs) -> list[TaskEntry]:
    entries = result.task_entries
    assert entries is not None, "a full pass always builds the task entries"
    return entries.tasks


def _queue(result: ProjectionInputs, *, now: datetime = NOW) -> list[AttentionItem]:
    return build_attention_queue(
        AnalyticalInputs(
            drift_snapshots=result.drift_snapshots,
            engine_start_progress=result.engine_start_progress,
            native_attention=result.native_attention,
            attention_dismissals=result.attention_dismissals,
        ),
        _tasks(result),
        now,
    )


def _ids(result: ProjectionInputs, *, now: datetime = NOW) -> list[str]:
    return [item.id for item in _queue(result, now=now)]


def _store(config) -> AttentionDismissalStore:
    return AttentionDismissalStore(config.coordination_root / "logs" / "observer")


def _acknowledge(config, item: AttentionItem, *, at: datetime = NOW) -> None:
    """What the dashboard's dismiss control does: one record for the published occurrence."""

    _store(config).dismiss(
        AttentionDismissalRecord(
            itemId=item.id, kind=item.kind, dismissedAt=(at + timedelta(seconds=1)).isoformat()
        )
    )


def _held_store(root: Path, config, rows: list[dict[str, object]]) -> Path:
    """One product-owned held-message store, named by the queue's own receipt."""

    folder = root / "worktrees" / "leaf"
    store = folder / ".ar-product" / "held-messages" / f"{HELD_AGENT}.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps(
            {
                "schema": "ar-held-messages/v1",
                "agentId": HELD_AGENT,
                "messages": rows,
                "updatedAt": "2026-10-22T08:55:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    receipt = (
        config.coordination_root
        / "tasks"
        / "repo"
        / "master"
        / "notes"
        / "reports"
        / "paseo-native-executions"
        / "receipt.json"
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "ar-role-execution/v1",
                "workspace": {"path": str(folder)},
                "execution": {"agentId": HELD_AGENT},
            }
        ),
        encoding="utf-8",
    )
    queue = config.coordination_root / "temp" / "held-message-queue.json"
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text(
        json.dumps(
            {
                "schema": "ar-held-message-queue/v1",
                "entries": {str(receipt): NOW.isoformat()},
            }
        ),
        encoding="utf-8",
    )
    return store


def _held_row(message_id: str) -> dict[str, object]:
    return {
        "id": message_id,
        "senderAgentId": SENDER,
        "text": "secret body",
        "takenAt": "2026-10-22T08:55:00+00:00",
        "reason": "recipient at a permission prompt",
    }


def _notice(
    config, occurrence: str, kind: TerminalKind = "task-landed", *, key: str = TASK_KEY
) -> None:
    reference = TaskDocumentRef(repository="repo", path=key.split("/", 1)[1])
    begin_terminal_notice(
        config.coordination_root,
        PendingTerminalNotice(
            occurrenceId=occurrence,
            kind=kind,
            taskKey=key,
            taskReference=reference,
            taskTitle="Leaf",
            detail="landed abc123def456 via merge" if kind == "task-landed" else "abandoned",
            at="2026-10-22T08:00:00+00:00",
        ),
    )
    confirm_terminal_notice(config.coordination_root, occurrence)


# --- rule 7: one acknowledgement per occurrence, and it persists --------------


def test_a_permission_entry_is_hidden_by_its_acknowledgement_until_that_prompt_is_answered(
    tmp_path,
) -> None:
    config, home = _world(tmp_path)
    _seat(home, AGENT)
    _publication(
        home,
        {
            AGENT: {
                "state": "permission",
                "eventAt": "2026-08-22T08:50:00+00:00",
                "permissionPrompts": ["prompt-a"],
            }
        },
    )
    state = _state()
    first = _pass(state, config)
    (item,) = [row for row in _queue(first) if row.kind == "permission-wait"]
    assert item.taskKey == TASK_KEY

    _acknowledge(config, item)
    hidden = _pass(state, config, now=NOW + timedelta(seconds=15))
    assert _ids(hidden, now=NOW + timedelta(seconds=15)) == []
    # Dismissing changes nothing at the cause: the prompt is still unanswered and the seat is
    # still waiting on it (v2 rule 7).
    assert [wait.prompt_id for wait in hidden.native_attention.permissions] == ["prompt-a"]
    # A service restart is a new input state reading the same acknowledgement store.
    restarted = _state()
    assert _ids(_pass(restarted, config, now=NOW + timedelta(seconds=30))) == []
    assert item.id in _store(config).current()

    # Answering the prompt ends exactly that occurrence: the record is reclaimed.
    _publication(
        home,
        {
            AGENT: {
                "state": "permission",
                "eventAt": NOW.isoformat(),
                "permissionPrompts": ["prompt-b"],
            }
        },
    )
    renewed = _pass(restarted, config, now=NOW + timedelta(seconds=45))
    assert [row.id for row in _queue(renewed)] == [f"permission-wait:{AGENT}:prompt-b"]
    assert _store(config).current() == {}


def test_a_held_entry_is_hidden_by_its_acknowledgement_until_that_message_leaves(
    tmp_path,
) -> None:
    config, home = _world(tmp_path)
    _seat(home, HELD_AGENT)
    _publication(home, {HELD_AGENT: {"state": "idle"}}, complete=True)
    store = _held_store(tmp_path, config, [_held_row("message-1")])
    state = _state()
    (item,) = _queue(_pass(state, config))
    assert item.kind == "held-message"

    _acknowledge(config, item)
    hidden = _pass(state, config, now=NOW + timedelta(seconds=15))
    assert _ids(hidden, now=NOW + timedelta(seconds=15)) == []
    # The message is still held: the acknowledgement hid a row, it did not deliver anything.
    assert [message.message_id for message in hidden.native_attention.held] == ["message-1"]
    restarted = _state()
    assert _ids(_pass(restarted, config, now=NOW + timedelta(seconds=30))) == []

    # The message leaves its own store: the occurrence is over, and a later one is visible.
    store.write_text(
        json.dumps(
            {
                "schema": "ar-held-messages/v1",
                "agentId": HELD_AGENT,
                "messages": [],
                "updatedAt": NOW.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    assert _ids(_pass(restarted, config, now=NOW + timedelta(seconds=45))) == []
    assert _store(config).current() == {}

    _held_store(tmp_path, config, [_held_row("message-2")])
    assert [
        row.heldMessageId
        for row in _queue(_pass(restarted, config, now=NOW + timedelta(seconds=60)))
    ] == ["message-2"]


def test_a_stall_entry_is_hidden_by_its_acknowledgement_and_a_resumed_turn_renews_it(
    tmp_path,
) -> None:
    config, home = _world(tmp_path)
    _seat(home, AGENT)
    _publication(
        home, {AGENT: {"state": "idle", "eventAt": (NOW - timedelta(hours=2)).isoformat()}}
    )
    state = _state()
    assert _ids(_pass(state, config)) == []

    # The settled interval passes on the reader's own watch.
    stalled = NOW + timedelta(seconds=901)
    result = _pass(state, config, now=stalled)
    (item,) = _queue(result, now=stalled)
    # Its identity carries the published turn end, so a reader that restarts names it again.
    assert item.id == f"task-stalled:{TASK_KEY}:{(NOW - timedelta(hours=2)).isoformat()}"

    _acknowledge(config, item, at=stalled)
    hidden = stalled + timedelta(seconds=15)
    dismissed_pass = _pass(state, config, now=hidden)
    assert _ids(dismissed_pass, now=hidden) == []
    # The task's stage and its seats stay as they are: the developer read the row, nothing else.
    assert [task.stage for task in _tasks(dismissed_pass)] == ["started"]
    assert (
        json.loads(
            (config.coordination_root / "tasks" / "repo" / "master" / "leaf.json").read_text()
        )
        == DOC
    )
    restarted = _state()
    assert (
        _ids(
            _pass(restarted, config, now=hidden + timedelta(seconds=5)),
            now=hidden + timedelta(seconds=5),
        )
        == []
    )
    assert item.id in _store(config).current()

    # A bound turn ends the interval: the acknowledgement leaves and the new interval is new.
    turn = hidden + timedelta(minutes=1)
    _publication(home, {AGENT: {"state": "in-turn", "eventAt": turn.isoformat()}})
    resumed = _pass(restarted, config, now=turn)
    assert _queue(resumed, now=turn) == []
    assert _store(config).current() == {}

    _publication(home, {AGENT: {"state": "idle", "eventAt": turn.isoformat()}})
    # The clock restarts with the new interval, so the settled interval is counted from it.
    _pass(restarted, config, now=turn + timedelta(seconds=10))
    later = turn + timedelta(seconds=910)
    renewed = _pass(restarted, config, now=later)
    assert [row.id for row in _queue(renewed, now=later)] == [
        f"task-stalled:{TASK_KEY}:{turn.isoformat()}"
    ]


def test_a_terminal_notice_is_hidden_by_its_acknowledgement_until_the_store_drops_it(
    tmp_path,
) -> None:
    config, home = _world(tmp_path)
    _publication(home, {})
    _notice(config, "task-landed:repo/master/leaf.json:abc")
    state = _state()
    (item,) = _queue(_pass(state, config))
    assert item.kind == "task-landed"

    _acknowledge(config, item)
    assert _ids(_pass(state, config, now=NOW + timedelta(seconds=15))) == []
    restarted = _state()
    assert _ids(_pass(restarted, config, now=NOW + timedelta(seconds=30))) == []

    # The bounded store drops the occurrence; a distinct landing is a distinct occurrence.
    discard_terminal_notice(config.coordination_root, item.occurrenceId or "")
    assert _ids(_pass(restarted, config, now=NOW + timedelta(seconds=45))) == []
    assert _store(config).current() == {}
    _notice(config, "task-landed:repo/master/leaf.json:def")
    assert [
        row.kind for row in _queue(_pass(restarted, config, now=NOW + timedelta(seconds=60)))
    ] == ["task-landed"]


def test_an_abandonment_is_hidden_by_its_acknowledgement_and_never_changes_the_task(
    tmp_path,
) -> None:
    config, home = _world(tmp_path)
    _publication(home, {})
    _notice(config, "task-abandoned:repo/master/leaf.json", kind="task-abandoned")
    state = _state()
    before = _pass(state, config)
    (item,) = _queue(before)
    assert item.kind == "task-abandoned"

    _acknowledge(config, item)
    after = _pass(state, config, now=NOW + timedelta(seconds=15))
    assert _ids(after, now=NOW + timedelta(seconds=15)) == []
    # Dismissing changes nothing at the cause: the task entry and the notice store stand.
    assert [task.stage for task in _tasks(after)] == ["started"]
    assert after.native_attention.notices == before.native_attention.notices
    # The task document on disk is still the one that was written.
    assert (
        json.loads(
            (config.coordination_root / "tasks" / "repo" / "master" / "leaf.json").read_text()
        )
        == DOC
    )

    # A task is abandoned once, so the next occurrence of the kind is another task's
    # abandonment: a distinct occurrence, published even though the first was acknowledged.
    second_key = "repo/master/leaf-two.json"
    second_doc = dict(DOC, id="L2", slug="leaf-two", title="Leaf two")
    (config.coordination_root / "tasks" / "repo" / "master" / "leaf-two.json").write_text(
        json.dumps(second_doc), encoding="utf-8"
    )
    _notice(
        config,
        f"task-abandoned:{second_key}",
        kind="task-abandoned",
        key=second_key,
    )
    again = _pass(state, config, now=NOW + timedelta(seconds=30))
    assert [row.taskKey for row in _queue(again, now=NOW + timedelta(seconds=30))] == [second_key]


def test_the_backend_accepts_every_published_kind_and_refuses_only_an_unpublished_item(
    tmp_path,
) -> None:
    config, home = _world(tmp_path)
    _seat(home, AGENT)
    _publication(
        home,
        {
            AGENT: {
                "state": "permission",
                "eventAt": NOW.isoformat(),
                "permissionPrompts": ["prompt-a"],
            }
        },
    )
    _notice(config, "task-landed:repo/master/leaf.json:abc")
    result = _pass(_state(), config)
    published = _queue(result)
    assert {row.kind for row in published} == {
        "permission-wait",
        "task-landed",
        "task-stalled",
    } or {row.kind for row in published} >= {"permission-wait", "task-landed"}
    projection = WorkspaceProjection(
        generatedAt=NOW.isoformat(), analytics=Analytics(attentionQueue=published)
    )

    for row in published:
        outcome = evaluate_action(
            projection,
            "dismiss",
            None,
            ActionEvaluationContext(
                actor="developer", now=NOW.isoformat(), item_id=row.id, kind=row.kind
            ),
        )
        assert outcome.status_code == 202, (row.kind, outcome.body)
        assert outcome.dismissal is not None and outcome.dismissal.kind == row.kind

    unknown = evaluate_action(
        projection,
        "dismiss",
        None,
        ActionEvaluationContext(
            actor="developer", now=NOW.isoformat(), item_id="task-stalled:gone", kind="task-stalled"
        ),
    )
    assert unknown.status_code == 404 and unknown.body["status"] == "unknown-item"

    mismatched = evaluate_action(
        projection,
        "dismiss",
        None,
        ActionEvaluationContext(
            actor="developer",
            now=NOW.isoformat(),
            item_id=published[0].id,
            kind="task-landed",
        ),
    )
    assert mismatched.status_code == 400 and mismatched.body["status"] == "item-kind-mismatch"


# --- rule 7: the acknowledgement store's named limits -------------------------


def test_the_store_stays_within_its_count_and_byte_limits_and_evicts_oldest_first(
    tmp_path,
) -> None:
    store = AttentionDismissalStore(tmp_path)
    store.log_path().parent.mkdir(parents=True, exist_ok=True)
    rows = [
        AttentionDismissalRecord(
            itemId=f"permission-wait:agent-{index}:prompt-{index}",
            kind="permission-wait",
            dismissedAt=(NOW + timedelta(seconds=index)).isoformat(),
        ).model_dump_json(by_alias=True, exclude_none=True)
        for index in range(MAX_DISMISSALS + 4)
    ]
    store.log_path().write_text("\n".join(rows) + "\n", encoding="utf-8")

    kept = store.reconcile([])
    assert len(kept) == MAX_DISMISSALS
    # The four oldest left; the newest is still there.
    assert "permission-wait:agent-0:prompt-0" not in kept
    assert f"permission-wait:agent-{MAX_DISMISSALS + 3}:prompt-{MAX_DISMISSALS + 3}" in kept
    assert len(store.log_path().read_text().splitlines()) == MAX_DISMISSALS

    # The byte bound is measured over the document that is actually written.
    store.log_path().write_text("", encoding="utf-8")
    long_ids = [f"task-stalled:{TASK_KEY}:{'x' * 400}-{index}" for index in range(MAX_DISMISSALS)]
    store.log_path().write_text(
        "\n".join(
            AttentionDismissalRecord(
                itemId=item_id,
                kind="task-stalled",
                dismissedAt=(NOW + timedelta(seconds=index)).isoformat(),
            ).model_dump_json(by_alias=True, exclude_none=True)
            for index, item_id in enumerate(long_ids)
        )
        + "\n",
        encoding="utf-8",
    )
    bounded = store.reconcile([])
    written = store.log_path().read_text(encoding="utf-8")
    assert len(written.encode("utf-8")) <= MAX_DISMISSAL_BYTES
    assert set(bounded) < set(long_ids)


def test_a_failed_write_leaves_the_entry_visible(tmp_path, monkeypatch) -> None:
    config, home = _world(tmp_path)
    _seat(home, AGENT)
    _publication(
        home,
        {
            AGENT: {
                "state": "permission",
                "eventAt": NOW.isoformat(),
                "permissionPrompts": ["prompt-a"],
            }
        },
    )
    state = _state()
    (item,) = _queue(_pass(state, config))

    def unwritable(_self, _record):
        raise PermissionError("read-only acknowledgement store")

    monkeypatch.setattr(AttentionDismissalStore, "dismiss", unwritable)
    with __import__("pytest").raises(PermissionError):
        _acknowledge(config, item)
    # Nothing was acknowledged, so the occurrence is still published.
    assert [row.id for row in _queue(_pass(state, config, now=NOW + timedelta(seconds=15)))] == [
        item.id
    ]


# --- rule 2a: the newest entry on top ----------------------------------------


def test_the_queue_lists_the_newest_entry_first_and_an_age_less_entry_counts_as_zero(
    tmp_path,
) -> None:
    config, home = _world(tmp_path)
    other = "44444444-4444-4444-8444-444444444444"
    _seat(home, AGENT)
    _seat(home, other)
    # One prompt the publication dates and one it does not: an entry with no age counts as zero
    # rather than sinking below an entry that can name when it began (v2 rule 2a).
    _publication(
        home,
        {
            AGENT: {"state": "permission", "permissionPrompts": ["prompt-b", "prompt-a"]},
            other: {
                "state": "permission",
                "eventAt": (NOW - timedelta(seconds=30)).isoformat(),
                "permissionPrompts": ["prompt-x"],
            },
        },
    )
    _notice(config, "task-landed:repo/master/leaf.json:abc")
    state = _state()
    later = NOW + timedelta(seconds=600)
    result = _pass(state, config, now=later)
    queue = _queue(result, now=later)
    assert [row.id for row in queue] == [
        f"permission-wait:{AGENT}:prompt-a",
        f"permission-wait:{AGENT}:prompt-b",
        # A notice reports an occurrence, not a wait, so it carries no age and ranks with the
        # newest entries rather than below a row that can name when it began.
        "task-landed:repo/master/leaf.json:abc",
        f"permission-wait:{other}:prompt-x",
    ]
    assert queue[0].waitSeconds is None and queue[2].waitSeconds == 0.0
    ages = [row.waitSeconds or 0.0 for row in queue]
    assert ages == sorted(ages)
    # An age-only tick re-orders nothing: the same facts one minute later keep the order.
    tick = _pass(state, config, now=later + timedelta(seconds=60))
    assert [row.id for row in _queue(tick, now=later + timedelta(seconds=60))] == [
        row.id for row in queue
    ]
