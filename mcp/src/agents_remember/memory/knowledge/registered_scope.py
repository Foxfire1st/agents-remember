"""Constructing the registered review scope from recorded links and one declared traversal policy.

``KS-R16@v1`` §1 owns this act and ``KS-R14@v1`` owns what a *run over* the scope could not resolve, so
the split is: this module produces the scope -- the declared snapshots, the followed edges with their
snapshot provenance, the policy identity and the resolved membership -- and reports nothing about a
detection run's limitations. Neither half is restated in the other's output.

The construction is deterministic in the strong sense §1.1 asks for: the same declaration over the same
snapshots under the same construction version yields the same scope, because every step is a declared
lookup in a declared order and every result is sorted by recorded identity rather than by whatever order
rows came back in. Concretely:

1. the declared pair is resolved to two exact datasets, and a declaration whose snapshot the run cannot
   produce is refused by name rather than approximated from another file;
2. each declared changed path is looked up by **exact equality** against the recorded anchor paths, and
   every hit contributes a ``source -> invariant`` edge carrying the side it was read from;
3. the invariant revisions reached **on one side** contribute that side's recorded memberships as
   ``invariant -> family`` edges, each carrying its own side;
4. composition edges are followed **only** from the declared seeds and the reached family revisions,
   **only** when the declaration names a policy version, and the traversal is ``KS-R17@v1``'s own
   :func:`follow_composition_scope` -- not a second walk, and not a re-reading of its rule.

Three absences are as load-bearing as the steps. The R07 read frontier is never consulted and no
selection, count or advertised expansion enters here (§1.4): this module imports nothing from the
retrieval read, so the two axes cannot be conflated by accident. Membership is never inferred from a
name, a prefix, a folder or a symbol (§1.3): every member arrives through a recorded row. And an
unresolvable declared input is a refusal that names it (§1.8), never a partial scope -- a scope that
quietly held less than it declared would make every later "the run examined the scope" claim false.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from agents_remember.memory.knowledge.composition_policies import get_policy_version
from agents_remember.memory.knowledge.composition_traversal import (
    CompositionScope,
    follow_composition_scope,
)
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.read_queries import (
    family_revision_is_recorded,
    fetch_memberships_of_invariants,
    fetch_realizations_at_path,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeRefused,
    RefusalFacts,
    composition_policy_refusal,
    refusal,
)
from agents_remember.memory.knowledge.store import OpenedKnowledgeStore
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.registered_scope import (
    FollowedScopeEdge,
    RegisteredScopeManifest,
    RegisteredScopeMembership,
    RegisteredScopeRequest,
    ScopeConstructionRefusal,
    ScopeMissingInputKind,
    ScopeSnapshotDeclaration,
    ScopeSnapshotSide,
)

__all__ = [
    "CONSTRUCT_SCOPE_OPERATION",
    "RegisteredScopeResult",
    "ScopeSnapshotSource",
    "construct_registered_scope",
    "snapshot_source",
]

# The one operation name a construction refusal carries. It is its own member of the operation
# vocabulary for the reason ``follow_family_composition`` is: constructing the §8 registered review
# scope is not a retrieval selection and not a variant of the composition traversal -- it *calls* that
# traversal. No refusal *code* is added by this leaf anywhere: the members of
# ``KnowledgeRefusalCode`` are unchanged, and a construction refusal reuses the shipped codes.
CONSTRUCT_SCOPE_OPERATION = "construct_registered_scope"

# The declared sides, in the order a construction visits them, so the edge list is a function of the
# declaration rather than of dictionary iteration.
_VISIT_ORDER: tuple[ScopeSnapshotSide, ...] = ("base", "candidate")


@dataclass(frozen=True)
class ScopeSnapshotSource:
    """One declared side's dataset, as the exact snapshot identity and the store opened over it.

    The identity travels with the handle so a construction compares the declaration against what it
    was actually given: a store whose content is not the declared snapshot is a different input, and
    reporting the declaration as satisfied would make the scope's own record of its inputs untrue.
    """

    side: ScopeSnapshotSide
    snapshot: SnapshotIdentity
    store: OpenedKnowledgeStore


@dataclass(frozen=True)
class RegisteredScopeResult:
    """The typed outcome of one construction: the manifest, or one refusal naming the missing input."""

    state: Literal["constructed", "refused"]
    scope_id: str
    manifest: RegisteredScopeManifest | None = None
    refusal: ScopeConstructionRefusal | None = None

    def constructed(self) -> bool:
        """Return whether this outcome is a scope rather than a refusal."""

        return self.state == "constructed"


def snapshot_source(
    side: ScopeSnapshotSide, database_path: Path, store: OpenedKnowledgeStore
) -> ScopeSnapshotSource:
    """Build one declared side's source, reading the dataset's own identity rather than a claim.

    The identity is read from the file through ``dataset_identity``, which opens it read-only and
    validates the bound namespace, so a caller cannot declare one snapshot and hand over another: the
    construction compares the two, and the comparison is against bytes rather than a description.
    """

    return ScopeSnapshotSource(side=side, snapshot=dataset_identity(database_path), store=store)


def construct_registered_scope(
    request: RegisteredScopeRequest,
    sources: Sequence[ScopeSnapshotSource],
) -> RegisteredScopeResult:
    """Construct the registered review scope, or refuse naming the exact input that blocked it.

    The returned scope is complete for its declared policy by construction: every declared side was
    resolved, every declared path was looked up, and -- when a policy was declared -- every seed family
    revision was traversed under that exact policy version. A failure at any of those points is
    returned as a refusal, so a caller never receives a scope that silently holds less than its own
    declaration says it examined.
    """

    resolved = _resolve_sources(request, sources)
    if isinstance(resolved, ScopeConstructionRefusal):
        return RegisteredScopeResult(state="refused", scope_id=request.scope_id, refusal=resolved)
    missing_seed = _first_unrecorded_seed(request, resolved)
    if missing_seed is not None:
        return RegisteredScopeResult(
            state="refused",
            scope_id=request.scope_id,
            refusal=_seed_refusal(request, missing_seed),
        )
    try:
        manifest = _assemble(request, resolved)
    except KnowledgeRefused as blocked:
        return RegisteredScopeResult(
            state="refused",
            scope_id=request.scope_id,
            refusal=_traversal_refusal(request, blocked),
        )
    return RegisteredScopeResult(state="constructed", scope_id=request.scope_id, manifest=manifest)


# ---------------------------------------------------------------------------
# Step 1: the declared pair, resolved to exact datasets.


def _resolve_sources(
    request: RegisteredScopeRequest,
    sources: Sequence[ScopeSnapshotSource],
) -> Mapping[ScopeSnapshotSide, ScopeSnapshotSource] | ScopeConstructionRefusal:
    """Resolve every declared side to the dataset that actually holds the declared snapshot."""

    supplied: dict[ScopeSnapshotSide, ScopeSnapshotSource] = {}
    for source in sources:
        supplied.setdefault(source.side, source)
    resolved: dict[ScopeSnapshotSide, ScopeSnapshotSource] = {}
    for declared in request.snapshots:
        found = supplied.get(declared.side)
        if found is None:
            return _missing_snapshot(request, declared, "<no dataset supplied for this side>")
        if found.snapshot != declared.snapshot:
            return _missing_snapshot(
                request,
                declared,
                f"the dataset supplied for {declared.side} holds "
                f"{found.snapshot.logical_digest} under {found.snapshot.schema_version}",
            )
        resolved[declared.side] = found
    return resolved


def _missing_snapshot(
    request: RegisteredScopeRequest,
    declared: ScopeSnapshotDeclaration,
    observed: str,
) -> ScopeConstructionRefusal:
    """Refuse a declared side the run cannot produce, naming the exact snapshot identity."""

    return _refusal(
        request,
        kind="declared_snapshot",
        missing_input=declared.snapshot.logical_digest,
        detail=(
            f"the registered review scope cannot be constructed: the declared {declared.side} "
            f"snapshot {declared.snapshot.logical_digest} could not be resolved ({observed}). The "
            "construction does not fall back to another dataset, does not infer membership from paths "
            "and does not proceed with a partial scope"
        ),
        next_action=(
            "Supply the exact declared snapshot for this side, or declare the snapshot the run can "
            "actually read."
        ),
    )


def _first_unrecorded_seed(
    request: RegisteredScopeRequest,
    resolved: Mapping[ScopeSnapshotSide, ScopeSnapshotSource],
) -> str | None:
    """Return the first declared seed no declared snapshot records, or ``None`` when all are recorded."""

    for seed in sorted(request.seed_family_revision_ids):
        recorded = any(
            family_revision_is_recorded(source.store.connection, source.store.repository_id, seed)
            for source in resolved.values()
        )
        if not recorded:
            return seed
    return None


# ---------------------------------------------------------------------------
# Steps 2-4: the declared lookups, then the declared traversal.


def _assemble(
    request: RegisteredScopeRequest,
    resolved: Mapping[ScopeSnapshotSide, ScopeSnapshotSource],
) -> RegisteredScopeManifest:
    """Build the manifest from the recorded links, then the declared traversal, in that order."""

    collected = _Collected()
    _follow_recorded_links(request, resolved, collected)
    policy_identity: tuple[str, str, str] | None = None
    if request.policy_id is not None and request.policy_version_id is not None:
        policy_identity = _follow_composition(
            request, resolved, sorted(collected.family_revision_ids), collected
        )
    return RegisteredScopeManifest(
        scope_id=request.scope_id,
        repository_id=request.repository_id,
        construction_version=request.construction_version,
        snapshots=request.snapshots,
        changed_paths=tuple(sorted(request.changed_paths)),
        policy_identity=policy_identity,
        followed_edges=tuple(_ordered(collected.edges)),
        membership=RegisteredScopeMembership(
            invariant_revision_ids=tuple(sorted(collected.invariant_revision_ids)),
            family_revision_ids=tuple(sorted(collected.family_revision_ids)),
            realization_claim_ids=tuple(sorted(collected.realization_claim_ids)),
            source_anchor_ids=tuple(sorted(collected.source_anchor_ids)),
            recorded_reference_refs=tuple(sorted(collected.recorded_reference_refs)),
        ),
    )


@dataclass
class _Collected:
    """The membership one construction accumulates, in the order it is discovered."""

    edges: list[FollowedScopeEdge] = field(default_factory=list)
    invariant_revision_ids: set[str] = field(default_factory=set)
    family_revision_ids: set[str] = field(default_factory=set)
    realization_claim_ids: set[str] = field(default_factory=set)
    source_anchor_ids: set[str] = field(default_factory=set)
    recorded_reference_refs: set[str] = field(default_factory=set)


def _follow_recorded_links(
    request: RegisteredScopeRequest,
    resolved: Mapping[ScopeSnapshotSide, ScopeSnapshotSource],
    collected: _Collected,
) -> None:
    """Follow the declared recorded links on both sides, retaining each edge's snapshot provenance.

    The sides are visited in the declared order and the paths in sorted order. The same path on both
    sides contributes two edges that differ in ``mapping_side``, which is §1.6 made visible: the union
    is a historical lookup for candidate discovery, so a link the candidate removed is still reported
    with the side that holds it.
    """

    for side in _VISIT_ORDER:
        declared = request.declared_side(side)
        if declared is None:
            continue
        source = resolved[side]
        reached_here = _follow_changed_paths(source, side, sorted(request.changed_paths), collected)
        _follow_memberships(source, side, sorted(reached_here), collected)


def _follow_changed_paths(
    source: ScopeSnapshotSource,
    side: ScopeSnapshotSide,
    paths: Sequence[str],
    collected: _Collected,
) -> set[str]:
    """Record every ``source -> invariant`` link one side holds for the declared paths."""

    reached_here: set[str] = set()
    for path in paths:
        rows = fetch_realizations_at_path(source.store.connection, source.store.repository_id, path)
        for row in rows:
            _collect_claim(row, side, collected)
            reached_here.add(str(row["invariant_revision_id"]))
    return reached_here


def _collect_claim(
    row: Mapping[str, object], side: ScopeSnapshotSide, collected: _Collected
) -> None:
    """Record one realization claim as a ``source -> invariant`` edge with the side it was read from."""

    claim_id = str(row["claim_id"])
    anchor_id = str(row["anchor_id"])
    revision_id = str(row["invariant_revision_id"])
    collected.edges.append(
        FollowedScopeEdge(
            edge_kind="source_to_invariant",
            edge_id=claim_id,
            mapping_side=side,
            from_record_id=anchor_id,
            to_record_id=revision_id,
        )
    )
    collected.realization_claim_ids.add(claim_id)
    collected.source_anchor_ids.add(anchor_id)
    collected.invariant_revision_ids.add(revision_id)
    collected.recorded_reference_refs.update(_origin_refs(row.get("provenance")))


def _follow_memberships(
    source: ScopeSnapshotSource,
    side: ScopeSnapshotSide,
    invariant_revision_ids: Sequence[str],
    collected: _Collected,
) -> None:
    """Record one side's family memberships of the revisions that side reached."""

    rows = fetch_memberships_of_invariants(
        source.store.connection, source.store.repository_id, invariant_revision_ids
    )
    for member_id, family_revision_id, invariant_revision_id in rows:
        collected.edges.append(
            FollowedScopeEdge(
                edge_kind="invariant_to_family",
                edge_id=member_id,
                mapping_side=side,
                from_record_id=invariant_revision_id,
                to_record_id=family_revision_id,
            )
        )
        collected.family_revision_ids.add(family_revision_id)


