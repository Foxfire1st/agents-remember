"""The Family view: what one family revision's recorded context is, reported and not traversed.

``Doc13:237`` gives the Family view one job -- report the recorded members and composition links --
and ``KS-R17@v1`` §7.2 and §8 fix its boundary exactly: it reports, it does not traverse, and it
reports no relationship that is not stored.

Four properties are the whole of it, and each is a refusal to do something a reader might otherwise
find convenient:

* **Only stored facts.** A missing composition link, a missing owning route and a missing explanatory
  context are reported as absent. Nothing here infers a relationship from a display label, a folder
  or path ancestry, a prefix, a symbol, a shared member, a shared anchor, or a prose sentence -- the
  three inferences ``Doc13:89`` forbids and the fourth this leaf applies to the route. A revision
  whose members all live under one route's path and which records no owning route is reported
  **ungoverned**, not governed by that route.
* **No derived summary read as a fact.** The view never presents a computed set of "related families"
  as a recorded relationship, and it carries no reachable set at all: the one value that could be
  mistaken for a traversal's result -- the declared depth bound -- travels as *the policy's declared
  bound*, labelled as a declaration, beside the links it declares.
* **Derived output, not a second authority.** The view is regenerable at will from the canonical
  rows and is not a graph authority (``Doc13:112``; ``retrieval-review-design.md:133``). It writes
  nothing, and every statement it runs is a ``SELECT`` on the caller's connection.
* **The policy version is visible wherever a link is reported.** ``Doc13:227`` makes completion
  reportable only relative to the snapshot, graph, traversal policy and extractors that were used,
  so a link reported without the policy version it was declared under would be unreadable against
  another version of the same policy.

The composition table is deliberately **not** consulted for selection anywhere: ``KS-R07@v1``'s
selected set, counts, ordering, revision groups, advertised frontier and manifest digest are
unchanged by this leaf, and the retrieval read does not import this module.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agents_remember.memory.knowledge import families
from agents_remember.memory.knowledge.read_queries import (
    fetch_composition_rows,
    fetch_contexts_for_revisions,
    fetch_owning_routes_for_revisions,
)
from agents_remember.memory.knowledge.refusals import (
    missing_expected_row_refusal,
)
from agents_remember.models.knowledge.composition import FamilyCompositionLink
from agents_remember.models.knowledge.family import FamilyRevision
from agents_remember.models.knowledge.result import KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The one operation name the projection carries when it must refuse. It reads and reports, so the
# only refusal it can return is "there is no such family revision here" -- and it returns the
# shipped ``missing_expected_row`` rather than inventing a code for a fact one already names.
VIEW_OPERATION = "read_knowledge_scope"


@dataclass(frozen=True)
class ReportedContext:
    """One family revision's authored explanatory context, exactly as it is stored.

    ``revision_id`` is the exact context revision the text belongs to, so a caller holds what it
    read: superseding context never retires the earlier text under the earlier dataset.
    """

    context_id: str
    revision_id: str
    predecessor_revision_id: str
    body: str
    provenance: Mapping[str, Any]

    @property
    def is_first_revision(self) -> bool:
        """Whether this is the context's first revision (it names itself as its predecessor)."""

        return self.predecessor_revision_id == self.revision_id


@dataclass(frozen=True)
class FamilyRevisionView:
    """Everything the Family view reports about one family revision, and nothing more.

    ``owning_route_id`` is ``None`` for the explicit ungoverned state -- never a default route.
    ``context`` is ``None`` when no context is recorded, and ``composition_links`` is empty when no
    edge relates this revision to another, which is reported as *absent composition* rather than as
    a derived relationship.
    """

    repository_id: str
    family_id: str
    revision_id: str
    display_version: str
    owning_route_id: str | None
    context: ReportedContext | None
    composition_links: tuple[FamilyCompositionLink, ...]

    @property
    def governed(self) -> bool:
        """Whether this revision records a canonical owning route."""

        return self.owning_route_id is not None


