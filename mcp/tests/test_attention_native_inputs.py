"""The native attention facts as one projection pass reads them (MIK-R200 rules 2-6, 9).

These cases drive the real input state over real files: the plugin's host publication, a product
held-message receipt and store, a contract-scoped activation record, and the terminal notice
store. They assert the two retention rules that make the queue honest -- an unreadable observation
resolves nothing, and a complete one reconciles -- rather than the builders' formatting, which
``test_attention_native_kinds.py`` covers.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from agents_remember.controlplane.attention_dismissals import AttentionDismissalRecord
from agents_remember.controlplane.attention_notices import (
    PendingTerminalNotice,
    begin_terminal_notice,
    confirm_terminal_notice,
    notice_store_path,
    read_notice_store,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.host_activity import (
    HOST_ACTIVITY_FILE,
    HostActivitySnapshot,
    PublishedAgentActivity,
    TaskAgentActivity,
)
from agents_remember.observer.projection import TaskEntry
from agents_remember.observer.projection_task_documents import TaskDocNode
from agents_remember.observer.reducer import AnalyticalInputs, build_attention_queue
from agents_remember.observer.task_entries import (
    DeclaredTaskSource,
    TaskDocumentSource,
    build_task_entries,
)
from agents_remember.serving.paseo.paseo_plugin_files import publish_role_agent
from agents_remember.serving.projections.native_attention import (
    CONTROLPLANE_DOMAIN,
    NativeAttentionInput,
)
from agents_remember.serving.projections.projection_inputs import (
    ProjectionDomain,
    ProjectionInputState,
    ProjectionReaders,
    ProjectionRefresh,
    RefreshPass,
)
from test_paseo_role_agents import runtime_config


def _entries(sources, host=None):
    """The canonical task list for a reader call (L190-R1-F04: one relation, not declared text)."""

    return build_task_entries(list(sources), [], host or _known_host())


NOW = datetime(2026, 10, 22, 9, 0, tzinfo=UTC)
BOOT = "boot-a"
TASK_KEY = "repo/master/leaf.json"


def _publication(
    home: Path, agents: dict[str, object], *, complete: bool = True, boot: str = BOOT
) -> None:
    directory = home / "agents-remember"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / HOST_ACTIVITY_FILE).write_text(
        json.dumps({"bootId": boot, "complete": complete, "agents": agents, "ignoredEvents": 0}),
        encoding="utf-8",
    )


def _state(boot: str = BOOT) -> ProjectionInputState:
    return ProjectionInputState(
        host_activity_reconcile=lambda _config: {"publication": {"bootId": boot}},
        host_supervisor_armed=lambda: True,
    )


def _read(state: ProjectionInputState, config, *, refresh: ProjectionRefresh | None = None):
    """One pass that includes the host publication, as its own invalidation does."""

    return state.read(
        config,
        ProjectionReaders(repo_surfaces=lambda _config, _now: ([], [])),
        observer_root=config.coordination_root / "logs" / "observer",
        pass_=RefreshPass(
            now=NOW,
            refresh=refresh
            or ProjectionRefresh.change(
                ProjectionRefresh.full().domains | {ProjectionDomain.HOST_ACTIVITY}
            ),
        ),
    )


def _task(status: str = "inProgress") -> TaskDocumentSource:
    path = Path("/coord") / "tasks" / "repo" / "master" / "leaf.json"
    return TaskDocumentSource(
        path=path,
        summary=TaskDocNode(
            id="L1",
            repository="repo",
            title="Leaf",
            kind="subTask",
            status=status,
            docPath="master/leaf.json",
        ),
    )


def _entry(
    activity: Literal["works", "paused", "unknown"] | None = "paused",
    event_at: str | None = None,
) -> TaskEntry:
    return TaskEntry(
        key=TASK_KEY,
        reference=TaskDocumentRef(repository="repo", path="master/leaf.json"),
        id="L1",
        title="Leaf",
        stage="started",
        activity=activity,
        eventAt=event_at,
    )


def _case_test_permission_prompts_arrive_from_the_publication_and_survive_an_unreadable_one(
    tmp_path,
) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish_role_agent(
        home,
        "11111111-1111-4111-8111-111111111111",
        {"role": "worker", "task": TASK_KEY, "reportPath": "/r"},
    )
    _publication(
        home,
        {
            "11111111-1111-4111-8111-111111111111": {
                "state": "permission",
                "eventAt": "2026-10-22T08:50:00+00:00",
                "permissionPrompts": ["prompt-a", "prompt-b"],
            }
        },
    )
    state = _state()
    facts = _read(state, config).native_attention
    assert facts.permissions_complete
    assert [wait.prompt_id for wait in facts.permissions] == ["prompt-a", "prompt-b"]
    queue = build_attention_queue([], AnalyticalInputs(native_attention=facts), [_entry()], NOW)
    assert [item.id for item in queue] == [
        "permission-wait:11111111-1111-4111-8111-111111111111:prompt-a",
        "permission-wait:11111111-1111-4111-8111-111111111111:prompt-b",
    ]

    # An incomplete publication is not a resolution: the last known prompts stay visible.
    _publication(home, {}, complete=False)
    facts = _read(state, config).native_attention
    assert facts.permissions_complete is False
    assert [wait.prompt_id for wait in facts.permissions] == ["prompt-a", "prompt-b"]

    # A complete publication that answers one prompt ends exactly that occurrence.
    _publication(
        home,
        {
            "11111111-1111-4111-8111-111111111111": {
                "state": "permission",
                "eventAt": "2026-10-22T08:50:00+00:00",
                "permissionPrompts": ["prompt-b"],
            }
        },
    )
    facts = _read(state, config).native_attention
    assert facts.permissions_complete
    assert [wait.prompt_id for wait in facts.permissions] == ["prompt-b"]


def _held_store(
    tmp_path: Path, config, rows: list[dict[str, object]], *, broken: bool = False
) -> Path:
    agent_id = "22222222-2222-4222-8222-222222222222"
    folder = tmp_path / "worktrees" / "leaf"
    store = folder / ".ar-product" / "held-messages" / f"{agent_id}.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    if broken:
        store.write_text("{broken", encoding="utf-8")
    else:
        store.write_text(
            json.dumps(
                {
                    "schema": "ar-held-messages/v1",
                    "agentId": agent_id,
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
                "execution": {"agentId": agent_id},
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


def _case_test_held_messages_come_from_the_store_row_and_clear_only_on_delivery(tmp_path) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish_role_agent(
        home,
        "22222222-2222-4222-8222-222222222222",
        {"role": "worker", "task": TASK_KEY, "reportPath": "/r"},
    )
    _publication(home, {"22222222-2222-4222-8222-222222222222": {"state": "idle"}})
    store = _held_store(
        tmp_path,
        config,
        [
            {
                "id": "message-1",
                "senderAgentId": "33333333-3333-4333-8333-333333333333",
                "text": "secret body",
                "takenAt": "2026-10-22T08:55:00+00:00",
                "reason": "recipient at a permission prompt",
            }
        ],
    )
    state = _state()
    facts = _read(state, config).native_attention
    assert facts.held_complete
    assert [(row.message_id, row.reason) for row in facts.held] == [
        ("message-1", "recipient at a permission prompt")
    ]
    queue = build_attention_queue([], AnalyticalInputs(native_attention=facts), [_entry()], NOW)
    assert [item.kind for item in queue] == ["held-message"]
    assert "secret body" not in json.dumps([item.model_dump() for item in queue])

    # An unreadable store resolves nothing: the held occurrence stays visible.
    store.write_text("{broken", encoding="utf-8")
    facts = _read(state, config).native_attention
    assert facts.held_complete is False
    assert [row.message_id for row in facts.held] == ["message-1"]

    # Native delivery is the row leaving the store: then the item clears.
    store.write_text(
        json.dumps(
            {
                "schema": "ar-held-messages/v1",
                "agentId": "22222222-2222-4222-8222-222222222222",
                "messages": [],
                "updatedAt": "2026-10-22T09:00:00+00:00",
            }
        ),
        encoding="utf-8",
    )
    facts = _read(state, config).native_attention
    assert facts.held == ()


def _activation(config, *, paused_at: str | None, state: str = "vacant") -> None:
    path = (
        config.coordination_root / "controlplane" / "atomic-series-activation" / f"{'a' * 64}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "schemaVersion": "2.0",
        "contractFingerprint": "a" * 64,
        "selectedMaster": {"repository": "repo", "path": "master/task.json"},
        "contractPath": "/coord/tasks/repo/master/series-contract.md",
        "state": state,
        "revision": 2,
        "selectedAt": "2026-10-22T08:00:00+00:00",
    }
    if paused_at is not None:
        record["pausedAt"] = paused_at
    path.write_text(json.dumps(record), encoding="utf-8")


def _case_test_only_an_explicit_pause_suppresses_a_stall(tmp_path) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {}, complete=True)
    state = _state()

    facts = _read(state, config).native_attention
    assert facts.pauses_complete
    assert facts.paused_task_keys == frozenset()

    _activation(config, paused_at="2026-10-22T08:30:00+00:00")
    facts = _read(state, config).native_attention
    assert facts.paused_task_keys == frozenset({"repo/master/task.json"})

    # A vacant record without the pause is ordinary vacancy: it suppresses nothing.
    _activation(config, paused_at=None)
    facts = _read(state, config).native_attention
    assert facts.paused_task_keys == frozenset()


def _case_test_a_held_store_the_queue_has_expired_is_still_read(tmp_path) -> None:
    """L190-R1-F02: the delivery queue is a discovery hint, never the held census.

    The queue's entries expire and are evicted; a message that is still in the recipient's store
    has neither been delivered nor withdrawn, so it stays visible. The frozen candidate aged the
    row out with the queue entry and reported an empty, complete observation.
    """

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    publish_role_agent(
        home,
        "22222222-2222-4222-8222-222222222222",
        {"role": "worker", "task": TASK_KEY, "reportPath": "/r"},
    )
    _publication(home, {"22222222-2222-4222-8222-222222222222": {"state": "idle"}})
    _held_store(
        tmp_path,
        config,
        [
            {
                "id": "held-one",
                "senderAgentId": "33333333-3333-4333-8333-333333333333",
                "takenAt": "2026-10-22T08:55:00+00:00",
                "reason": "recipient-in-turn",
            }
        ],
    )
    state = _state()
    facts = _read(state, config).native_attention
    assert facts.held_complete
    assert [row.message_id for row in facts.held] == ["held-one"]

    # The queue entry is two days old: expired for delivery, irrelevant to what is held.
    queue_file = config.coordination_root / "temp" / "held-message-queue.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    receipt = next((config.coordination_root / "tasks").rglob("receipt.json"))
    queue_file.write_text(
        json.dumps(
            {
                "schema": "ar-held-message-queue/v1",
                "entries": {str(receipt): "2026-10-20T08:00:00+00:00"},
            }
        ),
        encoding="utf-8",
    )
    facts = _read(_state(), config).native_attention
    assert facts.held_complete
    assert [row.message_id for row in facts.held] == ["held-one"]

    # A torn queue is an incomplete observation, not an empty one: the row stays.
    queue_file.write_text("{broken", encoding="utf-8")
    facts = _read(_state(), config).native_attention
    assert [row.message_id for row in facts.held] == ["held-one"]
    assert facts.held_complete is False


def _case_test_the_stall_clock_starts_at_the_first_complete_eligible_observation(tmp_path) -> None:
    """A startup listing with no idle stamp is not evidence of an older idle interval."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    reader = _state()._native_attention_reader
    source = _task()

    facts = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)
    assert facts.stall_clocks == {TASK_KEY: NOW.isoformat()}

    # The same eligibility on a later pass keeps the start: it is one continuous interval.
    later = reader.read(
        config,
        host=_known_host(),
        task_entries=_entries([source]),
        now=NOW + timedelta(minutes=10),
    )
    assert later.stall_clocks == {TASK_KEY: NOW.isoformat()}

    # L190-R1-F04: a task-event stamp never dates an interval nobody watched.
    stamped = _state()._native_attention_reader.read(
        config,
        host=_known_host(event_at=datetime(2026, 10, 22, 8, 40, tzinfo=UTC)),
        task_entries=_entries([source]),
        now=NOW,
    )
    assert stamped.stall_clocks == {TASK_KEY: NOW.isoformat()}


