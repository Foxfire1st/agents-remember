"""One staleness read of one retained revision: parts, grades and the regeneration message.

The route behind the review page needs one answer, not four: which parts of the page it shows are
stale, which grades are stale, what the trusted Explanation view states, and the text behind
"Regenerate stale (n parts · m grades)". This module composes those from the pure rules in
:mod:`agents_remember.canvas.explainer_staleness` against one **target comparison**, and it is the
only place that decides which comparison that is (MIK-R187 rule 4):

* while an environment lives, the target is its binding, recorded or planned;
* without one -- after Retire -- the target is the comparison the page shows, computed when read;
  on a ``planning`` leaf that is the planned comparison at the current tips (MIK-R189 rule 4), whose
  binding the caller builds at the tips it read and passes as ``on_screen``;
* with the selector on another number, that comparison is the target, and it is read-only.

A read is pure: it takes the site (task, leaf, retained manifest), the target and the grades'
store, and answers a record. It writes nothing, and it never turns an unread basis into a current
one -- every "could not be read" the target answers becomes a stale part or grade with its reason.

Two callers use the same record:

* the ``/state`` payload reads :meth:`StalenessRead.as_record` for the page's own lines;
* Update and Retire read the counts and the store's next state: ``regeneration.part_count`` and
  ``regeneration.grade_count`` are the Update's answer, and
  :func:`gone_grade_references` is the ``gone`` tuple ``GradeStore.prune`` already takes.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agents_remember.canvas.explainer_grades import Grade, Grades, GradeStore, grades_path
from agents_remember.canvas.explainer_staleness import (
    BlockFingerprint,
    BlockIdentity,
    GradeState,
    HunkCarry,
    HunkSides,
    PartStaleness,
    RegenerationRequest,
    TargetComparison,
    TargetReads,
    TrustedExplanation,
    carry_merged_hunk_grades,
    grade_staleness,
    moved_hunk_match,
    moved_sides,
    parse_block_identity,
    part_staleness,
    regeneration_request,
    stale_grade_references,
    trusted_list,
)
from agents_remember.canvas.explainer_state import ExplainerRefused
from agents_remember.canvas.explainer_target import ExplainerTargetReads
from agents_remember.models.knowledge.review_evidence import SupplementalReceipt
from agents_remember.models.knowledge.review_frame import FrameBinding, FrameManifest
from agents_remember.models.knowledge_files.unexplained import parse_hunk_subject

__all__ = [
    "GradeInputs",
    "GradeReconciliation",
    "StalenessRead",
    "gone_grade_references",
    "reconcile_grades",
    "screen_target",
    "staleness_read",
]


@dataclass(frozen=True)
class GradeInputs:
    """The leaf's grades, and the fingerprints they are compared against (rule 3).

    ``fingerprints`` is keyed by the reference a grade is *stored* under, not by the target block's
    own identity: the caller matches the revision's blocks to the target's (its per-block change
    set) and answers each stored reference's fingerprint there. A reference it cannot fingerprint is
    left out, and its grade reads stale with that reason -- a grade never reads current because its
    block could not be read.

    ``recorded_fingerprints`` is the proof entries' fingerprints as the revision recorded them. A
    revision from before that recording has none, and then only a proof entry's absence or its
    execution state can make the claim stale.
    """

    grades: Grades
    fingerprints: Mapping[str, BlockFingerprint] = field(default_factory=dict)
    recorded_fingerprints: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class GradeReconciliation:
    """The result of reconciling one leaf's grade store against one target comparison."""

    grades: Grades
    states: tuple[GradeState, ...] = ()
    stale_grades: tuple[str, ...] = ()
    gone_grades: tuple[str, ...] = ()
    carried_hunks: tuple[HunkCarry, ...] = ()
    removed_count: int = 0
    stale_count: int = 0


