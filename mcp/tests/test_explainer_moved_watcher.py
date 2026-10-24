"""The moved watcher's native lifetime in ``serving/change_watcher.py`` (MIK-R186 rule 7).

The pure measurements live in :mod:`test_explainer_watcher`; these cases protect what the watch
around them promises:

1. the native turn-end loop :meth:`MoveNoticeWatch.run` actually consumes the host's turn ends --
   a leaf seat's (the Worker's) ended turn publishes the moved notice on the in-process channel
   without a page reopen, without waking or messaging the explainer, and without a comparison pin
   or record;
2. the same composition maps an actual native Worker id to its leaf and lets an unrelated seat's
   turn end pass it by;
3. an old moved notice stops being authoritative: the Update/Retire clear reaches an existing
   subscriber, a reconnect under a new binding (or a new generation) cannot replay the previous
   notice, and a clean re-check clears the channel.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from agents_remember.application.review_evidence_packet import comparison_reference
from agents_remember.application.review_planned_comparison import explainer_application_ports
from agents_remember.application.review_tree_comparison import comparison_directory
from agents_remember.canvas.explainer_paths import GENERATION_SUFFIX, leaf_workspace
from agents_remember.canvas.explainer_state import (
    ExplainerBinding,
    write_binding,
)
from agents_remember.cli import role_held_messages
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig, RepositoryScope
from agents_remember.models.knowledge.review_frame import FrameBinding
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.serving import change_watcher
from agents_remember.serving.change_watcher import MoveNoticeWatch, WatchOptions
from agents_remember.serving.explainer_watcher import (
    CapturedComparison,
    ComparisonMeasurement,
    LeafSelection,
    MovedNotice,
    MoveNoticeChannel,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS, async_wait_until

REPO = "agents-remember"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
WORKER = "worker-seat-183"
NOW = "2026-10-22T08:00:00+00:00"

WATCHER_PORTS = explainer_application_ports().watcher


def _config(tmp_path: Path) -> McpRuntimeConfig:
    return McpRuntimeConfig(
        config_path=tmp_path / "settings.json",
        coordination_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        transcript_root=tmp_path / "transcripts",
        repositories={
            REPO: RepositoryScope(
                repo_id=REPO,
                path=tmp_path / "repo",
                memory_root=tmp_path / "memory",
            )
        },
    )


def _task_root(tmp_path: Path) -> Path:
    root = tmp_path / "tasks" / REPO / TASK
    root.mkdir(parents=True, exist_ok=True)
    return root


def _side(tree: str, repository: str = "/repo/code") -> ReviewTreeSide:
    return ReviewTreeSide(repository=repository, tree=tree)


def _record(
    number: int,
    code_candidate: str = "b" * 40,
    memory_candidate: str = "d" * 40,
    code_base: str = "a" * 40,
    memory_base: str = "c" * 40,
) -> ReviewTreeComparisonRecord:
    return ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=_side(code_base),
        code_candidate=_side(code_candidate),
        memory_base=_side(memory_base, "/repo/memory"),
        memory_candidate=_side(memory_candidate, "/repo/memory"),
        recorded_at=NOW,
    )


def _binding(record: ReviewTreeComparisonRecord) -> ExplainerBinding:
    return ExplainerBinding(
        binding=FrameBinding.from_record(
            record,
            comparison_owner="recorded",
            comparison_reference=comparison_reference(record),
        ),
        snapshot=f"snapshot-{record.number}",
        prepared_at=NOW,
        packet_digest="sha256:" + "0" * 64,
    )


def _environment(config: McpRuntimeConfig, binding: ExplainerBinding) -> Path:
    workspace = leaf_workspace(config, REPO, TASK, LEAF)
    workspace.mkdir(parents=True, exist_ok=True)
    write_binding(workspace, binding)
    return workspace


def _generation(config: McpRuntimeConfig, number: int = 1) -> None:
    counter = config.coordination_root / "explainer" / REPO / TASK / f"{LEAF}{GENERATION_SUFFIX}"
    counter.parent.mkdir(parents=True, exist_ok=True)
    counter.write_text(f"{number}\n", encoding="utf-8")


def _worker_receipt(task_root: Path, agent: str = WORKER) -> None:
    """The leaf's Worker execution receipt, as a real launch files one (role + taskDocumentRef)."""

    receipt = task_root / "notes" / "reports" / "paseo-native-executions" / f"{LEAF}.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(
        json.dumps(
            {
                "agentId": agent,
                "role": "worker",
                "selection": {
                    "role": "worker",
                    "taskDocumentRef": {"repository": REPO, "path": f"{TASK}/{LEAF}.md"},
                },
            }
        ),
        encoding="utf-8",
    )