def _case_test_an_unknown_observation_breaks_the_stall_interval(tmp_path) -> None:
    """L190-R1-F04: after a gap in observation, the deadline starts again from re-entry."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    reader = _state()._native_attention_reader
    source = _task()

    first = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)
    assert first.stall_clocks == {TASK_KEY: NOW.isoformat()}

    gap = reader.read(
        config,
        host=HostActivitySnapshot(),
        task_entries=_entries([source]),
        now=NOW + timedelta(minutes=5),
    )
    assert gap.stall_clocks == {}

    # The old eventAt on the resume observation does not restore the lost interval.
    resumed = NOW + timedelta(minutes=10)
    again = reader.read(
        config,
        host=_known_host(event_at=NOW - timedelta(hours=4)),
        task_entries=_entries([source]),
        now=resumed,
    )
    assert again.stall_clocks == {TASK_KEY: resumed.isoformat()}


def _case_test_a_new_native_boot_breaks_the_stall_interval(tmp_path) -> None:
    """A clock that spans two boots spans an interval nobody observed."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    reader = _state()._native_attention_reader
    source = _task()

    first = reader.read(
        config, host=_known_host(boot_id="boot-a"), task_entries=_entries([source]), now=NOW
    )
    assert first.stall_clocks == {TASK_KEY: NOW.isoformat()}

    reboot = NOW + timedelta(minutes=10)
    second = reader.read(
        config,
        host=_known_host(boot_id="boot-b"),
        task_entries=_entries([source]),
        now=reboot,
    )
    assert second.stall_clocks == {TASK_KEY: reboot.isoformat()}


