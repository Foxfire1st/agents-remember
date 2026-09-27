"""Retaining the bytes one comparison generation binds: the code pin and the knowledge snapshots.

:mod:`agents_remember.application.review_comparison_freeze` publishes a generation; this module is
where the things it publishes come *from*, and it exists as its own owner because retaining content
and publishing a record about it are different acts with different failure modes. Two kinds of
content are retained, by two different existing owners, and this module adds no third:

* **The code side** is retained explicitly. A captured candidate tree is in no commit, so ``git gc``
  may delete it at any moment; :func:`retain_comparison_source` measures whether durable committed
  history already holds the tree and, only when it does not, asks the retention owner for a pin.
  Custody is therefore a *measurement*, and a comparison whose content has landed stops acquiring refs.
* **Both knowledge halves** are copied through the **storage snapshot owner** --
  ``freeze_closed_snapshot`` holds a read transaction, copies through SQLite, normalises the journal
  mode and reopens the result read-only to prove it -- so what is retained is a closed, complete
  dataset of the recorded identity rather than a file that happened to be copied while a writer was
  live.

A half that is not there is one of exactly two states, and never a third guess: ``not-recorded`` when
the caller has established R05's historical absence, and ``not-selected`` when this comparison
selected no knowledge operand at all. A declared absence standing beside bytes that *are* present is
refused, because a real generation written out of history that way cannot be recovered from the record.

Every function here answers with a value or with a typed refusal, and never by raising a storage error
at its caller: the freeze has to publish a named reason, and an escaping exception is not one.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from agents_remember.application.knowledge_before_half import (
    NOT_RECORDED,
    unreadable_half_refusal,
)
from agents_remember.application.knowledge_composition import open_read_only_store
from agents_remember.application.review_candidate_resolution import (
    ReviewCandidateResolution,
    refusal,
    review_namespace,
)
from agents_remember.application.review_comparison_generation import (
    COMPARISON_KNOWLEDGE_DIRECTORY,
    COMPARISON_MANIFEST_NAME,
    COMPARISON_SNAPSHOT_NAME,
    KNOWLEDGE_NOT_SELECTED,
    ComparisonGenerationRef,
    ComparisonKnowledgeBinding,
    ComparisonSnapshotArtifact,
    ComparisonSourceBinding,
    KnowledgeSide,
    read_manifest,
)
from agents_remember.application.review_comparison_reclamation import (
    CODE_OBJECT_DELETION_OWNER,
    SNAPSHOT_DELETION_OWNER,
)
from agents_remember.errors import CodeObjectRetentionError
from agents_remember.kernel.canonical_json import sha256_digest
from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.closed_snapshot import freeze_closed_snapshot
from agents_remember.memory.knowledge.refusals import KnowledgeRefused, KnowledgeStorageError
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.review import ReviewRefusal
from agents_remember.worktrees.modules.code_object_retention import (
    CUSTODY_COMMITTED_HISTORY,
    CUSTODY_RETAINED,
    CustodyNames,
    RetainedCodeObject,
    code_object_custody,
    object_readable,
    retain_code_object,
    retention_ref,
)
from agents_remember.worktrees.modules.future_code_candidate import FutureCodeCandidateIdentity
from agents_remember.worktrees.modules.git import local_branch_ref
from agents_remember.worktrees.task_resolver import slugify

__all__ = [
    "ComparisonSourceRetention",
    "KnowledgeRetentionRequest",
    "retain_comparison_source",
    "retain_knowledge_sides",
]

# The refusal codes a retention refusal earns, from the shipped comparison vocabulary.
_ABSENT = "candidate_dataset_absent"
_UNRESOLVED = "candidate_unresolved"


@dataclass(frozen=True)
class KnowledgeRetentionRequest:
    """What retaining the two knowledge halves reads: the resolved pair, and what the caller declared.

    One value rather than the freeze's whole request, because the retention owner has no business
    reading a record's scope, records or lineage -- and a narrower input is what keeps it from
    growing one.
    """

    resolution: ReviewCandidateResolution
    historical_absence: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Capture:
    """The two bound objects, the capture that produced one of them, and the names custody asks.

    They travel together because every decision below reads all four: which history is asked, whether
    the tree is already in it, and what the pin has to hold if it is not.
    """

    repository: Path
    baseline: str
    candidate: str
    identity: FutureCodeCandidateIdentity
    names: CustodyNames


@dataclass(frozen=True)
class ComparisonSourceRetention:
    """The source binding, plus the pin *this call* created when it created one.

    ``created_pin`` is separated from the binding because the two answer different questions: the
    binding is what the record stores, and the pin is what a failed publication has to give back. A
    pin that was already there belongs to a generation that is already published, so reclaiming it on
    this call's failure would delete a record this call did not make.
    """

    binding: ComparisonSourceBinding
    repository: Path
    created_pin: RetainedCodeObject | None = None


def retain_comparison_source(
    resolved: ReviewCandidateResolution,
    *,
    retained_from: ComparisonGenerationRef | None = None,
) -> ComparisonSourceRetention | ReviewRefusal:
    """Bind both code objects, retaining the tree explicitly while durable history has not taken it.

    Custody is measured first, against the names :func:`custody_names` derives from the contract, and
    the pin is created only when *those* names do not already hold the tree. A comparison whose
    candidate content has landed on the protected source branch therefore stops acquiring refs, while
    one whose content exists only on the leaf's own disposable work branch keeps its pin -- which is
    what makes the generation survive the leaf. A repository that cannot read one of the two recorded
    objects is refused by name: a generation whose source side does not resolve is not a generation a
    reader could reopen.
    """

    unresolved = _unresolved_capture(resolved, retained_from)
    if isinstance(unresolved, ReviewRefusal):
        return unresolved
    if not object_readable(unresolved.repository, unresolved.baseline) or not object_readable(
        unresolved.repository, unresolved.candidate
    ):
        return refusal(
            _UNRESOLVED,
            f"the bound objects ({unresolved.baseline} and {unresolved.candidate}) are not both "
            f"readable in {unresolved.repository}, so there is nothing to retain",
            next_action="reopen the review so the endpoints are captured again from the live leaf",
            offending_input="candidate",
        )
    if (
        code_object_custody(unresolved.repository, unresolved.candidate, unresolved.names)
        == CUSTODY_COMMITTED_HISTORY
    ):
        return ComparisonSourceRetention(
            binding=_unpinned_binding(unresolved), repository=unresolved.repository
        )
    return _pinned_outcome(resolved, unresolved)


def _unresolved_capture(
    resolved: ReviewCandidateResolution, retained_from: ComparisonGenerationRef | None
) -> _Capture | ReviewRefusal:
    """The captured pair and its custody names, or the refusal for a resolution that carries none.

    A comparison with no enclosure contract has no protected branch to measure custody against, and
    that is a *refusal* rather than an empty name set: measuring against nothing would record
    ``retained`` for every tree and pin generations whose content had already landed.
    """

    capture = resolved.candidate_identity
    if retained_from is not None:
        capture = _retained_capture(resolved, retained_from)
        if isinstance(capture, ReviewRefusal):
            return capture
    repository = resolved.baseline_code_root
    baseline = resolved.baseline_code_tree_id
    candidate = resolved.candidate_code_tree_id
    contract = resolved.contract
    if capture is None or repository is None or baseline is None or candidate is None:
        return refusal(
            _UNRESOLVED,
            "the resolution carries no captured candidate identity, so there is no source endpoint "
            "pair to retain",
            next_action=(
                "resolve the comparison from canonical task context so the capture owner derives "
                "both endpoints"
            ),
            offending_input="candidate",
        )
    if contract is None:
        return refusal(
            _UNRESOLVED,
            "the resolution carries no enclosure contract, so no protected history exists to measure "
            "custody against",
            next_action=(
                "freeze a comparison resolved from canonical task context; the leaf's disposable work "
                "branch is not custody for the content it holds"
            ),
            offending_input="contract",
        )
    return _Capture(
        repository=repository,
        baseline=baseline,
        candidate=candidate,
        identity=capture,
        names=custody_names(contract),
    )


def _retained_capture(
    resolved: ReviewCandidateResolution, retained_from: ComparisonGenerationRef
) -> FutureCodeCandidateIdentity | ReviewRefusal:
    """Revalidate custody of an explicitly selected historical capture; never make it live."""

    closed = resolved.closed_leaf
    if closed is None or closed.manifest is None or closed.reopened.generation != retained_from:
        return refusal(
            _UNRESOLVED,
            "retained source inputs do not name this exact recorded parent",
            offending_input="parent",
            next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
        )
    try:
        manifest = read_manifest(retained_from.directory / COMPARISON_MANIFEST_NAME)
    except KnowledgeStorageError as error:
        return refusal(
            _UNRESOLVED,
            str(error),
            offending_input="parent",
            next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
        )
    if manifest.manifest_digest() != retained_from.manifest_digest or manifest != closed.manifest:
        return refusal(
            _UNRESOLVED,
            "the recorded parent moved before source retention",
            offending_input="parent",
            next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
        )
    source = closed.reopened.source
    if (
        source is None
        or source.state != "available"
        or (
            resolved.baseline_code_tree_id,
            resolved.candidate_code_tree_id,
            str(resolved.baseline_code_root),
            str(resolved.candidate_code_root),
        )
        != (
            manifest.source.baseline_code_tree_id,
            manifest.source.candidate_code_tree_id,
            manifest.source.code_repository_root,
            manifest.source.code_repository_root,
        )
    ):
        return refusal(
            _UNRESOLVED,
            "the retained source endpoints are unavailable or mismatched",
            offending_input="source",
            next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
        )
    return manifest.source.candidate_capture


def custody_names(contract) -> CustodyNames:
    """The durable history one comparison's custody is measured against, from its own contract.

    Two names and no more: the leaf's **protected source branch** -- the branch its work is destined
    for and the one cleanup does not delete -- and the commits the task record actually landed. The
    work branch is deliberately absent. It is the branch ``worktree_abandon`` force-deletes and the one
    ordinary cleanup removes, so a commit that exists only on it is not custody, and a comparison that
    treated it as one would publish a generation whose source side disappears with the leaf.
    """

    branches = (contract.code_source_branch,) if contract.code_source_branch else ()
    commits = tuple(
        commit for commit in (contract.code_commit, contract.integrated_code_commit) if commit
    )
    return CustodyNames(
        durable_refs=tuple(local_branch_ref(branch) for branch in branches),
        recorded_commits=commits,
    )


def _unpinned_binding(capture: _Capture) -> ComparisonSourceBinding:
    """The binding for a tree durable history already holds, which needs no pin of its own."""

    return ComparisonSourceBinding(
        code_repository_root=str(capture.repository),
        baseline_code_tree_id=capture.baseline,
        candidate_code_tree_id=capture.candidate,
        candidate_capture=capture.identity,
        custody=CUSTODY_COMMITTED_HISTORY,
        custody_refs=capture.names.durable_refs,
        custody_commits=capture.names.recorded_commits,
    )


def _pinned_outcome(
    resolved: ReviewCandidateResolution, capture: _Capture
) -> ComparisonSourceRetention | ReviewRefusal:
    """Create the explicit pin for a tree no named durable history holds, or refuse the attempt."""

    repository = capture.repository
    leaf = slugify(resolved.leaf_id)
    ref = retention_ref(leaf, _pin_key(capture.baseline, capture.candidate))
    existed = _ref_present(repository, ref)
    try:
        retained = retain_code_object(
            repository,
            ref=ref,
            tree=capture.candidate,
            base_commit=capture.baseline,
            message=(
                f"Agents Remember comparison retention for {resolved.leaf_id}: keep the captured "
                "candidate tree and its recorded base reachable until committed history holds them"
            ),
        )
    except CodeObjectRetentionError as error:
        return refusal(
            _UNRESOLVED,
            f"the captured candidate tree could not be retained explicitly ({error.status}): {error}",
            next_action=(
                "repair the repository's ref storage, or land the candidate so committed history "
                "holds the tree; a comparison is not published while its source side is unresolvable"
            ),
            offending_input="candidate",
        )
    binding = ComparisonSourceBinding(
        code_repository_root=str(repository),
        baseline_code_tree_id=capture.baseline,
        candidate_code_tree_id=capture.candidate,
        candidate_capture=capture.identity,
        custody=CUSTODY_RETAINED,
        custody_refs=capture.names.durable_refs,
        custody_commits=capture.names.recorded_commits,
        retained=retained,
        deletion_owner=CODE_OBJECT_DELETION_OWNER,
        cleanup_scope=ref,
    )
    return ComparisonSourceRetention(
        binding=binding,
        repository=repository,
        created_pin=None if existed else retained,
    )


def _pin_key(baseline: str, candidate: str) -> str:
    """The stable key naming the two objects one pin keeps alive.

    Derived from the objects rather than from a counter or a generation index, so a second freeze
    binding the same pair addresses the same ref and converges instead of accumulating pins.
    """

    return sha256_digest({"baseline": baseline, "candidate": candidate})[:32]


def _ref_present(repository: Path, ref: str) -> bool:
    """Whether a retention ref already resolves, asked before this call creates one."""

    resolved = run_git(repository, ["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
    return resolved.returncode == 0


# -- the knowledge sides ------------------------------------------------------------------------


def retain_knowledge_sides(
    request: KnowledgeRetentionRequest, stage_directory: Path
) -> tuple[ComparisonKnowledgeBinding, ...] | ReviewRefusal:
    """Retain both halves, or state the typed absence each side legitimately has.

    The pair is preflighted with R05's own reader before anything is copied, so a half that is
    present but cannot be read as a dataset is refused by name instead of raising out of the storage
    owner mid-freeze. A half that is not there is then one of exactly two states, and never a third
    guess: ``not-recorded`` (the caller established the repository never recorded one) or
    ``not-selected`` (this comparison selected no knowledge operand at all).
    """

    resolved = request.resolution
    unreadable = unreadable_half_refusal(resolved.baseline_database, resolved.candidate_database)
    if unreadable is not None:
        return unreadable
    bindings: list[ComparisonKnowledgeBinding] = []
    sides: tuple[tuple[KnowledgeSide, Path], ...] = (
        ("before", resolved.baseline_database),
        ("after", resolved.candidate_database),
    )
    for side, database in sides:
        binding = _side_binding(request, stage_directory, side, database)
        if isinstance(binding, ReviewRefusal):
            return binding
        bindings.append(binding)
    return tuple(bindings)


def _side_binding(
    request: KnowledgeRetentionRequest,
    stage_directory: Path,
    side: KnowledgeSide,
    database: Path,
) -> ComparisonKnowledgeBinding | ReviewRefusal:
    """One half: retained bytes, or the exact state that says why it carries none."""

    declared_absent = side in request.historical_absence
    if not database.is_file():
        closed = request.resolution.closed_leaf
        if closed is not None and closed.manifest is not None:
            expected = next(
                binding for binding in closed.manifest.knowledge if binding.side == side
            )
            if expected.state == "retained":
                return refusal(
                    _ABSENT,
                    f"the expected retained {side} snapshot disappeared before capture",
                    offending_input=side,
                    next_action="Restore the exact parent snapshot; expected history cannot become an absent or unselected input.",
                )
        return _absent_side(side, database, declared_absent)
    if declared_absent:
        return _contradicted_absence(side, database)
    return _freeze_side(database, request.resolution, stage_directory, side)


def _absent_side(
    side: KnowledgeSide, database: Path, declared_absent: bool
) -> ComparisonKnowledgeBinding:
    """The binding for a half with no dataset, as one of the two states that is not a failure."""

    if declared_absent:
        return ComparisonKnowledgeBinding(
            side=side,
            state=NOT_RECORDED,
            reason=(
                f"no {side} dataset exists at {database}, and this comparison records the historical "
                "absence R05 defines: the repository never recorded a generation for this half, "
                "which is a fact about its history rather than a missing input"
            ),
        )
    return ComparisonKnowledgeBinding(
        side=side,
        state=KNOWLEDGE_NOT_SELECTED,
        reason=(
            f"no {side} dataset exists at {database}, and this comparison selected no knowledge "
            "operand, so nothing was asked of this half"
        ),
    )


def _contradicted_absence(side: KnowledgeSide, database: Path) -> ReviewRefusal:
    """Refuse a declared historical absence standing beside the bytes it says never existed."""

    return refusal(
        _ABSENT,
        f"the {side} half was declared a historical absence, and a dataset is present at {database}; "
        "a recorded absence beside present bytes is how a real generation is written out of history",
        next_action=(
            "declare the absence only for a half that carries no dataset, or freeze the generation "
            "the present bytes belong to; the surface substitutes no other dataset"
        ),
        offending_input=database.name,
    )


def _freeze_side(
    database: Path,
    resolved: ReviewCandidateResolution,
    stage_directory: Path,
    side: KnowledgeSide,
) -> ComparisonKnowledgeBinding | ReviewRefusal:
    """Copy one half into the stage through the storage snapshot owner and bind what it holds.

    A half that cannot be frozen is a *refusal*, not an exception: the freeze has to publish a named
    reason, and a storage error escaping into it would be a traceback where the surface promises a
    state.
    """

    relative = f"{COMPARISON_KNOWLEDGE_DIRECTORY}/{side}/{COMPARISON_SNAPSHOT_NAME}"
    stage_path = stage_directory / relative
    stage_path.parent.mkdir(parents=True, exist_ok=True)
    namespace = review_namespace(resolved.repository_id, database)
    store = None
    try:
        store = open_read_only_store(database, namespace)
        # The identity is read from the dataset's own bound namespace row, and the storage owner
        # requires the namespace it was opened under to be the same one. Asking here turns that
        # precondition into a refusal naming both ids instead of a storage error raised mid-copy.
        identity = store.snapshot_identity()
        closed = resolved.closed_leaf
        if closed is not None and closed.manifest is not None:
            original = next(
                binding for binding in closed.manifest.knowledge if binding.side == side
            )
            if original.state != "retained" or identity != original.identity:
                return refusal(
                    _UNRESOLVED,
                    f"the retained {side} knowledge endpoint moved before capture",
                    offending_input=side,
                    next_action="Restore the exact retained parent and owner artifacts, then retry the explicitly selected operation; current inputs are not substitutes.",
                )
        if identity.repository_id != namespace:
            return _namespace_refusal(side, database, namespace, identity)
        prepared = freeze_closed_snapshot(store, identity, stage_path)
        byte_count = stage_path.stat().st_size
    except (KnowledgeRefused, KnowledgeStorageError, OSError) as error:
        return _snapshot_refusal(side, database, error)
    finally:
        if store is not None:
            store.close()
    return ComparisonKnowledgeBinding(
        side=side,
        state="retained",
        identity=prepared.identity,
        artifact=ComparisonSnapshotArtifact(
            relative_path=relative,
            sha256=prepared.file_digest,
            byte_count=byte_count,
            deletion_owner=SNAPSHOT_DELETION_OWNER,
            cleanup_scope=f"{COMPARISON_KNOWLEDGE_DIRECTORY}/{side}",
        ),
    )


def _snapshot_refusal(side: KnowledgeSide, database: Path, error: Exception) -> ReviewRefusal:
    """The refusal for a half whose bytes could not be frozen into a closed snapshot."""

    return refusal(
        _ABSENT,
        (
            f"the {side} dataset at {database} could not be retained as a closed snapshot of this "
            f"code ({error}), so the generation would record a digest of bytes it does not hold"
        ),
        next_action=(
            "repair or replace that half of the leaf's disposable knowledge root, then freeze the "
            "comparison again; the surface retains no copy of a dataset it could not read"
        ),
        offending_input=database.name,
    )


def _namespace_refusal(
    side: KnowledgeSide, database: Path, namespace: str, identity: SnapshotIdentity
) -> ReviewRefusal:
    """The refusal for a half bound to a namespace this comparison does not address."""

    return refusal(
        _ABSENT,
        (
            f"the {side} dataset at {database} is bound to namespace {identity.repository_id}, "
            f"while this comparison addresses {namespace}; a snapshot of it would be retained under "
            "an identity the dataset does not hold"
        ),
        next_action=(
            "open the comparison on the datasets the candidate's own namespace binds, or place the "
            "half this leaf forks from; the surface retains no snapshot under another namespace"
        ),
        offending_input=database.name,
    )
