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

**The endpoints are bound next door, and this adapter delegates to them.**
:mod:`agents_remember.application.review_candidate_resolution` owns the resolution: the recorded
task base commit on one side, the captured add-all candidate tree on the other, the recheck that
refuses by name when either moved, and the refusals every failure earns. It is a separate module
because resolution is a responsibility of its own and because this adapter is at the repository's
file-size rail; ``review_candidate_resolution`` is the one implementation, and the names re-exported
below are that module's -- there is no second resolution path here.

**Five more responsibilities this adapter hands to their own modules, for the same reason.**
:mod:`agents_remember.application.review_source_inventory` measures the exact source-change inventory
of the bound pair and renders the source pane; :mod:`agents_remember.application.review_record_rendering`
renders the record collections the caller supplied into the evidence and submission values;
:mod:`agents_remember.application.review_statement_sides` projects one comparison item's recorded
content into the pane's statement sides and mechanical field rows, where ICR-R06's one-sided contract
lives; :mod:`agents_remember.application.review_revision_comparison` selects which retained
revisions those sides render -- the before head and the after head from the snapshots' own
authored successor relationships, with explicit ambiguity when no unique head exists, which is
ICR-R07's explicit revision comparison; :mod:`agents_remember.application.review_subject_catalogue`
enumerates the entry's labelled subject catalogue from both snapshots' own identity tables, with
totals and per-row presence, comparing no subject to earn its row (ICR-R09); and
:mod:`agents_remember.application.review_task_context` composes the entry that needs no
selected subject. Each is one responsibility with one implementation, and every name an importer
referenced is re-exported below so no importer had to learn a new home -- the statement-side helpers
are the one move that leaves no alias, because they were private to this adapter and no module under
``mcp/`` imported them, the head-selection rule likewise leaves no alias because the
both-sides preference it replaces was private to this adapter, and the entry enumeration leaves
none either: the per-subject compare-to-earn-a-row helpers were private to this adapter, and the
catalogue replaces their mechanism rather than moving it -- this adapter resolves, calls and
assembles, and it grows no feature logic of its own while its file is over the soft rail.

**Every absence is a state.** An unresolvable author, a missing operand, an absent assessment
collection and a comparison the shipped operation refused each produce a named field or a typed
refusal -- never a blank a reader could take for a measured zero, and never a favourable default.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from agents_remember.application.knowledge_before_half import unreadable_half_refusal
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_views import read_knowledge_view
from agents_remember.application.review_candidate_resolution import (
    REVIEW_BASELINE_DIRECTORY,
    REVIEW_CANDIDATE_DIRECTORY,
    REVIEW_CANDIDATE_RELATIVE_ROOT,
    ReviewCandidateResolution,
    candidate_receipt_refusal,
    candidate_ref,
    missing_dataset_half,
    refusal,
    require_current_candidate_identity,
    resolve_review_candidate,
    review_namespace,
    unreadable_candidate_refusal,
)
from agents_remember.application.review_evidence_records import (
    AUTHORED_EFFECT_KINDS,
    review_records_for,
    with_selection_channels,
)
from agents_remember.application.review_record_rendering import (
    EMPTY_REVIEW_RECORDS,
    ReviewRecordInputs,
    assessment_displays,
    evidence_pane,
    refused,
    signal,
    subject_states,
    submission,
)
from agents_remember.application.review_relationship_movement import (
    RelationshipSources,
    relationship_movements,
)
from agents_remember.application.review_revision_comparison import (
    SubjectRevisionSelection,
    select_subject_revisions,
)
from agents_remember.application.review_source_inventory import (
    inventory_limitations,
    review_inventory,
    source_pane,
    source_tree_side,
    tree_difference_observation,
)
from agents_remember.application.review_statement_sides import (
    field_changes,
    side_conditions,
    side_content,
)
from agents_remember.application.review_subject_catalogue import read_subject_catalogue
from agents_remember.application.review_task_context import task_context_review
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.diff_display import TreeDifferenceProbe
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.diff import (
    KnowledgeDiffItem,
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
    SourceAttribution,
)
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review import (
    ComparisonIdentity,
    KnowledgeReviewPayload,
    KnowledgeReviewResult,
    ReviewAuthoredEffect,
    ReviewEntryListResult,
    ReviewKnowledgePane,
    ReviewRefusal,
    ReviewRevisionGroup,
    ReviewSideContent,
    ReviewSourceInventory,
    ReviewStaleness,
    ReviewSurfaceRequest,
    ReviewUnresolvedReference,
)
from agents_remember.models.knowledge.view import ReviewMatrixRow, ViewRequest, ViewResult
from agents_remember.models.lifecycles.review_assessment import SubjectAssessmentState

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