def _case_test_bound_work_breaks_the_stall_interval(tmp_path) -> None:
    """A turn or a subagent is work, so the silence either side of it is two intervals."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    reader = _state()._native_attention_reader
    source = _task()

    first = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)
    assert first.stall_clocks == {TASK_KEY: NOW.isoformat()}

    working = reader.read(
        config,
        host=_known_host(state="in-turn"),
        task_entries=_entries([source]),
        now=NOW + timedelta(minutes=5),
    )
    assert working.stall_clocks == {}

    resumed = NOW + timedelta(minutes=10)
    again = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=resumed)
    assert again.stall_clocks == {TASK_KEY: resumed.isoformat()}


def _known_host(
    event_at: datetime | None = None,
    *,
    boot_id: str = "boot-a",
    state: Literal["in-turn", "permission", "idle", "ended"] = "idle",
    prompts: tuple[str, ...] = (),
):
    return HostActivitySnapshot(
        known=True,
        agents=(
            TaskAgentActivity(
                TASK_KEY,
                PublishedAgentActivity(state=state, eventAt=event_at, permissionPrompts=prompts),
                agent_id="44444444-4444-4444-8444-444444444444",
                role="worker",
            ),
        ),
        boot_id=boot_id,
    )


def _case_test_an_unproven_intent_publishes_nothing_and_keeps_waiting(tmp_path) -> None:
    """L190-R1-F06: an intent with no durable native record of its own stays an intent.

    The projection asks the recovery resolver, which reads the landing/abandonment records
    themselves. A task document that does not exist in this coordination root proves nothing,
    so the pass publishes no notice and the pending row survives for a later pass -- the
    opposite of the frozen candidate, which published from a generic "status says abandoned".
    """

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    pending = PendingTerminalNotice(
        occurrenceId="task-abandoned:repo/master/leaf.json",
        kind="task-abandoned",
        taskKey=TASK_KEY,
        taskReference=TaskDocumentRef(repository="repo", path="master/leaf.json"),
        taskTitle="Leaf",
        detail="abandoned; the enclosure keeps its record",
        at="2026-10-22T08:58:00+00:00",
    )
    begin_terminal_notice(config.coordination_root, pending)
    store = read_notice_store(config.coordination_root)
    assert store is not None and store.notices == [] and len(store.pending) == 1

    reader = _state()
    for _ in range(2):
        facts = reader._native_attention_reader.read(
            config,
            host=_known_host(),
            task_entries=_entries([_task()]),
            now=NOW,
        )
        assert facts.notices == ()
        store = read_notice_store(config.coordination_root)
        assert store is not None and store.notices == [] and len(store.pending) == 1

    # A confirmation after the fact is the same occurrence, never a second one.
    confirm_terminal_notice(config.coordination_root, pending.occurrenceId)
    store = read_notice_store(config.coordination_root)
    assert store is not None and len(store.notices) == 1
    assert notice_store_path(config.coordination_root).is_file()


def _case_test_a_notice_acknowledgement_keeps_its_occurrence_once_and_not_the_next(
    tmp_path,
) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    for occurrence, at in (
        ("task-landed:repo/master/leaf.json:abc", "2026-10-22T08:00:00+00:00"),
        ("task-landed:repo/master/leaf.json:def", "2026-10-22T08:30:00+00:00"),
    ):
        begin_terminal_notice(
            config.coordination_root,
            PendingTerminalNotice(
                occurrenceId=occurrence,
                kind="task-landed",
                taskKey=TASK_KEY,
                taskReference=TaskDocumentRef(repository="repo", path="master/leaf.json"),
                taskTitle="Leaf",
                detail="landed",
                at=at,
            ),
        )
        confirm_terminal_notice(config.coordination_root, occurrence)
    facts = _read(_state(), config).native_attention
    assert [notice.occurrenceId for notice in facts.notices] == [
        "task-landed:repo/master/leaf.json:abc",
        "task-landed:repo/master/leaf.json:def",
    ]
    acknowledged = {"task-landed:repo/master/leaf.json:abc"}
    queue = [
        item
        for item in build_attention_queue(
            [],
            AnalyticalInputs(
                native_attention=facts,
                attention_dismissals={
                    occurrence: _dismissal(occurrence) for occurrence in acknowledged
                },
            ),
            [_entry()],
            NOW,
        )
        if item.kind == "task-landed"
    ]
    assert [item.id for item in queue] == ["task-landed:repo/master/leaf.json:def"]


def _dismissal(occurrence: str):
    return AttentionDismissalRecord(
        itemId=occurrence,
        kind="task-landed",
        dismissedAt="2026-10-22T08:45:00+00:00",
    )


# --- F03: prompt identity is never invented ----------------------------------


def _case_test_an_identity_free_permission_observation_retains_the_known_prompt() -> None:
    """L190-R1-F03: a listing without the identity proves neither the prompt nor its end."""

    reader = NativeAttentionInput()
    reader._read_permissions(_known_host(state="permission", prompts=("real-prompt",)))
    assert [wait.prompt_id for wait in reader._permissions] == ["real-prompt"]
    assert reader._permissions_complete

    reader._read_permissions(_known_host(state="permission", prompts=()))
    assert [wait.prompt_id for wait in reader._permissions] == ["real-prompt"]
    assert reader._permissions_complete is False

    # A complete identity observation naming the sibling ends exactly the answered prompt.
    reader._read_permissions(_known_host(state="permission", prompts=("prompt-b",)))
    assert [wait.prompt_id for wait in reader._permissions] == ["prompt-b"]
    assert reader._permissions_complete is True

    # Leaving the wait ends every prompt the agent was waiting on.
    reader._read_permissions(_known_host(state="idle"))
    assert reader._permissions == ()


# --- F11: retained inputs, no repeated parsing --------------------------------


class _ReadCounter:
    def __init__(self) -> None:
        self.paths: list[Path] = []

    def __call__(self, path: Path, *_args, **_kwargs):
        self.paths.append(path)
        return _ORIGINAL_READ_TEXT(path, *_args, **_kwargs)


_ORIGINAL_READ_TEXT = Path.read_text


def _count_reads(monkeypatch) -> _ReadCounter:
    counter = _ReadCounter()

    def counted(self, *args, **kwargs):
        return counter(Path(self), *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted)
    return counter


def _case_test_an_age_only_pass_parses_no_native_store(tmp_path, monkeypatch) -> None:
    """L190-R1-F11: a pass that changed nothing re-reads no native store."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    _activation(config, paused_at="2026-10-22T08:30:00+00:00")
    reader = _state()._native_attention_reader
    source = _task()
    reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)

    counter = _count_reads(monkeypatch)
    facts = reader.read(
        config,
        host=_known_host(),
        task_entries=_entries([source]),
        now=NOW + timedelta(seconds=30),
        refreshed=frozenset({"tasks"}),
    )
    assert counter.paths == []
    # Retained facts are still evaluated: the pause is still known and the clock still runs.
    assert facts.paused_task_keys == frozenset({"repo/master/task.json"})
    assert facts.stall_clocks == {TASK_KEY: NOW.isoformat()}


