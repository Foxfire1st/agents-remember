"""Only durable settled environment transitions wake the existing page watcher."""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

from agents_remember.canvas.explainer_paths import LOCK_RESOURCE, leaf_lock_resource
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    mint_seat_attempt,
    raise_generation,
    record_request,
    seat_for_workspace,
    write_binding,
    write_inflight_create,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.serving.review_publication_watch import (
    PublicationInputs,
    _settled,
    publication_events,
)
from canvas_host_test_support import record

IDENTITY = ("repo", "master", "leaf")


def settled(place: PublicationInputs, stop: threading.Event) -> tuple[object, ...]:
    """The settled transition, for a case that asserts one exists."""

    transition = _settled(place, stop)
    assert transition is not None
    return transition


def inputs(root: Path) -> PublicationInputs:
    root.mkdir(parents=True, exist_ok=True)
    return PublicationInputs(
        root,
        root / "task/ready.json",
        root / "env/leaf",
        root / "task/seat-generation",
        root / "env/leaf/state/requests.json",
    )


def binding(number: int = 7) -> ExplainerBinding:
    return ExplainerBinding(
        binding=FrameBinding.from_record(
            record(number),
            comparison_owner="recorded",
            comparison_reference=f"review-comparison:task/leaf/{number}",
        ),
        snapshot="trees",
        prepared_at="2026-10-25T12:00:00Z",
        packet_digest="sha256:packet",
    )


def begin(place: PublicationInputs) -> str:
    place.workspace.mkdir(parents=True)
    raise_generation(place.generation_file)
    mint_seat_attempt(place.workspace, "attempt")
    write_binding(place.workspace, binding())
    return seat_for_workspace(place.workspace, 1)


def complete(
    place: PublicationInputs, seat: str, delivery: str = "delivered", request: str = "first"
) -> None:
    record_request(
        place.ledger,
        "leaf",
        request,
        {"generation": 1, "agentId": seat, "delivery": delivery, "at": "2026-10-25T12:00:00Z"},
    )


def test_binding_pending_and_mismatched_receipts_are_not_completion(tmp_path):
    place = inputs(tmp_path)
    seat = begin(place)
    assert _settled(place, threading.Event()) is None
    complete(place, "wrong-seat")
    assert _settled(place, threading.Event()) is None
    complete(place, seat, "pending")
    assert _settled(place, threading.Event()) is None
    complete(place, seat)
    write_inflight_create(place.ledger, {"agentId": seat})
    assert _settled(place, threading.Event()) is None
    write_inflight_create(place.ledger, None)
    assert settled(place, threading.Event())[0] == "environment"


def test_changed_binding_after_terminal_receipt_is_update_and_removal_is_absent(tmp_path):
    place = inputs(tmp_path)
    seat = begin(place)
    complete(place, seat)
    before = _settled(place, threading.Event())
    write_binding(place.workspace, binding(8))
    assert _settled(place, threading.Event()) != before
    # MIK-R211 v5 rule 12: a removal deletes the folder, and a removed leaf reads as never built.
    shutil.rmtree(place.workspace)
    assert settled(place, threading.Event()) == ("absent",)
    assert not place.workspace.exists()


def test_failed_delivery_is_settled_invalidation_not_page_publication(tmp_path):
    place = inputs(tmp_path)
    seat = begin(place)
    complete(place, seat, "failed")
    assert settled(place, threading.Event())[0] == "environment"
    assert not place.pointer.exists()


def test_native_lock_wait_is_cancelled_without_reading_partial_state(tmp_path):
    place = inputs(tmp_path)
    seat = begin(place)
    complete(place, seat)
    stop = threading.Event()
    result = []
    with exclusive_file_lock(leaf_lock_resource(place.workspace), LOCK_RESOURCE):
        worker = threading.Thread(target=lambda: result.append(_settled(place, stop)))
        worker.start()
        stop.set()
        worker.join(
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            2
        )
        assert not worker.is_alive()
    assert result == [None]


