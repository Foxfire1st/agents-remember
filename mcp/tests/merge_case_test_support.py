"""The case harness for guarded common-base merge cases.

Every merge case needs the same three things, and this module owns them so no case re-derives them:

* **Authored datasets.** Each state is written through the real store operation -- schema created by
  the store, rows inserted by ``create_invariant``/``create_revision`` with real provenance
  envelopes -- and then closed through a SQLite backup, so a case measures datasets the package
  itself produced rather than hand-built files.
* **A real Git branching scenario.** The base dataset is committed, and each side is a *child commit
  of that same commit* holding its own dataset, so the common base is a fact of the commit graph
  rather than of the fixture's own bookkeeping. The three files a case merges are checked back out
  of those commits through Git, which is what makes the ancestry evidence and the datasets agree.
  Every Git command goes through the package's own runner.
* **The measurements a claim needs.** Logical identities, per-table row sets, file digests and
  journal peers are read through separate read-only connections, so "both sides' edits survived" and
  "the inputs did not move" are measured rather than asserted.

It is test support, not production code: it decides nothing, holds no policy, and the only writes it
performs are the ones a case explicitly asks for.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import apsw
from agents_remember.application.knowledge import write_authorship
from agents_remember.kernel.git_command import GitRunnerOptions, run_git
from agents_remember.memory.knowledge import schema
from agents_remember.memory.knowledge.candidate_workspace import create_candidate
from agents_remember.memory.knowledge.connection import open_read_only_database
from agents_remember.memory.knowledge.logical import dataset_identity
from agents_remember.memory.knowledge.store import open_existing_knowledge_store
from agents_remember.models.knowledge.authorship import Authorship
from agents_remember.models.knowledge.candidate import CandidateResolution, SnapshotIdentity
from agents_remember.models.knowledge.merge import MergeInput, MergeInputRole
from agents_remember.models.knowledge.repository import RepositoryIdentity
from agents_remember.models.knowledge.result import InvariantRequest, RevisionDraft, RevisionRequest
from agents_remember.models.knowledge.snapshot import AdmittedCandidateDestination

AUTHORITY_HOME = "agents-remember"
CODE_TREE_ID = "c" * 40
MEMORY_TREE_ID = "d" * 40

# The one identifier every case's base state carries, so a case can name what it changed without
# inventing a second identity scheme.
BASE_INVARIANT_ID = "11111111-1111-4111-8111-111111111111"
BASE_REVISION_ID = "22222222-2222-4222-8222-222222222222"
BASE_LABEL = "obligation P"
BASE_STATEMENT = "The base statement every side descends from."
BASE_DATABASE_NAME = "knowledge.sqlite"

# The deterministic Git identity the scenario commits under. The runner stops inherited repository
# selectors but not these, so an explicit identity is what makes a commit reproducible.
GIT_IDENTITY = {
    "GIT_AUTHOR_NAME": "merge case",
    "GIT_AUTHOR_EMAIL": "merge-case@example.invalid",
    "GIT_COMMITTER_NAME": "merge case",
    "GIT_COMMITTER_EMAIL": "merge-case@example.invalid",
    "GIT_AUTHOR_DATE": "2026-09-15T00:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-09-15T00:00:00+00:00",
}


@dataclass(frozen=True)
class GitBranchWorld:
    """One temporary Git repository holding the base, left and right dataset commits."""

    root: Path
    base_commit: str
    left_commit: str
    right_commit: str


@dataclass
class MergeCase:
    """One case's world: its namespace, its authored states and its Git scenario."""

    root: Path
    repository: RepositoryIdentity
    authorship: Authorship
    world: GitBranchWorld | None = None
    databases: dict[str, Path] = field(default_factory=dict)

    def state_path(self, role: str) -> Path:
        """Return the database file one role's dataset was checked out of a commit into."""

        return self.databases[role]

    def identity(self, role: str) -> SnapshotIdentity:
        """Return one role's logical dataset identity."""

        return dataset_identity(self.state_path(role))

    def merge_inputs(self) -> tuple[MergeInput, MergeInput, MergeInput]:
        """Return the three merge inputs, named for their roles."""

        return (
            MergeInput(
                role="base",
                reference="git:base",
                database_path=self.state_path("base"),
                expected_identity=self.identity("base"),
            ),
            MergeInput(
                role="left",
                reference="git:left",
                database_path=self.state_path("left"),
                expected_identity=self.identity("left"),
            ),
            MergeInput(
                role="right",
                reference="git:right",
                database_path=self.state_path("right"),
                expected_identity=self.identity("right"),
            ),
        )

    def databases_by_role(self) -> dict[MergeInputRole, Path]:
        """Return every role's dataset path, for a merge request."""

        return {
            "base": self.state_path("base"),
            "left": self.state_path("left"),
            "right": self.state_path("right"),
        }


