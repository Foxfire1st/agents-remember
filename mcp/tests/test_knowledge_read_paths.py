"""What a *path* is to this read: an address, not a pattern.

These cases are the path half of the read's real boundaries, and they are a separate module rather
than more cases in ``test_knowledge_read_boundaries.py`` because the file-size rail is a hard limit:
the read's own boundary module was already at 1163 of the 1200 lines the repository allows a test
module, so the path questions moved out rather than pushing it over.

The subject is one rule stated twice, once per direction:

* a spelling Git treats as **magic** -- the leading-``:`` forms such as ``:(exclude)…``, ``:!…``,
  ``:(top)…`` and ``:/…`` -- is refused at both typed path boundaries, because Git answers it with a
  non-zero exit or with a *different location*, and the read would otherwise publish that answer as
  an absence it never observed;
* a spelling Git addresses **literally** -- a real path containing ``*``, ``?`` or ``[`` -- is
  admitted, stored, seeded and resolved, because refusing it would make a legitimate anchor
  un-authorable and would report a file the tree really holds as ``path_absent``.

Both directions are measured against real Git (2.54.0 on this host) and against a real database, so
the premise of each case is evidence rather than prose.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest
from agents_remember.application.knowledge_read import (
    open_read_context,
    read_knowledge_scope,
)
from agents_remember.memory.knowledge import read_anchors, realizations
from agents_remember.memory.knowledge.connection import open_database
from agents_remember.memory.knowledge.read_anchors import _confined_posix_relative
from agents_remember.memory.knowledge.store import open_knowledge_store
from agents_remember.models.knowledge.candidate import SnapshotIdentity
from agents_remember.models.knowledge.graph import RealizationClaimDraft
from agents_remember.models.knowledge.read import (
    AnchorResolution,
    InvariantRevisionSeed,
    KnowledgeReadContext,
    KnowledgeReadRequest,
    KnowledgeReadResult,
    KnowledgeReadSeed,
    PathSeed,
)
from agents_remember.models.knowledge.result import NewAnchor, RealizationClaimRequest
from agents_remember.models.knowledge.source import (
    FileLocator,
    GitBlobIdentity,
    SourceAnchorDraft,
)
from pydantic import ValidationError
from read_scope_test_support import (
    INTEGRATION_PATH,
    ReadScopeFixture,
    build_read_scope_fixture,
)

pytestmark = pytest.mark.integration

SCHEMA_NAME = "ar-knowledge-sqlite/v1"
GIT_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_CONFIG_NOSYSTEM": "1",
}


@pytest.fixture
def fixture(tmp_path: Path) -> ReadScopeFixture:
    """One fresh fixture, with its own committed Git tree, per case."""

    return build_read_scope_fixture(tmp_path / "read-scope")


def read(
    fixture: ReadScopeFixture,
    seed: KnowledgeReadSeed,
) -> KnowledgeReadResult:
    """Run one read of the fixture at its anchored context."""

    return read_knowledge_scope(
        fixture.database_path,
        open_read_context(
            fixture.database_path,
            fixture.repository_id,
            repository_root=fixture.git_root,
            code_tree_id=fixture.git_tree_id,
        ),
        KnowledgeReadRequest(seed=seed),
    )


def subject_seed(fixture: ReadScopeFixture) -> InvariantRevisionSeed:
    """The exact subject revision every anchor case reads."""

    return InvariantRevisionSeed(
        invariant_id=fixture.retry_invariant_id, revision_id=fixture.subject_revision_id
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


def _ls_tree(git_root: Path, tree_id: str, path: str) -> tuple[int, str]:
    """Return ``(returncode, stdout)`` for one literal ``git ls-tree`` question about ``path``.

    The cases below are about what Git does with a *spelling*, so they ask Git rather than assert a
    belief about it: the same command the read path runs, with the same environment the fixture uses.
    """

    completed = subprocess.run(
        ["git", "ls-tree", "-z", tree_id, "--", path],
        cwd=git_root,
        capture_output=True,
        text=True,
        check=False,
        env={**GIT_ENVIRONMENT, "HOME": str(git_root)},
    )
    return completed.returncode, completed.stdout


def _admitted[BoundaryValue](
    reason: str, model: Callable[..., BoundaryValue], **values: object
) -> BoundaryValue:
    """Construct one value through a typed boundary, failing as an *assertion* if it is refused.

    A case that measures an admission must not die of the refusal it is checking for: an escaping
    ``ValidationError`` is an exception death, which is weaker evidence than a failed assertion
    because it does not say which claim was violated. This turns the refusal into that assertion
    while carrying the boundary's own message, so an ablation that re-narrows the boundary fails
    here on an assertion instead of vanishing into a construction error.
    """

    try:
        return model(**values)
    except ValidationError as refusal:
        raise AssertionError(f"{reason}: the boundary refused the value -- {refusal}") from refusal


def _git_commit_tree(git_root: Path, paths: tuple[str, ...]) -> str:
    """Commit one fresh repository holding ``paths`` and return its tree id."""

    git_root.mkdir(parents=True, exist_ok=True)
    for args in (
        ["init", "-q", "--initial-branch=main"],
        ["config", "user.email", "fixture@example.invalid"],
        ["config", "user.name", "read fixture"],
    ):
        subprocess.run(["git", *args], cwd=git_root, capture_output=True, text=True, check=True)
    for path in paths:
        target = git_root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {path}\n", encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-q", "-m", "fixture tree"]):
        subprocess.run(["git", *args], cwd=git_root, capture_output=True, text=True, check=True)
    committed = subprocess.run(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=git_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return committed.stdout.strip()


def test_a_pathspec_magic_spelling_is_refused_by_the_typed_path_and_never_answered_as_absence(
    fixture: ReadScopeFixture,
) -> None:
    """Pathspec *magic* is refused where a path is authored, and a legitimate spelling is not.

    A stored path is handed to ``git ls-tree`` as an argument, and Git reads a leading ``:`` as
    pathspec magic: ``:(exclude)src/x.py`` and ``:!src/x.py`` make the command exit non-zero with
    "pathspec magic not supported by this command", and ``:(top)…``/``:/…`` answer about a
    *different* location rather than about the confined path the record names. Both facts are
    measured here against the fixture's own tree, and the spelling is then refused at the typed
    boundaries -- the anchor *write* path and the path *seed* -- so a caller can never author a
    record whose read-back would publish an answer Git gave about something else.

    The glob characters are the deliberate other half. ``git ls-tree`` addresses ``*``, ``?`` and
    ``[`` literally -- measured here on the fixture's real tree -- so a repository really can hold
    such a path, and refusing it would make a legitimate anchor un-authorable and un-seedable and
    would report a file the tree holds as ``path_absent``. This case therefore measures the
    admission as well as the refusal, end to end: the anchor is authored through the typed write
    path, stored, selected by a path seed, and read back as the exact recorded blob.
    """

    for spelling in (":(exclude)src/nothing.py", ":!src/nothing.py"):
        returncode, stdout = _ls_tree(fixture.git_root, fixture.git_tree_id, spelling)
        assert returncode != 0 and stdout == "", (
            "Git refuses the pathspec spelling instead of answering it, which is why it must not "
            "be admitted as a path"
        )
    for spelling in (":/src/integration.py", ":(top)src/integration.py"):
        returncode, stdout = _ls_tree(fixture.git_root, fixture.git_tree_id, spelling)
        assert returncode == 0 and f"\t{INTEGRATION_PATH}\0" in stdout, (
            "Git answers the top-level spellings about another location, so they are not the "
            "confined path a record names"
        )
    for spelling in (
        ":(exclude)src/nothing.py",
        ":!src/nothing.py",
        ":/src/integration.py",
        ":(top)src/integration.py",
    ):
        with pytest.raises(ValidationError):
            SourceAnchorDraft(
                anchor_id=uuid4(),
                path=spelling,
                source_identity=GitBlobIdentity(object_id=fixture.git_blobs[INTEGRATION_PATH]),
                locator=FileLocator(),
            )
        with pytest.raises(ValidationError):
            PathSeed(path=spelling)
    # The admission is asserted *before* any refusal, so a boundary that refused a glob character
    # again fails here on an assertion rather than further down inside a construction.
    admitted_draft = _admitted(
        "the write path admits a glob character, which git ls-tree addresses literally",
        SourceAnchorDraft,
        anchor_id=uuid4(),
        path="src/[a]dapter.py",
        source_identity=GitBlobIdentity(object_id=fixture.git_blobs[INTEGRATION_PATH]),
        locator=FileLocator(),
    )
    assert admitted_draft.path == "src/[a]dapter.py"
    assert (
        _admitted(
            "a seed is an address, and a path containing * is one git ls-tree answers literally",
            PathSeed,
            path="src/*.py",
        ).path
        == "src/*.py"
    )
    for spelling in (
        ":",
        ":src/integration.py",
        "/src/integration.py",
        "src/../integration.py",
        "src\\integration.py",
    ):
        assert _confined_posix_relative(spelling) is None
    assert _confined_posix_relative(INTEGRATION_PATH) == INTEGRATION_PATH


def test_a_path_holding_glob_characters_is_authorable_seedable_and_observed_as_its_blob(
    fixture: ReadScopeFixture,
    tmp_path: Path,
) -> None:
    """A real path containing ``[``, ``*`` or ``?`` is an address, not a pattern.

    Git's own answer is measured first: in a tree that holds both ``src/a1.py`` and ``src/a[1].py``,
    ``git ls-tree`` resolves each spelling to its own entry with ``rc=0``, and a bare ``src/*.py``
    matches nothing at all. The literal reading is what makes such a path a legitimate recorded
    location, so the whole chain is then exercised against it rather than reasoned about:

    1. the typed **write** path accepts an anchor whose path contains ``[1]``;
    2. the claim and its anchor are **stored** through the ordinary store operation;
    3. the typed **seed** boundary accepts the same path as a ``PathSeed``;
    4. the **read** path selects the claim and observes ``exact_recorded_blob`` -- not a spelling
       refusal, and not the false ``path_absent`` this path would earn under a glob-shaped refusal.

    A stored row whose path *is* unaddressable is measured in the same case, because that is the
    other half of the same rule: it is reported as an unresolvable recorded anchor rather than as a
    path the tree was asked about and did not hold.
    """

    literal_path = "src/a[1].py"
    sibling_path = "src/a1.py"
    question_path = "src/a?b.py"
    star_path = "src/a*b.py"
    tree_root = tmp_path / "literal-tree"
    tree_id = _git_commit_tree(tree_root, (sibling_path, literal_path, question_path, star_path))

    returncode, stdout = _ls_tree(tree_root, tree_id, literal_path)
    assert returncode == 0 and f"\t{literal_path}\0" in stdout, (
        "git ls-tree addresses the bracketed spelling literally, even beside src/a1.py"
    )
    returncode, stdout = _ls_tree(tree_root, tree_id, sibling_path)
    assert returncode == 0 and f"\t{sibling_path}\0" in stdout
    for spelling in (question_path, star_path):
        returncode, stdout = _ls_tree(tree_root, tree_id, spelling)
        assert returncode == 0 and f"\t{spelling}\0" in stdout, (
            "git ls-tree addresses ? and * literally too"
        )
    returncode, stdout = _ls_tree(tree_root, tree_id, "src/*.py")
    assert returncode == 0 and stdout == "", "a bare glob is accepted and matches nothing"

    # (1) and (3): both typed boundaries admit the literal spelling.
    anchor_id = uuid4()
    blob = subprocess.run(
        ["git", "rev-parse", f"{tree_id}:{literal_path}"],
        cwd=tree_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    draft = _admitted(
        "the write path admits a real path containing a glob character",
        SourceAnchorDraft,
        anchor_id=anchor_id,
        path=literal_path,
        source_identity=GitBlobIdentity(object_id=blob),
        locator=FileLocator(),
    )
    assert draft.path == literal_path
    assert (
        _admitted(
            "a path seed is an address, and a real path containing a glob character is one",
            PathSeed,
            path=literal_path,
        ).path
        == literal_path
    )

    # (2): store the claim and its anchor through the ordinary write path.
    claim_id = str(uuid4())
    store = open_knowledge_store(fixture.database_path, fixture.repository_id)
    try:
        created = realizations.create_realization_claim(
            store,
            RealizationClaimRequest(
                repository_id=fixture.repository_id,
                claim=RealizationClaimDraft(
                    claim_id=claim_id,
                    invariant_revision_id=fixture.resolution_revision_id,
                    role="incidental",
                    rationale="A realization at a path whose name holds a glob character.",
                ),
                anchor=NewAnchor(anchor=draft),
                provenance=fixture.authorship,
            ),
        )
    finally:
        store.close()
    assert created.state == "created", created.refusal

    # (4): the read observes the exact recorded blob at that path.
    result = read_knowledge_scope(
        fixture.database_path,
        KnowledgeReadContext(
            repository_id=fixture.repository_id,
            knowledge=SnapshotIdentity(
                repository_id=fixture.repository_id,
                logical_digest=fixture.knowledge_digest,
                schema_version=SCHEMA_NAME,
            ),
            repository_root=str(tree_root),
            code_tree_id=tree_id,
        ),
        KnowledgeReadRequest(seed=PathSeed(path=literal_path)),
    )
    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    assert observed[literal_path].resolution == "exact_recorded_blob"
    assert observed[literal_path].observed_source_identity == blob
    assert observed[literal_path].recorded_source_identity == blob


def test_a_stored_path_that_cannot_be_addressed_is_refused_rather_than_reported_absent(
    fixture: ReadScopeFixture,
) -> None:
    """A recorded path Git would read as magic is refused, and never answered as an absence.

    The typed boundaries refuse such a spelling, so the row this case reads is written past them --
    which is exactly the case the read path's own confinement check exists for. The row is inserted
    as a **copy** of a real stored anchor and claim (the same JSON payloads, a new identity, one
    changed path), so nothing but the spelling differs from a legitimate record. What matters is the
    answer: the anchor is *not* reported as ``path_absent`` ("the requested tree contains no entry
    at this path"), because the tree was never asked about that path. The claim keeps its recorded
    identity and is reported as an anchor this increment cannot resolve at the spelled location.
    """

    unaddressable = ":(exclude)src/integration.py"
    donor_claim_id = str(uuid4())
    connection = open_database(fixture.database_path)
    try:
        donor = connection.execute(
            "SELECT anchor_id, provenance FROM source_anchor WHERE repository_id = ? AND anchor_id = ?",
            (fixture.repository_id, fixture.integration.anchor_id),
        ).fetchone()
        assert donor is not None, "the fixture really stored the anchor this case copies"
        donor_anchor_id, provenance = donor
        stored_anchor_id = str(uuid4())
        connection.execute(
            "INSERT INTO source_anchor "
            "(repository_id, anchor_id, path, source_identity, locator, provenance) "
            "SELECT repository_id, ?, ?, source_identity, locator, ? FROM source_anchor "
            "WHERE repository_id = ? AND anchor_id = ?",
            (
                stored_anchor_id,
                unaddressable,
                provenance,
                fixture.repository_id,
                donor_anchor_id,
            ),
        )
        connection.execute(
            "INSERT INTO realization_claim "
            "(repository_id, claim_id, invariant_revision_id, anchor_id, role, rationale, provenance) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                fixture.repository_id,
                donor_claim_id,
                fixture.subject_revision_id,
                stored_anchor_id,
                "support",
                "A claim whose recorded path cannot be addressed against a tree.",
                provenance,
            ),
        )
    finally:
        connection.close()

    result = read(fixture, subject_seed(fixture))

    assert result.state == "page", result.refusal
    observed = anchors_by_path(result)
    assert unaddressable in observed, "the claim is still selected and still carries its path"
    refused = observed[unaddressable]
    assert refused.resolution != "path_absent", (
        "the tree was never asked about this path, so reporting it absent would be a false "
        "statement about the repository"
    )
    assert refused.resolution == "unsupported_locator"
    assert refused.detail.endswith(
        "this is a refusal of the spelling, not an observation that the tree lacks the entry"
    )
    assert refused.recorded_source_identity == fixture.git_blobs[INTEGRATION_PATH]
    assert refused.observed_source_identity is None
    assert result.page is not None
    assert donor_claim_id in {item.claim_id for item in result.page.items}


def test_a_failed_tree_lookup_is_unavailable_rather_than_an_absent_path(
    fixture: ReadScopeFixture,
) -> None:
    """A lookup Git could not answer is *unknown*, and unknown is not the same fact as absent.

    ``_tree_entry`` separates three outcomes that a single ``None`` used to collapse: Git answered
    with no entry (``path_absent``), the recorded spelling is not a confined tree path
    (``unsupported_locator``), and the lookup did not answer at all
    (``recorded_object_unavailable``). This case builds a **real, well-formed, stored** anchor and
    pins the first of the two producers of the third outcome: the request names a tree the
    repository does not hold, so ``observe_anchor``'s availability guard answers before ``ls-tree``
    is ever run, and its ``detail`` says so in as many words. That is asserted here, because it is
    what distinguishes this producer from the lookup that failed.

    The **other** producer — a lookup that ran and did not answer — is not reached by this case and
    is not claimed to be: on this host ``ls-tree`` answers every well-formed path *literally*, exit
    0, even for spellings that look like patterns (measured: a path such as ``src/d?b.py`` matches
    itself), so no path argument makes its non-zero exit reproducible. That branch, and the
    ``OSError`` handler beside it for a Git binary this process cannot execute, are therefore
    defensive and are recorded as unasserted in the L9 defect ledger (entry A6); the ``OSError``
    half is driven by its own case,
    :func:`test_a_git_that_cannot_run_is_unavailable_rather_than_an_absent_path`, which intercepts
    the ``ls-tree`` invocation rather than pretending a path can reach it.
    """

    recorded = "src/expired_adapter.py"
    absent_tree = "f" * 40
    draft = _admitted(
        "an ordinary repository-relative path is admitted by the write boundary",
        SourceAnchorDraft,
        anchor_id=uuid4(),
        path=recorded,
        source_identity=GitBlobIdentity(object_id="0" * 40),
        locator=FileLocator(),
    )
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
                    rationale="A recorded location whose tree is not the requested one.",
                ),
                anchor=NewAnchor(anchor=draft),
                provenance=fixture.authorship,
            ),
        )
    finally:
        store.close()
    assert created.state == "created", created.refusal

    # The requested tree is one the context accepts and the repository does not hold, so no page can
    # be served from it and no absence can be claimed about it.
    unavailable = read_knowledge_scope(
        fixture.database_path,
        KnowledgeReadContext(
            repository_id=fixture.repository_id,
            knowledge=SnapshotIdentity(
                repository_id=fixture.repository_id,
                logical_digest=fixture.knowledge_digest,
                schema_version=SCHEMA_NAME,
            ),
            repository_root=str(fixture.git_root),
            code_tree_id=absent_tree,
        ),
        KnowledgeReadRequest(seed=PathSeed(path=recorded)),
    )
    assert unavailable.state == "page", unavailable.refusal
    observed = anchors_by_path(unavailable)
    assert observed[recorded].resolution == "recorded_object_unavailable"
    assert observed[recorded].recorded_source_identity == "0" * 40
    assert observed[recorded].detail == (
        f"the requested code tree {absent_tree} is not available in the selected repository, and no "
        "working tree or HEAD is substituted for it"
    ), (
        "the unavailable tree is the producer that answers here, and its detail says which fact it is"
    )
    assert recorded not in fixture.git_blobs, (
        "the recorded path is not in the fixture tree, so a served absence would prove nothing"
    )

    # And the same claim, read against the tree that really holds nothing at that path, is a genuine
    # absence: the two facts are distinguishable by the caller.
    available = read(fixture, PathSeed(path=recorded))
    assert available.state == "page", available.refusal
    from_available = anchors_by_path(available)
    assert from_available[recorded].resolution == "path_absent"


def test_a_git_that_cannot_run_is_unavailable_rather_than_an_absent_path(
    fixture: ReadScopeFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lookup that ran and *did not answer* is the second producer of the unavailable outcome.

    ``_tree_entry`` reports "the lookup did not answer" from two places — a ``ls-tree`` that exited
    non-zero, and an ``OSError`` raised when this process cannot execute Git at all — and both must
    be an *unknown* entry rather than an absent one, or the read would publish a fact about the
    repository it never observed. The two producers are told apart by the ``detail`` they carry, and
    the sibling case
    (:func:`test_a_failed_tree_lookup_is_unavailable_rather_than_an_absent_path`) pins the first
    producer: an unavailable *tree*, answered by ``observe_anchor``'s availability guard before any
    lookup runs.

    This case pins the ``OSError`` handler, which no path argument can reach: it makes the one
    ``ls-tree`` invocation raise the ``OSError`` a missing or non-executable Git binary raises, and
    delegates every other command of the read to the real runner. The delegate is what makes the
    case measure the branch rather than a stub — the availability probe before the lookup
    (``git cat-file -e <tree>^{tree}``) still runs for real and still answers *available*, so the
    observation can only come from the lookup that failed. The recorded command sequence is asserted,
    so a read that never reached the intercepted call fails here instead of passing on a different
    producer's answer.
    """

    recorded = "src/a_lookup_that_cannot_run.py"
    draft = _admitted(
        "an ordinary repository-relative path is admitted by the write boundary",
        SourceAnchorDraft,
        anchor_id=uuid4(),
        path=recorded,
        source_identity=GitBlobIdentity(object_id="0" * 40),
        locator=FileLocator(),
    )
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
                    rationale="A recorded location whose lookup cannot be run.",
                ),
                anchor=NewAnchor(anchor=draft),
                provenance=fixture.authorship,
            ),
        )
    finally:
        store.close()
    assert created.state == "created", created.refusal

    recorded_commands: list[list[str]] = []
    real_run_git = read_anchors.run_git

    def unable_to_spawn(repository_root: Path, args: list[str], **options: object) -> object:
        """Answer every command but the lookup, which fails the way an unrunnable Git binary does."""

        recorded_commands.append(list(args))
        if args[:1] == ["ls-tree"]:
            raise OSError(2, "No such file or directory")
        return real_run_git(repository_root, args, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(read_anchors, "run_git", unable_to_spawn)
    unavailable = read_knowledge_scope(
        fixture.database_path,
        KnowledgeReadContext(
            repository_id=fixture.repository_id,
            knowledge=SnapshotIdentity(
                repository_id=fixture.repository_id,
                logical_digest=fixture.knowledge_digest,
                schema_version=SCHEMA_NAME,
            ),
            repository_root=str(fixture.git_root),
            code_tree_id=fixture.git_tree_id,
        ),
        KnowledgeReadRequest(seed=PathSeed(path=recorded)),
    )
    assert unavailable.state == "page", unavailable.refusal
    assert recorded_commands[0] == ["cat-file", "-e", f"{fixture.git_tree_id}^{{tree}}"], (
        "the availability probe is the first command the read issues, and it is delegated: the tree "
        f"below really is available, so the answer cannot come from the availability branch: "
        f"{recorded_commands}"
    )
    assert ["ls-tree", "-z", fixture.git_tree_id, "--", recorded] in recorded_commands, (
        f"the recorded path really is the one that was asked for: {recorded_commands}"
    )
    observed = anchors_by_path(unavailable)
    assert observed[recorded].resolution == "recorded_object_unavailable", (
        "a lookup this process could not run is unknown, not absent"
    )
    assert {anchor.resolution for anchor in observed.values()} == {"recorded_object_unavailable"}, (
        "every anchor of the selection is answered by the failed lookup, never by a served absence"
    )
    assert observed[recorded].recorded_source_identity == "0" * 40
    assert observed[recorded].detail.endswith(
        f"did not answer (git ls-tree {fixture.git_tree_id} exited 0 [Errno 2] No such file or "
        "directory), so the entry is unknown rather than absent and no working tree or HEAD is "
        "substituted for it"
    ), "the failed lookup is the producer that answers here, and its detail distinguishes it"
    assert recorded not in fixture.git_blobs, (
        "the recorded path is not in the fixture tree, so a served absence would prove nothing"
    )