# The record kinds the review matrix is asked for. They are an input to L20's view rather than a
# selection policy of this leaf's: the view applies its own registered traversal over them.
REVIEW_MATRIX_KINDS: tuple[str, ...] = (
    "requirement_revision",
    "invariant_effect_claim",
    "preservation_claim",
    "unresolved_question",
    "evidence_claim",
)


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
        return refused(request.repository_id, resolved)
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
) -> ReviewEntryListResult:
    """The subjects the resolved pair records, or the one refusal that says why not.

    This is the *entry* half of the surface, and it exists because the reviewed subject is the one
    input a reader cannot supply from the task view: the subject is a recorded identity inside the
    candidate, and the browser must not choose the candidate. The resolution is the same one
    :func:`read_knowledge_review` performs -- canonical task context only, one contract, one derived
    root -- so the list a caller is offered and the review it then opens cannot disagree about which
    datasets are being compared.

    The catalogue is enumerated, not compared: every invariant and family identity the pair's
    before/after snapshots record is listed with its label, its presence on each side and the
    labelled totals, and no subject is compared to earn its row. A candidate that records no
    identity yields an empty list, which the caller renders as no entry beside the source
    inventory rather than as an invitation to name one. What one subject's review renders stays
    the comparison's own answer when that subject alone is opened.

    Both refusals that answer for the *pair* are stated before any subject is listed, so an
    unreadable side is a refusal on this route exactly as it is on the composition's: a half that
    is present but cannot be read as a dataset would otherwise raise out of the catalogue read,
    which is a traceback where this surface promises a state naming the side.
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
    unreadable = unreadable_half_refusal(resolved.baseline_database, resolved.candidate_database)
    if unreadable is not None:
        return _entry_refused(repository_id, master, leaf_id, unreadable)
    unreadable_receipt = candidate_receipt_refusal(resolved)
    if unreadable_receipt is not None:
        return _entry_refused(repository_id, master, leaf_id, unreadable_receipt)
    try:
        entries = read_subject_catalogue(resolved)
    except KnowledgeStorageError as error:
        # The preflight above reads the same bytes; this guard exists so that a candidate record which
        # moves between the two reads is still the same typed refusal rather than a traceback.
        return _entry_refused(
            repository_id,
            master,
            leaf_id,
            unreadable_candidate_refusal(resolved, str(error)),
        )
    invariant_total = sum(1 for entry in entries if entry.selector_kind == "invariant")
    return ReviewEntryListResult(
        state="entries",
        repository_id=repository_id,
        master=master,
        leaf_id=resolved.leaf_id,
        entries=entries,
        total_subjects=len(entries),
        invariant_total=invariant_total,
        family_total=len(entries) - invariant_total,
    )


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
    """Render one review over two already-resolved datasets. Selects nothing; calls the operations.

    The **source inventory is measured first and unconditionally**, from the two code trees the
    resolution bound: it is the one half of a review that cannot depend on a knowledge selection, and
    measuring it only after a comparison succeeded is how a task with no invariant -- or with no
    datasets at all -- came to lose its source review entirely.

    A request that names no selector is the **task context** and is answered without a comparison:
    the source pane still carries the complete inventory, and the knowledge pane states that no
    operand was compared instead of rendering an empty one. A request that does name a selector keeps
    the shipped behaviour exactly, including its refusals, because a selected subject that cannot be
    compared is a different fact from a review that selected no subject.
    """

    before_source = source_tree_side(resolved.baseline_code_tree_id, resolved.baseline_code_root)
    after_source = source_tree_side(resolved.candidate_code_tree_id, resolved.candidate_code_root)
    observed = (
        tree_difference_observation(before_source, after_source)
        if probe is None
        else probe(before_source, after_source)
    )
    inventory = review_inventory(before_source, after_source, observed=observed)
    if request.selector is None:
        return task_context_review(resolved, request, records, inventory, observed)
    opened = _open_dataset_pair(resolved, request)
    if isinstance(opened, KnowledgeReviewResult):
        return opened

    comparison = _compare(resolved, request.selector, probe=probe, namespace=opened)
    page = comparison.page
    if comparison.state != "page" or page is None or comparison.binding is None:
        return refused(
            request.repository_id,
            _comparison_refusal(comparison, request),
        )

    matrix = _review_matrix(resolved, opened, request.repository_id)
    if isinstance(matrix, KnowledgeReviewResult):
        return matrix
    rows: tuple[ReviewMatrixRow, ...] = tuple(getattr(matrix.payload, "rows", ()))

    # The two collections that live in the review matrix are added here, where the view's own answer
    # is: a review that read the matrix reports what it returned, and a review that read none says so
    # rather than reporting an absence it never asked about (ICR-R14).
    records = with_selection_channels(
        records, rows, selected=True, rows_remaining=_rows_remaining(matrix)
    )

    # The endpoints are re-derived here, after every read and immediately before the payload is
    # built: a capture input that moved while the comparison ran would otherwise be published as the
    # candidate's own comparison. A moved input is a named refusal, never a substitution.
    moved = require_current_candidate_identity(resolved)
    if moved is not None:
        return refused(request.repository_id, moved)

    identity = _comparison_identity(comparison)
    subjects = subject_states(records)
    stale = (
        previous_binding_digest is not None and previous_binding_digest != identity.binding_digest
    )
    # The reviewed identity's explicit revision selection is made here, from the comparison's
    # own union items and the two snapshots' own authored edges, and the pane renders it: the
    # adapter resolves, calls and assembles, and the head rule lives in its own module.
    selected = select_subject_revisions(
        page.items,
        request.selector,
        repository_id=comparison.repository_id,
        before_database=resolved.baseline_database,
        after_database=resolved.candidate_database,
    )
    # The recorded before/after relationship union is traversed here, from the comparison's own
    # union items and the two snapshots' own authored edges, and the source pane renders both of its
    # views: the addresses one location per selected claim, and the two-sided movement each location
    # belongs to (ICR-R08@v1). The traversal is its own module, called and not re-implemented.
    relationships = relationship_movements(
        page.items,
        RelationshipSources(
            repository_id=comparison.repository_id,
            before_database=resolved.baseline_database,
            after_database=resolved.candidate_database,
            selector=request.selector,
            before_code=before_source,
            after_code=after_source,
        ),
    )
    return KnowledgeReviewResult(
        state="review",
        repository_id=request.repository_id,
        payload=KnowledgeReviewPayload(
            candidate=candidate_ref(
                resolved,
                repository_id=request.repository_id,
                master=request.master,
            ),
            comparison=identity,
            knowledge=_knowledge_pane(comparison, rows, records, subjects, selected),
            source=source_pane(
                comparison, inventory, _comparison_attribution(comparison), relationships
            ),
            evidence=evidence_pane(rows, records, subjects),
            staleness=_staleness(identity, previous_binding_digest),
            submission=submission(stale),
            limitations=_limitations(comparison, inventory),
        ),
    )


def _comparison_attribution(comparison: KnowledgeDiffResult) -> SourceAttribution:
    """The attribution partition the comparison itself measured, carried verbatim.

    It is not recomputed here: the comparison read both snapshots through its own two connections and
    partitioned the population its own source observation measured, so a second measurement in the
    composition could only disagree with the one the payload's expansion already publishes.
    """

    expansion = comparison.expansion
    assert expansion is not None and expansion.attribution is not None
    return expansion.attribution


def _open_dataset_pair(
    resolved: ReviewCandidateResolution, request: ReviewSurfaceRequest
) -> str | KnowledgeReviewResult:
    """Return the namespace the pair opens under, or the named refusal an absent pair earns.

    A selector was named, so the comparison is *between* two datasets and the pair has to be there:
    an absent half and a half whose receipt cannot be read are both ``candidate_dataset_absent``, and
    each names which half and what to do instead. This is deliberately not reached by a task-context
    review, which compares no dataset and therefore has nothing to open.
    """

    unreadable = unreadable_half_refusal(resolved.baseline_database, resolved.candidate_database)
    if unreadable is not None:
        return refused(request.repository_id, unreadable)

    absent = missing_dataset_half(resolved)
    if absent is not None:
        half, database = absent
        return refused(
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
        return review_namespace(resolved.repository_id, resolved.candidate_database)
    except KnowledgeStorageError as error:
        return refused(request.repository_id, unreadable_candidate_refusal(resolved, str(error)))


def _review_matrix(
    resolved: ReviewCandidateResolution, namespace: str, repository_id: str
) -> ViewResult | KnowledgeReviewResult:
    """Read L20's review matrix for the candidate, or the refusal the view earns."""

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
    if matrix.state == "view" and matrix.payload is not None:
        return matrix
    detail = matrix.refusal.detail if matrix.refusal is not None else "no rows were returned"
    return refused(
        repository_id,
        refusal(
            "comparison_refused",
            f"the review-matrix view refused the candidate: {detail}",
            next_action="repair the candidate dataset, then reopen the review",
        ),
    )


