"""The held stores themselves are the authority for held-message attention (F02).

The delivery queue is a *visit order*: its entries age out after a day and a surplus one is
dropped beyond its bound. These cases drive the real store files, the real receipts and the real
queue and assert what the attention path needs -- a message that is still held reaches a reader
that the queue never named, a torn input is an incomplete observation and never an empty complete
one, and a row clears only when it leaves its store.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from agents_remember.cli.role_held_messages import (
    HeldMessage,
    hold_message,
    withdraw_message,
)
from agents_remember.controlplane import held_store_layout as layout
from agents_remember.controlplane.held_store_discovery import HeldFolderDiscovery

AGENT = "22222222-2222-4222-8222-222222222222"
OTHER_AGENT = "55555555-5555-4555-8555-555555555555"
SENDER = "44444444-4444-4444-8444-444444444444"
RECEIPTS = Path("tasks") / "repo" / "master" / "notes" / "reports" / "paseo-native-executions"


def _root(tmp_path: Path) -> Path:
    return tmp_path / "coordination"


def _folder(tmp_path: Path, name: str = "leaf") -> Path:
    folder = tmp_path / "worktrees" / name
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _receipt(root: Path, folder: Path, agent_id: str, *, name: str = "receipt") -> Path:
    path = root / RECEIPTS / f"{name}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": "ar-role-execution/v1",
                "workspace": {"path": str(folder)},
                "execution": {"agentId": agent_id},
            }
        ),
        encoding="utf-8",
    )
    return path


def _queue(root: Path, entries: dict[str, str]) -> Path:
    path = root / "temp" / layout.HELD_QUEUE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema": layout.HELD_QUEUE_SCHEMA, "entries": entries}), encoding="utf-8"
    )
    return path


def _hold(folder: Path, agent_id: str, text: str, **kwargs: Any) -> dict[str, Any]:
    message = HeldMessage(
        sender_line="Manager:",
        sender_agent_id=SENDER,
        text=text,
        reason=kwargs.pop("reason", "recipient in a turn the host cannot write into"),
        **kwargs,
    )
    return hold_message(folder, agent_id, message)


def _store_path(folder: Path, agent_id: str) -> Path:
    return layout.store_path_for(folder, agent_id)


def _agent_id(index: int) -> str:
    return str(uuid.UUID(int=10_000 + index))


def _many_holders(tmp_path: Path, count: int) -> tuple[Path, list[tuple[Path, str, str]]]:
    """``count`` recipients, each with one folder, one held row and one receipt."""

    root = _root(tmp_path)
    holders: list[tuple[Path, str, str]] = []
    for index in range(count):
        folder = _folder(tmp_path, f"leaf-{index:03d}")
        agent_id = _agent_id(index)
        held = _hold(folder, agent_id, f"batch {index}")
        _receipt(root, folder, agent_id, name=f"receipt-{index:03d}")
        holders.append((folder, agent_id, held["heldMessageId"]))
    _queue(root, {})
    return root, holders


def _case_test_a_held_store_the_queue_never_names_still_reaches_the_view(tmp_path) -> None:
    """Cold recovery: the queue names other receipts, the store holds one, the view has it."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "held-one")
    receipt = _receipt(root, folder, AGENT)
    queue = _queue(root, {str(root / "other" / "unrelated.json"): datetime.now(UTC).isoformat()})

    # The queue is the delivery machinery's own answer, and it does not name this holder at all.
    assert str(receipt) not in layout.read_queue_file(queue)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete
    assert [(row.message_id, row.recipient_agent_id, row.folder) for row in view.rows] == [
        (held["heldMessageId"], AGENT, str(folder))
    ]
    assert (view.stores_read, view.stores_known) == (1, 1)


