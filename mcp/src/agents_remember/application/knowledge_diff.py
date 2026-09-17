"""The application seam for the baseline-to-candidate comparison (KS-R08).

This is the narrow API a caller uses: one selector, two explicit sides, one bounded display. It is
the fifth application seam beside :mod:`knowledge`, :mod:`knowledge_snapshot`, :mod:`knowledge_merge`,
:mod:`knowledge_export` and :mod:`knowledge_read`, and like them it decides no authority and holds no
durable state.

Four boundaries this module owns, each because getting it wrong is a different kind of wrong:

1. **The selection is R07's, run twice.** Each side is selected by
   :func:`agents_remember.memory.knowledge.read.select_recorded_scope` on that side's own read-only
   connection. This module contributes no relevance rule: the only thing it adds to the selection
   contract is that a side may name its own exact revision, which is what lets a before revision and
   an after revision be addressed separately.
2. **A candidate change invalidates a continuation, because the binding says so.** The cursor binds a
   digest over both declared logical snapshots, both resolved contexts, both code trees and both
   selectors. A candidate whose bytes changed has another ``after`` identity, so its cursor no longer
   matches and is refused with ``continuation_binding_mismatch`` rather than continued -- the
   invalidation is the binding, not a check someone has to remember to write.
3. **A missing side refuses, and no HEAD is substituted.** An absent or unreadable database, or a
   side naming a snapshot the file does not hold, is refused by name. Nothing here reaches for a
   working tree, a branch or ``HEAD``: the expansion's command names the two *requested* trees, and a
   side that named no tree is reported as having requested none.
4. **Absence on one side is reported, not raised.** A selection that refuses on one side while the
   other holds records still serves the union and carries that side's absence on the result, which is
   what "the removed before-side realization remains in the diff" means at the seam. The operation
   refuses outright only when *neither* side selected anything.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import apsw

from agents_remember.kernel.git_command import run_git
from agents_remember.memory.knowledge.connection import inspect_schema, open_read_only_database
from agents_remember.memory.knowledge.diff import DiffComparison, compare_selected_scopes
from agents_remember.memory.knowledge.diff_display import (
    DiffDisplay,
    TreeDifferenceProbe,
    TreePaths,
    TreeSide,
    build_display,
    no_tree_difference_probe,
)
from agents_remember.memory.knowledge.logical import (
    bound_repository,
    dataset_identity,
    logical_digest,
)
from agents_remember.memory.knowledge.read import (
    SelectedScope,
    SelectionIncomplete,
    SelectionQuery,
    select_recorded_scope,
)
from agents_remember.memory.knowledge.read_anchors import anchor_resolver_for
from agents_remember.memory.knowledge.read_queries import (
    family_revision_is_recorded,
    invariant_revision_is_recorded,
)
from agents_remember.memory.knowledge.read_refusals import (
    continuation_binding_mismatch_refusal,
    registration_absent_refusal,
    selection_incomplete_refusal,
    selector_absent_refusal,
    snapshot_unavailable_refusal,
)
from agents_remember.memory.knowledge.refusals import (
    KnowledgeStorageError,
    selected_input_unavailable_refusal,
)
from agents_remember.memory.knowledge.schema import CANONICAL_TABLES
from agents_remember.models.knowledge.context import KnowledgeSchemaIdentity
from agents_remember.models.knowledge.diff import (
    DIFF_POLICY_VERSION,
    KnowledgeDiffBinding,
    KnowledgeDiffCounts,
    KnowledgeDiffCursor,
    KnowledgeDiffPage,
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
    KnowledgeDiffSummary,
    ReadSide,
    SideAbsence,
    continue_diff_from_cursor,
    diff_binding_digest,
    diff_cursor_for,
    filter_is_empty,
    filter_policy,
    side_revision_groups,
)
from agents_remember.models.knowledge.read import (
    FamilyIdentitySeed,
    FamilyRevisionSeed,
    InvariantIdentitySeed,
    InvariantRevisionSeed,
    KnowledgeReadContext,
    KnowledgeReadSeed,
    PathSeed,
    read_context_digest,
    seed_digest,
)
from agents_remember.models.knowledge.result import KnowledgeRefusal

__all__ = [
    "diff_knowledge_scope",
    "diff_row_counts",
    "git_tree_difference_probe",
    "open_diff_side",
]

# The one statement shape this module runs that changes nothing: it counts a table so a caller (and
# the evidence) can prove a refused comparison persisted nothing.
ROW_COUNT_TEMPLATE = "SELECT count(*) FROM {table}"

# The Git question the expansion's command answers. ``--no-renames`` is deliberate: a rename is a
# deletion of one path and an addition of another, and reporting a rename would attribute the
# candidate's *new* path to a baseline path that no recorded anchor names.
_TREE_DIFF_ARGS = ("diff", "--name-only", "--no-renames")


def open_diff_side(
    database_path: Path,
    repository_id: str,
    *,
    repository_root: Path | None = None,
    code_tree_id: str | None = None,
) -> KnowledgeReadContext:
    """Resolve one comparison side from the identity the dataset at ``database_path`` holds.

    This is :func:`agents_remember.application.knowledge_read.open_read_context` for one side of a
    comparison, and it says the same thing: a caller cannot hand-write the snapshot a side is
    verified against, it can only resolve one from the file the side names. A candidate that has not
    been published is a supported side -- this operation reads a database file, and a worktree
    candidate database is one -- because nothing here requires a commit, a ledger row or a leaf.

    ``repository_root`` and ``code_tree_id`` are supplied together or not at all: a baseline side
    resolves its anchors against the tree it names, and a side that names no tree reports its anchors
    as ``not_requested`` while still carrying their recorded identities.
    """

    identity = dataset_identity(Path(database_path))
    if identity.repository_id != repository_id:
        raise KnowledgeStorageError(
            f"the dataset at {database_path} is bound to {identity.repository_id}, not to the "
            f"requested repository namespace {repository_id}"
        )
    return KnowledgeReadContext(
        repository_id=repository_id,
        knowledge=identity,
        repository_root=None if repository_root is None else str(repository_root),
        code_tree_id=code_tree_id,
    )


def git_tree_difference_probe(before: TreeSide, after: TreeSide) -> TreePaths:
    """Return the paths two exact code trees differ at, as Git reports them.

    This is the production :data:`~agents_remember.memory.knowledge.diff_display.TreeDifferenceProbe`.
    Two sides are compared only when each has named an exact tree *and* is resolvable in the root it
    named; anything else is reported as an observation this run could not make, never as an empty
    change set. The two trees are addressed by object id and never by a branch, a working tree or
    ``HEAD``, so a comparison of published snapshots cannot silently become a comparison of whatever
    is checked out now.
    """

    if before.tree_id is None or after.tree_id is None:
        return no_tree_difference_probe(before, after)
    if before.root is None or after.root is None:
        return TreePaths(
            available=False,
            detail=(
                "a side named an exact code tree without the repository root it lives in, so the "
                "two trees could not be compared and no change set was observed"
            ),
        )
    result = run_git(Path(before.root), [*_TREE_DIFF_ARGS, before.tree_id, after.tree_id])
    if result.returncode != 0:
        return TreePaths(
            available=False,
            detail=(
                f"the two requested code trees could not be compared ({before.tree_id} in "
                f"{before.root} against {after.tree_id} in {after.root}), so no change set was "
                "observed and none is reported; no working tree or HEAD was substituted"
            ),
        )
    return TreePaths(
        available=True,
        paths=tuple(sorted(line for line in result.stdout.splitlines() if line.strip())),
    )


def diff_knowledge_scope(
    request: KnowledgeDiffRequest,
    *,
    before_path: Path,
    after_path: Path,
    probe: TreeDifferenceProbe | None = None,
) -> KnowledgeDiffResult:
    """Compare two named snapshots for one selected invariant or family.

    ``request`` carries the selector, the two sides, the display filter and the page position;
    ``before_path`` and ``after_path`` are the two database files. The two are separate arguments
    rather than fields of the sides because a side is an *identity* and a path is *where the bytes
    currently are*, and a request whose sides carried paths would make a resolved context and a
    filesystem location the same value.

    ``probe`` is the source-expansion seam and defaults to the real Git one
    (:func:`git_tree_difference_probe`). It is injectable so a case can measure the comparison's own
    behaviour without a repository, and its default is resolved here rather than at the signature
    because a module-level default would capture the function before it is defined.
    """

    before_side = request.before
    after_side = request.after
    resolved = _resolve_sides(request, before_path, after_path, before_side, after_side)
    if isinstance(resolved, KnowledgeDiffResult):
        return resolved
    binding, before_database, after_database = resolved

    cursor = None
    if request.continuation is not None:
        cursor = continue_diff_from_cursor(request.continuation)
        if cursor is None:
            return _refused(
                request,
                binding,
                continuation_binding_mismatch_refusal(
                    detail="the continuation is not a cursor of this format",
                    expected="knowledge-diff-cursor/v1",
                    observed="<unreadable>",
                ),
            )
        mismatch = _cursor_mismatch(cursor, request, binding)
        if mismatch is not None:
            return _refused(request, binding, mismatch)

    try:
        return _compare_inside_one_snapshot_pair(
            request,
            binding=binding,
            paths=SnapshotPair(before=before_database, after=after_database),
            cursor=cursor,
            probe=git_tree_difference_probe if probe is None else probe,
        )
    except (SelectionIncomplete, KnowledgeStorageError, apsw.Error, OSError) as error:
        return _refused(request, binding, _reading_failure(request, before_database, error))


def _reading_failure(
    request: KnowledgeDiffRequest, path: Path, error: Exception
) -> KnowledgeRefusal:
    """Return the typed refusal one failed comparison read earns.

    Four input classes reach here and each names its own fact rather than a generic failure: a
    selection that reached its declared bound, a file that is not the declared snapshot, a SQLite
    failure, and a file that could not be read at all. None of them escapes this boundary as an
    exception a caller would have to catch.
    """

    if isinstance(error, SelectionIncomplete):
        return selection_incomplete_refusal(item_count=error.item_count, bound=error.bound)
    if isinstance(error, KnowledgeStorageError):
        return _unusable(request, path, str(error))
    if isinstance(error, apsw.Error):
        return _unusable(
            request, path, f"a comparison side could not be read through SQLite: {error}"
        )
    return selected_input_unavailable_refusal(
        "diff_knowledge_scope",
        f"a comparison side could not be read: {error}",
        record_id=str(path),
    )


def _resolve_sides(
    request: KnowledgeDiffRequest,
    before_path: Path,
    after_path: Path,
    before_side: KnowledgeDiffSide,
    after_side: KnowledgeDiffSide,
) -> tuple[KnowledgeDiffBinding, Path, Path] | KnowledgeDiffResult:
    """Return the binding and the two paths to open, or the refusal an unresolvable side earns."""

    before_database = Path(before_path)
    after_database = Path(after_path)
    for side, path in (("before", before_database), ("after", after_database)):
        if not path.is_file():
            return _refused_without_binding(
                request,
                selected_input_unavailable_refusal(
                    "diff_knowledge_scope",
                    f"the {side} knowledge database is absent or is not a file: {path}",
                    record_id=str(path),
                ),
            )
    return (
        _binding(request, before_side, after_side),
        before_database,
        after_database,
    )


def _binding(
    request: KnowledgeDiffRequest,
    before_side: KnowledgeDiffSide,
    after_side: KnowledgeDiffSide,
) -> KnowledgeDiffBinding:
    """Return the comparison identity both sides and any continuation are bound to."""

    return KnowledgeDiffBinding(
        policy_version=DIFF_POLICY_VERSION,
        before=before_side.context.knowledge,
        after=after_side.context.knowledge,
        before_context_digest=read_context_digest(before_side.context),
        after_context_digest=read_context_digest(after_side.context),
        before_selector_digest=seed_digest(_effective_selector(before_side, request.selector)),
        after_selector_digest=seed_digest(_effective_selector(after_side, request.selector)),
        before_code_tree_id=before_side.context.code_tree_id,
        after_code_tree_id=after_side.context.code_tree_id,
    )


def _effective_selector(side: KnowledgeDiffSide, selector: KnowledgeReadSeed) -> KnowledgeReadSeed:
    """Return the selector one side actually selects with."""

    return selector if side.selector is None else side.selector


@dataclass(frozen=True)
class SnapshotPair:
    """The two database files one comparison opens, kept together so they cannot be crossed."""

    before: Path
    after: Path


@dataclass(frozen=True)
class OpenPair:
    """The two verified read-only connections one comparison selects through."""

    before: apsw.Connection
    after: apsw.Connection


@dataclass(frozen=True)
class OpenComparison:
    """Everything one comparison needs once both files are open, as one value.

    The request, its binding, the two connections and the source-expansion seam travel together
    because a comparison is exactly their combination, and a helper that had to be handed them
    separately could be handed one comparison's binding with another's connection.
    """

    request: KnowledgeDiffRequest
    binding: KnowledgeDiffBinding
    open_pair: OpenPair
    probe: TreeDifferenceProbe


def _compare_inside_one_snapshot_pair(
    request: KnowledgeDiffRequest,
    *,
    binding: KnowledgeDiffBinding,
    paths: SnapshotPair,
    cursor: KnowledgeDiffCursor | None,
    probe: TreeDifferenceProbe,
) -> KnowledgeDiffResult:
    """Open both sides read-only, verify both snapshots, select both and compare them."""

    before_connection = open_read_only_database(paths.before)
    try:
        after_connection = open_read_only_database(paths.after)
        try:
            return _select_and_compare(
                OpenComparison(
                    request=request,
                    binding=binding,
                    open_pair=OpenPair(before=before_connection, after=after_connection),
                    probe=probe,
                ),
                paths=paths,
                cursor=cursor,
            )
        finally:
            after_connection.close()
    finally:
        before_connection.close()


def _select_and_compare(
    opened: OpenComparison,
    *,
    paths: SnapshotPair,
    cursor: KnowledgeDiffCursor | None,
) -> KnowledgeDiffResult:
    """Verify, select, compare, display and page, in that order and each before the next."""

    request = opened.request
    binding = opened.binding
    open_pair = opened.open_pair
    repository_id = request.before.context.repository_id
    unusable = _any_side_snapshot_refusal(request, paths, open_pair)
    if unusable is not None:
        return _refused(request, binding, unusable)

    before_scope = _select_side(open_pair.before, request.before, request.selector, repository_id)
    after_scope = _select_side(open_pair.after, request.after, request.selector, repository_id)
    comparison = compare_selected_scopes(
        before_connection=open_pair.before,
        after_connection=open_pair.after,
        repository_id=repository_id,
        before_scope=before_scope,
        after_scope=after_scope,
    )
    absences = _side_absences(request, open_pair, repository_id, before_scope, after_scope)
    if not comparison.items:
        return _empty_comparison(request, binding, absences)

    display = build_display(
        comparison,
        display_filter=request.display_filter,
        probe=opened.probe,
        before=TreeSide(
            tree_id=request.before.context.code_tree_id,
            root=request.before.context.repository_root,
        ),
        after=TreeSide(
            tree_id=request.after.context.code_tree_id,
            root=request.after.context.repository_root,
        ),
    )
    position = 0 if cursor is None else cursor.position
    return KnowledgeDiffResult(
        state="page",
        repository_id=repository_id,
        binding=binding,
        binding_digest=diff_binding_digest(binding),
        selector_digest=seed_digest(request.selector),
        policy=filter_policy(_filter_roles(request)),
        summary=KnowledgeDiffSummary(
            before=before_scope.counts,
            after=after_scope.counts,
        ),
        revision_groups=side_revision_groups(
            before_scope.revision_groups, after_scope.revision_groups
        ),
        limitations=display.limitations,
        omissions=display.omissions,
        expansion=display.expansion,
        side_absences=absences,
        page=_page(comparison, display, request, binding, position),
        refusal=None,
    )


def _any_side_snapshot_refusal(
    request: KnowledgeDiffRequest, paths: SnapshotPair, open_pair: OpenPair
) -> KnowledgeRefusal | None:
    """Return the first side refusal either opened snapshot earns, or ``None``.

    Both sides are verified before either is selected, so a comparison cannot report a union built
    from one verified snapshot and one file that turned out not to be the snapshot it declared.
    """

    for side, connection, path, name in (
        (request.before, open_pair.before, paths.before, "before"),
        (request.after, open_pair.after, paths.after, "after"),
    ):
        refusal = _side_snapshot_refusal(connection, side, path, name)
        if refusal is not None:
            return refusal
    return None


def _select_side(
    connection: apsw.Connection,
    side: KnowledgeDiffSide,
    selector: KnowledgeReadSeed,
    repository_id: str,
) -> SelectedScope:
    """Select one side with R07's own policy, using that side's own selector when it named one."""

    return select_recorded_scope(
        connection,
        SelectionQuery(
            repository_id=repository_id,
            seed=selector,
            resolve_anchor=anchor_resolver_for(side.context),
            seed_override=side.selector,
        ),
    )


def _page(
    comparison: DiffComparison,
    display: DiffDisplay,
    request: KnowledgeDiffRequest,
    binding: KnowledgeDiffBinding,
    position: int,
) -> KnowledgeDiffPage:
    """Cut the display window at ``position`` and state the walk's arithmetic honestly.

    ``items_returned`` is cumulative over the whole comparison, exactly as the read page's own count
    is: a later page of a long comparison must not report a total that shrank. ``displayed_total``
    and ``suppressed_total`` are the display's own two numbers and they travel on every page, so a
    filter cannot be mistaken for a shorter comparison.
    """

    total = len(comparison.items)
    window = display.items[position : position + request.budget.max_items]
    returned = len(window)
    leftover = total - position - returned
    has_more = returned > 0 and leftover > 0
    counts = KnowledgeDiffCounts(
        items_total=total,
        items_returned=position + returned,
        items_remaining=total - position - returned,
        displayed_total=len(display.items),
        suppressed_total=total - len(display.items),
        changed_field_count=comparison.changed_field_count,
        changed_source_observation_count=comparison.changed_source_observation_count,
    )
    return KnowledgeDiffPage(
        items=window,
        counts=counts,
        has_more=has_more,
        enumeration_complete=not has_more,
        continuation=(
            diff_cursor_for(
                binding=binding,
                selector_digest=seed_digest(request.selector),
                policy=filter_policy(_filter_roles(request)),
                position=position + returned,
            )
            if has_more
            else None
        ),
    )


def _filter_roles(request: KnowledgeDiffRequest) -> tuple[str, ...]:
    """Return the roles one request's display filter names, in a canonical order."""

    if filter_is_empty(request.display_filter) or request.display_filter is None:
        return ()
    return tuple(sorted(request.display_filter.realization_roles))


