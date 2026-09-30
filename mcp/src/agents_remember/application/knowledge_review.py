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

**Six more responsibilities this adapter hands to their own modules, for the same reason.**
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
selected subject; and :mod:`agents_remember.application.review_comparison_staleness` carries the
comparison's own declared identity and the staleness that identity earns against the previous
binding a refresh read supplies (ICR-R17). **Two movement readings are delegated the same way, and
they are the two the read renders beside that staleness**: the leaf's own managed syncs
(:mod:`agents_remember.application.review_sync_movement`, ICR-R22) and the identities a *raw* Git
operation replaced with no managed transaction behind it
(:mod:`agents_remember.application.review_external_git_movement`, ICR-R23, which also owns the
transition vocabulary and the support matrix of routes this system does and does not reconcile).
Each is one responsibility with one implementation, the external reading outranks both the carried
identity and a recorded rebinding when it measured a replacement, and
every name an importer
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
    candidate_ref,
    missing_dataset_half,
    refusal,
    require_current_candidate_identity,
    resolve_review_candidate,
    review_namespace,
    unreadable_candidate_refusal,
)
from agents_remember.application.review_change_kinds import with_change_kinds
from agents_remember.application.review_committed_leaf import (
    closed_leaf_dataset_refusal,
    closed_leaf_limitations,
)
from agents_remember.application.review_comparison_staleness import (
    comparison_identity,
    review_staleness,
)
from agents_remember.application.review_evidence_records import (
    AUTHORED_EFFECT_KINDS,
    MatrixSelection,
    review_records_for,
    with_selection_channels,
)
from agents_remember.application.review_external_git_movement import external_git_movement
from agents_remember.application.review_family_context import (
    FAMILY_MEMBERS_COLLECTION,
    FamilyContextSources,
    review_family_context,
)
from agents_remember.application.review_family_rosters import family_collection_refusal
from agents_remember.application.review_pagination import (
    RecordsPagePosition,
    comparison_page,
    comparison_reset,
    records_page,
    records_page_refusal,
    reset_comparison_page,
)
from agents_remember.application.review_pair_preflight import pair_preflight_refusal
from agents_remember.application.review_record_applicability import (
    AppliedRecords,
    review_applicability,
)
from agents_remember.application.review_record_rendering import (
    EMPTY_REVIEW_RECORDS,
    KNOWLEDGE_APPLICABILITY_CLASSES,
    ReviewRecordInputs,
    assessment_displays,
    authored_effects,
    evidence_pane,
    refused,
    signal,
    subject_states,
    submission,
    unresolved_authors,
)
from agents_remember.application.review_recorded_selection import ComparisonFacts
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
from agents_remember.application.review_sync_movement import (
    review_staleness_with_external_movement,
    review_sync_movement,
)
from agents_remember.application.review_task_context import task_context_review
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge.diff_display import TreeDifferenceProbe
from agents_remember.memory.knowledge.refusals import KnowledgeStorageError
from agents_remember.models.knowledge.diff import (
    DIFF_DISPLAY_MAX_ITEMS,
    KnowledgeDiffBudget,
    KnowledgeDiffItem,
    KnowledgeDiffPage,
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
    SourceAttribution,
)
from agents_remember.models.knowledge.read import KnowledgeReadSeed
from agents_remember.models.knowledge.review import (
    KnowledgeReviewPayload,
    KnowledgeReviewResult,
    ReviewCollectionPage,
    ReviewEntryListResult,
    ReviewKnowledgePane,
    ReviewRefusal,
    ReviewRevisionGroup,
    ReviewSideContent,
    ReviewSourceInventory,
    ReviewSurfaceRequest,
)
from agents_remember.models.knowledge.view import (
    MAX_VIEW_ROWS,
    ReviewMatrixRow,
    ViewContinuation,
    ViewRefusal,
    ViewRequest,
    ViewResult,
    rebuild_continuation,
)
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
    probe: TreeDifferenceProbe | None = None,
) -> KnowledgeReviewResult:
    """Resolve the candidate the task context names, then render its review.

    ``request.previous_binding_digest`` is the comparison an already-displayed assessment was made
    against -- the identity a reader was looking at, carried on the read that replaces it (ICR-R17).
    When it disagrees with the comparison rendered now, the payload is ``stale``: the previous
    comparison is retained as a *labelled previous input* and submission is disabled against it, so
    a judgement made about inputs that have since moved is never re-presented as a review of what is
    there now. It travels on the request rather than beside it because there is one spelling of what
    was asked, and the composition below reads it from there.
    """

    resolved = resolve_review_candidate(
        config,
        request.repository_id,
        request.master,
        request.leaf_id,
        recorded=request.history == "recorded",
    )
    if isinstance(resolved, ReviewRefusal):
        return refused(request.repository_id, resolved)
    return compose_review(resolved, request, records, probe=probe)


