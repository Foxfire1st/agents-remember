"""Applying a validated candidate batch, and re-proving what it left behind.

This is the only module in the batch operation that writes. It runs strictly after every
precondition has passed, inside the same transaction and under the same candidate lock, so a
failure raised from here rolls the whole batch back -- including the rows earlier commands in the
same batch already inserted.

Three rules shape the apply step:

* **Provenance comes from the admission, never from the payload.** A draft that arrived carrying an
  author, an authorization or an instant of its own is re-stamped with the admitted envelope, so the
  fields a caller could author never become the record's provenance.
* **The receipt is derived from what was touched.** Each entry reports the row's own digest as the
  store computes it -- and whether the batch wrote it, deleted it, or found it already as requested.
  A command that ran no statement contributes no entry rather than a claimed write.
* **The failure position is observed, not inferred.** When the database refuses mid-batch, the index
  the loop was running travels with the outcome, so the mapped refusal names the command that
  actually failed instead of the last one the caller happened to declare.

The integrity pass at the bottom is deliberately not a re-run of the preconditions: it checks the
rows as they now stand -- deferred foreign keys, every stored revision's seal, and both lineage
graphs -- so a batch is refused by the state it produced rather than by the request that produced it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import apsw

from agents_remember.memory.knowledge import (
    anchors,
    composition_policies,
    compositions,
    effects,
    evidence,
    facet_records,
    facets,
    families,
    labels,
    lineage,
    lineages,
    memberships,
    realizations,
    records,
)
from agents_remember.memory.knowledge.candidate_records import pending_identities
from agents_remember.memory.knowledge.effect_records import (
    CHANGE_SET_PREDECESSOR_EDGES,
    EFFECT_RECORD_KINDS,
    stored_revisions_of_kind,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    KnowledgeStorageError,
    batch_command_refusal,
    batch_lineage_cycle_refusal,
    batch_refusal,
    facet_supersession_cycle_refusal,
    family_composition_cycle_refusal,
    unknown_family_refusal,
    unknown_invariant_refusal,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    AddFamily,
    AddFamilyComposition,
    AddFamilyCompositionPolicy,
    AddFamilyMember,
    AddFamilyRevision,
    AddInvariant,
    AddInvariantRevision,
    AddRealizationClaim,
    AddSourceAnchor,
    AuthorFamilyExplanationContext,
    ChangeCommand,
    MutableRecordTable,
    RecordIdentity,
    RemoveFamilyMember,
    RemoveRealizationClaim,
    RemoveSourceAnchor,
    SetFamilyLabel,
    SetFamilyRevisionRoute,
    SetInvariantLabel,
)
from agents_remember.models.knowledge.candidate import (
    EffectCommand as _EffectCommand,
)
from agents_remember.models.knowledge.composition import (
    COMPOSITION_COMMAND_KINDS,
    FamilyCompositionDraft,
    FamilyCompositionPolicyVersion,
    FamilyExplanationContext,
)
from agents_remember.models.knowledge.effect import EFFECT_COMMAND_KINDS
from agents_remember.models.knowledge.evidence import EVIDENCE_COMMAND_KINDS
from agents_remember.models.knowledge.facet import (
    FACET_COMMAND_KINDS,
    FACET_KINDS,
    AddExplanationRevision,
    AddFacet,
    AttachFacet,
    AuthorExplanation,
    DesignateExplanation,
    RemoveFacetAttachment,
)
from agents_remember.models.knowledge.family import FamilyRevision, FamilyRevisionDraft
from agents_remember.models.knowledge.invariant import InvariantRevision
from agents_remember.models.knowledge.result import (
    KnowledgeRefusal,
    RealizationClaimRequest,
    RevisionDraft,
)

if TYPE_CHECKING:
    from agents_remember.memory.knowledge.store import OpenedKnowledgeStore

# One touched record as the receipt reports it.
TouchedRow = tuple[RecordIdentity, ...]


@dataclass
class BatchLedger:
    """What one batch has actually touched, accumulated in command order.

    The ledger is the only mutable state the apply step keeps. It records the exact receipt entries
    and the identities the batch has created, so a receipt reports what happened and a later command
    can cite a record an earlier one created.
    """

    changed: list[RecordIdentity] = field(default_factory=list)
    created: set[tuple[str, str]] = field(default_factory=set)

    def written(self, table: MutableRecordTable, record_id: str, digest: str) -> TouchedRow:
        """Record one written row as a receipt entry and as a batch-created identity."""

        self.created.add((table, record_id))
        return self._append("written", table, record_id, digest)

    def removed(self, table: MutableRecordTable, record_id: str, digest: str) -> TouchedRow:
        """Record one deleted row, with the digest it had when the batch deleted it."""

        return self._append("removed", table, record_id, digest)

    def _append(
        self, state: str, table: MutableRecordTable, record_id: str, digest: str
    ) -> TouchedRow:
        entry = RecordIdentity.model_construct(
            state=state, table=table, record_id=record_id, digest=digest
        )
        self.changed.append(entry)
        return (entry,)


@dataclass(frozen=True)
class BatchApplication:
    """The outcome of one apply pass: what it touched, or where it stopped.

    ``failed_index`` and ``failed_command`` are observed inside the loop rather than reconstructed
    from the request, because the command SQLite refused is not necessarily the last one the caller
    declared. The caller turns them into the batch's typed refusal, which is where the mapping from
    a SQLite error to a refusal code already lives.
    """

    ledger: BatchLedger
    failed_index: int | None = None
    failed_command: str | None = None
    failure: apsw.Error | None = None

    @property
    def completed(self) -> bool:
        """Whether every command ran."""

        return self.failure is None

    def observed_failure(self) -> tuple[apsw.Error, int | None, str | None] | None:
        """Return the database failure with the command position the loop observed, if any."""

        if self.failure is None:
            return None
        return (self.failure, self.failed_index, self.failed_command)


def apply_commands(
    store: OpenedKnowledgeStore, commands: Sequence[ChangeCommand], authorship: Authorship
) -> BatchApplication:
    """Apply every command in order inside the caller's open transaction.

    Each command's own refusal is restated as a refusal of the batch that carried it, naming the
    position and kind of the failing command, because that is what the caller submitted. A failure
    raised by the database instead leaves the loop here, carrying the index and kind that were
    running so the mapped refusal can name the command that actually failed.
    """

    pending = frozenset(pending_identities(commands))
    ledger = BatchLedger()
    for index, command in enumerate(commands):
        try:
            for entry in _apply_one(store, index, command, authorship, pending):
                ledger.changed.append(entry)
        except apsw.Error as error:
            return BatchApplication(
                ledger=ledger,
                failed_index=index,
                failed_command=command.kind,
                failure=error,
            )
    store.require_referential_integrity()
    return BatchApplication(ledger=ledger)


def _apply_one(
    store: OpenedKnowledgeStore,
    index: int,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    """Apply one command, restating any refusal it raises as a refusal of the batch."""

    try:
        return _apply_command(store, command, authorship, pending)
    except KnowledgeRefused as refused:
        raise KnowledgeRefused(
            batch_refusal(refused.refusal, index=index, command=command.kind)
        ) from refused


# The families one command can belong to, as a dispatch table rather than a ladder: each row pairs
# the vocabulary's own declaration of a family's kinds with the step that applies it. Six families are
# dispatched separately -- insertion, label edit, the composition commands, the authored facet
# commands, the supporting-record commands and the authored-effect commands -- so each one reads as
# the single decision it is rather than as one long conditional over twenty-six variants. The facet,
# supporting-record and authored-effect commands go to their own modules' in-transaction steps, which
# raise the same typed refusals this batch restates and report the same kind of touched-row entry.
#
# The row's membership is the vocabulary's own constant rather than a second literal, so a command
# added to a family reaches its step without an edit here. A family that needs neither the authorship
# nor the pending set ignores the parameter it does not read, which is what keeps the table one shape
# instead of six.
ApplyFamily = Callable[
    ["OpenedKnowledgeStore", ChangeCommand, Authorship, frozenset[tuple[str, str]]],
    tuple[RecordIdentity, ...],
]


def _apply_family_insert(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    return _apply_insert(store, command, authorship, pending)


def _apply_family_label(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    del authorship, pending
    return _apply_label(store, command)


def _apply_family_composition(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    del pending
    return _apply_composition(store, command, authorship)


def _apply_family_facet(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    return _apply_facet(store, command, authorship, pending)


def _apply_family_evidence(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    del pending
    return _apply_evidence(store, command, authorship)


def _apply_family_effect(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    del pending
    return _apply_effect(store, command, authorship)


def _apply_command(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    """Apply one validated command, through the dispatched family that owns its kind."""

    for kinds, apply_family in _APPLY_FAMILIES:
        if command.kind in kinds:
            return apply_family(store, command, authorship, pending)
    return _apply_removal(store, command)


def _apply_effect(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
) -> tuple[RecordIdentity, ...]:
    """Apply one authored-effect command through the record group's in-transaction step.

    The step is handed no ``pending`` set, and that is deliberate: it resolves every reference against
    the rows as they stand, and the batch applies commands in the order the author wrote them, so a
    citation of an identity the same batch creates resolves once that command has run. A citation that
    arrives first is refused with the offending reference named and the ordering remedy stated.
    """

    if not isinstance(command, _EffectCommand):  # pragma: no cover - the dispatch set is closed
        raise KnowledgeStorageError(
            f"no authored-effect apply step for candidate command kind {command.kind!r}"
        )
    return effects.apply_effect_command(store, command, authorship)


def _apply_facet(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    """Apply one authored facet command through the facet module's in-transaction step."""

    assert isinstance(command, _FACET_COMMANDS)
    written = facets.apply_facet_command(store, command, authorship, pending)
    return tuple(
        _written_entry(entry.state, entry.table, entry.record_id, entry.digest) for entry in written
    )


