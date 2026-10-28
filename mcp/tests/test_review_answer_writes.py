"""A kept answer is stored after its response, by one detached writer no response waits for.

Storing moves off the response path; it does not get faster. The writer is detached from the
request (a client that leaves never cancels a write), its queue is bounded and counts what it
drops, and a write that fails leaves the answer absent and is logged with its reason.
"""

import logging
import threading

from agents_remember.kernel.read_cancellation import cancellable_read, read_cancellation
from agents_remember.serving import review_answer_reuse as module
from agents_remember.serving.changeset_cache import ContentRead
from agents_remember.serving.review_answer_reuse import AnswerCodec, RecordedAnswerReuse
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, wait_until
from pydantic import BaseModel


class Answer(BaseModel):
    value: str


CODEC = AnswerCodec[Answer](Answer)


def owner(root, **kwargs):
    return RecordedAnswerReuse(
        directory=root / "cache", build_namespace="sha256:source-a", **kwargs
    )


def inputs(root):
    path = root / "input.json"
    if not path.exists():
        path.write_text('{"input": 1}')
    return (ContentRead(path),)


def stored(root):
    return list((root / "cache").glob("*.review.json"))


def held_writes(monkeypatch):
    """Every file write waits on the returned gate; the list names each one that waited."""
    gate = threading.Event()
    waited = []
    write = module.atomic_write_bytes

    def held(path, payload, **kwargs):
        waited.append(read_cancellation())
        assert gate.wait(HANG_GUARD_SECONDS)
        write(path, payload, **kwargs)

    monkeypatch.setattr(module, "atomic_write_bytes", held)
    return gate, waited


def test_a_response_is_answered_while_its_answer_is_still_being_stored(tmp_path, monkeypatch):
    gate, _waited = held_writes(monkeypatch)
    answered = threading.Event()
    reuse = owner(tmp_path, writes=module.AnswerWrites())

    def ask():
        reuse.answer(("trees", 1), inputs(tmp_path), lambda: Answer(value="one"), codec=CODEC)
        answered.set()

    asker = threading.Thread(target=ask)
    asker.start()
    try:
        assert answered.wait(HANG_GUARD_SECONDS), "the response waited for its answer's write"
        assert stored(tmp_path) == []
    finally:
        gate.set()
        asker.join(HANG_GUARD_SECONDS)
    reuse.writes.drain()
    assert len(stored(tmp_path)) == 1


def test_a_client_that_leaves_never_cancels_the_write_of_its_kept_answer(tmp_path, monkeypatch):
    gate, waited = held_writes(monkeypatch)
    writes = module.AnswerWrites()
    reuse = owner(tmp_path, writes=writes)
    left = threading.Event()
    with cancellable_read(left):
        reuse.answer(("trees", 1), inputs(tmp_path), lambda: Answer(value="one"), codec=CODEC)
    left.set()  # the client disconnects after its response
    gate.set()
    writes.drain()
    assert waited == [None], "the write ran in the request's context"
    assert len(stored(tmp_path)) == 1


def test_a_failed_write_leaves_the_answer_absent_and_is_logged_with_its_reason(
    tmp_path, monkeypatch, caplog
):
    def full(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(module, "atomic_write_bytes", full)
    reuse = owner(tmp_path, writes=module.AnswerWrites())
    with caplog.at_level(logging.WARNING, logger=module.__name__):
        value = reuse.answer(
            ("trees", 1), inputs(tmp_path), lambda: Answer(value="one"), codec=CODEC
        )
        reuse.writes.drain()
    assert value == Answer(value="one")
    assert stored(tmp_path) == []
    assert "No space left on device" in caplog.text
    calls = []
    owner(tmp_path).answer(
        ("trees", 1), inputs(tmp_path), lambda: calls.append(1) or Answer(value="one"), codec=CODEC
    )
    assert calls == [1], "an absent answer is computed again"


def test_a_full_write_queue_drops_and_counts_and_never_holds_a_response(
    tmp_path, monkeypatch, caplog
):
    gate, waited = held_writes(monkeypatch)
    writes = module.AnswerWrites(capacity=1)
    reuse = owner(tmp_path, writes=writes)
    try:
        with caplog.at_level(logging.WARNING, logger=module.__name__):
            for number in range(3):
                reuse.answer(
                    ("trees", number),
                    inputs(tmp_path),
                    lambda number=number: Answer(value=str(number)),
                    codec=CODEC,
                )
                if number == 0:  # the first write is being written; the next one waits
                    wait_until(lambda: waited, "the first write")
    finally:
        gate.set()
    writes.drain()
    assert writes.dropped == 1
    assert "1 dropped" in caplog.text
    assert len(stored(tmp_path)) == 2