def list_knowledge_review_entries(
    config: McpRuntimeConfig,
    repository_id: str,
    master: str,
    leaf_id: str,
    *,
    recorded: bool = False,
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

    resolved = resolve_review_candidate(config, repository_id, master, leaf_id, recorded=recorded)
    if isinstance(resolved, ReviewRefusal):
        return _entry_refused(repository_id, master, leaf_id, resolved)
    # The pair's own refusals come first and are one answer, stated before any subject is listed so
    # the catalogue can never answer for a pair nothing could open. The rule and its order are
    # shared with the changed-intent summary (``review_pair_preflight``), so the entry cannot offer
    # a count for a pair its catalogue refuses.
    pair = pair_preflight_refusal(resolved)
    if pair is not None:
        return _entry_refused(repository_id, master, leaf_id, pair)
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

    **A request may continue either bounded collection, and only the one it names** (ICR-R10). The
    knowledge cursor is the comparison's own; the records cursor is the matrix view's own; neither is
    minted, decoded or re-derived here, and a request carries at most one of them. The page the
    response publishes is that collection's own counts and active scope with the owner's cursor for
    whatever lies beyond it, so a remainder is never visible without the way to reach it.
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
    # A closed leaf's pair answers for itself before the shipped preflight does (ICR-R12): a half the
    # record states this leaf never had is a fact about the repository's history and one the record
    # kept and that no longer resolves is unavailable now, and the two are different refusals. A live
    # candidate, and a closed leaf whose recorded halves both resolve, fall through unchanged.
    historical = closed_leaf_dataset_refusal(resolved)
    if historical is not None:
        return refused(request.repository_id, historical)
    opened = _open_dataset_pair(resolved, request)
    if isinstance(opened, KnowledgeReviewResult):
        return opened

    # The knowledge cursor is the comparison's own, so it is offered to the comparison and to nothing
    # else. A cursor that no longer binds the snapshot pair the resolution opened is the
    # moved-generation case: the comparison refuses it, and the composition asks once more for the
    # *first* page of the comparison that is there now rather than answering from the new generation
    # at a position the old one issued.
    cursor = request.continuation if request.page_of == "knowledge" else None
    comparison = _compare(opened, resolved, request, probe=probe, cursor=cursor)
    reset = comparison_reset(comparison, cursor=cursor)
    if reset is not None:
        comparison = _compare(opened, resolved, request, probe=probe, cursor=None)
    page = comparison.page
    if comparison.state != "page" or page is None or comparison.binding is None:
        return refused(
            request.repository_id,
            _comparison_refusal(comparison, request),
        )

    matrix = _review_matrix(resolved, opened, request)
    matrix_refusal = _records_refusal(
        matrix, request.continuation if request.page_of == "records" else None
    )
    page_refusal = (
        matrix_refusal if request.page_of == "records" and matrix_refusal is not None else None
    )
    if isinstance(matrix, ViewRefusal):
        rows: tuple[ReviewMatrixRow, ...] = ()
    else:
        rows = tuple(getattr(matrix[0].payload, "rows", ()))

    # The two collections that live in the review matrix are added here, where the view's own answer
    # is: a review that read the matrix reports what it returned, and a review that read none says so
    # rather than reporting an absence it never asked about (ICR-R14).
    #
    # The page handed to the channels is the one the *payload* publishes, not the raw matrix window:
    # a review that named no collection publishes no page and therefore no cursor, so a channel that
    # reported a remainder beside an unpublished cursor would be claiming a reachable action that is
    # not on the wire. The channel note and the payload therefore read the same value.
    published_page = _collection_page(request, page, comparison, matrix, reset)
    records = with_selection_channels(
        records,
        rows,
        MatrixSelection(
            # The page is handed over only when it is **this collection's** page: a request that paged
            # the knowledge comparison publishes a comparison page whose cursor continues the
            # comparison and reaches no records row, so the note beside the records remainder must not
            # claim it. In that state the note names the request that does reach them
            # (``pageOf=records``) instead.
            page=published_page if request.page_of == "records" else None,
            remaining=_matrix_rows_remaining(matrix),
            unreadable=matrix_refusal,
        ),
    )

    # The endpoints are re-derived here, after every read and immediately before the payload is
    # built: a capture input that moved while the comparison ran would otherwise be published as the
    # candidate's own comparison. A moved input is a named refusal, never a substitution.
    moved = require_current_candidate_identity(resolved)
    if moved is not None:
        return refused(request.repository_id, moved)

    identity = comparison_identity(comparison)
    subjects = subject_states(records)
    # One comparison and one answer about it: the state the payload publishes and the submission
    # state beside it are read from the same rule, so "an assessment is never submitted against a
    # comparison that has moved" cannot be true of one field and false of the other.
    # What this leaf's own managed syncs measured against the generation it published (ICR-R22@v1).
    # The measurement is the rebinding record's, read and checked against the generation by its own
    # owner; a measured movement outranks the reader's carried identity, so a review whose inputs a
    # sync moved never reads as untouched. ``None`` means no measurement is recorded, which is a
    # different fact from a measured agreement and is carried as one.
    sync_movement = review_sync_movement(resolved)
    # What a *raw* Git operation moved under the same generation (ICR-R23@v1). The measurement is the
    # boundary's own -- the reviewed generation's declared identities beside what the repository shows
    # now, taken by the module that owns it -- and a replaced identity outranks both the reader's
    # carried comparison and a recorded rebinding, because neither survives the branch being rewritten
    # under it. ``None`` is "no boundary was measured": a pair with no enclosure, a closed leaf's
    # record, a branch that published nothing. The transition vocabulary, the support matrix and the
    # routes this system does not reconcile live in that module; the adapter calls it and renders it.
    external_movement = external_git_movement(resolved)
    staleness = review_staleness_with_external_movement(
        review_staleness(identity, request.previous_binding_digest),
        external_movement,
        sync_movement,
    )
    stale = staleness.state == "stale"
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
    # The recorded families this selection belongs to, their independently authored guarantees and
    # their complete recorded member rosters are composed here, once, after the relationship union
    # the member contexts reference (ICR-R31@v1). The composition calls the shipped read operation
    # and the family/membership owners; it selects no subject, widens no scope and concludes nothing
    # about a guarantee, and its one bounded collection is continued with the read owner's own cursor.
    family = review_family_context(
        FamilyContextSources(
            repository_id=resolved.repository_id,
            namespace=opened,
            before_database=resolved.baseline_database,
            after_database=resolved.candidate_database,
            selector=request.selector,
            before_code_root=resolved.baseline_code_root,
            after_code_root=resolved.candidate_code_root,
            before_code_tree_id=resolved.baseline_code_tree_id,
            after_code_tree_id=resolved.candidate_code_tree_id,
            movements=relationships,
            page_size=_review_page_size(request),
        ),
        continuation=request.continuation if request.page_of == FAMILY_MEMBERS_COLLECTION else None,
    )
    # The family roster is the third bounded collection and its page is the projection's own: the
    # read owner minted the cursor and measured the counts, so neither is restated here. A request
    # naming that collection without a cursor addressed no single walk -- it is a set of per-family
    # walks -- and earns the collection's own refusal instead of an arbitrary one of them.
    if request.page_of == FAMILY_MEMBERS_COLLECTION:
        published_page = family.page
        page_refusal = family.refusal or (
            None if family.page is not None else family_collection_refusal()
        )
    # Which supplied record may be displayed beside *this* subject, and why, is decided once, here,
    # before either pane renders anything (ICR-R26@v1); the panes receive what it kept.
    applicability = review_applicability(
        resolved,
        request,
        records,
        rows,
        ComparisonFacts(
            items=page.items,
            selected=selected,
            generation=identity,
            relationships=relationships,
        ),
    )
    displayed_rows = applicability.displayed_rows(rows)
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
            knowledge=_knowledge_pane(comparison, displayed_rows, records, subjects, applicability),
            source=source_pane(
                comparison, inventory, _comparison_attribution(comparison), relationships
            ),
            evidence=evidence_pane(displayed_rows, records, subjects, applicability),
            # A tree comparison's families carry the change facts of the members this page
            # returned (MIK-R33); a dataset review's context is published exactly as composed.
            family_context=with_change_kinds(family.context, resolved.trees),
            staleness=staleness,
            submission=submission(stale),
            sync_movement=sync_movement,
            external_git_movement=external_movement,
            page=published_page,
            # A requested page the owner could not serve is stated as the refusal it is: a page value
            # needs the owner's own counts, and inventing zeros for a read that never happened would
            # be the measured-zero lie this composition refuses. The refusal carries the code, the
            # offending cursor and the owner's identities, so the reader gets the action rather than
            # a control claiming there is nothing left to reach.
            page_refusal=page_refusal,
            # The record this review was reopened from, when it was not composed from a live
            # enclosure, is declared beside the comparison's own limits (ICR-R12): which record
            # answered, which generation it was, and whether each intent half was ever recorded.
            # A live candidate contributes no token and this response is byte-identical to the one
            # it always was.
            limitations=(
                *_limitations(comparison, inventory),
                *closed_leaf_limitations(resolved),
            ),
        ),
    )


