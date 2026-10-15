"""MIK-R40: a reviewer read does work in proportion to what changed, and answers what it answered.

Every case enters through the port the dashboard route calls (``cli.dashboard.serving_collaborators``)
on the tree-review fixture of ``test_review_git_trees``: a live leaf with an uncommitted code edit and
an uncommitted knowledge edit. The counts asserted here do not depend on the machine:

* one resolution per request (rule 2), at most two captures per worktree (one, and the recheck
  before publication), none of them the full capture, and a bounded number of Git children (rule 7);
* a converted base tree Git holds is not written again, and the recorded tree id is used only for
  the request's own inputs (rule 3);
* the leaf-wide view captures nothing itself, reads no memory-tree diff,
  and computes its worklist once per exact comparison (rule 4);
* a read after an edit -- also a same-size rewrite in the second of an index write -- reflects the
  new content, and nothing composed is kept on the server (rule 6).

The capture's own identity matrix is ``test_worktree_candidate_capture``.
"""

from __future__ import annotations

import json
import subprocess
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from agents_remember.application import (
    knowledge_review,
    review_candidate_resolution,
    review_leaf_view_memo,
    review_tree_comparison,
    review_tree_knowledge,
)
from agents_remember.application.knowledge_review import read_knowledge_review, review_records_for
from agents_remember.application.knowledge_worklist import leaf as worklist_leaf
from agents_remember.application.review_candidate_resolution import ReviewCandidateResolution
from agents_remember.application.review_leaf_view_memo import (
    CAPACITY,
    LEAF_VIEW_MEMO,
    MAX_AGE_SECONDS,
    LeafViewKey,
    LeafViewParts,
    leaf_view_key,
    remember,
    remembered,
)
from agents_remember.application.review_tree_comparison import ReviewTrees
from agents_remember.cli.dashboard import serving_collaborators
from agents_remember.kernel import git_command, reviewer_worklist_process
from agents_remember.kernel.recorded_reads import ABSENT, bytes_identity
from agents_remember.memory.knowledge_index import text_uuid
from agents_remember.models.knowledge.read import InvariantIdentitySeed
from agents_remember.models.knowledge.review import KnowledgeReviewResult, ReviewSurfaceRequest
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreesResult,
    ReviewWorklistView,
)
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.tasks import store as task_store
from agents_remember.worktrees.modules import capture_index
from agents_remember.worktrees.modules import git as capture_owner
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember.worktrees.worktree_contract import WorktreeContract
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    CODE_V1,
    INVARIANT,
    INVARIANT_PATH,
    LEAF,
    MASTER,
    REPO,
    World,
    commit,
    converted_memory,
    git,
    invariant,
)
from knowledge_index_test_support import rewrite_in_the_second_of_the_index_write
from test_review_git_trees import world  # noqa: F401 - ``world`` is the fixture

SUBJECT_READ_CHILDREN = 80  # the packet's bound for one subject read (rule 7)


@pytest.fixture(autouse=True)
def _fresh_process_state() -> Iterator[None]:
    LEAF_VIEW_MEMO.clear()
    yield
    LEAF_VIEW_MEMO.clear()
    reset_worktree_services()


@dataclass(frozen=True)
class Ports:
    """The four review ports of the dashboard's composition root, as the routes call them."""

    review: Callable[[ReviewSurfaceRequest], KnowledgeReviewResult]
    entries: Callable[[str, str, str], Any]
    summary: Callable[[str, str, str], Any]
    trees: Callable[[ReviewTreesQuery], ReviewTreesResult]


def _ports(world: World) -> Ports:  # noqa: F811
    ports = serving_collaborators(world.config)
    assert ports.knowledge_review is not None and ports.knowledge_review_entries is not None
    assert ports.review_intent_summary is not None and ports.review_trees is not None
    return Ports(
        review=ports.knowledge_review,
        entries=ports.knowledge_review_entries,
        summary=ports.review_intent_summary,
        trees=ports.review_trees,
    )


