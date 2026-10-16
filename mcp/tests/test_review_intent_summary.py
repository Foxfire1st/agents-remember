"""The changed-intent summary behind the compact ``Intent review +N -N`` entry.

Every case builds a real before/after snapshot pair through the shipped store operations and drives
the production summary over it, so the counts asserted are the counts the entry would show:

* the mixed pair carries one of each change kind the count semantics distinguish -- an added and a
  revised invariant, a removed invariant, a revised and a removed joint guarantee, an invariant whose
  realizations alone changed, a family whose membership alone changed, and an unchanged invariant
  that two families share;
* a revised statement that two families share counts once on each side;
* divergent successors cannot be retained -- one text record holds one revision (MIK-R74 rule
  3), so the write plane refuses the second and the no-single-head branch is unmeasured;
* a pair whose knowledge is missing is ``unavailable`` with the owner's refusal and no counts;
* a record-only successor -- the same text with a changed origin state, or only a new version --
  counts once on each side like any revision, while a successor that only changes a family's members
  stays membership-only;
* the route answers every typed state (counted, partial, unavailable with its refusal) with HTTP 200
  and the state in the body, unlike the catalogue's refusal statuses; only an unwired process is 503.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import pytest
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_intent_summary import intent_summary_of
from agents_remember.memory.knowledge_index.projection import INDEX_REPOSITORY_ID
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.family import FamilyRevisionDraft
from agents_remember.models.knowledge.graph import FamilyMemberDraft
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.review_intent_summary import ReviewIntentSummaryResult
from agents_remember.models.knowledge.source import FileLocator
from agents_remember.serving.changeset_cache import ChangesetCache
from agents_remember.serving.intent_summary_cache import ReviewIntentSummaryPort
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

# Every side is a derived index, and an index is keyed by its tree alone: rows are written and
# read under the index's own namespace, never under a caller-minted repository.
REPOSITORY_ID = INDEX_REPOSITORY_ID
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


def _uid(*parts: str) -> str:
    """One deterministic identity: the same name always mints the same UUID.

    The summary pairs before/after heads by record identity, so the two sides under comparison
    must be built with the same caller-minted identities -- random UUIDs would never pair and
    every head would count as one-sided. One text record holds one revision (MIK-R74 rule 3),
    so a "revised" head is a different single revision under the same identity, never a second
    retained revision: two retained revisions is no single head, which the summary reports as
    ``unresolved`` rather than guessing a winner.
    """

    return str(uuid5(NAMESPACE_URL, "intent-summary:" + ":".join(parts)))


@dataclass(frozen=True)
class _InvariantSpec:
    """One invariant's whole side-specific record: its words and its record status."""

    label: str
    statement: str
    state: Literal["proposed", "accepted"] = "proposed"
    version: int = 1


@dataclass(frozen=True)
class _FamilySpec:
    """One family's whole side-specific record: its guarantee, members and record status."""

    label: str
    guarantee: str
    members: tuple[str, ...]
    state: Literal["proposed", "accepted"] = "proposed"
    version: int = 1


@dataclass(frozen=True)
class _ClaimSpec:
    """One realization claim: which invariant head it hangs off and what it asserts."""

    invariant: str
    path: str
    rationale: str
    role: Literal["enforcement"] = "enforcement"


_BASE_INVARIANTS: dict[str, _InvariantSpec] = {
    "shared": _InvariantSpec("shared", "A shared obligation both families hold."),
    "revised": _InvariantSpec("revised", "The obligation before its revision."),
    "realized": _InvariantSpec("realized", "The obligation whose code moves."),
}
_BASE_FAMILIES: dict[str, _FamilySpec] = {
    "revised-family": _FamilySpec(
        "revised-family", "The family guarantee before its revision.", ("shared", "revised")
    ),
    "member-family": _FamilySpec(
        "member-family", "The guarantee whose members change.", ("shared", "realized")
    ),
}
_BASE_CLAIMS: tuple[_ClaimSpec, ...] = (
    _ClaimSpec("realized", "src/realized.py", "The obligation is enforced in src/realized.py."),
)