def _case_test_a_controlplane_refresh_reads_its_own_stores_and_not_the_held_queue(
    tmp_path, monkeypatch
) -> None:
    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    _activation(config, paused_at=None)
    reader = _state()._native_attention_reader
    source = _task()
    reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)

    counter = _count_reads(monkeypatch)
    reader.read(
        config,
        host=_known_host(),
        task_entries=_entries([source]),
        now=NOW,
        refreshed=frozenset({CONTROLPLANE_DOMAIN}),
    )
    assert counter.paths, "the invalidated domain must actually be read"
    held_paths = [path for path in counter.paths if _HELD_QUEUE_NAME in path.name]
    assert held_paths == []


_HELD_QUEUE_NAME = "held-message-queue.json"


def _case_test_the_parse_budget_covers_the_admitted_pause_population(tmp_path, monkeypatch) -> None:
    """L190-R1-F11: 256 unchanged records -- the admitted maximum -- parse once."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    directory = config.coordination_root / "controlplane" / "atomic-series-activation"
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(256):
        (directory / f"{index:064x}.json").write_text(
            json.dumps(
                {
                    "schemaVersion": "2.0",
                    "contractFingerprint": f"{index:064x}",
                    "selectedMaster": {"repository": "repo", "path": "master/task.json"},
                    "contractPath": "/coord/tasks/repo/master/series-contract.md",
                    "state": "vacant",
                    "revision": 2,
                    "selectedAt": "2026-10-22T08:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
    reader = _state()._native_attention_reader
    source = _task()

    counter = _count_reads(monkeypatch)
    first = reader.read(config, host=_known_host(), task_entries=_entries([source]), now=NOW)
    # An exactly full entry allowance finishes its EOF check on the next heartbeat; it never
    # pulls a lookahead entry past the bound. The admitted records still parse exactly once.
    if not first.pauses_complete:
        first = reader.read(
            config,
            host=_known_host(),
            task_entries=_entries([source]),
            now=NOW,
            refreshed=frozenset(),
        )
    assert first.pauses_complete
    parsed_first = [path for path in counter.paths if path.parent == directory]
    assert len(parsed_first) == 256

    counter.paths.clear()
    reader.read(
        config,
        host=_known_host(),
        task_entries=_entries([source]),
        now=NOW + timedelta(seconds=30),
        refreshed=frozenset({CONTROLPLANE_DOMAIN}),
    )
    assert [path for path in counter.paths if path.parent == directory] == []


def _seat_store(
    tmp_path: Path, config, agent_id: str, folder_name: str, rows: list[dict[str, object]]
) -> Path:
    """One recipient's store and its receipt, so two seats can hold messages at once."""

    folder = tmp_path / "worktrees" / folder_name
    store = folder / ".ar-product" / "held-messages" / f"{agent_id}.json"
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps(
            {
                "schema": "ar-held-messages/v1",
                "agentId": agent_id,
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
        / f"{folder_name}.json"
    )
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": "ar-role-execution/v1",
                "workspace": {"path": str(folder)},
                "execution": {"agentId": agent_id},
            }
        ),
        encoding="utf-8",
    )
    return receipt