def _live(world: World, *, recorded: bool = False) -> tuple[WorktreeContract, ReviewTrees]:  # noqa: F811
    """The comparison a read resolves to now: its contract and its four trees."""

    resolved = review_candidate_resolution.resolve_review_candidate(
        world.config, REPO, MASTER, LEAF, recorded=recorded
    )
    assert isinstance(resolved, ReviewCandidateResolution), resolved
    assert resolved.contract is not None and resolved.trees is not None
    return resolved.contract, resolved.trees


def _real_indexes(world: World) -> dict[str, tuple[bytes, int, list[str]]]:  # noqa: F811
    """Each worktree's own index: its bytes, its time, and every lock file beside it."""

    seen = {}
    for worktree in (world.code_worktree, world.memory_worktree):
        index = Path(git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "index"))
        locks = sorted(path.name for path in index.parent.glob("*.lock"))
        seen[worktree.name] = (index.read_bytes(), index.stat().st_mtime_ns, locks)
    return seen


def _comparison(view: ReviewTreesResult) -> ReviewTreeComparisonRecord:
    assert view.comparison is not None
    return view.comparison


def _seed() -> InvariantIdentitySeed:
    return InvariantIdentitySeed(invariant_id=text_uuid("identity", INVARIANT))


def _query(**fields: Any) -> ReviewTreesQuery:
    return ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF, **fields)


def _body(result: Any, *, by_alias: bool = False) -> bytes:
    """The response body as the route serializes it."""

    data = result.model_dump(mode="json", exclude_none=True, by_alias=by_alias)
    return json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode()


class Measured:
    """What one request did: its Git children, its captures per worktree and its resolutions."""

    def __init__(self) -> None:
        self.children: list[str] = []
        self.arguments: list[tuple[str, ...]] = []
        self.captures: Counter[str] = Counter()
        self.full_captures = 0
        self.resolutions = 0

    @property
    def verbs(self) -> Counter[str]:
        return Counter(self.children)


def _verb(argv: list[str]) -> str:
    words = iter(argv[1:])
    for word in words:
        if word in ("-c", "-C"):
            next(words, None)
        elif not word.startswith("-"):
            return word
    return "?"


@contextmanager
def measured() -> Iterator[Measured]:
    seen = Measured()
    run = subprocess.run
    from_copy = capture_index._tree_from_index_copy
    from_head = capture_owner._tree_from_head
    resolve = review_candidate_resolution._live_resolution

    def counting_run(argv: Any, *args: Any, **kwargs: Any) -> Any:
        if argv and str(argv[0]) == "git":
            seen.children.append(_verb([str(word) for word in argv]))
            seen.arguments.append(tuple(str(word) for word in argv))
        return run(argv, *args, **kwargs)

    def counting_copy(repo: Path, *args: Any, **kwargs: Any) -> str:
        seen.captures[repo.name] += 1
        return from_copy(repo, *args, **kwargs)

    def counting_head(*args: Any, **kwargs: Any) -> str:
        seen.full_captures += 1
        return from_head(*args, **kwargs)

    def counting_resolution(*args: Any, **kwargs: Any) -> Any:
        seen.resolutions += 1
        return resolve(*args, **kwargs)

    with (
        mock.patch.object(git_command.subprocess, "run", counting_run),
        mock.patch.object(capture_index, "_tree_from_index_copy", counting_copy),
        mock.patch.object(capture_owner, "_tree_from_head", counting_head),
        mock.patch.object(review_candidate_resolution, "_live_resolution", counting_resolution),
    ):
        yield seen


# -- rules 2 and 7: one resolution, two captures, a bounded number of Git children ---------------------


