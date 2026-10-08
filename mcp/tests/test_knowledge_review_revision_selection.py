"""The explicit revision comparison: heads from authored successors, never from order or presence.

These cases drive the **real** two-snapshot comparison over **real** snapshot pairs built through
the public store operations, then run the exact head-selection function the review adapter calls.
Nothing here re-implements a read, a comparison or a predecessor lookup, fakes a snapshot, or
asserts a rendering it did not read back -- except the two fault-injection cases, which say so in
their own docstrings: a corrupt snapshot cannot be authored through the validating write path, so
the corruption is inserted as SQL into a scratch copy and the case asserts the read path refuses
to select from it.

The load-bearing properties, one case each:

* a unique multi-step chain defaults to the first before head versus the last after head
  (before ``r1`` and after ``r1 -> r2 -> r3`` is ``r1`` versus ``r3``; an advanced before head
  ``r2`` moves the default to ``r2`` versus ``r3``), with the exact selected revision ids
  recorded;
* an intermediate revision stays selectable history through the comparison's own per-side
  explicit revision selector;
* a fork on either side is an explicit ambiguity naming every head -- never an arbitrary winner;
* a known-empty side stays a one-sided addition or removal with the nonempty side's head
  recorded, which is ICR-R06@v1's contract reused rather than restated;
* a successor cycle and a dangling authored edge are unresolved selections with no pair
  recorded -- no timestamp, no similarity and no collection order fabricates a newest version;
* the review pane renders the selected head pair's own recorded statements.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import apsw
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
    InvariantRevisionSeed,
)
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.review import ReviewSurfaceRequest
from agents_remember.models.knowledge.revision_selection import ReviewRevisionSelection
from knowledge_rows_test_support import (
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
    """The revisions one snapshot file holds: their ids, texts and predecessor links."""

    revision_ids: tuple[str, ...]
    statements: tuple[str, ...]
    predecessors: dict[str, tuple[str, ...]]


def _build_snapshot(
    database_path: Path,
    repository_id: str,
    invariant_id: str,
    spec: SnapshotSpec,
    authorship: Authorship,
) -> None:
    """Author one snapshot's revisions through the public store operations, in chain order."""

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
                        predecessors=spec.predecessors.get(revision_id, ()),
                        provenance=authorship,
                    ),
                )
            )
            assert revision.state == "created", revision.refusal
    finally:
        store.close()


def _build_pair(
    directory: Path,
    *,
    before_count: int,
    after_count: int,
    fork: bool = False,
) -> ChainPair:
    """Build a before/after snapshot pair sharing the first ``before_count`` chain revisions.

    The after snapshot holds the full chain (or the fork when ``fork`` is set); the before
    snapshot holds its prefix. Every shared revision carries the same id and the same sealed
    fields in both files, authored under one provenance envelope.
    """

    repository_id = str(uuid4())
    invariant_id = str(uuid4())
    total = max(before_count, after_count)
    revision_ids = tuple(str(uuid4()) for _ in range(total))
    statements = STATEMENTS[:total]
    authorship = _authorship()
    if fork:
        predecessors = {revision_id: (revision_ids[0],) for revision_id in revision_ids[1:]}
    else:
        predecessors = {
            revision_ids[index]: (revision_ids[index - 1],) for index in range(1, total)
        }
    directory.mkdir(parents=True, exist_ok=True)
    before_database = directory / "before.db"
    after_database = directory / "after.db"
    _build_snapshot(
        before_database,
        repository_id,
        invariant_id,
        SnapshotSpec(
            revision_ids=revision_ids[:before_count],
            statements=statements[:before_count],
            predecessors=predecessors,
        ),
        authorship,
    )
    _build_snapshot(
        after_database,
        repository_id,
        invariant_id,
        SnapshotSpec(
            revision_ids=revision_ids[:after_count],
            statements=statements[:after_count],
            predecessors=predecessors,
        ),
        authorship,
    )
    return ChainPair(
        repository_id=repository_id,
        invariant_id=invariant_id,
        before_database=before_database,
        after_database=after_database,
        revision_ids=revision_ids,
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
        repository_id=pair.repository_id,
        before_database=pair.before_database,
        after_database=pair.after_database,
    )
    assert selected.selection is not None
    return selected.selection


