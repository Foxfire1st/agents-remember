"""Public source endpoints over real four-tree comparisons, capture and changeset routes.

The tests below use the converted ``World`` enclosure. ``EndpointFixture`` is retained separately
for composition unit tests that need synthetic row identities; its explicit assembly does not test
production candidate selection. Both fixtures capture real source trees with the user's index intact.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    ReviewRecordInputs,
    compose_review,
    read_knowledge_review,
    resolve_review_candidate,
)
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_source_content import read_review_source_content
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import KnowledgeReviewResult, ReviewSurfaceRequest
from agents_remember.serving.changeset import (
    ChangesetFileRef,
    leaf_changeset,
    leaf_file_diff,
    register_changeset_routes,
)
from agents_remember.serving.review import register_review_routes
from agents_remember.worktrees.modules import future_code_candidate as capture_owner
from agents_remember.worktrees.modules.future_code_candidate import (
    FutureCodeCandidateIdentity,
    capture_future_code_candidate,
)
from agents_remember.worktrees.worktree_contract import (
    ContractTask,
    LeafIdentity,
    RepoBranchPlan,
    WorktreeContract,
    default_contract,
    load_contract,
    write_contract,
)
from diff_scope_test_support import (
    BATCH_PATH_CANDIDATE_TEXT,
    SUCCESSOR_PATH,
    UNMAPPED_PATH,
    DiffFixture,
    _git,
    build_diff_fixture,
    independent_changed_records,
)
from fastapi import FastAPI
from fastapi.testclient import TestClient
from read_scope_test_support import (
    AUXILIARY_PATH,
    BATCH_PATH,
    MISMATCH_PATH,
    RESOLUTION_PATH,
    SYNCHRONIZATION_PATH,
    UNPARSED_PATH,
)
from test_review_git_trees import (
    CODE_FILE,
    CODE_V1,
    INVARIANT,
    LEAF,
    MASTER,
    REPO,
    World,
    build_world,
    commit,
    git,
)

pytestmark = pytest.mark.evidence_unit

LEAF_ID = "260921-icr-l1"
WORKTREE_NAME = "icr-r01-l1"
TASK_NAME = "review-source-endpoints-fixture"

# Synthetic row tests retain these exact source bytes and opaque identity semantics.
MODIFIED_PATH = BATCH_PATH
ELIGIBLE_UNTRACKED_PATH = SUCCESSOR_PATH
STAGED_ADDITION_PATH = "src/staged_addition.py"
IGNORED_PATH = "build/ignored.py"
IGNORED_RULE = "build/\n"
_IGNORED_TEXT = "# derived output, excluded by the repository's own ignore policy\n"
STAGED_ADDITION_TEXT = "# staged before the review opened\n"
LOCAL_COMMIT_PATH = "src/committed_later.py"
LOCAL_COMMIT_TEXT = "# committed after the recorded endpoint was bound\n"
UNCOMMITTED_PATH = "src/uncommitted.py"
UNCOMMITTED_TEXT = "# never committed: the working view's own population\n"


@dataclass(frozen=True)
class EndpointFixture:
    """Real source enclosure plus synthetic row indexes for composition unit tests.

    Public candidate resolution is covered separately with the converted ``World`` fixture.
    These rows keep the existing identity semantics of the low-level read fixtures.
    """

    config: McpRuntimeConfig
    contract: WorktreeContract
    diff: DiffFixture
    master: str

    @property
    def repository_id(self) -> str:
        return self.contract.repo_name

    @property
    def worktree(self) -> Path:
        assert self.contract.code_worktree is not None
        return self.contract.code_worktree

    def request(self, selector: str | None = None) -> ReviewSurfaceRequest:
        return ReviewSurfaceRequest(
            repository_id=self.repository_id,
            master=self.master,
            leaf_id=LEAF_ID,
            selector=InvariantIdentitySeed(
                invariant_id=selector or self.diff.retry_invariant_id,
            ),
        )

    def task_request(self) -> ReviewSurfaceRequest:
        """The task-context request (ICR-R02): the same task, with no reviewed subject.

        This is not a degraded subject request. It names exactly the task context the entry names and
        no selector at all, which is the entry a task with no recorded invariant -- or with no
        datasets yet -- has to be reviewable through.
        """

        return ReviewSurfaceRequest(
            repository_id=self.repository_id,
            master=self.master,
            leaf_id=LEAF_ID,
            selector=None,
        )

    def resolve(self) -> ReviewCandidateResolution:
        captured = capture_future_code_candidate(self.contract)
        root = self.contract.worktree_group / "provider-runtime/dev-ar-coordination/knowledge"
        return ReviewCandidateResolution(
            repository_id=self.repository_id,
            leaf_id=LEAF_ID,
            baseline_database=root / "baseline/knowledge-candidate.db",
            candidate_database=root / "candidate/knowledge-candidate.db",
            baseline_code_root=self.contract.code_repo_path,
            candidate_code_root=self.contract.code_repo_path,
            baseline_code_tree_id=self.contract.code_base_commit,
            candidate_code_tree_id=captured.codeCandidateTree,
            contract=self.contract,
            candidate_identity=captured,
        )

    def recorded_range(self, commit: str) -> WorktreeContract:
        """Record one landed commit on the contract, as closeout writes it."""

        recorded = replace(self.contract, code_commit=commit)
        write_contract(recorded.contract_path, recorded)
        return load_contract(recorded.contract_path)

    def record_code_with_unrecorded_memory(self, commit: str) -> WorktreeContract:
        """Record the code landed commit on a contract whose external memory leg has recorded none.

        This is the state the committed view has to degrade one half at a time for: the memory side
        is real -- its repository, worktree and ledger cells are recorded and readable -- but neither
        closeout nor integration has written its landed commit, so the memory range does not exist
        yet while the code range does. The memory paths are never read in this state, because the
        resolver names the unrecorded endpoint before it reaches the repository.
        """

        memory_worktree = self.contract.worktree_group / f"memory-{WORKTREE_NAME}"
        memory_worktree.mkdir(parents=True, exist_ok=True)
        ledger = memory_worktree / "memory.md"
        ledger.write_text("# fixture ledger\n", encoding="utf-8")
        recorded = replace(
            self.contract,
            code_commit=commit,
            memory_mode="external",
            memory_repo_path=memory_worktree,
            memory_source_branch=self.contract.code_source_branch,
            memory_work_branch=self.contract.code_work_branch,
            memory_base_commit=self.contract.code_base_commit,
            memory_worktree=memory_worktree,
            ledger_path=ledger,
            memory_state="",
            memory_content_commit="",
            integrated_memory_content_commit="",
        )
        write_contract(recorded.contract_path, recorded)
        return load_contract(recorded.contract_path)


@pytest.fixture
def endpoint_fixture(tmp_path: Path) -> EndpointFixture:
    """One fresh live enclosure per case; no case observes another's worktree state."""

    return build_endpoint_fixture(tmp_path / "endpoints")