def test_a_subject_read_resolves_once_and_captures_each_worktree_once_and_for_the_recheck(
    world: World,  # noqa: F811
) -> None:
    world.edit()
    indexes = _real_indexes(world)
    ports = _ports(world)
    request = world.review(selector=_seed())
    warm = ports.review(request)  # records the comparison and builds both indexes
    assert warm.state == "review", warm.refusal
    with measured() as seen:
        review = ports.review(request)
    assert review.state == "review" and _body(review) == _body(warm)
    # The answer is the one the two-resolution port gave: the composition over the candidate's
    # complete record collection.
    assert _body(review) == _body(
        read_knowledge_review(world.config, request, review_records_for(world.config, request))
    )
    assert review.payload is not None and review.payload.evidence.channels
    # The records half and the composition read one resolution (rule 2).
    assert seen.resolutions == 1
    # One capture per worktree for the resolution; the recheck before publication (ICR-R01)
    # still runs, but the resolve-time baseline proves the worktree unmoved, so it stands for
    # the remembered tree instead of deriving it again — one capture per worktree, never the
    # full capture, which hashes every file. The skip's meaning is pinned beside the subject
    # budget: a forced re-derivation beside a hit names the identical tree.
    assert seen.captures == {"code": 1, "memory": 1} and seen.full_captures == 0
    assert seen.verbs["mktree"] == 0 and seen.verbs["hash-object"] == 0
    assert len(seen.children) <= SUBJECT_READ_CHILDREN, seen.verbs

    # The other reads of the surface resolve once too and capture once: they publish no comparison
    # of their own that a moved candidate could be bound to.
    for read in (
        lambda: ports.entries(REPO, MASTER, LEAF),
        lambda: ports.summary(REPO, MASTER, LEAF),
        lambda: ports.trees(_query()),
    ):
        read()
        with measured() as seen:
            read()
        assert (seen.resolutions, seen.full_captures) == (1, 0)
        assert seen.captures == {"code": 1, "memory": 1}, seen.captures
    # The task-context read (no subject) publishes a comparison, so it rechecks like a subject read:
    # one capture per worktree, with the recheck standing for the remembered trees.
    with measured() as seen:
        assert ports.review(world.review()).state == "review"
    assert seen.resolutions == 1 and seen.captures == {"code": 1, "memory": 1}
    # No read wrote a worktree's own index or left a lock beside it (ICR-R01).
    assert _real_indexes(world) == indexes


def test_the_recheck_still_refuses_a_candidate_that_moved_while_the_review_was_composed(
    world: World,  # noqa: F811
) -> None:
    """Resolving once must not weaken ICR-R01: the answer is refused when either worktree changed
    between the resolution and the publication."""

    world.edit()
    ports = _ports(world)
    request = world.review(selector=_seed())
    assert ports.review(request).state == "review"
    compose = knowledge_review.compose_review

    def moving(side: str) -> Any:
        def composed(resolved: Any, *args: Any, **kwargs: Any) -> Any:
            if side == "memory":
                (world.memory_worktree / INVARIANT_PATH).write_text(
                    invariant(2, "Values land, moved meanwhile."), encoding="utf-8"
                )
            else:
                (world.code_worktree / "pkg" / "moved.py").write_text("M = 1\n", encoding="utf-8")
            return compose(resolved, *args, **kwargs)

        return composed

    for side, named in (("memory", "memory candidate"), ("code", "the captured candidate tree")):
        with mock.patch.object(knowledge_review, "compose_review", moving(side)):
            refused = ports.review(request)
        assert refused.state == "refused" and refused.refusal is not None
        assert refused.refusal.code == "candidate_unresolved"
        assert named in (refused.refusal.offending_input or ""), refused.refusal
        assert ports.review(request).state == "review"  # the next read captures again


# -- rule 3: a converted base tree Git holds is not written again -------------------------------------


def _unconverted_base(world: World) -> dict[str, bytes]:  # noqa: F811
    """The official line's base is unconverted (legacy onboarding only); the leaf is converted."""

    legacy_code = commit(world.code, {"pkg/c.py": "C = 1\n"})
    git(world.memory, "rm", "-q", "-r", "knowledge")
    commit(world.memory, {"onboarding/legacy.md": "# legacy\n"}, legacy_code)
    world.code_base = legacy_code
    world.contract()
    world.edit()
    converted = {
        **converted_memory(world.code, world.code_base),
        "onboarding/legacy.md": "# legacy\n",
    }
    return {path: text.encode() for path, text in converted.items()}


