"""``KS-R16@v1`` §1: the registered review scope, constructed from recorded inputs and one policy.

These cases protect the construction the pipeline runs over. They occupy the ``unit-regression`` lane
because what they measure is a construction over recorded rows and a declaration -- which edges were
followed, with which snapshot provenance, under which resolved policy identity, and which declared
input a refusal names -- rather than a process or a publication.

Every case names the failure it catches: a scope reported without the policy it was built under, an
edge attributed to a snapshot the run never read, membership inferred from a path prefix, the read
frontier leaking in as a widening axis, an ambiguous common base resolved by picking one, and an
unresolvable declared input answered with a smaller scope instead of a refusal.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from agents_remember.memory.knowledge import composition_policies, compositions
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.registered_scope import (
    CONSTRUCT_SCOPE_OPERATION,
    ScopeSnapshotSource,
    construct_registered_scope,
    snapshot_source,
)
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.composition import (
    REGISTERED_REVIEW_SCOPE,
    FamilyComposition,
    FamilyCompositionPolicyVersion,
)
from agents_remember.models.knowledge.registered_scope import (
    SCOPE_CONSTRUCTION_VERSION,
    FollowedScopeEdge,
    RegisteredScopeRequest,
    ScopeSnapshotDeclaration,
    scope_path_is_recorded,
)
from pydantic import ValidationError
from read_scope_test_support import (
    INTEGRATION_PATH,
    SYNCHRONIZATION_PATH,
    ReadScopeFixture,
    build_read_scope_fixture,
)

pytestmark = pytest.mark.evidence_unit

POLICY_ID = composition_policies.REGISTERED_COMPOSITION_POLICY_ID
DECLARED_VERSION = "2026-09-18.1"


@pytest.fixture(scope="module")
def scopes(tmp_path_factory: pytest.TempPathFactory) -> Any:
    """One two-snapshot construction fixture: a base dataset and a candidate that carries the edge."""

    directory = tmp_path_factory.mktemp("registered-scope")
    base = build_read_scope_fixture(directory / "base")
    candidate_path = directory / "candidate" / "candidate.db"
    candidate_path.parent.mkdir(parents=True, exist_ok=True)
    candidate_path.write_bytes(base.database_path.read_bytes())
    store = open_knowledge_store(candidate_path, base.repository_id)
    try:
        version_id = _declare_policy(store, base.authorship)
        composition_id = _author_edge(store, base, version_id)
    finally:
        store.close()
    return _ScopeFixture(base, candidate_path, version_id, composition_id)


class _ScopeFixture:
    """The built fixture: two declared sides, the declared policy version and the authored edge."""

    def __init__(
        self,
        base: ReadScopeFixture,
        candidate_path: Path,
        version_id: str,
        composition_id: str,
    ) -> None:
        self.base = base
        self.candidate_path = candidate_path
        self.version_id = version_id
        self.composition_id = composition_id

    def request(self, **overrides: Any) -> RegisteredScopeRequest:
        fields: dict[str, Any] = {
            "scope_id": "scope-B-M",
            "repository_id": self.base.repository_id,
            "snapshots": (
                _declaration("base", self.base.database_path),
                _declaration("candidate", self.candidate_path),
            ),
            "changed_paths": (INTEGRATION_PATH, SYNCHRONIZATION_PATH),
            "policy_id": POLICY_ID,
            "policy_version_id": self.version_id,
        }
        fields.update(overrides)
        return RegisteredScopeRequest(**fields)

    def sources(
        self, sides: tuple[str, ...] = ("base", "candidate")
    ) -> tuple[ScopeSnapshotSource, ...]:
        paths = {"base": self.base.database_path, "candidate": self.candidate_path}
        return tuple(
            snapshot_source(
                side,  # type: ignore[arg-type]
                paths[side],
                open_knowledge_store(paths[side], self.base.repository_id),
            )
            for side in sides
        )


def _declaration(side: str, path: Path) -> ScopeSnapshotDeclaration:
    return ScopeSnapshotDeclaration(
        side=side,  # type: ignore[arg-type]
        snapshot=dataset_identity(path),
        selector_policy_version="recorded-family-frontier/v1",
    )


def _declare_policy(store: Any, authorship: Any) -> str:
    """Declare one version of the registered composition policy, as ``KS-R17@v1`` stores it."""

    version_id = str(uuid4())
    composition_policies.insert_policy_version(
        store,
        FamilyCompositionPolicyVersion(
            policy_id=POLICY_ID,
            policy_version_id=version_id,
            declared_version=DECLARED_VERSION,
            direction="forward",
            depth_bound=1,
            widened_scope=REGISTERED_REVIEW_SCOPE,
            provenance=authorship,
            repository_id=store.repository_id,
        ),
    )
    return version_id


def _author_edge(store: Any, base: ReadScopeFixture, version_id: str) -> str:
    """Author one composition edge between two family revisions the fixture holds."""

    composition_id = str(uuid4())
    compositions.insert_composition(
        store,
        FamilyComposition(
            composition_id=composition_id,
            from_family_revision_id=base.family.revision_id,
            to_family_revision_id=base.direct_family.revision_id,
            policy_id=POLICY_ID,
            policy_version_id=version_id,
            provenance=base.authorship,
            repository_id=store.repository_id,
        ),
    )
    return composition_id


def _construct(
    scopes: _ScopeFixture, *, sides: tuple[str, ...] = ("base", "candidate"), **overrides: Any
) -> Any:
    sources = scopes.sources(sides)
    try:
        return construct_registered_scope(scopes.request(**overrides), sources)
    finally:
        for source in sources:
            source.store.close()


# ---------------------------------------------------------------------------
# The construction: recorded inputs, provenance, policy identity, membership.


def test_a_declared_scope_is_constructed_with_its_policy_identity_and_resolved_membership(
    scopes: _ScopeFixture,
) -> None:
    """§1.1/§1.2: the scope records its construction version, its resolved policy and its members.

    Catches a scope reported without the policy version it was built under -- a scope that cannot say
    which traversal produced it is not reproducible, and every later claim about what the run
    examined would rest on a policy nobody recorded.
    """

    outcome = _construct(scopes)

    assert outcome.constructed(), outcome.refusal
    manifest = outcome.manifest
    assert manifest.construction_version == SCOPE_CONSTRUCTION_VERSION
    assert manifest.policy_identity == (POLICY_ID, scopes.version_id, DECLARED_VERSION)
    assert manifest.scope_id == "scope-B-M"
    assert scopes.composition_id in manifest.followed_composition_ids()
    assert manifest.membership.invariant_revision_ids
    assert manifest.membership.realization_claim_ids
    assert manifest.membership.family_revision_ids


def test_every_followed_edge_keeps_the_snapshot_it_was_read_from(scopes: _ScopeFixture) -> None:
    """§1.1/§1.6: each edge carries its own snapshot provenance, on both sides of the declaration.

    Catches an edge attributed to no side, or to one side only: a union read that reported one
    provenance for both sides would erase the historical-lookup fact §1.6 rests on.
    """

    manifest = _construct(scopes).manifest

    recorded = {(edge.edge_kind, edge.mapping_side) for edge in manifest.followed_edges}
    assert ("source_to_invariant", "base") in recorded
    assert ("source_to_invariant", "candidate") in recorded
    assert ("invariant_to_family", "base") in recorded
    assert ("invariant_to_family", "candidate") in recorded
    assert ("composition", "candidate") in recorded
    assert {edge.mapping_side for edge in manifest.followed_edges} <= {"base", "candidate"}


def test_the_same_declaration_over_the_same_snapshots_yields_the_same_scope(
    scopes: _ScopeFixture,
) -> None:
    """§1.1: the construction is deterministic, so two runs compare equal field for field.

    Catches a construction whose membership depends on row or dictionary order: the scope would then
    differ between two runs that declare the same inputs, and "the same declaration yields the same
    scope" would be false exactly where it is hardest to notice.
    """

    first = _construct(scopes).manifest
    second = _construct(scopes).manifest

    assert first == second
    assert first.followed_edges == tuple(first.followed_edges)


def test_a_declaration_with_no_policy_follows_no_composition_edge(scopes: _ScopeFixture) -> None:
    """§1.2: absence never widens. A scope declared without a policy records no policy identity.

    Catches widening without a recorded policy -- the state §1.2 refuses -- by making the default a
    scope with no composition edge rather than one that resolved a policy for the caller.
    """

    outcome = _construct(scopes, policy_id=None, policy_version_id=None)

    assert outcome.constructed(), outcome.refusal
    assert outcome.manifest.policy_identity is None
    assert outcome.manifest.followed_composition_ids() == ()
    assert outcome.manifest.membership.family_revision_ids


def test_membership_is_never_inferred_from_a_path_prefix_or_a_label(scopes: _ScopeFixture) -> None:
    """§1.3: a path prefix compared against a stored anchor is a resolution fact, not a membership.

    Catches the widening §1.3 forbids in its most likely disguise: a declared path that is a *prefix*
    of a stored anchor path selects nothing, because the lookup is an exact equality against recorded
    authored data rather than a resolution against a tree.
    """

    outcome = _construct(scopes, changed_paths=(INTEGRATION_PATH.rsplit("/", 1)[0] + "/",))

    assert outcome.constructed(), outcome.refusal
    assert outcome.manifest.membership.realization_claim_ids == ()
    assert outcome.manifest.membership.source_anchor_ids == ()
    assert outcome.manifest.followed_edges == ()


def test_the_read_frontiers_selection_surface_is_not_an_input_to_construction() -> None:
    """§1.4: the read path's advertised frontier is not consulted, so the two axes cannot be conflated.

    This is a derivation over the module's own text rather than an assurance: the shipped selection
    surface (``select_recorded_scope`` and its paging and expansion helpers) does not appear in the
    construction module at all, so no composition edge can reach a retrieval policy through it.
    """

    source = Path("mcp/src/agents_remember/memory/knowledge/registered_scope.py").read_text()

    for forbidden in (
        "select_recorded_scope",
        "page_of_scope",
        "_frontier_expansions",
        "PageRequest",
    ):
        assert forbidden not in source
    assert "from agents_remember.memory.knowledge.read import" not in source


# ---------------------------------------------------------------------------
# The refusals. Each names the exact missing input, and none falls back to anything.


def test_a_missing_declared_snapshot_is_refused_naming_the_exact_snapshot(
    scopes: _ScopeFixture,
) -> None:
    """§1.8 / Failure And Recovery: an unresolvable declared snapshot is a refusal naming it.

    Catches the fallback the clause forbids: the refusal names the declared digest and is not answered
    by reading another dataset, by inferring membership from paths, or by a partial scope.
    """

    outcome = _construct(scopes, sides=("base",))

    assert outcome.state == "refused"
    assert outcome.manifest is None
    assert outcome.refusal is not None
    assert outcome.refusal.missing_input_kind == "declared_snapshot"
    assert (
        str(outcome.refusal.missing_input) == dataset_identity(scopes.candidate_path).logical_digest
    )
    assert outcome.refusal.refusal.operation == CONSTRUCT_SCOPE_OPERATION


def test_a_dataset_that_is_not_the_declared_snapshot_is_refused(scopes: _ScopeFixture) -> None:
    """§1.1: the declaration is compared against the bytes handed over, not accepted as a claim.

    Catches a run that declared one snapshot and read another -- the scope's own record of its inputs
    would be untrue, and every later statement about the examined inputs would inherit that.
    """

    swapped = (
        _declaration("base", scopes.candidate_path),
        _declaration("candidate", scopes.base.database_path),
    )
    outcome = _construct(scopes, snapshots=swapped)

    assert outcome.state == "refused"
    assert outcome.refusal is not None
    assert (
        str(outcome.refusal.missing_input) == dataset_identity(scopes.candidate_path).logical_digest
    )


def test_an_unrecorded_seed_family_revision_is_refused_naming_the_seed(
    scopes: _ScopeFixture,
) -> None:
    """§1.8: a traversal seed no snapshot holds is refused by name rather than dropped.

    Catches the silent-smaller-scope failure: dropping the seed would report a scope that is complete
    for a declaration the caller never made.
    """

    stranger = str(uuid4())
    outcome = _construct(scopes, seed_family_revision_ids=(stranger,))

    assert outcome.state == "refused"
    assert outcome.refusal is not None
    assert outcome.refusal.missing_input_kind == "seed_family_revision"
    assert outcome.refusal.missing_input == stranger


def test_an_undeclared_policy_version_is_refused_by_the_shared_traversal(
    scopes: _ScopeFixture,
) -> None:
    """§1.2: a policy version nobody declared is refused, never resolved to the only one stored.

    Catches policy resolution by convenience -- the defect that would make two traversals under
    different declared versions indistinguishable in the scope they produced.
    """

    outcome = _construct(scopes, policy_version_id=str(uuid4()))

    assert outcome.state == "refused"
    assert outcome.refusal is not None
    assert outcome.refusal.missing_input_kind == "traversal_policy"
    assert outcome.refusal.refusal.code == "invalid_payload"
    assert "not a declared policy version" in outcome.refusal.detail


def test_a_declaration_may_not_name_two_snapshots_for_one_side(scopes: _ScopeFixture) -> None:
    """§1.7: an ambiguous common base is a refusal, never an arbitrary base chosen to proceed.

    Catches the resolved-by-tie-break base: the declaration vocabulary has no representation for two
    bases, so the ambiguity fails where the caller can still fix it.
    """

    declaration = _declaration("base", scopes.base.database_path)
    with pytest.raises(ValidationError, match="ambiguous common base"):
        scopes.request(snapshots=(declaration, declaration))


def test_a_half_declared_policy_and_a_seed_without_one_are_both_refused(
    scopes: _ScopeFixture,
) -> None:
    """§1.2: a policy is an identity *and* a version, and a seed without one authorises nothing.

    Catches the two half-states: an identity without a version cannot be resolved by any registry, and
    a seed names a walk the declaration never authorised.
    """

    with pytest.raises(ValidationError, match="identity \\*and\\* a version"):
        scopes.request(policy_version_id=None)
    with pytest.raises(ValidationError, match="follows no composition edge"):
        scopes.request(policy_id=None, policy_version_id=None, seed_family_revision_ids=("seed",))


def test_a_recorded_link_may_not_carry_a_policy_and_a_composition_edge_must(
    scopes: _ScopeFixture,
) -> None:
    """§1.2: only an authored composition edge is a traversal, so only it carries a policy identity.

    Catches the two mislabels in one place: a recorded lookup reported as a policy execution, and a
    composition edge reported as followed without a policy -- which is widening without a record.
    """

    with pytest.raises(ValidationError, match="recorded lookup rather than a traversal"):
        FollowedScopeEdge(
            edge_kind="invariant_to_family",
            edge_id=str(uuid4()),
            mapping_side="base",
            from_record_id=str(uuid4()),
            to_record_id=str(uuid4()),
            policy_identity=(POLICY_ID, scopes.version_id, DECLARED_VERSION),
        )
    with pytest.raises(ValidationError, match="widening without a recorded policy"):
        FollowedScopeEdge(
            edge_kind="composition",
            edge_id=str(uuid4()),
            mapping_side="candidate",
            from_record_id=str(uuid4()),
            to_record_id=str(uuid4()),
        )


def test_a_declared_changed_path_may_not_carry_pathspec_magic() -> None:
    """§1.3: the declared path is compared for equality, so a resolvable spelling is refused.

    Catches a declared path being normalised, globbed or resolved: the lookup would then answer about
    a different location than the one the declaration named.
    """

    assert scope_path_is_recorded("src/integration.py") == "src/integration.py"
    with pytest.raises(ValueError, match="must not be blank"):
        scope_path_is_recorded("   ")
    for bad in ("/src/integration.py", ":(exclude)src/x.py", "../src/x.py"):
        with pytest.raises(ValueError, match="repository-relative"):
            scope_path_is_recorded(bad)
