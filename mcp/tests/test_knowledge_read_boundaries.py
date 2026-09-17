"""The read's real boundaries: a real Git tree, a real snapshot identity, a real publication.

The cases that need something genuinely outside the process live here, in the ``integration`` lane,
because that is the lane the repository reserves for real repository and publication boundaries --
the same reason L6 put its portable roundtrip here. Each case builds its own fixture, its own Git
object store and its own database file; nothing is shared and nothing is mocked.

What these cases are for is the part of the read that cannot be hermetic: the source-anchor
observation is a real ``git ls-tree`` against a real tree, the snapshot binding is a real logical
digest of a real file, and the no-task baseline read is a real read of a real dataset with no leaf
contract anywhere in the call.
"""

from __future__ import annotations

import base64
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from agents_remember.application.knowledge_read import (
    open_read_context,
    read_knowledge_scope,
    read_row_counts,
)
from agents_remember.memory.knowledge import realizations
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import logical_digest
from agents_remember.memory.knowledge.read import _manifest_digest
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.graph import RealizationClaimDraft
from agents_remember.models.knowledge.read import (
    AnchorResolution,
    FamilyRevisionSeed,
    InvariantRevisionSeed,
    KnowledgeReadBudget,
    KnowledgeReadContext,
    KnowledgeReadCursor,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    ReadItem,
    SelectionReason,
    continue_from_cursor,
)
from agents_remember.models.knowledge.result import NewAnchor, RealizationClaimRequest
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    SourceAnchorDraft,
    SymbolLocator,
)
from read_scope_test_support import (
    ABSENT_PATH,
    ABSENT_RECORDED_BLOB,
    AUXILIARY_PATH,
    BATCH_PATH,
    INTEGRATION_PATH,
    MISMATCH_PATH,
    MISMATCH_RECORDED_BLOB,
    RESOLUTION_PATH,
    SYNCHRONIZATION_PATH,
    ReadScopeFixture,
    build_read_scope_fixture,
)

pytestmark = pytest.mark.integration

SCHEMA_NAME = "ar-knowledge-sqlite/v1"
EVERY_RECORDED_PATH = {
    INTEGRATION_PATH,
    SYNCHRONIZATION_PATH,
    BATCH_PATH,
    RESOLUTION_PATH,
    AUXILIARY_PATH,
    MISMATCH_PATH,
}
GIT_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_CONFIG_NOSYSTEM": "1",
}
# The paths the subject-revision seed's selection realizes. It includes ``AUXILIARY_PATH`` because
# the subject revision is a member of two families and the second one carries that further member;
# it is not a list of the paths a *path* seed would reach.
SUBJECT_REALIZED_PATHS = {
    INTEGRATION_PATH,
    SYNCHRONIZATION_PATH,
    BATCH_PATH,
    ABSENT_PATH,
    MISMATCH_PATH,
    AUXILIARY_PATH,
}


@pytest.fixture
def fixture(tmp_path: Path) -> ReadScopeFixture:
    """One fresh fixture, with its own committed Git tree, per case."""

    return build_read_scope_fixture(tmp_path / "read-scope")


def anchored_context(fixture: ReadScopeFixture) -> KnowledgeReadContext:
    """The fixture's own context, resolving anchors against its real committed tree."""

    return open_read_context(
        fixture.database_path,
        fixture.repository_id,
        repository_root=fixture.git_root,
        code_tree_id=fixture.git_tree_id,
    )


def read(
    fixture: ReadScopeFixture,
    seed: KnowledgeReadSeed,
    *,
    budget: KnowledgeReadBudget | None = None,
    continuation: str | None = None,
) -> KnowledgeReadResult:
    """Run one read of the fixture at its anchored context."""

    return read_knowledge_scope(
        fixture.database_path,
        anchored_context(fixture),
        KnowledgeReadRequest(
            seed=seed, budget=budget or KnowledgeReadBudget(), continuation=continuation
        ),
    )


def subject_seed(fixture: ReadScopeFixture) -> InvariantRevisionSeed:
    """The exact subject revision every anchor case reads."""

    return InvariantRevisionSeed(
        invariant_id=fixture.retry_invariant_id, revision_id=fixture.subject_revision_id
    )


def family_seed(fixture: ReadScopeFixture) -> FamilyRevisionSeed:
    """The exact first revision of the fixture's main family."""

    return FamilyRevisionSeed(
        family_id=fixture.family.family_id, revision_id=fixture.family.revision_id
    )


def anchors_by_path(result: KnowledgeReadResult) -> dict[str, AnchorResolution]:
    """Map each realized path to the observation the read reported for it."""

    page = result.page
    assert page is not None
    return {
        item.anchor.path: item.anchor
        for item in page.items
        if item.kind == "realization_claim" and item.anchor is not None
    }


def test_the_fixture_tree_really_holds_the_blobs_its_anchors_record(
    fixture: ReadScopeFixture,
) -> None:
    """The cases' own precondition, asserted through Git rather than assumed.

    An anchor case is evidence only if the tree it resolved against really holds the objects the
    anchors recorded, so this case checks that independently: the tree is committed, it carries every
    recorded path but the deliberately absent one, and the blob it holds at each path is the one the
    fixture recorded -- while the mismatch anchor's recorded identity is deliberately not.
    """

    assert fixture.git_tree_id, "the fixture tree is committed"
    assert set(fixture.git_blobs) == EVERY_RECORDED_PATH
    assert ABSENT_PATH not in fixture.git_blobs, "the absent path is in no tree, by construction"
    for path, blob in fixture.git_blobs.items():
        assert len(blob) == 40
        observed = subprocess.run(
            ["git", "rev-parse", f"{fixture.git_tree_id}:{path}"],
            cwd=fixture.git_root,
            capture_output=True,
            text=True,
            check=False,
            env={**GIT_ENVIRONMENT, "HOME": str(fixture.git_root)},
        )
        assert observed.returncode == 0, observed.stderr
        assert observed.stdout.strip() == blob
    assert fixture.git_blobs[MISMATCH_PATH] != MISMATCH_RECORDED_BLOB


def test_an_anchor_whose_recorded_blob_is_the_requested_trees_blob_is_observed_as_exact(
    fixture: ReadScopeFixture,
) -> None:
    """The recorded identity equals the observed one, and the recorded identity is retained.

    The positive half of the anchor contract: a claim whose anchor records the blob the requested
    tree really holds reports ``exact_recorded_blob`` and still carries the recorded object id, so a
    reader can tell which authored identity was confirmed rather than being handed a bare boolean.
    """

    result = read(fixture, subject_seed(fixture))

    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    integration = observed[INTEGRATION_PATH]
    assert integration.resolution == "exact_recorded_blob"
    assert integration.recorded_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    assert integration.observed_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    assert integration.recorded_source_identity == integration.observed_source_identity


def test_a_changed_path_reports_a_blob_mismatch_and_never_promotes_the_old_claim(
    fixture: ReadScopeFixture,
) -> None:
    """Different bytes at a recorded path are reported as a mismatch, with both identities named.

    The claim is kept: an authored attribution is not retired because the bytes moved, and the
    observation distinguishes the recorded identity from the observed one instead of promoting the
    claim to a realization of the current bytes.
    """

    result = read(fixture, subject_seed(fixture))

    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    mismatch = observed[MISMATCH_PATH]
    assert mismatch.resolution == "recorded_blob_mismatch"
    assert mismatch.recorded_source_identity != mismatch.observed_source_identity
    assert mismatch.observed_source_identity == fixture.git_blobs[MISMATCH_PATH]
    assert mismatch.recorded_source_identity == MISMATCH_RECORDED_BLOB
    assert result.page is not None
    assert result.page.counts.unresolved_anchor_total >= 2


def test_a_path_absent_from_the_requested_tree_keeps_the_claim_and_reports_the_absence(
    fixture: ReadScopeFixture,
) -> None:
    """A recorded location the tree does not carry is ``path_absent``, and no other tree is consulted.

    The claim remains in the selected set with its recorded identity, which is what keeps a moved
    source from erasing a recorded attribution, and the observation is scoped to the tree the caller
    named: nothing here looks at a working tree, a branch or ``HEAD``.
    """

    result = read(fixture, subject_seed(fixture))

    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    absent = observed[ABSENT_PATH]
    assert absent.resolution == "path_absent"
    assert absent.recorded_source_identity == ABSENT_RECORDED_BLOB
    assert absent.observed_source_identity is None
    assert result.page is not None
    claim_ids = {item.claim_id for item in result.page.items if item.kind == "realization_claim"}
    assert fixture.absent_anchor.claim_id in claim_ids