def test_a_converted_base_tree_git_holds_is_not_written_again(world: World) -> None:  # noqa: F811
    files = _unconverted_base(world)
    ports = _ports(world)
    request = world.review(selector=_seed())
    with mock.patch.object(
        review_tree_comparison, "converted_base_files", return_value=files
    ) as convert:
        # The first read has no record to learn the tree from: the conversion is written.
        with measured() as first:
            review = ports.review(request)
        assert review.state == "review", review.refusal
        assert first.verbs["mktree"] > 0 and convert.call_count == 1
        contract, trees = _live(world)
        base = trees.record.converted_base
        assert base is not None and trees.before.wire.tree == base.tree
        assert convert.call_count == 1  # that resolution took the tree from the record
        # The view's key names the tree K_B is compared as: its conversion, not K_B's own tree.
        key = leaf_view_key(contract, trees)
        assert key is not None and key.memory_before == base.tree != key.memory_base
        assert key.memory_base == trees.record.memory_base.tree

        # Every later read of the same inputs uses the recorded tree, once Git confirmed it: no
        # tree is written, no converted file is loaded, and the answer is the same. The
        # confirmation is the resolve-time baseline proof, so the recheck stands for the
        # remembered candidate instead of capturing each worktree a second time.
        with measured() as again:
            repeat = ports.review(request)
        assert _body(repeat) == _body(review)
        assert again.verbs["mktree"] == 0 and again.verbs["hash-object"] == 0
        assert convert.call_count == 1
        assert len(again.children) <= SUBJECT_READ_CHILDREN, again.verbs
        assert again.resolutions == 1 and again.captures == {"code": 1, "memory": 1}

        # A new comparison of the same leaf (the candidate changed) still names the same inputs.
        (world.code_worktree / "pkg" / "b.py").write_text("B = 1\n", encoding="utf-8")
        with measured() as edited:
            assert ports.review(request).state == "review"
        assert edited.verbs["mktree"] == 0 and convert.call_count == 1

        # A record whose inputs are not the request's names another conversion: never used.
        assert trees.record_path is not None
        directory = trees.record_path.parent

        def latest() -> Path:
            return max(directory.glob("*.json"), key=lambda path: int(path.stem))

        recorded = json.loads(latest().read_text())
        for field, other in (
            ("version", "0"),
            ("commit", world.memory_base),
            ("code_commit", git(world.code, "rev-parse", "HEAD~1")),
        ):
            changed = json.loads(json.dumps(recorded))
            changed["converted_base"][field] = other
            # The tree it names exists, so only the inputs can keep it from being used.
            changed["converted_base"]["tree"] = trees.record.memory_candidate.tree
            latest().write_text(json.dumps(changed), encoding="utf-8")
            with measured() as other_inputs:
                _contract, resolved = _live(world)
            assert other_inputs.verbs["mktree"] > 0, field
            assert resolved.record.converted_base == base
        calls = convert.call_count

        # A recorded tree Git can no longer produce is written again, exactly as before.
        for path in directory.glob("*.json"):
            document = json.loads(path.read_text())
            if document.get("converted_base"):
                document["converted_base"] = {**recorded["converted_base"], "tree": "1" * 40}
                path.write_text(json.dumps(document), encoding="utf-8")
        with measured() as missing:
            _contract, rewritten = _live(world)
        assert missing.verbs["mktree"] > 0 and convert.call_count == calls + 1
        assert rewritten.record.converted_base == base


# -- rule 4: the leaf-wide view ------------------------------------------------------------------------