def _side_absences(
    request: KnowledgeDiffRequest,
    open_pair: OpenPair,
    repository_id: str,
    before_scope: SelectedScope,
    after_scope: SelectedScope,
) -> tuple[SideAbsence, ...]:
    """Return one entry per side whose own selector named nothing recorded.

    A side's absence is a fact about *that side's* snapshot and it does not stop the comparison: the
    other side's union is still served, so a realization the candidate removed is reported as a
    removal rather than as an absent comparison.
    """

    absences: list[SideAbsence] = []
    sides: tuple[tuple[ReadSide, apsw.Connection, SelectedScope], ...] = (
        ("before", open_pair.before, before_scope),
        ("after", open_pair.after, after_scope),
    )
    for side, connection, scope in sides:
        if scope.counts.primary_items_total > 0:
            continue
        entry = request.before if side == "before" else request.after
        selector = _effective_selector(entry, request.selector)
        refusal = _side_absence_refusal(connection, repository_id, selector)
        if refusal is not None:
            absences.append(SideAbsence(side=side, code=refusal.code, detail=refusal.detail))
    return tuple(absences)


def _side_absence_refusal(
    connection: apsw.Connection, repository_id: str, selector: KnowledgeReadSeed
) -> KnowledgeRefusal | None:
    """Return the typed absence one side's selector earns, using R07's own two codes."""

    if isinstance(selector, PathSeed):
        return registration_absent_refusal(path=str(selector.path))
    if _selector_is_recorded(connection, repository_id, selector):
        return None
    return selector_absent_refusal(
        record_id=_selector_id(selector),
        kind=_selector_kind(selector),
    )


