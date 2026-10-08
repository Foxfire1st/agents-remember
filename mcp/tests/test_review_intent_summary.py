"""The changed-intent summary behind the compact ``Intent review +N -N`` entry.

Every case builds a real before/after snapshot pair through the shipped store operations and drives
the production summary over it, so the counts asserted are the counts the entry would show:

* the mixed pair carries one of each change kind the count semantics distinguish -- an added and a
  revised invariant, a removed invariant, a revised and a removed joint guarantee, an invariant whose
  realizations alone changed, a family whose membership alone changed, and an unchanged invariant
  that two families share;
* a revised statement that two families share counts once on each side;
* divergent successors leave an identity with no single head, which is ``partial`` and not guessed;
* a pair whose knowledge is missing is ``unavailable`` with the owner's refusal and no counts;
* a record-only successor -- the same text with a changed origin state, or only a new version --
  counts once on each side like any revision, while a successor that only changes a family's members
  stays membership-only;
* the route answers every typed state (counted, partial, unavailable with its refusal) with HTTP 200
  and the state in the body, unlike the catalogue's refusal statuses; only an unwired process is 503.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_intent_summary import intent_summary_of
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult
from agents_remember.models.knowledge.source import FileLocator
from agents_remember.serving.review_summary import (
    KNOWLEDGE_REVIEW_SUMMARY_ROUTE,
    register_review_summary_route,
)
from anchor_fixture_models import GitBlobIdentity, RealizationClaimDraft, SourceAnchorDraft
from fastapi import FastAPI
from fastapi.testclient import TestClient
from knowledge_rows_test_support import (
    FamilyMemberRequest,
    FamilyRequest,
    FamilyRevisionRequest,
    InvariantRequest,
    NewAnchor,
    RealizationClaimRequest,
    RevisionDraft,
    RevisionRequest,
    RowStore,
    families,
    memberships,
    open_knowledge_store,
    realizations,
)

pytestmark = pytest.mark.evidence_unit

REPOSITORY_ID = str(uuid4())
MASTER = "260921_complete-code-and-intent-review"
LEAF = "260921-ICR-L47"
APPLICABILITY = "Every admitted candidate write in this repository namespace."
CONDITIONS = ("The candidate write is admitted for this namespace.",)
EXCLUSIONS = ("Historical rows are not rewritten by a candidate write.",)


def _authorship() -> Authorship:
    return Authorship(
        actor_ref="agent:intent-summary-fixture",
        authorization_ref="intent summary fixture",
        operation_id=uuid4(),
        recorded_at=datetime.now(UTC).isoformat(),
        origin_refs=("requirement:ICR-R24@v3",),
    )


class _Author:
    """The shipped write operations over one open snapshot, spelled as the fixture needs them."""

    def __init__(self, store: RowStore) -> None:
        self.store = store
        self.authorship = _authorship()
        # The origin state the next authored revision records; a case sets it to author an
        # acceptance successor.
        self.state: Literal["proposed", "accepted"] = "proposed"

    def invariant(self, label: str, statement: str) -> tuple[str, str]:
        invariant_id = str(uuid4())
        created = self.store.create_invariant(
            InvariantRequest(
                repository_id=REPOSITORY_ID,
                invariant_id=invariant_id,
                display_label=label,
                provenance=self.authorship,
            )
        )
        assert created.state == "created", created.refusal
        return invariant_id, self.revise(invariant_id, statement)

    def revise(self, invariant_id: str, statement: str, *predecessors: str) -> str:
        revision_id = str(uuid4())
        created = self.store.create_revision(
            RevisionRequest(
                repository_id=REPOSITORY_ID,
                revision=RevisionDraft(
                    revision_id=revision_id,
                    invariant_id=invariant_id,
                    display_version=f"v{len(predecessors) + 1}",
                    statement=statement,
                    applicability=APPLICABILITY,
                    conditions=CONDITIONS,
                    exclusions=EXCLUSIONS,
                    predecessors=predecessors,
                    state_at_origin=self.state,
                    acceptance_ref="developer:accepted" if self.state == "accepted" else None,
                    provenance=self.authorship,
                ),
            )
        )
        assert created.state == "created", created.refusal
        return revision_id

    def family(self, label: str, guarantee: str, members: tuple[str, ...]) -> tuple[str, str]:
        family_id = str(uuid4())
        created = families.create_family(
            self.store,
            FamilyRequest(
                repository_id=REPOSITORY_ID,
                family_id=family_id,
                display_label=label,
                provenance=self.authorship,
            ),
        )
        assert created.state == "created", created.refusal
        return family_id, self.revise_family(family_id, guarantee, members)

    def revise_family(
        self, family_id: str, guarantee: str, members: tuple[str, ...], *predecessors: str
    ) -> str:
        revision_id = str(uuid4())
        created = families.create_family_revision(
            self.store,
            FamilyRevisionRequest(
                repository_id=REPOSITORY_ID,
                revision=FamilyRevisionDraft(
                    family_id=family_id,
                    revision_id=revision_id,
                    display_version=f"v{len(predecessors) + 1}",
                    joint_guarantee=guarantee,
                    predecessors=predecessors,
                    state_at_origin=self.state,
                    acceptance_ref="developer:accepted" if self.state == "accepted" else None,
                    provenance=self.authorship,
                ),
            ),
        )
        assert created.state == "created", created.refusal
        for invariant_revision in members:
            member = memberships.create_family_member(
                self.store,
                FamilyMemberRequest(
                    repository_id=REPOSITORY_ID,
                    member=FamilyMemberDraft(
                        member_id=str(uuid4()),
                        family_revision_id=revision_id,
                        invariant_revision_id=invariant_revision,
                        provenance=self.authorship,
                    ),
                ),
            )
            assert member.state == "created", member.refusal
        return revision_id

    def realize(self, invariant_revision: str, path: str) -> None:
        created = realizations.create_realization_claim(
            self.store,
            RealizationClaimRequest(
                repository_id=REPOSITORY_ID,
                claim=RealizationClaimDraft(
                    claim_id=str(uuid4()),
                    invariant_revision_id=invariant_revision,
                    role="enforcement",
                    rationale=f"The obligation is enforced in {path}.",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(str(uuid4())),
                        path=path,
                        source_identity=GitBlobIdentity(object_id="2" * 40),
                        locator=FileLocator(),
                    )
                ),
                provenance=self.authorship,
            ),
        )
        assert created.state == "created", created.refusal


def _snapshot(path: Path) -> _Author:
    store = open_knowledge_store(path, REPOSITORY_ID)
    created = store.create_repository(
        RepositoryIdentity(repository_id=REPOSITORY_ID, authority_home="agents-remember")
    )
    assert created.state == "created", created.refusal
    return _Author(store)


def _extend(source: Path, target: Path) -> _Author:
    """Copy one snapshot and open the copy: the after side of a candidate forked from it."""

    shutil.copy(source, target)
    return _Author(open_knowledge_store(target, REPOSITORY_ID))


def _resolution(before: Path, after: Path) -> ReviewCandidateResolution:
    return ReviewCandidateResolution(
        repository_id=REPOSITORY_ID,
        leaf_id=LEAF,
        baseline_database=before,
        candidate_database=after,
        baseline_code_root=None,
        candidate_code_root=None,
        baseline_code_tree_id=None,
        candidate_code_tree_id=None,
    )


@dataclass(frozen=True)
class _SharedBase:
    """The identities the common ancestor records, which both sides inherit."""

    path: Path
    shared: tuple[str, str]
    revised: tuple[str, str]
    realized: tuple[str, str]
    revised_family: tuple[str, str]
    member_family: tuple[str, str]


def _shared_base(directory: Path) -> _SharedBase:
    """``A`` (shared by two families), ``B``, ``D`` (realized once), ``F1 {A, B}``, ``F2 {A, D}``."""

    path = directory / "common.sqlite"
    author = _snapshot(path)
    try:
        shared = author.invariant("shared", "A shared obligation both families hold.")
        revised = author.invariant("revised", "The obligation before its revision.")
        realized = author.invariant("realized", "The obligation whose code moves.")
        author.realize(realized[1], "src/realized.py")
        revised_family = author.family(
            "revised-family", "The family guarantee before its revision.", (shared[1], revised[1])
        )
        member_family = author.family(
            "member-family", "The guarantee whose members change.", (shared[1], realized[1])
        )
    finally:
        author.store.close()
    return _SharedBase(path, shared, revised, realized, revised_family, member_family)


def test_counts_statement_and_guarantee_heads_and_keeps_other_changes_apart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Added/revised/removed statements are +/-; realization- and membership-only are typed apart."""

    def _forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("the summary must not run the per-subject comparison")

    monkeypatch.setattr(
        "agents_remember.application.knowledge_diff.diff_knowledge_scope", _forbidden
    )
    base = _shared_base(tmp_path)
    before = _extend(base.path, tmp_path / "before.sqlite")
    try:
        removed = before.invariant("removed", "The obligation the candidate retires.")
        before.family("removed-family", "The guarantee the candidate retires.", (removed[1],))
    finally:
        before.store.close()
    after = _extend(base.path, tmp_path / "after.sqlite")
    try:
        revised = after.revise(
            base.revised[0], "The obligation after its revision.", base.revised[1]
        )
        added = after.invariant("added", "The obligation only the candidate records.")
        after.realize(base.realized[1], "src/realized_elsewhere.py")
        after.revise_family(
            base.revised_family[0],
            "The family guarantee after its revision.",
            (base.shared[1], revised),
            base.revised_family[1],
        )
        after.revise_family(
            base.member_family[0],
            "The guarantee whose members change.",
            (base.shared[1], base.realized[1], added[1]),
            base.member_family[1],
        )
    finally:
        after.store.close()

    result = intent_summary_of(
        _resolution(tmp_path / "before.sqlite", tmp_path / "after.sqlite"), MASTER
    )

    assert result.state == "counted", result.refusal
    counts = result.counts
    assert counts is not None
    # + : the revised invariant's new head, the added invariant, the revised guarantee's new head.
    # - : the revised invariant's old head, the retired invariant, the revised and retired guarantees.
    assert (counts.invariants.after_only, counts.invariants.before_only) == (2, 2)
    assert (counts.guarantees.after_only, counts.guarantees.before_only) == (1, 2)
    assert (counts.added, counts.removed) == (3, 4)
    # Same statement, different realizations; same guarantee, different members: never in +/-.
    assert counts.realization_only == 1
    assert counts.membership_only == 1
    assert counts.unresolved == 0


