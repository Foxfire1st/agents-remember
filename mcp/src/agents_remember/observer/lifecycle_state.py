"""Lifecycle state vocabulary and the session-held lifecycle record.

The observable lifecycle's states and phases (design ``docs/design/
observable-lifecycle.md`` §1.2-1.4), plus the typed errors a signal raises
against an incompatible state. This module is pure vocabulary + data with no
I/O, so the projection reducer (a later slice) can import the state types
without pulling in the ambient singleton's threading and emission machinery.

The state vocabulary is declared as a *partition* -- a live half and a terminal
half, composed into ``State`` -- so that "which states are terminal" and "which
states exist" cannot drift apart into two lists that disagree.

To be plain about what that does and does not remove: the state NAMES are still
written by hand, and always will be -- a vocabulary has to be typed somewhere.
What is gone is the *second* hand-written list, the one that could disagree with
the first. ``TERMINAL_STATES`` is no longer a set standing beside ``State``
naming two of its members again; it is half of ``State``, and the halves are
where the names live. Nothing downstream re-derives the split.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import UnionType
from typing import Literal, Union, cast, get_args, get_origin

from agents_remember.errors import AgentsRememberError
from agents_remember.models.lifecycle import LiveState, Phase, State, TerminalState


class LifecycleVocabularyError(AgentsRememberError):
    """A lifecycle vocabulary declaration is not one this module can read.

    Raised at import, naming the declaration and the offending member, rather than
    letting a malformed vocabulary travel: bare ``get_args`` on the union form
    ``Literal[...] | Other`` hands back ``Literal`` OBJECTS, and the first consumer to
    call ``.split`` on one dies with ``AttributeError`` -- taking down every importer of
    ``agents_remember.observer`` instead of failing the one declaration that is wrong.
    """


def vocabulary_names(spec: object, *, label: str) -> tuple[str, ...]:
    """The strings a ``Literal`` vocabulary declares, in declaration order.

    Reads through every form the declaration can legally take: a flat
    ``Literal["a", "b"]``, a composition of aliases (``Literal[LiveState, TerminalState]``,
    which PEP 586 flattens), and the union form (``Literal[...] | ReviewState``, which it
    does not). Anything that is not a string -- ``Literal[...] | str``, an enum member, a
    stray ``None`` -- is refused here, by name.
    """
    names: list[str] = []
    _collect_names(spec, label=label, into=names)
    if not names:
        raise LifecycleVocabularyError(
            f"{label} declares no members; expected a Literal of strings"
        )
    return tuple(names)


def _collect_names(spec: object, *, label: str, into: list[str]) -> None:
    if isinstance(spec, str):
        into.append(spec)
        return
    if get_origin(spec) in (Literal, Union, UnionType):
        for arg in get_args(spec):
            _collect_names(arg, label=label, into=into)
        return
    raise LifecycleVocabularyError(
        f"{label} contains {spec!r}, which is not a string member; a lifecycle vocabulary "
        "must be a Literal of strings (nesting and unions of those are fine)"
    )


def check_state_partition(*, live: object, terminal: object, whole: object) -> tuple[str, ...]:
    """Verify ``whole`` is EXACTLY its live half plus its terminal half, and return its names.

    This is what keeps terminality from being a second opinion. ``State`` is *composed*
    from the two halves below rather than declared independently and then filtered, so a
    new state cannot enter the vocabulary without being filed on one side of the line --
    and the one way to smuggle one in anyway (appending a bare literal to the composition)
    fails here, at import, naming it.
    """
    live_names = vocabulary_names(live, label="the live half")
    terminal_names = vocabulary_names(terminal, label="the terminal half")
    whole_names = vocabulary_names(whole, label="State")
    both = sorted(set(live_names) & set(terminal_names))
    if both:
        raise LifecycleVocabularyError(f"state(s) {both} are filed as both live and terminal")
    unfiled = [name for name in whole_names if name not in {*live_names, *terminal_names}]
    if unfiled:
        raise LifecycleVocabularyError(
            f"state(s) {unfiled} are declared on State but filed neither live nor terminal; "
            "every state must join the live half or the terminal half, because that filing "
            "is what decides whether it gets a metrics bucket and whether the lifecycle is over"
        )
    orphans = [name for name in (*live_names, *terminal_names) if name not in whole_names]
    if orphans:
        raise LifecycleVocabularyError(f"state(s) {orphans} are filed but absent from State")
    return whole_names


STATES: tuple[State, ...] = cast(
    "tuple[State, ...]", check_state_partition(live=LiveState, terminal=TerminalState, whole=State)
)
LIVE_STATES: tuple[LiveState, ...] = cast(
    "tuple[LiveState, ...]", vocabulary_names(LiveState, label="LiveState")
)
TERMINAL_STATES: frozenset[str] = frozenset(vocabulary_names(TerminalState, label="TerminalState"))

# Not a classification: a policy. ``lifecycle_end`` takes a free-form outcome string from the
# tool boundary, and anything that is not the affirmative completion is an abandonment.
DEFAULT_END_OUTCOME: TerminalState = "abandoned"

INITIAL_PHASE: Phase = "request"
PHASES: tuple[Phase, ...] = cast("tuple[Phase, ...]", vocabulary_names(Phase, label="Phase"))


def coerce_end_outcome(value: object) -> TerminalState:
    """The terminal state a ``lifecycle.ended`` outcome names.

    The outcome and the terminal state are the same vocabulary, so this is a membership
    test rather than a mapping table: an unrecognized or missing outcome is an abandonment
    (:data:`DEFAULT_END_OUTCOME`), never a completion.
    """
    if isinstance(value, str) and value in TERMINAL_STATES:
        return cast("TerminalState", value)
    return DEFAULT_END_OUTCOME


class LifecycleError(AgentsRememberError):
    """A lifecycle signal was issued against an incompatible state."""


class GuardedStartError(LifecycleError):
    """``lifecycle_start`` was issued while a lifecycle is already active.

    Guarded start is how the model is kept from ever holding two lifecycles or
    handling an id: the reminder names the active lifecycle so the model ends or
    switches it rather than stacking a new one.
    """

    def __init__(self, active_id: str) -> None:
        super().__init__(
            f"a lifecycle is already active ({active_id}); end or switch it before starting another"
        )
        self.active_id = active_id


def coerce_phase(value: str) -> Phase:
    """Validate a raw phase string from the tool boundary into a ``Phase``."""
    if value not in PHASES:
        raise LifecycleError(f"unknown phase {value!r}; expected one of {', '.join(PHASES)}")
    return cast(Phase, value)


@dataclass(frozen=True)
class LifecycleState:
    """The single lifecycle a session currently holds (zero or one).

    Frozen so each transition produces a new value -- no shared-mutable state
    surprises between the request thread and the heartbeat thread, and every
    prior state stays auditable in the event log rather than being overwritten.
    """

    id: str
    state: State
    phase: Phase
    fleeting: bool
    started_at: str
    # Set when the lifecycle is promoted to persistent (design §1.1, §1.5): the
    # enclosure (contract path) is the durable resume anchor; repo_id/scope record
    # where saved work belongs ("<repo_id>" | "0_unscoped" | "1_cross-repo").
    enclosure: str | None = None
    repo_id: str | None = None
    scope: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES
