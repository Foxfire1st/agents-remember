"""The Intent Reviewer's thin adapter over the shared read, diff and view operations.

This module **selects nothing**. It resolves which candidate a task context names, calls the
shipped operations, and assembles their results into the typed payload
:mod:`agents_remember.models.knowledge.review` defines. R07's selection policy, R08's comparison
result, `Route` as the recorded scope axis and L20's review matrix are consumed exactly as their
owners publish them: no scope is computed, no frontier is widened, no reference is re-resolved and
no row is re-diffed here.

**Rank, and why the adapter is here rather than in ``serving/``.** ``layers.toml`` ranks ``serving``
below ``application``, so a serving module may not import the application operations this adapter
composes. The dashboard reaches it the way it reaches the launch-capsule compiler: through a port on
``ServingCollaborators`` that the composition root wires. The HTTP shim therefore does transport
only, and the composition lives at the tier that may import the operations.

**The candidate is resolved from canonical task context.** A caller names a repository, a master and
a leaf id. The leaf's enclosure contract is located from the recorded task root -- never from a
caller-supplied path -- and the two datasets the comparison is between are derived from the
contract's own recorded worktree group. A candidate that does not resolve is refused by name; the
current ``HEAD``, a guessed worktree path and a browser-supplied path are all unavailable as
fallbacks, because none of them is reachable from this module's inputs.

**Every absence is a state.** An unresolvable author, a missing operand, an absent assessment
collection and a comparison the shipped operation refused each produce a named field or a typed
refusal -- never a blank a reader could take for a measured zero, and never a favourable default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_views import read_knowledge_view
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.candidate_receipt import read_candidate_receipt
from agents_remember.memory.knowledge.diff_display import TreeDifferenceProbe
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.memory.knowledge.store import (
    OpenedKnowledgeStore,
    open_existing_knowledge_store,
)
from agents_remember.models.knowledge.detection import DetectionSignalPayload
from agents_remember.models.knowledge.diff import (
    KnowledgeDiffItem,
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
)
from agents_remember.models.knowledge.evidence import VerificationObservationPayload
from agents_remember.models.knowledge.read import (
    FamilyIdentitySeed,
    InvariantIdentitySeed,
    ItemKind,
    KnowledgeReadSeed,
    ReadItem,
)
from agents_remember.models.knowledge.review import (
    PROPOSED_ASSESSMENT_DISPOSITIONS,
    ComparisonIdentity,
    KnowledgeReviewPayload,
    KnowledgeReviewResult,
    ReviewAssessmentDisplay,
    ReviewAuthoredEffect,
    ReviewCandidateRef,
    ReviewEntry,
    ReviewEntryListResult,
    ReviewEvidenceLink,
    ReviewEvidencePane,
    ReviewFieldChange,
    ReviewKnowledgePane,
    ReviewObservation,
    ReviewRefusal,
    ReviewRefusalCode,
    ReviewRemainingCount,
    ReviewRevisionGroup,
    ReviewSideContent,
    ReviewSignal,
    ReviewSourceLocation,
    ReviewSourcePane,
    ReviewStaleness,
    ReviewSubjectKind,
    ReviewSubmission,
    ReviewSurfaceRequest,
    ReviewUnresolvedReference,
)
from agents_remember.models.knowledge.snapshot import (
    CANDIDATE_DATABASE_NAME,
    CANDIDATE_RECEIPT_NAME,
)
from agents_remember.models.knowledge.view import ReviewMatrixRow, ViewRequest
from agents_remember.models.lifecycles.review_assessment import (
    ReviewAssessment,
    SubjectAssessmentState,
    assessment_state_for,
)
from agents_remember.worktrees.integration.closeout.curator_coherence import (
    CuratorCoherenceError,
    load_curator_coherence_authority,
)
from agents_remember.worktrees.task_resolver import slugify
from agents_remember.worktrees.worktree_contract import (
    ContractError,
    WorktreeContract,
    load_contract,
)

__all__ = [
    "EMPTY_REVIEW_RECORDS",
    "REVIEW_BASELINE_DIRECTORY",
    "REVIEW_CANDIDATE_DIRECTORY",
    "REVIEW_CANDIDATE_RELATIVE_ROOT",
    "REVIEW_MATRIX_KINDS",
    "ReviewCandidateResolution",
    "ReviewRecordInputs",
    "ReviewSurfaceRequest",
    "compose_review",
    "list_knowledge_review_entries",
    "read_knowledge_review",
    "resolve_review_candidate",
    "review_records_for",
]

# Where a leaf's reviewable datasets live. Both are inside the leaf's **disposable** local root --
# ``<worktree-group>/provider-runtime/dev-ar-coordination/``, the one root the checkout-coordination
# contract declares a linked task worktree may hold undeclared state under -- so a review reads no
# candidate out of the live coordination tree and writes beside none. The candidate half holds the
# database the leaf is authoring; the baseline half holds the dataset that candidate descends from.
# (This layout is the review surface's own recorded decision for this increment.)
REVIEW_CANDIDATE_RELATIVE_ROOT = Path("provider-runtime") / "dev-ar-coordination" / "knowledge"

# The two halves are named once, here, because two owners read them: this adapter resolves the pair
# it reviews from them, and the ingest CLI derives the candidate directory it authors into from the
# same two names. One spelling is what makes "the candidate the leaf authored" and "the candidate the
# review resolved" the same directory rather than two conventions that happen to agree today.
REVIEW_BASELINE_DIRECTORY = "baseline"
REVIEW_CANDIDATE_DIRECTORY = "candidate"

# The record kinds the review matrix is asked for. They are an input to L20's view rather than a
# selection policy of this leaf's: the view applies its own registered traversal over them.
REVIEW_MATRIX_KINDS: tuple[str, ...] = (
    "requirement_revision",
    "invariant_effect_claim",
    "preservation_claim",
    "unresolved_question",
    "evidence_claim",
)

# The record kinds the knowledge pane renders as mechanically-sourced *authored* records. They are
# the author's own rows; none of them is a detection fact, and the pane keeps the two collections
# apart by type rather than by a rendering convention.
AUTHORED_EFFECT_KINDS: frozenset[str] = frozenset(
    {"invariant_effect_claim", "preservation_claim", "unresolved_question"}
)

_IDENTITY_ITEM_KINDS: frozenset[str] = frozenset({"invariant", "family"})
_REALIZATION_ITEM_KIND = "realization"
_REALIZATION_READ_KIND: ItemKind = "realization_claim"


@dataclass(frozen=True)
class ReviewRecordInputs:
    """The records the renderer is *given*, rather than records it goes and selects for itself.

    Each collection belongs to another owner's read path, and the surface renders what it is handed.
    An empty collection is a stated absence and never a fabricated positive: no assessments means
    every subject is displayed ``unassessed``, and no observations means the evidence pane carries
    none -- neither is defaulted to a clearance.
    """

    assessments: tuple[ReviewAssessment, ...] = ()
    current: Mapping[str, Mapping[tuple[str, str], tuple[str, str]]] | None = None
    signals: tuple[DetectionSignalPayload, ...] = ()
    observations: tuple[VerificationObservationPayload, ...] = ()


# The empty record set, as one module-level value: a call in an argument default would rebuild it on
# every call, and the collections it holds are immutable tuples.
EMPTY_REVIEW_RECORDS = ReviewRecordInputs()


@dataclass(frozen=True)
class ReviewCandidateResolution:
    """The two datasets and two source trees one admitted live curator candidate resolves to.

    ``baseline_code_tree_id`` and ``candidate_code_tree_id`` are ``None`` when the contract records
    no commit for that side: a side with no exact tree is a side whose anchors were not resolved,
    which is a supported state and never a prompt to substitute a working tree.
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


def resolve_review_candidate(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> ReviewCandidateResolution | ReviewRefusal:
    """Resolve one admitted live curator candidate from canonical task context, or refuse by name."""

    for segment, value in (("repository", repository_id), ("master", master), ("leaf", leaf_id)):
        if not value or "/" in value or "\\" in value or value.startswith("."):
            return refusal(
                "candidate_unresolved",
                f"the {segment} selector is not a single path segment",
                next_action="name the canonical task context: repository, master and leaf id",
                offending_input=value,
            )
    contract = _leaf_contract(config, repository_id, master, leaf_id)
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
    if contract.code_worktree is None or not contract.code_worktree.exists():
        return refusal(
            "candidate_not_live",
            "the leaf's enclosure has no live worktree, so there is no candidate to review",
            next_action=(
                "review a leaf whose worktree is live; a landed leaf's committed change-set stays "
                "inspectable through the existing change-set views and is not this surface"
            ),
            offending_input=contract.leaf_id,
        )
    root = contract.worktree_group / REVIEW_CANDIDATE_RELATIVE_ROOT
    return ReviewCandidateResolution(
        repository_id=repository_id,
        leaf_id=contract.leaf_id,
        baseline_database=root / REVIEW_BASELINE_DIRECTORY / CANDIDATE_DATABASE_NAME,
        candidate_database=root / REVIEW_CANDIDATE_DIRECTORY / CANDIDATE_DATABASE_NAME,
        baseline_code_root=contract.code_repo_path,
        # The candidate side resolves to **both** a root and a tree id or to neither. The read
        # context refuses a root without a tree id, and correctly so: that is an incomplete source
        # resolution rather than a licence to read a working tree. A live leaf has no commit for its
        # own uncommitted line -- ``candidate_code_tree_id`` is ``None`` by this module's own recorded
        # decision -- so this side supplies no root either, and the comparison reports the source
        # expansion it could not make instead of resolving one against a tree nothing named.
        candidate_code_root=None,
        baseline_code_tree_id=contract.code_base_commit or None,
        candidate_code_tree_id=None,
        contract=contract,
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


def review_namespace(requested: str, candidate_database: Path) -> str:
    """The namespace to read the candidate's datasets under, from its receipt when it has one.

    The namespace and the requested repository are not the same string: a request names a
    *repository* ("agents-remember"), while a candidate the write plane admitted is bound to a
    *namespace* id derived from it, and a side opened under the requested spelling refuses against
    the dataset's own binding. So the candidate's own **receipt** is the authority -- the admission
    that created it wrote the receipt beside the working database and sealed it -- and a review of an
    admitted candidate reads the namespace that candidate actually holds.

    A candidate with **no** receipt beside its database is a dataset this surface was handed directly
    rather than one an admission produced (a fixture, a comparison a caller assembled from two
    named files). For that shape the requested repository *is* the available identity and is read as
    it always was, because the alternative -- refusing every caller-assembled pair -- would break the
    comparison contract for inputs that were never candidates.

    A receipt that **exists but cannot be read** is a different fact and is refused: something wrote
    a receipt here and it does not say which namespace this dataset belongs to, so standing in the
    caller's word for the dataset's own record is exactly how a review comes to read a namespace
    nothing admitted.
    """

    receipt_path = candidate_database.parent / CANDIDATE_RECEIPT_NAME
    if not receipt_path.exists():
        return requested
    return read_candidate_receipt(receipt_path).repository_id


def _leaf_contract(
    config: McpRuntimeConfig, repository_id: str, master: str, leaf_id: str
) -> WorktreeContract | None:
    """The one enclosure contract recorded for this leaf, or ``None`` when none is readable."""

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


def read_knowledge_review(
    config: McpRuntimeConfig,
    request: ReviewSurfaceRequest,
    records: ReviewRecordInputs = EMPTY_REVIEW_RECORDS,
    *,
    previous_binding_digest: str | None = None,
    probe: TreeDifferenceProbe | None = None,
) -> KnowledgeReviewResult:
    """Resolve the candidate the task context names, then render its review.

    ``previous_binding_digest`` is the comparison an already-displayed assessment was made against.
    When it disagrees with the comparison rendered now, the payload is ``stale``: the previous
    comparison is retained as a *labelled previous input* and submission is disabled against it, so
    a judgement made about inputs that have since moved is never re-presented as a review of what is
    there now.
    """

    resolved = resolve_review_candidate(
        config, request.repository_id, request.master, request.leaf_id
    )
    if isinstance(resolved, ReviewRefusal):
        return _refused(request.repository_id, resolved)
    return compose_review(
        resolved,
        request,
        records,
        previous_binding_digest=previous_binding_digest,
        probe=probe,
    )


def list_knowledge_review_entries(
    config: McpRuntimeConfig,
    repository_id: str,
    master: str,
    leaf_id: str,
    *,
    probe: TreeDifferenceProbe | None = None,
) -> ReviewEntryListResult:
    """The subjects the resolved pair can be reviewed on, or the one refusal that says why not.

    This is the *entry* half of the surface, and it exists because the reviewed subject is the one
    input a reader cannot supply from the task view: the subject is a recorded identity inside the
    candidate, and the browser must not choose the candidate. The resolution is the same one
    :func:`read_knowledge_review` performs -- canonical task context only, one contract, one derived
    root -- so the list a caller is offered and the review it then opens cannot disagree about which
    datasets are being compared.

    A subject is offered exactly when the **shipped comparison** reaches it: the candidate's own
    recorded invariant and family identities are each compared through
    :func:`~agents_remember.application.knowledge_diff.diff_knowledge_scope`, and only the ones the
    operation answers with a page are listed. Nothing is recorded to make that true and no ranking
    is applied here -- a candidate that records no identity the pair can compare yields an empty
    list, which the caller renders as no entry rather than as an invitation to name one.
    """

    resolved = resolve_review_candidate(config, repository_id, master, leaf_id)
    if isinstance(resolved, ReviewRefusal):
        return _entry_refused(repository_id, master, leaf_id, resolved)
    absent = missing_dataset_half(resolved)
    if absent is not None:
        half, database = absent
        return _entry_refused(
            repository_id,
            master,
            leaf_id,
            refusal(
                "candidate_dataset_absent",
                (
                    f"the resolved {half} dataset is absent, so the pair has nothing to compare; "
                    "the review reads neither of its two halves out of the live coordination tree "
                    "and substitutes no other dataset"
                ),
                next_action=(
                    "author the candidate's knowledge in the leaf's disposable knowledge root, and "
                    "place the dataset it forks from in the baseline half if this leaf has one; the "
                    "surface substitutes no other dataset"
                ),
                offending_input=database.name,
            ),
        )
    try:
        entries = _reviewable_entries(resolved, probe=probe)
    except KnowledgeStorageError as error:
        return _entry_refused(
            repository_id,
            master,
            leaf_id,
            refusal(
                "candidate_dataset_absent",
                f"the resolved candidate could not be opened for review: {error}",
                next_action=(
                    "repair the candidate's receipt and dataset in the leaf's disposable knowledge "
                    "root, then reopen the review; the surface substitutes no other dataset"
                ),
                offending_input=resolved.candidate_database.parent.name,
            ),
        )
    return ReviewEntryListResult(
        state="entries",
        repository_id=repository_id,
        master=master,
        leaf_id=resolved.leaf_id,
        entries=entries,
    )


def _reviewable_entries(
    resolved: ReviewCandidateResolution, *, probe: TreeDifferenceProbe | None
) -> tuple[ReviewEntry, ...]:
    """Every identity the shipped comparison reaches on this pair, as the entry list's own values.

    The namespace is read once and threaded into every comparison, so one entry read cannot compare
    its subjects under two different namespaces: the recorded one is what the list and the review it
    opens both use.
    """

    namespace = review_namespace(resolved.repository_id, resolved.candidate_database)
    store = open_existing_knowledge_store(resolved.candidate_database, namespace)
    try:
        recorded = _recorded_identities(store)
    finally:
        store.close()
    entries: list[ReviewEntry] = []
    for kind, identity_id, label in recorded:
        selected = _selected_item_count(
            resolved, kind, identity_id, probe=probe, namespace=namespace
        )
        if selected is None:
            continue
        entries.append(
            ReviewEntry(
                selector_kind=kind,
                selector_id=identity_id,
                label=label,
                selected_item_count=selected,
            )
        )
    return tuple(entries)


def _recorded_identities(
    store: OpenedKnowledgeStore,
) -> tuple[tuple[ReviewSubjectKind, str, str], ...]:
    """Every reviewable identity one candidate records, invariants before families.

    Read through the store's own two list operations rather than through a query written here, so
    the identities this list offers are the ones the namespace records and not the ones a second
    reader of the same tables believes it finds.
    """

    invariants: tuple[tuple[ReviewSubjectKind, str, str], ...] = tuple(
        ("invariant", invariant.invariant_id, invariant.display_label)
        for invariant in store.list_invariants()
    )
    families: tuple[tuple[ReviewSubjectKind, str, str], ...] = tuple(
        ("family", family.family_id, family.display_label) for family in store.list_families()
    )
    return invariants + families


def _selected_item_count(
    resolved: ReviewCandidateResolution,
    subject_kind: ReviewSubjectKind,
    selector_id: str,
    *,
    probe: TreeDifferenceProbe | None,
    namespace: str | None = None,
) -> int | None:
    """How many items the pair's comparison reached for one subject, or ``None`` when it refused.

    A refusal is not a zero: a subject the comparison could not answer for is absent from the list
    rather than offered with a count this reader made up, because an entry that opens a refusal is
    worse than no entry at all.
    """

    selector: KnowledgeReadSeed = (
        InvariantIdentitySeed(invariant_id=selector_id)
        if subject_kind == "invariant"
        else FamilyIdentitySeed(family_id=selector_id)
    )
    comparison = _compare(resolved, selector, probe=probe, namespace=namespace)
    page = comparison.page
    if comparison.state != "page" or page is None or comparison.binding is None:
        return None
    return page.counts.items_total


def _entry_refused(
    repository_id: str, master: str, leaf_id: str, refusal_value: ReviewRefusal
) -> ReviewEntryListResult:
    """One refused entry read, carrying the resolution's own refusal verbatim."""

    return ReviewEntryListResult(
        state="refused",
        repository_id=repository_id,
        master=master,
        leaf_id=leaf_id,
        refusal=refusal_value,
    )


def compose_review(
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    records: ReviewRecordInputs = EMPTY_REVIEW_RECORDS,
    *,
    previous_binding_digest: str | None = None,
    probe: TreeDifferenceProbe | None = None,
) -> KnowledgeReviewResult:
    """Render one review over two already-resolved datasets. Selects nothing; calls the operations."""

    absent = missing_dataset_half(resolved)
    if absent is not None:
        half, database = absent
        return _refused(
            request.repository_id,
            refusal(
                "candidate_dataset_absent",
                f"the resolved {half} dataset is absent, so there is nothing to compare",
                next_action=(
                    "author the candidate's knowledge in the leaf's disposable knowledge root, and "
                    "place the dataset it forks from in the baseline half if this leaf has one; the "
                    "surface substitutes no other dataset"
                ),
                offending_input=database.name,
            ),
        )
    try:
        namespace = review_namespace(resolved.repository_id, resolved.candidate_database)
    except KnowledgeStorageError as error:
        return _refused(
            request.repository_id,
            refusal(
                "candidate_dataset_absent",
                f"the resolved candidate could not be opened for review: {error}",
                next_action=(
                    "repair the candidate's receipt and dataset in the leaf's disposable knowledge "
                    "root, then reopen the review; the surface substitutes no other dataset"
                ),
                offending_input=resolved.candidate_database.parent.name,
            ),
        )

    comparison = _compare(resolved, request.selector, probe=probe, namespace=namespace)
    page = comparison.page
    if comparison.state != "page" or page is None or comparison.binding is None:
        return _refused(
            request.repository_id,
            _comparison_refusal(comparison, request),
        )

    matrix = read_knowledge_view(
        resolved.candidate_database,
        open_diff_side(
            resolved.candidate_database,
            namespace,
            repository_root=resolved.candidate_code_root,
            code_tree_id=resolved.candidate_code_tree_id,
        ),
        ViewRequest(
            view="review_matrix",
            repository_id=namespace,
            record_kinds=REVIEW_MATRIX_KINDS,
        ),
    )
    if matrix.state != "view" or matrix.payload is None:
        detail = matrix.refusal.detail if matrix.refusal is not None else "no rows were returned"
        return _refused(
            request.repository_id,
            refusal(
                "comparison_refused",
                f"the review-matrix view refused the candidate: {detail}",
                next_action="repair the candidate dataset, then reopen the review",
            ),
        )
    rows: tuple[ReviewMatrixRow, ...] = tuple(getattr(matrix.payload, "rows", ()))

    identity = _comparison_identity(comparison)
    subjects = _subject_states(records)
    stale = (
        previous_binding_digest is not None and previous_binding_digest != identity.binding_digest
    )
    return KnowledgeReviewResult(
        state="review",
        repository_id=request.repository_id,
        payload=KnowledgeReviewPayload(
            candidate=ReviewCandidateRef(
                repository_id=request.repository_id,
                master=request.master,
                leaf_id=resolved.leaf_id,
                task_ref=request.master,
            ),
            comparison=identity,
            knowledge=_knowledge_pane(comparison, rows, records, subjects, request.selector),
            source=_source_pane(comparison),
            evidence=_evidence_pane(rows, records, subjects),
            staleness=_staleness(identity, previous_binding_digest),
            submission=_submission(stale),
            limitations=_limitations(comparison),
        ),
    )


def _compare(
    resolved: ReviewCandidateResolution,
    selector: KnowledgeReadSeed,
    *,
    probe: TreeDifferenceProbe | None,
    namespace: str | None = None,
) -> KnowledgeDiffResult:
    """Run the shipped comparison over the two resolved datasets, adding no side and no selector.

    The namespace the two sides are opened under is the candidate's **own recorded** one
    (:func:`review_namespace`), not the repository name the request carried: the datasets are bound
    to an id, and a side opened under the requested spelling refuses against its own binding.
    """

    if namespace is None:
        namespace = review_namespace(resolved.repository_id, resolved.candidate_database)
    return diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=selector,
            before=KnowledgeDiffSide(
                context=open_diff_side(
                    resolved.baseline_database,
                    namespace,
                    repository_root=resolved.baseline_code_root,
                    code_tree_id=resolved.baseline_code_tree_id,
                )
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(
                    resolved.candidate_database,
                    namespace,
                    repository_root=resolved.candidate_code_root,
                    code_tree_id=resolved.candidate_code_tree_id,
                )
            ),
        ),
        before_path=resolved.baseline_database,
        after_path=resolved.candidate_database,
        probe=probe,
    )


