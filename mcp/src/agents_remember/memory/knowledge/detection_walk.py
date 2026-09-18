"""The deterministic walk: which recorded facts match which declared detection condition.

This module owns the **detector** half of ``KS-R14@v1``. It reads what R07's selection and R08's
two-sided comparison already produce -- it is not a second selection rule, and the shipped statement
of that boundary is ``memory/knowledge/diff.py:7-9`` -- and it emits facts-only signals in a declared
deterministic total order.

Three properties are enforced here rather than documented:

* **The walk is structural.** :func:`detect_review_conditions` reads the comparison's recorded facts
  -- which claims' source *observations* moved, which items the union held, what each anchor resolved
  to -- and never the bytes behind them. A budget change and a comments-only change over the same
  attributed files therefore produce the *same* condition identity and the same signal identity,
  which is the acceptance shape of the responsibility boundary: the detector reports a changed
  observation and the curator decides what it means.
* **Every signal is assembled once.** :func:`_emit` is the only place a signal is built, so the
  required field set, the closed vocabularies and the derived detail string cannot be satisfied on one
  path and skipped on another.
* **A group keeps every contributing match.** A grouped signal carries each contributing claim's own
  relationship path and edges, because requirement 1.5 permits grouping and forbids losing what was
  grouped.

The walk owns no SQL, no transaction and no lock. The records it produces are written by
:mod:`agents_remember.memory.knowledge.detection`, which is where the store's own refusals live, so
the two modules split along the property each one protects rather than along a call boundary.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from uuid import NAMESPACE_URL, uuid5

from agents_remember.models.knowledge.detection import (
    CONDITION_VOCABULARY_VERSION,
    DETECTION_CONDITIONS,
    DETECTION_EXTRACTOR_VERSION,
    DETECTION_POLICY_VERSION,
    NO_SEMANTIC_ASSESSMENT_LIMITATION,
    DetectionChangeGranularity,
    DetectionCondition,
    DetectionCounterpartProbe,
    DetectionInputSide,
    DetectionLimitation,
    DetectionObservedChange,
    DetectionRecordedInputSet,
    DetectionRelationshipPath,
    DetectionScopeManifest,
    DetectionSignalPayload,
    observed_basis_detail,
)
from agents_remember.models.knowledge.diff import KnowledgeDiffItem, KnowledgeDiffResult

__all__ = [
    "DetectionWalkInput",
    "detect_review_conditions",
]


# ---------------------------------------------------------------------------
# The deterministic walk over the shipped comparison.


@dataclass(frozen=True)
class DetectionWalkInput:
    """Everything one walk reads: the shipped comparison, and the inputs it was made against.

    ``comparison`` is R08's own result, unchanged and not redefined: the walk reads its union items,
    their coverage and their two independent change statements, and it never re-selects anything.
    ``unmapped_changed_paths`` is R08's own advertised gap, carried through rather than dropped,
    because dropping it would hide exactly the changes a reviewer most needs to see.
    """

    repository_id: str
    governing_route_id: str
    comparison: KnowledgeDiffResult
    input_sides: tuple[DetectionInputSide, ...]
    declared: str = "union_of_both_sides"
    scope_manifest: DetectionScopeManifest | None = None
    unmapped_changed_paths: tuple[str, ...] = ()
    truncated: bool = False


def _signal_id(condition: str, group_key: str) -> str:
    """Return the stable identity of one detected condition, derived from its group key.

    The identity is a function of *which recorded group* the condition is about and which condition
    it is -- never of the bytes behind the group. That is what makes two walks over the same recorded
    structure produce the same ordered sequence of identities, so reproducibility is a comparison of
    two sequences rather than of two sets, and it is why a scenario and its harmless control produce
    the same signal identity rather than two signals that merely share a condition.
    """

    return str(uuid5(NAMESPACE_URL, f"ar-detection/v1/{condition}/{group_key}"))


@dataclass(frozen=True)
class _ClaimFacts:
    """One union realization item, reduced to the recorded facts the walk classifies on."""

    item_id: str
    claim_id: str
    group_key: str
    invariant_revision_id: str
    path: str | None
    locator_kind: str | None
    role: str | None
    source_observation_changed: bool
    record_field_changed: bool
    removed_from_after: bool
    resolution: str | None
    recorded_identity: str | None
    observed_identity: str | None


def _claim_facts(item: KnowledgeDiffItem) -> _ClaimFacts:
    """Reduce one union realization item to the recorded facts, reading no source bytes."""

    payload = item.before if item.before is not None else item.after
    anchor = None if payload is None else payload.anchor
    change = item.source_change
    claim_id = str(item.record_id or item.item_id)
    return _ClaimFacts(
        item_id=item.item_id,
        claim_id=claim_id,
        group_key=claim_id,
        invariant_revision_id=str(
            (payload.invariant_revision_id if payload is not None else None) or ""
        ),
        path=None if anchor is None else anchor.path,
        locator_kind=None if anchor is None else _locator_kind(anchor.locator),
        role=None if payload is None else payload.role,
        source_observation_changed=bool(change and change.source_observation_changed),
        record_field_changed=bool(change and change.record_field_changed),
        removed_from_after=item.before is not None and item.after is None,
        resolution=None if anchor is None else anchor.resolution,
        recorded_identity=None if anchor is None else anchor.recorded_source_identity,
        observed_identity=None if anchor is None else anchor.observed_source_identity,
    )


def _locator_kind(locator: object) -> str | None:
    """Return one recorded locator's kind, whatever discriminated shape it was stored as."""

    kind = getattr(locator, "kind", None)
    return None if kind is None else str(kind)


