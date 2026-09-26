"""Reopen a closed leaf from its frozen comparison or its exact recorded Git endpoints.

A frozen generation keeps authority over its source, knowledge and assessment identities even
when one channel no longer resolves. Only a leaf with no generation uses its recorded source and
memory commit ranges. Knowledge reconstructed from those Git blobs is labelled as reconstructed,
never as a previously frozen review or an authored assessment. Missing endpoints remain explicit;
neither today's dataset nor another task's generation is substituted.

The repository object store, not a disposable checkout, holds the source endpoints. Knowledge
materialization is request-owned and reclaimed after the composed reads release their resolution.
This reader creates no durable comparison, knowledge record, assessment, or Git ref.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.application.knowledge_before_half import NOT_RECORDED
from agents_remember.application.review_candidate_resolution import (
    REVIEW_BASELINE_DIRECTORY,
    REVIEW_CANDIDATE_DIRECTORY,
    REVIEW_CANDIDATE_RELATIVE_ROOT,
    ReviewCandidateResolution,
    recorded_leaf_contract,
    refusal,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_KNOWLEDGE_DIRECTORY,
    COMPARISON_SNAPSHOT_NAME,
    TYPED_ABSENCE_STATES,
    ComparisonGenerationManifest,
    KnowledgeSide,
)
from agents_remember.application.review_comparison_reopen import (
    ComparisonKnowledgeChannel,
    ComparisonReopen,
    reopen_comparison_generation,
)
from agents_remember.application.review_recorded_knowledge import (
    RecordedKnowledge,
    read_recorded_knowledge,
)
from agents_remember.kernel.git_command import run_git
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.models.knowledge.snapshot import CANDIDATE_DATABASE_NAME
from agents_remember.serving.changeset_endpoints import (
    NOT_RECORDED as ENDPOINT_NOT_RECORDED,
)
from agents_remember.serving.changeset_endpoints import (
    RecordedEndpointAbsent,
    RecordedRange,
    recorded_committed_range,
)
from agents_remember.worktrees.worktree_contract import WorktreeContract

__all__ = [
    "CLOSED_LEAF_REVIEW_SENTENCE",
    "HISTORY_COMPARISON_PREFIX",
    "HISTORY_INTENT_PREFIX",
    "HISTORY_RECORDED_COMPARISON",
    "HISTORY_RECORDED_ENDPOINTS",
    "HISTORY_RECORDED_SOURCE_RANGE",
    "HISTORY_SOURCE_PREFIX",
    "ClosedLeafReview",
    "closed_leaf_dataset_refusal",
    "closed_leaf_intent_detail",
    "closed_leaf_limitations",
    "resolve_committed_leaf_review",
]

# The two records a closed leaf's review is reopened from, named once because the surface declares
# which one it answered with and a second spelling of either would be a second vocabulary.
HISTORY_RECORDED_COMPARISON = "history:recorded-comparison"
HISTORY_RECORDED_SOURCE_RANGE = "history:recorded-source-range"
HISTORY_RECORDED_ENDPOINTS = "history:reconstructed-recorded-endpoints"

# The declared-limit vocabulary. Every fact this module establishes is published at the top level of
# the response, because a limit a reader has to open a pane to discover is a limit the response did
# not state -- and because these are the facts that tell a *recorded* comparison from a live one.
HISTORY_COMPARISON_PREFIX = "history:comparison-generation:"
HISTORY_INTENT_PREFIX = "history:intent:"
HISTORY_SOURCE_PREFIX = "history:source:"

# The sentence the surface publishes when a closed leaf's review is opened. It is stated here rather
# than assembled at each rendering, so the entry, the review and the expansion cannot describe the
# same resolution three ways.
CLOSED_LEAF_REVIEW_SENTENCE = (
    "this review is reopened from the leaf's own durable records rather than from a live worktree: "
    "the leaf's enclosure is closed, so the comparison shown is the one its records bound and the "
    "surface substitutes neither the current branch tip nor today's knowledge for it"
)

# One intent half's state, as this surface declares it. ``retained`` means the generation kept the
# dataset and it still reads back as the identity that was frozen; the two typed absences are R05's
# own spellings, imported rather than restated; the last three are expected content that does not
# resolve now, which is deliberately NOT collapsed into either absence.
IntentState = Literal[
    "retained",
    "not-recorded",
    "not-selected",
    "missing",
    "corrupt",
    "unavailable-history",
]

# The three channel states that mean "expected content, and it is not comparable now".
_UNAVAILABLE_INTENT_STATES: frozenset[str] = frozenset(
    {"missing", "corrupt", "unavailable-history"}
)


@dataclass(frozen=True)
class ClosedLeafReview:
    """The record one closed leaf's review was reopened from, and what it measures now.

    ``reopened`` is the durable-generation owner's own answer, carried rather than restated: its
    ``manifest`` is present exactly when the leaf published a generation, and its per-channel states
    are the measurement this resolution read its endpoints from. A leaf with no generation is not an
    error here -- it is the older task whose source range the enclosure contract still records -- and
    :attr:`state` names which of the two records answered.
    """

    leaf_id: str
    reopened: ComparisonReopen
    code_base: str
    code_candidate: str
    detail: str
    recorded_knowledge: RecordedKnowledge | None = None

    @property
    def manifest(self) -> ComparisonGenerationManifest | None:
        """The published generation this review resolved, or ``None`` for a recorded source range."""

        return self.reopened.manifest

    @property
    def state(self) -> Literal["recorded-comparison", "recorded-source-range"]:
        """Which durable record answered: a published comparison, or the recorded range."""

        return "recorded-comparison" if self.manifest is not None else "recorded-source-range"

    def directory(self) -> Path | None:
        """Where the generation's own retained content lives, when a generation answered."""

        generation = self.reopened.generation
        return None if generation is None else generation.directory

    def intent(self, side: KnowledgeSide) -> ComparisonKnowledgeChannel | None:
        """One knowledge half's measured channel, or ``None`` when no generation recorded one."""

        if self.recorded_knowledge is not None:
            return self.recorded_knowledge.channel(side)
        for channel in self.reopened.knowledge:
            if channel.side == side:
                return channel
        return None