class FakeOwner:
    """A comparison owner whose capture is a prepared record; it counts its calls."""

    def __init__(self, captured: ReviewTreeComparisonRecord) -> None:
        self.record = captured
        self.calls = 0

    def capture(self, _selection: LeafSelection) -> CapturedComparison:
        self.calls += 1
        return CapturedComparison(self.record)


class FakeCounter:
    def __init__(self, blocks: int | None) -> None:
        self.blocks = blocks
        self.calls = 0

    def __call__(self, _binding: FrameBinding, _captured: ReviewTreeComparisonRecord) -> int | None:
        self.calls += 1
        return self.blocks


def _watch(
    config: McpRuntimeConfig,
    owner: FakeOwner,
    *,
    channel: MoveNoticeChannel | None = None,
    cooldown: float = 0,
) -> MoveNoticeWatch:
    return MoveNoticeWatch(
        config,
        WATCHER_PORTS,
        options=WatchOptions(
            measurement=ComparisonMeasurement(owner=owner, counter=FakeCounter(3)),
            channel=channel or MoveNoticeChannel(),
            cooldown=cooldown,
        ),
    )


def _selection() -> LeafSelection:
    return LeafSelection(REPO, TASK, LEAF)


# --- the native run loop (G12) and the Worker's turn end (G13) -----------------------------------


def test_the_native_turn_end_loop_runs_the_workers_turn_end_without_writes_or_messages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The lifetime's loop consumes a real Worker's turn end, publishes the notice, and stays
    alive; no page opened, no message sent, no pin and no comparison record written.
    """

    config = _config(tmp_path)
    task_root = _task_root(tmp_path)
    _environment(config, _binding(_record(7)))
    _generation(config)
    _worker_receipt(task_root)
    owner = FakeOwner(_record(1, code_candidate="e" * 40))
    watch = _watch(config, owner)

    def _refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the moved watcher sent or held a message")

    monkeypatch.setattr(role_held_messages, "hold_message", _refuse)
    monkeypatch.setattr(role_held_messages, "deliver_ended_turns", _refuse)
    released = threading.Event()

    def _turns(_config: McpRuntimeConfig, _seen: Any) -> list[str]:
        if not released.is_set():
            released.set()
            return [WORKER]
        return []

    monkeypatch.setattr(change_watcher, "turn_ended_agents", _turns)

    asyncio.run(_drive_run_loop(watch, released))

    # The Worker's turn end alone published the notice: no page opened, no seat messaged.
    notice = watch.channel.latest(_selection())
    assert notice is not None
    assert notice.kind == "comparison-moved" and notice.update_offered is True
    assert owner.calls == 1
    assert not comparison_directory(task_root, LEAF).exists()


async def _drive_run_loop(watch: MoveNoticeWatch, released: threading.Event) -> None:
    """Run the watcher's loop until its first tick consumed a turn end and published the notice."""

    task = asyncio.create_task(watch.run(tick=0.01))
    try:
        await async_wait_until(
            lambda: released.is_set() and watch.channel.latest(_selection()) is not None,
            "the run loop's first turn end",
        )
        assert released.is_set(), "the run loop never read a turn end"
        assert watch.channel.latest(_selection()) is not None
        assert not task.done(), "the native loop stopped after one tick"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def test_a_worker_turn_end_captures_through_the_watch_and_an_unrelated_seat_is_ignored(
    tmp_path: Path,
) -> None:
    """An actual native Worker id resolves to its leaf's environment and triggers the capture
    and the notice; a seat of no watched leaf does not.
    """

    config = _config(tmp_path)
    task_root = _task_root(tmp_path)
    _environment(config, _binding(_record(7)))
    _generation(config)
    _worker_receipt(task_root)
    owner = FakeOwner(_record(1, code_candidate="e" * 40))
    watch = _watch(config, owner)

    assert watch.seats().get(WORKER) == _selection()
    assert watch.after_turn_ends(["someone-elses-seat"]) == []
    assert owner.calls == 0

    published = watch.after_turn_ends([WORKER])
    assert owner.calls == 1
    assert len(published) == 1
    assert watch.channel.latest(_selection()) == published[0]