def _follow_composition(
    request: RegisteredScopeRequest,
    resolved: Mapping[ScopeSnapshotSide, ScopeSnapshotSource],
    seeds: Sequence[str],
    collected: _Collected,
) -> tuple[str, str, str]:
    """Follow declared composition edges from every seed under the declared policy version.

    The walk is ``KS-R17@v1``'s own, called once per seed, and its refusals propagate unchanged: an
    unknown policy version, an edge the policy does not admit and a step past the declared bound are
    that module's facts and this module does not re-word them. The resolved policy identity that comes
    back is what the manifest records beside the edges it produced.

    ``seeds`` is the declared seed list unioned with the family revisions the recorded links reached,
    so a family the changed paths joined is traversed from as well -- that is §1.1's order, and it is
    still gated on the declaration naming a policy version.
    """

    side, store = _traversal_source(resolved)
    targets = sorted(set(request.seed_family_revision_ids) | set(seeds))
    if not targets:
        return _declared_policy_identity(request, store)
    policy_identity: tuple[str, str, str] | None = None
    for seed in targets:
        scope: CompositionScope = follow_composition_scope(
            store, seed, str(request.policy_id), str(request.policy_version_id)
        )
        policy_identity = scope.policy_identity
        collected.family_revision_ids.update(scope.reached_family_revision_ids)
        _collect_composition_edges(store, side, scope, policy_identity, collected)
    if policy_identity is None:  # pragma: no cover - the loop above always assigns once
        raise KnowledgeRefused(_policy_refusal(request).refusal)
    return policy_identity