def _compare(
    opened: str,
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    *,
    probe: TreeDifferenceProbe | None,
    cursor: str | None,
) -> KnowledgeDiffResult:
    """The comparison this request asked for, at the position its own cursor names, or the first page.

    The page size is the caller's when it named one and the comparison's own display budget otherwise,
    so the bound the response publishes is the bound the comparison applied. Nothing else about the
    request reaches the comparison: this adapter adds no side, no selector and no filter.
    """

    return _compare_scope(resolved, request, probe=probe, namespace=opened, continuation=cursor)


def _comparison_attribution(comparison: KnowledgeDiffResult) -> SourceAttribution:
    """The attribution partition the comparison itself measured, carried verbatim.

    It is not recomputed here: the comparison read both snapshots through its own two connections and
    partitioned the population its own source observation measured, so a second measurement in the
    composition could only disagree with the one the payload's expansion already publishes.
    """

    expansion = comparison.expansion
    assert expansion is not None and expansion.attribution is not None
    return expansion.attribution


def _collection_page(
    request: ReviewSurfaceRequest,
    page: KnowledgeDiffPage,
    comparison: KnowledgeDiffResult,
    matrix: tuple[ViewResult, str] | ViewRefusal,
    reset: ReviewRefusal | None,
) -> ReviewCollectionPage | None:
    """The one bounded collection this request named, stated as its own page, or nothing.

    A request that named no collection pages nothing and this returns ``None``: that is a different
    fact from a page with no rows left in it, and the payload keeps them apart. The knowledge page is
    the comparison's own window; the records page is the matrix's own. A reset -- a cursor whose
    comparison moved -- is stated on the page it belongs to, beside the *first* page of the
    comparison that is there now.
    """

    collection = request.page_of
    if collection is None or collection == FAMILY_MEMBERS_COLLECTION:
        # A request that named no collection pages nothing, and the family roster collection is the
        # projection's own page rather than one of this function's two owners.
        return None
    if collection == "knowledge":
        scope = _knowledge_scope(request, comparison)
        if reset is not None:
            return reset_comparison_page(page, reset, collection=collection, scope=scope)
        return comparison_page(
            page, collection=collection, scope=scope, continued_from=request.continuation
        )
    if isinstance(matrix, ViewRefusal):
        # The matrix view refused the page this request named, so there is no window to state and no
        # cursor to publish: a page whose counts nothing measured would print a remainder over a read
        # that never happened. The refusal travels twice, deliberately -- once on the matrix channels
        # (so an unreadable matrix is ``unavailable`` rather than a measured zero) and once as
        # ``page_refusal`` on the payload (so the reader is told which cursor failed and what reaches
        # the collection instead of being shown a control that claims nothing remains).
        return None
    return _records_page(matrix, request)