def test_a_unique_chain_defaults_to_the_first_before_head_versus_the_last_after_head(
    tmp_path: Path,
) -> None:
    """Before ``r1`` and after ``r1 -> r2 -> r3`` defaults to ``r1`` versus ``r3``.

    The pair comes from the authored successor edges alone: ``r1`` is present on both sides
    yet is not chosen for the after side, and the stream position of ``r3`` participates
    nowhere -- the selection names stored revision identities, not first or last items.
    """

    pair = _build_pair(tmp_path / "chain", before_count=1, after_count=3)
    selection = _select(pair, _compare(pair))

    assert selection.state == "compared"
    assert selection.before_revision_id == pair.revision_ids[0]
    assert selection.after_revision_id == pair.revision_ids[2]
    assert selection.before_heads == (pair.revision_ids[0],)
    assert selection.after_heads == (pair.revision_ids[2],)
    assert sorted(selection.after_retained) == sorted(pair.revision_ids)
    assert pair.revision_ids[0] in selection.statement
    assert pair.revision_ids[2] in selection.statement


def test_an_advanced_before_head_moves_the_default_pair(tmp_path: Path) -> None:
    """Before ``r1 -> r2`` and after ``r1 -> r2 -> r3`` defaults to ``r2`` versus ``r3``."""

    pair = _build_pair(tmp_path / "advanced", before_count=2, after_count=3)
    selection = _select(pair, _compare(pair))

    assert selection.state == "compared"
    assert selection.before_revision_id == pair.revision_ids[1]
    assert selection.after_revision_id == pair.revision_ids[2]


def test_an_intermediate_revision_stays_selectable_history(tmp_path: Path) -> None:
    """The middle revision of a chain is addressable through the per-side explicit selector.

    The default still compares the heads, and the intermediate revision is compared on demand:
    addressing ``r2`` on the after side selects exactly ``r1`` versus ``r2`` with ``r2``'s own
    recorded text -- history that can be opened, not a count that can only be read.
    """

    pair = _build_pair(tmp_path / "history", before_count=1, after_count=3)
    result = diff_knowledge_scope(
        KnowledgeDiffRequest(
            selector=_identity_seed(pair),
            before=KnowledgeDiffSide(
                context=open_diff_side(pair.before_database, pair.repository_id)
            ),
            after=KnowledgeDiffSide(
                context=open_diff_side(pair.after_database, pair.repository_id),
                selector=InvariantRevisionSeed(
                    invariant_id=pair.invariant_id, revision_id=pair.revision_ids[1]
                ),
            ),
        ),
        before_path=pair.before_database,
        after_path=pair.after_database,
    )
    assert result.state == "page", result.refusal
    assert result.page is not None
    selection = select_subject_revisions(
        result.page.items,
        _identity_seed(pair),
        repository_id=pair.repository_id,
        before_database=pair.before_database,
        after_database=pair.after_database,
    ).selection
    assert selection is not None
    assert selection.state == "compared"
    assert selection.before_revision_id == pair.revision_ids[0]
    assert selection.after_revision_id == pair.revision_ids[1]


def test_a_fork_on_the_after_side_is_an_explicit_ambiguity(tmp_path: Path) -> None:
    """Two retained successors name no winner: the selection lists both heads and no pair."""

    pair = _build_pair(tmp_path / "fork", before_count=1, after_count=3, fork=True)
    selection = _select(pair, _compare(pair))

    assert selection.state == "ambiguous"
    assert selection.before_revision_id is None
    assert selection.after_revision_id is None
    assert selection.before_heads == (pair.revision_ids[0],)
    assert sorted(selection.after_heads) == sorted(pair.revision_ids[1:])
    assert pair.revision_ids[1] in selection.statement
    assert pair.revision_ids[2] in selection.statement


def test_a_fork_on_the_before_side_is_an_explicit_ambiguity(tmp_path: Path) -> None:
    """Ambiguity is per side: a branched before snapshot is ambiguous against a linear after."""

    pair = _build_pair(tmp_path / "before-fork", before_count=3, after_count=1, fork=True)
    selection = _select(pair, _compare(pair))

    assert selection.state == "ambiguous"
    assert selection.before_revision_id is None
    assert selection.after_revision_id is None
    assert sorted(selection.before_heads) == sorted(pair.revision_ids[1:])
    assert selection.after_heads == (pair.revision_ids[0],)


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
        repository_id=pair.repository_id,
        before_database=pair.before_database,
        after_database=void_database,
    ).selection
    assert selection is not None
    assert selection.state == "removed"
    assert selection.before_revision_id == pair.revision_ids[0]
    assert selection.after_revision_id is None
    assert selection.after_heads == ()
    assert "removal" in selection.statement


