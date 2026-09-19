"""The shared harness for the authored-judgment facet cases.

One admitted candidate, one recorded generation-2 fixture and the drivers that write and read
through the production application seams, so every facet case exercises the same admission,
lock and transaction boundary a caller would. It is a support module rather than part of the
test module because the recorded fixture is what makes the byte-identity evidence reproducible:
fixed identities, a fixed authorship instant and generation 2's own recorded DDL, so the page
it produces is a function of the substrate rather than of a UUID draw.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, get_args
from uuid import UUID, uuid4

from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    change_knowledge_candidate,
    initialize_knowledge_namespace,
    resolve_candidate_context,
    write_authorship,
)
from agents_remember.application.knowledge_facets import read_facet_scope
from agents_remember.application.knowledge_read import open_read_context
from agents_remember.memory.knowledge import (
    anchors,
    facets,
    families,
    memberships,
    realizations,
)
from agents_remember.memory.knowledge.connection import open_database
from agents_remember.memory.knowledge.schema_generations import (
    GENERATION_2,
    create_schema_statements,
)
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    CandidateResolution,
    ChangeBatch,
    ProposedCommand,
)
from agents_remember.models.knowledge.facet import (
    ATTACHMENT_ENDPOINT_KINDS,
    AddFacet,
    AttachFacet,
    AuthorExplanation,
    FacetPayload,
    FacetWriteRequest,
    FamilyRevisionEndpoint,
    InvariantRevisionEndpoint,
    RealizationClaimEndpoint,
    SourceAnchorEndpoint,
)
from agents_remember.models.knowledge.facet_read import (
    ExplanationSubjectSeed,
    FacetReadRequest,
    FacetReadSeed,
)
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft, RealizationClaimDraft
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    FamilyMemberRequest,
    FamilyRequest,
    FamilyRevisionRequest,
    InvariantRequest,
    NewAnchor,
    RealizationClaimRequest,
    RevisionDraft,
    RevisionRequest,
)
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    SourceAnchorDraft,
)
from pydantic import BaseModel

AUTHORIZATION = "260915-KS developer kickoff ruling"
CODE_TREE = "a" * 40
MEMORY_TREE = "b" * 40

# The measured pre-leaf values. They were read on the base revision -- before this leaf existed --
# by building the recorded fixture below and serialising what the shipped operations returned, so
# each is an observation of the substrate as it was rather than a value read back from the code
# under test.
PRE_LEAF_GENERATION_2_FINGERPRINT = (
    "39576df6fb2c952726e8b89b577468a58f131dde3ac02d86d39faac0974d1ba1"
)
PRE_LEAF_DATASET_DIGEST = "90758f63e2de6cd87ad6832e49029f8869919a2870c11d434eb5c45d2aaf082a"
PRE_LEAF_PAGE_DIGEST = "d2df8e74a807ac4a99211fca931176cf81a0ab9a5ab94be07e761d17835c4a93"
PRE_LEAF_RESULT_DIGEST = "9deba0cf8f1e2e6e757012685bc4c5bcdb1c88705d875d813f87b8f0c35b7be8"
PRE_LEAF_SELECTED_ITEMS = 4
PRE_LEAF_ITEM_LIMIT = 5000
PRE_LEAF_MAX_PAGE_ITEMS = 32

# The recorded fixture's identities, fixed so the page it produces is a function of the substrate
# rather than of a UUID draw.
REPOSITORY_ID = "11111111-1111-4111-8111-111111111111"
INVARIANT_ID = "22222222-2222-4222-8222-222222222222"
REVISION_ID = "33333333-3333-4333-8333-333333333333"
FAMILY_ID = "44444444-4444-4444-8444-444444444444"
FAMILY_REVISION_ID = "55555555-5555-4555-8555-555555555555"
MEMBER_ID = "66666666-6666-4666-8666-666666666666"
ANCHOR_ID = "77777777-7777-4777-8777-777777777777"
CLAIM_ID = "88888888-8888-4888-8888-888888888888"
FIXTURE_BLOB = "a" * 40
FIXTURE_PATH = "src/integration.py"

SHIPPED_COMMAND_KINDS = frozenset(
    {
        "add_invariant",
        "add_invariant_revision",
        "set_invariant_label",
        "add_family",
        "add_family_revision",
        "set_family_label",
        "add_source_anchor",
        "remove_source_anchor",
        "add_family_member",
        "remove_family_member",
        "add_realization_claim",
        "remove_realization_claim",
    }
)

MINIMAL_PAYLOADS: Mapping[str, dict[str, Any]] = {
    "decision": {"outcome": "defer the cache", "reason": "unproven", "decider": "architect-seat"},
    "assumption": {"proposition": "the budget is shared", "basis": "the retry ruling"},
    "incident": {
        "occurrence": "a write stalled",
        "observed_at": "the 0410 run",
        "observed_effect": "no row",
    },
    "failure_mode": {
        "failure": "a lost lock",
        "condition": "two writers",
        "observable_effect": "refusal",
    },
    "scenario": {
        "situation": "a cold start",
        "preconditions": ["no snapshot"],
        "outcome": "a refusal",
    },
    "limitation": {
        "limited": "the walk",
        "boundary": "one repository",
        "unsupported": ["cross-repo"],
    },
    "diagnostic_guidance": {
        "condition": "a stale cursor",
        "signal": "a mismatch code",
        "interpretation": "the snapshot moved",
        "interpretation_limit": "says nothing about content",
    },
    "terminology": {"term": "facet", "definition": "an authored claim", "scope": "this substrate"},
}

ENDPOINT_NOUNS: Mapping[str, str] = {
    "invariant_revision": "invariant revision",
    "family_revision": "family revision",
    "source_anchor": "source anchor",
    "realization_claim": "realization claim",
}


# ---------------------------------------------------------------------------
# Harness. Everything below is built through the production application seam, so a case exercises
# the same admission, lock and transaction boundary a caller would.


def build_admitted_candidate(directory: Path) -> tuple[Any, Authorship]:
    """Create one admitted candidate namespace and return it with the admitted provenance."""

    directory.mkdir(parents=True, exist_ok=True)
    authorship = write_authorship(
        actor_ref="agent:facets",
        authorization_ref=AUTHORIZATION,
        origin_refs=("requirement:KS-R11@v1",),
    )
    destination = admitted_knowledge_destination(
        directory / "candidate.db",
        RepositoryIdentity(repository_id=str(uuid4()), authority_home="agents-remember"),
        authorship,
    )
    initialize_knowledge_namespace(destination)
    return (destination, authorship)


def seed_subject(store: Any, destination: Any, authorship: Authorship) -> tuple[str, str]:
    """Author one invariant and one revision, returning their identities."""

    invariant_id, revision_id = str(uuid4()), str(uuid4())
    store.create_invariant(
        InvariantRequest(
            repository_id=store.repository_id,
            invariant_id=invariant_id,
            display_label="facet-subject",
            provenance=authorship,
        )
    )
    store.create_revision(
        RevisionRequest(
            repository_id=store.repository_id,
            revision=RevisionDraft(
                revision_id=revision_id,
                invariant_id=invariant_id,
                display_version="v1",
                statement="The subject statement.",
                applicability="Every facet case.",
                conditions=("The subject is stored.",),
                exclusions=(),
                provenance=authorship,
            ),
        )
    )
    del destination
    return (invariant_id, revision_id)


def insert_anchor(store: Any, destination: Any) -> str:
    anchor_id = str(uuid4())
    anchors.insert_anchor_row(
        store,
        anchors.source_anchor_from_draft(
            SourceAnchorDraft(
                anchor_id=UUID(anchor_id),
                path="src/facet.py",
                source_identity=GitBlobIdentity(object_id=FIXTURE_BLOB),
                locator=FileLocator(),
            ),
            destination.authorship,
        ),
    )
    return anchor_id


def insert_claim(store: Any, destination: Any, revision_id: str, anchor_id: str) -> str:
    claim_id = str(uuid4())
    realizations.insert_realization_claim(
        store,
        RealizationClaimRequest(
            repository_id=store.repository_id,
            claim=RealizationClaimDraft(
                claim_id=claim_id,
                invariant_revision_id=revision_id,
                role="enforcement",
                rationale="The recorded claim a facet attaches to.",
            ),
            anchor=NewAnchor(
                anchor=SourceAnchorDraft(
                    anchor_id=UUID(anchor_id),
                    path="src/facet.py",
                    source_identity=GitBlobIdentity(object_id=FIXTURE_BLOB),
                    locator=FileLocator(),
                )
            ),
            provenance=destination.authorship,
        ),
    )
    return claim_id


def insert_family_revision(store: Any, destination: Any, revision_id: str) -> str:
    """Author one family, its revision and one membership over the seeded invariant revision."""

    family_id, family_revision_id, member_id = str(uuid4()), str(uuid4()), str(uuid4())
    families.create_family(
        store,
        FamilyRequest(
            repository_id=store.repository_id,
            family_id=family_id,
            display_label="facet-endpoint-family",
            provenance=destination.authorship,
        ),
    )
    families.create_family_revision(
        store,
        FamilyRevisionRequest(
            repository_id=store.repository_id,
            revision=FamilyRevisionDraft(
                family_id=family_id,
                revision_id=family_revision_id,
                display_version="v1",
                joint_guarantee="The endpoint family guarantee.",
                provenance=destination.authorship,
            ),
        ),
    )
    memberships.create_family_member(
        store,
        FamilyMemberRequest(
            repository_id=store.repository_id,
            member=FamilyMemberDraft(
                member_id=member_id,
                family_revision_id=family_revision_id,
                invariant_revision_id=revision_id,
                provenance=destination.authorship,
            ),
        ),
    )
    return family_revision_id


def endpoint_of_kind(
    kind: str, *, revision_id: str, family_revision_id: str, anchor_id: str, claim_id: str
) -> Any:
    if kind == "invariant_revision":
        return InvariantRevisionEndpoint(revision_id=revision_id)
    if kind == "source_anchor":
        return SourceAnchorEndpoint(anchor_id=anchor_id)
    if kind == "realization_claim":
        return RealizationClaimEndpoint(claim_id=claim_id)
    return FamilyRevisionEndpoint(revision_id=family_revision_id)


def endpoints_for_subject(store: Any, destination: Any, revision_id: str) -> dict[str, Any]:
    """Author one stored target of every attachment kind, so each kind can be attached to."""

    anchor = insert_anchor(store, destination)
    claim = insert_claim(store, destination, revision_id, anchor)
    family_revision_id = insert_family_revision(store, destination, revision_id)
    return {
        kind: endpoint_of_kind(
            kind,
            revision_id=revision_id,
            family_revision_id=family_revision_id,
            anchor_id=anchor,
            claim_id=claim,
        )
        for kind in ATTACHMENT_ENDPOINT_KINDS
    }


def resolve_context(destination: Any) -> Any:
    return resolve_candidate_context(
        destination,
        CandidateResolution(
            lane="draft-candidate",
            code_tree_id=CODE_TREE,
            memory_tree_id=MEMORY_TREE,
            snapshot_ref="snapshot:facet-case",
            candidate_ref="candidate:facet-case",
        ),
    )


def apply_commands(
    destination: Any, context: Any, *commands: Any, expected: tuple[Any, ...] = ()
) -> Any:
    return change_knowledge_candidate(
        destination,
        ChangeBatch(expected=context, commands=commands, expected_records=expected),
    )


def write_facet(store: Any, destination: Any, command: Any) -> Any:
    """Apply one standalone facet write through the operation that owns it."""

    operation = {
        "add_facet": facets.add_facet,
        "attach_facet": facets.attach_facet,
        "remove_facet_attachment": facets.remove_facet_attachment,
        "author_explanation": facets.author_explanation,
        "add_explanation_revision": facets.add_explanation_revision,
        "designate_explanation": facets.designate_explanation,
    }[command.kind]
    return operation(
        store,
        FacetWriteRequest(
            repository_id=store.repository_id,
            command=command,
            provenance=destination.authorship,
        ),
    )


def facet_command(**overrides: Any) -> AddFacet:
    values: dict[str, Any] = {
        "record_id": str(uuid4()),
        "revision_id": str(uuid4()),
        "facet_kind": "decision",
        "payload": dict(MINIMAL_PAYLOADS["decision"]),
    }
    values.update(overrides)
    return AddFacet(**values)


def store_facet(store: Any, destination: Any, **overrides: Any) -> tuple[str, str]:
    """Author one facet, returning ``(record id, revision id)``.

    The overrides are the command's own fields, so a case states only what it varies.
    """

    facet_kind = overrides.pop("facet_kind", "decision")
    record = overrides.pop("record_id", None) or str(uuid4())
    revision = overrides.pop("revision_id", None) or str(uuid4())
    payload = overrides.pop("payload", None) or dict(MINIMAL_PAYLOADS[facet_kind])
    written = write_facet(
        store,
        destination,
        facet_command(
            record_id=record,
            revision_id=revision,
            facet_kind=facet_kind,
            payload=payload,
            **overrides,
        ),
    )
    assert written.state == "applied", written.refusal
    return (record, revision)


def attach_facet(store: Any, destination: Any, facet_revision_id: str, endpoint: Any) -> str:
    attachment_id = str(uuid4())
    written = write_facet(
        store,
        destination,
        AttachFacet(
            attachment_id=attachment_id,
            facet_revision_id=facet_revision_id,
            endpoint=endpoint,
        ),
    )
    assert written.state == "applied", written.refusal
    return attachment_id


def author_explanation(
    store: Any, destination: Any, subject: Any, body: str = "why this wording"
) -> tuple[str, str]:
    explanation_id, revision_id = str(uuid4()), str(uuid4())
    written = write_facet(
        store,
        destination,
        AuthorExplanation(
            explanation_id=explanation_id,
            revision_id=revision_id,
            subject=subject,
            body=body,
        ),
    )
    assert written.state == "applied", written.refusal
    return (explanation_id, revision_id)


def table_counts(store: Any) -> dict[str, int]:
    """Count every table the *open dataset* declares, so a version-2 dataset counts its own set."""

    return {
        table: int(next(iter(store.connection.execute(f"SELECT count(*) FROM {table}")))[0])
        for table in store.generation.tables
    }


def read_facet(store: Any, destination: Any, seed: FacetReadSeed) -> Any:
    del store
    return read_facet_scope(
        destination.database_path,
        open_read_context(destination.database_path, destination.repository.repository_id),
        FacetReadRequest(seed=seed),
    )


def page_item(page: Any, kind: str) -> Any:
    return next(item for item in page.page.items if item.kind == kind)


def member_models(annotation: Any) -> tuple[type, ...]:
    """Return the concrete models a union -- possibly wrapped in ``Annotated`` -- admits.

    The walk is recursive rather than one ``get_args`` deep because the declaration may be either
    ``Annotated[Union[A, B], Field]`` or the bare union, and because a ``Literal`` argument must not
    be mistaken for a member: only a concrete model is one.
    """

    found: list[type] = []
    for argument in get_args(annotation):
        if isinstance(argument, type) and issubclass(argument, BaseModel):
            found.append(argument)
        else:
            found.extend(member_models(argument))
    return tuple(found)


def payload_kinds() -> set[str]:
    return {
        literal
        for member in member_models(FacetPayload)
        for literal in get_args(member.model_fields["facet_kind"].annotation)
    }


def declared_subject_kinds() -> set[str]:
    annotation = ExplanationSubjectSeed.model_fields["subject"].annotation
    return {
        literal
        for member in member_models(annotation)
        for literal in get_args(member.model_fields["kind"].annotation)
    }


def command_kinds() -> set[str]:
    return {
        literal
        for member in member_models(ProposedCommand)
        for literal in get_args(member.model_fields["kind"].annotation)
    }


def build_recorded_generation_2_dataset(tmp_path: Path, repository_id: str) -> Path:
    """Create a genuine version-2 dataset through generation 2's own recorded DDL."""

    path = tmp_path / "recorded-v2" / "knowledge-candidate.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(path)
    try:
        for statement in create_schema_statements(GENERATION_2):
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {GENERATION_2.user_version}")
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (repository_id, "agents-remember"),
        )
    finally:
        connection.close()
    return path


