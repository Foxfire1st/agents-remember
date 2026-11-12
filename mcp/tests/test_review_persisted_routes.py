import json
from types import SimpleNamespace

import pytest
from agents_remember.application import review_answer_preparation as reads
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig
from agents_remember.kernel.recorded_reads import observed_json_files, observed_resolve, record_read
from agents_remember.models.knowledge.review_trees import ReviewTreesResult
from agents_remember.serving.changeset_cache import ContentRead
from agents_remember.serving.review_answer_reuse import ANSWER_WRITES, RecordedAnswerReuse
from agents_remember.serving.review_trees import ReviewTreesQuery


def fixture(tmp_path, monkeypatch):
    plain = tmp_path / "contract.md"
    plain.write_text("contract")
    index = tmp_path / "index.sqlite"
    index.write_bytes(b"index projection rows")
    contract = SimpleNamespace(code_worktree=None, memory_worktree=None)
    monkeypatch.setattr(reads, "_recorded_contract", lambda *_: contract)
    monkeypatch.setattr(reads, "_recorded_sides", lambda *_: ())
    monkeypatch.setattr(reads, "_trees_still_readable", lambda *_: True)
    monkeypatch.setattr(reads, "_sources_of", lambda *_: (ContentRead(plain),))
    monkeypatch.setattr(
        reads,
        "_persistent_inputs",
        lambda *_: ((ContentRead(index),), ("logical-before", "logical-after")),
    )
    latest = [3]
    monkeypatch.setattr(reads, "recorded_latest", lambda *_: latest[0])
    monkeypatch.setattr(reads, "delivered_notice", lambda *_: None)
    calls = []

    def produce():
        record_read(plain)
        calls.append(1)
        return ReviewTreesResult(
            state="trees",
            repository_id="repo",
            master="master",
            leaf_id="leaf",
            latest=2,
            currentness={"after": {"value": 1}},
        )

    def owner():
        ANSWER_WRITES.drain()  # the earlier process's stored answers are written
        return reads.RecordedReviewAnswers(
            McpRuntimeConfig(
                workspace_root=tmp_path,
                coordination_root=tmp_path,
                config_path=tmp_path / "config.json",
                transcript_root=tmp_path / "transcripts",
            ),
            RecordedAnswerReuse(directory=tmp_path / "cache", build_namespace="source-digest"),
        )

    return owner, produce, calls, latest, index


def query(number=3):
    return ReviewTreesQuery(repository_id="repo", master="master", leaf_id="leaf", number=number)


def test_static_main_survives_fresh_owner_with_fresh_latest_decoration(tmp_path, monkeypatch):
    owner, produce, calls, latest, _index = fixture(tmp_path, monkeypatch)
    first = owner().trees(query(), produce)
    latest[0] = 4
    second = owner().trees(query(), produce)
    assert len(calls) == 1
    assert first.latest == 3 and second.latest == 4
    assert second.currentness == first.currentness


def test_index_bytes_and_another_question_cannot_hit_persisted_main(tmp_path, monkeypatch):
    owner, produce, calls, _latest, index = fixture(tmp_path, monkeypatch)
    owner().trees(query(), produce)
    index.write_bytes(b"changed ix_* rows with same logical identity")
    owner().trees(query(), produce)
    owner().trees(query(4), produce)
    assert len(calls) == 3


def test_failed_current_closure_check_never_returns_a_persisted_answer(tmp_path, monkeypatch):
    owner, produce, calls, _latest, _index = fixture(tmp_path, monkeypatch)
    owner().trees(query(), produce)
    monkeypatch.setattr(reads, "_trees_still_readable", lambda *_: False)
    owner().trees(query(), produce)
    assert len(calls) == 2


def test_an_unlisted_dependency_is_stored_with_the_answer_and_compared_before_reuse(
    tmp_path, monkeypatch
):
    owner, produce, calls, _latest, _index = fixture(tmp_path, monkeypatch)
    extra = tmp_path / "unexpected.json"
    extra.write_text("unlisted")

    def with_extra():
        record_read(extra)
        return produce()

    owner().trees(query(), with_extra)
    owner().trees(query(), with_extra)
    assert len(calls) == 1
    (stored,) = list((tmp_path / "cache").glob("*.review.json"))
    assert str(extra) in json.loads(stored.read_text())["reads"]
    extra.write_text("the unlisted input changed")
    owner().trees(query(), with_extra)
    assert len(calls) == 2


