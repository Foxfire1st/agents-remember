"""What the control plane needs to know about a seat, declared by the control plane.

The routing and rebind predicates in this package all reason over the terminal seat
catalog: who spawned whom, which seat is still running, which leaf a seat is bound to.
They used to reach UP for that vocabulary and import
``serving.terminal_catalog.TerminalCatalog`` directly, which put a pure decision inside
the import closure of an HTTP application and made ``controlplane`` and ``serving``
mutually dependent -- neither could be loaded, read or tested without the other.

The direction is inverted here, per ``layers.toml``: ``controlplane`` is the lowest
domain service, so it DECLARES the shape it reads and the catalog SATISFIES it. No
class implements these explicitly and none needs to -- ``TerminalCatalogEntry`` and
``TerminalCatalog`` already have these members, so the match is structural and
the compatibility is checked at every call site rather than asserted once here.

Every member is a read-only property on purpose, and it is not a style choice. A
protocol attribute written ``status: str`` is read-write, which makes it INVARIANT: a
catalog row whose ``status`` is a narrow ``Literal["running", ...]`` would then fail to
match, because a writer of ``str`` could store something the literal forbids. Declared
as a read-only property the member is covariant, so the catalog keeps its precise
literal types and this package reads them at the width it actually uses -- comparison
against string constants. Nothing here writes a seat row; the catalog owns every
mutation of one.

The widths below are deliberately the widest this package can work with, not copies of
the catalog's own types. Copying ``TerminalSessionStatus`` here would be the same
dependency with a second definition of it, and the two would drift.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from agents_remember.errors import SeatOccupancyError
from agents_remember.models.task_document_ref import TaskDocumentRef


class SeatRow(Protocol):
    """One seat's row, as the control plane reads it.

    The members are exactly those this package's predicates touch: identity, liveness,
    spawn provenance, leaf binding and turn state. A member nothing here reads is not
    declared, because a protocol wider than its use is a coupling nobody asked for.
    """

    @property
    def id(self) -> str: ...

    @property
    def kind(self) -> str: ...

    @property
    def status(self) -> str: ...

    @property
    def lifecycle_id(self) -> str | None: ...

    @property
    def created_at(self) -> str: ...

    @property
    def last_attached_at(self) -> str: ...

    @property
    def landed_at(self) -> str | None: ...

    @property
    def task_document_ref(self) -> TaskDocumentRef | None: ...

    @property
    def replacement_for_task_document_ref(self) -> TaskDocumentRef | None: ...

    @property
    def spawned_by_session(self) -> str | None: ...

    @property
    def spawned_by_lifecycle(self) -> str | None: ...

    @property
    def structural_parent_task_document_ref(self) -> TaskDocumentRef | None: ...

    @property
    def structural_parent_role(self) -> str | None: ...

    @property
    def spawn_role(self) -> str | None: ...

    @property
    def turn_state(self) -> str | None: ...

    @property
    def turn_state_changed_at(self) -> str | None: ...

    @property
    def binding_role(self) -> str: ...

    @property
    def binding_task_document_ref(self) -> TaskDocumentRef | None: ...


class SeatDirectory(Protocol):
    """The seat catalog, as the control plane reads it: two pure reads and nothing else.

    ``list`` is declared to return a ``Sequence`` rather than a ``list`` for the same
    covariance reason the members above are properties -- a ``list`` return would force
    an exact element type and reject the catalog's own ``list[TerminalCatalogEntry]``.
    An implementation may take further optional arguments (the catalog's ``list`` has an
    ``include_terminated`` flag); this package never passes one, and asking for the
    default set is the whole of what it needs.
    """

    def get(self, session_id: str) -> SeatRow | None: ...

    def list(self) -> Sequence[SeatRow]: ...


def _seat_claimants[SeatRowT: SeatRow](
    rows: Sequence[SeatRowT],
    *,
    document: TaskDocumentRef,
    role: str,
    replacement: bool,
) -> list[SeatRowT]:
    return [
        row
        for row in rows
        if row.status == "running"
        and row.binding_role == role
        and (
            row.replacement_for_task_document_ref == document
            if replacement
            else row.task_document_ref == document
        )
    ]


def _one_claimant[SeatRowT: SeatRow](
    claimants: Sequence[SeatRowT],
    *,
    ambiguity: str,
) -> SeatRowT | None:
    if len(claimants) > 1:
        raise SeatOccupancyError(ambiguity)
    return claimants[0] if claimants else None


def current_seat_occupant[SeatRowT: SeatRow](
    rows: Sequence[SeatRowT],
    *,
    document: TaskDocumentRef,
    role: str,
) -> SeatRowT | None:
    """Resolve one canonical seat occupant, preferring its incumbent over one staged heir.

    ``replacement_for_task_document_ref`` is the same structural address in a staged state,
    never a second namespace.  One incumbent and one staged heir may coexist while a handover is
    prepared; only the incumbent is current until it leaves.  Multiple incumbents or multiple
    heirs are corrupt/ambiguous and fail closed even when the other side would otherwise win.
    """

    primary = _one_claimant(
        _seat_claimants(rows, document=document, role=role, replacement=False),
        ambiguity=f"multiple running occupants claim {document.key} as {role}",
    )
    replacement = _one_claimant(
        _seat_claimants(rows, document=document, role=role, replacement=True),
        ambiguity=f"multiple running replacements claim {document.key} as {role}",
    )
    return primary if primary is not None else replacement