def _apply_evidence(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
) -> tuple[RecordIdentity, ...]:
    """Apply one supporting-record command through its own module's in-transaction step.

    The evidence step resolves every link the command declares -- subject, evidence anchor and every
    claimed-coverage endpoint -- against the stored rows, and refuses before it writes anything. That
    is why the batch's completed-graph pass asks only that the *command* be declared, while the step
    asks whether the referenced rows exist.
    """

    written = evidence.apply_evidence_command(store, command, authorship)  # type: ignore[arg-type]
    return tuple(
        _written_entry(entry.state, entry.table, entry.record_id, entry.digest) for entry in written
    )


_INSERTING_KINDS = frozenset(
    {
        "add_invariant",
        "add_invariant_revision",
        "add_family",
        "add_family_revision",
        "add_source_anchor",
        "add_family_member",
        "add_realization_claim",
    }
)

_LABELING_KINDS = frozenset({"set_invariant_label", "set_family_label"})

# The six facet commands as classes, declared once beside the kind set the dispatcher routes on. The
# facet step's own parameter is the facet union rather than the whole closed command union, so the
# dispatcher states which family it routed at the boundary instead of widening that step.
_FACET_COMMANDS = (
    AddFacet,
    AttachFacet,
    RemoveFacetAttachment,
    AuthorExplanation,
    AddExplanationRevision,
    DesignateExplanation,
)