def _matrix_rows_remaining(matrix: tuple[ViewResult, str] | ViewRefusal) -> int:
    """How many matrix rows the view declared beyond the window it returned, or none.

    It is the owner's own count, read here for the channels rather than derived from a published
    page, because a review that named no collection still read a bounded selection: the count is
    measured either way, and only the *cursor* depends on the page being published.
    """

    if isinstance(matrix, ViewRefusal):
        return 0
    payload = matrix[0].payload
    if payload is None:  # pragma: no cover - a served view always carries its payload
        return 0
    return int(payload.counts.rows_remaining.value or 0)


def _records_page(
    matrix: tuple[ViewResult, str] | ViewRefusal, request: ReviewSurfaceRequest
) -> ReviewCollectionPage | None:
    """The matrix view's own counts as the records collection's page, or nothing when it refused."""

    if isinstance(matrix, ViewRefusal):
        return None
    payload = matrix[0].payload
    if payload is None:  # pragma: no cover - a served view always carries its payload
        return None
    return records_page(
        payload.counts,
        collection="records",
        position=RecordsPagePosition(
            scope=_records_scope(request),
            continuation=None if payload.continuation is None else payload.continuation.token,
            continued_from=request.continuation if request.page_of == "records" else None,
            page_size=_review_page_size(request),
        ),
    )