@dataclass(frozen=True)
class StalenessRead:
    """One revision's staleness against one target comparison, as the page and the hooks read it."""

    revision: int
    target: str
    binding: FrameBinding
    moved: tuple[str, ...]
    parts: PartStaleness
    trusted: TrustedExplanation
    grades: tuple[GradeState, ...]
    stale_grades: tuple[str, ...]
    regeneration: RegenerationRequest
    carried_hunks: tuple[HunkCarry, ...] = ()
    removed_grades: int = 0

    @property
    def stale(self) -> bool:
        """Whether anything at all is stale: a part, or a grade."""

        return bool(self.parts.parts or self.stale_grades)

    @property
    def stale_parts(self) -> tuple[str, ...]:
        return self.parts.part_ids

    def as_record(self) -> dict[str, Any]:
        """The plain shape of one ``/state`` answer: no model, no digest, no second reading."""

        return {
            "revision": self.revision,
            "target": self.target,
            "binding": self.binding.model_dump(mode="json", by_alias=True),
            "moved": list(self.moved),
            "stale": self.stale,
            "parts": self.parts.as_record(),
            "staleParts": list(self.stale_parts),
            "trusted": list(self.trusted.lines),
            "grades": [
                {
                    "reference": state.reference,
                    "category": state.grade.category,
                    "grade": state.grade.grade,
                    "gradeReason": state.grade.reason,
                    "stale": state.stale,
                    "reason": state.reason,
                }
                for state in self.grades
            ],
            "staleGrades": list(self.stale_grades),
            "carriedHunks": [carry.as_record() for carry in self.carried_hunks],
            "removedGrades": self.removed_grades,
            "regeneration": {
                "label": self.regeneration.label,
                "message": self.regeneration.message,
                "partCount": self.regeneration.part_count,
                "gradeCount": self.regeneration.grade_count,
            },
        }


def screen_target(
    *,
    task_root: Path,
    on_screen: FrameBinding | None = None,
    environment: FrameBinding | None = None,
    supplemental: Sequence[SupplementalReceipt] | None = None,
    reads: TargetReads | None = None,
) -> TargetComparison:
    """The one comparison a staleness read compares against (rule 4).

    ``on_screen`` is the comparison the page shows when that is not the environment's own binding: a
    selector opened on another number, a leaf read after Retire, or -- on a ``planning`` leaf -- the
    planned comparison built at the current tips. ``environment`` is the live environment's binding.
    With neither, there is nothing to compare against, and the read is refused by name rather than
    reporting a page that is silently current.

    ``reads`` lets a caller supply its own :class:`TargetReads` (a test's, or one already open);
    otherwise the real reads over this binding's trees answer, with ``supplemental`` as the target
    packet's receipts.
    """

    chosen = on_screen if on_screen is not None else environment
    if chosen is None:
        raise ExplainerRefused(
            "comparison-unavailable",
            "neither a comparison on screen nor an environment's binding was given; a staleness "
            "read compares against one comparison and never calls a page current without one",
        )
    if reads is not None:
        return TargetComparison(binding=chosen, reads=reads)
    return TargetComparison(
        binding=chosen,
        reads=ExplainerTargetReads(
            binding=chosen, task_root=task_root, supplemental=_receipts(supplemental)
        ),
    )


def staleness_read(
    *,
    task_root: Path,
    leaf_id: str,
    manifest: FrameManifest,
    target: TargetComparison,
    grades: GradeInputs | None = None,
) -> StalenessRead:
    """One retained revision against one target comparison: parts, grades and the message.

    The revision must belong to this leaf and the target must be this leaf's comparison: comparing
    one leaf's page against another's comparison would name staleness the developer never caused.
    Without ``grades`` the leaf's store is read from its records area and compared against the
    target's bound trees; any reference whose block cannot be fingerprinted reads stale with that
    reason, never current.
    """

    _require_same_leaf(manifest.binding, target.binding, leaf_id, task_root)
    if grades is None:
        reconciled = reconcile_grades(task_root, leaf_id, target, prune=False)
        recorded_fps = _baseline_proof_fingerprints(manifest, task_root)
        states = reconciled.states
        carried = reconciled.carried_hunks
        removed = reconciled.removed_count
    else:
        recorded_fps = (
            dict(grades.recorded_fingerprints)
            if grades.recorded_fingerprints
            else _baseline_proof_fingerprints(manifest, task_root)
        )
        states = grade_staleness(grades.grades, grades.fingerprints)
        carried = ()
        removed = 0
    parts = part_staleness(
        manifest,
        manifest.evidence.claims,
        target,
        recorded_fingerprints=recorded_fps,
    )
    return StalenessRead(
        revision=manifest.revision,
        target=target.binding.comparison_reference,
        binding=manifest.binding,
        moved=moved_sides(manifest.binding, target.binding),
        parts=parts,
        trusted=trusted_list(parts),
        grades=states,
        stale_grades=stale_grade_references(states),
        regeneration=regeneration_request(parts, states),
        carried_hunks=carried,
        removed_grades=removed,
    )