def test_no_requested_tree_reports_not_requested_and_still_returns_the_recorded_identities(
    fixture: ReadScopeFixture,
) -> None:
    """A read with no code tree is supported, and says so rather than inventing a resolution.

    Reading recorded knowledge does not require a Git object store. A context that named no tree is
    answered with ``not_requested`` plus every recorded identity, and an absent path is *not* reported
    as absent from a tree -- because no tree was named, and the two facts differ.
    """

    result = read_knowledge_scope(
        fixture.database_path,
        open_read_context(fixture.database_path, fixture.repository_id),
        KnowledgeReadRequest(seed=subject_seed(fixture)),
    )

    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    assert set(observed) == SUBJECT_REALIZED_PATHS
    assert observed[INTEGRATION_PATH].resolution == "not_requested"
    assert (
        observed[INTEGRATION_PATH].recorded_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    )
    assert observed[INTEGRATION_PATH].observed_source_identity is None
    assert observed[ABSENT_PATH].resolution == "not_requested"
    assert observed[ABSENT_PATH].recorded_source_identity == ABSENT_RECORDED_BLOB
    assert result.page is not None
    assert (
        result.page.counts.unresolved_anchor_total == result.page.counts.realization_claims_total
    ), "every anchor is an unresolved observation when no tree was requested"


def test_an_unavailable_tree_reports_the_unavailable_object_and_substitutes_nothing(
    fixture: ReadScopeFixture,
) -> None:
    """A tree the repository does not hold is one named observation, not a silent fallback to HEAD.

    The context names an object that is not in this repository, so the observation is
    ``recorded_object_unavailable`` and the recorded identity is still carried -- the caller can see
    that the question was asked and what prevented an answer.
    """

    missing_tree = "f" * 40
    result = read_knowledge_scope(
        fixture.database_path,
        open_read_context(
            fixture.database_path,
            fixture.repository_id,
            repository_root=fixture.git_root,
            code_tree_id=missing_tree,
        ),
        KnowledgeReadRequest(seed=subject_seed(fixture)),
    )

    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    assert observed[INTEGRATION_PATH].resolution == "recorded_object_unavailable"
    assert (
        observed[INTEGRATION_PATH].recorded_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    )
    assert fixture.git_blobs[INTEGRATION_PATH] != missing_tree


def test_an_unsupported_locator_stays_visible_while_its_blob_is_still_observed(
    fixture: ReadScopeFixture,
) -> None:
    """A symbol locator is reported as unsupported, and it does not erase the anchor.

    The initial resolver supports exact files and blob-bound ranges, so a recorded symbol is a
    representable observation rather than a dropped row: the claim and its recorded blob identity
    survive a locator this increment cannot resolve, and the claim is not reported as a resolved file
    it never was.
    """

    claim_id = str(uuid4())
    anchor_id = str(uuid4())
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        created = realizations.create_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim=RealizationClaimDraft(
                    claim_id=claim_id,
                    invariant_revision_id=fixture.subject_revision_id,
                    role="incidental",
                    rationale="A recorded symbol location for the same obligation.",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(anchor_id),
                        path=INTEGRATION_PATH,
                        source_identity=GitBlobIdentity(
                            object_id=fixture.git_blobs[INTEGRATION_PATH]
                        ),
                        locator=SymbolLocator(
                            language="python", qualified_name="integration.apply"
                        ),
                    )
                ),
                provenance=fixture.authorship,
            ),
        )
    finally:
        store.close()
    assert created.state == "created", created.refusal

    result = read(fixture, subject_seed(fixture))

    assert result.state == "page", result.refusal
    assert result.page is not None
    item = next(
        item
        for item in result.page.items
        if item.kind == "realization_claim" and item.claim_id == claim_id
    )
    assert item.anchor is not None
    assert item.anchor.resolution == "unsupported_locator"
    assert item.anchor.recorded_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    assert item.anchor.observed_source_identity is None