def _rows_remaining(result: ViewResult) -> int:
    """How many rows the matrix view declared beyond the page it returned, or none.

    The view's own count is the authority, so a review that rendered a bounded page reports the bound
    instead of presenting the page it read as the whole selection (ICR-R14).
    """

    payload = result.payload
    if payload is None:  # pragma: no cover - a served view always carries its payload
        return 0
    return int(payload.counts.rows_remaining.value or 0)


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


def _selector_kind_or_absence(selector: KnowledgeReadSeed | None) -> str:
    """Return the selector kind a refusal names, or the absence itself when there is no selector.

    A request that named no subject never reaches a comparison refusal -- the task-context
    composition answers it first -- so this narrows the optional field at the one place a refusal
    spells it. Reading ``selector.kind`` through would make the value that *explains* a refusal the
    thing that raises, and a request without a selector is a fact worth naming rather than a crash.
    """

    return "no selector" if selector is None else selector.kind


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
        offending_input=_selector_kind_or_absence(request.selector),
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


def _limitations(
    comparison: KnowledgeDiffResult, inventory: ReviewSourceInventory
) -> tuple[str, ...]:
    """The comparison's declared limits and its counted omissions, carried as facts.

    The inventory's own state is declared here as well, because a limit a reader has to open a pane
    to discover is a limit the response did not state: an unavailable measurement and a partial one
    are two different facts and both are named at the top level.
    """

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
            *inventory_limitations(inventory),
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