def _comparison_refusal(
    comparison: KnowledgeDiffResult, request: ReviewSurfaceRequest
) -> ReviewRefusal:
    """One refused comparison, as the surface's own named refusal."""

    shipped = comparison.refusal
    return refusal(
        "comparison_refused",
        (
            "the comparison operation returned no page for the requested subjects"
            if shipped is None
            else f"{shipped.code}: {shipped.detail}"
        ),
        next_action=(
            "select a subject both snapshots record, then reopen the review"
            if shipped is None
            else shipped.next_action
        ),
        offending_input=request.selector.kind,
    )


def _comparison_identity(comparison: KnowledgeDiffResult) -> ComparisonIdentity:
    """The comparison's own declared identity, carried verbatim and never recomputed."""

    binding = comparison.binding
    digest = comparison.binding_digest
    selector_digest = comparison.selector_digest
    assert binding is not None and digest is not None and selector_digest is not None
    return ComparisonIdentity(
        reference=digest,
        policy_version=comparison.policy_version,
        binding_digest=digest,
        selector_digest=selector_digest,
        before_snapshot_digest=binding.before.logical_digest,
        after_snapshot_digest=binding.after.logical_digest,
        before_code_tree_id=binding.before_code_tree_id,
        after_code_tree_id=binding.after_code_tree_id,
    )