def test_a_non_blob_tree_entry_is_reported_as_an_entry_and_never_read_as_source_bytes(
    fixture: ReadScopeFixture,
) -> None:
    """A recorded path holding a symlink is ``entry_not_blob``, and no path is followed.

    A recorded location can hold something that is not source bytes. Reporting it as a resolved blob
    would claim source that is not there, and following it would escape the tree the caller named, so
    the entry kind is what the observation names.
    """

    link = fixture.git_root / "src" / "linked_adapter.py"
    link.symlink_to(Path("retired_adapter.py"))
    subprocess.run(
        ["git", "add", "-A"],
        cwd=fixture.git_root,
        capture_output=True,
        check=True,
        env={**GIT_ENVIRONMENT, "HOME": str(fixture.git_root)},
    )
    subprocess.run(
        ["git", "commit", "-q", "-m", "a symlink at a recorded path"],
        cwd=fixture.git_root,
        capture_output=True,
        check=True,
        env={**GIT_ENVIRONMENT, "HOME": str(fixture.git_root)},
    )
    tree = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=fixture.git_root,
        capture_output=True,
        text=True,
        check=True,
        env={**GIT_ENVIRONMENT, "HOME": str(fixture.git_root)},
    ).stdout.strip()

    claim_id = str(uuid4())
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        created = realizations.create_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim=RealizationClaimDraft(
                    claim_id=claim_id,
                    invariant_revision_id=fixture.subject_revision_id,
                    role="support",
                    rationale="A recorded location that holds a link rather than source.",
                ),
                anchor=NewAnchor(
                    anchor=SourceAnchorDraft(
                        anchor_id=UUID(str(uuid4())),
                        path="src/linked_adapter.py",
                        source_identity=GitBlobIdentity(
                            object_id=fixture.git_blobs[INTEGRATION_PATH]
                        ),
                        locator=FileLocator(),
                    )
                ),
                provenance=fixture.authorship,
            ),
        )
    finally:
        store.close()
    assert created.state == "created", created.refusal

    result = read_knowledge_scope(
        fixture.database_path,
        open_read_context(
            fixture.database_path,
            fixture.repository_id,
            repository_root=fixture.git_root,
            code_tree_id=tree,
        ),
        KnowledgeReadRequest(seed=subject_seed(fixture)),
    )

    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    linked = observed["src/linked_adapter.py"]
    assert linked.resolution == "entry_not_blob"
    assert linked.observed_source_identity is not None
    assert linked.observed_source_identity != linked.recorded_source_identity
    assert linked.recorded_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    assert result.page is not None
    integration = observed[INTEGRATION_PATH]
    assert integration.resolution == "exact_recorded_blob", (
        "the entry-kind observation is about the linked path, not about its neighbour"
    )


def test_a_baseline_read_serves_a_task_free_context_and_reaches_the_whole_selected_scope(
    fixture: ReadScopeFixture,
) -> None:
    """A read with ``task_ref=None`` succeeds and reaches the full graph, with no enclosure involved.

    The planning case: recorded knowledge is readable before a leaf exists. Every read in this module
    already runs that way, so what this case adds is the assertion that the served context really is a
    task-free one and that the selection it produces is the whole one rather than a degraded subset --
    a reader can plan from it without inventing a task to satisfy the read.
    """

    context = anchored_context(fixture)
    assert context.task_ref is None

    result = read(fixture, family_seed(fixture))

    assert result.state == "page", result.refusal
    assert result.snapshot is not None
    assert result.snapshot.logical_digest == context.knowledge.logical_digest
    assert result.page is not None
    assert result.page.counts.primary_items_total == 13
    assert result.page.counts.distinct_source_locations_total == 5
    claim_ids = {item.claim_id for item in result.page.items if item.kind == "realization_claim"}
    assert claim_ids == {
        fixture.integration.claim_id,
        fixture.synchronization.claim_id,
        fixture.batch_primary.claim_id,
        fixture.batch_secondary.claim_id,
        fixture.absent_anchor.claim_id,
        fixture.mismatch_anchor.claim_id,
    }