def build_endpoint_fixture(
    directory: Path, *, datasets: bool = True, memory_mode: str = "disabled"
) -> EndpointFixture:
    """Build synthetic row indexes beside a real source enclosure for composition unit tests.

    ``datasets=False`` leaves both row indexes absent. The public resolver treats this enclosure's
    unconverted memory as legacy-unavailable, independently of whether these test rows are present.

    ``memory_mode="external"`` adds the other half of the enclosure a later leaf's cases need: a real
    external-memory repository and its linked worktree, on the same contract. It is a parameter rather
    than a second builder because every other fact about the enclosure -- the recorded base, the
    captured candidate, the two row indexes -- is the same one, and two builders would be two fixtures
    free to drift apart.
    """

    diff = build_diff_fixture(directory / "diff")
    contract = _enclosure(directory, diff, memory_mode=memory_mode)
    _materialize_candidate(diff, contract)
    if datasets:
        _place_datasets(diff, contract)
    return EndpointFixture(
        config=McpRuntimeConfig(
            workspace_root=directory,
            coordination_root=directory / "ar-coordination",
            config_path=directory / "config.json",
            transcript_root=directory / "transcripts",
        ),
        contract=load_contract(contract.contract_path),
        diff=diff,
        master=contract.task_root.name,
    )


