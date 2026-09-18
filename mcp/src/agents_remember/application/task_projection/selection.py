"""The explicit projection read plan: how much a bound seat reads, and which channels it gets.

The requirement is that a seat reads only the task altitude and the relevant
ancestors and neighbours its role and operation require -- a worker must not
receive the sprint's whole decision history while an orchestrator receives the
portfolio facts its decisions need. That is encoded as three declared tables, not
as a filter applied after loading everything:

* **seat kind x task altitude x parent kind** decides *how much* is read. The
  table is total over all eighteen combinations, so there is no early return, no
  default branch and no combination that silently falls back to a wider read. The
  ambient launcher is a seat kind, so its thin plan is a declared row rather than
  a special case beside the tables.
* **seat kind x task altitude** decides whether the seat admits its own
  document's portfolio facts.
* **operation** decides *which channels* the projection carries, so two
  operations over one binding produce genuinely different documents.

One rule is load-bearing and must not be relaxed: a leaf-altitude **role** seat
never reads a sprint-altitude ancestor. A leaf reads its own document plus its
immediate parent when that parent is a master; when the immediate parent is a
sprint, the leaf reads its own document only and the sprint is reached by
expansion reference instead. Ancestor decision logs are *referenced with their
entry count* rather than injected, so "the smallest complete task projection"
does not become "the whole series".

Nothing here is a heuristic. Every entry is inspectable data, and
:func:`operation_channels` fails loudly when the frozen operation vocabulary
grows without a channel set.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.errors import TaskProjectionSourceError
from agents_remember.models.role_capsules.vocabulary import (
    CAPSULE_OPERATIONS,
    CapsuleOperation,
    CapsuleRole,
    CapsuleSeatKind,
)
from agents_remember.tasks.document_refs import TaskAltitude

from .statuses import STATUS_OPERATION_UNSUPPORTED
from .types import ProjectionChannel, ProjectionReadPlan

_LAUNCHER: CapsuleSeatKind = "launcher"
_ROLE: CapsuleSeatKind = "role"

#: A parent that is neither a master nor a sprint (there is none today) reads as
#: "no ancestor document": the seat reads its own task document only. Declared so
#: the table below is total rather than open-ended.
_PARENT_BUCKETS: tuple[str, ...] = ("master", "sprint", "none")

#: ``(seat kind, own altitude, parent bucket)`` -> the altitudes the projection
#: reads. Leaf/master/sprint come from :mod:`agents_remember.tasks.document_refs`;
#: the "none" bucket covers a standalone leaf, a standalone master and every
#: sprint. All eighteen combinations are enumerated, including every launcher row,
#: so nothing about the read scope is decided outside the table.
_READ_ALTITUDES: Mapping[tuple[CapsuleSeatKind, TaskAltitude, str], tuple[TaskAltitude, ...]] = {
    (_ROLE, "leaf", "master"): ("leaf", "master"),
    (_ROLE, "leaf", "sprint"): ("leaf",),
    (_ROLE, "leaf", "none"): ("leaf",),
    (_ROLE, "master", "master"): ("master",),
    (_ROLE, "master", "sprint"): ("master",),
    (_ROLE, "master", "none"): ("master",),
    (_ROLE, "sprint", "master"): ("sprint",),
    (_ROLE, "sprint", "sprint"): ("sprint",),
    (_ROLE, "sprint", "none"): ("sprint",),
    # The ambient launcher routes; it does not work a task. Its own document is the
    # only document it reads, at whatever altitude it happens to be bound to.
    (_LAUNCHER, "leaf", "master"): ("leaf",),
    (_LAUNCHER, "leaf", "sprint"): ("leaf",),
    (_LAUNCHER, "leaf", "none"): ("leaf",),
    (_LAUNCHER, "master", "master"): ("master",),
    (_LAUNCHER, "master", "sprint"): ("master",),
    (_LAUNCHER, "master", "none"): ("master",),
    (_LAUNCHER, "sprint", "master"): ("sprint",),
    (_LAUNCHER, "sprint", "sprint"): ("sprint",),
    (_LAUNCHER, "sprint", "none"): ("sprint",),
}

#: ``(seat kind, own altitude)`` -> whether the seat admits the portfolio facts of
#: its own document -- a sprint's commanded masters, seats and execution topology,
#: or a master's series index. A leaf never does: its own slice plus its immediate
#: parent is the whole scope. A launcher never does: routing is not portfolio work.
_PORTFOLIO_FACTS: Mapping[tuple[CapsuleSeatKind, TaskAltitude], bool] = {
    (_ROLE, "leaf"): False,
    (_ROLE, "master"): True,
    (_ROLE, "sprint"): True,
    (_LAUNCHER, "leaf"): False,
    (_LAUNCHER, "master"): False,
    (_LAUNCHER, "sprint"): False,
}

#: ``seat kind`` -> the channels that seat may carry at all. A role seat's set comes
#: from the operation table below; the launcher's is fixed and thin, which is the
#: "no false architect seat" rule expressed as data instead of a branch.
_SEAT_KIND_CHANNELS: Mapping[CapsuleSeatKind, tuple[ProjectionChannel, ...] | None] = {
    _ROLE: None,
    _LAUNCHER: ("objective", "scope"),
}

#: Operation -> the channels that operation's projection carries. One entry per
#: operation in the compiler's frozen vocabulary.
_OPERATION_CHANNELS: Mapping[CapsuleOperation, tuple[ProjectionChannel, ...]] = {
    "orientation": ("objective", "scope", "preservation"),
    "planning": (
        "objective",
        "requirements",
        "acceptance",
        "scope",
        "decisions",
        "preservation",
        "portfolio",
        "handoff",
    ),
    "implementation": (
        "objective",
        "requirements",
        "acceptance",
        "scope",
        "decisions",
        "preservation",
        "handoff",
    ),
    "review": ("objective", "requirements", "acceptance", "decisions", "preservation", "evidence"),
    "curation": ("objective", "requirements", "acceptance", "scope", "evidence", "handoff"),
    "coordination": ("objective", "requirements", "decisions", "portfolio", "handoff"),
    "authorized-closeout": ("objective", "requirements", "scope", "decisions", "handoff"),
    "recovery": (
        "objective",
        "requirements",
        "scope",
        "decisions",
        "preservation",
        "handoff",
        "evidence",
    ),
    # The first-hour seat reads why the workspace is being set up, what is in scope, what
    # must not be disturbed, and what it hands over. It deliberately reads no task
    # obligations and no acceptance conditions: at setup time there is no task document to
    # carry them, which is exactly why this operation's channel set is the smallest one.
    "bootstrap": ("objective", "scope", "preservation", "handoff"),
}


def operation_channels(operation: CapsuleOperation) -> tuple[ProjectionChannel, ...]:
    """The channels one operation projects.

    A missing entry is a refusal rather than an empty projection: a new operation
    in the compiler's frozen vocabulary that silently projected nothing would be
    indistinguishable from a seat with no obligations.
    """

    channels = _OPERATION_CHANNELS.get(operation)
    if channels is None:
        raise TaskProjectionSourceError(
            STATUS_OPERATION_UNSUPPORTED,
            f"operation {operation!r} declares no projection channels",
            next_action=(
                "add the operation's channel set to "
                "application/task_projection/selection.py; the frozen operation "
                f"vocabulary is {list(CAPSULE_OPERATIONS)!r}"
            ),
        )
    return channels


def _parent_bucket(parent_altitude: TaskAltitude | None) -> str:
    return parent_altitude if parent_altitude in ("master", "sprint") else "none"


def read_plan(
    *,
    role: CapsuleRole | None,
    altitude: TaskAltitude,
    parent_altitude: TaskAltitude | None,
    operation: CapsuleOperation,
) -> ProjectionReadPlan:
    """Resolve exactly what this seat reads for this operation, by table lookup only."""

    kind: CapsuleSeatKind = _LAUNCHER if role is None else _ROLE
    declared = _SEAT_KIND_CHANNELS[kind]
    portfolio = _PORTFOLIO_FACTS[(kind, altitude)]
    channels: tuple[ProjectionChannel, ...] = tuple(
        channel
        for channel in (operation_channels(operation) if declared is None else declared)
        if channel != "portfolio" or portfolio
    )
    return ProjectionReadPlan(
        altitude=altitude,
        read_altitudes=_READ_ALTITUDES[(kind, altitude, _parent_bucket(parent_altitude))],
        portfolio_facts=portfolio,
        channels=channels,
    )


__all__ = ["operation_channels", "read_plan"]
