"""The third edge source: the repository's recorded family-composition edges.

:mod:`agents_remember.memory.knowledge.lineage` owns the one acyclic-lineage rule, and its own two
statements are per-object -- one filtered by ``invariant_id``, one by ``family_id``. The composition
graph is not per-object: it relates *different* family revisions inside one repository namespace, so
it needs a third statement, scoped by ``repository_id`` alone, that feeds the same generic functions.

That statement is the only new graph-specific code the composition cycle check requires, and it
lives here rather than anywhere else so that a reader comparing the three graphs sees all three edge
sources in one place, all three feeding ``find_cycle``, ``declared_cycle`` and ``cycle_vertices``.

**Why not a second cycle rule.** ``families.py`` states the guarantee the substrate makes about its
first two graphs -- the acyclic rule, its two branches and its reach "cannot drift between the two
graphs" -- and ``batch_preconditions.py`` states the boundary that keeps it true ("Keeping the walk
on that side is what stops a second cycle rule growing beside the first"). A composition check
written here would make drift possible between three graphs instead of two, and it is forbidden by
``KS-R17@v1`` §4.1/§4.2. This module therefore supplies *edges* and nothing that decides.
"""

from __future__ import annotations

import apsw

from agents_remember.memory.knowledge.lineage import LineageEdge

# The composition graph's edge statement. ``from`` -> ``to`` is copied into the ``(child, parent)``
# shape the shared rule reads, so the walk descends the edge in the direction its author declared.
# The direction a *traversal policy* later chooses is a different question and deliberately does not
# reach this statement: two datasets holding the same edges must agree about whether one of them is
# cyclic, whatever policies their edges carry.
_COMPOSITION_EDGES_SQL = """
SELECT from_family_revision_id, to_family_revision_id FROM family_composition
WHERE repository_id = ?
"""


def composition_edges(connection: apsw.Connection, repository_id: str) -> tuple[LineageEdge, ...]:
    """Return the repository's recorded composition edges, as ``(from, to)`` pairs.

    The edges are read from the composition table *alone*: no read path, projection or detector
    synthesises one, and this function cannot see a label, a path or prose to synthesise one from.
    """

    return tuple(
        (str(row[0]), str(row[1]))
        for row in connection.execute(_COMPOSITION_EDGES_SQL, (repository_id,))
    )


__all__ = ["composition_edges"]