def _case_test_an_entry_expired_by_age_costs_no_held_row(tmp_path) -> None:
    """The queue ages an entry out; the store still holds the message and the view says so."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "older than the queue's memory")
    receipt = _receipt(root, folder, AGENT)
    stale = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    queue = _queue(root, {str(receipt): stale})

    # What the delivery queue itself would visit now: nothing (the entry has aged out).
    assert layout.live_queue(layout.read_queue_file(queue)) == {}

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete
    assert [row.message_id for row in view.rows] == [held["heldMessageId"]]


def _case_test_an_entry_evicted_by_the_queue_bound_costs_no_held_row(tmp_path) -> None:
    """Beyond its entry bound the queue drops the oldest naming; the store is unaffected."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "dropped from the queue's list")
    receipt = _receipt(root, folder, AGENT)
    now = datetime.now(UTC)
    entries = {
        str(root / "filler" / f"{index}.json"): (now - timedelta(minutes=index)).isoformat()
        for index in range(layout.HELD_QUEUE_MAX_ENTRIES + 1)
    }
    entries[str(receipt)] = (now - timedelta(days=1, minutes=1)).isoformat()
    queue = _queue(root, entries)

    assert len(entries) > layout.HELD_QUEUE_MAX_ENTRIES
    assert str(receipt) not in layout.live_queue(layout.read_queue_file(queue))

    # A queue past the receipt budget is read in rotating slices, so the census over it is
    # reported only once the rotation has named every entry; the row is read regardless.
    reader = layout.HeldAuthorityReader()
    views = [reader.read(root) for _ in range(3)]
    assert [row.message_id for row in views[-1].rows] == [held["heldMessageId"]]
    assert views[-1].complete is True


def _case_test_every_store_that_holds_something_contributes_its_own_batch(tmp_path) -> None:
    """More than one delivery batch: one row per store, each named by its own receipt."""

    root = _root(tmp_path)
    first, second = _folder(tmp_path, "leaf-a"), _folder(tmp_path, "leaf-b")
    first_held = _hold(first, AGENT, "first batch")
    second_held = _hold(second, OTHER_AGENT, "second batch")
    _receipt(root, first, AGENT, name="a")
    _receipt(root, second, OTHER_AGENT, name="b")
    _queue(root, {})

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete
    assert sorted((row.recipient_agent_id, row.message_id) for row in view.rows) == sorted(
        [
            (AGENT, first_held["heldMessageId"]),
            (OTHER_AGENT, second_held["heldMessageId"]),
        ]
    )


def _case_test_a_provider_limit_hold_keeps_its_reset_time(tmp_path) -> None:
    """A row held by a provider limit reaches the view with the moment it may go out."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    reset = "2026-10-22T12:30:00+00:00"
    held = _hold(
        folder, AGENT, "waiting for the limit", reason="provider limit", limited_until=reset
    )
    _receipt(root, folder, AGENT)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete
    assert [(row.message_id, row.reason, row.limited_until) for row in view.rows] == [
        (held["heldMessageId"], "provider limit", reset)
    ]


def _case_test_a_row_clears_only_when_it_leaves_its_store(tmp_path) -> None:
    """Delivery or withdrawal removes the row; nothing else does."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "withdraw me")
    _receipt(root, folder, AGENT)
    reader = layout.HeldAuthorityReader()
    assert [row.message_id for row in reader.read(root).rows] == [held["heldMessageId"]]

    withdraw_message(folder, AGENT, held["heldMessageId"], SENDER)

    cleared = reader.read(root)
    assert cleared.complete
    assert cleared.rows == ()
    assert cleared.stores_known == 1, "the store is still there; it holds nothing now"


def _case_test_a_removed_working_folder_is_a_complete_emptiness(tmp_path) -> None:
    """A working folder that is gone takes its store with it; that is a complete no-rows read."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    _hold(folder, AGENT, "gone with the folder")
    _receipt(root, folder, AGENT)

    shutil.rmtree(folder)

    view = layout.HeldAuthorityReader().read(root)
    assert view.complete
    assert view.rows == ()


def _case_test_a_torn_queue_is_incomplete_rather_than_empty(tmp_path) -> None:
    """An unreadable queue resolves nothing; the store's rows are still what the reader read."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "still held behind a torn queue")
    _receipt(root, folder, AGENT)
    queue = root / "temp" / layout.HELD_QUEUE_FILE
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("{not json", encoding="utf-8")

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is False
    assert [row.message_id for row in view.rows] == [held["heldMessageId"]]

    # Repaired, the same reader reports the census complete again.
    _queue(root, {})
    assert layout.HeldAuthorityReader().read(root).complete is True