def gone_grade_references(
    states: Sequence[GradeState], present: Collection[str]
) -> tuple[str, ...]:
    """The stored references the target no longer holds: the ``gone`` tuple ``prune`` takes.

    Rule 4 keeps a stale grade until it is redone and prunes a *gone* block's grade at the next
    Update or Retire. ``present`` is the target's own block references, so a grade whose block the
    target still holds is never pruned, however stale it is.
    """

    held = set(present)
    return tuple(dict.fromkeys(state.reference for state in states if state.reference not in held))


def _require_same_leaf(
    retained: FrameBinding, selected: FrameBinding, leaf_id: str, task_root: Path
) -> None:
    """Refuse a revision or a comparison that belongs to another leaf (rule 4's identity)."""

    if retained.leaf_id != leaf_id or retained.task_id != selected.task_id:
        raise ExplainerRefused(
            "revision-foreign",
            f"the revision binds {retained.leaf_id!r} of {retained.task_id!r}, not the leaf "
            f"{leaf_id!r} of {selected.task_id!r} this read was asked for",
        )
    if selected.leaf_id != leaf_id:
        raise ExplainerRefused(
            "comparison-foreign",
            f"the target comparison names {selected.leaf_id!r}, not the leaf {leaf_id!r}; a page is "
            f"never compared against another leaf's comparison (task records at {task_root})",
        )


def _baseline_proof_fingerprints(manifest: FrameManifest, task_root: Path) -> dict[str, str]:
    """Reconstruct the retained revision's proof fingerprints from its bound trees."""

    reads = ExplainerTargetReads(binding=manifest.binding, task_root=task_root)
    recorded: dict[str, str] = {}
    for claim in manifest.evidence.claims.claims:
        for basis in claim.bases:
            if basis.kind == "proof-entry" and basis.proof_id and basis.record_id:
                entry = reads.proof_entry(proof=basis.proof_id, record=basis.record_id)
                if entry is not None and entry.fingerprint is not None:
                    recorded[basis.proof_id] = entry.fingerprint
    return recorded


def _target_ref_for(original: str, target_block: BlockIdentity) -> str:
    """Format ``target_block`` in the same reference syntax family as ``original``."""

    if "\x00" in original:
        return target_block.block_key()
    if original.startswith("{"):
        return target_block.reference()
    return original


def _is_hunk_grade(grade: Grade, block: BlockIdentity | None) -> bool:
    return (
        block is not None and block.kind in {"unexplained-hunk", "pure-deletion-hunk"}
    ) or grade.fingerprint.startswith("hunk:")


def _mapped_block_span(
    grade: Grade, block: BlockIdentity, reads: ExplainerTargetReads
) -> tuple[int, int] | None:
    if block.start_line is None or block.end_line is None:
        return None
    span = (block.start_line, block.end_line)
    if grade.binding.code_candidate_tree == reads.binding.code_candidate_tree:
        return span
    mapped = reads.mapped_lines(
        side="code-candidate",
        path=block.path or "",
        from_tree=grade.binding.code_candidate_tree,
        to_tree=reads.binding.code_candidate_tree,
        lines=span,
    )
    return mapped if mapped is not None else span


def _hunk_overlaps(
    item: HunkSides, span: tuple[int, int] | None, before_span: tuple[int, int] | None
) -> bool:
    cand_end = item.candidate_span[0] + max(item.candidate_span[1], 1) - 1
    base_end = item.base_span[0] + max(item.base_span[1], 1) - 1
    if span is not None:
        if max(span[0], item.candidate_span[0]) <= min(span[1], cand_end):
            return True
        if max(span[0], item.base_span[0]) <= min(span[1], base_end):
            return True
    if before_span is not None:
        b_end = before_span[0] + max(before_span[1], 1) - 1
        if max(before_span[0], item.base_span[0]) <= min(b_end, base_end):
            return True
    return False