def _regroup_shared_items(claims: Sequence[_ClaimFacts]) -> tuple[_ClaimFacts, ...]:
    """Return the union's realization items with the group key each single-item signal is named by.

    One realization claim *record* can be spoken for by more than one union item -- the union's own
    unit is the item, and two coverage items of one claim are two recorded facts about it -- so the
    record identity is not always a key that names one item. A condition about a single attribution
    ("this claim's anchor path is absent", "this claim's attribution is gone from the after side")
    is a condition about the item that carried that fact, and naming it by the shared record
    identity emits two signals under one identity, which the run's own reproducibility rule refuses
    -- correctly, because a total order that names an identity twice is not an order over a set.

    So the record identity stays the group key while it names exactly one item in this union, and
    the item identity -- the union's own stable key for the record it carries -- takes over exactly
    when several items share the record. Both are read from the comparison, so two walks over the
    same recorded structure still produce the same ordered identities, and the ordinary one-item
    case keeps the identity it has always had. What a relationship *path* reaches is unaffected:
    a path's edges name the claim record, which is what the record identity is for.
    """

    counts: dict[str, int] = {}
    for claim in claims:
        counts[claim.claim_id] = counts.get(claim.claim_id, 0) + 1
    return tuple(
        replace(
            claim,
            group_key=claim.item_id if counts[claim.claim_id] > 1 else claim.claim_id,
        )
        for claim in claims
    )


def _items(result: KnowledgeDiffResult) -> tuple[KnowledgeDiffItem, ...]:
    if result.page is None:
        return ()
    return result.page.items


def _family_members(
    items: Sequence[KnowledgeDiffItem],
) -> Mapping[str, tuple[str, ...]]:
    """Return each family revision's member invariant revisions, from the union's memberships."""

    members: dict[str, list[str]] = {}
    for item in items:
        if item.kind != "membership":
            continue
        payload = item.before if item.before is not None else item.after
        if payload is None or payload.family_revision_id is None:
            continue
        if payload.invariant_revision_id is None:
            continue
        members.setdefault(payload.family_revision_id, []).append(payload.invariant_revision_id)
    return {family: tuple(sorted(set(rows))) for family, rows in members.items()}


def _family_revision_ids(items: Sequence[KnowledgeDiffItem]) -> tuple[str, ...]:
    """Return the family revisions the union holds, in the comparison's own stream order."""

    seen: list[str] = []
    for item in items:
        if item.kind != "family" or item.revision_id is None:
            continue
        if item.revision_id not in seen:
            seen.append(item.revision_id)
    return tuple(seen)