def test_other_process_terminal_receipt_wakes_page_without_ready_pointer(tmp_path):
    async def scenario():
        place = inputs(tmp_path)
        stream = publication_events(IDENTITY, lambda _: place)
        async with contextlib.aclosing(stream):
            assert (
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                (await asyncio.wait_for(anext(stream), 10)).event == "review.explainer.changed"
            )
            seat = begin(place)
            pending = asyncio.create_task(anext(stream))
            # Binding/generation are present, but no terminal receipt exists.
            # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            assert not (await asyncio.wait({pending}, timeout=0.3))[0]
            script = """import sys
from pathlib import Path
from agents_remember.canvas.explainer_paths import LOCK_RESOURCE, leaf_lock_resource
from agents_remember.canvas.explainer_state import record_request
from agents_remember.kernel.file_lock import exclusive_file_lock
workspace, ledger, seat = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
with exclusive_file_lock(leaf_lock_resource(workspace), LOCK_RESOURCE):
 record_request(ledger, 'leaf', 'first', {'generation':1,'agentId':seat,'delivery':'held','at':'2026-10-25T12:00:00Z'})
"""
            # One separate writer process, not a mocked event or an extra watcher.
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script, str(place.workspace), str(place.ledger), seat],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=15,  # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            )
            assert result.returncode == 0, result.stderr
            event = await asyncio.wait_for(
                pending,
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                10,
            )
            assert event.event == "review.explainer.changed"
            assert event.data["kind"] == "environment"
            assert event.data["pageStamp"] is None
            assert not place.pointer.exists()
            assert settled(place, threading.Event())[0] == "environment"
        assert not any(
            item.name.startswith("review-publication-") for item in threading.enumerate()
        )

    asyncio.run(scenario())


def terminal_failure(place: PublicationInputs, number: int, *, at="2026-10-25T12:01:00Z") -> None:
    """The writer's durable row, including its exact pointer rather than sorted-row guessing."""
    record_request(
        place.ledger,
        "leaf",
        "failed-before-create",
        {
            "state": "failed",
            "outcome": "failed",
            "beforeCreate": True,
            "generation": number,
            "at": at,
            "failedStep": "comparison",
            "step": "comparison",
            "error": "comparison cannot be read",
            "reason": "evidence-source-unavailable",
            "percent": 0,
        },
    )


def test_failed_attempt_after_rollback_is_observed_without_a_current_seat(tmp_path):
    place = inputs(tmp_path)
    before = _settled(place, threading.Event())
    terminal_failure(place, 0)
    after = settled(place, threading.Event())
    assert before == ("absent",)
    assert after[0] == "failed"
    assert not place.generation_file.exists()
    # The ledger lives in the leaf's own state/ (MIK-R211 v5 rule 12), and nothing else is there.
    assert [path.name for path in place.workspace.iterdir()] == ["state"]
    write_inflight_create(place.ledger, {"requestId": "still-running"})
    assert _settled(place, threading.Event()) is None


def test_a_failed_start_after_a_removal_is_observed(tmp_path):
    place = inputs(tmp_path)
    place.workspace.mkdir(parents=True)
    raise_generation(place.generation_file)
    # MIK-R211 v5 rule 12: a removal leaves no mark, so the removed leaf reads as never built.
    shutil.rmtree(place.workspace)
    assert _settled(place, threading.Event()) == ("absent",)
    terminal_failure(place, 1)
    after = settled(place, threading.Event())
    assert after[0] == "failed" and after[2] is None


def test_terminal_failure_wakes_open_page_without_binding_or_seat(tmp_path):
    async def scenario():
        place = inputs(tmp_path)
        stream = publication_events(IDENTITY, lambda _: place)
        async with contextlib.aclosing(stream):
            baseline = await asyncio.wait_for(
                anext(stream),
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                10,
            )
            assert baseline.data["kind"] == "baseline"
            pending = asyncio.create_task(anext(stream))
            script = """import sys
from pathlib import Path
from agents_remember.canvas.explainer_paths import LOCK_RESOURCE, leaf_lock_resource
from agents_remember.canvas.explainer_state import record_request
from agents_remember.kernel.file_lock import exclusive_file_lock
workspace, ledger = Path(sys.argv[1]), Path(sys.argv[2])
with exclusive_file_lock(leaf_lock_resource(workspace), LOCK_RESOURCE):
 record_request(ledger, 'leaf', 'failed-before-create', {
  'state':'failed','outcome':'failed','beforeCreate':True,'generation':0,
  'at':'2026-10-25T12:01:00Z','step':'comparison','failedStep':'comparison',
  'reason':'evidence-source-unavailable','error':'comparison cannot be read','percent':0})
"""
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-c", script, str(place.workspace), str(place.ledger)],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                timeout=15,  # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
            )
            assert result.returncode == 0, result.stderr
            event = await asyncio.wait_for(
                pending,
                # load-independent: a hang detector around an in-memory fake; the assertion reads state, not elapsed time
                10,
            )
            assert event.data["kind"] == "environment"
            assert event.data["pageStamp"] is None
            assert [path.name for path in place.workspace.iterdir()] == ["state"]
            assert not place.pointer.exists()

    asyncio.run(scenario())
