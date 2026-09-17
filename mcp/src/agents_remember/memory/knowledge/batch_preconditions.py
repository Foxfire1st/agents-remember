"""Every precondition one candidate change batch must satisfy before it may write anything.

The whole module is *pre*-conditions: it reads, it compares, and it raises
:class:`KnowledgeRefused`. Nothing here writes, which is what makes "a refused batch leaves the
stored dataset unchanged" a property of the code's shape rather than a promise about its order.

Four rules shape the checks:

* **An expectation is a comparison, not a default.** A stated record state that does not match what
  is stored refuses; an identity the caller expected to be absent but that is stored refuses. A
  missing expectation is never read as permission to overwrite whatever is there.
* **An insertion is never an upsert.** A stored identity the caller believed it was creating means
  its read is stale, so the batch refuses and the caller rereads.
* **A command list is one authored act.** The same identity addressed by two commands refuses,
  because nothing says which of the two the author meant.
* **Validation is over the completed graph, not the request order.** A revision may declare any
  revision the batch creates, wherever in the sequence that command appears, because the design
  states the ordering is not the validation axis. What is checked is the *completed* graph: every
  declared predecessor must exist once the batch is applied, and a set of declarations that puts a
  revision on a lineage cycle is refused by name before any row is written.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from agents_remember.memory.knowledge import (
    anchors,
    facets,
    families,
    lineage,
    memberships,
    realizations,
)
from agents_remember.memory.knowledge.candidate_records import (
    inserted_identities,
    pending_identities,
    present,
    stored_record_digest,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    RefusalFacts,
    batch_absent_target_refusal,
    batch_command_refusal,
    batch_duplicate_expectation_refusal,
    batch_lineage_cycle_refusal,
    batch_promotion_not_supported_refusal,
    batch_stale_record_refusal,
    batch_supersession_cycle_refusal,
    cross_family_predecessor_refusal,
    cross_invariant_predecessor_refusal,
    duplicate_family_refusal,
    duplicate_family_revision_refusal,
    duplicate_invariant_refusal,
    duplicate_revision_refusal,
    unknown_family_refusal,
    unknown_invariant_refusal,
)
from agents_remember.models.knowledge.authorship import PROPOSED_STATE
from agents_remember.models.knowledge.candidate import (
    AddExplanationRevision,
    AddFacet,
    AddFamily,
    AddFamilyMember,
    AddFamilyRevision,
    AddInvariant,
    AddInvariantRevision,
    AddRealizationClaim,
    AddSourceAnchor,
    ChangeBatch,
    ChangeCommand,
    ExpectedRecord,
    NewAnchor,
    RemoveFacetAttachment,
    RemoveFamilyMember,
    RemoveRealizationClaim,
    RemoveSourceAnchor,
    SetFamilyLabel,
    SetInvariantLabel,
)
from agents_remember.models.knowledge.facet import (
    AttachFacet,
    AuthorExplanation,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

_AGGREGATE_KINDS = (AddInvariantRevision, AddFamilyRevision)

# The facet commands whose declared references may be satisfied by the batch itself rather than by
# stored rows. A batch validates the *completed* graph, so these cite `pending`, and the apply step
# -- which runs in command order -- sees the earlier command's row already written.
_FACET_REFERENCING_KINDS = (AddFacet, AttachFacet, AuthorExplanation, AddExplanationRevision)


def facet_commands(commands: Sequence[ChangeCommand]) -> tuple[ChangeCommand, ...]:
    """Return the facet commands one batch declares, in order."""

    return tuple(command for command in commands if command.kind in _FACET_KINDS)


_FACET_KINDS = frozenset(
    {
        "add_facet",
        "attach_facet",
        "remove_facet_attachment",
        "author_explanation",
        "add_explanation_revision",
        "designate_explanation",
    }
)


def require_preconditions(store: OpenedKnowledgeStore, batch: ChangeBatch) -> None:
    """Refuse unless every stated expectation and every command is admissible as it stands."""

    require_expected_records(store, batch.expected_records)
    require_distinct_commands(batch.commands)
    require_no_accepted_origin(batch.commands)
    require_facet_generation(store, batch.commands)
    require_insertions_absent(store, batch.commands)
    require_command_targets(store, batch.commands)


def require_expected_records(
    store: OpenedKnowledgeStore, expected_records: Sequence[ExpectedRecord]
) -> None:
    """Refuse unless every stated expectation is exactly what the stored dataset holds."""

    for expected in expected_records:
        observed = stored_record_digest(store, expected.table, expected.record_id)
        if expected.state == "absent":
            if observed is not None:
                raise KnowledgeRefused(
                    batch_absent_target_refusal(table=expected.table, record_id=expected.record_id)
                )
            continue
        if observed != expected.digest:
            raise KnowledgeRefused(
                batch_stale_record_refusal(
                    table=expected.table,
                    record_id=expected.record_id,
                    expected=expected.digest or "",
                    observed=observed if observed is not None else "<absent>",
                )
            )


def require_distinct_commands(commands: Sequence[ChangeCommand]) -> None:
    """Refuse a batch in which two commands create one record identity.

    A batch is one authored act, so an identity declared twice inside it is a contradiction rather
    than a sequence: nothing says which of the two the author meant. For a revision pair this would
    also be a two-node cycle, and the lineage pass refuses it in that vocabulary; this pass catches
    the same conflict for every other record kind, which has no graph to be cyclic in.
    """

    seen: set[tuple[str, str]] = set()
    for command in commands:
        for table, record_id in inserted_identities(command):
            if (table, record_id) in seen:
                raise KnowledgeRefused(
                    batch_duplicate_expectation_refusal(table=table, record_id=record_id)
                )
            seen.add((table, record_id))


def require_no_accepted_origin(commands: Sequence[ChangeCommand]) -> None:
    """Refuse a command that would store accepted origin data.

    A candidate batch authors proposals. Storing accepted origin data would make this operation a
    promotion path, which it deliberately is not: acceptance belongs to the process that owns it,
    and the operation has no command that could express a promotion in the first place.

    A facet command expresses its origin state too, so it is refused the same way and with the same
    code -- through :func:`…facets.require_proposed_origin`, which the standalone entry point also
    calls, so neither path can accept what the other refuses.
    """

    for command in commands:
        if isinstance(command, _AGGREGATE_KINDS):
            revision = command.revision
            if revision.state_at_origin != PROPOSED_STATE:
                raise KnowledgeRefused(batch_promotion_not_supported_refusal(revision.revision_id))
        if isinstance(command, AddFacet):
            facets.require_proposed_origin(command)


def require_facet_generation(
    store: OpenedKnowledgeStore, commands: Sequence[ChangeCommand]
) -> None:
    """Refuse a facet command against a dataset whose recorded generation predates its tables.

    Requirement 8.4, checked before any row is written: the dataset's own generation is compared
    with the one that registers the facet tables, and the refusal carries both as facts.
    """

    if not facet_commands(commands):
        return
    denied = facets.require_facet_generation(store, "change_candidate")
    if denied is not None:
        raise KnowledgeRefused(denied)


def require_insertions_absent(
    store: OpenedKnowledgeStore, commands: Sequence[ChangeCommand]
) -> None:
    """Refuse an insertion whose new identity is already stored."""

    for command in commands:
        for table, record_id in inserted_identities(command):
            observed = stored_record_digest(store, table, record_id)
            if observed is not None:
                raise KnowledgeRefused(
                    batch_stale_record_refusal(
                        table=table,
                        record_id=record_id,
                        expected="<absent>",
                        observed=observed,
                    )
                )


def require_command_targets(store: OpenedKnowledgeStore, commands: Sequence[ChangeCommand]) -> None:
    """Refuse a command whose named target or endpoints are not admissible.

    ``pending`` is the whole batch's declared identities rather than a running prefix, so a command
    may cite a revision any command in the same batch creates. The order of the commands is how the
    author wrote them down, not the axis the declarations are validated on; what has to hold is that
    the *completed* graph is well formed, which is what the lineage pass below checks.
    """

    pending = pending_identities(commands)
    for index, command in enumerate(commands):
        require_command_target(store, index, command, pending)
    require_completed_lineage(store, commands, pending)


def require_completed_lineage(
    store: OpenedKnowledgeStore, commands: Sequence[ChangeCommand], pending: set[tuple[str, str]]
) -> None:
    """Refuse a batch whose own declarations put a revision on a lineage cycle.

    The graph checked here is the batch's declared edges *plus* the stored ones, and the revisions
    that matter are the ones the batch creates: a stored revision's position was already settled
    when it was written. This is the same rule the single-record operations apply, evaluated over
    the graph the completed batch describes rather than one revision at a time -- so a cycle formed
    entirely among the batch's own revisions is refused here, by name, before any row is written.

    The after-integrity pass re-proves the same property over the stored rows. This pass exists so
    the refusal names the batch's own declarations; the pass after the apply step exists so the
    property is checked against what was actually written.
    """

    if not pending:
        return
    invariant_edges = _declared_edges(commands, family=False)
    family_edges = _declared_edges(commands, family=True)
    _require_declared_acyclic(
        store, pending, invariant_edges, table="invariant_predecessor", family=False
    )
    _require_declared_acyclic(store, pending, family_edges, table="family_predecessor", family=True)
    _require_declared_acyclic_supersessions(store, commands, pending)


def _declared_edges(
    commands: Sequence[ChangeCommand], *, family: bool
) -> tuple[tuple[str, str], ...]:
    """Return the predecessor edges the batch declares, as ``(child, parent)`` pairs."""

    edges: list[tuple[str, str]] = []
    for command in commands:
        if family:
            if not isinstance(command, AddFamilyRevision):
                continue
        elif not isinstance(command, AddInvariantRevision):
            continue
        edges.extend(
            (command.revision.revision_id, parent) for parent in command.revision.predecessors
        )
    return tuple(edges)


def _require_declared_acyclic(
    store: OpenedKnowledgeStore,
    pending: set[tuple[str, str]],
    declared: tuple[tuple[str, str], ...],
    *,
    table: str,
    family: bool,
) -> None:
    """Refuse when the completed graph leaves a revision the batch declares on a cycle.

    The judgement belongs to the lineage module, not here: this gathers the two edge sets the
    completed graph is made of -- the stored edges and the batch's own declarations -- and asks the
    shared rule whether they leave a declared revision on a cycle. Keeping the walk on that side is
    what stops a second cycle rule growing beside the first, and it is why the batch's declared
    edges genuinely reach the rule that decides instead of being post-processed here.
    """

    if not declared:
        return
    finding = lineage.declared_cycle(
        extras=_wider_edges, edges=_stored_edges(store, table), declared=declared
    )
    if finding is None:
        return
    creators = _creators_of(pending, family=family)
    named = sorted(creators & set(finding.members))
    if not named:
        return
    raise KnowledgeRefused(batch_lineage_cycle_refusal(named[0], finding.members, family=family))


def _wider_edges(child: str, declared: tuple[tuple[str, str], ...]) -> tuple[tuple[str, str], ...]:
    """Return the batch's other declarations: the edges a revision is judged *against*.

    A revision's own declared predecessors are already the candidate's own edges, so what the rule
    needs from the batch alongside them is every edge the *other* declared revisions contribute. A
    cycle formed entirely inside one batch is exactly what those edges make visible.
    """

    return tuple(edge for edge in declared if edge[0] != child)


def _creators_of(pending: set[tuple[str, str]], *, family: bool) -> set[str]:
    table = "family_revision" if family else "invariant_revision"
    return {record_id for known_table, record_id in pending if known_table == table}


def _stored_edges(store: OpenedKnowledgeStore, table: str) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(child), str(parent))
        for child, parent in store.connection.execute(
            f"SELECT child_revision_id, parent_revision_id FROM {table} WHERE repository_id = ?",
            (store.repository_id,),
        )
    )


# One target check per command kind, as a table rather than an if-ladder over eighteen variants:
# which check a command gets is the only thing that varies, and a table says so once. Every member
# of the closed union appears here; a case asserts the two agree, so a command added to the union
# without a target check fails the suite rather than raising a KeyError at a caller's expense.
TargetCheck = Callable[["OpenedKnowledgeStore", int, ChangeCommand, set[tuple[str, str]]], None]


def require_command_target(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    pending: set[tuple[str, str]],
) -> None:
    """Refuse one command whose own target preconditions do not hold."""

    _TARGET_CHECKS[command.kind](store, index, command, pending)


def _identity_check(table: str) -> TargetCheck:
    """Return the identity-or-label check bound to one identity table."""

    def check(
        store: OpenedKnowledgeStore,
        index: int,
        command: ChangeCommand,
        pending: set[tuple[str, str]],
    ) -> None:
        _require_identity(store, index, command, pending, table=table)

    return check


def _anchor_check(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    pending: set[tuple[str, str]],
) -> None:
    del pending
    _require_anchor(store, index, command)  # type: ignore[arg-type]


def _membership_check(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    pending: set[tuple[str, str]],
) -> None:
    _require_membership(store, index, command, pending)  # type: ignore[arg-type]


def _claim_check(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    pending: set[tuple[str, str]],
) -> None:
    _require_claim(store, index, command, pending)  # type: ignore[arg-type]


def _facet_check(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    pending: set[tuple[str, str]],
) -> None:
    _require_facet(store, index, command, pending)


def _require_identity(
    store: OpenedKnowledgeStore,
    index: int,
    command: AddInvariant | SetInvariantLabel | AddFamily | SetFamilyLabel,
    pending: set[tuple[str, str]],
    *,
    table: str,
) -> None:
    """Check one identity insert or label edit against the stored row."""

    record_id = (
        command.invariant_id
        if isinstance(command, AddInvariant | SetInvariantLabel)
        else command.family_id
    )
    existing = stored_record_digest(store, table, record_id)
    if isinstance(command, AddInvariant | AddFamily):
        if existing is not None and (table, record_id) not in pending:
            raise KnowledgeRefused(_duplicate_identity_refusal(command, existing))
        return
    if existing is None:
        raise KnowledgeRefused(
            unknown_invariant_refusal(record_id)
            if table == "invariant"
            else unknown_family_refusal(record_id)
        )
    if existing != command.expected_row_digest:
        raise KnowledgeRefused(
            batch_command_refusal(
                "stale_precondition",
                f"{index}:{command.kind}",
                detail="the identity row differs from the row the label edit was authored against",
                next_action=(
                    "Reread the identity row and author the edit against its current digest; the "
                    "stored row was left untouched."
                ),
            )
        )


def _duplicate_identity_refusal(
    command: AddInvariant | AddFamily, observed: str
) -> KnowledgeRefusal:
    """Build the duplicate-identity refusal for one identity insert.

    The stored digest is what the refusal reports as ``observed``, because a label is not an
    identity: two rows can carry the same label and be different rows.
    """

    label = command.display_label.strip()
    if isinstance(command, AddInvariant):
        return duplicate_invariant_refusal(command.invariant_id, observed, label)
    return duplicate_family_refusal(command.family_id, observed, label)


def _require_new_invariant_revision(
    store: OpenedKnowledgeStore,
    index: int,
    command: AddInvariantRevision,
    pending: set[tuple[str, str]],
) -> None:
    """Check one new invariant revision aggregate and its declared predecessors.

    The owning identity may be declared by any command in this same batch, which is why the
    existence question goes through the batch's declared set rather than straight to the table.
    """

    draft = command.revision
    if not present(store, pending, "invariant", draft.invariant_id):
        raise KnowledgeRefused(unknown_invariant_refusal(draft.invariant_id))
    existing = store.get_revision(draft.revision_id)
    if existing is not None:
        raise KnowledgeRefused(
            duplicate_revision_refusal(
                draft.revision_id,
                existing.revision.payload_digest,
                "a newly authored aggregate",
            )
        )
    for parent_id in sorted(draft.predecessors):
        if ("invariant_revision", parent_id) in pending:
            continue
        parent = store.get_revision(parent_id)
        if parent is None:
            raise KnowledgeRefused(_uncreated_predecessor(index, command))
        if parent.revision.invariant_id != draft.invariant_id:
            raise KnowledgeRefused(
                cross_invariant_predecessor_refusal(
                    parent_id, draft.invariant_id, parent.revision.invariant_id
                )
            )


def _require_new_family_revision(
    store: OpenedKnowledgeStore,
    index: int,
    command: AddFamilyRevision,
    pending: set[tuple[str, str]],
) -> None:
    """Check one new family revision aggregate and its declared predecessors.

    The owning identity may be declared by any command in this same batch, on the same rule its
    invariant twin applies.
    """

    draft = command.revision
    if not present(store, pending, "family", draft.family_id):
        raise KnowledgeRefused(unknown_family_refusal(draft.family_id))
    existing = families.get_family_revision(store, draft.revision_id)
    if existing is not None:
        raise KnowledgeRefused(
            duplicate_family_revision_refusal(
                draft.revision_id,
                existing.revision.payload_digest,
                "a newly authored aggregate",
            )
        )
    for parent_id in sorted(draft.predecessors):
        if ("family_revision", parent_id) in pending:
            continue
        parent = families.get_family_revision(store, parent_id)
        if parent is None:
            raise KnowledgeRefused(_uncreated_predecessor(index, command))
        if parent.revision.family_id != draft.family_id:
            raise KnowledgeRefused(
                cross_family_predecessor_refusal(
                    parent_id, draft.family_id, parent.revision.family_id
                )
            )


def _uncreated_predecessor(index: int, command: ChangeCommand) -> KnowledgeRefusal:
    """Build the refusal for a predecessor that is neither stored nor declared by this batch."""

    return batch_command_refusal(
        "invalid_reference",
        f"{index}:{command.kind}",
        detail="a declared predecessor is neither stored nor declared by any command in this batch",
        next_action=(
            "Declare the predecessor in this batch -- its position in the sequence does not matter "
            "-- or cite a stored revision identity. A revision's predecessor set is an exact set of "
            "ancestors, so it is never resolved by searching for something close."
        ),
    )


def _require_anchor(
    store: OpenedKnowledgeStore, index: int, command: AddSourceAnchor | RemoveSourceAnchor
) -> None:
    """Check one anchor insert or removal against the stored rows."""

    if isinstance(command, AddSourceAnchor):
        return
    if anchors.get_anchor(store, command.anchor_id) is not None:
        return
    raise KnowledgeRefused(
        batch_command_refusal(
            "missing_expected_row",
            f"{index}:{command.kind}",
            detail="the anchor a removal names is not stored in this namespace",
            next_action="Read the current anchors and name an existing identity.",
        )
    )


def _require_membership(
    store: OpenedKnowledgeStore,
    index: int,
    command: AddFamilyMember | RemoveFamilyMember,
    pending: set[tuple[str, str]],
) -> None:
    """Check one membership insert or removal against the stored rows and the batch's own writes."""

    if isinstance(command, RemoveFamilyMember):
        if memberships.get_family_member(store, command.member_id) is None:
            raise KnowledgeRefused(
                batch_command_refusal(
                    "missing_expected_row",
                    f"{index}:{command.kind}",
                    detail="the membership a removal names is not stored in this namespace",
                    next_action="Read the current memberships and name an existing identity.",
                )
            )
        return
    member = command.member
    _require_endpoint(
        store, index, command, pending, ("family_revision", member.family_revision_id)
    )
    _require_endpoint(
        store, index, command, pending, ("invariant_revision", member.invariant_revision_id)
    )


