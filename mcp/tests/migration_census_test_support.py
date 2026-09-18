"""The migration-census fixture: one real candidate dataset, written through the shipped write path.

The census is a read over records that were written by the one candidate batch operation, so a fixture
that inserted rows directly would test arithmetic against a state the real write path cannot produce.
Every identity here is therefore produced by ``change_knowledge_candidate`` -- the shipped application
entry point -- and the fixture's own route is authored through the shipped route writer.

The fixture is a small *real* corpus rather than a rich one on purpose. What the census's cases need to
be able to distinguish is not scale but **state**: an artifact that parsed, one that did not, a card
with a declared source that is absent at the baseline, a claim with no assessment and one whose verdict
a curator recorded, and a claim recorded outside the cohort. Four artifacts and three claims carry all
of those, and a larger fixture would only make the assertions harder to read.
"""

from __future__ import annotations

import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agents_remember.application.knowledge import (
    admitted_knowledge_destination,
    change_knowledge_candidate,
    initialize_knowledge_namespace,
    open_admitted_knowledge_store,
    resolve_candidate_context,
    write_authorship,
)
from agents_remember.memory.knowledge.routes import RouteDraft, author_route
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.memory.migration.baseline import FrozenBaseline, require_frozen_baseline
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import (
    CandidateResolution,
    ChangeBatch,
    KnowledgeContext,
    MutationResult,
)
from agents_remember.models.knowledge.census import (
    CensusAssessmentDisposition,
    CensusClaimCommand,
    CensusClaimEvidence,
    CensusClaimPayload,
    CensusClaimRealization,
    CensusDispositionCommand,
    CensusDispositionLink,
    CensusDispositionPayload,
    CensusInventoryRowCommand,
    CensusInventoryRowPayload,
    CensusProvenance,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity

# The four artifacts the fixture's corpus holds, as their repository-relative paths.
PARSED_CARD = "mcp/src/agents_remember/example.py.md"
UNPARSED_ARTIFACT = "bootstrap/coverage-plan.md"
ABSENT_SOURCE_CARD = "mcp/src/agents_remember/deleted.py.md"
ROUTE_OVERVIEW = "mcp/overview.md"

# The three claims the fixture records, one per state the accounting has to separate.
SUPPORTED_CLAIM_TEXT = "the guarded merge refuses a v1/v2 pair"
CONTRADICTED_CLAIM_TEXT = "the merge driver is installed by the runtime"
HISTORICAL_CLAIM_TEXT = "this was decided after the 2026-03 incident"

# The single claim text the fixture records once and repeats, for the unique-versus-occurrence count.
REPEATED_CLAIM_TEXT = "the store is append-only"

ADDRESSED_ROUTE = "mcp"

# The exact revisions the fixture's baseline names. They are object ids rather than refs because a
# baseline whose side is a ref name is not frozen.
CODE_TREE = "a" * 40
MEMORY_TREE = "b" * 40


@dataclass(frozen=True)
class CensusHarness:
    """One initialized candidate dataset, its namespace, its recorded route and its baseline."""

    database_path: Path
    repository: RepositoryIdentity
    destination: object
    authorship: Authorship
    route_id: str
    baseline: FrozenBaseline

    def context(self) -> KnowledgeContext:
        """Return a fresh context, because a successful batch makes the previous one stale."""

        return resolve_candidate_context(
            self.destination,  # type: ignore[arg-type]
            CandidateResolution(
                lane="draft-candidate",
                code_tree_id=CODE_TREE,
                memory_tree_id=MEMORY_TREE,
                snapshot_ref="candidate:migration-census",
                candidate_ref="draft:migration-census",
            ),
        )

    def store(self) -> OpenedKnowledgeStore:
        """Open the fixture's dataset for reading."""

        return open_admitted_knowledge_store(self.destination)  # type: ignore[arg-type]

    def apply(self, commands: Sequence[object]) -> MutationResult:
        """Apply one batch in a freshly resolved context, through the shipped entry point."""

        return change_knowledge_candidate(
            self.destination,  # type: ignore[arg-type]
            ChangeBatch(expected=self.context(), commands=tuple(commands)),  # type: ignore[arg-type]
        )

    def provenance(self, artifact_path: str, location: str) -> CensusProvenance:
        """Return one artifact's provenance at the fixture's frozen baseline."""

        return CensusProvenance(
            artifact_path=artifact_path,
            location=location,
            baseline=self.context().knowledge,
        )


@contextmanager
def census_harness() -> Iterator[CensusHarness]:
    """Yield one initialized candidate dataset bound to a frozen baseline and one recorded route."""

    with tempfile.TemporaryDirectory() as directory:
        database_path = Path(directory) / "candidate.db"
        repository = RepositoryIdentity(
            repository_id=str(uuid4()), authority_home="agents-remember"
        )
        authorship = write_authorship(
            actor_ref="agent:migration-census",
            authorization_ref="requirement:KS-R21@v1",
            origin_refs=("baseline:code", "baseline:memory"),
        )
        destination = admitted_knowledge_destination(database_path, repository, authorship)
        created = initialize_knowledge_namespace(destination)
        if created.state != "created":  # pragma: no cover - a fixture that cannot initialize
            raise AssertionError(f"the fixture's dataset was not created: {created.refusal}")
        route_id = _author_route(destination, repository, authorship)
        harness = CensusHarness(
            database_path=database_path,
            repository=repository,
            destination=destination,
            authorship=authorship,
            route_id=route_id,
            baseline=_validated_baseline(),
        )
        yield harness


def _validated_baseline() -> FrozenBaseline:
    """Return the fixture's frozen baseline through the one factory that validates it."""

    baseline = require_frozen_baseline(CODE_TREE, MEMORY_TREE)
    if not isinstance(baseline, FrozenBaseline):  # pragma: no cover - the ids above are exact
        raise AssertionError(f"the fixture's baseline was refused: {baseline.code}")
    return baseline


def _author_route(
    destination: object, repository: RepositoryIdentity, authorship: Authorship
) -> str:
    """Author the fixture's one route through the shipped writer."""

    store = open_admitted_knowledge_store(destination)  # type: ignore[arg-type]
    try:
        route_id = str(uuid4())
        authored = author_route(
            store.connection,
            repository.repository_id,
            RouteDraft(route_id=route_id, path=ADDRESSED_ROUTE),
            authorship,
        )
        if not isinstance(authored, str):  # pragma: no cover - a fixture whose route was refused
            raise AssertionError(f"the fixture's route was refused: {authored.code}")
        return authored
    finally:
        store.close()


@dataclass(frozen=True)
class InventoryRowSpec:
    """One inventory row's declared facts, so the builder takes a spec rather than eight arguments."""

    artifact_path: str
    outcome: str
    inventory_state: str
    declared_source_path: str | None = None
    observed_doc_type: str | None = "file-level-onboarding"
    artifact_kind: str = "file_level_onboarding"
    unparsed_content: str | None = None
    location: str = "L1"


@dataclass(frozen=True)
class ClaimSpec:
    """One claim's declared facts and its optional curator verdict."""

    text: str
    claim_kind: str = "unclassified"
    applicability: str = "assessable"
    disposition: str = "imported"
    assessment: CensusAssessmentDisposition | None = None
    realization_state: str | None = None
    artifact_path: str = PARSED_CARD
    location: str = "L40"


def inventory_row_for(harness: CensusHarness, spec: InventoryRowSpec) -> CensusInventoryRowCommand:
    """Return one inventory-row command on the fixture's baseline, with sane defaults."""

    artifact_path = spec.artifact_path
    outcome = spec.outcome
    inventory_state = spec.inventory_state
    declared_source_path = spec.declared_source_path
    observed_doc_type = spec.observed_doc_type
    artifact_kind = spec.artifact_kind
    unparsed_content = spec.unparsed_content
    location = spec.location
    return CensusInventoryRowCommand(
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload=CensusInventoryRowPayload(
            artifact_path=artifact_path,
            artifact_kind=artifact_kind,  # type: ignore[arg-type]
            declared_source_path=declared_source_path,
            observed_doc_type=observed_doc_type,
            outcome=outcome,  # type: ignore[arg-type]
            inventory_state=inventory_state,  # type: ignore[arg-type]
            unparsed_content=unparsed_content,
            source_route_path=None,
            provenance=harness.provenance(artifact_path, location),
        ),
        governing_route_id=harness.route_id,
    )


def claim_command_for(harness: CensusHarness, spec: ClaimSpec) -> CensusClaimCommand:
    """Return one claim command, optionally carrying a curator's recorded verdict."""

    text = spec.text
    claim_kind = spec.claim_kind
    applicability = spec.applicability
    disposition = spec.disposition
    assessment = spec.assessment
    realization_state = spec.realization_state
    artifact_path = spec.artifact_path
    location = spec.location
    claim_id = str(uuid4())
    evidence = (
        CensusClaimEvidence(
            claim_id=claim_id,
            evidence_ref=f"assessment:{claim_id}",
            evidence_state="unassessed" if assessment is None else "assessed",
            assessment_disposition=assessment,
        ),
    )
    realizations = (
        ()
        if realization_state is None
        else (
            CensusClaimRealization(
                claim_id=claim_id,
                realization_ref=f"code:{location}",
                attribution_state=realization_state,  # type: ignore[arg-type]
            ),
        )
    )
    return CensusClaimCommand(
        record_id=claim_id,
        revision_id=str(uuid4()),
        payload=CensusClaimPayload(
            claim_text=text,
            claim_location=f"{artifact_path}:{location}",
            claim_kind=claim_kind,  # type: ignore[arg-type]
            applicability=applicability,  # type: ignore[arg-type]
            disposition=disposition,  # type: ignore[arg-type]
            provenance=harness.provenance(artifact_path, location),
        ),
        governing_route_id=harness.route_id,
        evidence=evidence,
        realizations=realizations,
    )


def disposition_command_for(
    harness: CensusHarness,
    *,
    artifact_path: str,
    kind: str,
    location: str = "L1",
) -> CensusDispositionCommand:
    """Return one migration-disposition command for an artifact the fixture examined."""

    return CensusDispositionCommand(
        record_id=str(uuid4()),
        revision_id=str(uuid4()),
        payload=CensusDispositionPayload(
            disposition_kind=kind,  # type: ignore[arg-type]
            disposition_state="recorded",
            provenance=harness.provenance(artifact_path, location),
        ),
        governing_route_id=harness.route_id,
    )


def seed_census(harness: CensusHarness) -> None:
    """Write the fixture's whole corpus: four inventory rows, five claims and their dispositions.

    The shape is the one the census's cases assert against: one parsed card, one artifact that did not
    parse, one card whose declared source is absent, and one route overview; a supported claim, a
    contradicted claim, a historical piece outside the cohort, and one claim text recorded twice.
    """

    rows = (
        inventory_row_for(
            harness,
            InventoryRowSpec(
                artifact_path=PARSED_CARD,
                outcome="parsed",
                inventory_state="present",
                declared_source_path="mcp/src/agents_remember/example.py",
            ),
        ),
        inventory_row_for(
            harness,
            InventoryRowSpec(
                artifact_path=UNPARSED_ARTIFACT,
                outcome="unsupported",
                inventory_state="present",
                unparsed_content="a generated bootstrap artifact with no metadata table",
                observed_doc_type=None,
            ),
        ),
        inventory_row_for(
            harness,
            InventoryRowSpec(
                artifact_path=ABSENT_SOURCE_CARD,
                outcome="parsed",
                inventory_state="absent",
                declared_source_path="mcp/src/agents_remember/deleted.py",
            ),
        ),
        inventory_row_for(
            harness,
            InventoryRowSpec(
                artifact_path=ROUTE_OVERVIEW,
                outcome="parsed",
                inventory_state="present",
                artifact_kind="route_local_overview",
                observed_doc_type="route-local-overview",
            ),
        ),
    )
    claims = (
        claim_command_for(
            harness,
            ClaimSpec(
                text=SUPPORTED_CLAIM_TEXT,
                claim_kind="current_behavior",
                assessment="no_concern_found",
                realization_state="attributed",
            ),
        ),
        claim_command_for(
            harness,
            ClaimSpec(
                text=CONTRADICTED_CLAIM_TEXT,
                claim_kind="realization_attribution",
                assessment="concern_found",
            ),
        ),
        claim_command_for(
            harness,
            ClaimSpec(
                text=HISTORICAL_CLAIM_TEXT,
                claim_kind="historical_rationale",
                applicability="historical_non_applicable",
                disposition="historical",
            ),
        ),
        claim_command_for(harness, ClaimSpec(text=REPEATED_CLAIM_TEXT, location="L10")),
        claim_command_for(harness, ClaimSpec(text=REPEATED_CLAIM_TEXT, location="L90")),
    )
    dispositions = (
        disposition_command_for(harness, artifact_path=PARSED_CARD, kind="imported"),
        disposition_command_for(harness, artifact_path=UNPARSED_ARTIFACT, kind="unsupported"),
        disposition_command_for(harness, artifact_path=ABSENT_SOURCE_CARD, kind="imported"),
        disposition_command_for(harness, artifact_path="unmapped/artifact.md", kind="unmapped"),
    )
    result = harness.apply((*rows, *claims, *dispositions))
    if result.state == "refused":  # pragma: no cover - a fixture whose seed was refused
        raise AssertionError(f"the fixture's seed was refused: {result.refusal}")


def link_command_for(
    harness: CensusHarness, *, claim_record_id: str, link_kind: str = "split_into"
) -> CensusDispositionCommand:
    """Return one disposition that links to a record it produced, for the link cases."""

    disposition = disposition_command_for(harness, artifact_path=PARSED_CARD, kind="imported")
    return disposition.model_copy(
        update={
            "links": (
                CensusDispositionLink(
                    disposition_id=disposition.record_id,
                    link_kind=link_kind,  # type: ignore[arg-type]
                    target_ref=claim_record_id,
                    target_state="resolved",
                ),
            )
        }
    )