def test_a_revised_statement_two_families_share_counts_once_on_each_side(tmp_path: Path) -> None:
    """Membership is deduplicated by canonical identity, so a shared member is one statement."""

    base = _shared_base(tmp_path)
    shutil.copy(base.path, tmp_path / "before.sqlite")
    after = _extend(base.path, tmp_path / "after.sqlite")
    try:
        after.revise(base.shared[0], "The shared obligation, revised once.", base.shared[1])
    finally:
        after.store.close()

    result = intent_summary_of(
        _resolution(tmp_path / "before.sqlite", tmp_path / "after.sqlite"), MASTER
    )

    assert result.state == "counted", result.refusal
    assert result.counts is not None
    assert (result.counts.added, result.counts.removed) == (1, 1)
    # Both families still cite the same canonical member, so neither membership changed.
    assert result.counts.membership_only == 0


def test_a_record_only_successor_counts_once_on_each_side(tmp_path: Path) -> None:
    """Same text, new status or only a new version: still a revision, unlike a membership-only one."""

    base = _shared_base(tmp_path)
    shutil.copy(base.path, tmp_path / "before.sqlite")
    after = _extend(base.path, tmp_path / "after.sqlite")
    try:
        # The same statement, accepted: a status-only successor of the realized invariant's head.
        after.state = "accepted"
        after.revise(base.realized[0], "The obligation whose code moves.", base.realized[1])
        # The same guarantee and the same members, only a new version: a record-only successor.
        after.state = "proposed"
        after.revise_family(
            base.revised_family[0],
            "The family guarantee before its revision.",
            (base.shared[1], base.revised[1]),
            base.revised_family[1],
        )
        # The same guarantee with one more member: membership-only, not a revision count.
        after.revise_family(
            base.member_family[0],
            "The guarantee whose members change.",
            (base.shared[1], base.realized[1], base.revised[1]),
            base.member_family[1],
        )
    finally:
        after.store.close()

    result = intent_summary_of(
        _resolution(tmp_path / "before.sqlite", tmp_path / "after.sqlite"), MASTER
    )

    assert result.state == "counted", result.refusal
    counts = result.counts
    assert counts is not None
    assert (counts.invariants.after_only, counts.invariants.before_only) == (1, 1)
    assert (counts.guarantees.after_only, counts.guarantees.before_only) == (1, 1)
    assert (counts.added, counts.removed) == (2, 2)
    assert counts.membership_only == 1
    assert counts.realization_only == 0