def _limitations(comparison: KnowledgeDiffResult) -> tuple[str, ...]:
    """The comparison's declared limits and its counted omissions, carried as facts."""

    return tuple(
        [
            *(f"limitation:{limit}" for limit in comparison.limitations),
            *(
                f"omitted:{omission.reason}:{omission.omitted_count}"
                for omission in comparison.omissions
            ),
            *(
                f"side_absence:{absence.side}:{absence.code}"
                for absence in comparison.side_absences
            ),
        ]
    )


def _staleness(
    identity: ComparisonIdentity, previous_binding_digest: str | None
) -> ReviewStaleness:
    """Whether the comparison rendered now is still the one an assessment was made against."""

    if previous_binding_digest is None or previous_binding_digest == identity.binding_digest:
        return ReviewStaleness(
            state="current",
            statement="the displayed comparison is the candidate's current comparison",
        )
    return ReviewStaleness(
        state="stale",
        statement="Candidate changed — open a new comparison",
        previous_comparison_ref=previous_binding_digest,
        moved=("comparison-binding",),
    )


def _submission(stale: bool) -> ReviewSubmission:
    """Whether an assessment may be submitted, and through what.

    This increment ships no serving route that publishes an assessment, so the surface is
    display-only and says so. It grows no private write path to compensate: the published
    dispositions are the existing authority's own vocabulary, and the next action names that
    authority rather than a control this surface invented.
    """

    if stale:
        return ReviewSubmission(
            state="disabled_stale",
            reason="Candidate changed — open a new comparison",
            next_action="open a new comparison against the candidate's current inputs",
            proposed_dispositions=PROPOSED_ASSESSMENT_DISPOSITIONS,
        )
    return ReviewSubmission(
        state="unavailable",
        reason=(
            "this increment mounts no serving route that publishes an assessment, so the surface "
            "displays only and does not grow a private write path to compensate"
        ),
        next_action=(
            "publish an assessment through the existing curator authority's publication action, "
            "which supplies the author, the role and the authority provenance"
        ),
        proposed_dispositions=PROPOSED_ASSESSMENT_DISPOSITIONS,
    )