def test_a_context_naming_another_namespace_refuses_and_names_both_identities(
    fixture: ReadScopeFixture,
    tmp_path: Path,
) -> None:
    """A context resolved from another dataset is refused against this file, with both sides named.

    The context carries a namespace and a logical snapshot; this file is bound to neither. The
    refusal says which namespace it expected and which it found, so the caller is not left guessing
    whether the path or the selection was wrong.
    """

    other = build_read_scope_fixture(tmp_path / "other")
    foreign = open_read_context(other.database_path, other.repository_id)
    assert foreign.repository_id != fixture.repository_id

    result = read_knowledge_scope(
        fixture.database_path,
        foreign,
        KnowledgeReadRequest(seed=subject_seed(other)),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "snapshot_unavailable"
    assert result.refusal.expected == foreign.repository_id
    assert result.refusal.observed == fixture.repository_id
    assert "not bound to the requested repository namespace" in result.refusal.detail
    assert result.page is None


def test_a_context_whose_logical_digest_is_not_the_files_is_refused(
    fixture: ReadScopeFixture,
) -> None:
    """A context naming another *snapshot* of the right namespace is refused, not silently served.

    The namespace matches and the digest does not: the caller selected one dataset and the path holds
    a different one. Nothing is served from the bytes that are there, because a different dataset is a
    different snapshot rather than a newer version of the selected one.
    """

    resolved = anchored_context(fixture)
    context = resolved.model_copy(
        update={"knowledge": resolved.knowledge.model_copy(update={"logical_digest": "b" * 64})}
    )
    result = read_knowledge_scope(
        fixture.database_path, context, KnowledgeReadRequest(seed=subject_seed(fixture))
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "snapshot_unavailable"
    assert result.refusal.expected == "b" * 64
    assert result.refusal.observed == fixture.knowledge_digest
    assert result.page is None


def test_an_absent_database_refuses_as_an_unavailable_input_rather_than_as_empty_knowledge(
    fixture: ReadScopeFixture,
    tmp_path: Path,
) -> None:
    """A missing selected input is an input error, and it is not answered with an empty scope.

    The distinction the requirement insists on: "nothing is recorded here" is a fact about a snapshot
    that was read, and it must never be reachable by asking about a file that is not there.
    """

    result = read_knowledge_scope(
        tmp_path / "missing.db",
        anchored_context(fixture),
        KnowledgeReadRequest(seed=subject_seed(fixture)),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "selected_input_unavailable"
    assert result.page is None


def test_a_continuation_presented_with_another_selector_refuses_and_returns_no_partial_page(
    fixture: ReadScopeFixture,
) -> None:
    """A cursor presented with a different selector is refused, and no items come back.

    A continuation is a position in one named selection. Serving it against another selector would
    append a page of one selection to a page of another, so the binding check runs before any
    selection does and the refusal carries both digests.
    """

    first = read(fixture, family_seed(fixture), budget=KnowledgeReadBudget(max_items=2))
    assert first.page is not None
    assert first.page.continuation is not None

    foreign = read(
        fixture,
        subject_seed(fixture),
        budget=KnowledgeReadBudget(max_items=2),
        continuation=first.page.continuation,
    )

    assert foreign.state == "refused"
    assert foreign.refusal is not None
    assert foreign.refusal.code == "continuation_binding_mismatch"
    assert foreign.refusal.detail.endswith("it binds another selector")
    assert foreign.refusal.expected != foreign.refusal.observed
    assert foreign.page is None, "a mismatched continuation returns no partial page"


def test_a_continuation_presented_under_another_context_or_policy_refuses(
    fixture: ReadScopeFixture,
) -> None:
    """A cursor whose context or selection policy differs is refused, and no page comes back.

    Two of the five bindings a continuation carries, and the two the seam's own docstring names: a
    cursor binds the *resolved context* it was issued under, not only the logical snapshot, and it
    binds the *selection policy* that produced it. The context variant differs from the cursor's own
    in the exact code tree it names -- the same namespace and the same logical digest, another tree
    for the anchors -- which is precisely the case the logical-digest check cannot catch, so the
    refusal below can only be issued by the context binding.
    """

    seed = family_seed(fixture)
    first = read(fixture, seed, budget=KnowledgeReadBudget(max_items=2))
    assert first.page is not None
    assert first.page.continuation is not None
    cursor = continue_from_cursor(first.page.continuation)
    assert cursor is not None

    anchored = anchored_context(fixture)
    other_context = anchored.model_copy(
        update={"code_tree_id": "e" * 40, "repository_root": str(fixture.git_root)}
    )
    assert other_context.knowledge == anchored.knowledge, (
        "the variant names the same logical snapshot, so only the context binding can refuse it"
    )
    foreign = read_knowledge_scope(
        fixture.database_path,
        other_context,
        KnowledgeReadRequest(
            seed=seed, budget=KnowledgeReadBudget(max_items=2), continuation=first.page.continuation
        ),
    )
    assert foreign.state == "refused"
    assert foreign.refusal is not None
    assert foreign.refusal.code == "continuation_binding_mismatch"
    assert foreign.refusal.detail.endswith("it binds another resolved context")
    assert foreign.refusal.expected != foreign.refusal.observed
    assert foreign.page is None, "a continuation from another context returns no partial page"

    altered_policy = cursor.model_copy(update={"policy_version": "recorded-family-frontier/v0"})
    repolicy = read_knowledge_scope(
        fixture.database_path,
        anchored,
        KnowledgeReadRequest(
            seed=seed,
            budget=KnowledgeReadBudget(max_items=2),
            continuation=_encode_cursor(altered_policy),
        ),
    )
    assert repolicy.state == "refused"
    assert repolicy.refusal is not None
    assert repolicy.refusal.code == "continuation_binding_mismatch"
    assert repolicy.refusal.detail.endswith("it binds another selection policy")
    assert repolicy.refusal.observed == "recorded-family-frontier/v0"
    assert repolicy.page is None


def test_a_continuation_that_binds_another_manifest_is_refused_and_its_own_is_verified(
    fixture: ReadScopeFixture,
) -> None:
    """The cursor's manifest binding is checked against the selection it names a position in.

    A cursor carries the manifest digest of the *selected set* it continues, and this case measures
    both halves of that binding. Positively: every page of a real walk hands back a cursor whose
    manifest is the manifest the page declared, so the field is the same statement about the
    selection that the other bindings make about the request. Negatively: a cursor whose manifest
    names another selection is refused before a page is built from it, rather than being served a
    position in a set it does not describe.
    """

    seed = family_seed(fixture)
    continuation: str | None = None
    walked = 0
    while True:
        result = read(
            fixture, seed, budget=KnowledgeReadBudget(max_items=2), continuation=continuation
        )
        assert result.state == "page", result.refusal
        assert result.page is not None
        walked += 1
        if result.page.continuation is None:
            assert result.page.has_more is False
            break
        cursor = continue_from_cursor(result.page.continuation)
        assert cursor is not None
        assert cursor.manifest_digest == result.manifest_digest, (
            "the continuation a page hands back binds the manifest that page declared"
        )
        continuation = result.page.continuation
    assert walked > 1, "the walk had more than one page, so the continuation binding was exercised"

    first = read(fixture, seed, budget=KnowledgeReadBudget(max_items=2))
    assert first.page is not None
    assert first.page.continuation is not None
    forged = continue_from_cursor(first.page.continuation)
    assert forged is not None
    assert forged.manifest_digest == first.manifest_digest, (
        "the untouched continuation binds the served manifest; the edit below is the only change"
    )
    tampered = forged.model_copy(update={"manifest_digest": "c" * 64})
    result = read_knowledge_scope(
        fixture.database_path,
        anchored_context(fixture),
        KnowledgeReadRequest(
            seed=seed,
            budget=KnowledgeReadBudget(max_items=2),
            continuation=_encode_cursor(tampered),
        ),
    )
    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "continuation_binding_mismatch"
    assert result.refusal.detail.endswith("it binds another selected set")
    assert result.refusal.expected == first.manifest_digest
    assert result.refusal.observed == "c" * 64
    assert result.page is None


def test_a_continuation_naming_a_position_past_the_selection_refuses_rather_than_escaping(
    fixture: ReadScopeFixture,
) -> None:
    """A caller-edited cursor position is a typed refusal, not a raw exception out of the seam.

    The cursor is the caller's own text, so its position is an input this operation models rather
    than a fact it may assume. ``position <= len(items)`` holds for every cursor the read issues,
    and a position beyond the selection is refused by name and with no partial page -- it never
    reaches a slice or a model constraint, where it would leave the typed boundary as an
    exception a caller has to catch.
    """

    seed = family_seed(fixture)
    first = read(fixture, seed, budget=KnowledgeReadBudget(max_items=2))
    assert first.page is not None
    assert first.page.continuation is not None
    cursor = continue_from_cursor(first.page.continuation)
    assert cursor is not None

    forged = cursor.model_copy(update={"position": 9999})
    result = read_knowledge_scope(
        fixture.database_path,
        anchored_context(fixture),
        KnowledgeReadRequest(
            seed=seed,
            budget=KnowledgeReadBudget(max_items=2),
            continuation=_encode_cursor(forged),
        ),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "continuation_binding_mismatch"
    assert result.refusal.detail.endswith("it names a position past the end of the selected set")
    assert result.refusal.observed == "9999"
    assert result.page is None


def test_a_context_declaring_another_schema_generation_is_refused_before_a_page_is_built(
    fixture: ReadScopeFixture,
) -> None:
    """A declared schema that is not the file's own generation is refused, with no page served.

    The guard is reached by a *context*, not by two files, and this case builds exactly that input:
    the declared snapshot keeps the digest the file really holds -- asserted against the context the
    reader itself resolved -- and changes only the schema generation it claims. The digest
    comparison therefore cannot refuse this request, and the schema check is the only thing standing
    between the caller and a page served from a generation it did not name. The two variants are a
    pair: this one pins the schema comparison, the digest-only one below pins the digest comparison,
    and each is refused by its own guard as a different fact.
    """

    resolved = anchored_context(fixture)
    assert resolved.knowledge.schema_version == SCHEMA_NAME
    assert resolved.knowledge.logical_digest == fixture.knowledge_digest, (
        "the declared digest is the one the file really holds"
    )
    context = resolved.model_copy(
        update={
            "knowledge": resolved.knowledge.model_copy(
                update={"schema_version": "ar-knowledge-sqlite/v9"}
            )
        }
    )
    assert context.knowledge.logical_digest == fixture.knowledge_digest, (
        "only the schema generation changed, so the digest comparison cannot answer for the guard"
    )
    result = read_knowledge_scope(
        fixture.database_path, context, KnowledgeReadRequest(seed=subject_seed(fixture))
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "snapshot_unavailable"
    assert result.refusal.detail.endswith(
        "the selected database declares another schema generation"
    )
    assert result.refusal.expected == "ar-knowledge-sqlite/v9"
    assert result.refusal.observed == SCHEMA_NAME
    assert result.page is None

    # The digest half of the pair: the same request with the schema left honest and only the digest
    # forged is refused as a *different* fact. A guard that were only the digest check would answer
    # this one and not the one above, and the schema a caller declared would then be a claim nothing
    # read.
    forged_digest = "d" * 64
    digest_only = resolved.model_copy(
        update={
            "knowledge": resolved.knowledge.model_copy(update={"logical_digest": forged_digest})
        }
    )
    other = read_knowledge_scope(
        fixture.database_path, digest_only, KnowledgeReadRequest(seed=subject_seed(fixture))
    )
    assert other.state == "refused"
    assert other.refusal is not None
    assert other.refusal.code == "snapshot_unavailable"
    assert other.refusal.expected == forged_digest
    assert other.refusal.observed == fixture.knowledge_digest
    assert other.page is None


def test_a_continuation_whose_declared_snapshot_was_altered_refuses_rather_than_mixing_revisions(
    fixture: ReadScopeFixture,
) -> None:
    """An edited cursor whose declared snapshot differs is refused as a binding mismatch.

    The cursor is the caller's own text, so a caller can hand back one that names another snapshot. It
    is refused by name before any selection runs, which is what makes "a continuation used against
    another snapshot refuses" a property of the format rather than of a caller's care.
    """

    seed = family_seed(fixture)
    first = read(fixture, seed, budget=KnowledgeReadBudget(max_items=2))
    assert first.page is not None
    assert first.page.continuation is not None
    cursor = continue_from_cursor(first.page.continuation)
    assert cursor is not None

    altered = cursor.model_copy(update={"logical_digest": "b" * 64})
    result = read_knowledge_scope(
        fixture.database_path,
        anchored_context(fixture),
        KnowledgeReadRequest(seed=seed, continuation=_encode_cursor(altered)),
    )

    assert result.state == "refused"
    assert result.refusal is not None
    assert result.refusal.code == "continuation_binding_mismatch"
    assert result.refusal.detail.endswith("it binds another logical snapshot")
    assert result.refusal.observed == "b" * 64
    assert result.page is None


def test_a_continuation_against_its_own_snapshot_continues_the_same_manifest(
    fixture: ReadScopeFixture,
) -> None:
    """The same cursor against its own snapshot continues that manifest, page after page.

    The control for the two cases above, and the positive statement of the completeness rule: the
    continuation works here, every page it reaches declares the snapshot the first page declared, and
    the pages together enumerate the selected set exactly once.
    """

    seed = family_seed(fixture)
    continuation: str | None = None
    seen: list[str] = []
    declared: set[str] = set()
    while True:
        result = read(
            fixture, seed, budget=KnowledgeReadBudget(max_items=3), continuation=continuation
        )
        assert result.state == "page", result.refusal
        assert result.page is not None
        assert result.snapshot is not None
        declared.add(result.snapshot.logical_digest)
        seen.extend(item.item_id for item in result.page.items)
        if not result.page.has_more:
            assert result.page.continuation is None
            break
        assert result.page.continuation is not None
        continuation = result.page.continuation

    assert declared == {fixture.knowledge_digest}
    assert len(seen) == len(set(seen))
    assert len(seen) == 13


def test_a_refused_read_of_a_real_database_leaves_the_file_byte_identical(
    fixture: ReadScopeFixture,
    tmp_path: Path,
) -> None:
    """A refusal on a real file changes no row and no logical digest, measured either side.

    The real-file form of the persisted-nothing property, including a refusal that happens after the
    snapshot has been opened and verified, so the measurement covers the whole read path rather than
    only its earliest guard.
    """

    before_counts = read_row_counts(fixture.database_path)
    before_digest = _digest(fixture)

    elsewhere = build_read_scope_fixture(tmp_path / "other")
    wrong_snapshot = read_knowledge_scope(
        fixture.database_path,
        open_read_context(elsewhere.database_path, elsewhere.repository_id),
        KnowledgeReadRequest(seed=subject_seed(elsewhere)),
    )
    assert wrong_snapshot.state == "refused"
    assert wrong_snapshot.refusal is not None
    assert wrong_snapshot.refusal.code == "snapshot_unavailable"

    tiny = read(
        fixture,
        subject_seed(fixture),
        budget=KnowledgeReadBudget(max_items=1, max_utf8_bytes=64),
    )
    assert tiny.state == "refused"
    assert tiny.refusal is not None
    assert tiny.refusal.code == "page_budget_too_small"

    assert read_row_counts(fixture.database_path) == before_counts
    assert _digest(fixture) == before_digest


def test_the_selection_reasons_are_part_of_the_selected_sets_own_identity(
    fixture: ReadScopeFixture,
) -> None:
    """The manifest digest covers the reasons, so two selections reached differently differ.

    ``_manifest_digest`` covers each primary item's kind, identity **and selection reasons**, and
    the ledger records why that needed a case: a reviewer measured 42 passing cases with the
    reasons removed from the digest, so the reasons' contribution to the identity was a reachable
    line no case asserted (ledger A4, ``L7-V7``). The reason is load-bearing rather than
    decorative -- it is how a caller tells "this record was the seed" from "this record was
    reached because it is a member of a selected family" -- so removing it would silently collapse
    two different walks into one identity.

    The control is the other direction, and it is what keeps the assertion non-vacuous: two items
    whose reasons are *equal* must digest equally, and an item with no reasons must digest a third
    way. The real read then supplies the same property through the public boundary, and the
    assertion there is chosen so it *can* fail: it compares a real page's published
    ``manifest_digest`` against the digest of the same items **with their reasons stripped**, so a
    digest that did not cover the reasons would make the two equal and fail the case. Re-deriving
    the digest with the production helper would reproduce it by construction and prove nothing.
    """

    identity = "11111111-1111-4111-8111-111111111111"
    as_seed = _manifest_digest(
        [
            ReadItem(
                kind="invariant_revision",
                item_id=identity,
                selection_reasons=(SelectionReason(stage="seed_selected"),),
            )
        ]
    )
    as_member = _manifest_digest(
        [
            ReadItem(
                kind="invariant_revision",
                item_id=identity,
                selection_reasons=(SelectionReason(stage="member_of_selected_family"),),
            )
        ]
    )

    assert as_seed != as_member, (
        "two items with the same kind and identity but different selection reasons are two "
        "different facts about the walk, and the manifest digest is where that is recorded"
    )
    # The control: equal reasons digest equally, so the inequality above is about the reasons and
    # not about the digest being arbitrary.
    assert as_seed == _manifest_digest(
        [
            ReadItem(
                kind="invariant_revision",
                item_id=identity,
                selection_reasons=(SelectionReason(stage="seed_selected"),),
            )
        ]
    )
    # And reasons being *absent* is a third identity, not a failure and not silently the first.
    assert _manifest_digest([ReadItem(kind="invariant_revision", item_id=identity)]) not in {
        as_seed,
        as_member,
    }

    # The same property through the real boundary: re-deriving the digest from the reasons the
    # page itself reports reproduces the published digest exactly.
    result = read(
        fixture,
        InvariantRevisionSeed(
            invariant_id=fixture.retry_invariant_id, revision_id=fixture.base_revision_id
        ),
    )
    assert result.state == "page", result.refusal
    assert result.manifest_digest is not None
    assert result.page is not None
    assert not any(item.selection_reasons == () for item in result.page.items), (
        "the property is only measured if the real selection actually carries reasons"
    )
    # The discriminating public-boundary assertion. Re-deriving the digest with the production
    # helper would reproduce it by construction and could not fail, so this instead measures the
    # published digest against the digest of the **same items with their reasons stripped**: if the
    # reasons were not covered, the two would be equal, and this asserts they are not.
    reasons_stripped = _manifest_digest(
        [ReadItem(kind=item.kind, item_id=item.item_id) for item in result.page.items]
    )
    assert result.manifest_digest != reasons_stripped, (
        "the published manifest digest must differ from the digest of the same page items with "
        "their selection reasons stripped, or the reasons are not part of the identity"
    )


def _encode_cursor(cursor: KnowledgeReadCursor) -> str:
    """Encode one cursor the way the read hands a continuation back."""

    return base64.urlsafe_b64encode(cursor.model_dump_json().encode("utf-8")).decode("ascii")


def _digest(fixture: ReadScopeFixture) -> str:
    connection = open_read_only_database(fixture.database_path)
    try:
        return logical_digest(connection, SCHEMA_NAME)
    finally:
        connection.close()
