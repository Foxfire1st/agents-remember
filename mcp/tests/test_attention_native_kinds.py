"""The five native attention kinds carry their own subject, class and clearing rule.

These cases pin what each builder is allowed to say: the exact row ids (so a retry cannot drift
them), the ``itemClass``/``dismissible`` pair the client reads, the canonical task a row opens or
its honest absence, and the stall deadline against one fixed pass clock. Nothing here re-checks the
surviving provider/setup/start kinds, which ``test_observer_projection_attention`` owns.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal

import pytest
from agents_remember.controlplane.attention_dismissals import AttentionDismissalRecord
from agents_remember.controlplane.attention_notices import TerminalNotice
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.native_attention import (
    STALL_SECONDS,
    HeldMessage,
    NativeAttentionFacts,
    PermissionWait,
)
from agents_remember.observer.projection import ProviderNode, TaskEntry
from agents_remember.observer.reducer import AnalyticalInputs, build_attention_queue

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
LEAF_KEY = "repo/master/leaf.json"
MISSING_KEY = "repo/master/missing.json"

Stage = Literal["planned", "started", "aborted", "completed"]
Activity = Literal["works", "paused", "unknown"] | None


def _ref(key: str = LEAF_KEY) -> TaskDocumentRef:
    return TaskDocumentRef(repository=key.split("/", 1)[0], path=key.split("/", 1)[1])


def _task(
    key: str = LEAF_KEY,
    *,
    stage: Stage = "started",
    activity: Activity = "paused",
    event_at: str | None = None,
    parent: str | None = None,
) -> TaskEntry:
    return TaskEntry(
        key=key,
        reference=_ref(key),
        id=key.rpartition("/")[2].removesuffix(".json"),
        title="Leaf",
        stage=stage,
        activity=activity,
        eventAt=event_at,
        parent=parent,
    )


def _ago(seconds: float) -> str:
    return (NOW - timedelta(seconds=seconds)).isoformat()


def _queue(facts: NativeAttentionFacts, tasks: list[TaskEntry] | None = None):
    dismissed: dict[str, AttentionDismissalRecord] = {}
    return build_attention_queue(
        [],
        AnalyticalInputs(native_attention=facts, attention_dismissals=dismissed),
        tasks or [],
        NOW,
    )


# --- A. permission waits (rule 2) --------------------------------------------


def _case_test_two_prompts_for_one_agent_are_two_stable_rows() -> None:
    facts = NativeAttentionFacts(
        permissions=(
            PermissionWait("agent-1", "prompt-a", task_key=LEAF_KEY, role="worker", since=NOW),
            PermissionWait("agent-1", "prompt-b", task_key=LEAF_KEY, role="worker", since=NOW),
        ),
        permissions_complete=True,
    )
    tasks = [_task()]

    first = _queue(facts, tasks)
    second = _queue(facts, tasks)

    assert [item.id for item in first] == [
        "permission-wait:agent-1:prompt-a",
        "permission-wait:agent-1:prompt-b",
    ]
    assert first == second, "the same publication must not drift between passes"
    assert {item.itemClass for item in first} == {"action"}
    assert {item.dismissible for item in first} == {False}


def _case_test_an_empty_agent_list_produces_no_permission_row() -> None:
    assert _queue(NativeAttentionFacts(permissions=(), permissions_complete=True)) == []


def _case_test_a_bound_prompt_carries_its_task_and_a_taskless_prompt_does_not() -> None:
    facts = NativeAttentionFacts(
        permissions=(
            PermissionWait("agent-1", "prompt-a", task_key=LEAF_KEY, role="worker"),
            PermissionWait("agent-1", "prompt-b", task_key=MISSING_KEY, role="worker"),
        ),
        permissions_complete=True,
    )

    bound, taskless = _queue(facts, [_task()])

    assert bound.agentId == "agent-1"
    assert bound.promptId == "prompt-a"
    assert bound.taskKey == LEAF_KEY
    assert bound.taskReference == _ref()
    assert taskless.promptId == "prompt-b"
    assert taskless.agentId == "agent-1"
    assert taskless.taskKey is None
    assert taskless.taskReference is None


# --- B. held messages (rule 3) -----------------------------------------------


def _held(message_id: str, *, recipient: str = "agent-2", taken: str = "2026-10-22T11:50:00+00:00"):
    return HeldMessage(
        recipient_agent_id=recipient,
        message_id=message_id,
        sender_agent_id="agent-1",
        taken_at=taken,
        reason="recipient is rate limited",
        limited_until="2026-10-22T12:10:00+00:00",
        task_key=LEAF_KEY,
        role="worker",
    )


def _case_test_a_held_message_names_its_row_and_only_its_safe_reason() -> None:
    facts = NativeAttentionFacts(held=(_held("msg-1"),), held_complete=True)

    (row,) = _queue(facts, [_task()])

    assert row.id == "held-message:agent-2:msg-1"
    assert row.heldMessageId == "msg-1"
    assert row.agentId == "agent-2"
    assert row.detail == "recipient is rate limited until 2026-10-22T12:10:00+00:00"
    assert "the message body" not in row.model_dump_json()
    assert row.waitSeconds == 600.0
    assert row.taskKey == LEAF_KEY
    assert row.itemClass == "action"
    assert row.dismissible is False


def _case_test_a_second_held_message_is_a_second_row() -> None:
    facts = NativeAttentionFacts(held=(_held("msg-1"), _held("msg-2")), held_complete=True)

    rows = _queue(facts, [_task()])

    assert [row.id for row in rows] == [
        "held-message:agent-2:msg-1",
        "held-message:agent-2:msg-2",
    ]
    assert {row.itemClass for row in rows} == {"action"}
    assert {row.dismissible for row in rows} == {False}


# --- C. stall (rule 4) -------------------------------------------------------


@pytest.mark.parametrize(
    ("idle_seconds", "expected"), [(899, []), (900, ["task-stalled:" + LEAF_KEY])]
)
def test_stall_starts_at_exactly_the_settled_deadline(idle_seconds, expected) -> None:
    facts = NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(idle_seconds)})

    assert [row.id for row in _queue(facts, [_task(event_at=None)])] == expected


def _case_test_a_stall_row_opens_its_task_and_reports_the_idle_age() -> None:
    facts = NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(STALL_SECONDS)})

    (row,) = _queue(facts, [_task(event_at=None)])

    assert row.id == f"task-stalled:{LEAF_KEY}"
    assert row.taskKey == LEAF_KEY
    assert row.taskReference == _ref()
    assert row.waitSeconds == STALL_SECONDS
    assert row.itemClass == "action"
    assert row.dismissible is False


def _case_test_the_stall_detail_does_not_move_while_only_the_age_grows() -> None:
    """L190-R1-F12: the stable form of a stalled row is a function of the cause alone."""

    first = NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(STALL_SECONDS)})
    later = NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(STALL_SECONDS + 60)})

    (before,) = _queue(first, [_task(event_at=None)])
    (after,) = _queue(later, [_task(event_at=None)])

    assert before.detail == after.detail
    assert before.id == after.id
    assert before.waitSeconds is not None and after.waitSeconds is not None
    assert after.waitSeconds == before.waitSeconds + 60
    assert before.detail is not None
    assert not any(character.isdigit() for character in before.detail)


@pytest.mark.parametrize("activity", ["works", "unknown"])
def test_a_task_that_is_working_or_unknown_does_not_stall(activity) -> None:
    task = _task(activity=activity, event_at=_ago(STALL_SECONDS * 2))
    facts = NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(STALL_SECONDS * 2)})

    assert _queue(facts, [task]) == []


@pytest.mark.parametrize("stage", ["planned", "completed", "aborted"])
def test_only_a_started_task_can_stall(stage) -> None:
    task = _task(stage=stage, event_at=_ago(STALL_SECONDS * 2))
    facts = NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(STALL_SECONDS * 2)})

    assert _queue(facts, [task]) == []


def _case_test_an_old_task_event_stamp_never_dates_the_stall_interval() -> None:
    """L190-R1-F04: the deadline counts the interval the reader watched, not an old stamp."""

    old = _queue(
        NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(0)}),
        [_task(event_at=_ago(STALL_SECONDS * 4))],
    )

    assert old == []

    watched = _queue(
        NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(STALL_SECONDS)}),
        [_task(event_at=_ago(STALL_SECONDS * 4))],
    )

    assert [row.id for row in watched] == [f"task-stalled:{LEAF_KEY}"]


# --- D. pause suppression ----------------------------------------------------


def _master(key: str) -> TaskEntry:
    return _task(key).model_copy(update={"title": "Master"})


def _case_test_a_paused_master_suppresses_its_started_child() -> None:
    child = _task(LEAF_KEY, event_at=None, parent="repo/master.json")
    tasks = [_master("repo/master.json"), child]
    facts = NativeAttentionFacts(
        paused_task_keys=frozenset({"repo/master.json"}),
        stall_clocks={LEAF_KEY: _ago(STALL_SECONDS)},
    )

    assert _queue(facts, tasks) == []


def _case_test_the_same_child_still_stalls_when_no_key_is_paused() -> None:
    child = _task(LEAF_KEY, event_at=None, parent="repo/master.json")
    tasks = [_master("repo/master.json"), child]
    facts = NativeAttentionFacts(stall_clocks={LEAF_KEY: _ago(STALL_SECONDS)})

    assert [row.id for row in _queue(facts, tasks)] == [f"task-stalled:{LEAF_KEY}"]


def _case_test_an_unrelated_paused_master_suppresses_nothing() -> None:
    other_master = "repo/other.json"
    other_child = "repo/other/leaf.json"
    tasks = [
        _master("repo/master.json"),
        _task(LEAF_KEY, event_at=None, parent="repo/master.json"),
        _master(other_master),
        _task(other_child, event_at=None, parent=other_master),
    ]
    facts = NativeAttentionFacts(
        paused_task_keys=frozenset({other_master}),
        stall_clocks={LEAF_KEY: _ago(STALL_SECONDS), other_child: _ago(STALL_SECONDS)},
    )

    assert [row.id for row in _queue(facts, tasks)] == [f"task-stalled:{LEAF_KEY}"]


# --- E. notices (rules 5 and 6) ----------------------------------------------


def _notice(occurrence: str, *, sequence: int = 1, at: str = "2026-10-22T11:58:00+00:00"):
    return TerminalNotice(
        occurrenceId=occurrence,
        kind="task-landed",
        taskKey=LEAF_KEY,
        taskReference=_ref(),
        taskTitle="Leaf",
        detail="landed abc123def456 via merge",
        at=at,
        sequence=sequence,
    )


def _case_test_a_notice_is_one_dismissible_info_occurrence() -> None:
    facts = NativeAttentionFacts(notices=(_notice("task-landed:abc"),), notices_complete=True)

    (row,) = _queue(facts, [_task()])

    assert row.id == "task-landed:abc"
    assert row.occurrenceId == "task-landed:abc"
    assert row.kind == "task-landed"
    assert row.severity == "info"
    assert row.itemClass == "notice"
    assert row.dismissible is True
    assert row.taskKey == LEAF_KEY
    assert row.taskReference == _ref()


def _case_test_acknowledging_one_occurrence_leaves_a_later_one_visible() -> None:
    first = _notice("task-landed:abc", sequence=1)
    later = _notice("task-landed:def", sequence=2, at="2026-10-22T11:59:30+00:00")
    dismissed = {
        first.occurrenceId: AttentionDismissalRecord(
            itemId=first.occurrenceId,
            kind="task-landed",
            dismissedAt="2026-10-22T12:00:00+00:00",
        )
    }
    given = AnalyticalInputs(
        native_attention=NativeAttentionFacts(notices=(first,), notices_complete=True),
        attention_dismissals=dismissed,
    )

    assert build_attention_queue([], given, [_task()], NOW) == []

    given = AnalyticalInputs(
        native_attention=NativeAttentionFacts(notices=(first, later), notices_complete=True),
        attention_dismissals=dismissed,
    )
    assert [row.id for row in build_attention_queue([], given, [_task()], NOW)] == [
        "task-landed:def"
    ]

    given = AnalyticalInputs(
        native_attention=NativeAttentionFacts(notices=(later,), notices_complete=True),
        attention_dismissals=dismissed,
    )
    assert [row.id for row in build_attention_queue([], given, [_task()], NOW)] == [
        "task-landed:def"
    ]


# --- F. the real reducer path ranks a mixed queue ----------------------------


def _case_test_ranking_holds_across_a_mixed_native_queue() -> None:
    provider = ProviderNode(id="cgc", state="failed", ok=False, snapshotStaleSeconds=5)
    tasks = [_task(event_at=None)]
    facts = NativeAttentionFacts(
        permissions=(
            PermissionWait(
                "agent-1",
                "p0",
                task_key=LEAF_KEY,
                role="worker",
                since=NOW - timedelta(seconds=120),
            ),
            PermissionWait(
                "agent-1",
                "p2",
                task_key=LEAF_KEY,
                role="worker",
                since=NOW - timedelta(seconds=120),
            ),
            PermissionWait(
                "agent-1", "p1", task_key=LEAF_KEY, role="worker", since=NOW - timedelta(seconds=30)
            ),
        ),
        permissions_complete=True,
        held=(_held("msg-1"),),
        held_complete=True,
        stall_clocks={LEAF_KEY: _ago(STALL_SECONDS)},
        notices=(_notice("task-landed:abc", at=_ago(5)),),
        notices_complete=True,
    )

    queue = build_attention_queue(
        [provider],
        AnalyticalInputs(native_attention=facts),
        tasks,
        NOW,
    )

    assert [row.id for row in queue] == [
        "provider-down:cgc",
        f"task-stalled:{LEAF_KEY}",
        "held-message:agent-2:msg-1",
        "permission-wait:agent-1:p0",
        "permission-wait:agent-1:p2",
        "permission-wait:agent-1:p1",
        "task-landed:abc",
    ]
    assert [row.severity for row in queue] == [
        "alarm",
        "warn",
        "warn",
        "warn",
        "warn",
        "warn",
        "info",
    ]


# Consolidated cases: one group test per requirement. Every assertion and every
# failure message survives; each case runs as a subtest in its own root.


def test_permission_and_held_rows_are_one_row_each(subtests, tmp_path, monkeypatch) -> None:
    """Rules 2 and 3: every unanswered prompt and every held message is its own stable row, and a held row never carries the body text."""

    for message, run in (
        (
            "two prompts for one agent are two stable rows",
            lambda **fixtures: _case_test_two_prompts_for_one_agent_are_two_stable_rows(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "an empty agent list produces no permission row",
            lambda **fixtures: _case_test_an_empty_agent_list_produces_no_permission_row(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "a bound prompt carries its task and a taskless prompt does not",
            lambda **fixtures: (
                _case_test_a_bound_prompt_carries_its_task_and_a_taskless_prompt_does_not(
                    **_take(
                        fixtures,
                    )
                )
            ),
        ),
        (
            "a held message names its row and only its safe reason",
            lambda **fixtures: _case_test_a_held_message_names_its_row_and_only_its_safe_reason(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "a second held message is a second row",
            lambda **fixtures: _case_test_a_second_held_message_is_a_second_row(
                **_take(
                    fixtures,
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_a_stall_row_is_the_cause_and_every_break(subtests, tmp_path, monkeypatch) -> None:
    """Rule 4: the row opens its task, its wording is the cause alone, and an old stamp, bound work or a pause breaks the interval."""

    for message, run in (
        (
            "a stall row opens its task and reports the idle age",
            lambda **fixtures: _case_test_a_stall_row_opens_its_task_and_reports_the_idle_age(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "the stall detail does not move while only the age grows",
            lambda **fixtures: _case_test_the_stall_detail_does_not_move_while_only_the_age_grows(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "an old task event stamp never dates the stall interval",
            lambda **fixtures: _case_test_an_old_task_event_stamp_never_dates_the_stall_interval(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "a paused master suppresses its started child",
            lambda **fixtures: _case_test_a_paused_master_suppresses_its_started_child(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "the same child still stalls when no key is paused",
            lambda **fixtures: _case_test_the_same_child_still_stalls_when_no_key_is_paused(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "an unrelated paused master suppresses nothing",
            lambda **fixtures: _case_test_an_unrelated_paused_master_suppresses_nothing(
                **_take(
                    fixtures,
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_notices_and_ranking_across_the_one_queue(subtests, tmp_path, monkeypatch) -> None:
    """Rules 7 and 8: a notice is one dismissible occurrence, a later one is its own row, and everything ranks in one queue."""

    for message, run in (
        (
            "a notice is one dismissible info occurrence",
            lambda **fixtures: _case_test_a_notice_is_one_dismissible_info_occurrence(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "acknowledging one occurrence leaves a later one visible",
            lambda **fixtures: _case_test_acknowledging_one_occurrence_leaves_a_later_one_visible(
                **_take(
                    fixtures,
                )
            ),
        ),
        (
            "ranking holds across a mixed native queue",
            lambda **fixtures: _case_test_ranking_holds_across_a_mixed_native_queue(
                **_take(
                    fixtures,
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