# -- the three panes --------------------------------------------------------------------------


def _knowledge_pane(
    comparison: KnowledgeDiffResult,
    rows: Sequence[ReviewMatrixRow],
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
    selector: KnowledgeReadSeed,
) -> ReviewKnowledgePane:
    """Pane 1: identities, retained revisions, exact statements and separately authored records."""

    assert comparison.page is not None
    items = comparison.page.items
    identity = _identity_item(items, selector)
    return ReviewKnowledgePane(
        invariant_ids=_identity_ids(items, "invariant"),
        family_ids=_identity_ids(items, "family"),
        before_statement=_side_content(identity, "before"),
        after_statement=_side_content(identity, "after"),
        before_conditions=_conditions(identity, "before"),
        after_conditions=_conditions(identity, "after"),
        revision_groups=_revision_groups(comparison),
        field_changes=_field_changes(items),
        authored_effects=tuple(
            _authored_effect(row)
            for row in rows
            if row.subject.record_kind in AUTHORED_EFFECT_KINDS
        ),
        signals=tuple(_signal(signal) for signal in records.signals),
        assessments=_assessment_displays(records, subjects),
        unresolved=tuple(
            _unresolved_author(row)
            for row in rows
            if row.subject.record_kind in AUTHORED_EFFECT_KINDS
        ),
    )