def _knowledge_pane(
    comparison: KnowledgeDiffResult,
    rows: Sequence[ReviewMatrixRow],
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
    selected: SubjectRevisionSelection | None,
) -> ReviewKnowledgePane:
    """Pane 1: identities, retained revisions, exact statements and separately authored records.

    The two statements are the reviewed identity's selected revisions: the before head's
    statement and the after head's, chosen from the snapshots' own authored successor
    relationships rather than by presence on both sides (ICR-R07@v1). An ambiguous or
    unresolved selection renders no winner: both sides state the explicit ambiguity and the
    recorded selection beside them still lists every head and every retained revision.
    """

    assert comparison.page is not None
    items = comparison.page.items
    if selected is None:
        selected = SubjectRevisionSelection(selection=None, before_item=None, after_item=None)
    before_statement, after_statement, before_conditions, after_conditions = _selected_statements(
        selected
    )
    return ReviewKnowledgePane(
        invariant_ids=_identity_ids(items, "invariant"),
        family_ids=_identity_ids(items, "family"),
        before_statement=before_statement,
        after_statement=after_statement,
        before_conditions=before_conditions,
        after_conditions=after_conditions,
        revision_groups=_revision_groups(comparison),
        revision_selection=selected.selection,
        field_changes=field_changes(items),
        authored_effects=tuple(
            _authored_effect(row)
            for row in rows
            if row.subject.record_kind in AUTHORED_EFFECT_KINDS
        ),
        signals=tuple(signal(entry) for entry in records.signals),
        assessments=assessment_displays(records, subjects),
        unresolved=tuple(
            _unresolved_author(row)
            for row in rows
            if row.subject.record_kind in AUTHORED_EFFECT_KINDS
        ),
    )


def _selected_statements(
    selected: SubjectRevisionSelection,
) -> tuple[ReviewSideContent, ReviewSideContent, tuple[str, ...], tuple[str, ...]]:
    """Render one head selection's two statement sides and their conditions.

    A compared or one-sided selection renders the selected head items' own recorded sides, so a
    known-empty side stays the absent state ICR-R06@v1 already gives it. An ambiguous or
    unresolved selection renders no head's text as the subject's operand: both sides carry the
    explicit statement that says why no pair was chosen, and the recorded selection beside them
    keeps every head and every retained revision visible.
    """

    selection = selected.selection
    if selection is not None and selection.state in ("ambiguous", "unresolved"):
        explicit = ReviewSideContent(
            state="unresolved", language="text", detail=selection.statement
        )
        return (explicit, explicit, (), ())
    return (
        side_content(selected.before_item, "before"),
        side_content(selected.after_item, "after"),
        side_conditions(selected.before_item, "before"),
        side_conditions(selected.after_item, "after"),
    )


def _identity_ids(items: Sequence[KnowledgeDiffItem], kind: str) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item.record_id)
            for item in items
            if item.kind == kind and item.record_id is not None
        )
    )


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
