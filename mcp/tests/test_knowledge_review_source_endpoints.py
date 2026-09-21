"""The exact source endpoints a live curator review binds -- production composition, real Git.

``ICR-R01@v1`` requires a review generation to bind the actual source base and the selected
candidate to immutable Git object identities *before* it returns source content. These cases measure
that through the operations the dashboard really calls: a real enclosure contract on disk, a real
linked Git worktree with staged, unstaged and eligible untracked content, the real
:func:`~agents_remember.application.knowledge_review.read_knowledge_review` resolution, the real
capture owner and the real comparison. Nothing here injects a preconstructed resolution, a fake
index or a hand-built payload.

The load-bearing properties, one case each:

* the resolution binds the contract's **recorded base commit** on one side and the **captured
  add-all candidate tree** on the other, and leaves the real Git index byte-identical;
* the rendered review publishes those two ids and reaches the *whole* candidate -- a HEAD-to-unstaged
  range would silently miss an eligible untracked file, which is the packet's non-conforming example;
* a capture input that moves before publication is refused **by name** (the exact side, plus the two
  identities it compared) instead of being published as the candidate's comparison;
* a moved code HEAD names the head as the side that moved;
* a committed change-set range binds the two **recorded** commits, and a later commit on the branch
  does not move it; and
* a committed range nothing has recorded yet is refused by name while the working view stays
  available and labelled -- so "what is not committed yet" is never published as what landed.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from agents_remember.application.knowledge_review import (
    compose_review,
    read_knowledge_review,
    resolve_review_candidate,
)
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import (
    KnowledgeReviewPayload,
    ReviewSurfaceRequest,
)
from agents_remember.serving.changeset import ChangesetFileRef, leaf_changeset, leaf_file_diff
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

pytestmark = pytest.mark.evidence_unit

LEAF_ID = "260921-icr-l1"
WORKTREE_NAME = "icr-r01-l1"
TASK_NAME = "review-source-endpoints-fixture"

# The content the fixture's worktree holds, per index state. The tracked paths reproduce the diff
# fixture's own candidate bytes so the recorded anchors in the candidate dataset observe exactly the
# blobs it recorded -- that is what makes "the source opens" a measurement rather than a claim.
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
    """One live leaf enclosure: real contract, real worktree, real review datasets."""

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
        resolved = resolve_review_candidate(self.config, self.repository_id, self.master, LEAF_ID)
        assert isinstance(resolved, ReviewCandidateResolution), resolved
        return resolved

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
    """Build the diff fixture's two datasets inside a real leaf enclosure with a real worktree.

    ``datasets=False`` is the never-initialized task: the leaf has a real recorded base, a real
    worktree and a real captured candidate, and the two knowledge halves simply do not exist. That is
    the state the packet's "no knowledge at all" exercise is about, and it is a state of the *task*
    rather than a broken fixture.

    ``memory_mode="external"`` adds the other half of the enclosure a later leaf's cases need: a real
    external-memory repository and its linked worktree, on the same contract. It is a parameter rather
    than a second builder because every other fact about the enclosure -- the recorded base, the
    captured candidate, the two datasets -- is the same one, and two builders would be two fixtures
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
    """Put the two real datasets where the resolution reads them: the leaf's disposable root."""

    root = contract.worktree_group / "provider-runtime" / "dev-ar-coordination" / "knowledge"
    for half, database in (
        ("baseline", diff.before.database_path),
        ("candidate", diff.after.database_path),
    ):
        (root / half).mkdir(parents=True, exist_ok=True)
        (root / half / "knowledge-candidate.sqlite").write_bytes(database.read_bytes())


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


def _changed_paths_of(payload: KnowledgeReviewPayload) -> set[str]:
    """Every path the published comparison reports as changed, attributed or not."""

    source = payload.source
    return set(source.attributed_changed_paths) | set(source.unattributed_changed_paths)


# -- the bound endpoints -----------------------------------------------------------------------