def _source_pane(comparison: KnowledgeDiffResult) -> ReviewSourcePane:
    """Pane 2: the selected locations under registered claims, and what the selection did not reach."""

    assert comparison.page is not None
    items = comparison.page.items
    counts = comparison.page.counts
    expansion = comparison.expansion
    outside = tuple(item for item in items if item.coverage == "present_outside_selection")
    return ReviewSourcePane(
        locations=tuple(
            location for location in (_location(item) for item in items) if location is not None
        ),
        remaining=(
            ReviewRemainingCount(name="locations_remaining", value=counts.items_remaining),
            ReviewRemainingCount(
                name="changed_paths_outside_selection",
                value=(
                    None
                    if expansion is None
                    else len(expansion.attributed_changed_paths)
                    + len(expansion.unattributed_changed_paths)
                ),
                reason=(
                    None
                    if expansion is not None
                    else "the comparison published no source expansion for this selection"
                ),
            ),
            ReviewRemainingCount(
                name="unattributed_changed_paths",
                value=None if expansion is None else len(expansion.unattributed_changed_paths),
                reason=(
                    None
                    if expansion is not None
                    else (
                        "the comparison made no tree observation, so no path is reported as "
                        "attributed or unattributed"
                    )
                ),
            ),
            ReviewRemainingCount(name="records_present_outside_selection", value=len(outside)),
            ReviewRemainingCount(name="references_unresolved", value=counts.suppressed_total),
        ),
        expansion_reference=None if expansion is None else expansion.reference,
        expansion_command=None if expansion is None else expansion.command,
        unattributed_changed_paths=()
        if expansion is None
        else expansion.unattributed_changed_paths,
        attributed_changed_paths=() if expansion is None else expansion.attributed_changed_paths,
        unresolved=tuple(
            ReviewUnresolvedReference(
                field="attribution",
                recorded_reference=item.item_id,
                detail=(
                    "this record is held by one snapshot and was not reached by the other side's "
                    "declared selection; it is displayed as present outside the selection and "
                    "never as a deletion"
                ),
            )
            for item in outside
        ),
    )


def _evidence_pane(
    rows: Sequence[ReviewMatrixRow],
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
) -> ReviewEvidencePane:
    """Pane 3: evidence references, execution observations and the authored assessments."""

    links = tuple(
        ReviewEvidenceLink(
            claim_id=row.subject.record_id,
            revision_id=row.subject.revision_id,
            assessment_refs=row.assessment_ids,
            unresolved=(
                ReviewUnresolvedReference(
                    field="coverage",
                    recorded_reference=row.subject.record_id,
                    detail=(
                        "the review matrix publishes this claim's identity, lifecycle and "
                        "assessment references; it publishes no coverage or limitations for it, so "
                        "they are displayed as unresolved rather than reported as absent"
                    ),
                ),
            ),
        )
        for row in rows
        if row.subject.record_kind == "evidence_claim"
    )
    observations = tuple(_observation(entry) for entry in records.observations)
    assessments = _assessment_displays(records, subjects)
    return ReviewEvidencePane(
        evidence_state="recorded" if links or observations else "none_recorded",
        assessment_state="assessed" if assessments else "unassessed",
        evidence_links=links,
        observations=observations,
        assessments=assessments,
        source_inspection_available=True,
    )


# -- record rendering -------------------------------------------------------------------------


def _subject_states(records: ReviewRecordInputs) -> Mapping[str, SubjectAssessmentState]:
    """Every stored assessment projected per subject, with currentness left as measured.

    A caller that supplied no ``current`` measurement gets every assessment reported ``stale``:
    the shipped projection refuses to promote an unmeasured assessment to current, and this surface
    does not improve on that by guessing.
    """

    grouped: dict[str, list[ReviewAssessment]] = {}
    for assessment in records.assessments:
        grouped.setdefault(assessment.subject.recordId, []).append(assessment)
    states: dict[str, SubjectAssessmentState] = {}
    for subject_id, stored in grouped.items():
        stale_ids = (
            ()
            if records.current is not None
            else tuple(assessment.assessmentId for assessment in stored)
        )
        states[subject_id] = assessment_state_for(stored, stale_ids=stale_ids)
    return states


