"""One acyclic-lineage rule, shared by the invariant and the family lineage graphs.

Both lineage graphs are predecessor edges between revisions of one object, and both refuse a
write that would leave *any* revision of that object on a cycle. The rule therefore lives here
once and is applied twice, instead of being re-derived per relation:

* ``create_invariant_revision`` applies it over the stored ``invariant_predecessor`` edges;
* ``create_family_revision`` applies it over the stored ``family_predecessor`` edges.

The reach is deliberately wider than adjacency. A candidate is refused when inserting it would
leave it on a cycle, **or** when a retained revision reachable from it through predecessors is
already on one. The second branch exists because a stored cycle is a fact about the object's past
that a new successor must not silently inherit.

Raw cyclic state can only arise outside these operations: admission requires every declared
predecessor to exist already and refuses a self-referencing payload, so no operation can build a
cycle. That is why the membership query reports an empty set for a revision that merely descends
from a cycle while the write path still refuses it.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import apsw

LineageEdge = tuple[str, str]

_INVARIANT_EDGES_SQL = """
SELECT child_revision_id, parent_revision_id FROM invariant_predecessor
WHERE repository_id = ? AND invariant_id = ?
"""

_FAMILY_EDGES_SQL = """
SELECT child_revision_id, parent_revision_id FROM family_predecessor
WHERE repository_id = ? AND family_id = ?
"""


@dataclass(frozen=True)
class CycleFinding:
    """The cycle a candidate revision would leave in its lineage, if any.

    ``members`` are the revisions the refusal must name -- every vertex of the post-insert graph
    that lies on a cycle when the candidate itself would be on one, and only the cycle vertices
    the candidate reaches when it would sit below a stored cycle. ``candidate_on_cycle`` says
    which of the two branches applied, because the two remedies differ.
    """

    members: tuple[str, ...]
    candidate_on_cycle: bool


def invariant_edges(
    connection: apsw.Connection, repository_id: str, invariant_id: str
) -> tuple[LineageEdge, ...]:
    """Return the declared predecessor edges of one invariant."""

    return _edges(connection, _INVARIANT_EDGES_SQL, (repository_id, invariant_id))


def family_edges(
    connection: apsw.Connection, repository_id: str, family_id: str
) -> tuple[LineageEdge, ...]:
    """Return the declared predecessor edges of one family."""

    return _edges(connection, _FAMILY_EDGES_SQL, (repository_id, family_id))


def find_cycle(
    *,
    candidate_id: str,
    predecessors: Iterable[str],
    edges: Iterable[LineageEdge],
) -> CycleFinding | None:
    """Return the cycle inserting ``candidate_id`` would leave, or ``None`` when acyclic."""

    graph = post_insert_graph(edges, candidate_id, predecessors)
    cycle = cycle_vertices(graph)
    if candidate_id in cycle:
        return CycleFinding(members=tuple(sorted(cycle)), candidate_on_cycle=True)
    touched = descendants(candidate_id, graph) & cycle
    if touched:
        return CycleFinding(members=tuple(sorted(touched)), candidate_on_cycle=False)
    return None


def edges_on_cycle(
    *,
    candidate_id: str,
    predecessors: Iterable[str],
    edges: Iterable[LineageEdge],
) -> tuple[str, ...]:
    """Return the edges on a cycle through ``candidate_id``, as their child endpoints.

    This is the membership query, not the write-path rule. Only edges *on* a cycle through the
    revision are members; a revision that descends from a stored cycle without being on it has no
    members here, because no cycle passes through it -- the write path refuses it for a different
    reason, and says so in its own words.
    """

    graph = post_insert_graph(edges, candidate_id, predecessors)
    cycle = cycle_vertices(graph)
    if candidate_id not in cycle:
        return ()
    return tuple(
        sorted(child for child, parents in graph.items() if child in cycle and parents & cycle)
    )


def post_insert_graph(
    edges: Iterable[LineageEdge], candidate_id: str, predecessors: Iterable[str]
) -> dict[str, set[str]]:
    """Return the lineage graph as it would stand after inserting one candidate revision."""

    graph: dict[str, set[str]] = {}
    for child, parent in edges:
        graph.setdefault(child, set()).add(parent)
        graph.setdefault(parent, set())
    graph.setdefault(candidate_id, set())
    for parent in predecessors:
        graph[candidate_id].add(parent)
        graph.setdefault(parent, set())
    return graph


def cycle_vertices(graph: dict[str, set[str]]) -> frozenset[str]:
    """Return every vertex of ``graph`` that lies on a cycle.

    A vertex lies on a cycle exactly when it belongs to a strongly connected component of more
    than one vertex, or has an edge to itself. The components are found with Tarjan's algorithm,
    kept in :class:`_CycleScan` so the traversal carries one object rather than a dozen parallel
    maps. It is written here rather than as a recursive SQL walk because SQLite's ``UNION``
    deduplication changes which rows a recursive CTE revisits, so a traversal written that way
    does not close a cycle.
    """

    scan = _CycleScan(graph)
    found: set[str] = set()
    for component in scan.components():
        if len(component) > 1:
            found.update(component)
            continue
        member = next(iter(component))
        if member in graph.get(member, ()):
            found.add(member)
    return frozenset(found)


def descendants(origin: str, graph: dict[str, set[str]]) -> frozenset[str]:
    """Transitive closure of ``graph`` from ``origin``, excluding ``origin`` itself."""

    reachable: set[str] = set()
    pending = [origin]
    while pending:
        for successor in graph.get(pending.pop(), ()):
            if successor == origin or successor in reachable:
                continue
            reachable.add(successor)
            pending.append(successor)
    return frozenset(reachable)


def _edges(
    connection: apsw.Connection, statement: str, parameters: tuple[str, str]
) -> tuple[LineageEdge, ...]:
    return tuple((str(row[0]), str(row[1])) for row in connection.execute(statement, parameters))


class _CycleScan:
    """One Tarjan strongly-connected-component scan over a lineage graph.

    The traversal keeps its own work stack rather than recursing, so a long lineage chain does not
    put the interpreter's recursion limit in the middle of a write.
    """

    def __init__(self, graph: dict[str, set[str]]) -> None:
        self._graph = graph
        self._index: dict[str, int] = {}
        self._low_link: dict[str, int] = {}
        self._component_stack: list[str] = []
        self._on_stack: set[str] = set()
        self._counter = 0
        self._components: list[tuple[str, ...]] = []
        self._pending: list[tuple[str, Iterator[str]]] = []

    def components(self) -> list[tuple[str, ...]]:
        """Return every component, each with its members in pop order."""

        for root in sorted(self._graph):
            if root in self._index:
                continue
            self._open(root)
            self._pending.append((root, iter(sorted(self._graph[root]))))
            self._drain()
        return self._components

    def _open(self, vertex: str) -> None:
        self._index[vertex] = self._low_link[vertex] = self._counter
        self._counter += 1
        self._component_stack.append(vertex)
        self._on_stack.add(vertex)

    def _drain(self) -> None:
        while self._pending:
            vertex, successors = self._pending[-1]
            successor = self._next_unvisited(successors)
            if successor is not None:
                self._open(successor)
                self._pending.append((successor, iter(sorted(self._graph.get(successor, ())))))
                continue
            self._close(vertex)

    def _next_unvisited(self, successors: Iterator[str]) -> str | None:
        for successor in successors:
            if successor not in self._index:
                return successor
        return None

    def _close(self, vertex: str) -> None:
        self._pending.pop()
        for successor in self._graph.get(vertex, ()):
            if successor in self._on_stack:
                self._low_link[vertex] = min(self._low_link[vertex], self._index[successor])
        if self._low_link[vertex] == self._index[vertex]:
            self._components.append(self._pop_component(vertex))
        if self._pending:
            parent = self._pending[-1][0]
            self._low_link[parent] = min(self._low_link[parent], self._low_link[vertex])

    def _pop_component(self, vertex: str) -> tuple[str, ...]:
        component: list[str] = []
        while True:
            member = self._component_stack.pop()
            self._on_stack.discard(member)
            component.append(member)
            if member == vertex:
                return tuple(component)