def _selector_is_recorded(
    connection: apsw.Connection, repository_id: str, selector: KnowledgeReadSeed
) -> bool:
    """Return whether one snapshot records the identity or revision a side's selector names.

    The question is the one R07's own seam asks before it reports ``selector_absent``: an identity
    the snapshot does not hold is a selector that names nothing, while a recorded identity whose
    selected revisions carry no memberships and no claims is an empty but real selection.
    """

    if isinstance(selector, InvariantRevisionSeed):
        return invariant_revision_is_recorded(connection, repository_id, selector.revision_id)
    if isinstance(selector, FamilyRevisionSeed):
        return family_revision_is_recorded(connection, repository_id, selector.revision_id)
    if isinstance(selector, InvariantIdentitySeed):
        return _identity_is_recorded(
            connection,
            "SELECT count(*) FROM invariant WHERE repository_id = ? AND invariant_id = ?",
            (repository_id, selector.invariant_id),
        )
    if isinstance(selector, FamilyIdentitySeed):
        return _identity_is_recorded(
            connection,
            "SELECT count(*) FROM family WHERE repository_id = ? AND family_id = ?",
            (repository_id, selector.family_id),
        )
    return False


def _identity_is_recorded(
    connection: apsw.Connection, statement: str, parameters: tuple[Any, ...]
) -> bool:
    """Return whether one identity row is recorded in one snapshot."""

    return _count(connection, statement, parameters) > 0