# A case-shaped callback receives the case and the two derived side paths, and may edit either.
CaseShaper = Callable[["MergeCase", dict[str, Path]], None]


def new_case(root: Path) -> MergeCase:
    """Build one case world without authoring anything into it."""

    return MergeCase(
        root=root,
        repository=RepositoryIdentity(repository_id=str(uuid4()), authority_home=AUTHORITY_HOME),
        authorship=write_authorship(
            actor_ref="agent:merge-case",
            authorization_ref="260915-KS developer kickoff ruling",
            origin_refs=("requirement:KS-R05@v1",),
        ),
    )


def author_base_state(case: MergeCase, directory: Path) -> Path:
    """Write the base dataset through the real store operations and close it.

    The store creates the schema and inserts the rows; the file is then copied out through SQLite's
    own backup so the dataset a case merges is a closed, journal-independent file rather than a
    working database with a WAL beside it.
    """

    directory.mkdir(parents=True, exist_ok=True)
    working = _created(directory / "base-working.sqlite", case)
    store = open_existing_knowledge_store(working, case.repository.repository_id)
    try:
        # The lifecycle's create operation already wrote the namespace row; a second
        # ``create_repository`` for the same identity is the store's no-change path.
        created = store.create_invariant(
            InvariantRequest(
                repository_id=case.repository.repository_id,
                invariant_id=BASE_INVARIANT_ID,
                display_label=BASE_LABEL,
                provenance=case.authorship,
            )
        )
        if created.state != "created":
            raise AssertionError(f"the base invariant was not authored: {created.refusal!r}")
        revision = store.create_revision(
            RevisionRequest(
                repository_id=case.repository.repository_id,
                revision=RevisionDraft(
                    revision_id=BASE_REVISION_ID,
                    invariant_id=BASE_INVARIANT_ID,
                    display_version="v1",
                    statement=BASE_STATEMENT,
                    applicability="Every dataset in this case's namespace.",
                    provenance=case.authorship,
                ),
            )
        )
        if revision.state != "created":
            raise AssertionError(f"the base revision was not authored: {revision.refusal!r}")
    finally:
        store.connection.close()
    return copy_closed(working, directory / "base.sqlite")


def _created(path: Path, case: MergeCase) -> Path:
    """Create an empty candidate-shaped database at one private path, through the lifecycle."""

    destination = AdmittedCandidateDestination(
        directory=path.parent / f"admission-{uuid4()}",
        repository=case.repository,
        resolution=CandidateResolution(
            lane="draft-candidate",
            code_tree_id=CODE_TREE_ID,
            memory_tree_id=MEMORY_TREE_ID,
            snapshot_ref="candidate:merge-case",
            candidate_ref="draft:merge-case",
        ),
    )
    created = create_candidate(destination)
    if created.state != "created":
        raise AssertionError(f"the case's working database was not created: {created.refusal!r}")
    shutil.copyfile(destination.database_path, path)
    shutil.rmtree(destination.directory, ignore_errors=True)
    return path


def copy_closed(source: Path, destination: Path) -> Path:
    """Copy one database through SQLite's backup and normalise the copy's journal mode."""

    destination.parent.mkdir(parents=True, exist_ok=True)
    source_connection = apsw.Connection(str(source))
    target = apsw.Connection(str(destination))
    try:
        backup = target.backup("main", source_connection, "main")
        try:
            backup.step(-1)
        finally:
            backup.close()
    finally:
        source_connection.close()
        target.close()
    normalise = apsw.Connection(str(destination))
    try:
        normalise.execute("PRAGMA journal_mode=DELETE")
    finally:
        normalise.close()
    return destination


def derive_state(source: Path, destination: Path) -> Path:
    """Copy one closed dataset to a path a case will then change through the store."""

    return copy_closed(source, destination)