def _assessment_displays(
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
) -> tuple[ReviewAssessmentDisplay, ...]:
    """The authored assessments as displayed, each with its own binding status."""

    by_id = {assessment.assessmentId: assessment for assessment in records.assessments}
    displayed: list[ReviewAssessmentDisplay] = []
    seen: set[str] = set()
    for state in subjects.values():
        for entry in state.assessments:
            record = by_id.get(entry.assessmentId)
            if record is None or entry.assessmentId in seen:
                continue
            seen.add(entry.assessmentId)
            displayed.append(_assessment_display(record, entry.currentness))
    return tuple(displayed)


def _assessment_display(record: ReviewAssessment, currentness: str) -> ReviewAssessmentDisplay:
    """One stored assessment as the pane's own display value."""

    return ReviewAssessmentDisplay(
        assessment_id=record.assessmentId,
        disposition=record.disposition,
        finding=record.finding or record.rationale,
        rationale=record.rationale,
        author_ref=record.provenance.authorRef,
        role_ref=record.provenance.authorRole,
        examined_inputs=tuple(f"{kind}:{name}" for kind, name in record.examinedInputs.identities)
        or (record.comparisonRef,),
        binding_state=currentness,
        evidence_refs=tuple(reference.spelling for reference in record.evidenceRefs),
    )


def _observation(entry: VerificationObservationPayload) -> ReviewObservation:
    """One verification observation displayed exactly, with its authored limitations."""

    artifact = entry.result_artifact
    return ReviewObservation(
        observation_id=entry.command_name,
        tested_candidate=(
            None if entry.knowledge_candidate is None else entry.knowledge_candidate.logical_digest
        ),
        command_identity=entry.command_identity,
        result_artifact_ref=None if artifact is None else artifact.path,
        result_artifact_digest=None if artifact is None else artifact.sha256,
        execution_result=entry.execution_result,
        environment_identity=f"{entry.environment.host}/{entry.environment.interpreter}",
        # A verification observation carries no authored limitation field of its own; the pane
        # therefore adds none rather than inventing one, and the surface's own limitation list
        # states that no sufficiency claim is made from a result.
        limitations=(),
    )


def _identity_item(
    items: Sequence[KnowledgeDiffItem], selector: KnowledgeReadSeed
) -> KnowledgeDiffItem | None:
    """The **reviewed** subject's identity item, chosen by the selector that named it.

    A comparison over an identity selector selects every retained revision reachable from that
    identity, so one page can hold several identity items -- siblings the selection reached as well
    as the subject the review was opened for. The pane is about the subject, which is the item whose
    own record id is the one the caller's selector named; a sibling's item is never substituted for
    it, and a selector that names no identity addresses no identity item at all.
    """

    wanted = _selector_record_id(selector)
    if wanted is None:
        return None
    candidates = [
        item
        for item in items
        if item.record_id == wanted and (item.before is not None or item.after is not None)
    ]
    # A subject the comparison holds on both sides is preferred, because that is the item whose two
    # operands are the reviewed pair. An item present on one side only is the honest answer when the
    # subject itself was added or removed, and it is used only then.
    for item in candidates:
        if item.before is not None and item.after is not None:
            return item
    return candidates[0] if candidates else None


def _selector_record_id(selector: KnowledgeReadSeed) -> str | None:
    """The record id an identity selector names, or ``None`` for any other seed kind."""

    kind = getattr(selector, "kind", None)
    if kind == "invariant":
        return str(selector.invariant_id)  # type: ignore[attr-defined]
    if kind == "family":
        return str(selector.family_id)  # type: ignore[attr-defined]
    return None


def _identity_ids(items: Sequence[KnowledgeDiffItem], kind: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item.record_id)
            for item in items
            if item.kind == kind and item.record_id is not None
        )
    )


def _side_content(item: KnowledgeDiffItem | None, side: str) -> ReviewSideContent:
    read_item = _read_side(item, side)
    if read_item is None:
        return ReviewSideContent(
            state="absent",
            language="text",
            detail=f"the {side} snapshot selected no record for the reviewed subject",
        )
    if read_item.statement is None:
        return ReviewSideContent(
            state="unresolved",
            language="text",
            detail=(
                f"the {side} snapshot holds the record but published no statement for it; the "
                "operand is unresolved rather than an empty statement"
            ),
        )
    return ReviewSideContent(
        state="present",
        text=read_item.statement,
        language="text",
        detail=f"the {side} snapshot's recorded statement for this revision",
    )


def _conditions(item: KnowledgeDiffItem | None, side: str) -> tuple[str, ...]:
    read_item = _read_side(item, side)
    return () if read_item is None else tuple(read_item.essential_conditions)


def _read_side(item: KnowledgeDiffItem | None, side: str) -> ReadItem | None:
    if item is None:
        return None
    return item.before if side == "before" else item.after


def _revision_groups(comparison: KnowledgeDiffResult) -> tuple[ReviewRevisionGroup, ...]:
    groups = comparison.revision_groups
    return tuple(
        [
            ReviewRevisionGroup(
                side="before",
                record_id=group.record_id,
                selected_revision_count=group.selected_revision_count,
            )
            for group in groups.before
        ]
        + [
            ReviewRevisionGroup(
                side="after",
                record_id=group.record_id,
                selected_revision_count=group.selected_revision_count,
            )
            for group in groups.after
        ]
    )


