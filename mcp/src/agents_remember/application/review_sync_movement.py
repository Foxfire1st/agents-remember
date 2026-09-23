"""Whether a managed sync moved the inputs of the review a leaf published (ICR-R22@v1, F6).

`ICR-R22@v1`'s Required Behavior is that a completed sync which carried the official line
**invalidates** the moved review inputs and binds the resolved pair -- and its own non-conforming
example is the state where "the old review stays current while its scratch datasets lag the merged
memory line". A durable record that only the sync payload and the reopen channel read leaves that
state standing on the surface a reviewing agent is actually looking at, so this module is the read
half: it resolves what the leaf's own syncs measured against the generation the leaf published, and
reports it in the review payload's measured-currentness vocabulary.

**It measures; it owns no verdict of its own.** The measurement is the durable rebinding record
(:mod:`agents_remember.application.review_sync_rebinding`), and the record is accepted here only when
:func:`rebinding_names_the_generation` proves it describes the very generation this read selected. A
record that does not describe it is *no measurement of this generation* and is reported as the absence
it is, never as the movement it claims. Nothing here composes a comparison, decides a verdict, writes
anything, or grants clearance: the remedy it names is the successor generation the record already
names.

**A read that cannot measure says so by omission, not by a false claim.** No generation, an unreadable
manifest, an unreadable record, a record that describes another generation, and an error while reading
any of them all answer ``None`` -- "no sync movement is recorded for this leaf's published generation"
-- because the alternative would be a movement claim assembled from something other than a
measurement. The live review read is not the sync: nothing here can fail a response.

**It reuses the introducers' identities.** The generation, its binding digest and the reviewed
candidate tree come from the sealed manifest; the reviewed knowledge digest comes from that manifest's
own after side; the resolved head, candidate tree and dataset come from the record's own measured
fields; and the moved identities are the *reviewed* inputs that no longer match, which is what a reader
has to be able to name.
"""

from __future__ import annotations

from collections.abc import Mapping

from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_comparison_generation import (
    COMPARISON_MANIFEST_NAME,
    ComparisonGenerationManifest,
    read_manifest,
)
from agents_remember.application.review_final_output_receipt import select_review_generation
from agents_remember.application.review_sync_rebinding import (
    read_review_sync_rebinding,
    rebinding_names_the_generation,
)
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.review_staleness import (
    ReviewStaleness,
    ReviewSyncMovement,
    ReviewSyncMovementState,
)
from agents_remember.models.knowledge.review_sync_rebinding import (
    ReviewSyncRebinding,
    ReviewSyncRebindingVerdict,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "review_staleness_with_sync_movement",
    "review_sync_movement",
]

# The two identities that can move, each named by the channel it belongs to. A moved identity is
# spelled `channel:identity` so a reader gets both the input that moved and the exact value the
# review recorded for it -- R15's `moved_identities` in this review's own vocabulary.
_CODE_CHANNEL = "code-candidate-tree"
_KNOWLEDGE_CHANNEL = "published-knowledge"

# The remedy, stated once. It is the successor-generation route the rebinding record itself names,
# restated for a reader of the review rather than of the sync result.
# The record's three verdicts, mapped once. It is a table rather than two comparisons on the channel
# matches because the verdict IS the state: reading "did any channel differ" would promote the
# record's own ``unmeasured`` to agreement.
_MOVEMENT_STATES: Mapping[ReviewSyncRebindingVerdict, ReviewSyncMovementState] = {
    "current": "current",
    "moved": "stale",
    "unmeasured": "not-measured",
}

_SUPERSESSION = (
    "publish a successor generation naming the reviewed generation as its predecessor "
    "(freeze_review_comparison), which records the supersession as the successor's own lineage"
)


def review_sync_movement(
    resolved: ReviewCandidateResolution,
) -> ReviewSyncMovement | None:
    """What this leaf's own managed syncs measured against the generation it published, or ``None``.

    ``None`` is the answer whenever there is no measurement to report, and every one of those cases is
    a fact rather than a failure: no enclosure contract to resolve a generation under (a hand-assembled
    pair, or a closed leaf whose records the reopen channel already reports), no published generation,
    no recorded rebinding for it, a rebinding that does not describe it, and any read error along the
    way. The live review read must never fail because a *measurement* was unavailable.
    """

    contract = resolved.contract
    if contract is None or contract.kind != "leaf":
        return None
    try:
        return _measured(contract)
    except (KnowledgeStorageError, OSError, RuntimeError, ValueError):
        return None