# The word each selector kind is named by in a refusal. The keys are the seed union's own
# discriminators, so a selector kind added later is reported by its own name rather than as a
# neighbouring one.
_SELECTOR_KINDS: Mapping[str, str] = {
    "path": "path",
    "invariant": "invariant identity",
    "invariant_revision": "invariant revision",
    "family": "family identity",
    "family_revision": "family revision",
}


def _selector_kind(selector: KnowledgeReadSeed) -> str:
    """Return the noun one selector kind is named by in a refusal."""

    return _SELECTOR_KINDS[selector.kind]


def _selector_id(selector: KnowledgeReadSeed) -> str:
    """Name the record one absent selector asked for, in the spelling its own seed carries.

    A revision selector is named by its identity and its exact revision, an identity selector by its
    identity alone, and a path selector by its path. The narrowing is written out rather than reached
    through ``getattr`` so the spelling a caller is shown is a field of the selector that carries it.
    """

    if isinstance(selector, InvariantRevisionSeed):
        return f"{selector.invariant_id}/{selector.revision_id}"
    if isinstance(selector, FamilyRevisionSeed):
        return f"{selector.family_id}/{selector.revision_id}"
    if isinstance(selector, InvariantIdentitySeed):
        return selector.invariant_id
    if isinstance(selector, FamilyIdentitySeed):
        return selector.family_id
    return selector.path


