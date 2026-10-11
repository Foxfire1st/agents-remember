"""The explicit revision comparison: heads from retained revisions, never from order or presence.

These cases drive the **real** two-snapshot comparison over **real** snapshot pairs built through
the text-backed builder, then run the exact head-selection function the review adapter calls.
Nothing here re-implements a read, a comparison or a predecessor lookup, fakes a snapshot, or
asserts a rendering it did not read back -- except the fault-injection shape the void case
names: an empty side cannot be authored as a text revision, so it is an empty built file.

A text record holds exactly one revision (MIK-R74 rule 3), so one side retaining several
revisions is not a shape a text tree can express: the multi-revision cases below name their
removal rather than asserting it. What the selection still answers -- the single retained
revision per side, the one-sided removal with its recorded head, and the retained population
in sorted order -- is pinned here.

The load-bearing properties, one case each:

* a unique single retained revision per side compares head to head, with the exact selected
  revision ids recorded;
* a known-empty side stays a one-sided addition or removal with the nonempty side's head
  recorded, which is ICR-R06@v1's contract reused rather than restated;
* heads are the retained population in sorted order: collection order selects nothing;
* the review pane renders the selected head pair's own recorded statements.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_diff import diff_knowledge_scope, open_diff_side
from agents_remember.application.knowledge_review import (
    ReviewCandidateResolution,
    compose_review,
)
from agents_remember.application.review_revision_comparison import (
    revision_heads,
    select_subject_revisions,
)
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.diff import (
    KnowledgeDiffRequest,
    KnowledgeDiffResult,
    KnowledgeDiffSide,
)
from agents_remember.models.knowledge.read import (
    InvariantIdentitySeed,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection
from knowledge_rows_test_support import (
    INDEX_REPOSITORY_ID,
    InvariantRequest,
    RevisionDraft,
    RevisionRequest,
    open_knowledge_store,
)

pytestmark = pytest.mark.evidence_unit

STATEMENTS = (
    "The first statement governs the initial write.",
    "The second statement narrows the admitted scope.",
    "The third statement records the refusal explicitly.",
    "The forked statement takes the other branch.",
)


def _authorship() -> Authorship:
    """One provenance envelope shared by both snapshots of a pair, so shared revisions seal alike."""

    return Authorship(
        actor_ref="agent:icr-r07",
        authorization_ref="ICR-R07@v1 worker evidence",
        operation_id=uuid4(),
        recorded_at=datetime.now(UTC).isoformat(),
        origin_refs=("requirement:ICR-R07@v1",),
    )


@dataclass(frozen=True)
class ChainPair:
    """Two real snapshots sharing one namespace and one invariant identity."""

    repository_id: str
    invariant_id: str
    before_database: Path
    after_database: Path
    revision_ids: tuple[str, ...]
    statements: tuple[str, ...]


@dataclass(frozen=True)
class SnapshotSpec:
    """The revisions one snapshot file holds: their ids and texts."""

    revision_ids: tuple[str, ...]
    statements: tuple[str, ...]


def _build_snapshot(
    database_path: Path,
    repository_id: str,
    invariant_id: str,
    spec: SnapshotSpec,
    authorship: Authorship,
) -> tuple[str, tuple[str, ...]]:
    """Author one snapshot's revision through the text-backed builder, and return its projection.

    The returned identities are the projection's own UUIDs for the text it wrote, because the
    selection names stored revision identities -- the caller-side UUIDs name nothing in a tree.
    """

    projected_revisions: list[str] = []
    projected_invariant = invariant_id
    store = open_knowledge_store(database_path, repository_id)
    try:
        created = store.create_repository(
            RepositoryIdentity(
                repository_id=repository_id,
                authority_home="agents-remember",
            )
        )
        assert created.state == "created", created.refusal
        invariant = store.create_invariant(
            InvariantRequest(
                repository_id=repository_id,
                invariant_id=invariant_id,
                display_label="chain-invariant",
                provenance=authorship,
            )
        )
        assert invariant.state == "created", invariant.refusal
        projected_invariant = invariant.invariant_id
        for revision_id, statement in zip(spec.revision_ids, spec.statements, strict=True):
            revision = store.create_revision(
                RevisionRequest(
                    repository_id=repository_id,
                    revision=RevisionDraft(
                        revision_id=revision_id,
                        invariant_id=invariant_id,
                        display_version="v1",
                        statement=statement,
                        applicability="Every admitted candidate write.",
                        conditions=("The candidate write is admitted.",),
                        exclusions=("Historical rows are not rewritten.",),
                        provenance=authorship,
                    ),
                )
            )
            assert revision.state == "created", revision.refusal
            projected_revisions.append(revision.revision_id)
    finally:
        store.close()
    return projected_invariant, tuple(projected_revisions)


def _build_pair(
    directory: Path,
    *,
    before_count: int,
    after_count: int,
) -> ChainPair:
    """Build a before/after snapshot pair sharing the first ``before_count`` revisions.

    Both counts are one: a text record holds exactly one revision (MIK-R74 rule 3), so the
    pair shares its single retained revision. Every shared revision carries the same id and
    the same sealed fields in both files, authored under one provenance envelope.
    """

    assert before_count == 1 and after_count == 1, (
        "a text pair retains exactly one revision per side; "
        "the multi-revision cases name their removal below"
    )
    repository_id = INDEX_REPOSITORY_ID
    invariant_id = str(uuid4())
    revision_ids = (str(uuid4()),)
    statements = STATEMENTS[:1]
    authorship = _authorship()
    directory.mkdir(parents=True, exist_ok=True)
    before_database = directory / "before.db"
    after_database = directory / "after.db"
    projected_invariant, before_revisions = _build_snapshot(
        before_database,
        repository_id,
        invariant_id,
        SnapshotSpec(
            revision_ids=revision_ids[:before_count],
            statements=statements[:before_count],
        ),
        authorship,
    )
    _, after_revisions = _build_snapshot(
        after_database,
        repository_id,
        invariant_id,
        SnapshotSpec(
            revision_ids=revision_ids[:after_count],
            statements=statements[:after_count],
        ),
        authorship,
    )
    assert before_revisions == after_revisions, (
        "the same text projects the same identities in both files, "
        "which is what makes the shared revision shared"
    )
    return ChainPair(
        repository_id=repository_id,
        invariant_id=projected_invariant,
        before_database=before_database,
        after_database=after_database,
        revision_ids=before_revisions,
        statements=statements,
    )


def _identity_seed(pair: ChainPair) -> InvariantIdentitySeed:
    return InvariantIdentitySeed(invariant_id=pair.invariant_id)


def _compare(pair: ChainPair) -> KnowledgeDiffResult:
    """Run the shipped comparison of one pair with no code trees, failing loudly on refusal."""

    result = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=_identity_seed(pair),
            before=KnowledgeDiffSide(
                context=open_diff_side(pair.before_database, pair.repository_id)
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(pair.after_database, pair.repository_id)
            ),
        ),
        before_path=pair.before_database,
        after_path=pair.after_database,
    )
    assert result.state == "page", result.refusal
    assert result.page is not None
    return result


def _select(pair: ChainPair, result: KnowledgeDiffResult) -> ReviewRevisionSelection:
    """Run the adapter's own head selection over one comparison's union items."""

    assert result.page is not None
    selected = select_subject_revisions(
        result.page.items,
        _identity_seed(pair),
    )
    assert selected.selection is not None
    return selected.selection


def test_a_single_retained_revision_pair_compares_head_to_head(
    tmp_path: Path,
) -> None:
    """One retained revision per side compares head to head.

    Every retained revision is a head (MIK-R74 rule 1: text records hold no predecessor edge),
    so a side retaining exactly one revision establishes exactly one head, and the pair
    compares. The selection names stored revision identities, not first or last items.
    """

    pair = _build_pair(tmp_path / "chain", before_count=1, after_count=1)
    selection = _select(pair, _compare(pair))

    assert selection.state == "compared"
    assert selection.before_revision_id == pair.revision_ids[0]
    assert selection.after_revision_id == pair.revision_ids[0]
    assert selection.before_heads == (pair.revision_ids[0],)
    assert selection.after_heads == (pair.revision_ids[0],)
    assert pair.revision_ids[0] in selection.statement


# Dead: a text record holds exactly one revision (MIK-R74 rule 3), so one side retaining
# several revisions is not a shape a text tree can express. The multi-revision cases below
# name their removal rather than asserting it; the single retained revision per side above,
# the one-sided removal, the sorted heads and the pane statements still pin the selection.
_REMOVED_MULTI_REVISION_CASES = (
    "test_a_second_retained_revision_is_ambiguity_not_a_default",
    "test_an_intermediate_revision_stays_selectable_history",
    "test_a_fork_on_the_after_side_is_an_explicit_ambiguity",
    "test_a_fork_on_the_before_side_is_an_explicit_ambiguity",
)


def test_a_known_empty_after_side_stays_a_removal_with_the_before_head_recorded(
    tmp_path: Path,
) -> None:
    """A side that retains no revision of the identity is a one-sided removal, not a gap.

    The after snapshot is a real dataset under the same namespace that simply never recorded
    the invariant, so the before head is shown and recorded as a removal -- the one-sided
    contract ICR-R06@v1 owns, with the head id this leaf adds to it. The union reports the
    revision as absent from the snapshot rather than as a missing selection.
    """

    pair = _build_pair(tmp_path / "removal", before_count=1, after_count=1)
    void_database = tmp_path / "void.db"
    void_store = open_knowledge_store(void_database, pair.repository_id)
    try:
        created = void_store.create_repository(
            RepositoryIdentity(
                repository_id=pair.repository_id,
                authority_home="agents-remember",
            )
        )
        assert created.state == "created", created.refusal
    finally:
        void_store.close()
    result = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=_identity_seed(pair),
            before=KnowledgeDiffSide(
                context=open_diff_side(pair.before_database, pair.repository_id)
            ),
            after=KnowledgeDiffSide(context=open_diff_side(void_database, pair.repository_id)),
        ),
        before_path=pair.before_database,
        after_path=void_database,
    )
    assert result.state == "page", result.refusal
    assert result.page is not None
    assert {item.coverage for item in result.page.items if item.kind == "invariant"} == {
        "absent_from_snapshot"
    }
    selection = select_subject_revisions(
        result.page.items,
        _identity_seed(pair),
    ).selection
    assert selection is not None
    assert selection.state == "removed"
    assert selection.before_revision_id == pair.revision_ids[0]
    assert selection.after_revision_id is None
    assert selection.after_heads == ()
    assert "removal" in selection.statement


def test_heads_are_the_retained_population_in_sorted_order() -> None:
    """Heads are every retained revision, in sorted order: collection order selects nothing.

    Text records hold no predecessor edge (MIK-R74 rule 1), so no recorded successor relation
    narrows the population. A rule that picked the first stored id would answer ``m`` here;
    the head rule answers both, sorted.
    """

    assert revision_heads(("m-revision", "z-revision")) == ("m-revision", "z-revision")
    assert revision_heads(("z-revision", "m-revision")) == ("m-revision", "z-revision")


def _git_tree(root: Path, files: dict[str, str]) -> str:
    """Commit one tiny real tree and return its tree id, through real Git."""

    root.mkdir(parents=True, exist_ok=True)
    _git(root, ["init", "-q", "--initial-branch=main"])
    _git(root, ["config", "user.email", "icr-r07@example.invalid"])
    _git(root, ["config", "user.name", "icr-r07"])
    for path, text in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git(root, ["add", "-A"])
    _git(root, ["commit", "-q", "-m", "chain tree"])
    return _git(root, ["rev-parse", "HEAD^{tree}"])


def _git(root: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env={
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(root),
            "GIT_CONFIG_NOSYSTEM": "1",
        },
    )
    assert result.returncode == 0, f"fixture git {' '.join(args)} failed: {result.stderr.strip()}"
    return result.stdout.strip()


def test_the_review_pane_renders_the_selected_head_pairs_own_statements(tmp_path: Path) -> None:
    """The pane's operands are the selected heads' recorded statements, through the adapter.

    A pair with one retained revision per side goes through the production composition: the before
    operand is the before head's text, the after operand the after head's, and the recorded
    selection beside them names the exact pair.
    """

    pair = _build_pair(tmp_path / "pane", before_count=1, after_count=1)
    before_root = tmp_path / "before-code"
    after_root = tmp_path / "after-code"
    before_tree = _git_tree(before_root, {"src/a.py": "# before\n"})
    after_tree = _git_tree(after_root, {"src/a.py": "# after\n"})
    resolution = ReviewCandidateResolution(
        repository_id=pair.repository_id,
        leaf_id="260921-icr-l7",
        baseline_database=pair.before_database,
        candidate_database=pair.after_database,
        baseline_code_root=before_root,
        candidate_code_root=after_root,
        baseline_code_tree_id=before_tree,
        candidate_code_tree_id=after_tree,
    )
    result = compose_review(
        resolution,
        ReviewSurfaceRequest(
            repository_id=pair.repository_id,
            master="260921_complete-code-and-intent-review",
            leaf_id="260921-icr-l7",
            selector=_identity_seed(pair),
        ),
    )
    assert result.state == "review", result.refusal
    assert result.payload is not None
    knowledge = result.payload.knowledge
    selection = knowledge.revision_selection
    assert selection is not None
    assert selection.state == "compared"
    assert selection.before_revision_id == pair.revision_ids[0]
    assert selection.after_revision_id == pair.revision_ids[0]
    assert knowledge.before_statement.state == "present"
    assert knowledge.after_statement.state == "present"
    assert knowledge.before_statement.text == pair.statements[0]
    assert knowledge.after_statement.text == pair.statements[0]