def _measured(contract: WorktreeContract) -> ReviewSyncMovement | None:
    """Read the selected generation's rebinding and project it.

    Three outcomes, and they are three facts rather than two: ``None`` when no record is written for
    the generation at all ("nothing has measured this"), ``unavailable`` when a record is there and
    cannot be used -- its bytes are not a readable record, or it does not describe this generation --
    and otherwise the measured movement. Collapsing the middle case into either neighbour would either
    claim a measurement nobody made or deny one that exists and is broken.
    """

    selection = select_review_generation(contract.task_root, contract.leaf_id)
    if selection.state != "selected" or selection.ref is None:
        return None
    manifest = read_manifest(selection.ref.directory / COMPARISON_MANIFEST_NAME)
    read = read_review_sync_rebinding(contract.task_root, contract.leaf_id, manifest.generation_id)
    if read.state == "unreadable":
        return _unavailable(manifest, read.detail)
    record = rebinding_names_the_generation(read, manifest)
    if record is None:
        if read.state == "not-recorded":
            return None
        return _unavailable(
            manifest,
            f"a rebinding record exists at {read.destination} and does not describe comparison "
            f"generation {manifest.generation_id}: its recorded reviewed identities are not this "
            "generation's",
        )
    return _project(record, manifest)


def _unavailable(manifest: ComparisonGenerationManifest, reason: str) -> ReviewSyncMovement:
    """The movement value for a generation whose rebinding record exists and cannot be used."""

    return ReviewSyncMovement(
        binding_state="unavailable",
        reason=reason,
        statement=(
            f"no measurement of comparison generation {manifest.generation_id} "
            f"(index {manifest.generation_index}) is reported here: {reason}"
        ),
        generation_id=manifest.generation_id,
        generation_index=manifest.generation_index,
        reviewed_binding_digest=manifest.binding_digest,
        reviewed_candidate_code_tree_id=manifest.source.candidate_code_tree_id,
        reviewed_knowledge_logical_digest=_reviewed_knowledge(manifest),
        successor_action=_SUPERSESSION,
        record_readable=False,
    )


def _reviewed_knowledge(manifest: ComparisonGenerationManifest) -> str | None:
    """The dataset identity the generation retained, or ``None`` when it retained none."""

    after = manifest.knowledge_side("after")
    return None if after.identity is None else after.identity.logical_digest


def _project(
    record: ReviewSyncRebinding, manifest: ComparisonGenerationManifest
) -> ReviewSyncMovement:
    """Project one accepted record, taking the state from the record's own verdict.

    **The record's verdict is the state, never a two-way reading of the channel matches.** The record
    has three outcomes -- ``current``, ``moved`` and ``unmeasured`` -- and they map to ``current``,
    ``stale`` and ``not-measured`` here. Deriving the state from "did any channel differ" would
    silently promote ``unmeasured`` to agreement, which is a claim the record explicitly declined to
    make, and it would render an agreement sentence about a dataset nothing compared.
    """

    reviewed_knowledge = _reviewed_knowledge(manifest)
    code_moved = record.code_match == "differs-from-reviewed-input"
    knowledge_moved = record.knowledge_match == "differs-from-reviewed-input"
    state = _MOVEMENT_STATES[record.state]
    moved = tuple(
        name
        for name, happened in (
            (f"{_CODE_CHANNEL}:{record.reviewed_candidate_code_tree_id}", code_moved),
            (f"{_KNOWLEDGE_CHANNEL}:{reviewed_knowledge}", knowledge_moved),
        )
        if happened
    )
    reason = _unmeasured_reason(record, manifest) if state == "not-measured" else None
    # A resolved identity is named exactly on the channel that moved: a channel that still matches,
    # was never compared, or could not be read has no resolved value to report, and inventing one is
    # the fabricated identity this vocabulary refuses.
    return ReviewSyncMovement(
        binding_state=state,
        moved_identities=moved,
        reason=reason,
        statement=_statement(record, manifest, state=state, reason=reason),
        generation_id=manifest.generation_id,
        generation_index=manifest.generation_index,
        reviewed_binding_digest=manifest.binding_digest,
        reviewed_candidate_code_tree_id=manifest.source.candidate_code_tree_id,
        reviewed_knowledge_logical_digest=reviewed_knowledge,
        resolved_code_head=record.resolved_code_head if code_moved else None,
        resolved_candidate_code_tree_id=(
            record.resolved_candidate_code_tree_id if code_moved else None
        ),
        resolved_knowledge_logical_digest=(
            record.resolved_knowledge.dataset.logical_digest
            if knowledge_moved and record.resolved_knowledge.dataset is not None
            else None
        ),
        successor_action=_SUPERSESSION,
    )