def _require_claim(
    store: OpenedKnowledgeStore,
    index: int,
    command: AddRealizationClaim | RemoveRealizationClaim,
    pending: set[tuple[str, str]],
) -> None:
    """Check one realization claim insert or removal, including its anchor endpoint."""

    if isinstance(command, RemoveRealizationClaim):
        if realizations.get_realization_claim(store, command.claim_id) is None:
            raise KnowledgeRefused(
                batch_command_refusal(
                    "missing_expected_row",
                    f"{index}:{command.kind}",
                    detail="the realization claim a removal names is not stored in this namespace",
                    next_action="Read the current claims and name an existing identity.",
                )
            )
        return
    _require_endpoint(
        store,
        index,
        command,
        pending,
        ("invariant_revision", command.claim.invariant_revision_id),
    )
    if isinstance(command.anchor, NewAnchor):
        return
    _require_endpoint(store, index, command, pending, ("source_anchor", command.anchor.anchor_id))


def _require_facet(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    pending: set[tuple[str, str]],
) -> None:
    """Check one facet command's declared references against the completed batch.

    Three questions, and each is asked of the batch's declared set as well as the stored rows,
    because the completed graph is what a batch is validated against: does the facet revision this
    attachment names exist, is the statement revision this explanation explains there, and is the
    revision whose predecessor this edit names there.
    """

    if isinstance(command, AddFacet):
        _require_superseded_decision(store, index, command, pending)
        return
    if isinstance(command, AttachFacet):
        _require_endpoint(
            store, index, command, pending, ("record_revision", command.facet_revision_id)
        )
        return
    if isinstance(command, AuthorExplanation):
        subject = command.subject
        table = "invariant_revision" if subject.kind == "invariant_revision" else "family_revision"
        _require_endpoint(store, index, command, pending, (table, subject.revision_id))
        return
    if isinstance(command, AddExplanationRevision):
        _require_endpoint(store, index, command, pending, ("explanation", command.explanation_id))
        _require_endpoint(
            store,
            index,
            command,
            pending,
            ("explanation_revision", command.predecessor_revision_id),
        )
        return
    if isinstance(command, RemoveFacetAttachment):
        if facets.attachment_endpoint_digest(store, command.attachment_id) is None:
            raise KnowledgeRefused(
                batch_command_refusal(
                    "missing_expected_row",
                    f"{index}:{command.kind}",
                    detail="the attachment a removal names is not stored in this namespace",
                    next_action="Read the current attachments and name an existing identity.",
                )
            )
        return
    # A designation names a stored explanation and one of that explanation's own revisions; the
    # step checks both against the rows it finds, so nothing further is declared here.
    del command