def add_revision(
    path: Path,
    case: MergeCase,
    statement: str,
    *,
    display_version: str,
    predecessors: tuple[str, ...],
) -> str:
    """Author one new immutable revision into an existing dataset through the store.

    A successor is a new revision naming its predecessor; nothing here edits a sealed row, which is
    the only shape a legitimate side of a merge can take for revision content.
    """

    revision_id = str(uuid4())
    store = open_existing_knowledge_store(path, case.repository.repository_id)
    try:
        created = store.create_revision(
            RevisionRequest(
                repository_id=case.repository.repository_id,
                revision=RevisionDraft(
                    revision_id=revision_id,
                    invariant_id=BASE_INVARIANT_ID,
                    display_version=display_version,
                    statement=statement,
                    applicability="Every dataset in this case's namespace.",
                    predecessors=predecessors,
                    provenance=case.authorship,
                ),
            )
        )
        if created.state != "created":
            raise AssertionError(
                f"the case's successor revision was not authored: {created.refusal!r}"
            )
    finally:
        store.connection.close()
    return revision_id


def add_invariant(path: Path, case: MergeCase, label: str) -> str:
    """Author one new invariant identity into an existing dataset through the store."""

    invariant_id = str(uuid4())
    store = open_existing_knowledge_store(path, case.repository.repository_id)
    try:
        created = store.create_invariant(
            InvariantRequest(
                repository_id=case.repository.repository_id,
                invariant_id=invariant_id,
                display_label=label,
                provenance=case.authorship,
            )
        )
        if created.state != "created":
            raise AssertionError(f"the case's invariant was not authored: {created.refusal!r}")
    finally:
        store.connection.close()
    return invariant_id


def set_label(path: Path, label: str, *, invariant_id: str = BASE_INVARIANT_ID) -> None:
    """Change one invariant's display label in place, as the one mutable authored edit."""

    _execute(
        path, "UPDATE invariant SET display_label = ? WHERE invariant_id = ?", (label, invariant_id)
    )


def delete_anchor(path: Path, anchor_id: str) -> None:
    """Remove one source anchor that no stored realization cites."""

    _execute(path, "DELETE FROM source_anchor WHERE anchor_id = ?", (anchor_id,))


def add_anchor(path: Path, anchor_id: str, path_value: str) -> None:
    """Author one source anchor row with a well-formed typed identity and locator."""

    _execute(
        path,
        "INSERT INTO source_anchor (repository_id, anchor_id, path, source_identity, locator, "
        "provenance) VALUES (?, ?, ?, ?, ?, ?)",
        (
            _repository_id(path),
            anchor_id,
            path_value,
            '{"kind": "git_blob", "object_id": "' + "a" * 40 + '"}',
            '{"kind": "file"}',
            "{}",
        ),
    )


def add_realization_claim(path: Path, claim_id: str, revision_id: str, anchor_id: str) -> None:
    """Author one realization claim citing an exact revision and anchor."""

    _execute(
        path,
        "INSERT INTO realization_claim (repository_id, claim_id, invariant_revision_id, "
        "anchor_id, role, rationale, provenance) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            _repository_id(path),
            claim_id,
            revision_id,
            anchor_id,
            "supports",
            "the case's own realization",
            "{}",
        ),
    )


def add_family_member(
    path: Path, member_id: str, family_revision_id: str, revision_id: str
) -> None:
    """Author one membership row declaring a unique family/revision relationship."""

    _execute(
        path,
        "INSERT INTO family_member (repository_id, member_id, family_revision_id, "
        "invariant_revision_id, provenance) VALUES (?, ?, ?, ?, ?)",
        (_repository_id(path), member_id, family_revision_id, revision_id, "{}"),
    )


def add_family(path: Path, family_id: str, label: str) -> None:
    """Author one family identity row."""

    _execute(
        path,
        "INSERT INTO family (repository_id, family_id, display_label, label_provenance) "
        "VALUES (?, ?, ?, ?)",
        (_repository_id(path), family_id, label, "{}"),
    )


def add_family_revision(path: Path, family_id: str, revision_id: str, seal: str) -> None:
    """Author one family revision row with a caller-supplied seal."""

    _execute(
        path,
        "INSERT INTO family_revision (repository_id, family_id, revision_id, display_version, "
        "joint_guarantee, state_at_origin, acceptance_ref, provenance, payload_digest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _repository_id(path),
            family_id,
            revision_id,
            "v1",
            "the case's joint guarantee",
            "proposed",
            None,
            "{}",
            seal,
        ),
    )


