"""The five native attention builders (MIK-R200 rules 1-8).

Each kind is one small deterministic builder over facts the product already publishes or keeps,
beside the surviving operational builders in :mod:`._attention`. Nothing here infers a cause from
lifecycle prose, matches a title, or invents a task for a taskless seat: every row carries the
canonical task key and reference it opens, or the agent it names and no task at all.

Two rules separate the kinds for the client. ``permission-wait``, ``held-message`` and
``task-stalled`` are live causes: they clear only when their native cause ends, they are not
dismissible, and the backend refuses a dismissal of one. ``task-landed`` and ``task-abandoned``
are notices: one acknowledgement per occurrence, and a later distinct occurrence is its own row.
"""

from __future__ import annotations

from datetime import datetime

from agents_remember.controlplane.attention_notices import TerminalNotice
from agents_remember.observer.native_attention import (
    MAX_HELD_ITEMS,
    MAX_PERMISSION_ITEMS,
    MAX_TERMINAL_ITEMS,
    STALL_SECONDS,
    HeldMessage,
    NativeAttentionFacts,
    PermissionWait,
    task_lineage,
)
from agents_remember.observer.projection import AttentionItem, TaskEntry


def native_attention_items(
    facts: NativeAttentionFacts,
    tasks: list[TaskEntry],
    now: datetime,
) -> list[AttentionItem]:
    """Every native row, ranked later by the queue's one total sort."""

    return [
        *_permission_items(facts.permissions, tasks, now),
        *_held_items(facts.held, tasks, now),
        *_stall_items(facts, tasks, now),
        *_notice_items(facts.notices, tasks, now),
    ]


def _permission_items(
    permissions: tuple[PermissionWait, ...],
    tasks: list[TaskEntry],
    now: datetime,
) -> list[AttentionItem]:
    """One row per unanswered native prompt, at the agent that is waiting (rule 2)."""

    by_key = {task.key: task for task in tasks}
    items: list[AttentionItem] = []
    for prompt in permissions[:MAX_PERMISSION_ITEMS]:
        task = by_key.get(prompt.task_key) if prompt.task_key else None
        items.append(
            AttentionItem(
                id=f"permission-wait:{prompt.agent_id}:{prompt.prompt_id}",
                kind="permission-wait",
                severity="warn",
                lane="agent",
                title=f"{_agent_label(prompt.role)} waits for a permission answer",
                detail=None,
                waitSeconds=_age(now, prompt.since),
                itemClass="action",
                dismissible=False,
                taskKey=task.key if task is not None else None,
                taskReference=task.reference if task is not None else None,
                agentId=prompt.agent_id,
                agentRole=prompt.role,
                promptId=prompt.prompt_id,
            )
        )
    return items


def _held_items(
    held: tuple[HeldMessage, ...],
    tasks: list[TaskEntry],
    now: datetime,
) -> list[AttentionItem]:
    """One row per message the product actually holds (rule 3): no body, no attachment."""

    by_key = {task.key: task for task in tasks}
    items: list[AttentionItem] = []
    for message in held[:MAX_HELD_ITEMS]:
        task = by_key.get(message.task_key) if message.task_key else None
        items.append(
            AttentionItem(
                id=f"held-message:{message.recipient_agent_id}:{message.message_id}",
                kind="held-message",
                severity="warn",
                lane="agent",
                title=f"Message held for {_agent_label(message.role)}",
                detail=_held_reason(message),
                waitSeconds=_age(now, _parse_stamp(message.taken_at)),
                itemClass="action",
                dismissible=False,
                taskKey=task.key if task is not None else None,
                taskReference=task.reference if task is not None else None,
                agentId=message.recipient_agent_id,
                agentRole=message.role,
                heldMessageId=message.message_id,
            )
        )
    return items


def _stall_items(
    facts: NativeAttentionFacts,
    tasks: list[TaskEntry],
    now: datetime,
) -> list[AttentionItem]:
    """A started task with no bound agent in a turn for 900 continuous known seconds (rule 4).

    Eligibility is the task entry's own truth: only a started task, only while the host
    observation is known (an unknown observation resolves nothing), and only while no bound
    agent works -- a permission wait and a running subagent both count as work, so neither
    duplicates a stall row. An explicit pause suppresses the master the developer stopped and
    every started child bound to it by the canonical parent chain, and nothing else.
    """

    parent_of = {task.key: task.parent for task in tasks}
    items: list[AttentionItem] = []
    for task in tasks:
        if task.stage != "started" or task.activity != "paused":
            continue
        if _paused(task_lineage(task.key, parent_of), facts.paused_task_keys):
            continue
        since = _idle_since(task, facts)
        if since is None:
            continue
        idle = max(0.0, (now - since).total_seconds())
        if idle < STALL_SECONDS:
            continue
        items.append(
            AttentionItem(
                id=f"task-stalled:{task.key}",
                kind="task-stalled",
                severity="warn",
                lane="task",
                title=f"{task.title or task.id} has no agent in a turn",
                # L190-R1-F12: the cause is what this row is about, so its words do not move
                # as the minute count does. Age travels in the volatile `waitSeconds` only.
                detail="no bound agent has held a turn for the settled interval",
                waitSeconds=idle,
                itemClass="action",
                dismissible=False,
                taskKey=task.key,
                taskReference=task.reference,
            )
        )
    return items


def _notice_items(
    notices: tuple[TerminalNotice, ...],
    tasks: list[TaskEntry],
    now: datetime,
) -> list[AttentionItem]:
    """Landed and abandoned occurrences, one acknowledgement each (rule 5)."""

    by_key = {task.key: task for task in tasks}
    items: list[AttentionItem] = []
    for notice in notices[:MAX_TERMINAL_ITEMS]:
        task = by_key.get(notice.taskKey)
        items.append(
            AttentionItem(
                id=notice.occurrenceId,
                kind=notice.kind,
                severity="info",
                lane="task",
                title=(
                    f"{notice.taskTitle} landed"
                    if notice.kind == "task-landed"
                    else f"{notice.taskTitle} abandoned"
                ),
                detail=notice.detail,
                waitSeconds=_age(now, _parse_stamp(notice.at)),
                itemClass="notice",
                dismissible=True,
                taskKey=notice.taskKey,
                taskReference=task.reference if task is not None else notice.taskReference,
                occurrenceId=notice.occurrenceId,
            )
        )
    return items


def _task_lineage(task: TaskEntry, by_key: dict[str, TaskEntry]) -> set[str]:
    """Retained for the module's other readers; the shared owner lives in native_attention."""

    return set(task_lineage(task.key, {key: row.parent for key, row in by_key.items()}))


def _paused(lineage: set[str] | frozenset[str], paused: frozenset[str]) -> bool:
    return bool(lineage & paused)


def _idle_since(task: TaskEntry, facts: NativeAttentionFacts) -> datetime | None:
    """When this task's continuous eligible silence started, or None when nobody can say.

    L190-R1-F04: only the interval the reader itself observed counts. A task-event stamp is
    the last event of the task's own contributors, which may be far older than the moment the
    task became eligible -- it cannot date an interval nobody watched -- so it is not used here.
    """

    stamp = facts.stall_clocks.get(task.key)
    return _parse_stamp(stamp) if stamp else None


def _agent_label(role: str | None) -> str:
    return f"The {role}" if role else "An agent"


def _held_reason(message: HeldMessage) -> str:
    if message.limited_until:
        return f"{message.reason} until {message.limited_until}"
    return message.reason


def _age(now: datetime, stamp: datetime | None) -> float | None:
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        return None
    return max(0.0, (now - stamp).total_seconds())


def _parse_stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None