def _empty_comparison(
    request: KnowledgeDiffRequest,
    binding: KnowledgeDiffBinding,
    absences: tuple[SideAbsence, ...],
) -> KnowledgeDiffResult:
    """Refuse a comparison in which neither side selected anything.

    An empty union is not a comparison, and reporting it as one would say "nothing changed" about two
    snapshots neither of which was read. The refusal names the side that earned it: a path with no
    recorded claim on either side is ``registration_absent``, which is R07's own absence code and
    explicitly not a statement of no consequence.
    """

    refusal = (
        registration_absent_refusal(path=str(request.selector.path))
        if isinstance(request.selector, PathSeed)
        else selector_absent_refusal(
            record_id=_selector_id(request.selector), kind=_selector_kind(request.selector)
        )
    )
    if absences:
        refusal = _absence_naming_both(refusal, absences)
    return _refused(request, binding, refusal)


def _absence_naming_both(
    refusal: KnowledgeRefusal, absences: tuple[SideAbsence, ...]
) -> KnowledgeRefusal:
    """Return one refusal whose detail names every side that contributed an absence."""

    named = "; ".join(f"{entry.side}: {entry.code} -- {entry.detail}" for entry in absences)
    return refusal.model_copy(update={"detail": f"{refusal.detail}. Side absences: {named}"})