def _enclosure(
    directory: Path, diff: DiffFixture, *, memory_mode: str = "disabled"
) -> WorktreeContract:
    """The leaf contract, its source branch and its linked worktree, all really on disk."""

    code_repo = diff.before.git_root
    base_commit = _git(code_repo, ["rev-parse", "HEAD"])
    memory = None if memory_mode == "disabled" else _memory_plan(directory, diff)
    contract = default_contract(
        ContractTask(
            name=TASK_NAME,
            repo_name=diff.repository_id,
            coordination_root=directory / "ar-coordination",
            workflow_kind="light-task",
            memory_mode=memory_mode,
        ),
        leaf=LeafIdentity(worktree_name=WORKTREE_NAME, leaf_id=LEAF_ID),
        code=RepoBranchPlan(
            repo_path=code_repo,
            source_branch="super",
            work_branch=f"ar/{WORKTREE_NAME}",
            base_commit=base_commit,
        ),
        memory=memory,
    )
    _git(code_repo, ["branch", "super", base_commit])
    assert contract.code_worktree is not None
    contract.code_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(
        code_repo,
        [
            "worktree",
            "add",
            "-b",
            contract.code_work_branch,
            str(contract.code_worktree),
            "super",
        ],
    )
    _link_memory_worktree(contract)
    write_contract(contract.contract_path, contract)
    return contract


def _memory_plan(directory: Path, diff: DiffFixture) -> RepoBranchPlan:
    """One real external-memory repository at the conventional coordination path.

    The ledger the contract names is the repository's own ``memory.md``, written and committed by this
    helper, and ``super`` is created at that commit -- the same shape the managed worktree owner
    produces, so the enclosure the curator authority validates is a real one rather than a directory
    that merely exists.
    """

    repository = directory / "ar-coordination" / "memory-repos" / f"ar-{diff.repository_id}"
    repository.mkdir(parents=True, exist_ok=True)
    _git(repository, ["init", "-q"])
    _git(repository, ["config", "user.email", "fixture@example.invalid"])
    _git(repository, ["config", "user.name", "endpoint fixture"])
    (repository / "memory.md").write_text("# fixture memory ledger\n", encoding="utf-8")
    _git(repository, ["add", "-A"])
    _git(repository, ["commit", "-q", "-m", "Add memory ledger"])
    base_commit = _git(repository, ["rev-parse", "HEAD"])
    _git(repository, ["branch", "super", base_commit])
    return RepoBranchPlan(
        repo_path=repository,
        source_branch="super",
        work_branch=f"ar/{WORKTREE_NAME}",
        base_commit=base_commit,
    )


def _link_memory_worktree(contract: WorktreeContract) -> None:
    """Link the memory worktree when this contract names one, and nothing when it does not."""

    if contract.memory_worktree is None or contract.memory_repo_path is None:
        return
    contract.memory_worktree.parent.mkdir(parents=True, exist_ok=True)
    _git(
        contract.memory_repo_path,
        [
            "worktree",
            "add",
            "-b",
            contract.memory_work_branch,
            str(contract.memory_worktree),
            contract.memory_source_branch,
        ],
    )


def _materialize_candidate(diff: DiffFixture, contract: WorktreeContract) -> None:
    """Write the fixture's candidate content into the worktree, in three different index states."""

    worktree = contract.code_worktree
    assert worktree is not None
    # Unstaged tracked edit and unstaged tracked deletion.
    (worktree / MODIFIED_PATH).write_text(BATCH_PATH_CANDIDATE_TEXT, encoding="utf-8")
    (worktree / SYNCHRONIZATION_PATH).unlink()
    # Staged addition: in the real index, never committed.
    (worktree / STAGED_ADDITION_PATH).write_text(STAGED_ADDITION_TEXT, encoding="utf-8")
    _git(worktree, ["add", STAGED_ADDITION_PATH])
    # Eligible untracked content: the added realization's path and the unattributed path.
    (worktree / ELIGIBLE_UNTRACKED_PATH).write_text(
        "# retry interval\nfirst refusal\n", encoding="utf-8"
    )
    (worktree / UNMAPPED_PATH).write_text(
        "# unmapped\nthis file carries no recorded realization\n", encoding="utf-8"
    )
    # The byte-for-byte paths the candidate dataset records, unchanged by this fixture's transitions.
    (worktree / RESOLUTION_PATH).write_text("# resolution\n", encoding="utf-8")
    (worktree / AUXILIARY_PATH).write_text("# anchors\n", encoding="utf-8")
    (worktree / MISMATCH_PATH).write_text("# timeout\nchanged bytes\n", encoding="utf-8")
    (worktree / UNPARSED_PATH).write_text(
        "-- schema\nCREATE TABLE budget (attempts INTEGER);\n", encoding="utf-8"
    )
    # Ignored output, which the capture's own policy must leave out of the candidate tree.
    (worktree / ".gitignore").write_text(IGNORED_RULE, encoding="utf-8")
    (worktree / IGNORED_PATH).parent.mkdir(parents=True, exist_ok=True)
    (worktree / IGNORED_PATH).write_text(_IGNORED_TEXT, encoding="utf-8")