def test_the_leaf_wide_view_captures_nothing_itself_and_is_computed_once_per_comparison(
    world: World,  # noqa: F811
) -> None:
    world.edit()
    ports = _ports(world)
    worklists = mock.Mock(wraps=review_tree_knowledge.isolated_leaf_worklist)
    snapshots = mock.Mock(wraps=worklist_leaf.directory_snapshot)
    code_captures = mock.Mock(wraps=worklist_leaf._captured_code)
    with (
        mock.patch.object(review_tree_knowledge, "isolated_leaf_worklist", worklists),
        mock.patch.object(worklist_leaf, "directory_snapshot", snapshots),
        mock.patch.object(worklist_leaf, "_captured_code", code_captures),
    ):
        with measured() as first:
            view = ports.trees(_query())
        assert view.state == "trees" and view.worklist is not None and view.comparison is not None
        # R86: neither a raw listing nor a patch of these memory trees belongs to the leaf view.
        assert not any(
            "diff" in args
            and view.comparison.memory_base.tree in args
            and view.comparison.memory_candidate.tree in args
            for args in first.arguments
        )
        # The worklist is handed the comparison's own two candidate trees and reads exactly those.
        assert worklists.call_count == 1
        candidate = worklists.call_args.kwargs["candidate"]
        assert candidate.code == view.comparison.code_candidate.tree
        assert candidate.memory == view.comparison.memory_candidate.tree
        base = worklists.call_args.kwargs["base"]
        assert base.code == view.comparison.code_base.commit
        assert base.memory == view.comparison.memory_base.commit
        assert view.worklist.source == "computed" and view.worklist.bound
        assert snapshots.call_count == 0 and code_captures.call_count == 0
        assert first.captures == {"code": 1, "memory": 1} and first.full_captures == 0

        # Unchanged trees: both worktrees are captured again, and nothing is computed again.
        with measured() as repeat:
            again = ports.trees(_query())
        assert _body(again, by_alias=True) == _body(view, by_alias=True)
        assert worklists.call_count == 1
        assert repeat.captures == {"code": 1, "memory": 1}
        assert repeat.verbs["diff"] == 0 and len(repeat.children) < len(first.children)
        # The client pins the view to the comparison it shows: the same kept parts answer.
        assert _body(ports.trees(_query(number=1)), by_alias=True) == _body(view, by_alias=True)
        assert worklists.call_count == 1

        # Every identity of the key is a different key. A code edit and a knowledge edit are other
        # trees; the contract and the leaf's task document are read outside the trees.
        (world.code_worktree / CODE_FILE).write_text(
            CODE_V1.replace("return value", "return value + 1"), encoding="utf-8"
        )
        code_edited = ports.trees(_query())
        assert worklists.call_count == 2
        assert code_edited.comparison.code_candidate.tree != view.comparison.code_candidate.tree  # type: ignore[union-attr]
        (world.memory_worktree / INVARIANT_PATH).write_text(
            invariant(2, "Values land exactly as stated."), encoding="utf-8"
        )
        memory_edited = ports.trees(_query())
        assert worklists.call_count == 3
        assert memory_edited.currentness is not None
        ports.trees(_query())
        assert worklists.call_count == 3
        contract = world.task_root / "enclosures" / LEAF.lower() / "series-contract.md"
        contract.write_text(
            contract.read_text().replace("task_id: 260101_TREE-REVIEW", "task_id: 260101_TREE"),
            encoding="utf-8",
        )
        ports.trees(_query())
        assert worklists.call_count == 4
        ports.trees(_query())
        assert worklists.call_count == 4


def test_a_view_behind_which_a_read_failed_is_never_kept(world: World) -> None:  # noqa: F811
    world.edit()
    ports = _ports(world)
    computed = review_tree_knowledge.isolated_leaf_worklist

    # An incomplete worklist run (an input it could not read) is returned and read again next time.
    def incomplete(*args: Any, **kwargs: Any) -> Any:
        document = dict(computed(*args, **kwargs) or {})
        return {**document, "state": "incomplete", "incomplete": [{"input": "git", "detail": "-"}]}

    with mock.patch.object(
        review_tree_knowledge, "isolated_leaf_worklist", side_effect=incomplete
    ) as runs:
        for expected in (1, 2):
            view = ports.trees(_query())
            assert view.worklist is not None and view.worklist.state == "incomplete"
            assert runs.call_count == expected
    assert len(LEAF_VIEW_MEMO) == 0

    # A settled worklist with two readable memory sides may be kept; no memory patch is read.
    recovered = ports.trees(_query())
    assert recovered.worklist is not None and recovered.worklist.state == "complete"
    assert len(LEAF_VIEW_MEMO) == 1

    # A file the computation read outside the trees and that changed since is a miss.
    key = next(iter(LEAF_VIEW_MEMO._entries))
    held = LEAF_VIEW_MEMO.get(key)[0]  # type: ignore[index]
    settings = world.root / "outside-the-trees.md"
    settings.write_text("one\n", encoding="utf-8")
    LEAF_VIEW_MEMO.clear()
    remember(key, held.parts, {**dict(key.task_reads), settings.as_posix(): "sha256:" + "0" * 64})
    assert remembered(key) is None
    remember(key, held.parts, {**dict(key.task_reads), settings.as_posix(): "conflicting reads"})
    assert remembered(key) is None  # a file read twice with different bytes: the kept one stays out
    assert len(LEAF_VIEW_MEMO) == 0


