import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from threading import Event

from agents_remember.application import review_comparison_preparation as module
from agents_remember.application import review_tree_comparison as native_owner
from agents_remember.application.review_answer_preparation import RecordedReviewAnswers
from agents_remember.application.review_comparison_preparation import (
    PreparedComparison,
    SharedComparisonPreparation,
)
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.kernel.recorded_reads import recorded_reads
from agents_remember.models.knowledge.review_trees import ReviewTreeComparisonRecord
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery
from agents_remember.worktrees.services import reset_worktree_services
from agents_remember_test_support.testing.review_git_trees import (
    CODE_FILE,
    LEAF,
    MASTER,
    REPO,
    build_world,
    git,
)


def _comparison(prepared: PreparedComparison) -> ReviewTreeComparisonRecord:
    record = prepared.result.comparison
    assert record is not None
    return record


def test_page_and_explainer_share_native_comparison(tmp_path: Path, monkeypatch):
    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config,
        RecordedReviewAnswers(
            world.config,
            RecordedAnswerReuse(directory=tmp_path / "answers", build_namespace="test"),
        ),
    )
    calls = []
    original = module.resolve_tree_comparison

    def resolve(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(module, "resolve_tree_comparison", resolve)
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF, view="overview")
    first = owner.prepare(query)
    assert isinstance(first, PreparedComparison), first
    assert first.witness.current()
    second = owner.prepare(replace(query, view="complete"))
    assert isinstance(second, PreparedComparison)
    assert first.result == second.result
    assert len(calls) == 1
    assert first.result.worklist is None
    assert first.result.entry_inventory is None
    reset_worktree_services()


def test_changed_code_invalidates_before_reuse(tmp_path: Path):
    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    first = owner.prepare(query)
    assert isinstance(first, PreparedComparison), first
    (world.code_worktree / CODE_FILE).write_text(
        "def changed_after_preparation():\n    return 12\n"
    )
    assert not first.witness.current()
    second = owner.prepare(query)
    assert isinstance(second, PreparedComparison), second
    assert _comparison(second).code_candidate.tree != _comparison(first).code_candidate.tree
    reset_worktree_services()


def test_native_admitted_component_is_persisted_and_restored(tmp_path: Path):
    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    folder = tmp_path / "answers"

    def owner():
        return SharedComparisonPreparation(
            world.config,
            RecordedReviewAnswers(
                world.config, RecordedAnswerReuse(directory=folder, build_namespace="native-test")
            ),
        )

    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    first = owner().prepare(query)
    assert isinstance(first, PreparedComparison), first
    assert tuple(folder.glob("*.review.json"))
    second = owner().prepare(query)
    assert isinstance(second, PreparedComparison), second
    assert first.result.model_dump(mode="json") == second.result.model_dump(mode="json")
    reset_worktree_services()


def test_new_untracked_file_revokes_existing_comparison(tmp_path: Path):
    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    first = owner.prepare(query)
    assert isinstance(first, PreparedComparison), first
    (world.code_worktree / "new.py").write_text("UNTRACKED = True\n")
    assert not first.witness.current()
    second = owner.prepare(query)
    assert isinstance(second, PreparedComparison), second
    assert _comparison(first).code_candidate.tree != _comparison(second).code_candidate.tree
    reset_worktree_services()


def test_staged_input_uses_strict_native_recheck(tmp_path: Path):

    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    git(world.code_worktree, "add", CODE_FILE)
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    prepared = owner.prepare(query)
    assert isinstance(prepared, PreparedComparison), prepared
    assert prepared.witness.code is None or not prepared.witness.code.current()
    assert prepared.witness.current()  # Native re-derivation preserves the valid input.
    (world.code_worktree / CODE_FILE).write_text("def staged_then_edited():\n    return 7\n")
    assert not prepared.witness.current()
    reset_worktree_services()


def test_same_binding_record_bytes_still_invalidate(tmp_path: Path):

    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    prepared = owner.prepare(query)
    assert isinstance(prepared, PreparedComparison), prepared
    record = next(comparison_directory(world.task_root, LEAF).glob("*.json"))
    value = json.loads(record.read_text())
    value["recordedAt"] = "2026-10-25T12:00:00+00:00"
    record.write_text(json.dumps(value))
    assert not prepared.witness.current()
    reset_worktree_services()


def test_joined_caller_gets_native_observations(tmp_path, monkeypatch):

    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    entered, release, joined = Event(), Event(), Event()
    calls = []
    original = module.resolve_tree_comparison

    def resolve(*args):
        calls.append(1)
        value = original(*args)
        entered.set()
        assert release.wait(
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            5
        )
        return value

    monkeypatch.setattr(module, "resolve_tree_comparison", resolve)

    def read():
        with recorded_reads() as observations:
            value = owner.prepare(query)
        return value, observations

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(read)
        assert entered.wait(
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            5
        )
        pending = next(iter(owner._flights._flights.values())).future
        original_result = pending.result

        def waiting(*args, **kwargs):
            joined.set()
            return original_result(*args, **kwargs)

        monkeypatch.setattr(pending, "result", waiting)
        second = pool.submit(read)
        assert joined.wait(
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            5
        )
        release.set()
        first_value, _first_reads = first.result()
        second_value, second_reads = second.result()
    assert isinstance(first_value, PreparedComparison)
    assert isinstance(second_value, PreparedComparison)
    assert len(calls) == 1
    assert second_reads == dict(first_value.witness.reads)
    assert second_reads
    reset_worktree_services()