def _declared_policy_identity(
    request: RegisteredScopeRequest, store: OpenedKnowledgeStore
) -> tuple[str, str, str]:
    """Resolve a declared policy version when the recorded links reached no family revision.

    A declaration may legitimately name a policy and reach nothing to follow -- a changed path whose
    attributed invariants sit in no family is exactly that case. The scope must still record the
    policy version it was *built under*, so the identity is resolved from the same registry the
    traversal reads and a version nobody declared is refused with the shipped policy refusal rather
    than reported as an identity this code spelled itself.
    """

    declared = get_policy_version(store, str(request.policy_id), str(request.policy_version_id))
    if declared is None:
        raise KnowledgeRefused(
            composition_policy_refusal(
                CONSTRUCT_SCOPE_OPERATION,
                f"the scope declares policy {request.policy_id!r} version "
                f"{request.policy_version_id!r}, which is not a declared policy version in this "
                "namespace",
                record_id=str(request.policy_version_id),
                expected="a declared policy version",
                observed=f"{request.policy_id}/{request.policy_version_id}",
            )
        )
    return declared.identity


def _collect_composition_edges(
    store: OpenedKnowledgeStore,
    side: ScopeSnapshotSide,
    scope: CompositionScope,
    policy_identity: tuple[str, str, str],
    collected: _Collected,
) -> None:
    """Record each composition edge one traversal followed, with its endpoints and its policy."""

    for composition_id in scope.followed_composition_ids:
        endpoints = _composition_endpoints(store, composition_id)
        if endpoints is None:  # pragma: no cover - the traversal read the row it reports
            continue
        collected.edges.append(
            FollowedScopeEdge(
                edge_kind="composition",
                edge_id=composition_id,
                mapping_side=side,
                from_record_id=endpoints[0],
                to_record_id=endpoints[1],
                policy_identity=policy_identity,
            )
        )