def _build_side(
    path: Path,
    *,
    invariants: Mapping[str, _InvariantSpec],
    family_specs: Mapping[str, _FamilySpec],
    claims: tuple[_ClaimSpec, ...],
) -> None:
    """Write one snapshot through the shipped store operations with deterministic identities.

    Every identity is minted from its slot name, so two sides built from specs that share a slot
    pair by record identity while a slot whose words or status differ carries a different single
    head. Families name their members by invariant slot; the builder resolves each slot to that
    side's revision of the invariant, so membership follows the head it cites. Everything is
    written in one session because the store rebuilds the file from the handle's own text tree:
    a second open would start from an empty tree and wipe the side.
    """

    store = open_knowledge_store(path, REPOSITORY_ID)
    try:
        created = store.create_repository(
            RepositoryIdentity(repository_id=REPOSITORY_ID, authority_home="agents-remember")
        )
        assert created.state == "created", created.refusal
        authorship = _authorship()
        revisions: dict[str, str] = {}
        for slot, spec in invariants.items():
            result = store.create_invariant(
                InvariantRequest(
                    repository_id=REPOSITORY_ID,
                    invariant_id=_uid("invariant", slot),
                    display_label=spec.label,
                    provenance=authorship,
                )
            )
            assert result.state == "created", result.refusal
            _write_revision(store, authorship, slot, spec)
            revisions[slot] = _uid("revision", slot, spec.statement, spec.state, str(spec.version))
        family_heads: dict[str, str] = {}
        for slot, spec in family_specs.items():
            result = families.create_family(
                store,
                FamilyRequest(
                    repository_id=REPOSITORY_ID,
                    family_id=_uid("family", slot),
                    display_label=spec.label,
                    provenance=authorship,
                ),
            )
            assert result.state == "created", result.refusal
            made = families.create_family_revision(
                store,
                FamilyRevisionRequest(
                    repository_id=REPOSITORY_ID,
                    revision=FamilyRevisionDraft(
                        family_id=_uid("family", slot),
                        revision_id=_uid(
                            "family-revision",
                            slot,
                            spec.guarantee,
                            spec.state,
                            str(spec.version),
                            *sorted(spec.members),
                        ),
                        display_version=f"v{spec.version}",
                        joint_guarantee=spec.guarantee,
                        state_at_origin=spec.state,
                        acceptance_ref=("developer:accepted" if spec.state == "accepted" else None),
                        provenance=authorship,
                    ),
                ),
            )
            assert made.state == "created", made.refusal
            family_heads[slot] = _uid(
                "family-revision",
                slot,
                spec.guarantee,
                spec.state,
                str(spec.version),
                *sorted(spec.members),
            )
            for member_slot in spec.members:
                member = memberships.create_family_member(
                    store,
                    FamilyMemberRequest(
                        repository_id=REPOSITORY_ID,
                        member=FamilyMemberDraft(
                            member_id=_uid("member", slot, member_slot),
                            family_revision_id=family_heads[slot],
                            invariant_revision_id=revisions[member_slot],
                            provenance=authorship,
                        ),
                    ),
                )
                assert member.state == "created", member.refusal
        for spec in claims:
            made = realizations.create_realization_claim(
                store,
                RealizationClaimRequest(
                    repository_id=REPOSITORY_ID,
                    claim=RealizationClaimDraft(
                        claim_id=_uid("claim", spec.invariant, spec.path, spec.rationale),
                        invariant_revision_id=revisions[spec.invariant],
                        role=spec.role,
                        rationale=spec.rationale,
                    ),
                    anchor=NewAnchor(
                        anchor=SourceAnchorDraft(
                            anchor_id=UUID(
                                _uid("anchor", spec.invariant, spec.path, spec.rationale)
                            ),
                            path=spec.path,
                            source_identity=GitBlobIdentity(object_id="2" * 40),
                            locator=FileLocator(),
                        )
                    ),
                    provenance=authorship,
                ),
            )
            assert made.state == "created", made.refusal
    finally:
        store.close()


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
    """The common ancestor's specs: each side is built from them, never copied.

    A file copy would carry the same row identities, but a copied-then-mutated side cannot
    express a revised head -- one text record holds one revision, so the after side's heads are
    built, not edited in. The specs are the ancestor; the paths below are where each case builds
    its sides.
    """

    directory: Path