def test_an_unreadable_task_input_is_observed_and_never_memoized_even_when_the_worklist_completes(
    world: World,  # noqa: F811
) -> None:
    """An unrelated unreadable task JSON keeps the existing complete answer, with no kept view."""

    world.edit()
    ports = _ports(world)
    path = world.task_root / "task.json"
    read = Path.read_bytes

    for problem, identity in (
        (PermissionError, "unreadable (PermissionError)"),
        (FileNotFoundError, ABSENT),
    ):
        LEAF_VIEW_MEMO.clear()

        def denied(source: Path, failure: type[OSError] = problem) -> bytes:
            if source == path:
                raise failure("the task JSON cannot be read")
            return read(source)

        reply = reviewer_worklist_process._reply

        def child_observed(
            data: bytes, request: Any, decode: Any = reply, seen: str = identity
        ) -> Any:
            result = decode(data, request)
            # The original failure now occurs in two interpreters; replay the child's same
            # read failure, rather than pretend a parent-only monkeypatch also changes it.
            result["reads"][path.as_posix()] = seen
            return result

        with (
            mock.patch.object(Path, "read_bytes", denied),
            mock.patch.object(reviewer_worklist_process, "_reply", child_observed),
            mock.patch.object(
                task_store, "record_read", wraps=task_store.record_read
            ) as observations,
            mock.patch.object(
                review_tree_knowledge,
                "isolated_leaf_worklist",
                wraps=review_tree_knowledge.isolated_leaf_worklist,
            ) as runs,
        ):
            for expected in (1, 2):
                answer = ports.trees(_query())
                assert answer.worklist is not None and answer.worklist.state == "complete"
                assert runs.call_count == expected and len(LEAF_VIEW_MEMO) == 0
            assert mock.call(path, identity) in observations.call_args_list
        recovered = ports.trees(_query())
        assert _body(recovered, by_alias=True) == _body(answer, by_alias=True)
        assert len(LEAF_VIEW_MEMO) == 1


def _restore_task_document(path: Path, data: bytes | None) -> None:
    if data is None:
        path.unlink()
    else:
        path.write_bytes(data)