def _match_target_hunk(
    grade: Grade,
    block: BlockIdentity | None,
    hunks: Sequence[HunkSides],
    reads: ExplainerTargetReads,
) -> HunkSides | None:
    for item in hunks:
        if item.fingerprint().value == grade.fingerprint:
            return item
    if block is not None:
        span = _mapped_block_span(grade, block, reads)
        for item in hunks:
            if _hunk_overlaps(item, span, block.before_span):
                return item
    moved = moved_hunk_match(stored_subject=grade.fingerprint, hunks=hunks)
    if moved is not None:
        return moved
    if block is None:
        return hunks[0] if len(hunks) == 1 else None
    return None


def _scale_for(grades: Grades, category: str | None) -> tuple[str, ...]:
    if category:
        cat = grades.category(category)
        if cat is not None:
            return tuple(p.grade for p in cat.scale)
    if grades.categories:
        return tuple(p.grade for p in grades.categories[0].scale)
    return ()


def _reconcile_hunk_group(
    tgt: HunkSides,
    group: list[tuple[int, Grade]],
    grades: Grades,
    active_category: str | None,
) -> tuple[list[tuple[int, GradeState]], HunkCarry | None]:
    sample_ref = group[0][1].reference
    target_id = tgt.identity()
    target_ref = _target_ref_for(sample_ref, target_id)
    target_fp = tgt.fingerprint()
    if target_ref != target_fp.reference:
        # Preserve native blockKey format on target_fp when source used native blockKey.
        target_fp = BlockFingerprint.computed(target_id, str(target_fp.value))
    distinct_sources = {g.reference for _idx, g in group}
    if len(distinct_sources) >= 2:
        scale = _scale_for(grades, active_category or group[0][1].category)
        carry = carry_merged_hunk_grades(
            target=target_fp,
            sources=[g for _idx, g in group],
            scale=scale,
            active_category=active_category,
        )
        remapped_states: list[tuple[int, GradeState]] = []
        for (idx, _orig), state in zip(group, carry.carried, strict=True):
            updated_grade = state.grade.model_copy(update={"reference": target_ref})
            remapped_states.append(
                (idx, GradeState(grade=updated_grade, stale=True, reason=state.reason))
            )
        return remapped_states, carry
    results: list[tuple[int, GradeState]] = []
    for idx, g in group:
        ref = _target_ref_for(g.reference, target_id)
        if target_fp.value == g.fingerprint:
            updated = g.model_copy(update={"reference": ref, "stale": False, "stale_reason": None})
            results.append((idx, GradeState(grade=updated, stale=False, reason=None)))
        else:
            reason = f"the block changed: {g.fingerprint} is not {target_fp.value}"
            updated = g.model_copy(update={"reference": ref, "stale": True, "stale_reason": reason})
            results.append((idx, GradeState(grade=updated, stale=True, reason=reason)))
    return results, None


_UNCOMPUTABLE_REASON = (
    "the block's fingerprint at the target comparison cannot be computed, so the grade cannot be "
    "called current"
)


def _uncomputable_state(grade: Grade, reason: str = _UNCOMPUTABLE_REASON) -> GradeState:
    return GradeState(
        grade=grade.model_copy(update={"stale": True, "stale_reason": reason}),
        stale=True,
        reason=reason,
    )


def _reconcile_non_hunk_grade(
    grade: Grade, reads: ExplainerTargetReads, *, prune: bool
) -> tuple[GradeState | None, bool]:
    fp = reads.block_fingerprint(grade.reference, source_binding=grade.binding)
    if fp is None:
        return (None if prune else _uncomputable_state(grade)), True
    if not fp.current:
        return _uncomputable_state(grade, fp.reason or _UNCOMPUTABLE_REASON), False
    ref = _target_ref_for(grade.reference, fp.block)
    if fp.value == grade.fingerprint:
        updated = grade.model_copy(update={"reference": ref, "stale": False, "stale_reason": None})
        return GradeState(grade=updated, stale=False, reason=None), False
    reason = f"the block changed: {grade.fingerprint} is not {fp.value}"
    updated = grade.model_copy(update={"reference": ref, "stale": True, "stale_reason": reason})
    return GradeState(grade=updated, stale=True, reason=reason), False