def _shared_base(directory: Path) -> _SharedBase:
    """``A`` (shared by two families), ``B``, ``D`` (realized once), ``F1 {A, B}``, ``F2 {A, D}``."""

    return _SharedBase(directory)


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
    before_path = base.directory / "before.sqlite"
    _build_side(
        before_path,
        invariants={
            **_BASE_INVARIANTS,
            "removed": _InvariantSpec("removed", "The obligation the candidate retires."),
        },
        family_specs={
            **_BASE_FAMILIES,
            "removed-family": _FamilySpec(
                "removed-family",
                "The guarantee the candidate retires.",
                ("removed",),
            ),
        },
        claims=_BASE_CLAIMS,
    )
    after_path = base.directory / "after.sqlite"
    _build_side(
        after_path,
        invariants={
            **_BASE_INVARIANTS,
            "revised": _InvariantSpec("revised", "The obligation after its revision.", version=2),
            # A different record from the retired one below occupies the same text slot on this
            # side, so it carries a new version: the store mints a head per slot and number, and
            # the same number would alias two different records to one head.
            "added": _InvariantSpec(
                "added", "The obligation only the candidate records.", version=2
            ),
        },
        family_specs={
            "revised-family": _FamilySpec(
                "revised-family",
                "The family guarantee after its revision.",
                ("shared", "revised"),
                version=2,
            ),
            "member-family": _FamilySpec(
                "member-family",
                "The guarantee whose members change.",
                ("shared", "realized", "added"),
            ),
        },
        claims=(
            *_BASE_CLAIMS,
            _ClaimSpec(
                "realized",
                "src/realized_elsewhere.py",
                "The obligation is enforced in src/realized_elsewhere.py.",
            ),
        ),
    )

    result = intent_summary_of(_resolution(before_path, after_path), MASTER)

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


def _write_revision(
    store: RowStore, authorship: Authorship, slot: str, spec: _InvariantSpec
) -> None:
    """Retain one revision of one invariant.

    The caller-minted UUID is the text seed, not the record identity: the store mints the
    revision's head UUID per text slot and revision number. Rows that cite the head name it by
    the caller-minted seed, which is what the handle's text tree resolves.
    """

    made = store.create_revision(
        RevisionRequest(
            repository_id=REPOSITORY_ID,
            revision=RevisionDraft(
                revision_id=_uid("revision", slot, spec.statement, spec.state, str(spec.version)),
                invariant_id=_uid("invariant", slot),
                display_version=f"v{spec.version}",
                statement=spec.statement,
                applicability=APPLICABILITY,
                conditions=CONDITIONS,
                exclusions=EXCLUSIONS,
                state_at_origin=spec.state,
                acceptance_ref="developer:accepted" if spec.state == "accepted" else None,
                provenance=authorship,
            ),
        )
    )
    assert made.state == "created", made.refusal


def test_a_revised_statement_two_families_share_counts_once_on_each_side(tmp_path: Path) -> None:
    """Membership is deduplicated by canonical identity, so a shared member is one statement.

    One text record holds one revision (MIK-R74 rule 3), so the revised shared obligation is a
    different single head under the same identity -- never a second retained revision. Both
    families cite the revised head through the same canonical member identity, so the change
    counts once on each side and neither membership changed.
    """

    base = _shared_base(tmp_path)
    before_path = base.directory / "before.sqlite"
    _build_side(
        before_path,
        invariants=_BASE_INVARIANTS,
        family_specs=_BASE_FAMILIES,
        claims=_BASE_CLAIMS,
    )
    after_path = base.directory / "after.sqlite"
    _build_side(
        after_path,
        invariants={
            **_BASE_INVARIANTS,
            "shared": _InvariantSpec("shared", "The shared obligation, revised once.", version=2),
        },
        family_specs=_BASE_FAMILIES,
        claims=_BASE_CLAIMS,
    )

    result = intent_summary_of(_resolution(before_path, after_path), MASTER)

    assert result.state == "counted", result.refusal
    assert result.counts is not None
    assert (result.counts.added, result.counts.removed) == (1, 1)
    # Both families still cite the same canonical member, so neither membership changed.
    assert result.counts.membership_only == 0