def _composition_endpoints(
    store: OpenedKnowledgeStore, composition_id: str
) -> tuple[str, str] | None:
    """Read one followed composition row's endpoints, or report it as no longer recorded."""

    rows = store.connection.execute(
        "SELECT from_family_revision_id, to_family_revision_id FROM family_composition "
        "WHERE repository_id = ? AND composition_id = ?",
        (store.repository_id, composition_id),
    )
    row = next(iter(rows), None)
    if row is None:
        return None
    return (str(row[0]), str(row[1]))


def _traversal_source(
    resolved: Mapping[ScopeSnapshotSide, ScopeSnapshotSource],
) -> tuple[ScopeSnapshotSide, OpenedKnowledgeStore]:
    """Return the side a declared traversal runs against, and its store.

    Composition is authored repository data rather than a per-side observation, so the traversal is
    handed the candidate side's dataset when there is one: a policy version the candidate declares is
    the one that is executed, and the choice is recorded in the manifest through the resolved policy
    identity rather than left to whichever handle happened to be first. The side also becomes the
    provenance every edge the traversal produced is attributed to, so the two facts cannot disagree.
    """

    for side in ("candidate", "base"):
        source = resolved.get(side)
        if source is not None:
            return (side, source.store)
    raise AssertionError("a request declares at least one side")  # pragma: no cover


