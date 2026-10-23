"""The native facts the attention queue reports, and the bounds it reports them within.

Five kinds (MIK-R200 rule 1) come from facts the product already owns: the host publication's
unanswered permission prompts, the product's held-message stores, an explicit pause plus a bound
agent's silence, and the terminal notice store the landing and abandon routes publish into. None
of them is inferred from lifecycle prose, and none needs a new host poll (rule 9): every fact here
is read on an existing invalidation or on the observer heartbeat, from files the product writes.

Every collection is bounded by the constant beside it, and so is the per-refresh work that fills
it. The bounds are named here, once, where the reader and the builders both read them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from agents_remember.controlplane.attention_dismissals import AttentionObservation
from agents_remember.controlplane.attention_notices import TerminalNotice

#: Rule 4's settled stall predicate: 900 continuous known seconds with no bound agent in a turn.
#: v1 adds no user setting for it.
STALL_SECONDS = 900.0
#: Published permission items kept at once; one per unanswered native prompt, per agent.
MAX_PERMISSION_ITEMS = 128
#: Held items kept at once; the hold stores themselves hold at most 20 per recipient.
MAX_HELD_ITEMS = 128
#: Receipts visited in one pass, from the product's own bounded held-message queue (512 entries).
MAX_HELD_RECEIPTS = 64
#: Activation records read in one pass: one per series contract, from one small directory.
MAX_PAUSE_RECORDS = 256
#: Terminal notices published into the queue at once (the store retains 64 occurrences).
MAX_TERMINAL_ITEMS = 64
#: Per-task first-eligible observations retained for the stall clock (one per started task).
MAX_STALL_CLOCKS = 2048
#: Parsed files one reader keeps between looks. It is the sum of the admitted populations it
#: parses (every pause record it may read, every held receipt and store it may visit, plus the
#: notice store), so no admitted population can evict its own cache (L190-R1-F11).
PARSED_FILE_BUDGET = MAX_PAUSE_RECORDS + 2 * MAX_HELD_RECEIPTS + MAX_HELD_ITEMS + 8


@dataclass(frozen=True)
class PermissionWait:
    """One unanswered native prompt, retained with the agent and prompt that name it."""

    agent_id: str
    prompt_id: str
    task_key: str | None = None
    role: str | None = None
    since: datetime | None = None


@dataclass(frozen=True)
class HeldMessage:
    """One message the product actually holds, named by its own store row.

    ``reason`` is the store's own safe reason -- never the text, which the store keeps out of
    the publication entirely (rule 3).
    """

    recipient_agent_id: str
    message_id: str
    sender_agent_id: str
    taken_at: str
    reason: str
    limited_until: str | None = None
    task_key: str | None = None
    role: str | None = None


@dataclass(frozen=True)
class NativeAttentionFacts:
    """Everything the five native builders read, with the completeness of each observation.

    ``permissions_complete`` says whether the host publication behind the prompts was whole. The
    retained facts survive an incomplete read (rule 2): an unreadable observation resolves no
    prompt, so the last known occurrence stays visible until a complete one says otherwise.
    """

    permissions: tuple[PermissionWait, ...] = ()
    permissions_complete: bool = False
    held: tuple[HeldMessage, ...] = ()
    held_complete: bool = False
    paused_task_keys: frozenset[str] = frozenset()
    pauses_complete: bool = False
    notices: tuple[TerminalNotice, ...] = ()
    notices_complete: bool = False
    # Task key -> ISO time the task entered its current continuous eligible silence. A task that
    # stops being eligible (bound work, pause, an unknown observation, a boot change) loses its
    # entry, so the interval can only be one an observation actually covered (rule 4).
    stall_clocks: dict[str, str] = field(default_factory=dict)


def task_lineage(key: str, parent_of: dict[str, str | None]) -> frozenset[str]:
    """This task and the masters above it, by the canonical parent relation.

    One owner for the lineage both the stall clock and the stall builder read, so a paused
    master suppresses its children in exactly the same way in both places (MIK-R200 rule 4).
    """

    seen = {key}
    parent = parent_of.get(key)
    while parent is not None and parent not in seen:
        seen.add(parent)
        parent = parent_of.get(parent)
    return frozenset(seen)


def observe_terminal_notices(
    notices: tuple[TerminalNotice, ...],
    observations: dict[str, AttentionObservation],
) -> None:
    """A notice acknowledgement ends only when its occurrence leaves the retained set.

    A notice is acknowledged once per occurrence, so it is never reported healthy: while the
    unchanged occurrence is still retained, its acknowledgement is kept -- a partial refresh
    that reintroduces the same row must not resurrect it. An occurrence the store dropped (its
    bounded working set evicted it) is gone, and its acknowledgement is reclaimed with it.
    """
    for notice in notices:
        observation = observations.get(notice.kind)
        if observation is not None:
            observation.observe(notice.occurrenceId)


StallSource = Literal["observed-idle", "first-eligible"]