def _field_changes(items: Sequence[KnowledgeDiffItem]) -> tuple[ReviewFieldChange, ...]:
    return tuple(
        ReviewFieldChange(
            item_id=item.item_id,
            item_kind=item.kind,
            field=name,
            before_value=_field_text(item.before, name),
            after_value=_field_text(item.after, name),
        )
        for item in items
        for name in item.changed_fields
    )


def _field_text(item: ReadItem | None, name: str) -> str | None:
    if item is None:
        return None
    value = getattr(item, name, None)
    if value is None:
        return None
    if isinstance(value, tuple):
        return "; ".join(str(part) for part in value)
    if isinstance(value, dict):
        return None
    return str(value)


def _authored_effect(row: ReviewMatrixRow) -> ReviewAuthoredEffect:
    """One authored effect, preservation claim or unresolved question, as recorded."""

    return ReviewAuthoredEffect(
        record_kind=row.subject.record_kind,  # type: ignore[arg-type]
        record_id=row.subject.record_id,
        revision_id=row.subject.revision_id,
        label=row.provenance.provenance_class,
        rationale=None if row.consequence is None else row.consequence.detail,
        author_ref=None,
        examined_inputs=tuple(row.record_ids),
    )


def _unresolved_author(row: ReviewMatrixRow) -> ReviewUnresolvedReference:
    return ReviewUnresolvedReference(
        field="author",
        recorded_reference=row.subject.record_id,
        detail=(
            "the review matrix publishes this record's identity and classification but no author "
            "for it, so the attribution is displayed as unresolved rather than rendered "
            "anonymously or filled with the current actor"
        ),
    )


def _signal(signal: DetectionSignalPayload) -> ReviewSignal:
    """One detection fact, carried with its inputs, versions and scope limitations only."""

    return ReviewSignal(
        signal_id=signal.signal_id,
        condition=signal.condition,
        input_set=signal.input_set.declared,
        detected_at=signal.governing_route_id,
        relationship_paths=tuple(
            f"{path.path_id}:{path.reached_item_id}" for path in signal.relationship_paths
        ),
        extractor_version=signal.extractor_version,
        policy_version=signal.policy_version,
        scope_limitations=tuple(signal.limitations),
    )


def _location(item: KnowledgeDiffItem) -> ReviewSourceLocation | None:
    """One selected source location with its recorded role and its own change state."""

    if item.kind != _REALIZATION_ITEM_KIND:
        return None
    claim = _realization_read_item(item)
    if claim is None:
        return None
    anchor = claim.anchor
    return ReviewSourceLocation(
        claim_id=claim.claim_id or item.item_id,
        invariant_revision_id=claim.invariant_revision_id,
        path="" if anchor is None else anchor.path,
        role=claim.role,
        rationale=claim.rationale,
        recorded_source_identity=(
            item.item_id if anchor is None else anchor.recorded_source_identity
        ),
        observed_source_identity=None if anchor is None else anchor.observed_source_identity,
        resolution="unsupported_locator" if anchor is None else anchor.resolution,
        change_state=_change_state(item),
        before_only=item.before is not None and item.after is None,
        reached_via=tuple(item.reached_via),
    )


def _realization_read_item(item: KnowledgeDiffItem) -> ReadItem | None:
    for candidate in (item.after, item.before):
        if candidate is not None and candidate.kind == _REALIZATION_READ_KIND:
            return candidate
    return None


def _change_state(item: KnowledgeDiffItem) -> Literal["changed", "unchanged", "not_selected"]:
    change = item.source_change
    if change is None:
        return "not_selected"
    if change.source_observation_changed or change.source_change_only:
        return "changed"
    return "unchanged"


# -- refusals ---------------------------------------------------------------------------------


def _refused(repository_id: str, refusal_value: ReviewRefusal) -> KnowledgeReviewResult:
    return KnowledgeReviewResult(
        state="refused", repository_id=repository_id, refusal=refusal_value
    )


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


def review_records_for(
    config: McpRuntimeConfig, request: ReviewSurfaceRequest
) -> ReviewRecordInputs:
    """The published assessments for this candidate, as the renderer's own input.

    The collection is read from the **curator authority's own publication**, through the shipped
    loader rather than a second reader of the same bytes, and an absent or unreadable authority is
    an empty collection and not an error: a candidate with no published assessment is a candidate
    whose subjects are displayed ``unassessed``, which is a state the surface must be able to show
    truthfully rather than a failure it should hide behind a refusal.

    No ``current`` measurement is supplied. The shipped projection reports an unmeasured assessment
    ``stale`` rather than promoting it to current, and this surface does not improve on that by
    guessing which dependencies still match.
    """

    resolved = resolve_review_candidate(
        config, request.repository_id, request.master, request.leaf_id
    )
    if isinstance(resolved, ReviewRefusal) or resolved.contract is None:
        return EMPTY_REVIEW_RECORDS
    try:
        validated = load_curator_coherence_authority(resolved.contract)
    except (CuratorCoherenceError, OSError, ValueError):
        return EMPTY_REVIEW_RECORDS
    return ReviewRecordInputs(assessments=tuple(validated.record.assessments))