def detect_review_conditions(walk: DetectionWalkInput) -> tuple[DetectionSignalPayload, ...]:
    """Walk the recorded graph and return one facts-only signal per matched condition.

    The walk implements the five conditions the detection policy declares, over the union R08
    selected. It reads only recorded structure -- coverage, change statements, anchor resolutions and
    the membership edges -- and it emits its signals in the **declared deterministic total order**:
    the condition vocabulary's own order first, then the recorded group key. Nothing about the bytes
    behind a changed observation can move either, which is what makes a scenario and its harmless
    control indistinguishable to the detector by construction.
    """

    items = _items(walk.comparison)
    claims = _regroup_shared_items(
        tuple(_claim_facts(item) for item in items if item.kind == "realization")
    )
    members = _family_members(items)
    claims_by_invariant: dict[str, list[_ClaimFacts]] = {}
    for claim in claims:
        claims_by_invariant.setdefault(claim.invariant_revision_id, []).append(claim)

    emitted: list[tuple[int, str, DetectionSignalPayload]] = []
    for family_revision_id in _family_revision_ids(items):
        family_members = members.get(family_revision_id, ())
        contributing = tuple(
            claim
            for member in family_members
            for claim in claims_by_invariant.get(member, ())
            if claim.source_observation_changed
        )
        siblings = tuple(
            claim
            for member in family_members
            for claim in claims_by_invariant.get(member, ())
            if not claim.source_observation_changed
        )
        if len(contributing) >= 2:
            emitted.append(
                _family_signal(
                    walk,
                    family_revision_id,
                    "source_changed_on_both_sides_joined_to_same_family",
                    contributing,
                    siblings,
                )
            )
        elif len(contributing) == 1 and siblings:
            emitted.append(
                _family_signal(
                    walk,
                    family_revision_id,
                    "one_sided_source_change_with_recorded_siblings",
                    contributing,
                    siblings,
                )
            )
    for item in items:
        if item.kind in ("invariant", "family") and item.changed_fields:
            emitted.append(_record_change_signal(walk, item))
    for claim in claims:
        if claim.resolution == "path_absent":
            emitted.append(_claim_signal(walk, claim, "absent_anchor", "after"))
        if claim.removed_from_after:
            emitted.append(
                _claim_signal(walk, claim, "removed_or_reparented_attribution", "before")
            )

    emitted.sort(key=lambda row: (row[0], row[1]))
    return tuple(row[2] for row in emitted)


def _family_signal(
    walk: DetectionWalkInput,
    family_revision_id: str,
    condition: DetectionCondition,
    contributing: tuple[_ClaimFacts, ...],
    siblings: tuple[_ClaimFacts, ...],
) -> tuple[int, str, DetectionSignalPayload]:
    """Build one family-conditioned signal, retaining every contributing match and its paths."""

    paths = tuple(
        DetectionRelationshipPath(
            path_id=f"family:{family_revision_id}/claim:{claim.claim_id}",
            snapshot_side="after",
            edges=(family_revision_id, claim.invariant_revision_id, claim.claim_id),
            reached_item_id=claim.item_id,
            realization_role=claim.role,  # type: ignore[arg-type]
        )
        for claim in contributing + siblings
    )
    changes = tuple(
        DetectionObservedChange(
            item_id=claim.item_id,
            item_kind="realization",
            granularity=_granularity(claim.locator_kind),
            path=claim.path,
            recorded_identity=claim.recorded_identity,
            observed_identity=claim.observed_identity,
            locator_kind=claim.locator_kind,
        )
        for claim in contributing
    )
    return _emit(
        walk,
        _MatchedCondition(
            condition=condition,
            group_key=family_revision_id,
            changes=changes,
            paths=paths,
        ),
    )


def _record_change_signal(
    walk: DetectionWalkInput, item: KnowledgeDiffItem
) -> tuple[int, str, DetectionSignalPayload]:
    """Build one signal for an edited invariant, family or evidence record."""

    payload = item.before if item.before is not None else item.after
    granularity: DetectionChangeGranularity = (
        "invariant_statement_changed" if item.kind == "invariant" else "family_statement_changed"
    )
    change = DetectionObservedChange(
        item_id=item.item_id,
        item_kind=str(item.kind),
        granularity=granularity,
        path=None,
        recorded_identity=None if payload is None else payload.revision_id,
        observed_identity=None if payload is None else payload.revision_id,
        locator_kind=None,
    )
    path = DetectionRelationshipPath(
        path_id=f"record:{item.item_id}",
        snapshot_side="after",
        edges=(str(item.record_id or item.item_id),),
        reached_item_id=item.item_id,
    )
    return _emit(
        walk,
        _MatchedCondition(
            condition="edited_invariant_family_or_evidence_record",
            group_key=item.item_id,
            changes=(change,),
            paths=(path,),
        ),
    )