def build_recorded_fixture(tmp_path: Path) -> Path:
    """Create the recorded generation-2 fixture whose page digests were measured before this leaf.

    It is built through generation 2's own recorded DDL and through the production store calls, with
    fixed identities and a fixed authorship instant, so the page it produces is a function of the
    substrate rather than of a draw. The exact same construction produced the recorded digests on
    the base revision.
    """

    path = tmp_path / "golden" / "knowledge-candidate.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = open_database(path)
    try:
        for statement in create_schema_statements(GENERATION_2):
            connection.execute(statement)
        connection.execute(f"PRAGMA user_version = {GENERATION_2.user_version}")
        connection.execute(
            "INSERT INTO repository (repository_id, authority_home) VALUES (?, ?)",
            (REPOSITORY_ID, "agents-remember"),
        )
    finally:
        connection.close()
    authorship = Authorship(
        actor_ref="agent:ks-l11-measurement",
        authorization_ref=AUTHORIZATION,
        operation_id=UUID("99999999-9999-4999-8999-999999999999"),
        recorded_at=datetime(2026, 9, 18, tzinfo=UTC).isoformat(),
        origin_refs=("requirement:KS-R11@v1",),
    )
    store = open_existing_knowledge_store(path, REPOSITORY_ID)
    try:
        store.create_invariant(
            InvariantRequest(
                repository_id=REPOSITORY_ID,
                invariant_id=INVARIANT_ID,
                display_label="retry-budget",
                provenance=authorship,
            )
        )
        store.create_revision(
            RevisionRequest(
                repository_id=REPOSITORY_ID,
                revision=RevisionDraft(
                    revision_id=REVISION_ID,
                    invariant_id=INVARIANT_ID,
                    display_version="v1",
                    statement="The retry budget is shared.",
                    applicability="Every admitted candidate write.",
                    conditions=("The write is admitted.",),
                    exclusions=("Historical rows are not rewritten.",),
                    provenance=authorship,
                ),
            )
        )
        families.create_family(
            store,
            FamilyRequest(
                repository_id=REPOSITORY_ID,
                family_id=FAMILY_ID,
                display_label="retry-family",
                provenance=authorship,
            ),
        )
        families.create_family_revision(
            store,
            FamilyRevisionRequest(
                repository_id=REPOSITORY_ID,
                revision=FamilyRevisionDraft(
                    family_id=FAMILY_ID,
                    revision_id=FAMILY_REVISION_ID,
                    display_version="v1",
                    joint_guarantee="The retry budget holds together.",
                    provenance=authorship,
                ),
            ),
        )
        memberships.create_family_member(
            store,
            FamilyMemberRequest(
                repository_id=REPOSITORY_ID,
                member=FamilyMemberDraft(
                    member_id=MEMBER_ID,
                    family_revision_id=FAMILY_REVISION_ID,
                    invariant_revision_id=REVISION_ID,
                    provenance=authorship,
                ),
            ),
        )
        anchors.insert_anchor_row(
            store,
            anchors.source_anchor_from_draft(
                SourceAnchorDraft(
                    anchor_id=UUID(ANCHOR_ID),
                    path=FIXTURE_PATH,
                    source_identity=GitBlobIdentity(object_id=FIXTURE_BLOB),
                    locator=FileLocator(),
                ),
                authorship,
            ),
        )
        realizations.insert_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=REPOSITORY_ID,
                claim=RealizationClaimDraft(
                    claim_id=CLAIM_ID,
                    invariant_revision_id=REVISION_ID,
                    role="enforcement",
                    rationale="The recorded claim at the integration path.",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(ANCHOR_ID),
                        path=FIXTURE_PATH,
                        source_identity=GitBlobIdentity(object_id=FIXTURE_BLOB),
                        locator=FileLocator(),
                    )
                ),
                provenance=authorship,
            ),
        )
    finally:
        store.close()
    return path