def _case_test_a_cold_reader_over_a_torn_queue_still_reads_the_store(tmp_path) -> None:
    """The walk, not the queue, is what a cold reader discovers with: rows survive a torn queue.

    The delivery queue is only an accelerator. A reader constructed after the queue was torn has
    no retained state at all, so the row it reports can only come from the receipt walk -- and an
    incomplete census must never be published as an empty one.
    """

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "held behind a torn queue on a cold reader")
    _receipt(root, folder, AGENT)
    queue = root / "temp" / layout.HELD_QUEUE_FILE
    queue.parent.mkdir(parents=True, exist_ok=True)
    queue.write_text("{broken", encoding="utf-8")

    view = layout.HeldAuthorityReader().read(root)

    assert [row.message_id for row in view.rows] == [held["heldMessageId"]]
    assert view.complete is False
    assert (view.stores_read, view.stores_known) == (1, 1)


def _case_test_a_cold_reader_with_no_queue_at_all_still_reads_the_store(tmp_path) -> None:
    """No queue file is an ordinary state, not a reason to lose what the stores hold."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "held with no queue file")
    _receipt(root, folder, AGENT)
    assert not (root / "temp" / layout.HELD_QUEUE_FILE).exists()

    view = layout.HeldAuthorityReader().read(root)

    assert [row.message_id for row in view.rows] == [held["heldMessageId"]]
    assert view.complete is True


def _case_test_a_store_that_goes_unreadable_keeps_the_row_the_reader_already_read(tmp_path) -> None:
    """A torn store is a partial observation: the last whole read stands, never a clear."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "read once, then unreadable")
    receipt = _receipt(root, folder, AGENT)
    reader = layout.HeldAuthorityReader()
    assert reader.read(root).complete is True

    store = _store_path(folder, AGENT)
    store.write_text("{broken", encoding="utf-8")

    torn = reader.read(root)
    assert [row.message_id for row in torn.rows] == [held["heldMessageId"]]
    assert torn.complete is False

    # Repaired, the same reader is whole again; a delivered row still clears.
    store.unlink()
    repaired = reader.read(root)
    assert repaired.rows == ()
    assert repaired.complete is True
    assert receipt.exists(), "the receipt is only a name; deleting a store is not delivery"