# --- the old notice stops being authoritative (G15) ----------------------------------------------


def test_update_retire_and_a_new_generation_clear_the_old_moved_notice(tmp_path: Path) -> None:
    """After the moved notice, the Update/Retire clear reaches an existing subscriber, a
    reconnect within the cooldown under the new binding cannot replay the old notice, and a new
    generation's key never matches the previous notice's.
    """

    config = _config(tmp_path)
    task_root = _task_root(tmp_path)
    _environment(config, _binding(_record(7)))
    _generation(config)
    _worker_receipt(task_root)
    owner = FakeOwner(_record(1, code_candidate="e" * 40))
    channel = MoveNoticeChannel()
    watch = _watch(config, owner, channel=channel, cooldown=60.0)
    selection = _selection()

    published = watch.after_turn_ends([WORKER])
    assert published and channel.latest(selection) == published[0]

    # An existing subscriber holds the moved notice and receives the cleared one on the
    # Update/Retire clear; the channel forgets the notice entirely.
    seen = _subscriber_notices(channel, selection, clear=lambda: watch.clear(selection))
    assert seen and seen[0] == published[0]
    assert seen[-1].cleared is True
    assert channel.latest(selection) is None

    # Moved again while the page is open: the notice is stored under the binding's own key.
    again = watch.after_turn_ends([WORKER])
    assert again and channel.latest(selection) == again[0]
    assert watch.page_opened(selection) == again[0]  # the open page read the same comparison

    # Update binds #8; a reconnect within the cooldown finds the old notice stored under the
    # previous binding's key, drops it, and replays nothing.
    _environment(config, _binding(_record(8)))
    owner.record = _record(8)  # the Update bound exactly what the owner now captures: clean
    assert watch.page_opened(selection) is None
    assert channel.latest(selection) is None

    # Retire and rebuild with a new generation, same binding: the previous notice's key is gone,
    # so a reconnect cannot replay it either.
    owner.record = _record(1, code_candidate="e" * 40)
    once_more = watch.after_turn_ends([WORKER])
    assert once_more and channel.latest(selection) == once_more[0]
    _generation(config, number=2)
    _environment(config, _binding(_record(8)))
    owner.record = _record(8)
    assert watch.page_opened(selection) is None
    assert channel.latest(selection) is None


def _subscriber_notices(
    channel: MoveNoticeChannel,
    selection: LeafSelection,
    *,
    clear: Any,
) -> list[MovedNotice]:
    """One subscriber's view: the replayed latest, then the cleared notice the clear broadcasts."""

    seen: list[MovedNotice] = []
    loop = asyncio.new_event_loop()

    async def _drain() -> None:
        async with contextlib.aclosing(channel.listen(selection)) as stream:
            async for notice in stream:
                seen.append(notice)
                if notice.cleared:
                    return

    task = loop.create_task(_drain())
    try:
        deadline = time.monotonic() + HANG_GUARD_SECONDS
        while not seen and time.monotonic() < deadline:
            loop.run_until_complete(asyncio.sleep(0.01))
        assert seen and not seen[-1].cleared, "the subscriber never received the moved notice"
        clear()  # the Update/Retire seam, from the main thread
        deadline = time.monotonic() + HANG_GUARD_SECONDS
        while time.monotonic() < deadline and not (seen and seen[-1].cleared):
            loop.run_until_complete(asyncio.sleep(0.02))
        assert seen and seen[-1].cleared, "the clear never reached the existing subscriber"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            loop.run_until_complete(task)
        loop.close()
    return seen