def _place_datasets(diff: DiffFixture, contract: WorktreeContract) -> None:
    """Place synthetic row indexes for the fixture's direct composition-unit resolution.

    Production resolution never selects these test files.
    """

    root = contract.worktree_group / "provider-runtime" / "dev-ar-coordination" / "knowledge"
    for half, database in (
        ("baseline", diff.before.database_path),
        ("candidate", diff.after.database_path),
    ):
        (root / half).mkdir(parents=True, exist_ok=True)
        (root / half / "knowledge-candidate.db").write_bytes(database.read_bytes())


def compose_endpoint_review(
    fixture: EndpointFixture,
    request: ReviewSurfaceRequest | None = None,
    records: ReviewRecordInputs | None = None,
) -> KnowledgeReviewResult:
    """Compose synthetic row inputs with captured source; this is not a public resolver test."""

    if records is None:
        return compose_review(fixture.resolve(), request or fixture.request())
    return compose_review(fixture.resolve(), request or fixture.request(), records)


def _index_path(worktree: Path) -> Path:
    """The worktree's *real* Git index, as Git itself resolves it."""

    resolved = Path(_git(worktree, ["rev-parse", "--git-path", "index"]))
    return resolved if resolved.is_absolute() else worktree / resolved


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _commit(worktree: Path, message: str) -> str:
    """Commit the worktree's whole content and return the commit ``HEAD`` now names.

    ``git commit`` prints its own summary line on stdout, so the new identity is read back with
    ``rev-parse`` rather than taken from the commit command's output.
    """

    _git(worktree, ["add", "-A"])
    _git(worktree, ["commit", "-m", message])
    return _git(worktree, ["rev-parse", "HEAD"])


def _tree_paths(repository: Path, tree_id: str) -> set[str]:
    return set(_git(repository, ["ls-tree", "-r", "--name-only", tree_id]).split())


def _captured_tree(resolved: ReviewCandidateResolution) -> str:
    """The candidate tree the resolution bound; a live resolution always carries one."""

    tree = resolved.candidate_code_tree_id
    assert tree is not None, "a resolved live candidate binds its captured tree"
    return tree


def _captured_identity(resolved: ReviewCandidateResolution) -> FutureCodeCandidateIdentity:
    """The whole capture the resolution bound, required because it is what the recheck compares."""

    identity = resolved.candidate_identity
    assert identity is not None, "a resolved live candidate binds the capture it derived"
    return identity


# -- public resolution: a converted memory repository and its real four-tree comparison ---------


@pytest.fixture
def source_world(tmp_path: Path) -> World:
    world = build_world(tmp_path / "source")
    world.code_base = commit(world.code, {SYNCHRONIZATION_PATH: "# removed by the leaf\n"})
    world.memory_base = commit(world.memory, {}, trailer=world.code_base)
    git(world.code_worktree, "reset", "--hard", world.code_base)
    git(world.memory_worktree, "reset", "--hard", world.memory_base)
    world.contract()
    world.edit()
    (world.code_worktree / SYNCHRONIZATION_PATH).unlink()
    (world.code_worktree / "src").mkdir(exist_ok=True)
    (world.code_worktree / STAGED_ADDITION_PATH).write_text(STAGED_ADDITION_TEXT)
    git(world.code_worktree, "add", STAGED_ADDITION_PATH)
    (world.code_worktree / ELIGIBLE_UNTRACKED_PATH).write_text("# eligible untracked\n")
    (world.code_worktree / UNMAPPED_PATH).write_text("# unregistered\n")
    (world.code_worktree / ".gitignore").write_text(IGNORED_RULE)
    (world.code_worktree / IGNORED_PATH).parent.mkdir()
    (world.code_worktree / IGNORED_PATH).write_text(_IGNORED_TEXT)
    return world