def _records_refusal(
    matrix: tuple[ViewResult, str] | ViewRefusal, cursor: str | None
) -> ReviewRefusal | None:
    """The refusal a records request earned, in the surface's own vocabulary, or ``None``.

    A matrix the composition asked for and could not read is a state, not an absence: the two
    matrix-sourced collections are reported ``unavailable`` with this refusal's reason and next action
    rather than counted as a measured zero, which is the collapse ``ICR-R14@v1`` keeps apart. When the
    refused thing was a *cursor* whose snapshot pair moved, the refusal carries the new-generation
    action ICR-R10 requires -- the token is not re-resolved against the generation that is there now.
    """

    if not isinstance(matrix, ViewRefusal):
        return None
    return records_page_refusal(
        matrix,
        fallback_next_action="repair the candidate dataset, then reopen the review",
        cursor=cursor,
    )


def _knowledge_scope(
    request: ReviewSurfaceRequest, comparison: KnowledgeDiffResult
) -> tuple[str, ...]:
    """The filters that were **active** for one knowledge page, in the owner's own vocabulary.

    The reviewed subject is the comparison's own selector digest rather than the selector's spelling,
    because the digest is what the cursor binds and two spellings of one subject are one selection.
    The display filter is the comparison's own declared policy, which is empty for this surface: the
    review request carries no display filter, and reporting one it did not apply would be a scope
    statement about a filter nobody set.
    """

    selector = request.selector
    return (
        f"selector_digest={comparison.selector_digest}",
        f"selector_kind={'none' if selector is None else selector.kind}",
        f"display_filter={comparison.policy or 'none'}",
        f"page_size={_knowledge_page_size(request)}",
    )


def _records_scope(request: ReviewSurfaceRequest) -> tuple[str, ...]:
    """The filters that were **active** for one records page, in the owner's own vocabulary.

    The record kinds are the ones this composition asked the view for, which is the selection the
    counts describe; the size is the bound the view applied. Both travel with the page, so a
    ``remaining`` count is never read apart from the selection it is a remainder of.
    """

    return (
        f"record_kinds={','.join(REVIEW_MATRIX_KINDS)}",
        "ordering_input=stable_ordering",
        f"page_size={_review_page_size(request)}",
    )