def _ordered(edges: Sequence[FollowedScopeEdge]) -> tuple[FollowedScopeEdge, ...]:
    """Return the followed edges in the declared deterministic order, with duplicates collapsed."""

    return tuple(
        sorted(
            set(edges),
            key=lambda edge: (
                edge.edge_kind,
                edge.mapping_side,
                edge.edge_id,
                edge.from_record_id,
                edge.to_record_id,
            ),
        )
    )


def _origin_refs(provenance: object) -> tuple[str, ...]:
    """Return the explicit sources one reached record was authored from, as it recorded them.

    The stored provenance envelope is read and nothing is inferred from it: a row that declared no
    source answers the empty tuple, which is a fact about the record rather than permission to supply
    one. This is the reference channel the reached records actually carry at this base --
    ``memory/knowledge/record_envelope.py:16`` records that a dedicated ``EvidenceClaim`` record group
    is a later leaf -- so a construction collects what is recorded instead of inventing an evidence
    link or carrying a field no record could populate.
    """

    if not isinstance(provenance, Mapping):
        return ()
    declared = provenance.get("origin_refs") or provenance.get("originRefs") or ()
    if isinstance(declared, str):
        return (declared,)
    return tuple(str(item) for item in declared)


# ---------------------------------------------------------------------------
# The refusals. Each names the exact missing input, which §1.8 requires.


def _seed_refusal(request: RegisteredScopeRequest, seed: str) -> ScopeConstructionRefusal:
    return _refusal(
        request,
        kind="seed_family_revision",
        missing_input=seed,
        detail=(
            f"the registered review scope declares {seed!r} as a traversal seed and no declared "
            "snapshot holds that family revision, so the traversal has no starting point. The "
            "construction reports the missing input rather than dropping the seed and reporting a "
            "smaller scope as the declared one"
        ),
        next_action="Declare a seed family revision the snapshots record, or remove the seed.",
    )


def _policy_refusal(request: RegisteredScopeRequest) -> ScopeConstructionRefusal:
    identity = f"{request.policy_id}/{request.policy_version_id}"
    return _refusal(
        request,
        kind="traversal_policy",
        missing_input=identity,
        detail=(
            f"the registered review scope declares traversal policy {identity} and no declared "
            "traversal under it produced a policy identity, so the scope cannot record the policy it "
            "was built under. A traversal is only reportable together with the version it executed"
        ),
        next_action="Declare a policy version the snapshots record, or declare no policy at all.",
    )


def _traversal_refusal(
    request: RegisteredScopeRequest, blocked: KnowledgeRefused
) -> ScopeConstructionRefusal:
    """Wrap one traversal refusal, keeping its own code, facts and next action unchanged."""

    inner = blocked.refusal
    observed = inner.observed or inner.record_id or ""
    return ScopeConstructionRefusal(
        missing_input_kind="traversal_policy",
        missing_input=observed or f"{request.policy_id}/{request.policy_version_id}",
        detail=(
            "the registered review scope's declared traversal refused, so no scope was constructed: "
            f"{inner.detail}"
        ),
        refusal=inner,
    )


def _refusal(
    request: RegisteredScopeRequest,
    *,
    kind: ScopeMissingInputKind,
    missing_input: str,
    detail: str,
    next_action: str,
) -> ScopeConstructionRefusal:
    """Build one construction refusal with its typed shipped refusal beside the named input."""

    del request
    typed = refusal(
        "invalid_reference",
        CONSTRUCT_SCOPE_OPERATION,
        detail,
        next_action=next_action,
        facts=RefusalFacts(
            record_id=missing_input,
            expected="a declared input the construction can resolve",
            observed=missing_input,
        ),
    )
    return ScopeConstructionRefusal(
        missing_input_kind=kind,
        missing_input=missing_input,
        detail=detail,
        refusal=typed,
    )