def test_an_identity_without_one_head_makes_the_summary_partial(tmp_path: Path) -> None:
    """Divergent successors are not a guessed winner: the identity is unresolved, the rest counted."""

    base = _shared_base(tmp_path)
    shutil.copy(base.path, tmp_path / "before.sqlite")
    after = _extend(base.path, tmp_path / "after.sqlite")
    try:
        after.revise(base.revised[0], "One successor.", base.revised[1])
        after.revise(base.revised[0], "A competing successor.", base.revised[1])
        after.invariant("added", "The obligation only the candidate records.")
    finally:
        after.store.close()

    result = intent_summary_of(
        _resolution(tmp_path / "before.sqlite", tmp_path / "after.sqlite"), MASTER
    )

    assert result.state == "partial", result.refusal
    assert result.counts is not None
    assert result.counts.unresolved == 1
    assert (result.counts.added, result.counts.removed) == (1, 0)


def test_missing_knowledge_is_unavailable_with_its_refusal_never_zero(tmp_path: Path) -> None:
    """An absent candidate dataset answers with the owner's refusal and carries no counts at all."""

    base = _shared_base(tmp_path)

    result = intent_summary_of(_resolution(base.path, tmp_path / "never-authored.sqlite"), MASTER)

    assert result.state == "unavailable"
    assert result.counts is None
    assert result.refusal is not None
    assert result.refusal.code == "candidate_dataset_absent"
    assert result.refusal.offending_input == "never-authored.sqlite"
    assert "counts" not in result.model_dump(mode="json", exclude_none=True)