def _unmeasured_reason(record: ReviewSyncRebinding, manifest: ComparisonGenerationManifest) -> str:
    """The record's own reason for a knowledge channel it never compared, in its own words.

    Every clause is a value the record holds: the channel's match, the state of the location the sync
    read, and that location's own sentence. Nothing here re-diagnoses why the dataset was absent --
    the read route already said which of "no publication" and "something unreadable" it found.
    """

    after = manifest.knowledge_side("after")
    if after.state != "retained":
        return (
            f"the generation records {after.state} for its knowledge operand, so no dataset was "
            "compared to a reviewed one"
        )
    return (
        f"the retained knowledge operand was not compared (knowledge_match "
        f"{record.knowledge_match}): the declared publication location is "
        f"{record.resolved_knowledge.state}, and {record.resolved_knowledge.detail}"
    )


def _statement(
    record: ReviewSyncRebinding,
    manifest: ComparisonGenerationManifest,
    *,
    state: ReviewSyncMovementState,
    reason: str | None,
) -> str:
    """The one sentence this value publishes, derived from what the record actually measured.

    Each state has its own sentence, and the agreement sentence is reachable **only** from
    ``current`` -- a record whose every retained channel was compared and matched. A channel the sync
    never compared is named as unmeasured, with the record's own reason, and never appears in a clause
    about what the leaf holds.
    """

    generation = (
        f"comparison generation {manifest.generation_id} (index {manifest.generation_index})"
    )
    if state == "not-measured":
        return (
            f"a managed sync completed and {generation} does not fully describe the pair it "
            f"resolved: {reason}. Only the part that was compared is reported as measured, and the "
            f"rest is unmeasured; {_SUPERSESSION}"
        )
    if state == "current":
        return (
            f"a managed sync completed and {generation} still describes the pair it resolved: "
            f"{_measured_clause(record, manifest)}"
        )
    moved: list[str] = []
    if record.code_match == "differs-from-reviewed-input":
        moved.append(
            f"the candidate tree the review captured ({record.reviewed_candidate_code_tree_id}) is "
            f"not the tree the leaf now holds ({record.resolved_candidate_code_tree_id})"
        )
    if record.knowledge_match == "differs-from-reviewed-input":
        moved.append(
            f"the dataset the review compared ({_reviewed_knowledge(manifest)}) is not the dataset "
            "at the declared publication location"
        )
    return (
        f"a managed sync carried this leaf onto the official line and the reviewed inputs of "
        f"{generation} moved: {'; '.join(moved)}. The comparison rendered here is a new composition, "
        f"and the recorded generation does not describe it; {_SUPERSESSION}"
    )


def _measured_clause(record: ReviewSyncRebinding, manifest: ComparisonGenerationManifest) -> str:
    """The agreement clause, which names only the channels that were compared and matched.

    A generation that retained no knowledge operand has no dataset to speak of, so the clause says
    exactly that rather than borrowing an agreement about a comparison nobody made.
    """

    clauses = [
        f"the reviewed candidate tree {manifest.source.candidate_code_tree_id} is the tree the leaf "
        "holds today"
    ]
    after = manifest.knowledge_side("after")
    if after.state != "retained":
        clauses.append(
            f"the generation records {after.state} for its knowledge operand, so no dataset was "
            "compared to a reviewed one"
        )
    elif record.knowledge_match == "matches-reviewed-input":
        clauses.append(
            f"the dataset the review compared ({_reviewed_knowledge(manifest)}) is the dataset at "
            "the declared publication location"
        )
    return ", and ".join(clauses)


def review_staleness_with_sync_movement(
    staleness: ReviewStaleness, movement: ReviewSyncMovement | None
) -> ReviewStaleness:
    """Fold a measured sync movement into the staleness a review publishes.

    **The movement outranks the reader's carried identity**, because it is the stronger fact: a
    recorded sync that moved the reviewed inputs means the review does not describe the pair whatever
    the reader was shown, and R17's ``current`` -- "the displayed comparison is the candidate's current
    comparison" -- would let the moved review keep reading as untouched, which is the packet's own
    non-conforming example. Submission follows automatically: the payload's constructor refuses a
    ``stale`` comparison offered for submission.

    The previous input labelled here is a real one in both cases: the identity the reader carried when
    they carried a mismatching one, and otherwise the comparison identity the reviewed generation
    bound -- never an identity nobody held. A movement that measured agreement changes nothing.
    """

    if movement is None or movement.binding_state != "stale":
        return staleness
    carried = staleness.previous_comparison_ref
    return ReviewStaleness(
        state="stale",
        statement=movement.statement,
        previous_comparison_ref=carried or movement.reviewed_binding_digest,
        moved=movement.moved_identities,
    )