def _require_superseded_decision(
    store: OpenedKnowledgeStore,
    index: int,
    command: AddFacet,
    pending: set[tuple[str, str]],
) -> None:
    """Check one supersession's exact endpoint.

    A decision that supersedes another may cite a decision the same batch authors, wherever in the
    sequence it appears, so the endpoint question is asked of the batch's declared set first. The
    *graph* question is asked separately, over every edge the completed batch declares: one edge at
    a time cannot see a cycle, because each of the cycle's edges belongs to a different command.
    """

    superseded = command.supersedes_revision_id
    if superseded is None:
        return
    if ("record_revision", superseded) in pending:
        return
    try:
        facets.require_decision_revision(store, superseded, command.revision_id)
    except KnowledgeRefused as refused:
        raise KnowledgeRefused(
            batch_command_refusal(
                refused.refusal.code,
                f"{index}:{command.kind}",
                detail=refused.refusal.detail,
                next_action=refused.refusal.next_action,
                facts=RefusalFacts(
                    table=refused.refusal.table,
                    record_id=refused.refusal.record_id,
                    expected=refused.refusal.expected,
                    observed=refused.refusal.observed,
                ),
            )
        ) from refused


def _require_declared_acyclic_supersessions(
    store: OpenedKnowledgeStore, commands: Sequence[ChangeCommand], pending: set[tuple[str, str]]
) -> None:
    """Refuse a batch whose own supersession declarations leave a decision it authors on a cycle.

    The judgement belongs to the shared lineage rule, not here: this gathers the batch's declared
    edges and the stored ones and asks it whether a revision the batch creates is left on a cycle.
    A cycle formed entirely inside one batch is what makes this check worth having beside the
    after-apply pass -- it refuses before any row is written, and it names the batch's own
    declarations rather than the rows that would have carried them.
    """

    declared = _declared_supersession_edges(commands)
    if not declared:
        return
    finding = lineage.declared_cycle(
        extras=lambda child, edges: tuple(edge for edge in edges if edge[0] != child),
        edges=lineage.supersession_edges(store.connection, store.repository_id),
        declared=declared,
    )
    if finding is None:
        return
    creators = {record_id for table, record_id in pending if table == "record_revision"}
    named = sorted(creators & set(finding.members))
    if not named:
        return
    raise KnowledgeRefused(batch_supersession_cycle_refusal(named[0], finding.members))


