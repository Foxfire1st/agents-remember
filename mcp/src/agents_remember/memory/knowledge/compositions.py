"""Family composition: the authored edge, its declared policy, and the traversal it permits.

Four acts live here, and the split is the whole safety property of ``KS-R17@v1``:

* **Authoring a declared policy version.** :func:`insert_policy_version` writes an immutable,
  versioned policy row inside a caller's open transaction. The policy is a row rather than two text
  columns on the edge precisely so that "which version was executed" is answerable and so that
  identity-without-version is not a representable state.
* **Authoring an edge.** :func:`insert_composition` and :func:`insert_family_revision_route` write
  rows inside a caller's open transaction, refusing a missing endpoint by name before any row is
  written. The endpoints are family revisions, and the declared foreign keys make a wrong endpoint
  kind unrepresentable rather than merely rejected (``memberships.py``'s doctrine, applied to the
  third authored graph).
* **Reporting what is stored.** :func:`get_composition`, :func:`composition_links_of_revision`,
  :func:`owning_route_of_family_revision` and :func:`context_of_family_revision` answer what the
  Family projection reports. Nothing here fills a missing link, route or context with an inferred
  value, and there is deliberately **no** function in this module that derives a composition from a
  label, a path, a prefix, a member or prose.
* **Following declared edges under a declared policy.** :func:`follow_composition_scope` is the
  read-side successor of requirement 7.3: it starts at one family revision, follows only edges whose
  declared policy admits the step, respects the policy's finite depth bound, and reports the policy
  identity and version it executed under. It is a different operation from
  ``read_knowledge_scope``'s retrieval selection and it does not widen it.

**Why the traversal is a separate operation and not a flag on the shipped read.** ``KS-R07@v1``'s
selection policy and its advertised frontier are unchanged in this leaf, and the shipped read does
not consult the composition table at all for selection. Composition context reaches a caller through
the Family projection, which reports and never traverses. The traversal below exists only where §8
registered-review-scope construction asks for a scope under a named policy, which ``L16`` owns; this
module supplies the policy's declaration, its recorded shape and these semantics.

**Why there is no second cycle rule.** The composition graph's cycle check is the shipped shared
rule in :mod:`agents_remember.memory.knowledge.lineage`. This module contributes the third *edge
source* -- :func:`lineages.composition_edges`, which is scoped by ``repository_id`` alone because
the composition graph spans family identities -- and never a walk of its own.

**Why an edge is never deleted.** Requirement 2.2 makes a composition edge immutable in the same
sense a revision row is: no ``UPDATE`` and no ``DELETE`` of a composition row through a candidate
write, and a correction is a new edge with its own identity. There is therefore no removal step in
this module and none in the command union -- the substrate's own trigger refuses a delete at the
database, and a caller that wants to correct a relationship authors the edge it means. That is a
weaker operation set than ``family_member``'s removal pair on purpose: ``memberships.py``'s removal
is addressed by identity *and* the digest of the row the caller expects, and this leaf adds no
removal at all rather than adding a second, weaker one.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.memory.knowledge import families
from agents_remember.memory.knowledge.composition_policies import require_declared_policy
from agents_remember.memory.knowledge.connection import fetch_one
from agents_remember.memory.knowledge.endpoints import (
    require_family_revision_endpoint,
    require_route_endpoint,
)
from agents_remember.memory.knowledge.records import decode_authorship, encode_authorship
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    RefusalFacts,
    refusal,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.composition import (
    FamilyComposition,
    FamilyCompositionDraft,
    FamilyCompositionLink,
    FamilyExplanationContext,
)
from agents_remember.models.knowledge.result import KnowledgeOperation

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# The operation name every refusal raised by an *authored act* here carries when it is reached
# through the in-transaction step. A candidate batch restates it under ``change_candidate`` and
# names the command and its position, which is what its caller submitted.
_AUTHORING_OPERATION: KnowledgeOperation = "change_candidate"

# The declared traversal policy, its version set and its one registered identity are declared in
# :mod:`…composition_policies`; this module consumes them. Nothing here resolves a policy to a
# default, and nothing here decides whether a policy is well formed.

COMPOSITION_COLUMNS = (
    "repository_id, composition_id, from_family_revision_id, to_family_revision_id, policy_id, "
    "policy_version_id, provenance"
)

_COMPOSITION_INSERT = (
    "INSERT INTO family_composition "
    "(repository_id, composition_id, from_family_revision_id, to_family_revision_id, policy_id, "
    "policy_version_id, provenance) VALUES (?, ?, ?, ?, ?, ?, ?)"
)

_CONTEXT_INSERT = (
    "INSERT INTO family_revision_context "
    "(repository_id, context_id, family_id, family_revision_id, current_revision_id, provenance) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

_CONTEXT_REVISION_INSERT = (
    "INSERT INTO family_revision_context_revision "
    "(repository_id, context_id, revision_id, predecessor_revision_id, body, provenance) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

_ROUTE_INSERT = (
    "INSERT INTO family_revision_route "
    "(repository_id, family_revision_id, route_id, provenance) VALUES (?, ?, ?, ?)"
)


# ---------------------------------------------------------------------------
# The authored edge.


def insert_composition(store: OpenedKnowledgeStore, composition: FamilyComposition) -> None:
    """Insert one authored composition edge inside the caller's open transaction.

    Every declared reference is checked before the row is written, in this order, and each refusal
    names the offending identity: both endpoints must be stored family revisions of this namespace,
    and a declared policy must be a declared version of a declared identity. An edge with no
    declared policy is stored -- it is authored and readable -- and is simply not traversable.

    A second row for the same pair under the same policy identity is refused rather than stored:
    the declared unique tuple makes one authored relationship between one pair of exact revisions
    under one policy a single fact.
    """

    if composition.repository_id != store.repository_id:
        raise KnowledgeRefused(
            refusal(
                "unauthorized_scope",
                _AUTHORING_OPERATION,
                "the composition edge names a repository namespace this store is not bound to",
                facts=RefusalFacts(
                    table="family_composition",
                    record_id=composition.composition_id,
                    expected=store.repository_id,
                    observed=composition.repository_id,
                ),
                next_action="Author the edge in the namespace the store is bound to.",
            )
        )
    existing = get_composition(store, composition.composition_id)
    if existing is not None:
        if existing == composition:
            return
        raise KnowledgeRefused(
            refusal(
                "duplicate_identity",
                _AUTHORING_OPERATION,
                "the composition identity is already stored with different endpoints or policy, and "
                "an authored composition edge is immutable",
                facts=RefusalFacts(
                    table="family_composition",
                    record_id=composition.composition_id,
                    expected=_edge_fingerprint(existing),
                    observed=_edge_fingerprint(composition),
                ),
                next_action=(
                    "Author a corrected relationship as a new edge with its own identity; an "
                    "authored edge is never repointed in place."
                ),
            )
        )
    require_family_revision_endpoint(
        store, composition.from_family_revision_id, composition.composition_id, "create_composition"
    )
    require_family_revision_endpoint(
        store, composition.to_family_revision_id, composition.composition_id, "create_composition"
    )
    if composition.policy_id is not None and composition.policy_version_id is not None:
        require_declared_policy(store, composition.policy_id, composition.policy_version_id)
    duplicate = find_composition_by_pair(
        store,
        composition.from_family_revision_id,
        composition.to_family_revision_id,
        composition.policy_id,
        composition.policy_version_id,
    )
    if duplicate is not None:
        raise KnowledgeRefused(
            refusal(
                "relationship_constraint",
                _AUTHORING_OPERATION,
                "this pair of family revisions is already related by a stored composition edge "
                "under this policy identity",
                facts=RefusalFacts(
                    table="family_composition",
                    record_id=composition.composition_id,
                    expected=duplicate.composition_id,
                    observed=composition.composition_id,
                ),
                next_action=(
                    "Read the stored edge and cite it; one authored relationship between one pair "
                    "of exact revisions under one policy is stored once."
                ),
            )
        )
    store.write(
        _COMPOSITION_INSERT,
        (
            store.repository_id,
            composition.composition_id,
            composition.from_family_revision_id,
            composition.to_family_revision_id,
            composition.policy_id,
            composition.policy_version_id,
            _provenance_text(composition.provenance),
        ),
    )


def composition_draft_of(draft: FamilyCompositionDraft, repository_id: str) -> FamilyComposition:
    """Bind one authored draft to the namespace it is being stored in."""

    return FamilyComposition(
        repository_id=repository_id,
        composition_id=draft.composition_id,
        from_family_revision_id=draft.from_family_revision_id,
        to_family_revision_id=draft.to_family_revision_id,
        policy_id=draft.policy_id,
        policy_version_id=draft.policy_version_id,
        provenance=draft.provenance,
    )


def get_composition(store: OpenedKnowledgeStore, composition_id: str) -> FamilyComposition | None:
    """Return one stored composition edge, or ``None`` when it is not in this namespace."""

    row = fetch_one(
        store.connection,
        f"SELECT {COMPOSITION_COLUMNS} FROM family_composition "
        "WHERE repository_id = ? AND composition_id = ?",
        (store.repository_id, composition_id),
    )
    return None if row is None else composition_of(row)


def find_composition_by_pair(
    store: OpenedKnowledgeStore,
    from_family_revision_id: str,
    to_family_revision_id: str,
    policy_id: str | None,
    policy_version_id: str | None = None,
) -> FamilyComposition | None:
    """Return the one edge relating this pair under this declared policy version, or ``None``.

    Identity and version travel together, exactly as they do on the edge: the pair of them is what
    the declared unique tuple names. Both are compared with SQLite's ``IS`` semantics through the
    ``IS ?`` predicate, so an undeclared policy (``NULL``) matches an undeclared policy and only
    that, rather than matching every row the way ``= NULL`` would.

    ``policy_version_id`` defaults to ``None`` so a caller asking "is there a *bare* edge between
    these two revisions" -- the question the duplicate check asks for an edge that declares no
    policy -- states exactly that and does not have to spell out two absences.
    """

    row = fetch_one(
        store.connection,
        f"SELECT {COMPOSITION_COLUMNS} FROM family_composition "
        "WHERE repository_id = ? AND from_family_revision_id = ? AND to_family_revision_id = ? "
        "AND policy_id IS ? AND policy_version_id IS ?",
        (
            store.repository_id,
            from_family_revision_id,
            to_family_revision_id,
            policy_id,
            policy_version_id,
        ),
    )
    return None if row is None else composition_of(row)


def composition_links_of_revision(
    store: OpenedKnowledgeStore, family_revision_id: str
) -> tuple[FamilyCompositionLink, ...]:
    """Return every recorded composition link one family revision participates in.

    Both directions are returned -- the edges that start at this revision and the edges that end at
    it -- each labelled with the direction relative to the revision asked about, because a caller
    that only saw one direction could not tell an authored successor from an authored predecessor.
    The order is the composition identity's, which is the table's declared primary key, so two
    datasets holding the same records report the same links in the same order.

    The declared policy's version spelling, widened scope and bound travel with each link when one
    is declared: a link reported without the version it was declared under would be unreadable
    against another version of the same policy (requirement 3.3, applied to the projection).
    """

    rows = store.connection.execute(
        "SELECT composition.composition_id, composition.from_family_revision_id, "
        "composition.to_family_revision_id, composition.policy_id, composition.policy_version_id, "
        "composition.provenance, version.declared_version, version.widened_scope, "
        "version.depth_bound "
        "FROM family_composition AS composition "
        "LEFT JOIN family_composition_policy_version AS version "
        "ON version.repository_id = composition.repository_id "
        "AND version.policy_id = composition.policy_id "
        "AND version.policy_version_id = composition.policy_version_id "
        "WHERE composition.repository_id = ? "
        "AND (composition.from_family_revision_id = ? OR composition.to_family_revision_id = ?) "
        "ORDER BY composition.composition_id",
        (store.repository_id, family_revision_id, family_revision_id),
    )
    links: list[FamilyCompositionLink] = []
    for row in rows:
        from_revision_id = str(row[1])
        links.append(
            FamilyCompositionLink(
                composition_id=str(row[0]),
                direction="outgoing" if from_revision_id == family_revision_id else "incoming",
                from_family_revision_id=from_revision_id,
                to_family_revision_id=str(row[2]),
                policy_id=None if row[3] is None else str(row[3]),
                policy_version_id=None if row[4] is None else str(row[4]),
                declared_version=None if row[6] is None else str(row[6]),
                widened_scope=None if row[7] is None else str(row[7]),
                depth_bound=None if row[8] is None else int(row[8]),
                provenance=_decode_provenance(row[5]),
            )
        )
    return tuple(links)


# ---------------------------------------------------------------------------
# The family revision's canonical owning route.


def insert_family_revision_route(
    store: OpenedKnowledgeStore,
    family_revision_id: str,
    route_id: str,
    provenance: Authorship,
) -> None:
    """Record the canonical owning route of one exact family revision.

    Two references are checked before the row is written: the family revision must be stored as a
    family revision here, and the route must be an authored route of this namespace. Nothing is
    derived -- a route is never inferred from a member's anchor, from a path a realization cites,
    from a display label or from the deepest common path prefix of anything -- and the table's
    primary key makes "at most one canonical owning route per family revision" a constraint rather
    than a convention.

    Re-recording the same association is idempotent; naming a *different* route for an already
    governed revision is refused rather than silently overwritten, exactly as
    ``routes.set_governing_route`` refuses it for a governed identity.
    """

    existing = owning_route_of_family_revision(store, family_revision_id)
    if existing is not None:
        if existing == route_id:
            return
        raise KnowledgeRefused(
            refusal(
                "relationship_constraint",
                _AUTHORING_OPERATION,
                "this family revision already records a different canonical owning route, and a "
                "family revision names at most one",
                facts=RefusalFacts(
                    table="family_revision_route",
                    record_id=family_revision_id,
                    expected=existing,
                    observed=route_id,
                ),
                next_action=(
                    "Read the recorded owning route; a revision-level owner that differs from the "
                    "family identity's own route is a separately recorded fact, never a silent "
                    "reconciliation."
                ),
            )
        )
    require_family_revision_endpoint(
        store, family_revision_id, route_id, "set_family_revision_route"
    )
    require_route_endpoint(store, route_id, family_revision_id, "set_family_revision_route")
    store.write(
        _ROUTE_INSERT,
        (
            store.repository_id,
            family_revision_id,
            route_id,
            _provenance_text(provenance),
        ),
    )


def owning_route_of_family_revision(
    store: OpenedKnowledgeStore, family_revision_id: str
) -> str | None:
    """Return the family revision's recorded canonical owning route, or ``None`` when ungoverned.

    ``None`` is the explicit **ungoverned** state, not a default: it is never the repository root,
    never the family identity's own route, and never a route inferred from the revision's members.
    A caller that must tell "ungoverned" from "not a stored revision" asks
    :func:`families.family_id_of_revision` itself, exactly as ``routes.find_governing_route``
    documents for its own two questions.
    """

    row = fetch_one(
        store.connection,
        "SELECT route_id FROM family_revision_route "
        "WHERE repository_id = ? AND family_revision_id = ?",
        (store.repository_id, family_revision_id),
    )
    return None if row is None else str(row[0])


# ---------------------------------------------------------------------------
# The authored explanatory context.


def insert_context_revision(
    store: OpenedKnowledgeStore,
    context: FamilyExplanationContext,
) -> None:
    """Insert one authored explanatory-context revision, and its record when it is the first.

    A change is a newly identified context revision with its own provenance, never an in-place edit:
    the first revision creates the context record and is the record's current designation, and each
    later revision names its exact predecessor and becomes the designation. The earlier text stays
    stored, so a dataset that read the earlier revision keeps reading it.

    The context is bound to the exact family revision, through the family build's own declared keys,
    so it cannot be attached to a revision of a statement identity it does not belong to. Nothing
    here reads or writes ``joint_guarantee``: a context explains and never carries an obligation,
    and this function has no path to the guarantee column at all.
    """

    existing = get_context_revision(store, context.context_id, context.revision_id)
    if existing is not None:
        raise KnowledgeRefused(
            refusal(
                "duplicate_identity",
                _AUTHORING_OPERATION,
                "this context revision identity is already stored, and a sealed context revision "
                "cannot be rewritten",
                facts=RefusalFacts(
                    table="family_revision_context_revision",
                    record_id=context.revision_id,
                    expected=existing.body,
                    observed=context.body,
                ),
                next_action="Author the change as a new revision naming this one as its predecessor.",
            )
        )
    subject = families.get_family_revision(store, context.family_revision_id)
    if subject is None or subject.revision.family_id != context.family_id:
        raise KnowledgeRefused(
            refusal(
                "missing_expected_row",
                _AUTHORING_OPERATION,
                "the family revision this context explains is not stored in this namespace, or the "
                "family identity it names is not the revision's own",
                facts=RefusalFacts(
                    table="family_revision",
                    record_id=context.family_revision_id,
                    expected=context.family_id,
                    observed="<absent>" if subject is None else subject.revision.family_id,
                ),
                next_action=(
                    "Name a stored family revision and the family it belongs to; explanatory context "
                    "is authored against an exact revision."
                ),
            )
        )
    record = get_context_record(store, context.context_id)
    if record is None:
        if not context.is_first_revision:
            raise KnowledgeRefused(
                refusal(
                    "missing_expected_row",
                    _AUTHORING_OPERATION,
                    "this context revision names a predecessor, but no context record exists yet",
                    facts=RefusalFacts(
                        table="family_revision_context",
                        record_id=context.context_id,
                        expected="a stored context record",
                        observed="<absent>",
                    ),
                    next_action="Author the context's first revision without a predecessor.",
                )
            )
        if context_id_of_family_revision(store, context.family_revision_id) is not None:
            raise KnowledgeRefused(
                refusal(
                    "relationship_constraint",
                    _AUTHORING_OPERATION,
                    "this family revision already carries an authored explanatory context",
                    facts=RefusalFacts(
                        table="family_revision_context",
                        record_id=context.family_revision_id,
                        expected=context_id_of_family_revision(store, context.family_revision_id)
                        or "",
                        observed=context.context_id,
                    ),
                    next_action=(
                        "Author the change as a new revision of the stored context, naming its "
                        "current revision as the predecessor."
                    ),
                )
            )
        store.write(
            _CONTEXT_REVISION_INSERT,
            (
                store.repository_id,
                context.context_id,
                context.revision_id,
                context.revision_id,
                context.body,
                _provenance_text(context.provenance),
            ),
        )
        store.write(
            _CONTEXT_INSERT,
            (
                store.repository_id,
                context.context_id,
                context.family_id,
                context.family_revision_id,
                context.revision_id,
                _provenance_text(context.provenance),
            ),
        )
        return
    if record.family_revision_id != context.family_revision_id:
        raise KnowledgeRefused(
            refusal(
                "relationship_constraint",
                _AUTHORING_OPERATION,
                "this context identity already explains a different family revision",
                facts=RefusalFacts(
                    table="family_revision_context",
                    record_id=context.context_id,
                    expected=record.family_revision_id,
                    observed=context.family_revision_id,
                ),
                next_action="Author a new context identity for the other family revision.",
            )
        )
    if context.is_first_revision:
        raise KnowledgeRefused(
            refusal(
                "invalid_reference",
                _AUTHORING_OPERATION,
                "a successor context revision must name the exact revision it succeeds, and this "
                "one names itself, which is the stored spelling of 'no predecessor'",
                facts=RefusalFacts(
                    table="family_revision_context_revision",
                    record_id=context.revision_id,
                    expected="the current revision identity",
                    observed="<absent>",
                ),
                next_action=(
                    "Name the revision this one succeeds; an explanation's change is a successor "
                    "naming its predecessor rather than a replacement."
                ),
            )
        )
    predecessor = get_context_revision(store, context.context_id, context.predecessor_revision_id)
    if predecessor is None:
        raise KnowledgeRefused(
            refusal(
                "missing_expected_row",
                _AUTHORING_OPERATION,
                "the predecessor this context revision names is not stored for this context",
                facts=RefusalFacts(
                    table="family_revision_context_revision",
                    record_id=context.predecessor_revision_id,
                    expected="a stored revision of this context",
                    observed="<absent>",
                ),
                next_action="Reread the context and name its current revision as the predecessor.",
            )
        )
    store.write(
        _CONTEXT_REVISION_INSERT,
        (
            store.repository_id,
            context.context_id,
            context.revision_id,
            context.predecessor_revision_id,
            context.body,
            _provenance_text(context.provenance),
        ),
    )
    store.write(
        "UPDATE family_revision_context SET current_revision_id = ? "
        "WHERE repository_id = ? AND context_id = ?",
        (context.revision_id, store.repository_id, context.context_id),
    )


def get_context_record(
    store: OpenedKnowledgeStore, context_id: str
) -> FamilyExplanationContext | None:
    """Return the current revision of one context record, or ``None`` when it is not stored."""

    row = fetch_one(
        store.connection,
        "SELECT context.repository_id, context.context_id, context.family_id, "
        "context.family_revision_id, revision.revision_id, revision.predecessor_revision_id, "
        "revision.body, revision.provenance "
        "FROM family_revision_context AS context "
        "JOIN family_revision_context_revision AS revision "
        "ON revision.repository_id = context.repository_id "
        "AND revision.revision_id = context.current_revision_id "
        "WHERE context.repository_id = ? AND context.context_id = ?",
        (store.repository_id, context_id),
    )
    return None if row is None else _context_of(row)


def get_context_revision(
    store: OpenedKnowledgeStore, context_id: str, revision_id: str
) -> FamilyExplanationContext | None:
    """Return one exact stored context revision, or ``None``.

    This is the read a caller uses to hold an earlier revision under an earlier dataset: the
    revision identity is the address, so superseding context never retires the text a caller
    already read.
    """

    row = fetch_one(
        store.connection,
        "SELECT context.repository_id, context.context_id, context.family_id, "
        "context.family_revision_id, revision.revision_id, revision.predecessor_revision_id, "
        "revision.body, revision.provenance "
        "FROM family_revision_context AS context "
        "JOIN family_revision_context_revision AS revision "
        "ON revision.repository_id = context.repository_id AND revision.context_id = context.context_id "
        "WHERE context.repository_id = ? AND context.context_id = ? AND revision.revision_id = ?",
        (store.repository_id, context_id, revision_id),
    )
    return None if row is None else _context_of(row)


def context_of_family_revision(
    store: OpenedKnowledgeStore, family_revision_id: str
) -> FamilyExplanationContext | None:
    """Return the authored explanatory context bound to one exact family revision, or ``None``.

    ``None`` means no context is recorded. It is reported as absent rather than reconstructed from
    the joint guarantee, and the guarantee is never reconstructed from it: the two are separately
    authored claims, and ``storage-design.md``'s rule that an explanation must not be written
    through the statement column binds here in both directions.
    """

    context_id = context_id_of_family_revision(store, family_revision_id)
    if context_id is None:
        return None
    return get_context_record(store, context_id)


def context_id_of_family_revision(
    store: OpenedKnowledgeStore, family_revision_id: str
) -> str | None:
    """Return the context identity bound to one family revision, or ``None``."""

    row = fetch_one(
        store.connection,
        "SELECT context_id FROM family_revision_context "
        "WHERE repository_id = ? AND family_revision_id = ?",
        (store.repository_id, family_revision_id),
    )
    return None if row is None else str(row[0])


def composition_of(row: Sequence[object]) -> FamilyComposition:
    """Decode one stored composition row, in the declared column order.

    Public because the traversal module reads the same rows through the same decoder: one shape for
    one table, so a reader and a writer cannot disagree about what a stored edge is.
    """

    return FamilyComposition(
        repository_id=str(row[0]),
        composition_id=str(row[1]),
        from_family_revision_id=str(row[2]),
        to_family_revision_id=str(row[3]),
        policy_id=None if row[4] is None else str(row[4]),
        policy_version_id=None if row[5] is None else str(row[5]),
        provenance=_decode_provenance(row[6]),
    )


def _context_of(row: Sequence[object]) -> FamilyExplanationContext:
    return FamilyExplanationContext(
        context_id=str(row[1]),
        family_id=str(row[2]),
        family_revision_id=str(row[3]),
        revision_id=str(row[4]),
        predecessor_revision_id=str(row[5]),
        body=str(row[6]),
        provenance=_decode_provenance(row[7]),
    )


def _provenance_text(provenance: Authorship) -> str:
    return encode_authorship(provenance)


def _decode_provenance(value: object) -> Authorship:
    return decode_authorship(str(value))


def composition_row_digest(repository_id: str, composition: FamilyComposition) -> str:
    """Digest one composed row so an expectation or a receipt can name exactly it.

    The digest is computed from the authored value rather than read from a stored column, because a
    composition row deliberately stores no digest of its own: it is not a revision aggregate, it
    mints no content address, and it is not a second identity authority. The shape follows the
    shipped row digests -- the table name first, then every authored field -- so a caller carries
    one value from a read into an expectation the same way it carries a ``row_digest``.
    """

    return sha256_digest(
        {
            "table": "family_composition",
            "repository_id": repository_id,
            "composition_id": composition.composition_id,
            "from_family_revision_id": composition.from_family_revision_id,
            "to_family_revision_id": composition.to_family_revision_id,
            "policy_id": composition.policy_id,
            "policy_version_id": composition.policy_version_id,
            "provenance": composition.provenance.model_dump(mode="json"),
        }
    )


def owning_route_row_digest(repository_id: str, family_revision_id: str, route_id: str) -> str:
    """Digest one recorded owning-route row, so an expectation can name it."""

    return sha256_digest(
        {
            "table": "family_revision_route",
            "repository_id": repository_id,
            "family_revision_id": family_revision_id,
            "route_id": route_id,
        }
    )


def context_row_digest(repository_id: str, context: FamilyExplanationContext) -> str:
    """Digest one context revision row, so an expectation can name exactly it."""

    return sha256_digest(
        {
            "table": "family_revision_context_revision"
            if not context.is_first_revision
            else "family_revision_context",
            "repository_id": repository_id,
            "context_id": context.context_id,
            "family_id": context.family_id,
            "family_revision_id": context.family_revision_id,
            "revision_id": context.revision_id,
            "predecessor_revision_id": context.predecessor_revision_id,
            "body": context.body,
            "provenance": context.provenance.model_dump(mode="json"),
        }
    )


def _edge_fingerprint(edge: FamilyComposition) -> str:
    return (
        f"{edge.from_family_revision_id}->{edge.to_family_revision_id}"
        f"@{edge.policy_id or '<none>'}/{edge.policy_version_id or '<none>'}"
    )


__all__ = [
    "composition_draft_of",
    "composition_links_of_revision",
    "composition_row_digest",
    "context_id_of_family_revision",
    "context_of_family_revision",
    "context_row_digest",
    "find_composition_by_pair",
    "get_composition",
    "get_context_record",
    "get_context_revision",
    "insert_composition",
    "insert_context_revision",
    "insert_family_revision_route",
    "owning_route_of_family_revision",
    "owning_route_row_digest",
]