def _public_resolution(world: World) -> ReviewCandidateResolution:
    resolved = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    return resolved


def _source_app(world: World) -> FastAPI:
    app = FastAPI()
    register_review_routes(
        app,
        world.config,
        lambda request: read_knowledge_review(world.config, request),
        source_content_port=lambda request: read_review_source_content(world.config, request),
    )
    return app


def _source_params(*, subject: bool = False) -> dict[str, str]:
    params = {"repo": REPO, "master": MASTER, "leaf": LEAF}
    if subject:
        params.update(selectorKind="invariant", selectorId=text_uuid("identity", INVARIANT))
    return params


def test_the_live_candidate_binds_the_recorded_base_and_the_captured_tree(
    source_world: World,
) -> None:
    world = source_world
    index = _index_path(world.code_worktree)
    before = _digest(index)
    resolved = _public_resolution(world)
    assert _digest(index) == before, "capture rewrote the user's Git index"
    contract = resolved.contract
    assert contract is not None and resolved.trees is not None
    assert resolved.baseline_code_tree_id == world.code_base
    assert (
        resolved.candidate_code_tree_id == capture_future_code_candidate(contract).codeCandidateTree
    )
    assert resolved.baseline_code_root == resolved.candidate_code_root == world.code
    for tree in (resolved.baseline_code_tree_id, resolved.candidate_code_tree_id):
        assert tree is not None and git(world.code, "cat-file", "-t", tree) in {"tree", "commit"}
    paths = _tree_paths(world.code, _captured_tree(resolved))
    assert {STAGED_ADDITION_PATH, ELIGIBLE_UNTRACKED_PATH, UNMAPPED_PATH, CODE_FILE} <= paths
    assert SYNCHRONIZATION_PATH not in paths and IGNORED_PATH not in paths


def test_the_rendered_review_publishes_the_endpoints_and_reaches_the_whole_candidate(
    source_world: World,
) -> None:
    world = source_world
    resolved = _public_resolution(world)
    with TestClient(_source_app(world)) as client:
        response = client.get("/api/review/intent", params=_source_params(subject=True))
        expanded = client.get(
            "/api/review/intent/source-content",
            params={
                **_source_params(),
                "path": ELIGIBLE_UNTRACKED_PATH,
                "beforeCodeTreeId": world.code_base,
                "afterCodeTreeId": _captured_tree(resolved),
            },
        )
    assert response.status_code == 200, response.text
    payload = response.json()["payload"]
    comparison = payload["comparison"]
    assert comparison["before_code_tree_id"] == world.code_base
    assert comparison["after_code_tree_id"] == _captured_tree(resolved)
    inventory = payload["source"]["inventory"]
    changed = {entry["path"] for entry in inventory["entries"]}
    assert {
        CODE_FILE,
        ELIGIBLE_UNTRACKED_PATH,
        UNMAPPED_PATH,
        STAGED_ADDITION_PATH,
        SYNCHRONIZATION_PATH,
    } <= changed
    assert IGNORED_PATH not in changed
    assert sorted(changed) == sorted(
        independent_changed_records(world.code, world.code_base, _captured_tree(resolved))
    )
    head_to_unstaged = set(git(world.code_worktree, "diff", "--name-only", "HEAD").splitlines())
    assert ELIGIBLE_UNTRACKED_PATH not in head_to_unstaged and CODE_FILE in head_to_unstaged
    assert expanded.status_code == 200, expanded.text
    source = expanded.json()["expansion"]
    assert source["admission"] == "changed" and source["before"]["state"] == "absent"
    assert source["after_code_tree_id"] == _captured_tree(resolved)
    assert source["after"]["text"] == "# eligible untracked\n"


def test_a_capture_input_that_moves_before_publication_is_refused_by_name(
    source_world: World,
) -> None:
    world = source_world
    resolved = _public_resolution(world)
    (world.code_worktree / CODE_FILE).write_text("# moved while composing\n")
    assert resolved.contract is not None
    moved = capture_future_code_candidate(resolved.contract).codeCandidateTree
    result = compose_review(resolved, world.review())
    assert result.state == "refused" and result.payload is None
    refusal = result.refusal
    assert refusal is not None and refusal.code == "candidate_unresolved"
    assert refusal.offending_input == "the captured candidate tree"
    assert _captured_tree(resolved) in (refusal.expected or "")
    assert moved in (refusal.observed or "") and refusal.next_action