def _cursor_mismatch(
    cursor: KnowledgeDiffCursor, request: KnowledgeDiffRequest, binding: KnowledgeDiffBinding
) -> KnowledgeRefusal | None:
    """Return the refusal a continuation earns, or ``None`` when it binds exactly this comparison.

    The binding digest is the check that makes a candidate change invalidate its continuations: the
    digest covers both snapshots, so a candidate whose bytes moved presents a binding this cursor does
    not name. The selector and filter digests are checked separately because they are *request*
    bindings rather than *comparison* bindings, and a caller that changed its selector or its filter
    has changed the question, not the bytes.

    The request-level bindings are decided first, so a caller who changed the question is told that
    rather than being sent to re-select a dataset they selected correctly. A caller who changed both
    is told about the question, which is the part they can see; the snapshot pair is reported by the
    digest check once the request agrees.
    """

    checks = (
        (cursor.policy_version, DIFF_POLICY_VERSION, "it binds another comparison policy"),
        (
            cursor.selector_digest,
            seed_digest(request.selector),
            "it binds another selector",
        ),
        (cursor.policy, filter_policy(_filter_roles(request)), "it binds another display filter"),
        (cursor.binding_digest, diff_binding_digest(binding), "it binds another snapshot pair"),
    )
    for observed, expected, detail in checks:
        if observed != expected:
            return continuation_binding_mismatch_refusal(
                detail=detail, expected=str(expected), observed=str(observed)
            )
    return None