# The four commands the composition generation adds, dispatched together: each writes one authored
# row through the module that owns it, and none of them addresses a sealed revision aggregate. The
# membership comes from the vocabulary's own declaration rather than from a second literal.
_COMPOSITION_KINDS = COMPOSITION_COMMAND_KINDS


def _apply_composition(
    store: OpenedKnowledgeStore, command: ChangeCommand, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    """Apply one composition command through its own in-transaction step."""

    if isinstance(command, AddFamilyCompositionPolicy):
        return _add_composition_policy(store, command, authorship)
    if isinstance(command, AddFamilyComposition):
        return _add_composition(store, command, authorship)
    if isinstance(command, SetFamilyRevisionRoute):
        return _set_family_revision_route(store, command, authorship)
    if isinstance(command, AuthorFamilyExplanationContext):
        return _author_family_explanation_context(store, command, authorship)
    return _refuse_unreachable(command)


def _add_composition_policy(
    store: OpenedKnowledgeStore, command: AddFamilyCompositionPolicy, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    """Declare one immutable policy version under the admitted provenance.

    The provenance comes from the admission and never from the command, so no part of a submitted
    payload can become the record's author, authorization or instant.
    """

    version = FamilyCompositionPolicyVersion(
        repository_id=store.repository_id,
        policy_id=command.policy.policy_id,
        policy_version_id=command.policy.policy_version_id,
        declared_version=command.policy.declared_version,
        direction=command.policy.direction,
        depth_bound=command.policy.depth_bound,
        widened_scope=command.policy.widened_scope,
        provenance=authorship,
    )
    composition_policies.insert_policy_version(store, version)
    return (
        _written_entry(
            "written",
            "family_composition_policy_version",
            version.policy_version_id,
            composition_policies.policy_version_row_digest(store.repository_id, version),
        ),
    )


def _add_composition(
    store: OpenedKnowledgeStore, command: AddFamilyComposition, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    composition = compositions.composition_draft_of(
        FamilyCompositionDraft(
            composition_id=command.composition_id,
            from_family_revision_id=command.from_family_revision_id,
            to_family_revision_id=command.to_family_revision_id,
            policy_id=command.policy_id,
            policy_version_id=command.policy_version_id,
            provenance=authorship,
        ),
        store.repository_id,
    )
    compositions.insert_composition(store, composition)
    return (
        _written_entry(
            "written",
            "family_composition",
            composition.composition_id,
            compositions.composition_row_digest(store.repository_id, composition),
        ),
    )


def _set_family_revision_route(
    store: OpenedKnowledgeStore, command: SetFamilyRevisionRoute, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    compositions.insert_family_revision_route(
        store, command.family_revision_id, command.route_id, authorship
    )
    return (
        _written_entry(
            "written",
            "family_revision_route",
            command.family_revision_id,
            compositions.owning_route_row_digest(
                store.repository_id, command.family_revision_id, command.route_id
            ),
        ),
    )


def _author_family_explanation_context(
    store: OpenedKnowledgeStore,
    command: AuthorFamilyExplanationContext,
    authorship: Authorship,
) -> tuple[RecordIdentity, ...]:
    subject = families.get_family_revision(store, command.context.family_revision_id)
    if subject is None:  # pragma: no cover - the precondition refuses this before the apply step
        raise KnowledgeRefused(unknown_family_refusal(command.context.family_revision_id))
    context = FamilyExplanationContext(
        context_id=command.context.context_id,
        family_id=subject.revision.family_id,
        family_revision_id=command.context.family_revision_id,
        revision_id=command.context.revision_id,
        predecessor_revision_id=(
            command.context.predecessor_revision_id or command.context.revision_id
        ),
        body=command.context.body,
        provenance=authorship,
    )
    compositions.insert_context_revision(store, context)
    entries = [
        _written_entry(
            "written",
            "family_revision_context_revision",
            context.revision_id,
            compositions.context_row_digest(store.repository_id, context),
        )
    ]
    if command.context.predecessor_revision_id is None:
        entries.append(
            _written_entry(
                "written",
                "family_revision_context",
                context.context_id,
                compositions.context_row_digest(store.repository_id, context),
            )
        )
    return tuple(entries)


# The authored-effect command kinds, as the one vocabulary the record group's own module declares.
# Derived from that declaration rather than restated, so a fifth command cannot exist in the union
# without reaching its apply step.
_EFFECT_KINDS = frozenset(EFFECT_COMMAND_KINDS)

# The dispatch table itself, declared after every family's kind set and every step it names, so a
# reader sees each declaration before the table that selects between them.
_APPLY_FAMILIES: tuple[tuple[frozenset[str], ApplyFamily], ...] = (
    (_INSERTING_KINDS, _apply_family_insert),
    (_LABELING_KINDS, _apply_family_label),
    (frozenset(_COMPOSITION_KINDS), _apply_family_composition),
    (frozenset(FACET_COMMAND_KINDS), _apply_family_facet),
    (frozenset(EVIDENCE_COMMAND_KINDS), _apply_family_evidence),
    (frozenset(EFFECT_COMMAND_KINDS), _apply_family_effect),
)


def _apply_insert(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    """Apply one command that creates a record."""

    if isinstance(
        command,
        AddInvariant | AddInvariantRevision | AddFamily | AddFamilyRevision | AddSourceAnchor,
    ):
        return _apply_identity_insert(store, command, authorship, pending)
    if isinstance(command, AddFamilyMember):
        return _add_member(store, command, authorship)
    if isinstance(command, AddRealizationClaim):
        return _add_claim(store, command, authorship)
    return _refuse_unreachable(command)


def _apply_identity_insert(
    store: OpenedKnowledgeStore,
    command: ChangeCommand,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    """Apply one command that creates an identity row or a sealed revision aggregate."""

    if isinstance(command, AddInvariant):
        return _add_invariant(store, command, authorship)
    if isinstance(command, AddInvariantRevision):
        return _add_invariant_revision(store, command, authorship, pending)
    if isinstance(command, AddFamily):
        return _add_family(store, command, authorship)
    if isinstance(command, AddFamilyRevision):
        return _add_family_revision(store, command, authorship, pending)
    if isinstance(command, AddSourceAnchor):
        return _add_anchor(store, command, authorship)
    return _refuse_unreachable(command)


def _apply_label(store: OpenedKnowledgeStore, command: ChangeCommand) -> tuple[RecordIdentity, ...]:
    """Apply one friendly-label edit.

    A label edit whose requested label is already the stored one runs no statement and contributes
    no receipt entry: the row's value did not move, and a receipt reports what was touched.
    """

    if isinstance(command, SetInvariantLabel):
        return _set_invariant_label(store, command)
    if isinstance(command, SetFamilyLabel):
        return _set_family_label(store, command)
    return _refuse_unreachable(command)


def _apply_removal(
    store: OpenedKnowledgeStore, command: ChangeCommand
) -> tuple[RecordIdentity, ...]:
    """Apply one explicit removal, reporting the row that is gone."""

    if isinstance(command, RemoveSourceAnchor):
        return _remove_anchor(store, command)
    if isinstance(command, RemoveFamilyMember):
        return _remove_member(store, command)
    if isinstance(command, RemoveRealizationClaim):
        return _remove_claim(store, command)
    return _refuse_unreachable(command)


def _refuse_unreachable(command: ChangeCommand) -> tuple[RecordIdentity, ...]:
    """Refuse a command that reached the apply step without a handler.

    Unreachable by construction -- the union and the dispatch tables are closed over the same twelve
    kinds -- which is why it is a defect rather than a refusal a caller could provoke.
    """

    raise KnowledgeStorageError(  # pragma: no cover - the union and the dispatch agree
        f"no apply step for candidate command kind {command.kind!r}"
    )


def _written_entry(
    state: str, table: MutableRecordTable, record_id: str, digest: str
) -> RecordIdentity:
    """Build one receipt entry without re-validating it: the store computed every field here."""

    return RecordIdentity.model_construct(
        state=state, table=table, record_id=record_id, digest=digest
    )


def _add_invariant(
    store: OpenedKnowledgeStore, command: AddInvariant, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    store.insert_invariant_identity(
        invariant_id=command.invariant_id,
        display_label=command.display_label,
        provenance=authorship,
    )
    return (
        _written_entry(
            "written",
            "invariant",
            command.invariant_id,
            _invariant_digest(store, command.invariant_id),
        ),
    )


def _invariant_digest(store: OpenedKnowledgeStore, invariant_id: str) -> str:
    identity = store.get_invariant(invariant_id)
    if identity is None:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeRefused(unknown_invariant_refusal(invariant_id))
    return identity.row_digest


def _family_digest(store: OpenedKnowledgeStore, family_id: str) -> str:
    identity = families.get_family(store, family_id)
    if identity is None:  # pragma: no cover - the insert above either wrote the row or raised
        raise KnowledgeRefused(unknown_family_refusal(family_id))
    return identity.row_digest


def _add_family(
    store: OpenedKnowledgeStore, command: AddFamily, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    families.insert_family(
        store,
        family_id=command.family_id,
        display_label=command.display_label,
        provenance=authorship,
    )
    return (
        _written_entry(
            "written", "family", command.family_id, _family_digest(store, command.family_id)
        ),
    )


def _sealed_revision(
    repository_id: str, draft: RevisionDraft, authorship: Authorship
) -> InvariantRevision:
    """Seal one authored invariant draft under the admitted provenance."""

    return records.sealed_revision_from_draft(
        repository_id, draft.model_copy(update={"provenance": authorship})
    )


def _sealed_family_revision(
    repository_id: str, draft: FamilyRevisionDraft, authorship: Authorship
) -> FamilyRevision:
    """Seal one authored family draft under the admitted provenance."""

    return records.sealed_family_revision_from_draft(
        repository_id, draft.model_copy(update={"provenance": authorship})
    )


def _add_invariant_revision(
    store: OpenedKnowledgeStore,
    command: AddInvariantRevision,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    revision = _sealed_revision(store.repository_id, command.revision, authorship)
    store.insert_revision_aggregate(revision, pending=pending)
    return (
        _written_entry(
            "written", "invariant_revision", revision.revision_id, revision.payload_digest
        ),
    )


def _add_family_revision(
    store: OpenedKnowledgeStore,
    command: AddFamilyRevision,
    authorship: Authorship,
    pending: frozenset[tuple[str, str]],
) -> tuple[RecordIdentity, ...]:
    revision = _sealed_family_revision(store.repository_id, command.revision, authorship)
    families.insert_family_revision(store, revision, pending=pending)
    return (
        _written_entry("written", "family_revision", revision.revision_id, revision.payload_digest),
    )


def _add_anchor(
    store: OpenedKnowledgeStore, command: AddSourceAnchor, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    anchor = anchors.source_anchor_from_draft(command.anchor, authorship)
    anchors.insert_anchor_row(store, anchor)
    return (
        _written_entry(
            "written",
            "source_anchor",
            str(anchor.anchor_id),
            records.anchor_row_digest(anchor, store.repository_id),
        ),
    )


def _add_member(
    store: OpenedKnowledgeStore, command: AddFamilyMember, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    member = memberships.insert_family_member_draft(store, command.member, authorship)
    return (_written_entry("written", "family_member", member.member_id, member.row_digest),)


def _add_claim(
    store: OpenedKnowledgeStore, command: AddRealizationClaim, authorship: Authorship
) -> tuple[RecordIdentity, ...]:
    stored = realizations.insert_realization_claim(
        store,
        RealizationClaimRequest(
            repository_id=store.repository_id,
            claim=command.claim,
            anchor=command.anchor,
            provenance=authorship,
        ),
    )
    if stored is None:  # pragma: no cover - the duplicate check refuses this before the apply step
        return ()
    return (
        _written_entry("written", "realization_claim", stored.claim_id, stored.row_digest),
        _written_entry(
            "written",
            "source_anchor",
            stored.anchor_id,
            _anchor_digest(store, stored.anchor_id),
        ),
    )


def _anchor_digest(store: OpenedKnowledgeStore, anchor_id: str) -> str:
    anchor = anchors.get_anchor(store, anchor_id)
    if anchor is None:  # pragma: no cover - a claim cannot exist without its anchor row
        raise KnowledgeRefused(
            batch_command_refusal(
                "missing_expected_row",
                "0:add_realization_claim",
                detail="the claim's anchor was not recorded with it",
                next_action="Treat the store as damaged and recover it from an intact snapshot.",
            )
        )
    return records.anchor_row_digest(anchor, store.repository_id)


def _set_invariant_label(
    store: OpenedKnowledgeStore, command: SetInvariantLabel
) -> tuple[RecordIdentity, ...]:
    wrote = labels.apply_invariant_label(
        store,
        invariant_id=command.invariant_id,
        display_label=command.display_label,
        expected_row_digest=command.expected_row_digest,
    )
    if not wrote:
        return ()
    return (
        _written_entry(
            "written",
            "invariant",
            command.invariant_id,
            _invariant_digest(store, command.invariant_id),
        ),
    )


def _set_family_label(
    store: OpenedKnowledgeStore, command: SetFamilyLabel
) -> tuple[RecordIdentity, ...]:
    wrote = labels.apply_family_label(
        store,
        family_id=command.family_id,
        display_label=command.display_label,
        expected_row_digest=command.expected_row_digest,
    )
    if not wrote:
        return ()
    return (
        _written_entry(
            "written", "family", command.family_id, _family_digest(store, command.family_id)
        ),
    )


def _remove_anchor(
    store: OpenedKnowledgeStore, command: RemoveSourceAnchor
) -> tuple[RecordIdentity, ...]:
    anchor = anchors.get_anchor(store, command.anchor_id)
    if anchor is None:  # pragma: no cover - the precondition refuses this before the apply step
        raise KnowledgeRefused(
            batch_command_refusal(
                "missing_expected_row",
                "0:remove_source_anchor",
                detail="the anchor a removal names is not stored in this namespace",
                next_action="Read the current anchors and name an existing identity.",
            )
        )
    digest = records.anchor_row_digest(anchor, store.repository_id)
    anchors.delete_anchor(store, command.anchor_id)
    return (_written_entry("removed", "source_anchor", command.anchor_id, digest),)


def _remove_member(
    store: OpenedKnowledgeStore, command: RemoveFamilyMember
) -> tuple[RecordIdentity, ...]:
    memberships.delete_family_member(store, command.member_id, command.expected_row_digest)
    return (
        _written_entry("removed", "family_member", command.member_id, command.expected_row_digest),
    )


def _remove_claim(
    store: OpenedKnowledgeStore, command: RemoveRealizationClaim
) -> tuple[RecordIdentity, ...]:
    realizations.delete_realization_claim(store, command.claim_id, command.expected_row_digest)
    return (
        _written_entry(
            "removed", "realization_claim", command.claim_id, command.expected_row_digest
        ),
    )


# -- the integrity pass -----------------------------------------------------------------------


def require_after_integrity(store: OpenedKnowledgeStore) -> None:
    """Re-prove the stored rows after the batch, inside the same transaction.

    Three passes, each catching a failure the others do not:

    * the deferred foreign keys, so a batch that inserted a row whose endpoint it never wrote is
      refused rather than committed;
    * every stored revision's payload seal, re-derived from the row as stored, so an aggregate that
      does not match its own identity is caught before it becomes durable;
    * both lineage graphs as the completed batch left them, so a cycle is refused by the rows rather
      than by the request that produced them.
    """

    store.require_referential_integrity()
    _require_sealed_rows(store)
    _require_sealed_facet_rows(store)
    _require_sealed_effect_rows(store)
    _require_acyclic_graph(store, table="invariant_predecessor", family=False)
    _require_acyclic_graph(store, table="family_predecessor", family=True)
    _require_acyclic_supersessions(store)
    _require_acyclic_compositions(store)
    _require_acyclic_successions(store)


def _require_sealed_rows(store: OpenedKnowledgeStore) -> None:
    """Decode every stored revision, which re-derives and verifies its payload seal."""

    for table in ("invariant_revision", "family_revision"):
        for record_id in _revision_ids(store, table):
            stored = (
                store.get_revision(record_id)
                if table == "invariant_revision"
                else families.get_family_revision(store, record_id)
            )
            if stored is None:  # pragma: no cover - the id came from that same table
                raise KnowledgeRefused(_vanished_row(table))


def _require_sealed_facet_rows(store: OpenedKnowledgeStore) -> None:
    """Decode every stored facet revision and explanation revision, re-deriving its seal.

    A facet revision's ``content_digest`` and an explanation revision's ``payload_digest`` are seals
    over content, and the decoder recomputes each from the row as stored. A batch that wrote a row
    which does not match its own identity is therefore caught here, inside the same transaction,
    rather than becoming durable.

    Facet aggregates join this pass instead of bypassing it: a facet record whose revision is
    missing, or whose payload no longer validates against its declared subtype, is not a state this
    substrate stores.
    """

    for record_id, revision_id, facet_kind in _facet_revisions(store):
        row = _row_of(store, "record_revision", revision_id)
        facet_records.decode_facet_revision_row(row, facet_kind)
        del record_id
    for revision_id in _ids_of(store, "explanation_revision"):
        row = _row_of(store, "explanation_revision", revision_id)
        facet_records.decode_explanation_revision_row(row)


def _facet_revisions(store: OpenedKnowledgeStore) -> tuple[tuple[str, str, str], ...]:
    """Return every stored facet revision with the kind of record it belongs to.

    The pass is scoped to the facet kinds, because the decoder it drives is the *facet* revision
    decoder: it refuses a record whose kind and stored schema disagree, and a record group that is
    not the facet vocabulary has its own decoder and its own shape. ``knowledge_record`` is shared by
    every record group, so "which revisions does this pass own" is a question about the kind the
    envelope carries rather than about the table.

    The kind list is part of the statement rather than a filter applied afterwards, and that is a
    correction rather than a convenience: the envelope carries every record group's rows, so a scan
    that named no kind would hand this pass another group's revision to decode as a facet -- which its
    decoder correctly refuses, turning a healthy store into a reported storage error. The list is
    interpolated from the vocabulary's own declaration, so a ninth facet subtype is admitted here
    without a second edit, and it reaches the statement as declared identifiers rather than as caller
    text, exactly as the shared endpoint check does the same thing.
    """

    kind_placeholders = ", ".join("?" for _ in FACET_KINDS)
    rows = store.connection.execute(
        "SELECT envelope.record_id, revision.revision_id, envelope.kind "
        "FROM knowledge_record AS envelope "
        "JOIN record_revision AS revision ON revision.repository_id = envelope.repository_id "
        "AND revision.record_id = envelope.record_id "
        f"WHERE envelope.repository_id = ? AND envelope.kind IN ({kind_placeholders}) "
        "ORDER BY revision.revision_id",
        (store.repository_id, *FACET_KINDS),
    )
    return tuple((str(row[0]), str(row[1]), str(row[2])) for row in rows)


def _ids_of(store: OpenedKnowledgeStore, table: str) -> tuple[str, ...]:
    rows = store.connection.execute(
        f"SELECT revision_id FROM {table} WHERE repository_id = ? ORDER BY revision_id",
        (store.repository_id,),
    )
    return tuple(str(row[0]) for row in rows)


def _row_of(store: OpenedKnowledgeStore, table: str, revision_id: str) -> tuple[object, ...]:
    """Return one stored revision row in its declared column order."""

    row = next(
        iter(
            store.connection.execute(
                f"SELECT * FROM {table} WHERE repository_id = ? AND revision_id = ?",
                (store.repository_id, revision_id),
            )
        ),
        None,
    )
    if row is None:  # pragma: no cover - the id came from that same table
        raise KnowledgeRefused(_vanished_row(table))
    return row


def _require_acyclic_supersessions(store: OpenedKnowledgeStore) -> None:
    """Refuse when the decision supersession graph holds a cycle after the batch was applied.

    The third graph the shared rule is applied to. It is a whole-graph check like the two above:
    the batch is finished, so the question is whether the graph it left is acyclic at all.
    """

    edges = lineage.supersession_edges(store.connection, store.repository_id)
    graph: dict[str, set[str]] = {}
    for child, parent in edges:
        graph.setdefault(child, set()).add(parent)
        graph.setdefault(parent, set())
    cycle = lineage.cycle_vertices(graph)
    if not cycle:
        return
    members = tuple(sorted(cycle))
    raise KnowledgeRefused(facet_supersession_cycle_refusal(members[0], members))


def _require_acyclic_compositions(store: OpenedKnowledgeStore) -> None:
    """Refuse when the composition graph holds a cycle after the batch was applied.

    The fourth whole-graph pass, and the third *graph*: the batch is finished, so the question is
    whether the graph it left is acyclic at all, and the verdict belongs to the shared rule in
    :mod:`…lineage` -- this gathers the repository's edges through the third edge source and asks
    ``cycle_vertices``. A single cycle anywhere in that graph is refused with its members, whatever
    command happened to create it.
    """

    graph: dict[str, set[str]] = {}
    for child, parent in lineages.composition_edges(store.connection, store.repository_id):
        graph.setdefault(child, set()).add(parent)
        graph.setdefault(parent, set())
    cycle = lineage.cycle_vertices(graph)
    if not cycle:
        return
    members = tuple(sorted(cycle))
    raise KnowledgeRefused(family_composition_cycle_refusal(members[0], members))


def _require_sealed_effect_rows(store: OpenedKnowledgeStore) -> None:
    """Decode every stored authored-effect revision, re-deriving its seal.

    An authored-effect revision's ``content_digest`` is a seal over its payload, and the decoder
    recomputes it from the row as stored -- so a batch that wrote a row which does not match its own
    identity is caught here, inside the same transaction, rather than becoming durable. Each revision
    is decoded against the shape its own kind declares, because the kind lives on the envelope row and
    the payload does not carry it.
    """

    for kind in EFFECT_RECORD_KINDS:
        stored_revisions_of_kind(store, kind)


def _require_acyclic_successions(store: OpenedKnowledgeStore) -> None:
    """Refuse when the change-set succession graph holds a cycle after the batch was applied.

    The fourth graph the shared rule is applied to, and a whole-graph check like the three above: the
    batch is finished, so the question is whether the graph it left is acyclic at all. A cycle a
    single command could not write alone -- two successors each naming the other -- is refused by the
    rows rather than by the request that produced them.
    """

    graph: dict[str, set[str]] = {}
    for child, parent in _change_set_edges(store):
        graph.setdefault(child, set()).add(parent)
        graph.setdefault(parent, set())
    cycle = lineage.cycle_vertices(graph)
    if not cycle:
        return
    members = tuple(sorted(cycle))
    denial = effects.require_acyclic_successions(store, members[0])
    if denial is None:  # pragma: no cover - the same edges were just read
        return
    raise KnowledgeRefused(denial)


def _change_set_edges(store: OpenedKnowledgeStore) -> tuple[tuple[str, str], ...]:
    """Return every stored change-set succession edge as a ``(successor, predecessor)`` pair."""

    return tuple(
        (str(row[0]), str(row[1]))
        for row in store.connection.execute(CHANGE_SET_PREDECESSOR_EDGES, (store.repository_id,))
    )


def _vanished_row(table: str) -> KnowledgeRefusal:
    return batch_command_refusal(
        "missing_expected_row",
        "0:after_integrity",
        detail=f"a {table} row disappeared inside its own transaction",
        next_action="Treat the store as damaged and recover it from an intact snapshot.",
    )


def _revision_ids(store: OpenedKnowledgeStore, table: str) -> tuple[str, ...]:
    rows = store.connection.execute(
        f"SELECT revision_id FROM {table} WHERE repository_id = ? ORDER BY revision_id",
        (store.repository_id,),
    )
    return tuple(str(row[0]) for row in rows)


def _require_acyclic_graph(store: OpenedKnowledgeStore, *, table: str, family: bool) -> None:
    """Refuse when a lineage graph holds a cycle after the batch was applied.

    The check is a whole-graph one rather than a per-candidate one: the batch is finished, so the
    question is whether the graph it left is acyclic at all. A single cycle anywhere in that graph
    is refused with its members, whatever command happened to create it.
    """

    edges = _graph_edges(store, table)
    cycle = lineage.cycle_vertices(edges)
    if not cycle:
        return
    members = tuple(sorted(cycle))
    raise KnowledgeRefused(batch_lineage_cycle_refusal(members[0], members, family=family))


def _graph_edges(store: OpenedKnowledgeStore, table: str) -> dict[str, set[str]]:
    edges: dict[str, set[str]] = {}
    for child, parent in store.connection.execute(
        f"SELECT child_revision_id, parent_revision_id FROM {table} WHERE repository_id = ?",
        (store.repository_id,),
    ):
        edges.setdefault(str(child), set()).add(str(parent))
        edges.setdefault(str(parent), set())
    return edges