def test_last_check_change_refuses_owner_and_joiner(tmp_path, monkeypatch):

    reset_worktree_services()
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    prepared = owner.prepare(query)
    assert isinstance(prepared, PreparedComparison)
    owner._kept.clear()
    entered, release, joined = Event(), Event(), Event()

    def prepare(_query):
        entered.set()
        assert release.wait(
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            5
        )
        return prepared

    monkeypatch.setattr(owner, "_prepare", prepare)
    monkeypatch.setattr(module.ComparisonInputWitness, "current", lambda _self: False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(owner.prepare, query)
        assert entered.wait(
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            5
        )
        pending = next(iter(owner._flights._flights.values())).future
        result = pending.result

        def wait(*args, **kwargs):
            joined.set()
            return result(*args, **kwargs)

        monkeypatch.setattr(pending, "result", wait)
        second = pool.submit(owner.prepare, query)
        assert joined.wait(
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            5
        )
        release.set()
        for future in (first, second):
            value = future.result()
            assert not isinstance(value, PreparedComparison)
            assert value.state == "refused"
            assert value.refusal is not None
            assert value.refusal.code == "inputs_changing"
    assert not owner._kept
    reset_worktree_services()


def test_record_rewrite_between_native_read_and_stamp_refuses(tmp_path, monkeypatch):

    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    original = module.resolve_tree_comparison

    def resolve(*args):
        native = original(*args)
        path = next(comparison_directory(world.task_root, LEAF).glob("*.json"))
        value = json.loads(path.read_text())
        value["recordedAt"] = "2026-10-25T12:00:00+00:00"
        path.write_text(json.dumps(value))
        return native

    monkeypatch.setattr(module, "resolve_tree_comparison", resolve)
    value = owner.prepare(query)
    assert not isinstance(value, PreparedComparison)
    assert value.state == "refused"
    assert not owner._kept


def test_appended_record_between_native_read_and_stamp_refuses(tmp_path, monkeypatch):

    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    original = module.resolve_tree_comparison

    def resolve(*args):
        native = original(*args)
        directory = comparison_directory(world.task_root, LEAF)
        path = next(directory.glob("*.json"))
        value = json.loads(path.read_text())
        value["number"] += 1
        (directory / f"{value['number']}.json").write_text(json.dumps(value))
        return native

    monkeypatch.setattr(module, "resolve_tree_comparison", resolve)
    value = owner.prepare(query)
    assert not isinstance(value, PreparedComparison)
    assert value.state == "refused"
    assert not owner._kept


def test_invalid_record_becoming_valid_during_read_refuses(tmp_path, monkeypatch):

    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    first = owner.prepare(query)
    assert isinstance(first, PreparedComparison)
    owner._kept.clear()
    directory = comparison_directory(world.task_root, LEAF)
    invalid = directory / "999.json"
    invalid.write_text("{invalid")
    original = module.resolve_tree_comparison

    def resolve(*args):
        native = original(*args)
        value = _comparison(first).model_dump(mode="json", by_alias=True)
        value["number"] = 999
        invalid.write_text(json.dumps(value))
        return native

    monkeypatch.setattr(module, "resolve_tree_comparison", resolve)
    value = owner.prepare(query)
    assert not isinstance(value, PreparedComparison)
    assert value.state == "refused"


def test_external_append_during_owned_publication_refuses(tmp_path, monkeypatch):

    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    publish = native_owner.publish_comparison_record

    def racing_publish(path, payload):
        written = publish(path, payload)
        value = json.loads(payload)
        value["number"] += 1
        (path.parent / f"{value['number']}.json").write_text(json.dumps(value))
        return written

    monkeypatch.setattr(native_owner, "publish_comparison_record", racing_publish)
    value = owner.prepare(query)
    assert not isinstance(value, PreparedComparison)
    assert value.state == "refused"
    assert not owner._kept


def test_recorded_comparison_without_capture_worktrees_stays_current(tmp_path):
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    live = owner.prepare(query)
    assert isinstance(live, PreparedComparison)
    world.contract(live=False)
    for recorded in (True, False):
        historical = owner.prepare(replace(query, recorded=recorded))
        assert isinstance(historical, PreparedComparison), historical
        assert not historical.native[1].live
        assert historical.witness.current()
        assert historical.result.comparison == live.result.comparison
        again = owner.prepare(replace(query, recorded=recorded))
        assert isinstance(again, PreparedComparison)
        assert again.result == historical.result


def test_thin_comparison_does_not_swallow_the_projected_worklist_error(tmp_path):
    world = build_world(tmp_path)
    world.edit()
    owner = SharedComparisonPreparation(
        world.config, RecordedReviewAnswers(world.config, RecordedAnswerReuse())
    )
    query = ReviewTreesQuery(repository_id=REPO, master=MASTER, leaf_id=LEAF)
    live = owner.prepare(query)
    assert isinstance(live, PreparedComparison)
    contract = live.native[0]
    world.contract(live=False)
    worklist = contract.contract_path.parent / "knowledge-worklist.json"
    worklist.mkdir()
    query = replace(query, recorded=True)
    prepared = owner.prepare(query)
    assert isinstance(prepared, PreparedComparison), prepared
    assert prepared.witness.current()
    assert all(str(worklist) not in str(source) for source in prepared.witness.sources)
    # The page still invokes the native worklist reader; its actual error is retained.
    projected = owner.render(query)
    assert projected.state == "refused"
    assert projected.refusal is not None
    assert "IsADirectoryError" in projected.refusal.detail