def _side_snapshot_refusal(
    connection: apsw.Connection,
    side: KnowledgeDiffSide,
    path: Path,
    side_name: str,
) -> KnowledgeRefusal | None:
    """Return the refusal one opened side earns against the snapshot it declared, or ``None``."""

    declared = side.context.knowledge
    repository = bound_repository(connection)
    if repository is None or repository.repository_id != side.context.repository_id:
        return snapshot_unavailable_refusal(
            detail=(f"the {side_name} database is not bound to the requested repository namespace"),
            expected=side.context.repository_id,
            observed=("<no namespace row>" if repository is None else repository.repository_id),
        )
    identity: KnowledgeSchemaIdentity = inspect_schema(connection)
    if identity.schema_name != declared.schema_version:
        return snapshot_unavailable_refusal(
            detail=f"the {side_name} database declares another schema generation",
            expected=declared.schema_version,
            observed=identity.schema_name,
        )
    observed_digest = logical_digest(connection, identity.schema_name)
    if observed_digest != declared.logical_digest:
        return snapshot_unavailable_refusal(
            detail=(
                f"the {side_name} database holds another logical dataset than the one this "
                f"comparison selected (at {path})"
            ),
            expected=declared.logical_digest,
            observed=observed_digest,
        )
    return None


def _unusable(request: KnowledgeDiffRequest, path: Path, detail: str) -> KnowledgeRefusal:
    """Return the refusal a side that could not be read at all earns."""

    return snapshot_unavailable_refusal(
        detail=f"{detail} (at {path})",
        expected=request.before.context.knowledge.logical_digest,
        observed=str(path),
    )


def _refused(
    request: KnowledgeDiffRequest,
    binding: KnowledgeDiffBinding,
    refusal: KnowledgeRefusal,
) -> KnowledgeDiffResult:
    """Return one refusal bound to the comparison identity it was refused for."""

    return KnowledgeDiffResult(
        state="refused",
        repository_id=request.before.context.repository_id,
        binding=binding,
        binding_digest=diff_binding_digest(binding),
        selector_digest=seed_digest(request.selector),
        policy=filter_policy(_filter_roles(request)),
        page=None,
        refusal=refusal,
    )


def _refused_without_binding(
    request: KnowledgeDiffRequest, refusal: KnowledgeRefusal
) -> KnowledgeDiffResult:
    """Return one refusal that could not be bound, because a side never resolved to a file."""

    return KnowledgeDiffResult(
        state="refused",
        repository_id=request.before.context.repository_id,
        page=None,
        refusal=refusal,
    )


def _count(connection: apsw.Connection, statement: str, parameters: tuple[Any, ...]) -> int:
    return int(next(iter(connection.execute(statement, parameters)))[0])


def diff_row_counts(database_path: Path) -> Mapping[str, int]:
    """Return one row count per canonical table for one database, reading it without writing.

    This is the measurement half of "a refused comparison persisted nothing": a caller takes it
    before and after a refusal and compares. It is a real read of the real file through the same
    read-only handle the operation uses, not an assertion about the operation's intent.
    """

    connection = open_read_only_database(Path(database_path))
    try:
        return {
            table: _count(connection, ROW_COUNT_TEMPLATE.format(table=table), ())
            for table in CANONICAL_TABLES
        }
    finally:
        connection.close()