def _execute(path: Path, statement: str, parameters: apsw.Bindings) -> None:
    """Run one explicit statement against a dataset, with foreign keys enforced."""

    connection = apsw.Connection(str(path))
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(statement, parameters)
    finally:
        connection.close()


def _repository_id(path: Path) -> str:
    """Return the one repository namespace a dataset is bound to."""

    connection = open_read_only_database(path)
    try:
        return str(next(iter(connection.execute("SELECT repository_id FROM repository")))[0])
    finally:
        connection.close()


def build_git_world(case: MergeCase, states: dict[str, Path], *, work: Path) -> GitBranchWorld:
    """Commit three datasets into one temporary repository and return their commit ids.

    The base commit is a real commit and each side is a *child of it*, so the graph has exactly one
    common base and the ancestry evidence a case asks for is a property of the repository rather
    than of the fixture's bookkeeping. Nothing outside ``work`` is touched: the repository is created
    here, its objects are its own, and no ref outside it exists.
    """

    root = work / "scenario"
    _checkout = work / "checkout"
    _checkout.mkdir(parents=True, exist_ok=True)
    _git(_checkout, ["init", "--initial-branch=main", "--quiet"])
    base_commit = _commit_state(_checkout, states["base"])
    left_commit = _commit_state(_checkout, states["left"], parent=base_commit)
    right_commit = _commit_state(_checkout, states["right"], parent=base_commit)
    shutil.move(str(_checkout), str(root))
    return GitBranchWorld(
        root=root,
        base_commit=base_commit,
        left_commit=left_commit,
        right_commit=right_commit,
    )


def _commit_state(checkout: Path, database: Path, *, parent: str | None = None) -> str:
    """Commit one dataset as the working tree's only file and return the commit id."""

    shutil.copyfile(database, checkout / BASE_DATABASE_NAME)
    _git(checkout, ["add", BASE_DATABASE_NAME])
    if parent is not None:
        _git(checkout, ["read-tree", parent])
        _git(checkout, ["checkout-index", "-a", "-f"])
        shutil.copyfile(database, checkout / BASE_DATABASE_NAME)
        _git(checkout, ["add", BASE_DATABASE_NAME])
        _git(checkout, ["update-ref", "HEAD", parent])
    state = _git(checkout, ["status", "--porcelain=1", "--untracked-files=no"])
    if not state.stdout.strip():
        # A side that changed nothing is the same tree as its parent, which is a legitimate case
        # ("no-op in this direction") rather than a scenario the fixture has to invent a commit for.
        if parent is None:
            raise AssertionError("the base snapshot produced no commit, so there is no base")
        return parent
    _git(checkout, ["commit", "--quiet", "-m", "case snapshot"])
    return _git(checkout, ["rev-parse", "HEAD"]).stdout.strip()


def materialize_commit(world: GitBranchWorld, commit: str, destination: Path) -> Path:
    """Export one commit's dataset file through Git and write it to a case path.

    The file is read out of the commit archive rather than copied from a working tree, so the
    dataset a case merges is exactly the blob Git holds for that commit and the repository's own
    working tree is never involved.
    """

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(_read_committed_blob(world.root, commit, BASE_DATABASE_NAME))
    return destination


def _read_committed_blob(repository: Path, commit: str, member: str) -> bytes:
    """Return one committed file's exact bytes.

    This is the one binary read in the harness, and it deliberately does not go through the
    package's text-decoding runner: a dataset is bytes, and a runner that decodes to text with
    ``surrogateescape`` would hand back something that is not the blob Git stored. The read is
    read-only and scoped to a repository this fixture created.
    """

    blob = run_git(
        repository,
        ["rev-parse", f"{commit}:{member}"],
        GitRunnerOptions(identity=dict(GIT_IDENTITY)),
    )
    if blob.returncode:
        raise AssertionError(f"the commit {commit} carries no {member}: {blob.stderr!r}")
    content = subprocess.run(
        ["git", "-C", str(repository), "cat-file", "blob", blob.stdout.strip()],
        capture_output=True,
        check=True,
    )
    return content.stdout