def family_revision_view(
    store: OpenedKnowledgeStore, family_revision_id: str
) -> FamilyRevisionView | KnowledgeRefusal:
    """Return the Family view of one exact family revision, or the refusal that replaced it.

    The view is built from the canonical rows of one namespace in one read, so it observes a single
    snapshot. It never fills a gap: a revision that records no owning route reports ungoverned, and
    a revision that records no context or no link reports that absence.
    """

    revision = _stored_revision(store, family_revision_id)
    if revision is None:
        return missing_expected_row_refusal(
            operation=VIEW_OPERATION, table="family_revision", record_id=family_revision_id
        )
    routes = fetch_owning_routes_for_revisions(
        store.connection, store.repository_id, [family_revision_id]
    )
    contexts = fetch_contexts_for_revisions(
        store.connection, store.repository_id, [family_revision_id]
    )
    return FamilyRevisionView(
        repository_id=store.repository_id,
        family_id=revision.family_id,
        revision_id=revision.revision_id,
        display_version=revision.display_version,
        owning_route_id=routes.get(family_revision_id),
        context=None if not contexts else _context_of(contexts[0]),
        composition_links=composition_links_for(
            store, family_revision_id, family_id=revision.family_id
        ),
    )


def composition_links_for(
    store: OpenedKnowledgeStore, family_revision_id: str, *, family_id: str
) -> tuple[FamilyCompositionLink, ...]:
    """Return this revision's recorded composition links, each labelled with its direction.

    The rows come from one declared-order query over the composition table, and the direction is
    computed *from the stored endpoints* -- ``outgoing`` when this revision is the edge's ``from``
    endpoint and ``incoming`` when it is the ``to`` endpoint. Nothing is traversed: a link that
    points at another revision reports that revision's identity and stops there, and no link is
    synthesised for a revision whose row does not exist.
    """

    del family_id
    rows = fetch_composition_rows(store.connection, store.repository_id, [family_revision_id])
    return tuple(_link_of(row, family_revision_id) for row in rows)


def _link_of(row: Mapping[str, Any], family_revision_id: str) -> FamilyCompositionLink:
    from_family_revision_id = str(row["from_family_revision_id"])
    return FamilyCompositionLink(
        composition_id=str(row["composition_id"]),
        direction="outgoing" if from_family_revision_id == family_revision_id else "incoming",
        from_family_revision_id=from_family_revision_id,
        to_family_revision_id=str(row["to_family_revision_id"]),
        policy_id=None if row["policy_id"] is None else str(row["policy_id"]),
        policy_version_id=(
            None if row["policy_version_id"] is None else str(row["policy_version_id"])
        ),
        declared_version=(
            None if row["declared_version"] is None else str(row["declared_version"])
        ),
        widened_scope=None if row["widened_scope"] is None else str(row["widened_scope"]),
        depth_bound=None if row["depth_bound"] is None else int(row["depth_bound"]),
        provenance=row["provenance"],
    )


def _context_of(row: Mapping[str, Any]) -> ReportedContext:
    return ReportedContext(
        context_id=str(row["context_id"]),
        revision_id=str(row["revision_id"]),
        predecessor_revision_id=str(row["predecessor_revision_id"]),
        body=str(row["body"]),
        provenance=row["provenance"],
    )


def _stored_revision(store: OpenedKnowledgeStore, family_revision_id: str) -> FamilyRevision | None:
    """Return the sealed family revision, decoded, or ``None``.

    The aggregate is decoded rather than read column by column so its payload seal is verified on
    the way out, exactly as every other family read does: a view served from a row that does not
    match its own identity would be a view of something the substrate does not store.
    """

    stored = families.get_family_revision(store, family_revision_id)
    return None if stored is None else stored.revision


__all__ = [
    "VIEW_OPERATION",
    "FamilyRevisionView",
    "ReportedContext",
    "composition_links_for",
    "family_revision_view",
]
