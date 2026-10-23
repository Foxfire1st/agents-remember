"""The five native attention builders (MIK-R200 rules 1-8).

Each kind is one small deterministic builder over facts the product already publishes or keeps,
beside the surviving operational builders in :mod:`._attention`. Nothing here infers a cause from
lifecycle prose, matches a title, or invents a task for a taskless seat: every row carries the
canonical task key and reference it opens, or the agent it names and no task at all.

Two rules separate the kinds for the client. ``permission-wait``, ``held-message`` and
``task-stalled`` are live causes: they clear when their native cause ends, and their ``itemClass``
says so. ``task-landed`` and ``task-abandoned`` are notices. Under MIK-R200@v2 rule 7 both classes
are acknowledged once per occurrence through the same dismissal owner -- the developer reads an
entry once -- so every row here is dismissible, and what differs is only how its occurrence ends
and how its identity is named.
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
    """One row per unanswered native prompt, at the agent that is waiting (rule 2).

    Its occurrence identity is the agent and the prompt (v2 rule 7), so answering that prompt ends
    it, and a second prompt at the same agent is a second entry.
    """

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
                dismissible=True,
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
    """One row per message the product actually holds (rule 3): no body, no attachment.

    Its occurrence identity is the held message itself (v2 rule 7): native delivery or withdrawal
    ends it, and another held message is another entry.
    """

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
                dismissible=True,
                taskKey=task.key if task is not None else None,
                taskReference=task.reference if task is not None else None,
                agentId=message.recipient_agent_id,
                agentRole=message.role,
                heldMessageId=message.message_id,
            )
        )
    return items


def stall_occurrences(facts: NativeAttentionFacts, tasks: list[TaskEntry]) -> frozenset[str]:
    """The identity of every started task's current idle interval (MIK-R200@v2 rule 7).

    A stall acknowledgement names one interval, so the live identities are the tasks that are
    idle *now* -- whether or not the settled deadline has passed. A task that resumed work, took
    an explicit pause, or began a new interval after a turn leaves this set, and that is the proof
    that its acknowledgement is over. The deadline is deliberately not part of the identity: a
    reader that restarted counts the 900 seconds again from its own watch, and the row it will
    publish names the very interval the developer already read.
    """

    return frozenset(
        f"task-stalled:{task.key}:{start}" for task, start in _stall_candidates(facts, tasks)
    )


def _stall_candidates(
    facts: NativeAttentionFacts, tasks: list[TaskEntry]
) -> list[tuple[TaskEntry, str]]:
    """Every started task that is idle right now, with the identity of that idle interval.

    One owner for the eligibility the stall row and the stall acknowledgement both read, so the
    row that is published and the interval that is reclaimed can never be two different things.
    """

    parent_of = {task.key: task.parent for task in tasks}
    candidates: list[tuple[TaskEntry, str]] = []
    for task in tasks:
        if task.stage != "started" or task.activity != "paused":
            continue
        if _paused(task_lineage(task.key, parent_of), facts.paused_task_keys):
            continue
        start = _interval_start(task, facts)
        if start is not None:
            candidates.append((task, start))
    return candidates


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

    The 900 seconds are counted from the reader's own watch (L190-R1-F04), while the occurrence
    identity names the interval's start (v2 rule 7): a resumed bound turn ends the occurrence, so
    the next idle interval is a new entry rather than one the developer's earlier acknowledgement
    still hides.
    """

    items: list[AttentionItem] = []
    for task, start in _stall_candidates(facts, tasks):
        since = _parse_stamp(facts.stall_clocks.get(task.key))
        if since is None:
            continue
        idle = max(0.0, (now - since).total_seconds())
        if idle < STALL_SECONDS:
            continue
        items.append(
            AttentionItem(
                id=f"task-stalled:{task.key}:{start}",
                kind="task-stalled",
                severity="warn",
                lane="task",
                title=f"{task.title or task.id} has no agent in a turn",
                # L190-R1-F12: the cause is what this row is about, so its words do not move
                # as the minute count does. Age travels in the volatile `waitSeconds` only.
                detail="no bound agent has held a turn for the settled interval",
                waitSeconds=idle,
                itemClass="action",
                dismissible=True,
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


def _interval_start(task: TaskEntry, facts: NativeAttentionFacts) -> str | None:
    """The stamp that names one idle interval: when the task's last bound turn ended.

    MIK-R200@v2 rule 7 names a stall's occurrence by the task *and the start of that idle
    interval*. The publication dates that moment itself -- its bound agents' own ``eventAt``
    turn transition -- so a turn ends the interval and moves the identity with it, and a reader
    that restarted after the developer dismissed the entry derives the same stamp again and the
    acknowledgement keeps hiding the interval it acknowledged.

    A task with no bound agent falls back to its own last event stamp, then to the reader's own
    watch (``stall_clocks``) -- the one stamp a restart cannot reproduce. Nothing here dates the
    900-second deadline: L190-R1-F04 keeps that on the watch, because a stamp older than the
    interval cannot date an interval nobody observed.
    """

    latest: str | None = None
    latest_at: datetime | None = None
    for stamp in facts.turn_ends.get(task.key, ()):
        parsed = _parse_stamp(stamp)
        if parsed is None:
            continue
        if latest_at is None or parsed > latest_at:
            latest, latest_at = stamp, parsed
    if latest is not None:
        return latest
    turn_end = _parse_stamp(task.eventAt)
    if turn_end is not None and turn_end.tzinfo is not None:
        return task.eventAt
    return facts.stall_clocks.get(task.key)


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