def _case_test_a_delivered_row_does_not_return_because_another_store_is_torn(tmp_path) -> None:
    """L190-R1-F02: incompleteness keeps a store's own rows, never a merged global set.

    The reviewer's residual: with a global merge on an incomplete pass, a row whose store had
    been read *empty* -- a delivery or a withdrawal -- was resurrected because some other
    store was unreadable. Two seats held a message; one is delivered, the other's store is torn,
    and only the torn seat's row may remain.
    """

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    alice = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
    bob = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
    for seat, role in ((alice, "worker"), (bob, "reviewer")):
        publish_role_agent(home, seat, {"role": role, "task": TASK_KEY, "reportPath": "/r"})
    _publication(home, {alice: {"state": "idle"}, bob: {"state": "idle"}})
    alice_receipt = _seat_store(
        tmp_path,
        config,
        alice,
        "alice",
        [
            {
                "id": "alice-1",
                "senderAgentId": "33333333-3333-4333-8333-333333333333",
                "takenAt": "2026-10-22T08:55:00+00:00",
                "reason": "recipient-in-turn",
            }
        ],
    )
    bob_receipt = _seat_store(
        tmp_path,
        config,
        bob,
        "bob",
        [
            {
                "id": "bob-1",
                "senderAgentId": "33333333-3333-4333-8333-333333333333",
                "takenAt": "2026-10-22T08:55:30+00:00",
                "reason": "recipient-in-turn",
            }
        ],
    )
    queue_file = config.coordination_root / "temp" / "held-message-queue.json"
    queue_file.parent.mkdir(parents=True, exist_ok=True)
    queue_file.write_text(
        json.dumps(
            {
                "schema": "ar-held-message-queue/v1",
                "entries": {str(alice_receipt): NOW.isoformat(), str(bob_receipt): NOW.isoformat()},
            }
        ),
        encoding="utf-8",
    )

    state = _state()
    facts = _read(state, config).native_attention
    assert facts.held_complete
    assert sorted(row.message_id for row in facts.held) == ["alice-1", "bob-1"]

    # Alice's message is delivered (her store is readably empty) and Bob's store is torn.
    _seat_store(tmp_path, config, alice, "alice", [])
    torn = tmp_path / "worktrees" / "bob" / ".ar-product" / "held-messages" / f"{bob}.json"
    torn.write_text("{broken", encoding="utf-8")

    facts = _read(state, config).native_attention
    assert not facts.held_complete, "a torn store may not be called whole"
    assert [row.message_id for row in facts.held] == ["bob-1"], (
        "the delivered row must not be resurrected by another store's tornness"
    )