def build_case(
    root: Path,
    *,
    diverging_revisions: bool = True,
    diverging_identities: bool = True,
    shape: CaseShaper | None = None,
    base_shape: CaseShaper | None = None,
) -> MergeCase:
    """Build one complete case: base state, two sides, and the Git scenario joining them.

    ``base_shape`` runs on the base state, and ``shape`` runs after the two sides are derived; both
    run *before* the Git scenario is built, so a case that needs an unusual state gets it inside the
    commits instead of having to rewrite the repository afterwards. That ordering is what keeps the
    ancestry evidence and the datasets true statements about each other.

    A case that wants a specific side state uses ``shape``: the two sides always diverge by their own
    successor revisions and their own identities, and anything more specific -- a same-field label
    conflict, a tampered sealed revision, a removed anchor -- arrives through that callback so the
    commit graph is built from the states the case actually means to merge.
    """

    case = new_case(root)
    base = author_base_state(case, root / "authored")
    if base_shape is not None:
        base_shape(case, {"base": base})
    left = derive_state(base, root / "states" / "left.sqlite")
    right = derive_state(base, root / "states" / "right.sqlite")
    shaped = {"left": left, "right": right}
    if shape is not None:
        shape(case, shaped)
        left, right = shaped["left"], shaped["right"]
    if diverging_revisions:
        add_revision(
            left,
            case,
            "The left side's own successor statement.",
            display_version="v2",
            predecessors=(BASE_REVISION_ID,),
        )
        add_revision(
            right,
            case,
            "The right side's own successor statement.",
            display_version="v2",
            predecessors=(BASE_REVISION_ID,),
        )
    if diverging_identities:
        add_invariant(left, case, "the left side's own obligation")
        add_invariant(right, case, "the right side's own obligation")
    states = {"base": base, "left": left, "right": right}
    world = build_git_world(case, states, work=root / "git")
    case.world = world
    case.databases = {
        role: materialize_commit(world, commit, root / "committed" / f"{role}.sqlite")
        for role, commit in (
            ("base", world.base_commit),
            ("left", world.left_commit),
            ("right", world.right_commit),
        )
    }
    return case


def _git(root: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run one scenario Git command through the package's single runner, refusing a failure."""

    result = run_git(root, args, GitRunnerOptions(identity=dict(GIT_IDENTITY)))
    if result.returncode:
        raise AssertionError(f"scenario git {args} failed: {result.stderr!r}")
    return result


def table_rows(path: Path, table: str) -> list[tuple[object, ...]]:
    """Return one canonical table's rows, read through a separate read-only connection."""

    columns = ", ".join(schema.CANONICAL_COLUMNS[table])
    connection = open_read_only_database(path)
    try:
        return sorted(tuple(row) for row in connection.execute(f"SELECT {columns} FROM {table}"))
    finally:
        connection.close()


def row_counts(path: Path) -> dict[str, int]:
    """Return every canonical table's row count from a separate read-only connection."""

    connection = open_read_only_database(path)
    try:
        return {
            table: int(next(iter(connection.execute(f"SELECT count(*) FROM {table}")))[0])
            for table in schema.CANONICAL_TABLES
        }
    finally:
        connection.close()


def file_digest(path: Path) -> str:
    """Return the sha256 of one file's exact bytes."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def journal_peer_names(path: Path) -> list[str]:
    """Return the journal/WAL peer names beside one database file."""

    return sorted(
        peer.name
        for suffix in ("-wal", "-shm", "-journal")
        if (peer := path.with_name(path.name + suffix)).exists()
    )


def statements_of(path: Path) -> list[str]:
    """Return every stored invariant statement, in a stable order."""

    connection = open_read_only_database(path)
    try:
        return sorted(
            str(row[0]) for row in connection.execute("SELECT statement FROM invariant_revision")
        )
    finally:
        connection.close()


def labels_of(path: Path) -> list[str]:
    """Return every stored invariant label, in a stable order."""

    connection = open_read_only_database(path)
    try:
        return sorted(
            str(row[0]) for row in connection.execute("SELECT display_label FROM invariant")
        )
    finally:
        connection.close()


def revision_id_for(path: Path, statement: str) -> str:
    """Return the revision id holding one exact statement."""

    connection = open_read_only_database(path)
    try:
        row = next(
            iter(
                connection.execute(
                    "SELECT revision_id FROM invariant_revision WHERE statement = ?", (statement,)
                )
            ),
            None,
        )
    finally:
        connection.close()
    if row is None:
        raise AssertionError(f"no stored revision holds the statement {statement!r}")
    return str(UUID(str(row[0])))
