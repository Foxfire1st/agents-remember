"""The exact source endpoints and the dataset pair one curator review binds.

A review reads two things at once: the *records* two knowledge datasets hold, and the *source* the
candidate's recorded anchors are resolved against. The record half names its own two files; the
source half is a pair of **immutable Git object identities**, and this module is the one place that
derives them:

* the **baseline** is the enclosure contract's recorded ``code_base_commit`` -- the commit the leaf
  forked from, written when the worktree was created and never re-derived from a branch or from
  ``HEAD``;
* the **candidate** is the tree
  :func:`~agents_remember.worktrees.modules.future_code_candidate.capture_future_code_candidate`
  derives through a private index: the leaf's ``HEAD`` with every staged, unstaged and eligible
  untracked change applied (ignored paths stay excluded by the existing policy), with the real Git
  index left byte-identical. The *working tree* is therefore never the endpoint, and a review of
  "HEAD to unstaged" that called itself the full task diff is exactly what this binding prevents.

Each root travels with its tree id, because a tree id without the repository that holds it is not
resolvable; the read context refuses a half-resolution rather than falling back to a working tree.
Both sides of a live leaf therefore name the **repository** the contract records: a linked worktree
shares its repository's object store, so the two bound objects resolve in either, and the repository
is the root a durable comparison generation records -- which is what lets a closed leaf's review
reproduce the live one (ICR-R12).

**A leaf whose enclosure is closed is resolved from its records, not refused.**
:mod:`agents_remember.application.review_committed_leaf` owns that resolution -- the leaf's published
comparison generation, or the source range its enclosure contract recorded -- and this module
delegates to it. ``candidate_not_live`` remains the answer only for a leaf that is neither live nor
recorded, which is a state no record can answer for.

Every way the derivation can fail is a **named state**: a contract that records no base commit, a
capture the shipped owner refused, and a capture whose inputs moved while the review was being
composed each produce a typed
:class:`~agents_remember.models.knowledge.review.ReviewRefusal` that names the offending side and
the action that yields a reviewable candidate. ``None`` is never returned for an endpoint that could
not be bound, and no refusal is softened into a favourable default: ``HEAD``, another branch and a
different working tree are all unavailable as substitutes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import apsw

from agents_remember.application.knowledge_baseline_generation import (
    read_baseline_generation,
)
from agents_remember.application.review_tree_comparison import (
    live_review_trees,
    recheck_memory_candidate,
    tree_resolution,
)
from agents_remember.errors import FutureCodeCandidateError
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.candidate_receipt import read_candidate_receipt
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge_index import IndexMismatchError, KnowledgeIndex
from agents_remember.models.knowledge.review import (
    ReviewCandidateRef,
    ReviewRefusal,
    ReviewRefusalCode,
)
from agents_remember.models.knowledge.snapshot import (
    CANDIDATE_DATABASE_NAME,
    CANDIDATE_RECEIPT_NAME,
)
from agents_remember.worktrees.modules.future_code_candidate import (
    FutureCodeCandidateIdentity,
    capture_future_code_candidate,
)
from agents_remember.worktrees.task_resolver import slugify
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

if TYPE_CHECKING:  # pragma: no cover - the annotation only; the value's owner imports this module
    from agents_remember.application.review_committed_leaf import ClosedLeafReview
    from agents_remember.application.review_tree_comparison import ReviewTrees

__all__ = [
    "REVIEW_BASELINE_DIRECTORY",
    "REVIEW_CANDIDATE_DIRECTORY",
    "REVIEW_CANDIDATE_RELATIVE_ROOT",
    "ReviewCandidateResolution",
    "candidate_receipt_refusal",
    "candidate_ref",
    "missing_dataset_half",
    "recorded_leaf_contract",
    "refusal",
    "require_current_candidate_identity",
    "resolve_review_candidate",
    "review_namespace",
    "unreadable_candidate_refusal",
]

# Where a leaf's reviewable datasets live. Both are inside the leaf's **disposable** local root --
# ``<worktree-group>/provider-runtime/dev-ar-coordination/``, the one root the checkout-coordination
# contract declares a linked task worktree may hold undeclared state under -- so a review reads no
# candidate out of the live coordination tree and writes beside none. The candidate half holds the
# database the leaf is authoring; the baseline half holds the dataset that candidate descends from.
# (This layout is the review surface's own recorded decision for this increment.)
REVIEW_CANDIDATE_RELATIVE_ROOT = Path("provider-runtime") / "dev-ar-coordination" / "knowledge"

# The two halves are named once, here, because two owners read them: the resolution below derives the
# pair it reviews from them, and the ingest CLI derives the candidate directory it authors into from
# the same two names. One spelling is what makes "the candidate the leaf authored" and "the candidate
# the review resolved" the same directory rather than two conventions that happen to agree today.
REVIEW_BASELINE_DIRECTORY = "baseline"
REVIEW_CANDIDATE_DIRECTORY = "candidate"

# The three fields of the capture's own identity, each with the words a refusal needs to name it. The
# order is the order the capture observes them, so a refusal lists the moved inputs left to right.
_CAPTURE_INPUTS: tuple[tuple[str, str], ...] = (
    ("observedCodeHead", "the leaf worktree's code HEAD"),
    ("codeBaseCommit", "the contract's recorded task base commit"),
    ("codeCandidateTree", "the captured candidate tree"),
)

# The one action that produces a reviewable candidate once a capture input has moved. It is stated
# once because three refusals need it and a caller acts on all three the same way.
_RECAPTURE_ACTION = (
    "reopen the review so the candidate is captured again from the leaf's live worktree; the surface "
    "substitutes neither the moved HEAD, nor another branch, nor a different working tree"
)


@dataclass(frozen=True)
class ReviewCandidateResolution:
    """The two datasets and two source trees one admitted live curator candidate resolves to.

    Both source sides are bound here and neither is optional in a resolution this module produced:
    a side that could not be bound is refused by name before a resolution exists, so a caller never
    reads an endpoint as an absent ``None``. ``baseline_code_tree_id`` is the contract's recorded
    base *commit* -- the recorded task endpoint -- and ``candidate_code_tree_id`` is the captured
    candidate *tree*.

    ``candidate_identity`` is the capture's own complete identity, carried so the composition can
    re-derive it and require it to still be current before the comparison is published. A resolution
    that was hand-assembled rather than resolved (a caller comparing two named files) carries none,
    and names no contract to recheck against.
    """

    repository_id: str
    leaf_id: str
    baseline_database: Path
    candidate_database: Path
    baseline_code_root: Path | None
    candidate_code_root: Path | None
    baseline_code_tree_id: str | None
    candidate_code_tree_id: str | None
    # The enclosure contract the resolution read, when there was one. The composition ignores it;
    # it is carried so a caller that also needs the recorded task facts (the published assessment
    # collection, for instance) reads them from the same resolution rather than resolving twice.
    contract: WorktreeContract | None = None
    # The captured candidate identity, when this resolution derived one. Never re-derived here: the
    # recheck belongs to the composition, immediately before the comparison is published.
    candidate_identity: FutureCodeCandidateIdentity | None = None
    # The durable record this resolution was reopened from, when it was not resolved from a live
    # enclosure (ICR-R12). A live resolution carries none: ``None`` here means "this candidate has a
    # worktree and was captured from it", and a resolved value means "the leaf is closed and this is
    # the record the surface re-opened". Its own owner is
    # :mod:`agents_remember.application.review_committed_leaf`, which is also the only thing that
    # constructs one.
    closed_leaf: ClosedLeafReview | None = None
    # The four-tree comparison this resolution reads, when the leaf's memory is converted (MIK-R25).
    # Both database paths are then the derived indexes of the two memory trees, never a copy; a
    # knowledge side Git can no longer produce names no file. ``None`` is the dataset review.
    trees: ReviewTrees | None = None
    # ``(side, state, detail)`` for each knowledge side a recorded comparison can no longer read --
    # ``legacy-unavailable`` for a comparison recorded before the repository's conversion (MIK-R25
    # rule 4). A side listed here names no file, so nothing reads a database in its place.
    knowledge_unavailable: tuple[tuple[str, str, str], ...] = ()


def resolve_review_candidate(
    config: McpRuntimeConfig,
    repository_id: str,
    master: str,
    leaf_id: str,
    *,
    recorded: bool = False,
) -> ReviewCandidateResolution | ReviewRefusal:
    """Resolve one curator candidate from canonical task context, or refuse by name.

    ``recorded`` is the caller's own statement of *which record it is reading* (ICR-R12):
    ``True`` resolves the leaf's published comparison generation -- the same answer whether the
    enclosure is live or cleanup has removed it -- and the default resolves the live candidate when
    there is one. A leaf whose enclosure is **closed** resolves from its records either way, because
    there is no live candidate to resolve; that fallback is what the intake defect needed, and it is
    a resolution rather than a refusal so the same comparison stays openable after cleanup.
    """

    for segment, value in (("repository", repository_id), ("master", master), ("leaf", leaf_id)):
        if not value or "/" in value or "\\" in value or value.startswith("."):
            return refusal(
                "candidate_unresolved",
                f"the {segment} selector is not a single path segment",
                next_action="name the canonical task context: repository, master and leaf id",
                offending_input=value,
            )
    contract = recorded_leaf_contract(config, repository_id, master, leaf_id)
    if contract is None:
        return refusal(
            "candidate_unresolved",
            "no readable leaf enclosure contract records this leaf under the named master",
            next_action=(
                "open the review for an admitted live curator candidate whose enclosure contract "
                "exists under tasks/<repository>/<master>/enclosures"
            ),
            offending_input=f"{master}/{leaf_id}",
        )
    if recorded or contract.code_worktree is None or not contract.code_worktree.exists():
        # Either the caller named the leaf's recorded comparison, or the leaf's enclosure is closed
        # and its records are the only candidate there is. The owner of that resolution is
        # :mod:`agents_remember.application.review_committed_leaf`; the import is local because that
        # module resolves through this one's contract accessor, so a module-level import would be a
        # cycle with a type annotation on it.
        from agents_remember.application.review_committed_leaf import (  # noqa: PLC0415 - cycle
            resolve_committed_leaf_review,
        )

        return resolve_committed_leaf_review(config, repository_id, master, leaf_id)
    if not contract.code_base_commit:
        return refusal(
            "candidate_unresolved",
            "the leaf's enclosure contract records no code base commit, so the review has no "
            "baseline source endpoint to bind",
            next_action=(
                "repair the leaf's recorded base in its enclosure contract, then reopen the review; "
                "the surface binds no branch, no HEAD and no other working tree in its place"
            ),
            offending_input="baseline",
        )
    captured = _captured_candidate(contract)
    if isinstance(captured, ReviewRefusal):
        return captured
    return _live_resolution(config, repository_id, contract, captured)


def _live_resolution(
    config: McpRuntimeConfig,
    repository_id: str,
    contract: WorktreeContract,
    captured: FutureCodeCandidateIdentity,
) -> ReviewCandidateResolution | ReviewRefusal:
    """The live pair: four Git trees for a converted leaf (MIK-R25), else the dataset pair.

    Every leaf whose memory is unconverted keeps the dataset pair below, byte for byte.
    """

    trees = live_review_trees(config.coordination_root, contract, captured.codeCandidateTree)
    if isinstance(trees, ReviewRefusal):
        return trees
    if trees is not None:
        return tree_resolution(repository_id, contract, trees, candidate_identity=captured)
    root = contract.worktree_group / REVIEW_CANDIDATE_RELATIVE_ROOT
    return ReviewCandidateResolution(
        repository_id=repository_id,
        leaf_id=contract.leaf_id,
        baseline_database=root / REVIEW_BASELINE_DIRECTORY / CANDIDATE_DATABASE_NAME,
        candidate_database=root / REVIEW_CANDIDATE_DIRECTORY / CANDIDATE_DATABASE_NAME,
        baseline_code_root=contract.code_repo_path,
        # The candidate side resolves to **both** a root and a tree id or to neither: the read
        # context refuses a root without a tree id, and correctly so -- that is an incomplete source
        # resolution rather than a licence to read a working tree. Both are supplied from the same
        # capture: the tree the isolated index produced, and the repository that holds it.
        #
        # The root is the *repository* rather than the disposable checkout the capture was taken in,
        # because a tree id is resolvable exactly where the object lives: a linked worktree shares
        # its repository's object store, so both objects resolve in both roots, and naming the
        # repository is what makes the comparison the surface composes identical to the one its
        # durable generation records (ICR-R12) -- an inventory whose reproduction command names a
        # checkout that cleanup removes is not reproducible from the record. The capture itself still
        # reads the live worktree; only the root the two bound objects are read in is the
        # repository's.
        candidate_code_root=contract.code_repo_path,
        baseline_code_tree_id=contract.code_base_commit,
        candidate_code_tree_id=captured.codeCandidateTree,
        contract=contract,
        candidate_identity=captured,
    )


def candidate_ref(
    resolved: ReviewCandidateResolution, *, repository_id: str, master: str
) -> ReviewCandidateRef:
    """The reviewed candidate as the surface names it: task context plus the resolved leaf id.

    This is the one construction of that value, shared by the subject review and the task-context
    review, so the two cannot come to name different leaves for the same resolution. It carries only
    identities a caller may legitimately name -- no path appears in it -- and the leaf id is the
    resolution's own, never the requested spelling, because the resolution is what located it.
    """

    return ReviewCandidateRef(
        repository_id=repository_id,
        master=master,
        leaf_id=resolved.leaf_id,
        task_ref=master,
    )


def require_current_candidate_identity(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    """Re-derive the captured candidate and refuse by name when one of its inputs has moved.

    The comparison is composed *after* the capture and reads several files while it runs, so a
    capture that was current when it was taken can be stale by the time a payload would be returned.
    Publishing that payload would bind a generation to a candidate the leaf no longer has, under a
    label that says it is the candidate's own comparison. This is the recheck: the shipped capture
    owner recomputes the whole identity, and any field that disagrees names the exact side that moved
    plus the action that produces a current one.

    A resolution with no capture to recheck -- a hand-assembled pair naming two files -- has nothing
    to re-derive and is left exactly as it was assembled.
    """

    accepted = resolved.candidate_identity
    contract = resolved.contract
    if accepted is None or contract is None:
        return None
    if resolved.trees is not None:
        moved_memory = recheck_memory_candidate(resolved.trees)
        if moved_memory is not None:
            return moved_memory
    try:
        current = capture_future_code_candidate(contract)
    except FutureCodeCandidateError as error:
        return _capture_refusal(error)
    moved = tuple(
        label
        for field, label in _CAPTURE_INPUTS
        if getattr(accepted, field) != getattr(current, field)
    )
    if not moved:
        return None
    return _moved_candidate_refusal(accepted, current, moved)


def _moved_candidate_refusal(
    accepted: FutureCodeCandidateIdentity,
    current: FutureCodeCandidateIdentity,
    moved: tuple[str, ...],
) -> ReviewRefusal:
    """The refusal for a capture whose inputs moved, carrying both identities it compared.

    ``expected`` and ``observed`` are the two complete captures, rendered by the model that owns
    them rather than restated here: a reader can compare the side the detail names against the exact
    object ids it changed from and to.
    """

    return ReviewRefusal(
        code="candidate_unresolved",
        detail=(
            "the candidate's captured source endpoint moved while the review was being composed: "
            f"{'; '.join(moved)} changed, so the comparison that was read no longer describes the "
            "candidate this leaf holds"
        ),
        next_action=_RECAPTURE_ACTION,
        offending_input="; ".join(moved),
        expected=accepted.model_dump_json(),
        observed=current.model_dump_json(),
    )


def missing_dataset_half(resolved: ReviewCandidateResolution) -> tuple[str, Path] | None:
    """The half of the candidate pair that is absent, or ``None`` when both are on disk.

    A comparison is *between* two datasets, so an absent half is not a smaller comparison -- the
    shipped operation refuses a side whose database is not a file, and it refuses it by returning a
    typed result. Preflighting here is what keeps that refusal a named state instead of a storage
    exception raised from inside a read-context construction, and it is also what lets the refusal
    say *which* half is missing: ``baseline`` and ``candidate`` are different facts about a leaf, and
    a reader who is told "the datasets are absent" cannot tell whether to author a candidate or to
    place the dataset it forks from.
    """

    for half, database in (
        ("baseline", resolved.baseline_database),
        ("candidate", resolved.candidate_database),
    ):
        if not database.is_file():
            return half, database
    return None


def review_namespace(requested: str, database: Path) -> str:
    """The namespace to read one dataset under, from the record standing beside it.

    The namespace and the requested repository are not the same string: a request names a
    *repository* ("agents-remember"), while a dataset the write plane admitted is bound to a
    *namespace* id derived from it, and a side opened under the requested spelling refuses against the
    dataset's own binding. So the dataset's own **record** is the authority -- whatever the write
    plane wrote beside these bytes when it placed them -- and a review of an admitted pair reads the
    namespace the pair actually holds.

    **Two records answer, because the two halves of a comparison are placed by two different acts.**
    A *candidate* half is placed by an admission, which seals ``candidate-receipt.json`` beside it.
    A *before* half is placed either by the first-generation owner, which leaves the admission's own
    receipt beside the empty dataset it creates, or by a run handed a published ``--baseline``, which
    writes ``baseline-generation.json`` -- and that record names the namespace the captured bytes
    belong to. Consulting only the receipt was wrong for the second case in a way that could not
    surface while nothing called the freeze: the before half of every continuity run is a selected
    baseline, it never carries a receipt, and the fallback below would then stand the *requested*
    repository in for a dataset bound to a namespace id, which the storage owner refuses. One rule --
    the record beside the bytes -- read from whichever record the half's own placement wrote.

    A dataset with **neither** record beside it is one this surface was handed directly rather than
    one the write plane placed (a fixture, a comparison a caller assembled from two named files). For
    that shape the requested repository *is* the available identity and is read as it always was,
    because the alternative -- refusing every caller-assembled pair -- would break the comparison
    contract for inputs that were never placed by a run.

    A record that **exists but cannot be read** is a different fact and is refused: something wrote a
    record here and it does not say which namespace this dataset belongs to, so standing in the
    caller's word for the dataset's own record is exactly how a review comes to read a namespace
    nothing admitted.
    """

    receipt_path = database.parent / CANDIDATE_RECEIPT_NAME
    if receipt_path.exists():
        return read_candidate_receipt(receipt_path).repository_id
    generation = read_baseline_generation(database.parent)
    if generation is not None:
        return generation.repository_id
    return _index_namespace(database) or requested


def _index_namespace(database: Path) -> str | None:
    """The namespace of a derived knowledge index (MIK-R25), or ``None`` for any other file.

    A tree comparison reads each memory side through its index, which is a dataset of the store's
    schema bound to the index's own constant namespace; the index's format marker is its record.
    Every other file -- a dataset with no record beside it, or no file -- answers ``None`` and
    keeps the requested repository, exactly as before.
    """

    if not database.is_file():
        return None
    try:
        with KnowledgeIndex(database) as index:
            return index.repository_id
    except (IndexMismatchError, KnowledgeStorageError, OSError, apsw.Error):
        return None


# The next action one unreadable candidate record earns. It is stated once because the whole point of
# the refusal is that an operator can act on it: the subject route, the entry route and the task-context
# route all answer the same bytes, and three copies of this sentence is how they stop agreeing.
_REPAIR_CANDIDATE_ACTION = (
    "repair the candidate's receipt and dataset in the leaf's disposable knowledge root, then reopen "
    "the review; the surface substitutes no other dataset"
)


def unreadable_candidate_refusal(resolved: ReviewCandidateResolution, reason: str) -> ReviewRefusal:
    """The one refusal a candidate whose own recorded receipt cannot be read earns.

    Every route that opens this candidate answers this state with this value: the entry list, the
    subject review and the task-context review. The code is the shipped ``candidate_dataset_absent``
    -- this vocabulary's code for a pair input that cannot be opened -- and the detail names the
    candidate's own failure rather than the caller's, so the three routes cannot drift apart about the
    same bytes.
    """

    return refusal(
        "candidate_dataset_absent",
        f"the resolved candidate could not be opened for review: {reason}",
        next_action=_REPAIR_CANDIDATE_ACTION,
        offending_input=resolved.candidate_database.parent.name,
    )


def candidate_receipt_refusal(resolved: ReviewCandidateResolution) -> ReviewRefusal | None:
    """The refusal this candidate's own receipt earns, or ``None`` when it reads as one.

    This is the *preflight* form, for a caller that wants the state as a value instead of an exception:
    it asks the same question :func:`review_namespace` answers and turns its failure into the typed
    refusal above. A caller that would rather catch the storage error can still build the identical
    value from it with :func:`unreadable_candidate_refusal`.
    """

    try:
        review_namespace(resolved.repository_id, resolved.candidate_database)
    except (KnowledgeStorageError, OSError, ValueError) as error:
        return unreadable_candidate_refusal(resolved, str(error))
    return None


def refusal(
    code: ReviewRefusalCode,
    detail: str,
    *,
    next_action: str,
    offending_input: str | None = None,
) -> ReviewRefusal:
    """One typed review refusal: what was asked, why it cannot be answered, what to do instead."""

    return ReviewRefusal(
        code=code,
        detail=detail,
        next_action=next_action,
        offending_input=offending_input,
    )


def _captured_candidate(
    contract: WorktreeContract,
) -> FutureCodeCandidateIdentity | ReviewRefusal:
    """Capture the leaf's candidate identity, or the named refusal the capture owner's failure earns.

    The capture is the existing owner's and stays the owner's: it derives the full add-all tree
    through a private index and already re-derives the observed ``HEAD`` after the tree is written,
    so a head that moves *during* the capture is caught there. This function adds no second capture
    path -- it only turns the owner's typed failure into the surface's named state.
    """

    try:
        return capture_future_code_candidate(contract)
    except FutureCodeCandidateError as error:
        return _capture_refusal(error)


def _capture_refusal(error: FutureCodeCandidateError) -> ReviewRefusal:
    """The surface's refusal for one capture-owner failure, naming the candidate side and the action."""

    return refusal(
        "candidate_unresolved",
        (
            f"the candidate's source endpoint could not be captured in the leaf's live worktree "
            f"({_status_text(error)}): {error}"
        ),
        next_action=_RECAPTURE_ACTION,
        offending_input="candidate",
    )


def _status_text(error: FutureCodeCandidateError) -> str:
    """The capture owner's own status spelling, for a refusal that has to be actionable."""

    return str(getattr(error, "status", "unavailable"))


def recorded_leaf_contract(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> WorktreeContract | None:
    """The one enclosure contract recorded for this leaf, or ``None`` when none is readable.

    It is public because two resolutions read it and they must read the *same* record: the live
    resolution below, and the closed-leaf resolution
    :mod:`agents_remember.application.review_committed_leaf` owns. Two scans of the enclosure
    directory would be two answers to "which contract is this leaf's", and the second one is exactly
    how a review comes to be composed for a leaf nobody addressed.
    """

    task_root = config.coordination_root / "tasks" / repository_id / master
    if not task_root.is_dir():
        return None
    want = slugify(leaf_id)
    for path in sorted((task_root / "enclosures").glob("*/series-contract.md")):
        try:
            contract = load_contract(path)
        except (ContractError, OSError):
            continue
        if contract.repo_name != repository_id or contract.cleanup == "abandoned":
            continue
        if master not in (contract.parent_task_name, contract.task_name):
            continue
        if slugify(contract.leaf_id) == want:
            return contract
    return None