def test_transient_changed_read_is_not_persisted_when_source_returns_to_original(
    tmp_path, monkeypatch
):
    owner, _produce, _calls, _latest, _index = fixture(tmp_path, monkeypatch)
    source = tmp_path / "contract.md"
    original = source.read_bytes()

    def raced():
        source.write_bytes(b"different actual bytes")
        record_read(source)
        source.write_bytes(original)
        return ReviewTreesResult(
            state="trees", repository_id="repo", master="master", leaf_id="leaf"
        )

    owner().trees(query(), raced)
    assert not list((tmp_path / "cache").glob("*.review.json"))


def test_unnumbered_recorded_question_has_no_persisted_alias(tmp_path, monkeypatch):
    owner, produce, calls, _latest, _index = fixture(tmp_path, monkeypatch)
    unnumbered = ReviewTreesQuery(
        repository_id="repo", master="master", leaf_id="leaf", recorded=True
    )
    owner().trees(unnumbered, produce)
    owner().trees(unnumbered, produce)
    assert len(calls) == 2
    assert not list((tmp_path / "cache").glob("*.review.json"))


def test_trusted_resolver_selection_is_reobserved_before_disk_hit(tmp_path, monkeypatch):
    owner, produce, calls, _latest, _index = fixture(tmp_path, monkeypatch)
    original = reads._recorded_contract
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    logical = tmp_path / "selected"
    logical.symlink_to(first, target_is_directory=True)

    def resolve(*args):
        observed_resolve(logical)
        return original(*args)

    monkeypatch.setattr(reads, "_recorded_contract", resolve)
    owner().trees(query(), produce)
    logical.unlink()
    logical.symlink_to(second, target_is_directory=True)
    owner().trees(query(), produce)
    assert len(calls) == 2


def test_a_producer_selection_observation_is_only_compared_before_reuse(tmp_path, monkeypatch):
    owner, produce, calls, _latest, _index = fixture(tmp_path, monkeypatch)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    def with_selection():
        observed_resolve(tmp_path / "unlisted")
        return produce()

    first = owner().trees(query(), with_selection)
    assert owner().trees(query(), with_selection) == first
    assert len(calls) == 1
    # The stored resolution is re-observed and compared; a locator that moved is another answer.
    (tmp_path / "unlisted").symlink_to(elsewhere)
    owner().trees(query(), with_selection)
    assert len(calls) == 2


@pytest.mark.parametrize("observation", ["metadata", "locator", "listing"])
def test_warm_lookup_rechecks_cold_resolver_observations_without_reopening_indexes(
    tmp_path, monkeypatch, observation
):
    owner, produce, calls, _latest, index = fixture(tmp_path, monkeypatch)
    client = owner()
    native_inputs = reads._persistent_inputs
    metadata = tmp_path / "metadata.json"
    metadata.write_text("before")
    directory = tmp_path / "choices"
    directory.mkdir()
    first = tmp_path / "index-before"
    second = tmp_path / "index-after"
    first.write_text("same database stays present")
    second.write_text("another database")
    locator = tmp_path / "bound-index"
    locator.symlink_to(first)
    preparations = []

    def observed_inputs(*args):
        preparations.append(1)
        if observation == "metadata":
            record_read(metadata)
        elif observation == "locator":
            observed_resolve(locator)
        else:
            observed_json_files(directory)
        return native_inputs(*args)

    monkeypatch.setattr(reads, "_persistent_inputs", observed_inputs)
    client.trees(query(), produce)
    client.trees(query(), produce)
    assert len(calls) == 1
    assert len(preparations) == 1
    if observation == "metadata":
        metadata.write_text("after")
    elif observation == "locator":
        locator.unlink()
        locator.symlink_to(second)
    else:
        (directory / "new.json").write_text("{}")
    assert first.is_file() and index.is_file()
    client.trees(query(), produce)
    assert len(calls) == 2
    assert len(preparations) == 2


@pytest.mark.parametrize("stage", ["produce", "restore"])
def test_closure_is_checked_again_after_answer_materializes(tmp_path, monkeypatch, stage):
    owner, produce, calls, _latest, _index = fixture(tmp_path, monkeypatch)
    closure = [True]
    monkeypatch.setattr(reads, "_trees_still_readable", lambda *_: closure[0])
    if stage == "restore":
        owner().trees(query(), produce)
        original = reads.AnswerCodec.decode

        def decode(self, value):
            result = original(self, value)
            closure[0] = False
            return result

        monkeypatch.setattr(reads.AnswerCodec, "decode", decode)

    def materialize():
        result = produce()
        closure[0] = False
        return result

    client = owner()
    client.trees(query(), materialize)
    assert client._reuse.retained() == 0
    assert len(calls) == (2 if stage == "restore" else 1)
    if stage == "produce":
        assert not list((tmp_path / "cache").glob("*.review.json"))