def test_the_live_candidate_binds_the_recorded_base_and_the_captured_tree(
    endpoint_fixture: EndpointFixture,
) -> None:
    """The endpoints are the recorded base commit and the captured tree; the real index is untouched.

    The index digest is taken before and after the capture, because "the user's work is unchanged"
    is a claim about bytes and not about intent: an add-all capture that refreshed or rewrote the
    real index would leave it different here.
    """

    fixture = endpoint_fixture
    index = _index_path(fixture.worktree)
    before_capture = _digest(index)

    resolved = fixture.resolve()

    assert _digest(index) == before_capture, "the capture rewrote the real Git index"
    assert resolved.baseline_code_tree_id == fixture.contract.code_base_commit
    assert (
        resolved.candidate_code_tree_id
        == capture_future_code_candidate(fixture.contract).codeCandidateTree
    )
    # Root and tree travel together on both sides: a tree id with no repository is unresolvable, and
    # a repository with no tree id would be a licence to read a working tree.
    assert resolved.baseline_code_root == fixture.contract.code_repo_path
    assert resolved.candidate_code_root == fixture.contract.code_worktree
    candidate_paths = _tree_paths(fixture.contract.code_repo_path, _captured_tree(resolved))
    assert {STAGED_ADDITION_PATH, ELIGIBLE_UNTRACKED_PATH, UNMAPPED_PATH} <= candidate_paths
    assert MODIFIED_PATH in candidate_paths
    assert SYNCHRONIZATION_PATH not in candidate_paths
    # The packet's boundary example: ignored files stay excluded by the existing policy.
    assert IGNORED_PATH not in candidate_paths


