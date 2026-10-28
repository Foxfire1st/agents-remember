import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from agents_remember.serving import review_answer_reuse as module
from agents_remember.serving.changeset_cache import ContentRead
from agents_remember.serving.review_answer_reuse import (
    ANSWER_WRITES,
    AnswerCodec,
    RecordedAnswerReuse,
)
from pydantic import BaseModel


class Answer(BaseModel):
    value: str


CODEC = AnswerCodec[Answer](Answer)


def owner(root, **kwargs):
    ANSWER_WRITES.drain()  # the earlier process's stored answers are written
    return RecordedAnswerReuse(
        directory=root / "cache", build_namespace="sha256:source-a", **kwargs
    )


def inputs(root):
    path = root / "input.json"
    if not path.exists():
        path.write_text('{"input": 1}')
    return (ContentRead(path),)


def test_fresh_owner_reuses_typed_answer_and_isolates_returned_mutation(tmp_path):
    calls = []

    def produce():
        calls.append(1)
        return Answer(value="first")

    first = owner(tmp_path).answer(("trees", 1), inputs(tmp_path), produce, codec=CODEC)
    first.value = "mutated"
    second = owner(tmp_path).answer(("trees", 1), inputs(tmp_path), produce, codec=CODEC)
    assert second == Answer(value="first")
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change", ["input", "question", "build", "broken-json", "wrong-schema", "digest"]
)
def test_changed_inputs_or_invalid_cache_are_never_served(tmp_path, change):
    sources = inputs(tmp_path)
    owner(tmp_path).answer(("trees", 1), sources, lambda: Answer(value="before"), codec=CODEC)
    restored = owner(tmp_path)
    key = ("trees", 1)
    if change == "input":
        sources[0].path.write_text('{"input": 2}')
    elif change == "question":
        key = ("trees", 2)
    elif change == "build":
        restored = RecordedAnswerReuse(
            directory=tmp_path / "cache", build_namespace="sha256:source-b"
        )
    else:
        path = next((tmp_path / "cache").glob("*.review.json"))
        if change == "broken-json":
            path.write_text("{")
        else:
            data = json.loads(path.read_bytes())
            if change == "wrong-schema":
                data["identity"]["format"] = "unknown"
            else:
                data["answer"]["value"] = "tampered"
            path.write_text(json.dumps(data))
    assert (
        restored.answer(key, sources, lambda: Answer(value="fresh"), codec=CODEC).value == "fresh"
    )


def test_failures_and_raced_input_are_not_persisted(tmp_path):
    sources = inputs(tmp_path)
    reuse = owner(tmp_path)
    reuse.answer(
        ("failed",),
        sources,
        lambda: Answer(value="failed"),
        codec=CODEC,
        keep=lambda _value: False,
    )

    def race():
        sources[0].path.write_text('{"input": 2}')
        return Answer(value="raced")

    reuse.answer(("race",), sources, race, codec=CODEC)
    assert list((tmp_path / "cache").glob("*.review.json")) == []


def test_a_persisted_answer_is_restored_at_any_age_while_its_inputs_observe_identical(tmp_path):
    sources = inputs(tmp_path)
    owner(tmp_path, wall_clock=lambda: 100.0).answer(
        ("trees",), sources, lambda: Answer(value="stored"), codec=CODEC
    )
    result = owner(tmp_path, wall_clock=lambda: 100.0 + 10**9).answer(
        ("trees",), sources, lambda: Answer(value="computed again"), codec=CODEC
    )
    assert result.value == "stored"


def test_disk_storage_enforces_count_and_byte_bounds(tmp_path):
    reuse = owner(tmp_path, capacity=1, max_disk_bytes=2048)
    sources = inputs(tmp_path)
    reuse.answer(("one",), sources, lambda: Answer(value="one"), codec=CODEC)
    reuse.answer(("two",), sources, lambda: Answer(value="two"), codec=CODEC)
    ANSWER_WRITES.drain()
    assert len(list((tmp_path / "cache").glob("*.review.json"))) == 1
    reuse.answer(("large",), sources, lambda: Answer(value="x" * 4000), codec=CODEC)
    ANSWER_WRITES.drain()
    assert sum(path.stat().st_size for path in (tmp_path / "cache").glob("*.review.json")) <= 2048


def test_configured_store_requires_build_namespace(tmp_path):
    with pytest.raises(ValueError, match="producing-build"):
        RecordedAnswerReuse(directory=tmp_path / "cache")


def test_oversized_answer_is_serialized_once_and_never_written(tmp_path, monkeypatch):
    writes = []
    encodes = []
    original = module.json.dumps

    def encoded(value, *args, **kwargs):
        encodes.append(value)
        return original(value, *args, **kwargs)

    monkeypatch.setattr(module.json, "dumps", encoded)
    monkeypatch.setattr(module, "atomic_write_bytes", lambda *args: writes.append(args))
    owner(tmp_path, max_disk_bytes=128).answer(
        ("large",), inputs(tmp_path), lambda: Answer(value="x" * 1000), codec=CODEC
    )
    ANSWER_WRITES.drain()
    assert writes == []
    assert sum(isinstance(value, dict) and "answer" in value for value in encodes) == 1
    assert not list((tmp_path / "cache").glob("*.review.json"))


def test_concurrent_owners_share_one_bounded_write_transaction(tmp_path):
    sources = inputs(tmp_path)

    def write(number):
        owner(tmp_path, capacity=3, max_disk_bytes=4096).answer(
            ("concurrent", number), sources, lambda: Answer(value=str(number) * 200), codec=CODEC
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(write, range(20)))
    ANSWER_WRITES.drain()  # the writes are the background writer's: read the store once they end
    paths = list((tmp_path / "cache").glob("*.review.json"))
    assert len(paths) <= 3
    assert sum(path.stat().st_size for path in paths) <= 4096
    assert all(
        json.loads(path.read_bytes())["identity"]["format"] == "recorded-review-answer/v1"
        for path in paths
    )


class MappingAnswer(BaseModel):
    values: dict[str, int]


def test_disk_round_trip_preserves_raw_mapping_order(tmp_path):
    codec = AnswerCodec[MappingAnswer](MappingAnswer)
    sources = inputs(tmp_path)
    original = MappingAnswer(values={"zebra": 1, "alpha": 2, "middle": 3})
    first = owner(tmp_path).answer(("ordered",), sources, lambda: original, codec=codec)

    def unexpected() -> MappingAnswer:
        raise AssertionError("the same input must load the persisted answer")

    second = owner(tmp_path).answer(("ordered",), sources, unexpected, codec=codec)
    assert second.model_dump_json() == first.model_dump_json()
    assert list(second.values) == ["zebra", "alpha", "middle"]


def test_revoked_pure_predicate_recomputes_with_unchanged_source_bytes(tmp_path):
    reuse = owner(tmp_path)
    sources = inputs(tmp_path)
    allowed = [True]
    calls = []

    def produce():
        calls.append(1)
        return Answer(value=str(len(calls)))

    def keep(_value):
        return allowed[0]

    assert reuse.answer(("guard",), sources, produce, keep=keep).value == "1"
    allowed[0] = False
    assert reuse.retained_answer(("guard",)) is None
    assert reuse.answer(("guard",), sources, produce, keep=keep).value == "2"
    assert len(calls) == 2