def _case_test_a_torn_store_is_incomplete_and_never_an_empty_store(tmp_path) -> None:
    """A store that cannot be parsed proves nothing; the reader keeps a partial view."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "unreadable")
    _receipt(root, folder, AGENT)
    _store_path(folder, AGENT).write_text("{broken", encoding="utf-8")

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is False
    assert view.stores_read == view.stores_known == 1
    assert view.rows == ()
    assert held["heldMessageId"], "the store still holds a row the read could not state"


def _case_test_a_row_the_store_could_not_have_written_makes_the_read_incomplete(tmp_path) -> None:
    """A store whose messages are partly unreadable is not reported as fully read."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    store = _store_path(folder, AGENT)
    store.parent.mkdir(parents=True, exist_ok=True)
    store.write_text(
        json.dumps(
            {
                "schema": layout.HELD_STORE_SCHEMA,
                "agentId": AGENT,
                "messages": [{"id": "message-1", "reason": "held by a test"}, {"no-id": True}],
                "updatedAt": datetime.now(UTC).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    _receipt(root, folder, AGENT)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is False
    assert [row.message_id for row in view.rows] == ["message-1"]


def _case_test_a_row_stamped_long_ago_is_never_dropped_for_its_age(tmp_path) -> None:
    """Nothing in this read expires by time: only delivery or withdrawal ends a row."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    store = _store_path(folder, AGENT)
    store.parent.mkdir(parents=True, exist_ok=True)
    taken = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    store.write_text(
        json.dumps(
            {
                "schema": layout.HELD_STORE_SCHEMA,
                "agentId": AGENT,
                "messages": [
                    {
                        "id": "message-old",
                        "senderAgentId": SENDER,
                        "takenAt": taken,
                        "reason": "held",
                    }
                ],
                "updatedAt": taken,
            }
        ),
        encoding="utf-8",
    )
    _receipt(root, folder, AGENT)
    reader = layout.HeldAuthorityReader()

    for _ in range(3):
        view = reader.read(root)
        assert view.complete is True
        assert [row.message_id for row in view.rows] == ["message-old"]


def _case_test_a_pass_over_more_receipts_than_one_observation_resumes_bounded(
    tmp_path, monkeypatch
) -> None:
    """Discovery is bounded per observation and says so until its pass reaches the end."""

    root = _root(tmp_path)
    folders = [_folder(tmp_path, name) for name in ("leaf-a", "leaf-b", "leaf-c")]
    agents = [AGENT, OTHER_AGENT, SENDER]
    held = [
        _hold(folder, agent_id, f"batch {index}")
        for index, (folder, agent_id) in enumerate(zip(folders, agents, strict=True))
    ]
    for index, (folder, agent_id) in enumerate(zip(folders, agents, strict=True)):
        _receipt(root, folder, agent_id, name=f"receipt-{index}")
    monkeypatch.setattr(layout, "MAX_HELD_AUTHORITY_RECEIPTS", 2)
    reader = layout.HeldAuthorityReader()

    partial = reader.read(root)
    assert partial.complete is False
    assert len(partial.rows) == 2

    finished = reader.read(root)
    assert finished.complete is True
    assert sorted(row.message_id for row in finished.rows) == sorted(
        row["heldMessageId"] for row in held
    )


def _case_test_rows_beyond_the_row_bound_are_shed_and_the_view_says_incomplete(
    tmp_path, monkeypatch
) -> None:
    """A bounded answer is never passed off as everything that is held."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    _hold(folder, AGENT, "first")
    _hold(folder, AGENT, "second")
    _receipt(root, folder, AGENT)
    monkeypatch.setattr(layout, "MAX_HELD_AUTHORITY_ROWS", 1)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is False
    assert len(view.rows) == 1


def _case_test_a_receipt_that_names_no_existing_store_creates_nothing(tmp_path) -> None:
    """No invented store: a receipt for a folder with no store file reads as nothing held."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    _receipt(root, folder, AGENT)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is True
    assert view.rows == ()
    assert view.stores_known == 0
    assert not (folder / ".ar-product" / "held-messages").exists()


def _case_test_an_unreadable_receipt_makes_the_census_incomplete(tmp_path) -> None:
    """A receipt that cannot be parsed may name a store that holds rows: no complete claim."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    _hold(folder, AGENT, "held behind a torn receipt")
    receipt = _receipt(root, folder, AGENT)
    receipt.write_text("{broken", encoding="utf-8")

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is False
    assert view.rows == ()


def test_the_shared_reader_is_resettable(tmp_path) -> None:
    """The process-wide reader forgets its retained view on request (a cold start)."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    held = _hold(folder, AGENT, "shared reader")
    _receipt(root, folder, AGENT)

    layout.reset_held_authority()
    assert [row.message_id for row in layout.read_held_authority(root).rows] == [
        held["heldMessageId"]
    ]
    layout.reset_held_authority()
    assert layout.read_held_authority(root).complete is True


@pytest.mark.parametrize("agent_id", ["not-a-uuid", ""])
def test_a_store_name_that_is_not_an_agent_id_is_never_read(tmp_path, agent_id: str) -> None:
    """Only an AR-minted id names a store; other receipts are not store locations."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    _receipt(root, folder, agent_id)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is True
    assert view.rows == ()
    assert view.stores_known == 0


def _case_test_sixty_five_holders_reach_a_complete_pass_by_rotation(tmp_path) -> None:
    """More holders than one pass reads: each pass keeps its own records until all are read.

    L190-R1-F02: completeness is a claim about every known store's *record*, not about how many
    stores this one call happened to read, so the 65th holder is not a permanent reason to
    report an incomplete census -- and no row of the 64 already read is dropped while it waits.
    """

    root, holders = _many_holders(tmp_path, 65)
    assert len(holders) == 65 > layout.MAX_HELD_AUTHORITY_STORES
    reader = layout.HeldAuthorityReader()

    first = reader.read(root)
    assert first.complete is False
    assert first.stores_read == layout.MAX_HELD_AUTHORITY_STORES
    assert first.stores_known == 65
    assert len(first.rows) == first.stores_read, "every store this pass read contributes its row"

    second = reader.read(root)
    assert second.complete is True
    assert second.stores_read == 65 - layout.MAX_HELD_AUTHORITY_STORES, "only what pass 1 missed"
    assert second.stores_known == 65
    # Rows are retained per store, so the complete pass still carries pass 1's rows.
    assert {row.message_id for row in first.rows} <= {row.message_id for row in second.rows}
    assert len(second.rows) == 65
    assert {row.message_id for row in second.rows} == {held for _, _, held in holders}


def _case_test_a_row_cleared_between_passes_does_not_survive_the_complete_pass(tmp_path) -> None:
    """The reviewer's stuck-clearing case: a delivery after pass 1 clears on the complete pass."""

    root, _holders = _many_holders(tmp_path, 65)
    reader = layout.HeldAuthorityReader()
    first = reader.read(root)
    assert first.complete is False

    delivered = first.rows[0]
    withdraw_message(
        Path(delivered.folder), delivered.recipient_agent_id, delivered.message_id, SENDER
    )

    second = reader.read(root)
    assert second.complete is True
    assert delivered.message_id not in {row.message_id for row in second.rows}
    assert len(second.rows) == 64


def _case_test_a_torn_store_among_sixty_five_keeps_its_bytes_and_the_view_incomplete(
    tmp_path,
) -> None:
    """One unreadable store among many: the others still report their rows, nothing is complete."""

    root, holders = _many_holders(tmp_path, 65)
    folder, agent_id, _held = holders[0]
    torn = _store_path(folder, agent_id)
    torn.write_text("{broken", encoding="utf-8")
    before = torn.read_bytes()
    reader = layout.HeldAuthorityReader()

    first = reader.read(root)
    assert first.complete is False
    assert len(first.rows) == layout.MAX_HELD_AUTHORITY_STORES - 1, "the good stores' rows"
    assert torn.read_bytes() == before, "a reader never repairs or rewrites a store"

    second = reader.read(root)
    assert second.complete is False, "a store that cannot be read never makes a complete census"
    assert len(second.rows) == 64, "the holder pass 1 never read arrives even while incomplete"
    assert torn.read_bytes() == before


def _case_test_retention_trims_the_retained_view_on_an_incomplete_pass(
    tmp_path, monkeypatch
) -> None:
    """Eviction is not conditional on the census: an incomplete pass still sheds to its cap."""

    root, _holders = _many_holders(tmp_path, 65)
    monkeypatch.setattr(layout, "MAX_HELD_AUTHORITY_RETAINED", 64)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is False
    assert view.stores_known == 64, "the retained view is trimmed to its cap whatever it read"
    assert len(view.rows) == 64, "a shed store contributes no row to the view"

    again = layout.HeldAuthorityReader().read(root)
    assert again.stores_known == 64


def _case_test_store_discovery_is_walked_within_one_budget_per_call(tmp_path, monkeypatch) -> None:
    """L190-R1-F11: a crowded held folder is walked within a budget and resumed, never drained.

    The reviewer's residual: the heap capped what was *kept* while the walk still visited and
    stat'd every entry, so one call cost time proportional to the folder's whole population. With a
    budget below the population one call meets at most the budget and reports itself incomplete,
    successive calls resume where it stopped, and the call that reaches the end names the newest
    stores a whole walk would have named.
    """

    directory = layout.product_held_dir(_folder(tmp_path, "crowded"))
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(300):
        path = directory / f"{_agent_id(index)}.json"
        path.write_text("{}", encoding="utf-8")
        os.utime(path, ns=(1_000_000 + index, 1_000_000 + index))

    discovery = HeldFolderDiscovery()

    found, scanned, complete, _whole = discovery.read(directory, limit=8, budget=64)
    assert scanned == 64, "one call visits the budget, not the folder's population"
    assert complete is False, "a walk short of its end never claims to have named the population"
    assert len(found) == 8

    calls = 1
    while not complete:
        found, scanned, complete, _whole = discovery.read(directory, limit=8, budget=64)
        assert scanned <= 64, "every call stays within the budget"
        calls += 1
        assert calls <= 8, "the walk must resume, not restart, or it never completes"

    assert calls == 5, "300 entries at a budget of 64 take five calls"
    newest = {f"{_agent_id(index)}.json" for index in range(292, 300)}
    assert {path.name for path in found} == newest, "the completed walk names the newest stores"

    # A folder walked whole is re-listed from its retained heap: its entries are not read again.
    again, rescanned, complete_again, _whole = discovery.read(directory, limit=8, budget=64)
    assert (again, rescanned, complete_again) == (found, 0, True)

    # The default bound walks the same way and, once whole, names the newest of its own bound.
    discovery.reset()
    for _ in range(8):
        default, _default_scanned, complete, _whole = discovery.read(directory, budget=64)
        if complete:
            break
    assert complete
    assert {path.name for path in default} == {
        f"{_agent_id(index)}.json" for index in range(300 - layout.MAX_HELD_AUTHORITY_STORES, 300)
    }
    assert discovery.read(directory).paths == default


def _case_test_a_store_beside_a_named_one_is_reached_by_the_bounded_enumeration(tmp_path) -> None:
    """One folder can hold several seats' stores; the folder's own bounded listing finds them."""

    root, folder = _root(tmp_path), _folder(tmp_path)
    named = _hold(folder, AGENT, "named by its receipt")
    beside = _hold(folder, OTHER_AGENT, "in the same folder, no receipt")
    _receipt(root, folder, AGENT)

    view = layout.HeldAuthorityReader().read(root)

    assert view.complete is True
    assert view.stores_known == 2
    assert sorted(row.message_id for row in view.rows) == sorted(
        [named["heldMessageId"], beside["heldMessageId"]]
    )


# Consolidated cases: one group test per requirement. Every assertion and every failure
# message survives; each case is a subtest with its own coordination root.


def test_the_queue_is_a_hint_never_the_census(subtests, tmp_path, monkeypatch) -> None:
    """F02: a store the queue never names, ages out or evicts is still read, and still contributes."""

    for message, run in (
        (
            "a held store the queue never names still reaches the view",
            lambda **fixtures: _case_test_a_held_store_the_queue_never_names_still_reaches_the_view(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "an entry expired by age costs no held row",
            lambda **fixtures: _case_test_an_entry_expired_by_age_costs_no_held_row(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "an entry evicted by the queue bound costs no held row",
            lambda **fixtures: _case_test_an_entry_evicted_by_the_queue_bound_costs_no_held_row(
                **_take(fixtures, "tmp_path")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_each_store_contributes_its_own_rows(subtests, tmp_path, monkeypatch) -> None:
    """F02: every store that holds something contributes its own rows, and a row leaves with its store."""

    for message, run in (
        (
            "every store that holds something contributes its own batch",
            lambda **fixtures: (
                _case_test_every_store_that_holds_something_contributes_its_own_batch(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a provider limit hold keeps its reset time",
            lambda **fixtures: _case_test_a_provider_limit_hold_keeps_its_reset_time(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a row clears only when it leaves its store",
            lambda **fixtures: _case_test_a_row_clears_only_when_it_leaves_its_store(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a removed working folder is a complete emptiness",
            lambda **fixtures: _case_test_a_removed_working_folder_is_a_complete_emptiness(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a row stamped long ago is never dropped for its age",
            lambda **fixtures: _case_test_a_row_stamped_long_ago_is_never_dropped_for_its_age(
                **_take(fixtures, "tmp_path")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_a_torn_observation_is_incomplete_never_empty(subtests, tmp_path, monkeypatch) -> None:
    """F02/F07: a torn queue, torn store, unreadable receipt or impossible row makes the read incomplete."""

    for message, run in (
        (
            "a torn queue is incomplete rather than empty",
            lambda **fixtures: _case_test_a_torn_queue_is_incomplete_rather_than_empty(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a cold reader over a torn queue still reads the store",
            lambda **fixtures: _case_test_a_cold_reader_over_a_torn_queue_still_reads_the_store(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a cold reader with no queue at all still reads the store",
            lambda **fixtures: _case_test_a_cold_reader_with_no_queue_at_all_still_reads_the_store(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a store that goes unreadable keeps the row the reader already read",
            lambda **fixtures: (
                _case_test_a_store_that_goes_unreadable_keeps_the_row_the_reader_already_read(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a torn store is incomplete and never an empty store",
            lambda **fixtures: _case_test_a_torn_store_is_incomplete_and_never_an_empty_store(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a row the store could not have written makes the read incomplete",
            lambda **fixtures: (
                _case_test_a_row_the_store_could_not_have_written_makes_the_read_incomplete(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "an unreadable receipt makes the census incomplete",
            lambda **fixtures: _case_test_an_unreadable_receipt_makes_the_census_incomplete(
                **_take(fixtures, "tmp_path")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_bounds_shed_only_what_they_must(subtests, tmp_path, monkeypatch) -> None:
    """F02/F11: the receipt walk, the row bound and a nameless store are bounded without inventing rows."""

    for message, run in (
        (
            "a pass over more receipts than one observation resumes bounded",
            lambda **fixtures: (
                _case_test_a_pass_over_more_receipts_than_one_observation_resumes_bounded(
                    **_take(fixtures, "tmp_path", "monkeypatch")
                )
            ),
        ),
        (
            "rows beyond the row bound are shed and the view says incomplete",
            lambda **fixtures: (
                _case_test_rows_beyond_the_row_bound_are_shed_and_the_view_says_incomplete(
                    **_take(fixtures, "tmp_path", "monkeypatch")
                )
            ),
        ),
        (
            "a receipt that names no existing store creates nothing",
            lambda **fixtures: _case_test_a_receipt_that_names_no_existing_store_creates_nothing(
                **_take(fixtures, "tmp_path")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_rotation_reaches_a_complete_census(subtests, tmp_path, monkeypatch) -> None:
    """F02: more holders than one pass reads reach a complete clearing by rotation."""

    for message, run in (
        (
            "sixty five holders reach a complete pass by rotation",
            lambda **fixtures: _case_test_sixty_five_holders_reach_a_complete_pass_by_rotation(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a row cleared between passes does not survive the complete pass",
            lambda **fixtures: (
                _case_test_a_row_cleared_between_passes_does_not_survive_the_complete_pass(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a torn store among sixty five keeps its bytes and the view incomplete",
            lambda **fixtures: (
                _case_test_a_torn_store_among_sixty_five_keeps_its_bytes_and_the_view_incomplete(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a store beside a named one is reached by the bounded enumeration",
            lambda **fixtures: (
                _case_test_a_store_beside_a_named_one_is_reached_by_the_bounded_enumeration(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_bounds_hold_on_an_incomplete_pass(subtests, tmp_path, monkeypatch) -> None:
    """F11: retention and enumeration bounds hold even when the pass is not complete."""

    for message, run in (
        (
            "retention trims the retained view on an incomplete pass",
            lambda **fixtures: _case_test_retention_trims_the_retained_view_on_an_incomplete_pass(
                **_take(fixtures, "tmp_path", "monkeypatch")
            ),
        ),
        (
            "store discovery is walked within one budget per call",
            lambda **fixtures: _case_test_store_discovery_is_walked_within_one_budget_per_call(
                **_take(fixtures, "tmp_path", "monkeypatch")
            ),
        ),
    ):
        with subtests.test(msg=message):
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def _slug(message: str) -> str:
    """A directory name for one case, so no two subtests share a coordination root."""

    return "".join(char if char.isalnum() else "-" for char in message)[:48]


def _take(fixtures: dict, *names: str) -> dict:
    """The fixtures one case declares, so a group can supply its whole set."""

    return {name: fixtures[name] for name in names}