def _claim_signal(
    walk: DetectionWalkInput,
    claim: _ClaimFacts,
    condition: DetectionCondition,
    side: str,
) -> tuple[int, str, DetectionSignalPayload]:
    """Build one signal for a single-claim condition, with its recorded path."""

    change = DetectionObservedChange(
        item_id=claim.item_id,
        item_kind="realization",
        granularity=(
            "anchor_resolved_elsewhere"
            if condition == "absent_anchor"
            else "realization_claim_record_changed"
        ),
        path=claim.path,
        recorded_identity=claim.recorded_identity,
        observed_identity=claim.observed_identity,
        locator_kind=claim.locator_kind,
    )
    path = DetectionRelationshipPath(
        path_id=f"claim:{claim.claim_id}",
        snapshot_side=side,  # type: ignore[arg-type]
        edges=(claim.invariant_revision_id, claim.claim_id),
        reached_item_id=claim.item_id,
        realization_role=claim.role,  # type: ignore[arg-type]
    )
    return _emit(
        walk,
        _MatchedCondition(
            condition=condition, group_key=claim.group_key, changes=(change,), paths=(path,)
        ),
    )


@dataclass(frozen=True)
class _MatchedCondition:
    """One matched condition's own basis: which condition, which recorded group, and what was seen.

    The four travel together because they are one fact -- a condition is always a condition *about*
    a recorded group, observed through these changes and these paths -- and a builder handed them
    separately could be handed a change belonging to another group's signal.
    """

    condition: DetectionCondition
    group_key: str
    changes: tuple[DetectionObservedChange, ...]
    paths: tuple[DetectionRelationshipPath, ...]


def _emit(
    walk: DetectionWalkInput,
    matched: _MatchedCondition,
    extra_limitations: tuple[DetectionLimitation, ...] = (),
) -> tuple[int, str, DetectionSignalPayload]:
    """Assemble one signal, with the limitations the recorded facts require and no others."""

    condition, group_key = matched.condition, matched.group_key
    scope = walk.scope_manifest or _unretained_manifest(group_key)
    limitations = _walk_limitations(matched.changes, walk)
    for limitation in extra_limitations:
        if limitation not in limitations:
            limitations = (*limitations, limitation)
    signal = DetectionSignalPayload(
        signal_id=_signal_id(condition, group_key),
        repository_id=walk.repository_id,
        governing_route_id=walk.governing_route_id,
        condition=condition,
        condition_vocabulary_version=CONDITION_VOCABULARY_VERSION,
        input_set=_recorded_input_set(walk),
        observed_changes=matched.changes,
        relationship_paths=matched.paths,
        extractor_version=DETECTION_EXTRACTOR_VERSION,
        policy_version=DETECTION_POLICY_VERSION,
        scope_manifest=scope,
        registered_scope_status=(
            "incomplete_scan" if walk.truncated else "complete_for_declared_policy"
        ),
        unmapped_changed_paths=walk.unmapped_changed_paths,
        limitations=limitations,
        detail=observed_basis_detail(
            condition=condition,
            relationship_paths=tuple(path.path_id for path in matched.paths),
            limitations=limitations,
        ),
    )
    return (DETECTION_CONDITIONS.index(condition), group_key, signal)


def _walk_limitations(
    changes: tuple[DetectionObservedChange, ...], walk: DetectionWalkInput
) -> tuple[DetectionLimitation, ...]:
    """Return the limitations the recorded facts require, plus the unconditional one.

    Each member is derived from a recorded fact rather than added by habit, because the signal's own
    validator refuses a limitation that has no omission behind it as well as an omission with no
    limitation: a declaration a record does not need teaches a reviewer to ignore the field.
    """

    limitations: list[DetectionLimitation] = []
    if walk.unmapped_changed_paths:
        limitations.append("unmapped_changed_paths")
    if walk.truncated:
        limitations.append("truncated_scan")
    if any(change.granularity == "anchor_resolved_elsewhere" for change in changes):
        limitations.append("missing_attribution")
    if _has_unread_locator(walk.comparison):
        limitations.append("unsupported_locator")
    if walk.declared == "trigger_side_only":
        limitations.append("no_counterpart_read")
    if _declared_probe_omission(walk.comparison):
        limitations.append("records_present_outside_the_declared_selection")
    limitations.append(NO_SEMANTIC_ASSESSMENT_LIMITATION)
    return tuple(limitations)


