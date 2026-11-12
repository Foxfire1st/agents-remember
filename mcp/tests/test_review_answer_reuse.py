"""A recorded comparison's answers are kept by their inputs, bounded, and unchanged (MIK-R179).

Focused units: the reuse rule itself, not the reviewer routes. The routes' own behaviour is
protected where it already was; these cases protect the four properties a kept answer must have --
an unchanged answer, an answer byte-identical to a fresh one, invalidation on an input change, and
the size and age bounds that stop a long-lived process holding everything it ever answered.
"""

from __future__ import annotations

import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agents_remember.serving.changeset_cache import ContentRead, DirectoryRead
from agents_remember.serving.review_answer_reuse import RecordedAnswerReuse


@contextmanager
def _no_readable_input(reuse: RecordedAnswerReuse) -> Iterator[None]:
    """Make every observation unreadable: a doubt, so nothing is kept and nothing is served."""

    original = reuse.cache.snapshot
    reuse.cache.snapshot = lambda _source: None  # type: ignore[method-assign]
    try:
        yield
    finally:
        reuse.cache.snapshot = original  # type: ignore[method-assign]


def _sources(root: Path) -> tuple[ContentRead | DirectoryRead, ...]:
    record = root / "record.json"
    record.write_text('{"number": 1}\n', encoding="utf-8")
    return (ContentRead(record), DirectoryRead(root))


def test_a_second_read_of_the_same_inputs_is_served_from_the_kept_answer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = _sources(root)
        reuse = RecordedAnswerReuse()
        computed: list[dict[str, object]] = []

        def produce() -> dict[str, object]:
            computed.append({"answer": len(computed)})
            return {"answer": "task", "nested": {"items": [1, 2, 3]}}

        first = reuse.answer(("intent", "task"), sources, produce)
        second = reuse.answer(("intent", "task"), sources, produce)

        assert first == second
        assert len(computed) == 1
        assert reuse.retained() == 1


def test_a_kept_answer_is_byte_identical_to_a_fresh_computation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = _sources(root)

        def produce() -> dict[str, object]:
            return {"state": "review", "sides": [{"name": "before"}, {"name": "after"}]}

        kept = RecordedAnswerReuse().answer(("intent", "task"), sources, produce)
        fresh = produce()
        assert json.dumps(kept, sort_keys=True) == json.dumps(fresh, sort_keys=True)


def test_a_changed_input_invalidates_the_kept_answer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = _sources(root)
        reuse = RecordedAnswerReuse()
        calls: list[int] = []

        def produce() -> dict[str, int]:
            calls.append(1)
            return {"call": len(calls)}

        first = reuse.answer(("intent", "task"), sources, produce)
        # A successor record in the comparison directory is a different input set.
        (root / "record-2.json").write_text('{"number": 2}\n', encoding="utf-8")
        second = reuse.answer(("intent", "task"), sources, produce)

        assert first != second
        assert len(calls) == 2


def test_an_unreadable_input_is_never_kept_or_served() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = _sources(root)
        reuse = RecordedAnswerReuse()
        calls: list[int] = []

        def produce() -> dict[str, int]:
            calls.append(1)
            return {"call": len(calls)}

        with _no_readable_input(reuse):
            reuse.answer(("intent", "task"), sources, produce)
            reuse.answer(("intent", "task"), sources, produce)

        assert len(calls) == 2
        assert reuse.retained() == 0


def test_the_size_bound_evicts_the_least_recently_used_answer() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        sources = _sources(root)
        reuse = RecordedAnswerReuse(capacity=1)
        calls: list[str] = []

        def produce(name: str) -> dict[str, str]:
            calls.append(name)
            return {"answer": name}

        reuse.answer(("intent", "a"), sources, lambda: produce("a"))
        reuse.answer(("intent", "b"), sources, lambda: produce("b"))
        reuse.answer(("intent", "a"), sources, lambda: produce("a"))

        assert calls == ["a", "b", "a"]
        assert reuse.retained() == 1