def _master_listing(child: TaskDocumentSource) -> TaskDocumentSource:
    """The sprint whose listing holds ``child``: the master row, and the leaf record alone.

    L190-R1-F04: the relation that binds a listed child to its master is the canonical one the
    task list serves. The leaf's own document names no master here at all -- only the row does --
    which is exactly the shape the reviewer measured (a master-list-only canonical child).
    """

    return TaskDocumentSource(
        path=Path("/coord") / "tasks" / "repo" / "master" / "task.json",
        summary=TaskDocNode(
            id="M1",
            repository="repo",
            title="Master",
            kind="master",
            status="inProgress",
            docPath="master/task.json",
        ),
        children=(
            DeclaredTaskSource(
                reference=TaskDocumentRef(repository="repo", path="master/leaf.json"),
                id="L1",
                title="Leaf",
                status="inProgress",
            ),
        ),
    )


def _case_test_a_paused_master_breaks_its_listed_child_stall_clock(tmp_path) -> None:
    """L190-R1-F04: a stop the developer made is not silence; the interval ends there.

    The child is bound to the paused master by the canonical listing, not by authored parent
    text: the clock must use that same relation, or a paused master's child keeps accruing.
    """

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    _activation(config, paused_at="2026-10-22T08:30:00+00:00")
    reader = _state()._native_attention_reader
    leaf = _task()  # names no master at all
    listed = _entries([_master_listing(leaf), leaf])

    facts = reader.read(config, host=_known_host(), task_entries=listed, now=NOW)
    assert facts.paused_task_keys == frozenset({"repo/master/task.json"})
    assert facts.stall_clocks == {}

    # The same record with no listing row is not under the paused master, so its clock runs.
    unlinked = reader.read(config, host=_known_host(), task_entries=_entries([_task()]), now=NOW)
    assert unlinked.stall_clocks == {TASK_KEY: NOW.isoformat()}