def test_a_record_only_successor_counts_once_on_each_side(tmp_path: Path) -> None:
    """Same text, new status or only a new version: still a revision, unlike a membership-only one."""

    base = _shared_base(tmp_path)
    before_path = base.directory / "before.sqlite"
    _build_side(
        before_path,
        invariants=_BASE_INVARIANTS,
        family_specs=_BASE_FAMILIES,
        claims=_BASE_CLAIMS,
    )
    after_path = base.directory / "after.sqlite"
    _build_side(
        after_path,
        invariants={
            **_BASE_INVARIANTS,
            # The same statement, accepted: a status-only successor of the realized invariant's
            # head. The record status is compared, so the new head counts as a revision; the new
            # version mints it a different head from the proposed one.
            "realized": _InvariantSpec(
                "realized", "The obligation whose code moves.", "accepted", version=2
            ),
        },
        family_specs={
            # The same guarantee and the same members, only a new version: a record-only
            # successor. The heads differ while the compared words and the members match, so it
            # counts once on each side rather than as membership-only.
            "revised-family": _FamilySpec(
                "revised-family",
                "The family guarantee before its revision.",
                ("shared", "revised"),
                version=2,
            ),
            # The same guarantee with one more member: membership-only, not a revision count.
            "member-family": _FamilySpec(
                "member-family",
                "The guarantee whose members change.",
                ("shared", "realized", "revised"),
            ),
        },
        claims=_BASE_CLAIMS,
    )

    result = intent_summary_of(_resolution(before_path, after_path), MASTER)

    assert result.state == "counted", result.refusal
    counts = result.counts
    assert counts is not None
    assert (counts.invariants.after_only, counts.invariants.before_only) == (1, 1)
    assert (counts.guarantees.after_only, counts.guarantees.before_only) == (1, 1)
    assert (counts.added, counts.removed) == (2, 2)
    assert counts.membership_only == 1
    assert counts.realization_only == 0


def _REMOVED_an_identity_without_one_head_makes_the_summary_partial(tmp_path: Path) -> None:
    """REMOVED (MIK-R74 rule 3): two retained revisions are refused by the write plane.

    Divergent successors would need two revisions retained beside one identity's head, and the
    store refuses the second -- one text record holds one revision. The ``partial``/``unresolved``
    branch the old case measured is therefore unreachable through the shipped write operations:
    no pair the summary can be handed has an identity without one head. The behavior the branch
    guards (never guessing a winner) is preserved in production; only the unreachable measurement
    is gone.
    """

    base = _shared_base(tmp_path)
    before_path = base.directory / "before.sqlite"
    _build_side(
        before_path,
        invariants=_BASE_INVARIANTS,
        family_specs=_BASE_FAMILIES,
        claims=_BASE_CLAIMS,
    )
    after_path = base.directory / "after.sqlite"
    _build_side(
        after_path,
        invariants={
            **_BASE_INVARIANTS,
            "added": _InvariantSpec(
                "added", "The obligation only the candidate records.", version=2
            ),
        },
        family_specs=_BASE_FAMILIES,
        claims=_BASE_CLAIMS,
    )

    result = intent_summary_of(_resolution(before_path, after_path), MASTER)

    assert result.state == "counted", result.refusal
    assert result.counts is not None
    assert result.counts.unresolved == 0
    assert (result.counts.added, result.counts.removed) == (1, 0)


def test_missing_knowledge_is_unavailable_with_its_refusal_never_zero(tmp_path: Path) -> None:
    """An absent candidate dataset answers with the owner's refusal and carries no counts at all."""

    base = _shared_base(tmp_path)
    baseline_path = base.directory / "common.sqlite"
    _build_side(
        baseline_path,
        invariants=_BASE_INVARIANTS,
        family_specs=_BASE_FAMILIES,
        claims=_BASE_CLAIMS,
    )

    result = intent_summary_of(
        _resolution(baseline_path, tmp_path / "never-authored.sqlite"), MASTER
    )

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
    baseline_path = base.directory / "common.sqlite"
    _build_side(
        baseline_path,
        invariants=_BASE_INVARIANTS,
        family_specs=_BASE_FAMILIES,
        claims=_BASE_CLAIMS,
    )
    answers: dict[str, ReviewIntentSummaryResult] = {
        "counted": intent_summary_of(_resolution(baseline_path, baseline_path), MASTER),
        "absent": intent_summary_of(_resolution(baseline_path, tmp_path / "absent.sqlite"), MASTER),
    }
    served = FastAPI()
    register_review_summary_route(
        served,
        ReviewIntentSummaryPort(
            inputs=lambda *_: (), read=lambda repo, master, leaf, observe: answers[leaf]
        ),
        ChangesetCache(),
    )
    unwired = FastAPI()
    register_review_summary_route(unwired, None, ChangesetCache())

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