def _declared_supersession_edges(
    commands: Sequence[ChangeCommand],
) -> tuple[tuple[str, str], ...]:
    """Return every supersession edge the batch declares, as ``(superseding, superseded)`` pairs."""

    edges: list[tuple[str, str]] = []
    for command in commands:
        if not isinstance(command, AddFacet):
            continue
        if command.supersedes_revision_id is None:
            continue
        edges.append((command.revision_id, command.supersedes_revision_id))
    return tuple(edges)


# The noun each endpoint table is called in a refusal. The wording matters because the remedy
# differs: a reader has to know whether to author a family revision, an invariant revision or an
# anchor before it can act on the refusal.
_ENDPOINT_NOUN: dict[str, str] = {
    "family_revision": "family revision",
    "invariant_revision": "invariant revision",
    "source_anchor": "source anchor",
    "record_revision": "facet revision",
    "explanation": "explanation",
    "explanation_revision": "explanation revision",
}


def _require_endpoint(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    pending: set[tuple[str, str]],
    endpoint: tuple[str, str],
) -> None:
    """Refuse a command whose declared endpoint is neither stored nor declared by this batch."""

    table, record_id = endpoint
    if present(store, pending, table, record_id):
        return
    noun = _ENDPOINT_NOUN[table]
    raise KnowledgeRefused(
        batch_command_refusal(
            "invalid_reference",
            f"{index}:{command.kind}",
            detail=f"the command names a {noun} that is not in this namespace",
            next_action=(
                f"Author the {noun} in this batch before the command that cites it, or cite a "
                "stored identity."
            ),
        )
    )


# Declared after the checks it names, so a reader sees every definition before the table that
# selects between them.
_TARGET_CHECKS: Mapping[str, TargetCheck] = {
    "add_invariant": _identity_check("invariant"),
    "set_invariant_label": _identity_check("invariant"),
    "add_family": _identity_check("family"),
    "set_family_label": _identity_check("family"),
    "add_invariant_revision": _require_new_invariant_revision,
    "add_family_revision": _require_new_family_revision,
    "add_source_anchor": _anchor_check,
    "remove_source_anchor": _anchor_check,
    "add_family_member": _membership_check,
    "remove_family_member": _membership_check,
    "add_realization_claim": _claim_check,
    "remove_realization_claim": _claim_check,
    "add_facet": _facet_check,
    "attach_facet": _facet_check,
    "remove_facet_attachment": _facet_check,
    "author_explanation": _facet_check,
    "add_explanation_revision": _facet_check,
    "designate_explanation": _facet_check,
}
