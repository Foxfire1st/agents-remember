"""Which history row may govern a record or family the leaf changed.

While a leaf has changed an invariant's revision or a family's guarantee, the leaf's governing row
about it must keep that change visible: the ``changed`` row of the change, or the row that retires
the record. A later row of another disposition would replace that judgment and hide the change, so
the routes and the validator refuse it.
"""

from __future__ import annotations

from agents_remember.models.knowledge_files.history import FamilyRow, HistoryRow, InvariantRow


def keeps_change_visible(row: InvariantRow | FamilyRow) -> bool:
    """Whether ``row`` may govern a record its leaf changed: the ``changed`` row of that change, or
    the row that retires the record (``deleted`` for an invariant, ``retired`` for a family)."""

    return row.disposition in {"changed", "deleted", "retired"}


def changed_record_row_violation(
    row: InvariantRow, *, base_revision: int | None, candidate_revision: int | None
) -> str | None:
    """Return why ``row`` cannot govern an invariant whose revision the leaf changed, or ``None``.

    ``base_revision`` is the invariant's revision on the parent line (the leaf's K_B) and
    ``candidate_revision`` its revision in K_C. While the two differ, the leaf changed the
    invariant's meaning, and the leaf's governing row about it -- the latest across its attempt
    files -- is the ``changed`` row of that change (or the ``deleted`` row that retires it). A
    later ``no_impact``, ``moved`` or ``extended`` row would replace that judgment and hide the
    change, so it is refused at the routes. The writer's own form of the rule is
    :func:`~agents_remember.models.knowledge_files.history_writer_checks.invariant_revision_violation`, against
    its base.
    """

    if base_revision is None or candidate_revision is None or base_revision == candidate_revision:
        return None
    if keeps_change_visible(row):
        return None
    return (
        f"{_a_row(row)} governs an invariant this leaf changed (revision "
        f"{base_revision} on the parent line, {candidate_revision} in K_C); the governing row of "
        "a changed record is its changed row"
    )


def changed_family_row_violation(row: FamilyRow, *, guarantee_changed: bool) -> str | None:
    """Return why ``row`` cannot govern a family whose guarantee the leaf changed, or ``None``.

    A family has one governing row, the leaf's latest about it (the route rule reads "the latest
    family row"). ``guarantee_changed`` says that K_C states another guarantee than the
    parent line (the leaf's K_B). While it does, the governing row is the ``changed`` row of that
    change (or the ``retired`` row that retires the family): a ``no_impact``, ``assigned`` or
    ``rerouted`` row would replace that judgment. Route work is recorded under the ``changed``
    row, which answers the family's route conditions too.
    """

    if not guarantee_changed or keeps_change_visible(row):
        return None
    return (
        f"{_a_row(row)} governs a family whose guarantee this leaf changed; the governing row of "
        "such a family is its changed row, which answers its route conditions too (MIK-R06 rule 3)"
    )


def _a_row(row: HistoryRow) -> str:
    return f"{'an' if row.disposition[:1] in 'aeiou' else 'a'} {row.disposition} row"