def test_the_route_answers_every_typed_state_in_the_body(
    tmp_path: Path,
) -> None:
    """Every typed answer is a 200 with its state in the body; only an unwired process is a 503."""

    base = _shared_base(tmp_path)
    answers: dict[str, ReviewIntentSummaryResult] = {
        "counted": intent_summary_of(_resolution(base.path, base.path), MASTER),
        "absent": intent_summary_of(_resolution(base.path, tmp_path / "absent.sqlite"), MASTER),
    }
    served = FastAPI()
    register_review_summary_route(served, lambda repo, master, leaf: answers[leaf])
    unwired = FastAPI()
    register_review_summary_route(unwired, None)

    with TestClient(served) as client:
        counted = client.get(
            KNOWLEDGE_REVIEW_SUMMARY_ROUTE,
            params={"repo": "r", "master": MASTER, "leaf": "counted"},
        )
        absent = client.get(
            KNOWLEDGE_REVIEW_SUMMARY_ROUTE, params={"repo": "r", "master": MASTER, "leaf": "absent"}
        )
    with TestClient(unwired) as client:
        missing = client.get(
            KNOWLEDGE_REVIEW_SUMMARY_ROUTE, params={"repo": "r", "master": MASTER, "leaf": LEAF}
        )

    assert counted.status_code == 200
    assert counted.json()["state"] == "counted"
    assert counted.json()["counts"]["added"] == counted.json()["counts"]["removed"] == 0
    assert absent.status_code == 200
    assert absent.json()["state"] == "unavailable"
    assert absent.json()["refusal"]["code"] == "candidate_dataset_absent"
    assert "counts" not in absent.json()
    assert missing.status_code == 503
    assert missing.json()["status"] == "unavailable"