def _knowledge_page_size(request: ReviewSurfaceRequest) -> int:
    """The comparison window's size: the caller's when it named one, the owner's budget otherwise."""

    return DIFF_DISPLAY_MAX_ITEMS if request.page_size == 0 else request.page_size


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
    resolved: ReviewCandidateResolution, namespace: str, request: ReviewSurfaceRequest
) -> tuple[ViewResult, str] | ViewRefusal:
    """Read L20's review matrix for the candidate, or hand back the view's own refusal.

    The refusal is returned **unmapped** on purpose: the composition is the layer that knows whether a
    refused read is a page cursor whose comparison moved -- a state the surface states with a new
    generation action (ICR-R10) -- or a matrix that genuinely could not be read, and it is the only
    layer that still holds the cursor the refusal is about.
    """

    cursor = _records_cursor(request)
    if isinstance(cursor, ViewRefusal):
        return cursor
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
            limit=_review_page_size(request),
            continuation=cursor,
        ),
    )
    if matrix.state == "view" and matrix.payload is not None:
        return matrix, namespace
    if matrix.refusal is not None:
        return matrix.refusal
    return ViewRefusal(
        code="snapshot_unavailable",
        view="review_matrix",
        detail=(
            "the review-matrix view returned neither a payload nor a refusal, so no row of the "
            "selection can be reported and none is invented"
        ),
        next_action="repair the candidate dataset, then reopen the review",
    )


def _records_cursor(request: ReviewSurfaceRequest) -> ViewContinuation | ViewRefusal | None:
    """The view continuation one request presents for the records collection, or the refusal it earns.

    The token is rebuilt through the shipped codec rather than parsed here: a token this substrate did
    not mint, or one minted for another view's walk, rebuilds nothing and the codec's own refusal is
    what the caller receives. The rebuilt continuation then travels as the *binding* the view checks
    the snapshot against, so a cursor issued for one candidate and presented against another is
    refused by the owner rather than re-resolved here.
    """

    if request.page_of != "records" or request.continuation is None:
        return None
    return rebuild_continuation(request.continuation, view="review_matrix")


def _review_page_size(request: ReviewSurfaceRequest) -> int:
    """The row bound this request asked for, or the view's own default when it named none.

    The size is a request input rather than a constant of this adapter because the bound a response
    publishes has to be the bound its owner applied: a caller that asks for fewer rows and is told
    ``remaining`` from a walk of a different size could not reconcile the two.
    """

    return MAX_VIEW_ROWS if request.page_size == 0 else min(request.page_size, MAX_VIEW_ROWS)


def _compare_scope(
    resolved: ReviewCandidateResolution,
    request: ReviewSurfaceRequest,
    *,
    probe: TreeDifferenceProbe | None,
    namespace: str,
    continuation: str | None,
) -> KnowledgeDiffResult:
    """Run the shipped comparison over the two resolved datasets, adding no side and no selector.

    The namespace the two sides are opened under is the candidate's **own recorded** one
    (:func:`review_namespace`), not the repository name the request carried: the datasets are bound
    to an id, and a side opened under the requested spelling refuses against its own binding.

    ``continuation`` is the comparison's own cursor, carried through untouched: this adapter does not
    mint one, decode one or hold a second position, so the page a caller receives is a function of the
    cursor the comparison's owner issued and of nothing this surface computed.
    """

    selector = request.selector
    assert selector is not None  # a request with no selector is answered before any comparison runs
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
            budget=KnowledgeDiffBudget(max_items=_knowledge_page_size(request)),
            continuation=continuation,
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


def _knowledge_pane(
    comparison: KnowledgeDiffResult,
    rows: Sequence[ReviewMatrixRow],
    records: ReviewRecordInputs,
    subjects: Mapping[str, SubjectAssessmentState],
    applicability: AppliedRecords,
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
    # The selection the statements render from is the one the classification was made against, read
    # from the projection rather than passed a second time: two spellings of "which revisions were
    # selected" is how a pane comes to render one selection beside another selection's records.
    selected = applicability.revision_selection or SubjectRevisionSelection(
        selection=None, before_item=None, after_item=None
    )
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
        authored_effects=authored_effects(
            tuple(row for row in rows if row.subject.record_kind in AUTHORED_EFFECT_KINDS),
            applicability,
        ),
        signals=tuple(
            signal(entry, applicability.label_of(entry.signal_id))
            for entry in records.signals
            if applicability.label_of(entry.signal_id) is not None
        ),
        assessments=assessment_displays(records, subjects, applicability),
        context=applicability.context_of(KNOWLEDGE_APPLICABILITY_CLASSES),
        applicability=applicability.summaries_of(KNOWLEDGE_APPLICABILITY_CLASSES),
        unresolved=unresolved_authors(
            tuple(row for row in rows if row.subject.record_kind in AUTHORED_EFFECT_KINDS)
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
