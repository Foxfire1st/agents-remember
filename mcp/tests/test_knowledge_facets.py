"""The authored judgment vocabulary: eight closed subtypes, their writes, and the explanation pair.

Every case here protects one clause group of ``KS-R11@v1``, in the order the packet's
`## Required Behavior` states them: the closed subtype vocabulary and its payload seam, authorship
and lifecycle as stored data, the typed attachment contract, the decision's supersession edge, the
independently editable explanation, the candidate write path, the registered generation, and the
read projection beside the shipped selection.

Two properties are load-bearing enough to be worth naming before the cases:

* **The payload seam is the only payload decision point.** An unknown subtype, an undeclared field,
  a missing meaning, a payload that carries its own provenance and prose past the bound are all the
  same shipped ``invalid_payload`` refusal, and every one of them leaves the dataset byte-identical.
* **Nothing here is derived.** An attachment's endpoint, a record's governing route, a supersession
  edge and an explanation's designation are stored facts with an operation behind them; the cases
  assert the absent state is reported as absent rather than defaulted.

**Why a case carries a loop rather than a parametrization.** The unit population's declared case
ceiling had twenty slots left when this leaf landed, and the repository's own record forbids a KS
leaf from raising it (``pyproject.toml``'s budget note: "no KS leaf may raise that ceiling"). The
per-subtype and per-endpoint-kind variants are therefore driven *inside* the case that owns their
property, with each assertion naming the subtype or kind it is about, so a failure still says which
shape broke. What that costs is independent failure attribution between variants of one property;
what it preserves is every clause, and a population that runs at all -- an over-budget population
raises ``UsageError`` and executes zero tests.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any, get_args
from uuid import uuid4

import apsw
import pytest
from agents_remember.application.knowledge import (
    open_admitted_knowledge_store,
    write_authorship,
)
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge import (
    facet_records,
    facets,
    routes,
)
from agents_remember.memory.knowledge.batch_commands import _INSERTING_KINDS
from agents_remember.memory.knowledge.batch_preconditions import _TARGET_CHECKS
from agents_remember.memory.knowledge.candidate_records import EFFECT_ONLY_WRITABLE_TABLES
from agents_remember.memory.knowledge.facet_records import FacetEnvelopeDraft, facet_record_row
from agents_remember.memory.knowledge.facets import _STEPS
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.record_envelope import (
    CHANGE_SET_RECORD_KINDS,
    CITATION_BINDING_RECORD_KINDS,
    DETECTION_RECORD_KINDS,
    EFFECT_MEMBER_RECORD_KINDS,
    EVIDENCE_RECORD_KINDS,
    FACET_RECORD_KINDS,
    KIND_SCHEMAS,
    PAYLOAD_MODELS,
    REQUIREMENT_RECORD_KINDS,
    validate_facet_payload,
)
from agents_remember.memory.knowledge.routes import RouteDraft
from agents_remember.memory.knowledge.schema_generations import (
    CURRENT_GENERATION,
    GENERATION_1,
    GENERATION_1_FINGERPRINT,
    GENERATION_2,
    GENERATION_3,
    GENERATIONS,
    create_schema_statements,
)
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.candidate import (
    ExpectedRecord,
    MutableRecordTable,
)
from agents_remember.models.knowledge.composition import (
    COMPOSITION_COMMAND_KINDS,
    COMPOSITION_WRITABLE_TABLES,
)
from agents_remember.models.knowledge.effect import EFFECT_COMMAND_KINDS
from agents_remember.models.knowledge.evidence import (
    EVIDENCE_COMMAND_KINDS,
    EVIDENCE_WRITABLE_TABLES,
)
from agents_remember.models.knowledge.facet import (
    ATTACHMENT_ENDPOINT_KINDS,
    EXPLANATION_SUBJECT_KINDS,
    FACET_COMMAND_KINDS,
    FACET_KINDS,
    FACET_RECORD_SCHEMAS,
    FACET_WRITABLE_TABLES,
    AddExplanationRevision,
    AttachFacet,
    AuthorExplanation,
    DecisionPayload,
    DesignateExplanation,
    FacetWriteRequest,
    FacetWriteResult,
    FamilyJointGuaranteeSubject,
    InvariantRevisionEndpoint,
    InvariantStatementSubject,
    RemoveFacetAttachment,
    facet_payload_model,
)
from agents_remember.models.knowledge.facet_read import (
    ExplanationSubjectSeed,
    FacetReadSeed,
    FacetRecordSeed,
)
from agents_remember.models.knowledge.result import (
    InvariantRequest,
    KnowledgeRefusal,
)
from pydantic import BaseModel, ValidationError

pytestmark = pytest.mark.evidence_unit

import hashlib

from agents_remember.application.knowledge_facets import read_facet_scope
from agents_remember.application.knowledge_read import open_read_context, read_knowledge_scope
from agents_remember.memory.knowledge.facet_read import (
    FacetSelectionIncomplete,
    FacetSelectionQuery,
    select_facet_scope,
)
from agents_remember.models.knowledge.facet_read import (
    FACET_SELECTION_ITEM_LIMIT,
    FACET_SELECTION_POLICY_VERSION,
    FacetReadRequest,
    facet_item_sort_key,
)
from agents_remember.models.knowledge.read import (
    KNOWLEDGE_READ_POLICY_VERSION,
    MAX_PAGE_ITEMS,
    SELECTION_ITEM_LIMIT,
    InvariantRevisionSeed,
    ItemKind,
    KnowledgeReadBudget,
    KnowledgeReadRequest,
    PathSeed,
)
from facet_test_support import (
    AUTHORIZATION,
    ENDPOINT_NOUNS,
    FIXTURE_PATH,
    MINIMAL_PAYLOADS,
    PRE_LEAF_DATASET_DIGEST,
    PRE_LEAF_GENERATION_2_FINGERPRINT,
    PRE_LEAF_ITEM_LIMIT,
    PRE_LEAF_MAX_PAGE_ITEMS,
    PRE_LEAF_PAGE_DIGEST,
    PRE_LEAF_RESULT_DIGEST,
    PRE_LEAF_SELECTED_ITEMS,
    REPOSITORY_ID,
    SHIPPED_COMMAND_KINDS,
    apply_commands,
    attach_facet,
    author_explanation,
    build_admitted_candidate,
    build_recorded_fixture,
    build_recorded_generation_2_dataset,
    command_kinds,
    declared_subject_kinds,
    endpoint_of_kind,
    endpoints_for_subject,
    facet_command,
    insert_family_revision,
    member_models,
    page_item,
    payload_kinds,
    read_facet,
    resolve_context,
    seed_subject,
    store_facet,
    table_counts,
    write_facet,
)


@pytest.fixture
def admitted(tmp_path: Path) -> tuple[Any, Any]:
    """One admitted candidate, ready for a store to be opened on it."""

    return build_admitted_candidate(tmp_path / "facets")


pytestmark = pytest.mark.evidence_unit


def _present[Present](value: Present | None) -> Present:
    """Return one recorded value a case depends on, refusing an absent one in the same breath.

    The store's readers answer ``X | None`` because a missing row is a fact a caller branches on. A
    case that has already established the row exists needs one place to say so, and naming it here
    keeps the narrowing out of every assertion that follows.
    """

    assert value is not None
    return value


def _refused_payload(value: BaseModel | KnowledgeRefusal) -> KnowledgeRefusal:
    """Return the refusal one validation seam produced, refusing a value that is not one.

    The seam answers ``BaseModel | KnowledgeRefusal`` because that is what a caller branches on. It
    is stated in this module's own source rather than borrowed from the evidence lane's support
    module, which is a governed artifact with a declared consumer list this case is not part of.
    """

    assert isinstance(value, KnowledgeRefusal), value
    return value


# ---------------------------------------------------------------------------
# Requirements 1 and 2.2: the closed, structurally validated vocabulary and its payload seam.


def test_the_seam_registry_is_exactly_the_eight_declared_subtypes() -> None:
    """Requirements 1.1, 1.2, 6.6 and 6.7: the vocabulary is closed -- the eight subtypes, the two
    explanation subject kinds, and the six tables a facet command may write -- and each subtype is
    reached through a discriminator over one frozen shape.

    RE-SCOPED for ``KS-R14@v1``, again for ``KS-R19@v1``, again for ``KS-R18@v1`` and again for
    ``KS-R12@v1``. The registry this asserts against is the *shared* envelope seam, and
    ``260915-KS-L10``'s own docstring named the concrete non-facet knowledge categories as later
    leaves; ``260915-KS-L14`` registers the mechanical-detection pair through it,
    ``260915-KS-L19`` registers the requirement-revision kind, ``260915-KS-L18`` registers the
    citation-binding kind and ``260915-KS-L12`` registers the supporting-record pair. The claim is
    unchanged in strength and is stated as the union it now is: the kinds *this vocabulary* admits are
    exactly the eight (``FACET_RECORD_KINDS``), and the seam's whole membership is exactly the union of
    the groups the registry declares -- the internal conformance kind, the eight facet kinds, the two
    detection kinds, the requirement-revision kinds, the citation-binding kind and the two
    supporting-record kinds -- so a further group still cannot be admitted without this line changing,
    with the facet kinds' admissible schemas still exactly their declared ones. The membership is
    written as a union of the groups' own derived sets rather than as a literal, so a later record
    group's registration is answered by that group's constant instead of by an edit here.

    RE-SCOPED again for ``KS-R13@v1``, which registers the authored-effect group -- three member
    kinds and the change-set kind -- through the same seam. Two edits, both strengthening rather than
    weakening. First, the group's own two derived sets (``EFFECT_MEMBER_RECORD_KINDS`` and
    ``CHANGE_SET_RECORD_KINDS``) join the union as terms, so the vocabulary answers for them exactly
    as it answers for every earlier group. Second, the enumerated-union line became a *union over a
    tuple of groups* with two facts beside it that the enumeration could only imply: every named group
    must be a **subset of the registry** -- otherwise a group constant naming an unregistered kind
    would be silently absorbed by the equality -- and the groups must be **pairwise disjoint**, which
    is what makes the equality a partition rather than a list a later leaf could widen by overlapping
    an existing group. Nothing was deleted: the eight facet kinds are still exactly
    ``FACET_RECORD_KINDS``, every group is still named, and a ninth registration still cannot be
    admitted without this case changing.
    """

    assert len(FACET_KINDS) == 8
    assert payload_kinds() == set(FACET_KINDS)
    assert set(FACET_RECORD_SCHEMAS) == set(FACET_KINDS)
    assert frozenset(FACET_KINDS) == FACET_RECORD_KINDS
    groups = (
        # The facet vocabulary's own eight kinds, this leaf's group.
        set(FACET_KINDS),
        # The registry's own conformance kind and the mechanical-detection pair, both registered by
        # ``260915-KS-L14``.
        {"internal_conformance"},
        set(DETECTION_RECORD_KINDS),
        # The requirement-revision kind, registered by ``260915-KS-L19``.
        set(REQUIREMENT_RECORD_KINDS),
        # The citation-binding kind, registered by ``260915-KS-L18``.
        set(CITATION_BINDING_RECORD_KINDS),
        # The supporting-record pair, registered by ``260915-KS-L12``.
        set(EVIDENCE_RECORD_KINDS),
        # The authored-effect record group, registered by ``260915-KS-L13``: three member kinds and
        # the change set that composes them. The two sets are the group's own derived declarations
        # rather than a literal, so a fourth member kind or a second change-set kind is answered by
        # the vocabulary instead of by an edit here.
        set(EFFECT_MEMBER_RECORD_KINDS),
        set(CHANGE_SET_RECORD_KINDS),
    )
    # Every named group is a subset of the registry: a group constant that named a kind the registry
    # does not hold would otherwise be absorbed by the equality below without failing.
    for group in groups:
        assert group <= set(KIND_SCHEMAS), sorted(group - set(KIND_SCHEMAS))
    # RE-SCOPED for ``KS-R13@v1``, and stated as the property the enumerated-union line was standing
    # for: the declared groups are pairwise disjoint, so the union above is a *partition* of the
    # registry rather than a list that a later leaf can widen by adding an overlapping entry, and a
    # further group still cannot be admitted without this line changing.
    for index, group in enumerate(groups):
        for other in groups[index + 1 :]:
            assert not group & other, (sorted(group), sorted(other))
    assert set(KIND_SCHEMAS) == set().union(*groups)
    for kind in FACET_KINDS:
        assert KIND_SCHEMAS[kind] == frozenset({FACET_RECORD_SCHEMAS[kind]}), kind
    for kind in FACET_KINDS:
        model = facet_payload_model(kind)
        assert model is not None, kind
        assert model.model_config.get("extra") == "forbid", kind
        assert model.model_config.get("frozen") is True, kind
        assert PAYLOAD_MODELS[(kind, FACET_RECORD_SCHEMAS[kind])] is model, kind

    # The envelope row a facet is stored as, derived from its kind rather than supplied twice.
    row = facet_record_row(
        FacetEnvelopeDraft(
            record_id=str(uuid4()),
            facet_kind="decision",
            authority_home="agents-remember",
            lifecycle="proposed",
        ),
        write_authorship(actor_ref="agent:x", authorization_ref=AUTHORIZATION),
    )
    assert row[2] == "agents-remember"
    assert row[3] == "proposed"
    assert row[4] is None
    assert row[5] == "facet-decision/v1"
    assert set(EXPLANATION_SUBJECT_KINDS) == {"invariant_revision", "family_revision"}
    assert declared_subject_kinds() == set(EXPLANATION_SUBJECT_KINDS)
    assert {member.__name__ for member in member_models(FacetReadSeed)} == {
        "FacetRecordSeed",
        "ExplanationSubjectSeed",
    }
    assert FACET_WRITABLE_TABLES == (
        "knowledge_record",
        "record_revision",
        "facet_attachment",
        "facet_decision_supersession",
        "explanation",
        "explanation_revision",
    )


def test_every_subtype_refuses_a_bad_shape_a_missing_meaning_and_its_own_provenance() -> None:
    """Requirements 1.1 to 1.5 and 2.2, driven across all eight subtypes.

    Each assertion names the subtype it is about, so a failure says which shape broke even though
    the variants share one case. The ninth subtype is checked at the end of the same walk: it is the
    same refusal reached through the same seam, and it must not be storable as a generic facet.
    """

    for facet_kind in FACET_KINDS:
        minimal = dict(MINIMAL_PAYLOADS[facet_kind])
        validated = validate_facet_payload(facet_kind, minimal)
        assert validated.model_dump(mode="json")["facet_kind"] == facet_kind, facet_kind

        undeclared = _refused_payload(
            validate_facet_payload(facet_kind, minimal | {"undeclared_note": "x"})
        )
        assert undeclared.code == "invalid_payload", facet_kind
        assert "undeclared_note" in undeclared.detail, facet_kind

        omitted = dict(minimal)
        dropped = next(iter(omitted))
        omitted.pop(dropped)
        missing = _refused_payload(validate_facet_payload(facet_kind, omitted, record_id="r-1"))
        assert missing.code == "invalid_payload", (facet_kind, dropped)
        assert missing.record_id == "r-1", facet_kind

        for substituted in ("actor_ref", "authorization_ref", "recorded_at"):
            refused = _refused_payload(
                validate_facet_payload(facet_kind, minimal | {substituted: "caller-supplied"})
            )
            assert refused.code == "invalid_payload", (facet_kind, substituted)
            assert substituted in refused.detail, (facet_kind, substituted)

        prose = next(
            name
            for name, field in type(validated).model_fields.items()
            if field.annotation is str and name != "facet_kind"
        )
        over = _refused_payload(validate_facet_payload(facet_kind, minimal | {prose: "x" * 20001}))
        assert over.code == "invalid_payload", (facet_kind, prose)

        # Requirement 1.5: guidance may state an interpretation, never a verdict.
        for forbidden in (
            "assessment",
            "verdict",
            "endorsement",
            "confidence",
            "severity",
            "score",
        ):
            assert forbidden not in type(validated).model_fields, (facet_kind, forbidden)

    ninth = _refused_payload(
        validate_facet_payload("retrospective", {"text": "we should have"}, record_id="r-9")
    )
    assert ninth.code == "invalid_payload"
    assert ninth.observed == "retrospective"
    assert ninth.expected == " | ".join(FACET_KINDS)


def test_an_unknown_ninth_subtype_is_refused_and_never_stored_as_a_generic_facet(
    admitted: Any,
) -> None:
    """Requirement 1.1 and example 7: no generic facet, at the seam and through the write path."""

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        before = table_counts(store)
        refused = write_facet(
            store,
            destination,
            facet_command(facet_kind="retrospective", payload={"text": "x"}),
        )
        assert refused.state == "refused"
        assert refused.refusal.code == "invalid_payload"
        assert table_counts(store) == before


# ---------------------------------------------------------------------------
# Requirements 2 and 3: authorship, the decider, and lifecycle as stored data.


def test_a_facets_authorship_lifecycle_and_receipt_are_stored_data_with_no_verdict(
    admitted: Any,
) -> None:
    """Requirements 2.2, 2.3, 3.1 and 3.4: the decider is content, the receipt certifies nothing,
    and the lifecycle is stored data with no default and no flag.
    """

    forbidden = {"approval", "approved", "verdict", "confidence", "severity", "score", "endorsed"}
    assert forbidden.isdisjoint(FacetWriteResult.model_fields)

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        record_id, revision_id = store_facet(store, destination)
        written = write_facet(store, destination, facet_command())
        assert set(written.model_dump(mode="json")) == {
            "state",
            "repository_id",
            "written",
            "refusal",
        }
        assert all(
            set(entry) == {"state", "table", "record_id", "digest"}
            for entry in written.model_dump(mode="json")["written"]
        )
        page = read_facet(store, destination, FacetRecordSeed(record_id=record_id))
        record = page_item(page, "facet_record").facet
        assert record.lifecycle == "proposed"
        assert record.authority_home == "agents-remember"
        revision = page_item(page, "facet_revision").revision
        assert revision.revision_id == revision_id
        assert revision.payload["decider"] == "architect-seat"
        assert revision.provenance.actor_ref == "agent:facets"
        assert revision.provenance.operation_id == destination.authorship.operation_id
        assert revision.provenance.actor_ref != revision.payload["decider"]
        rendered = canonical_json_bytes(page.model_dump(mode="json")).decode("utf-8")
        for word in ("approval", "endorsement", "confidence", "severity", "score", "latest"):
            assert word not in rendered, word


def test_accepted_origin_data_is_refused_at_both_entry_points(admitted: Any) -> None:
    """Requirement 3.2: proposed origin data only, with the shipped ``promotion_not_supported``."""

    destination, _authorship = admitted
    accepted = facet_command(state_at_origin="accepted", acceptance_ref="authority:someone")
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        before = table_counts(store)
        standalone = write_facet(store, destination, accepted)
        assert standalone.state == "refused"
        assert standalone.refusal.code == "promotion_not_supported"
        assert table_counts(store) == before

        batched = apply_commands(destination, resolve_context(destination), accepted)
        assert batched.state == "refused"
        assert batched.refusal.code == "promotion_not_supported"
        assert batched.before == batched.after
        assert table_counts(store) == before


# ---------------------------------------------------------------------------
# Requirement 4: attachment is an exact, typed reference.


def test_every_endpoint_kind_is_a_checked_group_that_refuses_a_missing_target(
    admitted: Any,
) -> None:
    """Requirements 4.1, 4.3 and 4.4, across all four endpoint kinds and in both directions.

    The stored row carries one foreign-key column per kind and a constraint that the populated group
    matches the stored kind, so the endpoint is a fact of the table rather than a string, and the
    forbidden polymorphic shape has no column to land in; a target the build cannot find is refused
    with the endpoint kind and identity named, before any row is written.
    """

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        _invariant_id, revision_id = seed_subject(store, destination, destination.authorship)
        endpoints = endpoints_for_subject(store, destination, revision_id)
        record_id, facet_revision_id = store_facet(store, destination)
        for kind, endpoint in endpoints.items():
            attachment_id = attach_facet(store, destination, facet_revision_id, endpoint)
            page = read_facet(store, destination, FacetRecordSeed(record_id=record_id))
            assert page.state == "page", (kind, page.refusal)
            stored = next(
                item.attachment
                for item in page.page.items
                if item.kind == "facet_attachment"
                and item.attachment.attachment_id == attachment_id
            )
            assert stored.endpoint == endpoint, kind
            assert stored.facet_revision_id == facet_revision_id, kind
        assert page.page.counts.attachments == len(ATTACHMENT_ENDPOINT_KINDS)

        columns = GENERATION_3.columns["facet_attachment"]
        assert "endpoint_id" not in columns
        assert {"invariant_revision_id", "family_revision_id", "anchor_id", "claim_id"} <= set(
            columns
        )
        ddl = GENERATION_3.table_ddl["facet_attachment"]
        for kind in ATTACHMENT_ENDPOINT_KINDS:
            assert f"endpoint_kind = '{kind}'" in ddl, kind
        assert ddl.count("ON DELETE NO ACTION DEFERRABLE INITIALLY DEFERRED") == 6
        with pytest.raises(apsw.Error):
            store.write(
                "INSERT INTO facet_attachment (repository_id, attachment_id, facet_revision_id, "
                "endpoint_kind, invariant_revision_id, family_revision_id, anchor_id, claim_id, "
                "provenance) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    store.repository_id,
                    str(uuid4()),
                    facet_revision_id,
                    "invariant_revision",
                    revision_id,
                    revision_id,
                    None,
                    None,
                    "{}",
                ),
            )

        # The same walk in the failing direction: every kind refuses a target it cannot find,
        # before any row is written, and names the endpoint kind and identity it was given.
        _record_id, facet_revision_id = store_facet(store, destination)
        for kind in ATTACHMENT_ENDPOINT_KINDS:
            before = table_counts(store)
            orphan = str(uuid4())
            refused = write_facet(
                store,
                destination,
                AttachFacet(
                    attachment_id=str(uuid4()),
                    facet_revision_id=facet_revision_id,
                    endpoint=endpoint_of_kind(
                        kind,
                        revision_id=orphan,
                        family_revision_id=orphan,
                        anchor_id=orphan,
                        claim_id=orphan,
                    ),
                ),
            )
            assert refused.state == "refused", kind
            assert refused.refusal.code == "invalid_reference", kind
            assert refused.refusal.record_id == orphan, kind
            assert refused.refusal.expected == ENDPOINT_NOUNS[kind], kind
            assert table_counts(store) == before, kind


def test_removing_an_attachment_names_its_row_and_deletes_that_row_only(admitted: Any) -> None:
    """Requirement 4.6: the removal names the exact row, and nothing else is removed."""

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        _invariant_id, revision_id = seed_subject(store, destination, destination.authorship)
        record_id, facet_revision_id = store_facet(store, destination)
        attachment_id = attach_facet(
            store,
            destination,
            facet_revision_id,
            InvariantRevisionEndpoint(revision_id=revision_id),
        )
        digest = facet_records.attachment_endpoint_digest(store, attachment_id)
        assert digest is not None

        stale = write_facet(
            store,
            destination,
            RemoveFacetAttachment(attachment_id=attachment_id, expected_row_digest="0" * 64),
        )
        assert stale.state == "refused"
        assert stale.refusal.code == "stale_precondition"
        assert stale.refusal.expected == "0" * 64
        assert facet_records.attachment_endpoint_digest(store, attachment_id) == digest

        removed = write_facet(
            store,
            destination,
            RemoveFacetAttachment(attachment_id=attachment_id, expected_row_digest=digest),
        )
        assert removed.state == "applied"
        assert removed.written[0].state == "removed"
        assert facet_records.attachment_endpoint_digest(store, attachment_id) is None
        assert store.get_revision(revision_id) is not None
        assert facet_records.record_revision_content_digest(store, facet_revision_id) is not None
        assert facet_records.facet_record_digest(store, record_id) is not None


def test_an_unattached_facet_and_the_envelopes_route_association_are_explicit_states(
    admitted: Any,
) -> None:
    """Requirements 4.2 and 4.5: nothing is inferred, and ungoverned is not a default."""

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        _invariant_id, revision_id = seed_subject(store, destination, destination.authorship)
        ungoverned_id, _ungoverned_revision = store_facet(store, destination)
        page = read_facet(store, destination, FacetRecordSeed(record_id=ungoverned_id))
        assert page.page.counts.attachments == 0
        assert [item.kind for item in page.page.items] == ["facet_record", "facet_revision"]
        assert page_item(page, "facet_record").facet.governing_route_id is None
        # The subject revision exists and is named nowhere: an attachment is authored, never found.
        assert store.get_revision(revision_id) is not None

        route_id = str(uuid4())
        authored = routes.author_route(
            store.connection,
            store.repository_id,
            RouteDraft(route_id=route_id, path="src/governed.py"),
            destination.authorship,
        )
        assert isinstance(authored, str)
        governed_id, _governed_revision = store_facet(
            store, destination, governing_route_id=route_id
        )
        governed = read_facet(store, destination, FacetRecordSeed(record_id=governed_id))
        assert page_item(governed, "facet_record").facet.governing_route_id == route_id

        missing_route = write_facet(
            store,
            destination,
            facet_command(record_id=str(uuid4()), governing_route_id=str(uuid4())),
        )
        assert missing_route.state == "refused"
        assert missing_route.refusal.code == "missing_expected_row"
    assert "facet_route" not in CURRENT_GENERATION.tables
    assert "governing_route_id" in GENERATION_2.columns["knowledge_record"]


# ---------------------------------------------------------------------------
# Requirement 5: the decision record and its supersession edge.


def test_a_superseding_decision_authors_an_edge_and_the_superseded_one_is_retained(
    admitted: Any,
) -> None:
    """Requirements 5.1 to 5.4: recorded, retaining, and no second governed-artifact copy."""

    assert set(DecisionPayload.model_fields) == {"facet_kind", "outcome", "reason", "decider"}

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        _invariant_id, revision_id = seed_subject(store, destination, destination.authorship)
        first_id, first_revision = store_facet(store, destination)
        attachment_id = attach_facet(
            store,
            destination,
            first_revision,
            InvariantRevisionEndpoint(revision_id=revision_id),
        )
        record_before = facet_records.facet_record_digest(store, first_id)
        revision_before = facet_records.record_revision_content_digest(store, first_revision)
        attachment_before = facet_records.attachment_endpoint_digest(store, attachment_id)

        second_id, second_revision = store_facet(
            store, destination, supersedes_revision_id=first_revision
        )
        assert facet_records.facet_record_digest(store, first_id) == record_before
        assert (
            facet_records.record_revision_content_digest(store, first_revision) == revision_before
        )
        assert facet_records.attachment_endpoint_digest(store, attachment_id) == attachment_before

        page = read_facet(store, destination, FacetRecordSeed(record_id=first_id))
        assert page.state == "page"
        edge = page_item(page, "decision_supersession").supersession
        assert (edge.superseding_revision_id, edge.superseded_revision_id) == (
            second_revision,
            first_revision,
        )
        assert page_item(page, "facet_revision").revision.payload == dict(
            MINIMAL_PAYLOADS["decision"]
        ) | {"facet_kind": "decision"}
        assert page_item(page, "facet_attachment").attachment.attachment_id == attachment_id
        assert page_item(page, "facet_record").facet.record_id == first_id
        assert (
            read_facet(
                store, destination, FacetRecordSeed(record_id=second_id)
            ).page.counts.items_total
            == 3
        )


def test_a_supersession_cycle_rolls_back_and_a_sealed_decision_cannot_be_rewritten(
    admitted: Any,
) -> None:
    """Requirements 5.2 and 5.3: the whole batch rolls back, and the rows stay sealed."""

    destination, _authorship = admitted
    first, second, third = (str(uuid4()) for _ in range(3))
    first_revision, second_revision, third_revision = (str(uuid4()) for _ in range(3))
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        before = table_counts(store)
        outcome = apply_commands(
            destination,
            resolve_context(destination),
            facet_command(
                record_id=first, revision_id=first_revision, supersedes_revision_id=third_revision
            ),
            facet_command(
                record_id=second,
                revision_id=second_revision,
                supersedes_revision_id=first_revision,
            ),
            facet_command(
                record_id=third,
                revision_id=third_revision,
                supersedes_revision_id=second_revision,
            ),
        )
        assert outcome.state == "refused"
        assert outcome.refusal.code == "lineage_cycle"
        assert outcome.refusal.table == "facet_decision_supersession"
        assert set(outcome.refusal.observed.split(", ")) == {
            first_revision,
            second_revision,
            third_revision,
        }
        assert table_counts(store) == before

        # A non-decision supersession is not expressive at all, and a non-decision target is refused.
        with pytest.raises(ValidationError, match="supersedes nothing"):
            facet_command(
                facet_kind="assumption",
                payload=dict(MINIMAL_PAYLOADS["assumption"]),
                supersedes_revision_id=third_revision,
            )
        assumption_revision = store_facet(store, destination, facet_kind="assumption")[1]
        wrong_kind = write_facet(
            store,
            destination,
            facet_command(supersedes_revision_id=assumption_revision),
        )
        assert wrong_kind.state == "refused"
        assert wrong_kind.refusal.code == "invalid_reference"
        assert wrong_kind.refusal.expected == "decision revision"

        stored_record, stored_revision = store_facet(store, destination)
        _superseding_id, superseding_revision = store_facet(
            store, destination, supersedes_revision_id=stored_revision
        )
        for statement, parameters in (
            (
                "UPDATE record_revision SET payload = ? WHERE repository_id = ? AND revision_id = ?",
                ("{}", store.repository_id, stored_revision),
            ),
            (
                "DELETE FROM record_revision WHERE repository_id = ? AND revision_id = ?",
                (store.repository_id, stored_revision),
            ),
            (
                "DELETE FROM facet_decision_supersession WHERE repository_id = ? AND "
                "superseding_revision_id = ?",
                (store.repository_id, superseding_revision),
            ),
        ):
            with pytest.raises(apsw.Error, match="immutable_revision"):
                store.write(statement, parameters)
        # The envelope cannot be deleted either: its revisions reference it under a deferred key.
        with pytest.raises(apsw.Error):
            store.write(
                "DELETE FROM knowledge_record WHERE repository_id = ? AND record_id = ?",
                (store.repository_id, stored_record),
            )
        assert facet_records.record_revision_content_digest(store, stored_revision) is not None


# ---------------------------------------------------------------------------
# Requirement 6: the independently editable explanation.


def test_an_explanation_is_separable_and_editing_it_never_rewrites_the_statement(
    admitted: Any,
) -> None:
    """Requirements 6.1 to 6.5 in one act, with the exact digests as the acceptance evidence."""

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        invariant_id, revision_id = seed_subject(store, destination, destination.authorship)
        before = store.get_revision(revision_id)
        assert before is not None
        row_digest_before = _present(store.get_invariant(invariant_id)).row_digest
        payload_digest_before = before.revision.payload_digest

        subject = InvariantStatementSubject(invariant_id=invariant_id, revision_id=revision_id)
        explanation_id, first_revision = author_explanation(store, destination, subject)
        successor = str(uuid4())
        assert (
            write_facet(
                store,
                destination,
                AddExplanationRevision(
                    explanation_id=explanation_id,
                    revision_id=successor,
                    predecessor_revision_id=first_revision,
                    body="why this wording, revised",
                ),
            ).state
            == "applied"
        )

        stale = write_facet(
            store,
            destination,
            DesignateExplanation(
                explanation_id=explanation_id,
                revision_id=first_revision,
                expected_row_digest="0" * 64,
            ),
        )
        assert stale.state == "refused"
        assert _present(stale.refusal).code == "stale_precondition"
        wrong_revision = write_facet(
            store,
            destination,
            DesignateExplanation(
                explanation_id=explanation_id,
                revision_id=str(uuid4()),
                expected_row_digest=_present(
                    facet_records.explanation_record_digest(store, explanation_id)
                ),
            ),
        )
        assert wrong_revision.state == "refused"
        assert _present(wrong_revision.refusal).code == "invalid_reference"

        # The recorded designation is the FIRST revision even though a successor exists: a response
        # reports what the record stores rather than the newest revision.
        designated = write_facet(
            store,
            destination,
            DesignateExplanation(
                explanation_id=explanation_id,
                revision_id=first_revision,
                expected_row_digest=_present(
                    facet_records.explanation_record_digest(store, explanation_id)
                ),
            ),
        )
        assert designated.state == "applied"

        after = store.get_revision(revision_id)
        assert after is not None
        assert after.revision.payload_digest == payload_digest_before
        assert after.revision.statement == before.revision.statement
        assert after.revision.conditions == before.revision.conditions
        assert _present(store.get_invariant(invariant_id)).row_digest == row_digest_before
        assert "explanation" not in GENERATION_1.columns["invariant_revision"]

        page = read_facet(store, destination, ExplanationSubjectSeed(subject=subject))
        record = page_item(page, "explanation").explanation
        assert record.current_revision_id == first_revision
        assert record.subject == subject
        assert sorted(
            item.revision.revision_id
            for item in page.page.items
            if item.kind == "explanation_revision"
        ) == sorted([first_revision, successor])
        rendered = canonical_json_bytes(page.model_dump(mode="json")).decode("utf-8")
        assert "latest" not in rendered
        assert "newest" not in rendered

        # Requirement 6.6: the subject set is closed at two statement kinds, so a family revision of
        # another identity is refused with the endpoint kind and identity named.
        family_revision = insert_family_revision(store, destination, revision_id)
        missing = write_facet(
            store,
            destination,
            AuthorExplanation(
                explanation_id=str(uuid4()),
                revision_id=str(uuid4()),
                subject=FamilyJointGuaranteeSubject(
                    family_id=str(uuid4()), revision_id=family_revision
                ),
                body="why the guarantee reads this way",
            ),
        )
        assert missing.state == "refused"
        assert missing.refusal.code == "invalid_reference"
        assert missing.refusal.record_id == family_revision
        # Requirement 6.7: no facet command writes a statement or its conditions, so a condition
        # can never be moved out of a statement and into an explanation.
        assert facet_records.explanation_revision_payload_digest(store, successor) is not None


def test_the_facet_commands_join_the_closed_union_and_its_dispatch_tables() -> None:
    """Requirements 7.3 and 7.5: one union, one set of dispatch tables, one set of tables.

    RE-SCOPED for ``KS-R17@v1`` and again for ``KS-R12@v1``. This case's protected property is that the
    union is *closed* over the declarations that are in it and that every dispatch table agrees with
    the union -- not that the union holds exactly eighteen members. The composition generation
    (``KS-R17@v1``) appended four commands and six record tables beside this leaf's six and six, and the
    supporting-record generation (``KS-R12@v1``) appends two more commands and five more record tables,
    so the assertions that spelled the membership as one leaf's own sum are replaced by the fact they
    stood in for: the union is exactly the shipped kinds plus each record group's own declaration, and
    the writable-table literal is exactly the shipped seven plus every group's declared set. The
    stronger half is unchanged and still checked -- ``_TARGET_CHECKS`` is exactly the union, so a
    command added without a target check still fails here rather than at a caller's expense.

    RE-SCOPED again for ``KS-R13@v1``, which appends four authored-effect commands. The command
    equality gains the group's own ``EFFECT_COMMAND_KINDS`` as a term rather than restating its four
    literals, so a fifth command in that group is answered by the vocabulary. The writable-table
    equality gains ``EFFECT_ONLY_WRITABLE_TABLES`` as a term *and* an assertion that the term is
    empty: the group's commands write the two envelope tables and no table of their own -- the
    succession edge a change set declares is written only as part of the aggregate that owns it -- so
    naming the term keeps the equality exactly as strong as before while making the group's shape a
    checked fact instead of an absence a reader has to notice. Nothing was deleted, no case was
    weakened, and ``set(_TARGET_CHECKS) == kinds`` still holds the union closed.
    """

    # RE-SCOPED for ``KS-R13@v1``, and stated as the property the 18-kind literal was standing in
    # for: the union is exactly the *declared* command groups, and every kind it declares has a
    # target check. A literal count cannot catch a kind added to one group but not to the dispatch
    # table; the set equality below can, and it keeps catching it for the next group too.
    kinds = command_kinds()
    assert kinds >= SHIPPED_COMMAND_KINDS
    assert kinds == (
        SHIPPED_COMMAND_KINDS
        | set(FACET_COMMAND_KINDS)
        | set(COMPOSITION_COMMAND_KINDS)
        | set(EVIDENCE_COMMAND_KINDS)
        | set(EFFECT_COMMAND_KINDS)
    )
    assert set(_TARGET_CHECKS) == kinds
    assert set(_STEPS) == set(FACET_COMMAND_KINDS)
    assert set(FACET_COMMAND_KINDS) <= (kinds | set(_INSERTING_KINDS))
    # The authored-effect group's own declared table set is the two envelope tables, and it is named
    # as its own term in the union below rather than absorbed into the literal: the group's commands
    # write ``knowledge_record`` and ``record_revision`` and no table of their own, so subtracting the
    # envelope names from it yields the empty set and the equality is unchanged in strength. A group
    # that does gain a table of its own therefore adds one term here instead of editing the literal.
    assert set(EFFECT_ONLY_WRITABLE_TABLES) == set()
    assert set(get_args(MutableRecordTable)) == (
        {
            "invariant",
            "invariant_revision",
            "family",
            "family_revision",
            "source_anchor",
            "family_member",
            "realization_claim",
        }
        | set(FACET_WRITABLE_TABLES)
        | set(EVIDENCE_WRITABLE_TABLES)
        | set(COMPOSITION_WRITABLE_TABLES)
        | set(EFFECT_ONLY_WRITABLE_TABLES)
    )


def test_the_two_entry_points_agree_and_a_refused_write_writes_nothing(admitted: Any) -> None:
    """Requirements 7.3 and 7.5: both entry points, all-or-nothing, and the integrity pass."""

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        seed_subject(store, destination, destination.authorship)
        standalone_id, _standalone_revision = store_facet(store, destination)
        batch_record = str(uuid4())
        outcome = apply_commands(
            destination, resolve_context(destination), facet_command(record_id=batch_record)
        )
        assert outcome.state == "changed", outcome.refusal
        assert [entry.table for entry in outcome.changed] == [
            "knowledge_record",
            "record_revision",
        ]

        # A batch whose second command is inadmissible leaves nothing of the first.
        before = table_counts(store)
        refused = apply_commands(
            destination,
            resolve_context(destination),
            facet_command(),
            facet_command(facet_kind="retrospective", payload={"text": "x"}),
        )
        assert refused.state == "refused"
        assert refused.refusal.code == "invalid_payload"
        assert refused.before == refused.after
        assert table_counts(store) == before

        # A command presented to another operation's entry point is refused rather than performed.
        wrong = facets.add_facet(
            store,
            FacetWriteRequest(
                repository_id=store.repository_id,
                provenance=destination.authorship,
                command=AttachFacet(
                    attachment_id=str(uuid4()),
                    facet_revision_id=str(uuid4()),
                    endpoint=InvariantRevisionEndpoint(revision_id=str(uuid4())),
                ),
            ),
        )
        assert wrong.state == "refused"
        assert wrong.refusal is not None
        assert wrong.refusal.code == "invalid_reference"
        assert (wrong.refusal.expected, wrong.refusal.observed) == ("add_facet", "attach_facet")

        # A batch expectation may name a facet row, so the aggregates join the integrity pass.
        revision_id = first_record_revision(store)
        digest = facet_records.record_revision_content_digest(store, revision_id)
        assert digest is not None
        stale = apply_commands(
            destination,
            resolve_context(destination),
            facet_command(),
            expected=(
                ExpectedRecord(
                    state="present",
                    table="record_revision",
                    record_id=revision_id,
                    digest="0" * 64,
                ),
            ),
        )
        assert stale.state == "refused"
        assert stale.refusal.code == "stale_precondition"
        assert standalone_id is not None


def first_record_revision(store: Any) -> str:
    return str(
        next(iter(store.connection.execute("SELECT revision_id FROM record_revision LIMIT 1")))[0]
    )


# ---------------------------------------------------------------------------
# Requirement 8: the generation this leaf registers.


def test_the_registered_generation_appends_only_and_the_preceding_ones_are_unchanged() -> None:
    """Requirements 8.1, 8.2, 8.3 and 8.5: the observed number, the prefix, and the idiom.

    RE-SCOPED for ``KS-R14@v1``, again for ``KS-R18@v1`` and again for ``KS-R12@v1``. This case's
    protected property is that *this leaf's* generation appends and that the generations before it are
    unchanged; the registry has since grown the mechanical-detection generation (``KS-R14@v1``), the
    citation-binding generation, the composition generation (``KS-R17@v1``) and the supporting-record
    generation (``KS-R12@v1``), so the assertions that spelled the registry's membership as a closed
    list are replaced by the fact they were standing in for. For ``KS-R18@v1`` that fact was
    *strengthened* rather than trimmed: the membership is checked as the structural property itself --
    one contiguous version sequence from 1 to the newest registered generation, each declaring its own
    ``ar-knowledge-sqlite/vN`` name, in register order -- instead of as a literal list. A hand-edited
    list of versions would still be green the day a generation was registered out of order or skipped;
    these assertions redden on exactly that, so re-scoping to the fact that now holds buys a property
    the enumeration never had. Generation 3 is still exactly this leaf's generation with the same
    schema name, fingerprint-bearing declarations and appended table list, and the created generation
    is the newest registered one rather than a pinned literal. Every generation-3-specific assertion
    below is unchanged.

    RE-SCOPED again for ``KS-R13@v1``, which registers generation 8 through the same registry. The
    property this case protects is unchanged -- *this leaf's* generation appends, every generation
    before it is untouched, and the created generation is the newest -- and the registry half is
    stated as two facts rather than one: the versions form the contiguous ascending sequence from 1
    to the newest registered generation, and every generation's table list begins with the whole of
    the previous generation's. The second is ``KS-R10@v1`` §1.3's additive rule checked at *every*
    boundary rather than only where this leaf's own generation sits, so a later generation that
    retyped, reordered or dropped an inherited table now reddens here. Nothing was deleted and the
    generation-3-specific assertions below are still unchanged.
    """

    # RE-SCOPED again for ``KS-R17@v1``, on the same reasoning the paragraph above records for
    # ``KS-R14@v1``: a registry membership spelled as a closed list of generations is a claim about how
    # many generations happen to exist, not about this leaf's generation. What is asserted instead is
    # what the closed list was standing in for -- the registry is the generations in *order*, each
    # one's schema name is its own version's name, and generation 3 is still exactly this leaf's
    # generation.
    #
    # RE-SCOPED again for ``KS-R13@v1``, and unioned rather than replaced: ``KS-R13@v1``'s own
    # paragraph asserts the property the two literal lists were standing in for -- strictly ascending
    # versions from 1, each generation's schema name its own version's name, and every generation's
    # table list beginning with the whole of the previous generation's, which is ``KS-R10@v1`` §1.3's
    # additive rule applied across the registry rather than checked only at generation 3. Each half
    # catches something the other does not: the contiguous-range equality reddens on a *gap* or a
    # mis-ordered entry at the tip, and the pairwise prefix walk reddens on a broken append at any
    # boundary. Both are kept.
    versions = [generation.user_version for generation in GENERATIONS]
    assert versions == list(range(1, len(GENERATIONS) + 1)), versions
    assert versions == sorted(set(versions))
    assert versions[0] == 1
    assert [generation.schema_name for generation in GENERATIONS] == [
        f"ar-knowledge-sqlite/v{version}" for version in versions
    ]
    for generation in GENERATIONS:
        assert generation.schema_name == f"ar-knowledge-sqlite/v{generation.user_version}"
    for earlier, later in itertools.pairwise(GENERATIONS):
        assert later.tables[: len(earlier.tables)] == earlier.tables, later.schema_name
    assert GENERATION_3 in GENERATIONS
    assert GENERATION_3.user_version == 3
    assert CURRENT_GENERATION is GENERATIONS[-1]
    assert CURRENT_GENERATION.user_version >= GENERATION_3.user_version
    assert GENERATION_1.fingerprint == GENERATION_1_FINGERPRINT
    assert GENERATION_2.fingerprint == PRE_LEAF_GENERATION_2_FINGERPRINT
    assert len(GENERATION_2.tables) == 16
    assert GENERATION_3.tables[: len(GENERATION_2.tables)] == GENERATION_2.tables
    for table in GENERATION_2.tables:
        assert GENERATION_3.columns[table] == GENERATION_2.columns[table], table
        assert GENERATION_3.primary_keys[table] == GENERATION_2.primary_keys[table], table
    appended = GENERATION_3.tables[len(GENERATION_2.tables) :]
    assert appended == (
        "facet_attachment",
        "facet_decision_supersession",
        "explanation",
        "explanation_revision",
    )

    for generation in GENERATIONS:
        for statement in create_schema_statements(generation):
            assert "ALTER TABLE" not in statement.upper()
    for table in appended:
        ddl = GENERATION_3.table_ddl[table]
        assert ddl.rstrip().endswith(") STRICT"), table
        assert "ON DELETE CASCADE" not in ddl.upper(), table
        assert "DEFERRABLE INITIALLY DEFERRED" in ddl, table
        for column in GENERATION_3.primary_keys[table]:
            assert f"{column} TEXT NOT NULL" in ddl, (table, column)
    assert set(GENERATION_3.triggers) > set(GENERATION_2.triggers)
    assert GENERATION_3.index_ddl != GENERATION_2.index_ddl
    assert (
        "current_revision_id"
        not in GENERATION_3.triggers["explanation_no_rebind"].split("ON explanation")[0]
    )
    # Requirement 7.2: the envelope is not a second identity authority.
    assert "content_digest" not in GENERATION_3.columns["knowledge_record"]
    assert "content_digest" in GENERATION_3.columns["record_revision"]
    assert "payload_digest" in GENERATION_3.columns["explanation_revision"]


def test_a_dataset_predating_the_facet_tables_refuses_a_facet_write(tmp_path: Path) -> None:
    """Requirement 8.4: observed and required generation as facts, no migration, no widening."""

    repository_id = str(uuid4())
    path = build_recorded_generation_2_dataset(tmp_path, repository_id)
    authorship = write_authorship(
        actor_ref="agent:facets",
        authorization_ref=AUTHORIZATION,
        origin_refs=("requirement:KS-R11@v1",),
    )
    store = open_existing_knowledge_store(path, repository_id)
    try:
        before = table_counts(store)
        standalone = facets.add_facet(
            store,
            FacetWriteRequest(
                repository_id=repository_id,
                provenance=authorship,
                command=facet_command(),
            ),
        )
        assert standalone.state == "refused"
        assert standalone.refusal is not None
        assert standalone.refusal.code == "unsupported_schema"
        assert standalone.refusal.expected == str(GENERATION_3.user_version)
        assert standalone.refusal.observed == str(GENERATION_2.user_version)
        assert table_counts(store) == before

        batched = facets.require_facet_generation(store, "change_candidate")
        assert batched is not None
        assert (batched.expected, batched.observed) == ("3", "2")

        # The dataset is still served as what it is: a shipped write in a generation-1 table works.
        invariant_id = str(uuid4())
        store.create_invariant(
            InvariantRequest(
                repository_id=repository_id,
                invariant_id=invariant_id,
                display_label="still-writable",
                provenance=authorship,
            )
        )
        assert store.get_invariant(invariant_id) is not None
    finally:
        store.close()
    assert dataset_identity(path).schema_version == GENERATION_2.schema_name


# ---------------------------------------------------------------------------
# Requirement 9: the read projection, and the shipped selection it must not move.


def test_the_shipped_seed_page_is_byte_identical_and_the_facet_page_is_its_own_policy(
    tmp_path: Path,
) -> None:
    """Requirements 9.2, 9.3 and 9.5: the pre-leaf page, the declared constants, and a new policy.

    The comparison is against digests measured on the base revision -- before this leaf existed --
    over the same recorded fixture, so it is a before/after observation rather than a value read
    back from the code under test.
    """

    path = build_recorded_fixture(tmp_path)
    identity = dataset_identity(path)
    assert identity.schema_version == GENERATION_2.schema_name
    assert identity.logical_digest == PRE_LEAF_DATASET_DIGEST

    context = open_read_context(path, REPOSITORY_ID)
    result = read_knowledge_scope(
        path,
        context,
        KnowledgeReadRequest(seed=PathSeed(path=FIXTURE_PATH), budget=KnowledgeReadBudget()),
    )
    assert result.state == "page", result.refusal
    assert result.policy_version == KNOWLEDGE_READ_POLICY_VERSION == "recorded-family-frontier/v1"
    assert result.page is not None
    assert len(result.page.items) == PRE_LEAF_SELECTED_ITEMS
    assert result.page.counts.primary_items_total == PRE_LEAF_SELECTED_ITEMS
    page_digest = hashlib.sha256(canonical_json_bytes(result.page.model_dump(mode="json")))
    assert page_digest.hexdigest() == PRE_LEAF_PAGE_DIGEST
    result_digest = hashlib.sha256(canonical_json_bytes(result.model_dump(mode="json")))
    assert result_digest.hexdigest() == PRE_LEAF_RESULT_DIGEST
    assert get_args(ItemKind) == (
        "invariant_revision",
        "family_revision",
        "family_membership",
        "realization_claim",
        "advertised_family",
    )
    assert MAX_PAGE_ITEMS == PRE_LEAF_MAX_PAGE_ITEMS
    assert SELECTION_ITEM_LIMIT == PRE_LEAF_ITEM_LIMIT
    assert FACET_SELECTION_POLICY_VERSION != KNOWLEDGE_READ_POLICY_VERSION
    assert FACET_SELECTION_ITEM_LIMIT == PRE_LEAF_ITEM_LIMIT

    facet = read_facet_scope(
        path, context, FacetReadRequest(seed=FacetRecordSeed(record_id=str(uuid4())))
    )
    assert facet.state == "refused"
    assert facet.refusal is not None
    assert facet.refusal.code == "selector_absent"
    assert facet.page is None
    assert facet.policy_version == FACET_SELECTION_POLICY_VERSION


def test_a_facet_does_not_join_a_shipped_seed_and_the_facet_page_is_exact(
    admitted: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Requirements 9.1 to 9.3: facets stay out of the shipped selection, and their page holds."""

    destination, _authorship = admitted
    with open_admitted_knowledge_store(destination) as store:
        invariant_id, revision_id = seed_subject(store, destination, destination.authorship)
        record_id, facet_revision_id = store_facet(store, destination)
        attach_facet(
            store,
            destination,
            facet_revision_id,
            InvariantRevisionEndpoint(revision_id=revision_id),
        )
        page = read_facet(store, destination, FacetRecordSeed(record_id=record_id))
        assert page.page.counts.items_total == len(page.page.items)
        assert page.page.enumeration_complete is True
        assert page.page.counts.facet_records == 1
        assert page.page.counts.facet_revisions == 1
        assert page.page.counts.attachments == 1
        assert list(page.page.items) == sorted(page.page.items, key=facet_item_sort_key)
        assert [item.kind for item in page.page.items] == [
            "facet_record",
            "facet_revision",
            "facet_attachment",
        ]
        revision = page_item(page, "facet_revision").revision
        assert revision.revision_id == facet_revision_id
        assert revision.content_digest == facet_records.record_revision_content_digest(
            store, facet_revision_id
        )
        assert revision.provenance.actor_ref == "agent:facets"
        assert page_item(page, "facet_attachment").attachment.endpoint == (
            InvariantRevisionEndpoint(revision_id=revision_id)
        )

        # The shipped selection is untouched: no facet kind appears in a shipped seed's page.
        shipped = read_knowledge_scope(
            destination.database_path,
            open_read_context(destination.database_path, destination.repository.repository_id),
            KnowledgeReadRequest(
                seed=InvariantRevisionSeed(invariant_id=invariant_id, revision_id=revision_id),
                budget=KnowledgeReadBudget(),
            ),
        )
        assert shipped.state == "page", shipped.refusal
        assert shipped.policy_version == KNOWLEDGE_READ_POLICY_VERSION
        assert shipped.page is not None
        kinds = {item.kind for item in shipped.page.items}
        assert kinds <= set(get_args(ItemKind))
        assert all("facet" not in kind for kind in kinds)

        # A recorded statement revision with no explanation is an empty but real selection.
        empty = read_facet(
            store,
            destination,
            ExplanationSubjectSeed(
                subject=InvariantStatementSubject(
                    invariant_id=invariant_id, revision_id=revision_id
                )
            ),
        )
        assert empty.state == "page"
        assert empty.page.counts.items_total == 0

        # Complete or refused: a selection past its declared bound refuses rather than truncating.
        monkeypatch.setattr("agents_remember.memory.knowledge.facet_read.ITEM_LIMIT", 1)
        with pytest.raises(FacetSelectionIncomplete) as raised:
            select_facet_scope(
                store.connection,
                FacetSelectionQuery(
                    repository_id=store.repository_id,
                    seed=FacetRecordSeed(record_id=record_id),
                ),
            )
        assert (raised.value.item_count, raised.value.bound) == (3, 1)
        bounded = read_facet(store, destination, FacetRecordSeed(record_id=record_id))
        assert bounded.state == "refused"
        assert bounded.refusal.code == "selection_incomplete"
        assert bounded.page is None

        # A stored revision whose seal does not hold is not a page: such a row can only arrive from
        # outside the operation -- the triggers refuse a rewrite and the write path recomputes every
        # seal -- so it is inserted directly, and the read must refuse it rather than serve content
        # that is not the identity it names.
        sealed_record, _sealed_revision = store_facet(store, destination)
        store.write(
            "INSERT INTO record_revision (repository_id, revision_id, record_id, record_schema, "
            "payload, predecessor_revision_id, content_digest, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                store.repository_id,
                str(uuid4()),
                sealed_record,
                FACET_RECORD_SCHEMAS["decision"],
                '{"facet_kind": "decision", "outcome": "rewritten", "reason": "r", "decider": "d"}',
                None,
                "0" * 64,
                "{}",
            ),
        )
    sealed = read_facet(store, destination, FacetRecordSeed(record_id=sealed_record))
    assert sealed.state == "refused"
    assert sealed.refusal.code == "snapshot_unavailable"
    assert "content digest" in sealed.refusal.detail