def _case_test_reacquisition_restarts_the_interval_instead_of_reusing_it(tmp_path) -> None:
    """L190-R1-F04: the pause broke the interval, so the reacquisition begins a new one."""

    home = tmp_path / "host"
    config = runtime_config(tmp_path, home)
    _publication(home, {})
    leaf = _task()
    listed = _entries([_master_listing(leaf), leaf])
    reader = _state()._native_attention_reader

    _activation(config, paused_at="2026-10-22T08:30:00+00:00")
    paused = reader.read(
        config,
        host=_known_host(),
        task_entries=listed,
        now=NOW,
        refreshed=frozenset({CONTROLPLANE_DOMAIN}),
    )
    assert paused.stall_clocks == {}

    # The developer resumed: the master is selected again, with no pause stamped on it.
    _activation(config, paused_at=None, state="active")
    resumed_at = NOW + timedelta(minutes=30)
    resumed = reader.read(
        config,
        host=_known_host(),
        task_entries=listed,
        now=resumed_at,
        refreshed=frozenset({CONTROLPLANE_DOMAIN}),
    )
    assert resumed.paused_task_keys == frozenset()
    # The leaf's interval restarts at the reacquisition, not at the pre-pause observation; the
    # master is itself a started task with no bound agent, so it holds its own fresh clock.
    assert resumed.stall_clocks[TASK_KEY] == resumed_at.isoformat()
    assert resumed.stall_clocks["repo/master/task.json"] == resumed_at.isoformat()


# Consolidated cases: one group test per requirement. Every assertion and every
# failure message survives; each case runs as a subtest in its own root.


