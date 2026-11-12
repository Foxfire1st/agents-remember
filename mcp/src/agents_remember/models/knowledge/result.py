"""Typed operation requests, refusal codes and mutation results.

Every refusal names the exact code, operation, offending record and a concrete next action.
The codes here are the vocabulary the storage layer maps onto; a storage failure that has no
code here is a defect, not a new code invented at the raise site.

The result models enforce the outcome they report: a ``created`` result must carry ``stored=True``
and no refusal, a ``refused`` result must carry its refusal, and a revision result must carry the
digest the store recomputed. A caller therefore reads one consistent value instead of re-deriving
whether an operation succeeded.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from agents_remember.models.knowledge.base import (
    LABEL_MAX_LENGTH,
    PROSE_MAX_LENGTH,
    REFERENCE_MAX_LENGTH,
    KnowledgeModel,
)

KnowledgeOperation = Literal[
    "create_repository",
    "create_invariant",
    "create_invariant_revision",
    "create_family",
    "create_family_revision",
    "create_source_anchor",
    "author_route",
    "set_governing_route",
    "remove_source_anchor",
    "create_family_member",
    "remove_family_member",
    "create_realization_claim",
    "remove_realization_claim",
    "set_invariant_label",
    "set_family_label",
    "change_candidate",
    # Family composition. The edge, the revision-level owning association and the explanatory
    # context are authored through the candidate batch, whose refusals are restated as
    # ``change_candidate``; these two names are the *operations* a relation write reports when it is
    # reached directly, exactly as ``create_family_member`` and ``set_governing_route`` are for the
    # relation tables that came before them. They are members here because
    # :data:`…endpoints.RelationWrite` is a subset of this vocabulary: a relation write's
    # ``operation`` has always been an operation name, and a new relation kind does not get to
    # invent a second spelling for "which act refused this".
    "create_composition",
    "set_family_revision_route",
    "create_candidate",
    "clone_candidate",
    "open_candidate",
    "publish_snapshot",
    "read_published_snapshot",
    "dispose_candidate",
    # Common-base merge. Two operations rather than one because base resolution is a separate,
    # separately-refusable step: a caller that cannot even establish which dataset is the common
    # base has not attempted a merge, and the refusal has to say so in its own name.
    "resolve_merge_base",
    "merge_knowledge_datasets",
    # Portable logical export and import. Two operations rather than one for the same reason the
    # merge pair is split: an export answers "what is this dataset, logically" and an import
    # answers "may this artifact become a dataset here", and a caller branches on which of the two
    # it asked.
    "export_knowledge_dataset",
    "import_knowledge_dataset",
    # Selective recorded-scope read. One operation, because a seed and a continuation are two ways
    # of asking the same question -- "which recorded scope, and which page of it" -- and a caller
    # branches on the refusal code, not on which of the two it passed.
    "read_knowledge_scope",
    # Baseline-to-candidate comparison. One operation rather than two, for the reason the read pair
    # is one: a first comparison and a continuation are two ways of asking the same question, and a
    # caller branches on the refusal code rather than on which of the two it passed.
    "diff_knowledge_scope",
    # Authored judgment facets. Six write operations -- record a facet, attach it, remove one
    # attachment, author an explanation, edit an explanation, and record a designation -- plus the
    # facet-specific read. They are separate members rather than one, because a caller branches on
    # which authored act it performed and each carries its own remedy; the candidate batch that
    # carries the same commands restates its refusals as ``change_candidate``, so these names belong
    # to the standalone entry points the two-entry-point rule requires.
    "add_facet",
    "attach_facet",
    "remove_facet_attachment",
    "author_explanation",
    "add_explanation_revision",
    "designate_explanation",
    "read_facet_scope",
    # Mechanical detection. Two members rather than one, for the reason the read pair and the diff
    # pair are one each: recording a run and reading one back are different acts, and the read is the
    # one that has to answer with the recorded order rather than with whatever order the rows happen
    # to come back in. Neither member can carry a conclusion, and neither mints a new gate.
    "record_detection_run",
    "read_detection_run",
    # Requirement revisions. Two members rather than one, for the reason the read pair and the diff
    # pair are one each: recording a revision of a requirement obligation and reading the revisions
    # back are different acts with different remedies, and the read is the one that has to answer
    # with the derived views rather than with whatever rows the record happens to hold. Neither
    # member can carry a task status, a seat owner or a lifecycle gate, and neither mints an
    # approval: the owner's recorded acceptance is carried as data here and never produced.
    "record_requirement_revision",
    "read_requirement_revisions",
    # Citation bindings. Two members rather than one, for the reason the read pair and the detection
    # pair are one each: authoring a binding and reading a selected prose view's citation closure are
    # different acts, and the read is the one that has to answer with a per-state enumeration rather
    # than with whatever rows happen to come back. Neither member can carry a verdict, and neither
    # mints a new gate.
    "author_citation_binding",
    "read_citation_closure",
    # Family composition. One member, and deliberately not one per authored act: the edge, its
    # declared policy version, the family revision's owning route and its explanatory context are all
    # authored through the candidate batch's commands, whose refusals are restated as
    # ``change_candidate``. This member names the one act that is *not* a write -- following declared
    # composition edges under a versioned traversal policy -- because it is a different operation
    # from ``read_knowledge_scope``'s retrieval selection rather than a variant of it (R17 §7.3).
    "follow_family_composition",
    # Supporting records. Three members: two authored writes and one read. The two writes are
    # separate members rather than one because a caller branches on which authored act it performed
    # and each carries its own remedy -- recording a claim about what evidence covers is a different
    # act from recording that a command ran -- and the batch that carries either command restates its
    # refusals as ``change_candidate``. The read is its own member for the reason the facet read and
    # the detection read are: it is a question rather than an act, and its refusals are about the
    # snapshot rather than about a payload.
    "add_evidence_claim",
    "add_verification_observation",
    "read_evidence_scope",
    # Authored effects and change sets. One member, and it is the read: recording an effect claim, a
    # preservation claim, an unresolved question or a change set is the candidate batch's own
    # operation (``change_candidate``), because the requirement places those records on that one
    # write path rather than beside it. Reading the authored-effect scope is the act a caller asks
    # for by name, and the read is the one that has to answer with derived views -- authorship, the
    # shipped lifecycle state, computed membership and every unresolved reference verbatim -- rather
    # than with whatever rows the record group happens to hold.
    "read_effect_scope",
    # The registered review scope. Its own member for the same reason: constructing the §8 scope from
    # the declared snapshots, the recorded links and a declared policy is neither the retrieval
    # selection nor the composition traversal -- it consumes the second and never touches the first
    # (R16 §1.4). No refusal *code* is added with it: a construction refusal reuses the shipped codes.
    "construct_registered_scope",
    # The pipeline's own composition act. Its own member because it is neither the construction nor a
    # record read: it groups the facts a recorded run emitted, composes the five separated statuses and
    # routes the groups -- and it refuses a request that would make it report an owner's status for it,
    # which is a fact about the composition rather than about any record it read (R16 §4.1).
    "compose_family_integrity_report",
]

# The exact refusal vocabulary of the storage contract. Each member names a distinct
# observable failure, so a caller branches on the code rather than on message text.
KnowledgeRefusalCode = Literal[
    "invalid_payload",
    "unauthorized_scope",
    "unsupported_schema",
    "destination_occupied",
    "candidate_busy",
    "lock_capability_unavailable",
    "missing_expected_row",
    "stale_precondition",
    "duplicate_identity",
    "immutable_revision",
    "invalid_reference",
    "lineage_cycle",
    "relationship_constraint",
    "unknown_invariant",
    "unknown_family",
    "target_not_candidate",
    "promotion_not_supported",
    "no_change",
    # Snapshot publication. One member per observable failure point of the publication and
    # candidate-lifecycle contract, so a caller branches on the code rather than on prose.
    "selected_input_unavailable",
    "candidate_binding_changed",
    "candidate_snapshot_unpublished",
    "snapshot_incomplete",
    "destination_stale",
    "publication_failed",
    "publication_durability_unconfirmed",
    # Common-base merge. One member per observable failure point of the merge contract, so a
    # caller branches on the code rather than on prose. ``missing_expected_row``,
    # ``duplicate_identity``, ``relationship_constraint``, ``immutable_revision`` and
    # ``changeset_incomplete`` are shared with the write path where the failure is the same
    # fact; the members here are the ones that only a merge can observe.
    "common_base_unavailable",
    "common_base_ambiguous",
    "common_base_mismatch",
    "schema_mismatch",
    "missing_required_table",
    "conflicting_values",
    "duplicate_relationship",
    "delete_reference_conflict",
    "immutable_revision_changed",
    "session_unavailable",
    "changeset_incomplete",
    "changeset_postcondition_failed",
    # Portable logical export and import. ``unsupported_schema``, ``duplicate_identity``,
    # ``invalid_reference`` and ``destination_occupied`` are shared with the paths where the
    # failure is the same fact; ``invalid_export`` is the one member that only a portable artifact
    # can reach, because it is the only input that can be *malformed as a document* -- an unknown
    # field, a missing manifest key, a repeated JSON key or a value the declared column type
    # cannot hold is a defect of the artifact, not of an authored payload.
    "invalid_export",
    # Selective recorded-scope read. ``unsupported_schema``, ``selected_input_unavailable``,
    # ``stale_precondition`` and ``candidate_snapshot_unpublished`` are shared with the paths where
    # the failure is the same fact. The members below are the ones only a bounded, continuable
    # selection can reach: a seed that names nothing recorded, a one-item page budget that cannot
    # hold even one item, a continuation presented against another snapshot, and a selection that
    # reached its declared execution bound. Each is a distinct fact a caller acts on differently,
    # so none of them is folded into a neighbouring code.
    "selector_absent",
    "registration_absent",
    "page_budget_too_small",
    "continuation_binding_mismatch",
    "snapshot_unavailable",
    "selection_incomplete",
    # Mechanical detection. The one member only a detection write can reach: a signal or a run is a
    # measurement of an already-existing dataset, so a write that would enter that dataset's own
    # measurement transaction -- and move the identity of the dataset it just digested -- is refused
    # with its own name rather than folded into a neighbouring storage code. Requirement 7.1 states
    # the refusal, and ``detection_self_reference`` is the fact a caller branches on.
    "detection_self_reference",
    # The cutover (MIK-R37 rule 3). A converted memory tree's knowledge is text written by the
    # curator file writer (MIK-R12); the database file it still holds is frozen in place until
    # MIK-R26 removes it, so a database write or publication into that tree is refused by name.
    "database_frozen",
]


class KnowledgeRefusal(KnowledgeModel):
    """One refusal with its code, offending record and concrete next action."""

    code: KnowledgeRefusalCode
    operation: KnowledgeOperation
    detail: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
    table: str | None = Field(default=None, max_length=LABEL_MAX_LENGTH)
    record_id: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    expected: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    observed: str | None = Field(default=None, max_length=REFERENCE_MAX_LENGTH)
    next_action: str = Field(min_length=1, max_length=PROSE_MAX_LENGTH)