def test_a_capture_that_detects_a_moved_head_refuses_instead_of_publishing_a_stale_tree(
    source_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    world = source_world
    original = capture_owner.worktree_candidate_tree

    def capture_then_move(repo: Path, index_path: Path, **kwargs: object) -> str:
        tree = original(repo, index_path, **kwargs)  # type: ignore[arg-type]
        _commit(world.code_worktree, "commit during capture")
        return tree

    monkeypatch.setattr(capture_owner, "worktree_candidate_tree", capture_then_move)
    outcome = resolve_review_candidate(world.config, REPO, MASTER, LEAF)
    assert not isinstance(outcome, ReviewCandidateResolution)
    assert outcome.code == "candidate_unresolved" and outcome.offending_input == "candidate"
    assert "future-code-candidate-head-moved" in outcome.detail and outcome.next_action


def test_a_moved_code_head_names_the_head_as_the_side_that_moved(source_world: World) -> None:
    world = source_world
    resolved = _public_resolution(world)
    captured = _captured_identity(resolved)
    landed = _commit(world.code_worktree, "land captured content")
    result = compose_review(resolved, world.review())
    refusal = result.refusal
    assert result.state == "refused" and refusal is not None
    assert refusal.offending_input == "the leaf worktree's code HEAD"
    assert captured.observedCodeHead in (refusal.expected or "")
    assert landed in (refusal.observed or "")
    assert resolved.contract is not None
    assert capture_future_code_candidate(resolved.contract).codeCandidateTree == _captured_tree(
        resolved
    )


def test_a_committed_range_binds_the_recorded_commit_and_a_later_commit_does_not_move_it(
    source_world: World,
) -> None:
    world = source_world
    landed = _commit(world.code_worktree, "land the candidate")
    world.contract(code_commit=landed)
    committed = leaf_changeset(world.config, REPO, MASTER, LEAF, "committed")
    assert committed["mode"] == "committed"
    assert {entry["path"] for entry in committed["code"]} >= {
        CODE_FILE,
        ELIGIBLE_UNTRACKED_PATH,
        STAGED_ADDITION_PATH,
        SYNCHRONIZATION_PATH,
    }
    diff = leaf_file_diff(
        world.config,
        ChangesetFileRef(
            repo=REPO, path=CODE_FILE, kind="code", master=MASTER, leaf=LEAF, mode="committed"
        ),
    )
    assert diff["before"] == {"content": CODE_V1}
    assert diff["after"] == {"content": CODE_V1.replace("return value", "return value + 0")}
    (world.code_worktree / LOCAL_COMMIT_PATH).write_text(LOCAL_COMMIT_TEXT)
    assert _commit(world.code_worktree, "later local commit") != landed
    assert leaf_changeset(world.config, REPO, MASTER, LEAF, "committed") == committed
    (world.code_worktree / UNCOMMITTED_PATH).write_text(UNCOMMITTED_TEXT)
    working = leaf_changeset(world.config, REPO, MASTER, LEAF, "working")
    assert working["mode"] == "working"
    assert {entry["path"] for entry in working["code"]} == {UNCOMMITTED_PATH}


def test_an_unrecorded_committed_endpoint_is_answered_with_its_own_state_rather_than_read_from_head(
    source_world: World,
) -> None:
    world = source_world
    head = _commit(world.code_worktree, "unrecorded commit")
    view = leaf_changeset(world.config, REPO, MASTER, LEAF, "committed")
    assert view["state"] == "unrecorded"
    assert (
        "no committed code range yet" in view["stateDetail"]
        and "mode=working" in view["stateDetail"]
    )
    assert head not in view["stateDetail"] and view["code"] == []
    assert view["counters"] == {
        side: {"files": 0, "insertions": 0, "deletions": 0} for side in ("code", "memory")
    }
    world.contract(code_commit=head)
    recorded = leaf_changeset(world.config, REPO, MASTER, LEAF, "committed")
    assert recorded["state"] == "recorded" and recorded["stateDetail"] == ""
    assert leaf_changeset(world.config, REPO, MASTER, LEAF, "working")["mode"] == "working"


def test_the_route_answers_an_unrecorded_committed_view_without_a_status_error(
    source_world: World,
) -> None:
    world = source_world
    _commit(world.code_worktree, "unrecorded commit")
    app = FastAPI()
    register_changeset_routes(app, world.config)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/api/changeset/task", params={**_source_params(), "mode": "committed"}
        )
        missing = client.get(
            "/api/changeset/task",
            params={**_source_params(), "leaf": "260101-NOPE", "mode": "committed"},
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "unrecorded" and "no committed code range yet" in body["stateDetail"]
    assert body["code"] == [] and body["counters"]["code"] == {
        "files": 0,
        "insertions": 0,
        "deletions": 0,
    }
    assert missing.status_code == 404 and missing.json()["status"] == "not-found"


def test_an_unrecorded_memory_half_empties_only_itself_and_keeps_the_code_half(
    source_world: World,
) -> None:
    world = source_world
    world.contract(code_commit=_commit(world.code_worktree, "land code without memory"))
    view = leaf_changeset(world.config, REPO, MASTER, LEAF, "committed")
    assert view["mode"] == "committed"
    assert {entry["path"] for entry in view["code"]} >= {
        CODE_FILE,
        ELIGIBLE_UNTRACKED_PATH,
        STAGED_ADDITION_PATH,
        SYNCHRONIZATION_PATH,
    }
    assert view["counters"]["code"]["files"] == len(view["code"])
    assert view["memory"] == [] and view["counters"]["memory"] == {
        "files": 0,
        "insertions": 0,
        "deletions": 0,
    }


def test_a_task_context_review_lists_the_complete_source_inventory_with_no_knowledge_at_all(
    source_world: World,
) -> None:
    world = source_world
    for root in (world.memory, world.memory_worktree):
        git(root, "rm", "-q", "-r", "-f", "knowledge", "onboarding")
        git(root, "commit", "-q", "-m", "unconverted memory")
    resolved = _public_resolution(world)
    assert resolved.trees is None
    assert {state for _, state, _ in resolved.knowledge_unavailable} == {"legacy-unavailable"}
    with TestClient(_source_app(world)) as client:
        response = client.get("/api/review/intent", params=_source_params())
        named = client.get("/api/review/intent", params=_source_params(subject=True))
    assert response.status_code == 200, response.text
    payload = response.json()["payload"]
    assert "comparison" not in payload and payload["staleness"]["state"] == "not_compared"
    assert payload["knowledge"]["selection_state"] == "task_context"
    assert payload["knowledge"]["before_statement"]["state"] == "unresolved"
    inventory = payload["source"]["inventory"]
    assert inventory["state"] == "measured" and not inventory["partial"]
    listed = {entry["path"] for entry in inventory["entries"]}
    assert sorted(listed) == sorted(
        independent_changed_records(world.code, world.code_base, _captured_tree(resolved))
    )
    assert {CODE_FILE, STAGED_ADDITION_PATH, ELIGIBLE_UNTRACKED_PATH, UNMAPPED_PATH} <= listed
    assert IGNORED_PATH not in listed and inventory["listed_total"] == len(listed)
    assert inventory["before_code_tree_id"] == world.code_base
    assert inventory["after_code_tree_id"] == _captured_tree(resolved)
    assert (
        named.status_code == 404 and named.json()["refusal"]["code"] == "candidate_dataset_absent"
    )
    assert "legacy-unavailable" in named.json()["refusal"]["detail"]


UNUSUAL_PATH = "src/tab\tnewline\nname.py"
BINARY_ADDITION_PATH = "assets/blob.dat"
NON_UTF8_ADDITION = b"src/caf\xe9-latin1.py"
NON_UTF8_BYTE_FORM = "b'src/caf\\xe9-latin1.py'"


def test_the_production_inventory_keeps_an_unusual_filename_as_the_address_it_expands_by(
    source_world: World,
) -> None:
    world = source_world
    (world.code_worktree / UNUSUAL_PATH).write_text("a name is not a separator\n")
    resolved = _public_resolution(world)
    result = read_knowledge_review(world.config, world.review())
    assert result.payload is not None, result.refusal
    inventory = result.payload.source.inventory
    listed = [entry.path for entry in inventory.entries]
    assert UNUSUAL_PATH in listed
    assert sorted(listed) == sorted(
        independent_changed_records(world.code, world.code_base, _captured_tree(resolved))
    )
    unusual = next(entry for entry in inventory.entries if entry.path == UNUSUAL_PATH)
    assert unusual.status == "added" and unusual.content == "text"
    assert (world.code_worktree / unusual.path).is_file()
    assert (
        UNUSUAL_PATH
        not in git(
            world.code,
            "diff",
            "--name-only",
            "--no-renames",
            world.code_base,
            _captured_tree(resolved),
        ).splitlines()
    )


def test_the_production_inventory_lists_non_text_and_mode_changed_paths_it_cannot_render(
    source_world: World,
) -> None:
    world = source_world
    (world.code_worktree / BINARY_ADDITION_PATH).parent.mkdir()
    (world.code_worktree / BINARY_ADDITION_PATH).write_bytes(b"binary\x00content\n")
    (world.code_worktree / CODE_FILE).chmod(0o755)
    resolved = _public_resolution(world)
    result = read_knowledge_review(world.config, world.review())
    assert result.state == "review", result.refusal
    assert result.payload is not None
    entries = {entry.path: entry for entry in result.payload.source.inventory.entries}
    assert sorted(entries) == sorted(
        independent_changed_records(world.code, world.code_base, _captured_tree(resolved))
    )
    # A binary addition and a mode-only change stay listed, each with the fact that says so.
    assert entries[BINARY_ADDITION_PATH].content == "binary"
    assert entries[BINARY_ADDITION_PATH].status == "added"
    assert entries[BINARY_ADDITION_PATH].mode_change is False
    assert entries[CODE_FILE].mode_change is True
    assert entries[CODE_FILE].status == "modified"
    assert entries[CODE_FILE].content == "text"
    assert "limitation:source_inventory_partial" not in result.payload.limitations
    assert result.payload.source.inventory.partial is False


def _write_non_utf8_addition(worktree: Path) -> None:
    with open(os.fsencode(worktree) + b"/" + NON_UTF8_ADDITION, "wb") as handle:
        handle.write(b"# a name that is not text\n")


def test_a_non_utf8_pathname_leaves_the_review_openable_and_states_why_it_is_partial(
    source_world: World,
) -> None:
    world = source_world
    _write_non_utf8_addition(world.code_worktree)
    resolved = _public_resolution(world)
    with TestClient(_source_app(world), raise_server_exceptions=False) as client:
        response = client.get("/api/review/intent", params=_source_params())
    assert response.status_code == 200, response.text
    payload = response.json()["payload"]
    inventory = payload["source"]["inventory"]
    assert inventory["state"] == "measured" and inventory["partial"] is True
    assert [entry["path_bytes"] for entry in inventory["unrepresentable_paths"]] == [
        NON_UTF8_BYTE_FORM
    ]
    assert inventory["unrepresentable_paths"][0]["status"] == "added"
    assert (
        NON_UTF8_BYTE_FORM in inventory["detail"]
        and "limitation:source_inventory_partial" in payload["limitations"]
    )
    expected = independent_changed_records(world.code, world.code_base, _captured_tree(resolved))
    assert inventory["listed_total"] == len(inventory["entries"]) == len(expected) - 1
    assert {CODE_FILE, STAGED_ADDITION_PATH, ELIGIBLE_UNTRACKED_PATH, UNMAPPED_PATH} <= {
        entry["path"] for entry in inventory["entries"]
    }


def test_a_task_context_review_states_a_damaged_half_and_still_lists_the_source_inventory(
    source_world: World,
) -> None:
    world = source_world
    app = FastAPI()

    def damaged_between_resolution_and_composition(
        request: ReviewSurfaceRequest,
    ) -> KnowledgeReviewResult:
        resolved = _public_resolution(world)
        resolved.baseline_database.write_bytes(b"this is not an index\n")
        return compose_review(resolved, request)

    register_review_routes(app, world.config, damaged_between_resolution_and_composition)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/review/intent", params=_source_params())
        named = client.get("/api/review/intent", params=_source_params(subject=True))
    assert response.status_code == 200, response.text
    payload = response.json()["payload"]
    assert payload["source"]["inventory"]["state"] == "measured"
    assert payload["source"]["inventory"]["listed_total"] > 0
    assert "limitation:knowledge_half_unreadable" in payload["limitations"]
    assert "baseline" in payload["knowledge"]["selection_detail"]
    assert (
        named.status_code == 404 and named.json()["refusal"]["code"] == "candidate_dataset_absent"
    )
