"""The shared branching knowledge fixture built through the real typed operations.

The fixture exists because the interesting identity behaviour is a *conflict*, not a
constructor call: two divergent successors of one revision carry the same friendly display
version, and both must remain separately addressable. It is built through the public
operation, never by inserting rows, so a later leaf that builds on it inherits a store it can
trust and a scenario it can extend rather than re-invent.

The module is test support, not production code: it decides nothing and is imported by tests
and by later leaves' fixtures only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agents_remember.memory.knowledge.store import OpenedKnowledgeStore, open_knowledge_store
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import (
    InvariantRequest,
    RevisionDraft,
    RevisionRequest,
)

# Stable prose so a reopened comparison is meaningful rather than incidental.
REPOSITORY_AUTHORITY_HOME = "agents-remember"
INVARIANT_LABEL = "approved-state-preservation"
BASE_STATEMENT = "Preserve approved state across an admitted candidate write."
BRANCH_A_STATEMENT = "Preserve approved state and record the deciding attribution."
BRANCH_B_STATEMENT = "Preserve approved state and refuse a partially applied batch."
BASE_DISPLAY_VERSION = "v1"
SHARED_SUCCESSOR_DISPLAY_VERSION = "v2"
ESSENTIAL_APPLICABILITY = "Every admitted candidate write in this repository namespace."
BASE_CONDITIONS = ("The candidate write is admitted for this namespace.",)
BRANCH_A_CONDITIONS = (
    "The candidate write is admitted for this namespace.",
    "Attribution for the authored change is recorded.",
)
BRANCH_B_CONDITIONS = (
    "The candidate write is admitted for this namespace.",
    "The batch is applied as one transaction.",
)
ESSENTIAL_EXCLUSIONS = ("Historical rows are not rewritten by a candidate write.",)


@dataclass(frozen=True)
class RevisionClauses:
    """The optional clause variations a caller applies to one further fixture revision."""

    display_version: str = SHARED_SUCCESSOR_DISPLAY_VERSION
    conditions: tuple[str, ...] = BRANCH_A_CONDITIONS


@dataclass(frozen=True)
class BranchingKnowledgeFixture:
    """One repository, one invariant and three revisions: one base and two v2 successors."""

    database_path: Path
    repository_id: str
    invariant_id: str
    base_revision_id: str
    left_revision_id: str
    right_revision_id: str
    authorship: Authorship

    def reopen(self) -> OpenedKnowledgeStore:
        """Reopen the fixture store so a test reads what was really persisted."""

        return open_knowledge_store(self.database_path, self.repository_id)


def make_authorship(
    *, actor_ref: str = "agent:fixture", recorded_at: str | None = None
) -> Authorship:
    """Build one provenance envelope with a stable actor and a real recorded instant."""

    return Authorship(
        actor_ref=actor_ref,
        authorization_ref="260915-KS developer kickoff ruling",
        operation_id=uuid4(),
        recorded_at=recorded_at or datetime.now(UTC).isoformat(),
        origin_refs=("requirement:KS-R01@v1",),
    )


def build_branching_knowledge_fixture(
    directory: Path,
    *,
    repository_id: str | None = None,
    invariant_id: str | None = None,
    database_name: str = "knowledge-candidate.db",
) -> BranchingKnowledgeFixture:
    """Create the fixture store through the public operations and return its identities.

    I0 is authored with statement, applicability, conditions, exclusions and provenance.
    I-A and I-B are then authored as successors of I0, both displaying ``v2`` with different
    statements. The call leaves the store closed, so a caller reopens it to read.
    """

    directory.mkdir(parents=True, exist_ok=True)
    authorship = make_authorship()
    fixture = BranchingKnowledgeFixture(
        database_path=directory / database_name,
        repository_id=repository_id or str(uuid4()),
        invariant_id=invariant_id or str(uuid4()),
        base_revision_id=str(uuid4()),
        left_revision_id=str(uuid4()),
        right_revision_id=str(uuid4()),
        authorship=authorship,
    )
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        repository_result = store.create_repository(_repository_identity(fixture))
        _require(fixture, "create_repository", repository_result.state, repository_result.refusal)
        invariant_result = store.create_invariant(
            InvariantRequest(
                repository_id=fixture.repository_id,
                invariant_id=fixture.invariant_id,
                display_label=INVARIANT_LABEL,
                provenance=authorship,
            )
        )
        _require(fixture, "create_invariant", invariant_result.state, invariant_result.refusal)
        for revision in _fixture_revisions(fixture, authorship):
            revision_result = store.create_revision(revision)
            _require(fixture, "create_revision", revision_result.state, revision_result.refusal)
    finally:
        store.close()
    return fixture


def fixture_revision_draft(
    fixture: BranchingKnowledgeFixture,
    revision_id: str,
    *,
    statement: str,
    predecessors: tuple[str, ...],
    clauses: RevisionClauses | None = None,
) -> RevisionDraft:
    """Build one further revision draft of the fixture's invariant for extension scenarios."""

    resolved = clauses or RevisionClauses()
    return RevisionDraft(
        revision_id=revision_id,
        invariant_id=fixture.invariant_id,
        display_version=resolved.display_version,
        statement=statement,
        applicability=ESSENTIAL_APPLICABILITY,
        conditions=resolved.conditions,
        exclusions=ESSENTIAL_EXCLUSIONS,
        predecessors=predecessors,
        provenance=fixture.authorship,
    )


def _repository_identity(fixture: BranchingKnowledgeFixture) -> RepositoryIdentity:
    return RepositoryIdentity(
        repository_id=fixture.repository_id, authority_home=REPOSITORY_AUTHORITY_HOME
    )


def _fixture_revisions(
    fixture: BranchingKnowledgeFixture, authorship: Authorship
) -> tuple[RevisionRequest, ...]:
    base = RevisionDraft(
        revision_id=fixture.base_revision_id,
        invariant_id=fixture.invariant_id,
        display_version=BASE_DISPLAY_VERSION,
        statement=BASE_STATEMENT,
        applicability=ESSENTIAL_APPLICABILITY,
        conditions=BASE_CONDITIONS,
        exclusions=ESSENTIAL_EXCLUSIONS,
        provenance=authorship,
    )
    return (
        RevisionRequest(repository_id=fixture.repository_id, revision=base),
        RevisionRequest(
            repository_id=fixture.repository_id,
            revision=fixture_revision_draft(
                fixture,
                fixture.left_revision_id,
                statement=BRANCH_A_STATEMENT,
                predecessors=(fixture.base_revision_id,),
            ),
        ),
        RevisionRequest(
            repository_id=fixture.repository_id,
            revision=fixture_revision_draft(
                fixture,
                fixture.right_revision_id,
                statement=BRANCH_B_STATEMENT,
                predecessors=(fixture.base_revision_id,),
                clauses=RevisionClauses(conditions=BRANCH_B_CONDITIONS),
            ),
        ),
    )


def _require(
    fixture: BranchingKnowledgeFixture, step: str, state: str, refusal_value: object
) -> None:
    """Fail loudly when a fixture step does not create: a fixture is not a probe."""

    if state != "created":
        raise AssertionError(
            f"knowledge fixture step {step} for repository {fixture.repository_id} returned "
            f"{state!r} instead of 'created': {refusal_value!r}"
        )