def _classify_hunk_grade(
    idx: int,
    grade: Grade,
    block: BlockIdentity | None,
    reads: ExplainerTargetReads,
    *,
    prune: bool,
) -> tuple[GradeState | None, bool, tuple[tuple[str, str], HunkSides] | None]:
    parsed_subj = parse_hunk_subject(grade.fingerprint)
    path = (block.path if block and block.path else None) or (parsed_subj[0] if parsed_subj else "")
    hunks = reads.file_hunks(path) if path else ()
    if hunks is None:
        return _uncomputable_state(grade), False, None
    matched = _match_target_hunk(grade, block, hunks, reads)
    if matched is None:
        return (None if prune else _uncomputable_state(grade)), True, None
    del idx
    return None, False, ((path, matched.identity().reference()), matched)


def _partition_grades(
    grades: Grades, reads: ExplainerTargetReads, *, prune: bool
) -> tuple[
    list[tuple[int, GradeState]],
    list[str],
    dict[tuple[str, str], tuple[HunkSides, list[tuple[int, Grade]]]],
]:
    indexed_states: list[tuple[int, GradeState]] = []
    gone: list[str] = []
    hunk_groups: dict[tuple[str, str], tuple[HunkSides, list[tuple[int, Grade]]]] = {}
    for idx, g in enumerate(grades.grades):
        block = parse_block_identity(g.reference, binding=g.binding)
        if _is_hunk_grade(g, block):
            state, is_gone, grouped = _classify_hunk_grade(idx, g, block, reads, prune=prune)
            if grouped is not None:
                key, matched = grouped
                hunk_groups.setdefault(key, (matched, []))[1].append((idx, g))
        else:
            state, is_gone = _reconcile_non_hunk_grade(g, reads, prune=prune)
        if is_gone:
            gone.append(g.reference)
        if state is not None:
            indexed_states.append((idx, state))
    return indexed_states, gone, hunk_groups


def reconcile_grades(
    task_root: Path,
    leaf_id: str,
    target: TargetComparison,
    *,
    prune: bool = True,
    active_category: str | None = None,
) -> GradeReconciliation:
    """Reconcile, remap, carry merged hunks and optionally prune gone grades for ``target``."""

    store = GradeStore(path=grades_path(task_root, leaf_id), leaf_id=leaf_id)
    grades = store.load()
    if not grades.grades:
        return GradeReconciliation(grades=grades)
    reads = (
        target.reads
        if isinstance(target.reads, ExplainerTargetReads)
        else ExplainerTargetReads(binding=target.binding, task_root=task_root)
    )
    indexed_states, gone, hunk_groups = _partition_grades(grades, reads, prune=prune)
    carries: list[HunkCarry] = []
    for _key, (tgt, group) in hunk_groups.items():
        group_states, carry = _reconcile_hunk_group(tgt, group, grades, active_category)
        indexed_states.extend(group_states)
        if carry is not None:
            carries.append(carry)
    indexed_states.sort(key=lambda item: item[0])
    states = tuple(state for _idx, state in indexed_states)
    kept_grades = tuple(state.grade for state in states)
    updated_store = grades.model_copy(update={"grades": kept_grades})
    if prune and (gone or kept_grades != grades.grades):
        store.write(updated_store)
    stale_refs = stale_grade_references(states)
    return GradeReconciliation(
        grades=updated_store,
        states=states,
        stale_grades=stale_refs,
        gone_grades=tuple(gone),
        carried_hunks=tuple(carries),
        removed_count=len(gone),
        stale_count=len(stale_refs),
    )


def _receipts(
    supplemental: Sequence[SupplementalReceipt] | None,
) -> tuple[SupplementalReceipt, ...] | None:
    """The target packet's receipts as the reads carry them; ``None`` stays unread, not empty."""

    return None if supplemental is None else tuple(supplemental)