def test_the_rendered_review_publishes_the_endpoints_and_reaches_the_whole_candidate(
    endpoint_fixture: EndpointFixture,
) -> None:
    """The payload names both ids and its source half covers staged, unstaged and untracked content.

    The untracked addition is the falsifier for the packet's non-conforming example: a review opened
    against ``HEAD``-to-unstaged -- whatever it was labelled -- cannot reach a file that is in no
    commit and no index entry, so its presence in the published changed paths is what distinguishes
    the bound candidate from that range.
    """

    fixture = endpoint_fixture
    resolved = fixture.resolve()

    result = read_knowledge_review(fixture.config, fixture.request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    assert payload.comparison is not None
    assert payload.comparison.before_code_tree_id == fixture.contract.code_base_commit
    assert payload.comparison.after_code_tree_id == resolved.candidate_code_tree_id
    changed = _changed_paths_of(payload)
    assert ELIGIBLE_UNTRACKED_PATH in changed
    assert UNMAPPED_PATH in changed
    assert STAGED_ADDITION_PATH in changed
    assert SYNCHRONIZATION_PATH in changed
    assert IGNORED_PATH not in changed
    # The non-conforming range is measured here rather than merely asserted: the same worktree's
    # HEAD-to-unstaged diff cannot reach the untracked addition the published expansion reaches, so
    # the two ranges are demonstrably not the same population.
    head_to_unstaged = set(_git(fixture.worktree, ["diff", "--name-only", "HEAD"]).split())
    assert ELIGIBLE_UNTRACKED_PATH not in head_to_unstaged
    assert MODIFIED_PATH in head_to_unstaged
    # Source content is openable against the captured tree, and the published command names it.
    opened = [location for location in payload.source.locations if location.path == SUCCESSOR_PATH]
    assert opened, [location.path for location in payload.source.locations]
    assert {location.resolution for location in opened} == {"exact_recorded_blob"}
    assert _captured_tree(resolved) in (payload.source.expansion_command or "")
    # Displayed, not only returned: the same payload the route serializes to the browser carries
    # the two bound ids, read back out of the JSON body the client receives.
    served = FastAPI()
    register_review_routes(
        served, fixture.config, lambda request: read_knowledge_review(fixture.config, request)
    )
    with TestClient(served) as client:
        body = client.get(
            "/api/review/intent",
            params={
                "repo": fixture.repository_id,
                "master": fixture.master,
                "leaf": LEAF_ID,
                "selectorKind": "invariant",
                "selectorId": fixture.diff.retry_invariant_id,
            },
        )
    assert body.status_code == 200, body.text
    shown = body.json()["payload"]["comparison"]
    assert shown["before_code_tree_id"] == fixture.contract.code_base_commit
    assert shown["after_code_tree_id"] == _captured_tree(resolved)


# -- a moved input is a named state ------------------------------------------------------------


def test_a_capture_input_that_moves_before_publication_is_refused_by_name(
    endpoint_fixture: EndpointFixture,
) -> None:
    """A worktree edit between capture and publication refuses, naming the side and both ids."""

    fixture = endpoint_fixture
    resolved = fixture.resolve()
    (fixture.worktree / MODIFIED_PATH).write_text(
        "# batch\nedited while the review was being composed\n", encoding="utf-8"
    )
    moved_tree = capture_future_code_candidate(fixture.contract).codeCandidateTree
    assert moved_tree != _captured_tree(resolved)

    result = compose_review(resolved, fixture.request())

    assert result.state == "refused"
    assert result.payload is None
    refusal = result.refusal
    assert refusal is not None
    assert refusal.code == "candidate_unresolved"
    assert refusal.offending_input == "the captured candidate tree"
    assert refusal.expected is not None
    assert _captured_tree(resolved) in refusal.expected
    assert refusal.observed is not None and moved_tree in refusal.observed
    assert refusal.next_action


def test_a_capture_that_detects_a_moved_head_refuses_instead_of_publishing_a_stale_tree(
    endpoint_fixture: EndpointFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The capture owner's own mid-capture head check becomes the surface's named refusal.

    The head really moves while the capture runs -- the wrapper commits between the isolated index's
    ``write-tree`` and the owner's re-read of ``HEAD`` -- so the state this case measures is produced
    by the shipped detection rather than asserted from a raised fixture. What is protected is that
    the failure arrives as a typed refusal naming the candidate side and the retry action: an
    unhandled capture error escaping the review route, and a tree captured under a head that has
    already moved, are the two states this refusal exists to prevent.
    """

    fixture = endpoint_fixture
    original = capture_owner.worktree_candidate_tree

    def capture_then_move(repo: Path, index_path: Path, **kwargs: object) -> str:
        tree = original(repo, index_path, **kwargs)  # type: ignore[arg-type]
        _commit(fixture.worktree, "a commit made while the capture was running")
        return tree

    monkeypatch.setattr(capture_owner, "worktree_candidate_tree", capture_then_move)

    outcome = resolve_review_candidate(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID
    )

    assert not isinstance(outcome, ReviewCandidateResolution)
    assert outcome.code == "candidate_unresolved"
    assert outcome.offending_input == "candidate"
    assert "future-code-candidate-head-moved" in outcome.detail
    assert outcome.next_action


def test_a_moved_code_head_names_the_head_as_the_side_that_moved(
    endpoint_fixture: EndpointFixture,
) -> None:
    """Committing the captured content moves HEAD, and HEAD is what the refusal names."""

    fixture = endpoint_fixture
    resolved = fixture.resolve()
    captured = _captured_identity(resolved)
    landed = _commit(fixture.worktree, "commit the captured candidate")
    assert landed != captured.observedCodeHead

    result = compose_review(resolved, fixture.request())

    assert result.state == "refused"
    refusal = result.refusal
    assert refusal is not None
    assert refusal.offending_input == "the leaf worktree's code HEAD"
    assert refusal.expected is not None
    assert captured.observedCodeHead in refusal.expected
    assert refusal.observed is not None and landed in refusal.observed
    # The tree the commit produced is the tree that was captured, so no other side is reported: the
    # refusal names what moved and not what happened to stay the same.
    assert capture_future_code_candidate(fixture.contract).codeCandidateTree == _captured_tree(
        resolved
    )


# -- the committed range binds recorded endpoints -----------------------------------------------


def test_a_committed_range_binds_the_recorded_commit_and_a_later_commit_does_not_move_it(
    endpoint_fixture: EndpointFixture,
) -> None:
    """``committed`` is the recorded base -> recorded landed commit, for paths and for content."""

    fixture = endpoint_fixture
    landed = _commit(fixture.worktree, "land the candidate")
    recorded = fixture.recorded_range(landed)

    committed = leaf_changeset(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID, "committed"
    )
    assert committed["mode"] == "committed"
    assert {entry["path"] for entry in committed["code"]} >= {
        MODIFIED_PATH,
        ELIGIBLE_UNTRACKED_PATH,
        STAGED_ADDITION_PATH,
        SYNCHRONIZATION_PATH,
    }
    diff = leaf_file_diff(
        fixture.config,
        ChangesetFileRef(
            repo=fixture.repository_id,
            path=MODIFIED_PATH,
            kind="code",
            master=fixture.master,
            leaf=LEAF_ID,
            mode="committed",
        ),
    )
    assert diff["before"] == {"content": "# batch\none transaction\n"}
    assert diff["after"] == {"content": BATCH_PATH_CANDIDATE_TEXT}

    # A later commit on the branch moves HEAD; the recorded range is a task fact and does not move.
    (fixture.worktree / LOCAL_COMMIT_PATH).write_text(LOCAL_COMMIT_TEXT, encoding="utf-8")
    later_commit = _commit(fixture.worktree, "a later local commit")
    assert later_commit != landed
    later = leaf_changeset(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID, "committed"
    )
    assert later == committed
    assert LOCAL_COMMIT_PATH not in {entry["path"] for entry in later["code"]}
    assert recorded.code_commit == landed

    # The uncommitted view is its own, explicitly labelled population: the dirty file only.
    (fixture.worktree / UNCOMMITTED_PATH).write_text(UNCOMMITTED_TEXT, encoding="utf-8")
    working = leaf_changeset(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID, "working"
    )
    assert working["mode"] == "working"
    assert {entry["path"] for entry in working["code"]} == {UNCOMMITTED_PATH}


def test_an_unrecorded_committed_endpoint_is_refused_rather_than_read_from_head(
    endpoint_fixture: EndpointFixture,
) -> None:
    """A live leaf with commits but no recorded endpoint has no committed range, and says so."""

    fixture = endpoint_fixture
    head = _commit(fixture.worktree, "a commit nothing has recorded yet")
    assert fixture.contract.code_commit == ""

    with pytest.raises(FileNotFoundError) as caught:
        leaf_changeset(fixture.config, fixture.repository_id, fixture.master, LEAF_ID, "committed")

    message = str(caught.value)
    assert "no committed code range yet" in message
    assert "mode=working" in message
    assert head not in message
    # The working view keeps working and keeps its own name, so the uncommitted delta stays readable.
    assert (
        leaf_changeset(fixture.config, fixture.repository_id, fixture.master, LEAF_ID, "working")[
            "mode"
        ]
        == "working"
    )


def test_an_unrecorded_memory_half_empties_only_itself_and_keeps_the_code_half(
    endpoint_fixture: EndpointFixture,
) -> None:
    """One side's unrecorded endpoint never discards the other side's resolved range.

    The contract records the code landed commit and runs an external memory leg whose landed commit
    nothing has written yet. The memory half must degrade to "nothing to show" with its counters at
    zero -- the degradation this side has always published for a leaf whose memory leg is not run --
    because the code half was resolved from its own recorded commit and a whole-view refusal would
    throw that result away. The code side's own refusal stays exactly as it is (measured in the case
    above), and nothing here is answered from the worktree's ``HEAD``.
    """

    fixture = endpoint_fixture
    landed = _commit(fixture.worktree, "land the candidate")
    recorded = fixture.record_code_with_unrecorded_memory(landed)
    assert recorded.code_commit == landed
    assert recorded.memory_content_commit == ""

    view = leaf_changeset(
        fixture.config, fixture.repository_id, fixture.master, LEAF_ID, "committed"
    )

    assert view["mode"] == "committed"
    assert {entry["path"] for entry in view["code"]} >= {
        MODIFIED_PATH,
        ELIGIBLE_UNTRACKED_PATH,
        STAGED_ADDITION_PATH,
        SYNCHRONIZATION_PATH,
    }
    assert view["counters"]["code"]["files"] == len(view["code"])
    assert view["memory"] == []
    assert view["counters"]["memory"] == {"files": 0, "insertions": 0, "deletions": 0}


# --- the task-context entry and the complete source inventory (ICR-R02) --------------------------
#
# The entry is the task context. Every case below opens the review the way the dashboard does -- the
# task's own context, no reviewed subject -- through the real resolution, the real capture and the
# real route, and compares the inventory it returns against an independent Git observation of the
# same bound pair rather than against a list written here.

UNUSUAL_PATH = "src/tab\tnewline\nname.py"
BINARY_ADDITION_PATH = "assets/blob.dat"


def test_a_task_context_review_lists_the_complete_source_inventory_with_no_knowledge_at_all(
    tmp_path: Path,
) -> None:
    """The packet's entry case: a never-initialized task still opens its complete source review.

    The leaf has a recorded base, a live worktree and a captured candidate, and neither knowledge half
    exists. The inventory must be the *whole* change set of the bound pair -- compared here against an
    independent Git observation of those two exact objects -- and the knowledge pane must state that
    no operand was compared rather than rendering an empty one. The subject route keeps its own
    missing-dataset refusal, so nothing here softens an existing named state.
    """

    fixture = build_endpoint_fixture(tmp_path / "task-context", datasets=False)
    resolved = fixture.resolve()
    candidate_tree = _captured_tree(resolved)

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    # No comparison identity is published, because none was made, and the staleness state says so in
    # its own word rather than borrowing "current".
    assert payload.comparison is None
    assert payload.staleness.state == "not_compared"
    assert payload.knowledge.selection_state == "task_context"
    assert "absent" in (payload.knowledge.selection_detail or "")
    assert payload.knowledge.before_statement.state == "unresolved"
    assert payload.source.inventory.state == "measured"
    assert payload.source.inventory.partial is False

    expected = independent_changed_records(
        fixture.contract.code_repo_path, fixture.contract.code_base_commit, candidate_tree
    )
    listed = [entry.path for entry in payload.source.inventory.entries]
    assert sorted(listed) == sorted(expected)
    assert payload.source.inventory.listed_total == len(listed) == len(expected)
    assert {
        MODIFIED_PATH,
        SYNCHRONIZATION_PATH,
        STAGED_ADDITION_PATH,
        ELIGIBLE_UNTRACKED_PATH,
        UNMAPPED_PATH,
    } <= set(listed)
    assert IGNORED_PATH not in listed
    assert payload.source.inventory.before_code_tree_id == fixture.contract.code_base_commit
    assert payload.source.inventory.after_code_tree_id == candidate_tree
    assert "limitation:no_knowledge_subject_selected" in payload.limitations
    assert "limitation:source_inventory_unavailable" not in payload.limitations

    # The entry is reachable through the real transport with NO selector parameters, which is what
    # makes "the task context is the entry" a served behaviour rather than an internal one.
    served = FastAPI()
    register_review_routes(
        served, fixture.config, lambda request: read_knowledge_review(fixture.config, request)
    )
    with TestClient(served) as client:
        body = client.get(
            "/api/review/intent",
            params={
                "repo": fixture.repository_id,
                "master": fixture.master,
                "leaf": LEAF_ID,
            },
        )
    assert body.status_code == 200, body.text
    served_payload = body.json()["payload"]
    assert served_payload["staleness"]["state"] == "not_compared"
    assert "comparison" not in served_payload
    assert sorted(
        entry["path"] for entry in served_payload["source"]["inventory"]["entries"]
    ) == sorted(expected)

    # The subject route is untouched: a named subject with no datasets is still refused by name.
    named = read_knowledge_review(fixture.config, fixture.request())
    assert named.state == "refused"
    assert named.refusal is not None and named.refusal.code == "candidate_dataset_absent"


def test_the_production_inventory_keeps_an_unusual_filename_as_the_address_it_expands_by(
    tmp_path: Path,
) -> None:
    """A tab and a newline inside a name survive the capture, the resolution and the payload.

    The file is written into the leaf's real worktree and reaches the candidate through the shipped
    capture owner, so the path the payload publishes is the address of a file the leaf really holds
    -- and the line-oriented Git question about the same pair is shown losing it.
    """

    fixture = build_endpoint_fixture(tmp_path / "unusual-name")
    (fixture.worktree / UNUSUAL_PATH).write_text(
        "a name that is not a separator\n", encoding="utf-8"
    )
    resolved = fixture.resolve()
    candidate_tree = _captured_tree(resolved)

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    listed = [entry.path for entry in payload.source.inventory.entries]
    assert UNUSUAL_PATH in listed
    assert "\\t" not in listed
    assert sorted(listed) == sorted(
        independent_changed_records(
            fixture.contract.code_repo_path, fixture.contract.code_base_commit, candidate_tree
        )
    )
    unusual = next(
        entry for entry in payload.source.inventory.entries if entry.path == UNUSUAL_PATH
    )
    assert unusual.status == "added"
    assert unusual.content == "text"
    # The same address reaches the file the leaf holds, which is what "used for file expansion" means.
    assert (fixture.worktree / unusual.path).is_file()
    line_oriented = _git(
        fixture.contract.code_repo_path,
        ["diff", "--name-only", "--no-renames", fixture.contract.code_base_commit, candidate_tree],
    )
    assert UNUSUAL_PATH not in line_oriented.splitlines()


def test_the_production_inventory_lists_non_text_and_mode_changed_paths_it_cannot_render(
    tmp_path: Path,
) -> None:
    """A binary addition and a mode-only change stay listed, each with the fact that says so."""

    fixture = build_endpoint_fixture(tmp_path / "non-text")
    (fixture.worktree / BINARY_ADDITION_PATH).parent.mkdir(parents=True, exist_ok=True)
    (fixture.worktree / BINARY_ADDITION_PATH).write_bytes(b"binary\x00content\n")
    (fixture.worktree / MODIFIED_PATH).chmod(0o755)
    resolved = fixture.resolve()
    candidate_tree = _captured_tree(resolved)

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    entries = {entry.path: entry for entry in payload.source.inventory.entries}
    assert sorted(entries) == sorted(
        independent_changed_records(
            fixture.contract.code_repo_path, fixture.contract.code_base_commit, candidate_tree
        )
    )
    assert entries[BINARY_ADDITION_PATH].content == "binary"
    assert entries[BINARY_ADDITION_PATH].status == "added"
    assert entries[MODIFIED_PATH].mode_change is True
    assert entries[MODIFIED_PATH].status == "modified"
    assert "limitation:source_inventory_partial" not in payload.limitations


NON_UTF8_ADDITION = b"src/caf\xe9-latin1.py"
NON_UTF8_BYTE_FORM = "b'src/caf\\xe9-latin1.py'"


def _write_non_utf8_addition(worktree: Path) -> None:
    """Create the leaf's one changed file whose name is not valid UTF-8.

    A Python ``str`` path cannot express this name -- the filesystem encoding would turn it into valid
    UTF-8 bytes -- so the file is created through the exact bytes, which is what makes the case real
    rather than simulated.
    """

    with open(os.fsencode(worktree) + b"/" + NON_UTF8_ADDITION, "wb") as handle:
        handle.write(b"# a name that is not text\n")


def test_a_non_utf8_pathname_leaves_the_review_openable_and_states_why_it_is_partial(
    tmp_path: Path,
) -> None:
    """A name that is not text is carried by its bytes; the route answers 200, never a crash.

    The runner preserves the change (``surrogateescape``), and this surface's text fields refuse the
    value that decoding produces. The packet's failure behaviour is a *stated* unknown: the review
    stays openable, the changed path is listed by its exact byte form, the renderable remainder is
    listed in full, and the response declares itself partial at the top level. The real route is
    exercised because "the review is openable" is a claim about the served behaviour: the same request
    raised a validation error and answered 500 before this boundary stated the fact.
    """

    fixture = build_endpoint_fixture(tmp_path / "non-utf8")
    _write_non_utf8_addition(fixture.worktree)
    resolved = fixture.resolve()
    candidate_tree = _captured_tree(resolved)

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    inventory = payload.source.inventory
    assert inventory.state == "measured"
    assert inventory.partial is True
    assert [entry.path_bytes for entry in inventory.unrepresentable_paths] == [NON_UTF8_BYTE_FORM]
    assert inventory.unrepresentable_paths[0].status == "added"
    assert NON_UTF8_BYTE_FORM in inventory.detail
    assert "limitation:source_inventory_partial" in payload.limitations
    # The renderable remainder is the whole independent observation minus the one uncarried path, so
    # the odd name costs exactly one entry and never the review.
    observed = independent_changed_records(
        fixture.contract.code_repo_path, fixture.contract.code_base_commit, candidate_tree
    )
    listed = [entry.path for entry in inventory.entries]
    assert inventory.listed_total == len(listed) == len(observed) - 1
    assert {
        MODIFIED_PATH,
        SYNCHRONIZATION_PATH,
        STAGED_ADDITION_PATH,
        ELIGIBLE_UNTRACKED_PATH,
        UNMAPPED_PATH,
    } <= set(listed)

    served = FastAPI()
    register_review_routes(
        served, fixture.config, lambda request: read_knowledge_review(fixture.config, request)
    )
    with TestClient(served, raise_server_exceptions=False) as client:
        body = client.get(
            "/api/review/intent",
            params={"repo": fixture.repository_id, "master": fixture.master, "leaf": LEAF_ID},
        )
    assert body.status_code == 200, body.text
    shown = body.json()["payload"]["source"]["inventory"]
    assert shown["partial"] is True
    assert [entry["path_bytes"] for entry in shown["unrepresentable_paths"]] == [NON_UTF8_BYTE_FORM]


def test_a_task_context_review_states_a_damaged_half_and_still_lists_the_source_inventory(
    tmp_path: Path,
) -> None:
    """The pair's preflight runs on the task-context route too, and is stated there rather than raised.

    This is the cross-leaf seam between two obligations that are both true at once: a half that is
    present and cannot be read as a dataset is a **named refusal** where a comparison would be made
    (asserted here on the subject route), and a **stated reason** where none is (this review reads no
    dataset at all, so refusing would take the whole source review with it -- the failure the
    task-context entry exists to remove). Both facts come from the same preflight, so neither route
    can answer for the pair while the other skips it.
    """

    fixture = build_endpoint_fixture(tmp_path / "damaged-half")
    resolved = fixture.resolve()
    resolved.baseline_database.write_bytes(b"this is not a database\n")

    result = read_knowledge_review(fixture.config, fixture.task_request())

    assert result.state == "review", result.refusal
    payload = result.payload
    assert payload is not None
    assert payload.source.inventory.state == "measured"
    assert payload.source.inventory.listed_total > 0
    assert "limitation:knowledge_half_unreadable" in payload.limitations
    detail = payload.knowledge.selection_detail or ""
    assert "present but cannot be read" in detail
    assert str(resolved.baseline_database) in detail

    refused = read_knowledge_review(fixture.config, fixture.request())

    assert refused.state == "refused"
    assert refused.refusal is not None
    assert refused.refusal.code == "candidate_dataset_absent"
    assert "baseline" in refused.refusal.detail
    assert "present but cannot be read" in refused.refusal.detail