def test_a_task_edit_restored_during_computation_refuses_and_the_next_read_uses_original_inputs(
    world: World,  # noqa: F811
) -> None:
    """L40-R1-F3: both real task lookups bind their consumed bytes, including an absent leaf."""

    world.edit()
    ports = _ports(world)
    path = world.task_root / "leaf.json"
    document = {
        "id": LEAF,
        "slug": "leaf",
        "title": "Leaf",
        "kind": "subTask",
        "repo": REPO,
        "createdAt": "2026-10-02T00:00+02:00",
    }
    original = json.dumps(document).encode()
    altered = json.dumps(
        {
            **document,
            "expectedKnowledgeEffects": [
                {
                    "subject": "invariant:INV-ZZZZZZ",
                    "effect": "clarify",
                    "requirementRef": "MIK-R40@v1",
                }
            ],
        }
    ).encode()
    worklist = review_tree_knowledge.isolated_leaf_worklist
    expected_effects = worklist_leaf.leaf_expected_effects
    for timing in ("before-both", "between-lookups", "absent-then-present"):
        LEAF_VIEW_MEMO.clear()
        stable = None if timing == "absent-then-present" else original
        _restore_task_document(path, stable)
        baseline = ports.trees(_query())
        assert baseline.worklist is not None and baseline.worklist.state == "complete"
        LEAF_VIEW_MEMO.clear()
        consumed: list[dict[str, Any]] = []

        def restore_after_effects(contract: WorktreeContract) -> Any:
            effects = expected_effects(contract)
            path.write_bytes(original)
            return effects

        def race(
            *args: Any,
            when: str = timing,
            captured: list[dict[str, Any]] = consumed,
            restored: bytes | None = stable,
            **kwargs: Any,
        ) -> Any:
            try:
                path.write_bytes(altered)
                if when == "between-lookups":
                    with mock.patch.object(
                        worklist_leaf, "leaf_expected_effects", restore_after_effects
                    ):
                        result = worklist(*args, **kwargs)
                else:
                    result = worklist(*args, **kwargs)
                captured.append(result or {})
                return result
            finally:
                _restore_task_document(path, restored)

        with mock.patch.object(review_tree_knowledge, "isolated_leaf_worklist", race):
            refused = ports.trees(_query())
        assert consumed[0]["state"] == "complete"
        assert len(consumed[0]["items"]) > len(baseline.worklist.items)
        assert refused.state == "refused" and refused.refusal is not None
        # The read is computed once more on the state that exists now (MIK-R42); this race repeats
        # itself, so the answer is the accurate "inputs keep changing", never a kept view.
        assert len(consumed) == 2
        assert refused.refusal.code == "inputs_changing"
        assert str(path) in (refused.refusal.offending_input or "")
        assert "retry" in refused.refusal.next_action
        assert len(LEAF_VIEW_MEMO) == 0
        served = ports.trees(_query())
        LEAF_VIEW_MEMO.clear()
        fresh = ports.trees(_query())
        assert (
            _body(served, by_alias=True)
            == _body(fresh, by_alias=True)
            == _body(baseline, by_alias=True)
        )
        key = next(iter(LEAF_VIEW_MEMO._entries))
        held = LEAF_VIEW_MEMO.get(key)[0]  # type: ignore[index]
        if timing != "absent-then-present":
            assert dict(held.reads)[str(path)] == bytes_identity(original)


def test_the_view_memo_is_bounded_by_count_and_by_age_and_never_answers_another_key(
    world: World,  # noqa: F811
) -> None:
    world.edit()
    contract, trees = _live(world)
    key = leaf_view_key(contract, trees)
    assert key is not None
    assert key.gate.code_tree == trees.record.code_candidate.tree
    assert key.gate.memory_tree == trees.record.memory_candidate.tree
    assert key.gate.parent_memory_tip == world.memory_base
    assert key.gate.leaf_memory_head == git(world.memory_worktree, "rev-parse", "HEAD")
    assert (key.code_base, key.memory_base, key.memory_before) == (
        trees.record.code_base.tree,
        trees.record.memory_base.tree,
        trees.before.wire.tree,
    )
    parts = LeafViewParts(ReviewWorklistView(source="computed", state="complete"))

    def other(number: int) -> LeafViewKey:
        return replace(key, memory_before=f"{number:040x}")

    # Bounded by count at two sizes: the oldest keys are gone, the newest are served.
    for size in (CAPACITY - 1, CAPACITY + 3):
        LEAF_VIEW_MEMO.clear()
        for number in range(size):
            remember(other(number), parts, dict(key.task_reads))
        assert len(LEAF_VIEW_MEMO) == min(size, CAPACITY)
        assert remembered(other(size - 1)) is parts
        assert (remembered(other(0)) is None) is (size > CAPACITY)
    # Bounded by age, and a key that was never computed is never answered.
    remember(key, parts, dict(key.task_reads))
    now = time.monotonic()
    assert remembered(key, now=now) is parts
    assert remembered(key, now=now + MAX_AGE_SECONDS + 1) is None
    assert remembered(other(999)) is None
    # A recorded comparison is not a live one, whether or not its worktrees are still there, and
    # a leaf whose memory HEAD cannot be read has no key.
    recorded_contract, recorded = _live(world, recorded=True)
    assert recorded.record == trees.record and not recorded.live
    assert recorded_contract.memory_worktree == world.memory_worktree
    assert leaf_view_key(recorded_contract, recorded) is None
    with mock.patch.object(review_leaf_view_memo, "head_commit", side_effect=RuntimeError("no")):
        assert leaf_view_key(contract, trees) is None
    with mock.patch.object(review_leaf_view_memo, "parent_memory_tip", return_value=None):
        assert leaf_view_key(contract, trees) is None  # the line K_B pairs on cannot be named