def resolve_committed_leaf_review(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> ReviewCandidateResolution | ReviewRefusal:
    """Resolve one committed or closed leaf's review from its durable records, or refuse by name.

    The order is the packet's: the leaf's recorded enclosure contract first (it names the task, the
    disposable knowledge root and the recorded commits), then its published comparison generation,
    and -- only when no generation was ever published -- the recorded source range. Nothing here
    reads a worktree, which is what lets a fresh process reconstruct the same comparison from durable
    records alone.
    """

    contract = recorded_leaf_contract(config, repository_id, master, leaf_id)
    if contract is None:
        return refusal(
            "candidate_unresolved",
            "no readable leaf enclosure contract records this leaf under the named master",
            next_action=(
                "open the review for an admitted leaf whose enclosure contract exists under "
                "tasks/<repository>/<master>/enclosures"
            ),
            offending_input=f"{master}/{leaf_id}",
        )
    reopened = reopen_comparison_generation(config, repository_id, master, leaf_id)
    if reopened.state in {"ambiguous", "manifest-unreadable"}:
        # A record that is there and cannot be read is refused in the reopen's own words: nothing
        # about it is claimed, and no other record is resolved in its place.
        return _reopen_refusal(contract, reopened)
    if reopened.manifest is None:
        return _recorded_range_resolution(contract, reopened)
    return _generation_resolution(contract, reopened, reopened.manifest)


def _reopen_refusal(contract: WorktreeContract, reopened: ComparisonReopen) -> ReviewRefusal:
    """The refusal one unreadable or ambiguous generation earns, carrying the reopen's own words."""

    existing = reopened.refusal
    if existing is not None:
        return existing
    return refusal(
        "comparison_refused",
        f"the comparison record of leaf {contract.leaf_id} could not be resolved",
        next_action=(
            "restore or name the exact comparison generation this leaf published, then reopen the "
            "review"
        ),
        offending_input=contract.leaf_id,
    )


# -- from a published generation -----------------------------------------------------------------


def _generation_resolution(
    contract: WorktreeContract,
    reopened: ComparisonReopen,
    manifest: ComparisonGenerationManifest,
) -> ReviewCandidateResolution:
    """The resolution the generation bound: its two code objects and its two retained snapshots."""

    repository = Path(manifest.source.code_repository_root)
    review = ClosedLeafReview(
        leaf_id=manifest.leaf_id,
        reopened=reopened,
        code_base=manifest.source.baseline_code_tree_id,
        code_candidate=manifest.source.candidate_code_tree_id,
        detail=(
            f"{CLOSED_LEAF_REVIEW_SENTENCE}. This review is the comparison generation "
            f"{manifest.generation_id} (index {manifest.generation_index}, seal "
            f"{manifest.binding_digest}) recorded at {manifest.recorded_at}."
        ),
    )
    return ReviewCandidateResolution(
        repository_id=_retained_namespace(manifest, contract),
        leaf_id=manifest.leaf_id,
        baseline_database=_half_database(contract, review, manifest, "before"),
        candidate_database=_half_database(contract, review, manifest, "after"),
        # Both objects are read in the repository the record names -- the same repository the live
        # resolution's baseline root names -- so a comparison composed while the leaf was live is
        # reproduced byte for byte by a process that holds only the record.
        baseline_code_root=repository,
        candidate_code_root=repository,
        baseline_code_tree_id=manifest.source.baseline_code_tree_id,
        candidate_code_tree_id=manifest.source.candidate_code_tree_id,
        # The contract is the leaf's *recorded* enclosure, carried because the record-reading owners
        # (the curator authority among them) are addressed by it. There is deliberately **no**
        # captured candidate identity: nothing about a closed enclosure may be re-derived, so the
        # pre-publication recheck has nothing to refuse and leaves this resolution as it was read.
        contract=contract,
        candidate_identity=None,
        closed_leaf=review,
    )


def _half_database(
    contract: WorktreeContract,
    review: ClosedLeafReview,
    manifest: ComparisonGenerationManifest,
    side: KnowledgeSide,
) -> Path:
    """Where one half's dataset is read from: the retained snapshot, or the leaf's own half path.

    A retained half is read from the generation's own copy -- the dataset that was frozen, in the
    directory the reopen resolved. A half the record states it never had is read from the leaf's own
    disposable location, which is where the live resolution looks too: the file is not there, and
    *why* it is not there is the record's own typed absence rather than an absence this module
    inferred from a missing file.
    """

    binding = manifest.knowledge_side(side)
    directory = review.directory()
    if binding.state == "retained" and directory is not None:
        return directory / COMPARISON_KNOWLEDGE_DIRECTORY / side / COMPARISON_SNAPSHOT_NAME
    return _disposable_half(contract, side)


def _disposable_half(contract: WorktreeContract, side: KnowledgeSide) -> Path:
    """The half path inside the leaf's own disposable knowledge root, from its enclosure contract.

    It is the live resolution's own expression -- the contract's worktree group, this feature's
    relative root, the half's directory and the dataset name -- so "the half this leaf would hold" is
    one path rather than two conventions that agree today.
    """

    root = contract.worktree_group / _DISPOSABLE_RELATIVE
    return root / _HALF_DIRECTORY[side] / CANDIDATE_DATABASE_NAME


def _retained_namespace(manifest: ComparisonGenerationManifest, contract: WorktreeContract) -> str:
    """The namespace the retained halves are read under, from the record's own dataset identities.

    The live resolution opens the pair under the namespace its candidate's receipt names; the
    generation recorded that same namespace inside each retained half's dataset identity, so a
    reopened comparison is read under the identity the dataset itself holds rather than under the
    repository the request happens to spell. A generation that retained no half falls back to the
    repository the contract records, which is what the live path reads for a pair with no admission
    record either.
    """

    namespaces = {
        binding.identity.repository_id
        for binding in manifest.knowledge
        if binding.state == "retained" and binding.identity is not None
    }
    if len(namespaces) == 1:
        return namespaces.pop()
    return contract.repo_name


# -- from the recorded source range --------------------------------------------------------------


def _recorded_range_resolution(
    contract: WorktreeContract, reopened: ComparisonReopen
) -> ReviewCandidateResolution | ReviewRefusal:
    """Read the exact recorded source and memory endpoints when no frozen generation exists."""

    try:
        recorded = recorded_committed_range(contract, memory=False)
    except RecordedEndpointAbsent as absent:
        return _no_recorded_range_refusal(contract, absent)
    candidate_tree = _tree_of(recorded)
    if isinstance(candidate_tree, ReviewRefusal):
        return candidate_tree
    knowledge = read_recorded_knowledge(contract)
    return ReviewCandidateResolution(
        repository_id=knowledge.namespace(contract.repo_name),
        leaf_id=contract.leaf_id,
        baseline_database=knowledge.database("before"),
        candidate_database=knowledge.database("after"),
        baseline_code_root=recorded.repository,
        candidate_code_root=recorded.repository,
        baseline_code_tree_id=recorded.base_commit,
        candidate_code_tree_id=candidate_tree,
        contract=contract,
        candidate_identity=None,
        closed_leaf=ClosedLeafReview(
            leaf_id=contract.leaf_id,
            reopened=reopened,
            code_base=recorded.base_commit,
            code_candidate=candidate_tree,
            recorded_knowledge=knowledge,
            detail=(
                f"{CLOSED_LEAF_REVIEW_SENTENCE}. This leaf published no comparison generation, so "
                f"the review exposes its recorded source range: {recorded.base_commit} to "
                f"{recorded.head_commit} as tree {candidate_tree}. "
                + " ".join(knowledge.channel(side).detail for side in _SIDES)
            ),
        ),
    )


def _tree_of(recorded: RecordedRange) -> str | ReviewRefusal:
    """The tree the recorded landed commit names, or the refusal for a commit that will not resolve.

    The review addresses **trees**: the expansion owner refuses a commit, a blob or a tag by name, so
    binding the commit here would publish an inventory whose own entries could not be opened. The
    recorded commit identifies exactly one tree, and it is resolved in the repository the range names
    rather than taken from the caller.
    """

    resolved = run_git(recorded.repository, ["rev-parse", f"{recorded.head_commit}^{{tree}}"])
    tree = resolved.stdout.strip()
    if resolved.returncode != 0 or not tree:
        return refusal(
            "candidate_unresolved",
            (
                f"the recorded code commit {recorded.head_commit} does not resolve to a tree in "
                f"{recorded.repository}, so this leaf's recorded source range cannot be read"
            ),
            next_action=(
                "restore the recorded commit in the repository the enclosure names, or reopen the "
                "review while the leaf's worktree is live; the surface reads no branch tip and no "
                "working tree in its place"
            ),
            offending_input="code",
        )
    return tree


def _no_recorded_range_refusal(
    contract: WorktreeContract, absent: RecordedEndpointAbsent
) -> ReviewRefusal:
    """The refusal for a closed leaf whose recorded source range cannot be read, in its own kind.

    Three states arrive here and they are three different facts, so they earn three different codes
    rather than one. A leaf that recorded **no** landed commit is the one state in which the
    packet's original code is still the honest answer: nothing is live and nothing was recorded. A
    contract that names no code repository, and a recorded commit the repository does not hold, are
    *recorded endpoints that do not resolve* -- that is ``candidate_unresolved``, and answering
    ``candidate_not_live`` for them would be the intake defect's own claim ("there is no candidate
    to review") applied to a leaf that recorded exactly the candidate this surface failed to read.
    """

    if absent.kind != ENDPOINT_NOT_RECORDED:
        return refusal(
            "candidate_unresolved",
            (
                f"leaf {contract.leaf_id} records a source range that cannot be read ({absent.kind}), "
                f"so no candidate can be resolved: {absent}"
            ),
            next_action=(
                "restore the recorded commit in the repository the enclosure names, or record the "
                "landed commit the leaf actually holds; the surface reads the recorded endpoints and "
                "substitutes no branch tip, no working tree and no other leaf's records"
            ),
            offending_input="code",
        )
    return refusal(
        "candidate_not_live",
        (
            f"leaf {contract.leaf_id} has no live worktree and records no comparison generation, "
            f"so no candidate can be resolved: {absent}"
        ),
        next_action=(
            "record the leaf's landed commit in its enclosure contract, or freeze its comparison "
            "while the leaf's worktree is live; a review is never composed from the current branch "
            "tip or from another leaf's records"
        ),
        offending_input="code",
    )


# -- what the surface declares about a reopened review -------------------------------------------


def closed_leaf_limitations(resolved: ReviewCandidateResolution) -> tuple[str, ...]:
    """The declared facts one reopened review publishes, or none for a live candidate.

    Every fact is a top-level token in the response's own ``kind:value`` vocabulary, because each one
    is something a reader acts on: which record answered, which generation it was, whether each
    intent half was ever recorded, and whether an expected source channel still resolves. None of
    them replaces a channel's own state -- they are the *declaration* of the states the panes carry.
    """

    review = resolved.closed_leaf
    if review is None:
        return ()
    recorded = (
        HISTORY_RECORDED_COMPARISON
        if review.manifest is not None
        else HISTORY_RECORDED_SOURCE_RANGE
    )
    tokens = [recorded]
    if review.recorded_knowledge is not None and any(
        review.recorded_knowledge.channel(side).identity is not None for side in _SIDES
    ):
        tokens.append(HISTORY_RECORDED_ENDPOINTS)
    manifest = review.manifest
    if manifest is not None:
        tokens.append(f"{HISTORY_COMPARISON_PREFIX}{manifest.generation_id}")
    for side in _SIDES:
        state = _intent_state(review.intent(side))
        if state == "retained" and review.recorded_knowledge is not None:
            state = "reconstructed"
        tokens.append(f"{HISTORY_INTENT_PREFIX}{side}:{state}")
    source = review.reopened.source
    if source is not None and source.state != "available":
        tokens.append(f"{HISTORY_SOURCE_PREFIX}{source.state}")
    return tuple(tokens)


def closed_leaf_intent_detail(resolved: ReviewCandidateResolution) -> str | None:
    """Why a reopened review compared no knowledge operand, or ``None`` when it compared one.

    The sentence names the two facts a reader has to be able to tell apart: an intent half the leaf
    never recorded -- R05's typed absence, which is history rather than a failure -- and an intent
    half that was recorded and does not resolve now, which is unavailable content on that channel.
    Neither is rendered as an empty statement, and neither is replaced by today's knowledge.
    """

    review = resolved.closed_leaf
    if review is None:
        return None
    return _intent_sentence(review)


def closed_leaf_dataset_refusal(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    """The refusal a closed leaf with no comparable knowledge half earns, or ``None``.

    The entry route and the subject composition ask the same pair preflight, and for a closed leaf the
    answer has to say *which* absence it found and *why*. Two facts must never be reported as each
    other, and the sentence is built from the distinction rather than from one list of reasons:

    * an **owner-stated absence** -- R05's ``not-recorded`` or this comparison's ``not-selected`` --
      is the record saying no such content was ever recorded. Nothing is missing and nothing failed,
      so the answer says that and never calls it unavailable;
    * **expected content that does not resolve** -- ``missing``, ``corrupt`` or a recorded deletion
      (``unavailable-history``) -- is a channel that is unavailable now, and it is the only thing
      this refusal may describe as unavailable.

    A leaf that recorded both kinds is told both, in that order, because the two answer different
    questions: what was never there, and what was there and is not now.
    """

    review = resolved.closed_leaf
    if review is None:
        return None
    reasons: list[tuple[KnowledgeSide, str]] = [
        (side, reason)
        for side in _SIDES
        if (reason := _half_reason(review, resolved, side)) is not None
    ]
    if not reasons:
        return None
    if review.manifest is None:
        return refusal(
            "candidate_dataset_absent",
            (
                f"leaf {review.leaf_id} records no comparison generation and its exact recorded "
                "memory endpoints cannot supply both knowledge operands: "
                + "; ".join(
                    f"{side}: {channel.detail}"
                    for side, _ in reasons
                    if (channel := review.intent(side)) is not None
                )
            ),
            next_action=(
                "open the complete recorded source change inventory; restore an unreadable exact "
                "memory endpoint if available. An endpoint that never contained knowledge has no "
                "historical knowledge to restore; today's dataset is not substituted"
            ),
            offending_input="knowledge",
        )
    stated = _named(reasons, typed=True)
    unresolved = _named(reasons, typed=False)
    return refusal(
        "candidate_dataset_absent",
        (
            f"{_stated_sentence(stated)}{_unresolved_sentence(unresolved)}"
            "so no subject can be listed or compared from it, and the surface substitutes neither "
            "today's knowledge nor another dataset"
        ),
        next_action=(
            "restore the generation's retained snapshot under its own record, or reopen the review "
            "on the channels that did resolve; the surface reads the recorded pair and no other"
        ),
        offending_input="; ".join(f"{side}:{reason}" for side, reason in reasons),
    )


def _named(reasons: Sequence[tuple[KnowledgeSide, str]], *, typed: bool) -> str:
    """One group of reasons as its ``side:state`` list, in the order the two halves are reported."""

    return ", ".join(
        f"{side}:{reason}" for side, reason in reasons if (reason in TYPED_ABSENCE_STATES) == typed
    )


def _stated_sentence(stated: str) -> str:
    """What the record itself declares about a half it never kept, or nothing when it declares none."""

    if not stated:
        return ""
    return (
        f"the record states that no such knowledge content was ever recorded ({stated}), which is a "
        "declared absence in the repository's history and not a channel that is unavailable; "
    )


def _unresolved_sentence(unresolved: str) -> str:
    """What an expected half that does not resolve now is reported as, or nothing when none is."""

    if not unresolved:
        return ""
    return (
        "the knowledge content this leaf's comparison generation recorded does not resolve now "
        f"({unresolved}), so the affected channel is reported unavailable rather than as an empty "
        "catalogue; "
    )


def _half_reason(
    review: ClosedLeafReview, resolved: ReviewCandidateResolution, side: KnowledgeSide
) -> str | None:
    """Why one half cannot be compared, or ``None`` when it can.

    The record's own channel state is the first authority: a half whose measured state is one of the
    three unresolved kinds states exactly that, and a half the record never kept states its typed
    absence. Only a half the record kept and that *does* resolve is then checked on disk, where a
    missing file is the one unexpected case and is reported as missing rather than as either absence.
    """

    state = _intent_state(review.intent(side))
    if state in _UNAVAILABLE_INTENT_STATES or state != "retained":
        return state
    return None if _half_present(resolved, side) else "missing"


def _half_present(resolved: ReviewCandidateResolution, side: KnowledgeSide) -> bool:
    """Whether one half's dataset is a file right now, asked of the resolution's own paths."""

    database = resolved.baseline_database if side == "before" else resolved.candidate_database
    return database.is_file()


def _intent_sentence(review: ClosedLeafReview) -> str:
    """The one sentence a reopened review states about the operand it did not compare.

    A published generation's own record decides which of the two facts leads: an **owner-stated
    absence** is the record saying no such content was ever recorded, and it is named as exactly that
    rather than as a channel that failed; **expected content that does not resolve** is the only
    state this sentence calls unavailable. A generation whose halves both resolve and simply were not
    selected over states that instead, because none of the three is true of it.
    """

    if review.manifest is None:
        if review.recorded_knowledge is not None:
            return (
                "no intent generation was ever recorded for this leaf; its knowledge is read from "
                "the exact recorded memory endpoints, separately from a frozen review: "
                + "; ".join(review.recorded_knowledge.channel(side).detail for side in _SIDES)
                + ". No subject selected means no knowledge comparison; the Source pane carries "
                "the complete recorded source range independently of knowledge availability"
            )
        return (
            "no intent generation was ever recorded for this leaf, so no knowledge operand exists to "
            "compare: that is a recorded absence in the repository's history rather than content "
            "that could not be read, and the surface substitutes neither today's knowledge nor an "
            "empty statement for it. The Source pane carries the leaf's recorded source range, which "
            "does not depend on knowledge availability"
        )
    states = [
        f"{side}:{state}"
        for side in _SIDES
        if (state := _intent_state(review.intent(side))) != "retained"
    ]
    blocked = [state for state in states if state.split(":", 1)[1] in _UNAVAILABLE_INTENT_STATES]
    declared = [state for state in states if state.split(":", 1)[1] in TYPED_ABSENCE_STATES]
    if blocked:
        return (
            "no knowledge operand was compared: the intent content this leaf's generation recorded "
            f"does not resolve now ({', '.join(blocked)}), so the affected knowledge channels are "
            "reported unavailable and never as empty. The channels that did resolve stay exactly as "
            "the generation recorded them, and neither today's knowledge nor the current branch tip "
            "is substituted"
        )
    if declared:
        return (
            "no knowledge operand was compared: the leaf's own generation states that no such intent "
            f"content was ever recorded ({', '.join(declared)}), which is a declared absence in the "
            "repository's history rather than content that could not be read. The Source pane carries "
            "the complete source change inventory of the generation this leaf recorded, and neither "
            "today's knowledge nor an empty statement is substituted for the absence"
        )
    return (
        "no invariant or family subject was selected for this review, so no knowledge operand was "
        "compared; the Source pane carries the complete source change inventory of the generation "
        "this leaf recorded"
    )


def _intent_state(channel: ComparisonKnowledgeChannel | None) -> IntentState:
    """One half's declared state: its measured channel state, or ``not-recorded`` with no record.

    A leaf that published no generation recorded no intent half either -- the leaf predates the
    record, and R05's spelling for exactly that fact is the one this returns.
    """

    if channel is None:
        return NOT_RECORDED
    if channel.state == "available":
        return "retained"
    return channel.state


# The two knowledge halves, in the order every report of them uses.
_SIDES: tuple[KnowledgeSide, ...] = ("before", "after")

# The two half directories of the leaf's disposable knowledge root. They are the live resolution's
# own two names, imported rather than re-spelled, so "the half this leaf would hold" is one path and
# not two conventions that agree today.
_HALF_DIRECTORY = {"before": REVIEW_BASELINE_DIRECTORY, "after": REVIEW_CANDIDATE_DIRECTORY}

# Where that root sits inside a worktree group, relative to the *worktree group* the contract records
# -- the same expression the live resolution derives its two database paths from.
_DISPOSABLE_RELATIVE = REVIEW_CANDIDATE_RELATIVE_ROOT