def test_permission_waits_arrive_and_never_lose_their_identity(
    subtests, tmp_path, monkeypatch
) -> None:
    """F03: permission waits come from the publication, keep the last known identity when an observation cannot name it, and survive an unreadable publication."""

    for message, run in (
        (
            "permission prompts arrive from the publication and survive an unreadable one",
            lambda **fixtures: (
                _case_test_permission_prompts_arrive_from_the_publication_and_survive_an_unreadable_one(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "an identity free permission observation retains the known prompt",
            lambda **fixtures: (
                _case_test_an_identity_free_permission_observation_retains_the_known_prompt(
                    **_take(
                        fixtures,
                    )
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_held_rows_come_from_the_store_and_leave_with_it(subtests, tmp_path, monkeypatch) -> None:
    """F02: a held row comes from the recipient's store, clears on delivery, and is read even when the queue no longer names it."""

    for message, run in (
        (
            "held messages come from the store row and clear only on delivery",
            lambda **fixtures: (
                _case_test_held_messages_come_from_the_store_row_and_clear_only_on_delivery(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a held store the queue has expired is still read",
            lambda **fixtures: _case_test_a_held_store_the_queue_has_expired_is_still_read(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a delivered row does not return because another store is torn",
            lambda **fixtures: (
                _case_test_a_delivered_row_does_not_return_because_another_store_is_torn(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_the_stall_interval_is_only_what_the_reader_watched(
    subtests, tmp_path, monkeypatch
) -> None:
    """F04: one continuous eligible interval, broken by an unknown observation, a new boot, bound work or an explicit pause anywhere on the canonical lineage."""

    for message, run in (
        (
            "only an explicit pause suppresses a stall",
            lambda **fixtures: _case_test_only_an_explicit_pause_suppresses_a_stall(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "the stall clock starts at the first complete eligible observation",
            lambda **fixtures: (
                _case_test_the_stall_clock_starts_at_the_first_complete_eligible_observation(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "an unknown observation breaks the stall interval",
            lambda **fixtures: _case_test_an_unknown_observation_breaks_the_stall_interval(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a new native boot breaks the stall interval",
            lambda **fixtures: _case_test_a_new_native_boot_breaks_the_stall_interval(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "bound work breaks the stall interval",
            lambda **fixtures: _case_test_bound_work_breaks_the_stall_interval(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a paused master breaks its listed child stall clock",
            lambda **fixtures: _case_test_a_paused_master_breaks_its_listed_child_stall_clock(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "reacquisition restarts the interval instead of reusing it",
            lambda **fixtures: _case_test_reacquisition_restarts_the_interval_instead_of_reusing_it(
                **_take(fixtures, "tmp_path")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_a_pass_reads_only_the_stores_its_domains_name(subtests, tmp_path, monkeypatch) -> None:
    """F11: an age-only pass parses nothing, a control-plane refresh reads only its own stores, and the enumeration is bounded."""

    for message, run in (
        (
            "an age only pass parses no native store",
            lambda **fixtures: _case_test_an_age_only_pass_parses_no_native_store(
                **_take(fixtures, "tmp_path", "monkeypatch")
            ),
        ),
        (
            "a controlplane refresh reads its own stores and not the held queue",
            lambda **fixtures: (
                _case_test_a_controlplane_refresh_reads_its_own_stores_and_not_the_held_queue(
                    **_take(fixtures, "tmp_path", "monkeypatch")
                )
            ),
        ),
        (
            "the parse budget covers the admitted pause population",
            lambda **fixtures: _case_test_the_parse_budget_covers_the_admitted_pause_population(
                **_take(fixtures, "tmp_path", "monkeypatch")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_notices_publish_only_what_the_native_record_proves(
    subtests, tmp_path, monkeypatch
) -> None:
    """F06: an unproven intent announces nothing, and an acknowledgement covers exactly one occurrence."""

    for message, run in (
        (
            "an unproven intent publishes nothing and keeps waiting",
            lambda **fixtures: _case_test_an_unproven_intent_publishes_nothing_and_keeps_waiting(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a notice acknowledgement keeps its occurrence once and not the next",
            lambda **fixtures: (
                _case_test_a_notice_acknowledgement_keeps_its_occurrence_once_and_not_the_next(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def _slug(message: str) -> str:
    """A directory name for one case, so no two subtests share a coordination root."""

    return "".join(char if char.isalnum() else "-" for char in message)[:48]


def _take(fixtures: dict, *names: str) -> dict:
    """The fixtures one case declares, so a group can supply its whole set."""

    return {name: fixtures[name] for name in names}