def _has_unread_locator(result: KnowledgeDiffResult) -> bool:
    """Return whether the scan met a recorded locator no extractor in this increment supports.

    The shipped resolver reports ``unsupported_locator`` with the reason that no symbol extractor
    supports it (``memory/knowledge/read_anchors.py:132-139``). That is a *declared scope
    limitation* and not a negative match: the detector reports what it read but could not map, and
    the anchor still carries its recorded claim and blob identity.
    """

    return any(
        _anchor_state(item) == "unsupported_locator"
        for item in _items(result)
        if item.kind == "realization"
    )


def _anchor_state(item: KnowledgeDiffItem) -> str | None:
    payload = item.before if item.before is not None else item.after
    if payload is None or payload.anchor is None:
        return None
    return str(payload.anchor.resolution)


def _declared_probe_omission(result: KnowledgeDiffResult) -> bool:
    """Return whether the shipped comparison omitted an item for the declared-selection reason.

    The omission is R08's own recorded fact -- an :class:`OmittedChanges` with the reason
    ``present_outside_the_declared_selection`` -- and this reads it rather than re-deriving it, so
    the signal's declaration and the comparison's omission cannot disagree.
    """

    return any(
        omission.reason == "present_outside_the_declared_selection" for omission in result.omissions
    )


def _recorded_input_set(walk: DetectionWalkInput) -> DetectionRecordedInputSet:
    """Return the recorded discriminator one declaration requires, from the walk's own sides."""

    if walk.declared == "both_sides_declared":
        return DetectionRecordedInputSet(declared="both_sides_declared", sides=walk.input_sides)
    if walk.declared == "trigger_side_only":
        trigger = tuple(side for side in walk.input_sides if side.side == "trigger")
        return DetectionRecordedInputSet(
            declared="trigger_side_only", sides=trigger or walk.input_sides[:1]
        )
    return DetectionRecordedInputSet(
        declared="union_of_both_sides",
        sides=walk.input_sides,
        counterpart_probe=_probe_from(walk.comparison),
        probe_omission_declared=_declared_probe_omission(walk.comparison),
    )


def _probe_from(result: KnowledgeDiffResult) -> tuple[DetectionCounterpartProbe, ...]:
    """Return the counterpart probe's recorded outcome per reported union item.

    This is R08's own coverage value, read off the union the comparison already produced: the probe
    is not re-run here, and the vocabulary is the shipped :data:`DiffCoverage` rather than a second
    one. An empty union is a *recorded* probe over no items, which is different from a missing probe
    -- the union declaration is refused for a missing one, never for an empty one.
    """

    return tuple(
        DetectionCounterpartProbe(
            item_id=item.item_id, item_kind=str(item.kind), coverage=item.coverage
        )
        for item in _items(result)
    )


def _granularity(locator_kind: str | None) -> DetectionChangeGranularity:
    """Return the *observed* granularity one locator kind supports, and no finer one."""

    if locator_kind in ("line_range", "byte_range"):
        return "attributed_span_changed"
    return "source_file_changed"


def _unretained_manifest(group_key: str) -> DetectionScopeManifest:
    """Return the manifest reference a walk records when no destination has been verified yet.

    It is deliberately *not* a fabricated durable destination: nothing in this leaf publishes, and a
    manifest whose destination has not been checked is recorded as requiring retention at a
    destination that is not the durable route, so
    :meth:`DetectionScopeManifest.resolve` reports it unresolved until ``KS-R12@v1``'s route
    resolves it.
    """

    return DetectionScopeManifest(
        manifest_ref=f"scope-manifest/{group_key}",
        retention_required=True,
        destination_kind="regenerable_worklist",
        destination_ref=None,
        retention_basis=(
            "a durable assessment that cites this signal needs the exact revisions, observed source "
            "identities and followed edges behind it"
        ),
    )