# -- rule 6: never a stale answer ---------------------------------------------------------------------


def test_a_read_after_an_edit_shows_the_new_content_and_nothing_composed_is_kept(
    world: World,  # noqa: F811
) -> None:
    world.edit()
    ports = _ports(world)
    request = world.review(selector=_seed())

    def read() -> tuple[str, str, str]:
        review = ports.review(request)
        assert review.state == "review" and review.payload is not None, review.refusal
        inventory = review.payload.source.inventory
        tree = next(
            token.rsplit(":", 1)[-1]
            for token in review.payload.limitations
            if token.startswith("review:trees:")
        )
        return (
            review.payload.knowledge.after_statement.text or "",
            inventory.after_code_tree_id or "",
            tree,
        )

    # Two identical reads are two compositions over two captures: nothing composed is kept.
    composed = mock.Mock(wraps=knowledge_review.compose_review)
    with mock.patch.object(knowledge_review, "compose_review", composed), measured() as seen:
        first, second = read(), read()
    assert first == second and first[0] == "Values land exactly as given."
    assert composed.call_count == 2 and seen.resolutions == 2
    # Two reads, one capture per worktree per read: each read's recheck proves its worktrees
    # unmoved and stands for the remembered trees instead of capturing twice.
    assert seen.captures == {"code": 2, "memory": 2}

    # An edit of a knowledge file, then of a code file: the next read is the new comparison.
    (world.memory_worktree / INVARIANT_PATH).write_text(
        invariant(2, "Values land exactly as written."), encoding="utf-8"
    )
    edited = read()
    assert edited[0] == "Values land exactly as written." and edited[2] != first[2]
    (world.code_worktree / CODE_FILE).write_text(
        CODE_V1.replace("return value", "return value + 2"), encoding="utf-8"
    )
    code_edited = read()
    assert code_edited[1] != edited[1] and code_edited[2] != edited[2]

    # A same-size rewrite in the second the worktree's index was written, on each side.
    rewrite_in_the_second_of_the_index_write(
        world.memory_worktree,
        world.memory_worktree / INVARIANT_PATH,
        invariant(2, "Values land exactly as printed."),
    )
    rewritten = read()
    assert rewritten[0] == "Values land exactly as printed." and rewritten[2] != code_edited[2]
    rewrite_in_the_second_of_the_index_write(
        world.code_worktree,
        world.code_worktree / CODE_FILE,
        CODE_V1.replace("return value", "return value + 3"),
    )
    code_rewritten = read()
    assert code_rewritten[1] != rewritten[1]
    assert git(world.code, "rev-parse", f"{code_rewritten[1]}:{CODE_FILE}") == git(
        world.code_worktree, "hash-object", CODE_FILE
    )
    # The leaf-wide view of the rewritten worktrees is computed for them, not served from before.
    before_view = ports.trees(_query())
    rewrite_in_the_second_of_the_index_write(
        world.memory_worktree,
        world.memory_worktree / INVARIANT_PATH,
        invariant(2, "Values land exactly as granted."),
    )
    after_view = ports.trees(_query())
    assert (
        _comparison(after_view).memory_candidate.tree
        != _comparison(before_view).memory_candidate.tree
    )
    assert read()[0] == "Values land exactly as granted."