def test_a_successor_cycle_is_unresolved_and_names_no_pair(tmp_path: Path) -> None:
    """A cycle leaves no head; the selection is unresolved rather than a newest version.

    The cycle cannot be authored through the validating write path -- the second edge closes a
    loop no authoring order reaches -- so it is inserted as SQL into a scratch copy, and the
    case first proves the surgery landed. What is asserted is the read path: two recorded
    revisions, every one naming a successor, zero heads, no pair, no timestamp, no similarity.
    """

    pair = _build_pair(tmp_path / "cycle", before_count=1, after_count=2)
    first, second = pair.revision_ids[0], pair.revision_ids[1]
    cyclic = tmp_path / "cyclic.db"
    cyclic.write_bytes(pair.after_database.read_bytes())
    connection = apsw.Connection(str(cyclic))
    with connection:
        connection.execute(
            "INSERT INTO invariant_predecessor "
            "(repository_id, invariant_id, child_revision_id, parent_revision_id) "
            "VALUES (?, ?, ?, ?)",
            (pair.repository_id, pair.invariant_id, first, second),
        )
    connection.close()
    check = apsw.Connection(str(cyclic))
    try:
        edges = {
            (str(row[0]), str(row[1]))
            for row in check.execute(
                "SELECT child_revision_id, parent_revision_id FROM invariant_predecessor "
                "WHERE repository_id = ?",
                (pair.repository_id,),
            )
        }
    finally:
        check.close()
    assert (first, second) in edges and (second, first) in edges, "the cycle surgery landed"
    assert revision_heads((first, second), tuple(sorted(edges))) == ()

    cyclic_pair = ChainPair(
        repository_id=pair.repository_id,
        invariant_id=pair.invariant_id,
        before_database=pair.before_database,
        after_database=cyclic,
        revision_ids=pair.revision_ids,
        statements=pair.statements,
    )
    selection = _select(cyclic_pair, _compare(cyclic_pair))

    assert selection.state == "unresolved"
    assert selection.before_revision_id is None
    assert selection.after_revision_id is None
    assert "cyclic" in selection.statement


def test_a_dangling_authored_edge_is_unresolved_and_names_the_missing_revision(
    tmp_path: Path,
) -> None:
    """An edge to a revision the snapshot does not retain is an invalid graph, not a head.

    The dangling edge cannot be committed through the validating write path -- its foreign key
    would fail -- so it is inserted with key enforcement off in a scratch copy, and the case
    first proves the surgery landed. The read path then refuses to establish a head from it.
    """

    pair = _build_pair(tmp_path / "dangling", before_count=1, after_count=2)
    missing = str(uuid4())
    second = pair.revision_ids[1]
    corrupt = tmp_path / "corrupt.db"
    corrupt.write_bytes(pair.after_database.read_bytes())
    connection = apsw.Connection(str(corrupt))
    with connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "INSERT INTO invariant_predecessor "
            "(repository_id, invariant_id, child_revision_id, parent_revision_id) "
            "VALUES (?, ?, ?, ?)",
            (pair.repository_id, pair.invariant_id, second, missing),
        )
    connection.close()
    check = apsw.Connection(str(corrupt))
    try:
        edges = {
            (str(row[0]), str(row[1]))
            for row in check.execute(
                "SELECT child_revision_id, parent_revision_id FROM invariant_predecessor "
                "WHERE repository_id = ?",
                (pair.repository_id,),
            )
        }
    finally:
        check.close()
    assert (second, missing) in edges, "the dangling-edge surgery landed"

    corrupt_pair = ChainPair(
        repository_id=pair.repository_id,
        invariant_id=pair.invariant_id,
        before_database=pair.before_database,
        after_database=corrupt,
        revision_ids=pair.revision_ids,
        statements=pair.statements,
    )
    selection = _select(corrupt_pair, _compare(corrupt_pair))

    assert selection.state == "unresolved"
    assert selection.before_revision_id is None
    assert selection.after_revision_id is None
    assert missing in selection.statement


def test_heads_come_from_successors_not_from_sorted_order() -> None:
    """A head that sorts last is still the head: collection order selects nothing.

    The successor ``z`` names ``m`` as its predecessor, so ``m`` has a recorded successor and
    ``z`` -- which sorts after it -- is the only head. A rule that picked the first stored id
    would answer ``m``; the authored edge answers ``z``.
    """

    assert revision_heads(("m-revision", "z-revision"), (("z-revision", "m-revision"),)) == (
        "z-revision",
    )
    assert revision_heads(("m-revision", "z-revision"), ()) == ("m-revision", "z-revision")


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

    A three-step chain with two real trees goes through the production composition: the before
    operand is the before head's text, the after operand the after head's, and the recorded
    selection beside them names the exact pair -- so the old rule's identical-statements
    rendering would fail this case and the head rule passes it.
    """

    pair = _build_pair(tmp_path / "pane", before_count=1, after_count=3)
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
    assert selection.after_revision_id == pair.revision_ids[2]
    assert knowledge.before_statement.state == "present"
    assert knowledge.after_statement.state == "present"
    assert knowledge.before_statement.text == pair.statements[0]
    assert knowledge.after_statement.text == pair.statements[2]
