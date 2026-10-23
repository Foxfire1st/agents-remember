"""The terminal-notice store publishes once per native occurrence and never on a guess.

A notice is written as a pending intent before the native publication and confirmed after it, so
these cases pin the three settle verdicts (proven, refuted, undecidable), the exact-retry rule, the
acknowledgement kinds the dismissal store accepts, the creation cut that says where this store's
publication order begins, the count and whole-file byte bounds the store enforces over the exact
document it writes, and the four read states -- a missing store, a readable empty one, a populated
one, and bytes this build cannot interpret, which is never emptiness.
"""

from __future__ import annotations

import itertools
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from agents_remember.controlplane import attention_notices
from agents_remember.controlplane.attention_dismissals import (
    AttentionDismissalRecord,
    AttentionDismissalStore,
    AttentionObservation,
)
from agents_remember.controlplane.attention_notices import (
    MAX_NOTICE_STORE_BYTES,
    MAX_PENDING_NOTICES,
    MAX_TERMINAL_NOTICES,
    AttentionNoticeStore,
    PendingTerminalNotice,
    StoreCut,
    TerminalKind,
    TerminalNotice,
    _publication_order,
    begin_terminal_notice,
    confirm_terminal_notice,
    discard_terminal_notice,
    notice_store_path,
    read_notice_store,
    read_notice_store_state,
    serialize_notice_store,
    settle_terminal_notices,
)
from agents_remember.models.task_document_ref import TaskDocumentRef
from agents_remember.observer.native_attention import (
    NativeAttentionFacts,
    observe_terminal_notices,
)
from agents_remember.observer.reducer import AnalyticalInputs, build_attention_queue
from pydantic import ValidationError

NOW = datetime(2026, 10, 22, 12, 0, tzinfo=UTC)
LEAF_KEY = "repo/master/leaf.json"
MB_REF = TaskDocumentRef(repository="仓库", path="master/子流程/叶子任务.json")


@pytest.fixture(autouse=True)
def _pinned_publication_clock(monkeypatch):
    """Pin the one native publication clock this store reads, so no case depends on the hour.

    A store's creation cut and a confirmed notice's `at` are both read from `occurrence_at()`,
    while these cases write fixtures at fixed 12:xx stamps. On the wall clock the two intertwine:
    the suite passes in the morning and starts failing at midday, which is exactly how two of
    these cases behaved. The clock is pinned once, monotonically, above every fixture stamp.
    """

    ticks = itertools.count()
    base = datetime(2026, 10, 22, 13, 0, tzinfo=UTC)
    monkeypatch.setattr(
        attention_notices,
        "occurrence_at",
        lambda now=None: (base + timedelta(seconds=next(ticks))).isoformat(),
    )


def _ref() -> TaskDocumentRef:
    return TaskDocumentRef(repository="repo", path="master/leaf.json")


def _pending(
    occurrence: str = f"task-landed:{LEAF_KEY}:abc123",
    kind: TerminalKind = "task-landed",
    at: str = "2026-10-22T11:58:00+00:00",
):
    return PendingTerminalNotice(
        occurrenceId=occurrence,
        kind=kind,
        taskKey=LEAF_KEY,
        taskReference=_ref(),
        taskTitle="Leaf",
        detail="landed abc123 via merge",
        at=at,
    )


def _queue(store_path_root: Path):
    """The rows the projection publishes from the store as it stands on disk."""

    store = read_notice_store(store_path_root)
    assert store is not None
    given = AnalyticalInputs(
        native_attention=NativeAttentionFacts(
            notices=tuple(store.published()), notices_complete=True
        )
    )
    return build_attention_queue([], given, [], NOW)


def _queue_with(
    store_path_root: Path, dismissals: dict[str, AttentionDismissalRecord]
) -> list[str]:
    """The rows the projection publishes, given the acknowledgements it holds."""

    store = read_notice_store(store_path_root)
    assert store is not None
    given = AnalyticalInputs(
        native_attention=NativeAttentionFacts(
            notices=tuple(store.published()), notices_complete=True
        ),
        attention_dismissals=dismissals,
    )
    return [item.id for item in build_attention_queue([], given, [], NOW)]


def _multibyte_pending(
    index: int,
    *,
    title_chars: int = 486,
    at: str | None = None,
    root: str | None = None,
) -> PendingTerminalNotice:
    """One allowed pending row with multibyte identity and reference, ~4 KiB serialized."""

    title = ("标题" * ((title_chars + 1) // 2))[:title_chars]
    return PendingTerminalNotice(
        occurrenceId=f"task-landed:{MB_REF.key}:提交-{index:02d}-" + "安" * 60,
        kind="task-landed",
        taskKey="任务键/" + "叶子" * 246,
        taskReference=MB_REF,
        taskTitle=title,
        detail="发布细节" * 70,
        at=at or f"2026-10-22T11:{index:02d}:00+00:00",
        root=root,
    )


def _serialized_bytes(notices: list, *, cut_at: str) -> int:
    """The exact byte count of the document a store built from ``notices`` writes."""

    store = AttentionNoticeStore(pending=notices, cut=StoreCut(at=cut_at, sequence=1))
    return len(serialize_notice_store(store).encode("utf-8"))


def _rows_near_the_cap() -> list[PendingTerminalNotice]:
    """The largest pending-only store that lands just under the declared byte cap.

    The creating store's cut is stamped by the runtime clock, so a store built here cannot know the
    document's last few bytes in advance. These rows land inside the padding window instead, and
    the caller fills to the exact cap by measuring the document the writer actually publishes.
    """

    for title_chars in range(512, 0, -1):
        rows = [
            _multibyte_pending(index, title_chars=title_chars)
            for index in range(MAX_PENDING_NOTICES)
        ]
        size = _serialized_bytes(rows, cut_at=rows[0].at)
        if MAX_NOTICE_STORE_BYTES - 4096 <= size <= MAX_NOTICE_STORE_BYTES - 128:
            return rows
    raise AssertionError("no pending-only store fits the cap window; the row builder drifted")


def _kept_is_a_suffix(order: list[str], kept: list[str]) -> bool:
    """Whether ``kept`` is the tail of ``order`` -- the whole of "oldest rows first"."""

    return kept == order[len(order) - len(kept) :]


def _case_test_begin_then_confirm_publishes_exactly_one_notice(tmp_path) -> None:
    notice = _pending()

    begin_terminal_notice(tmp_path, notice)
    confirm_terminal_notice(tmp_path, notice.occurrenceId)

    store = read_notice_store(tmp_path)
    assert store is not None
    assert store.pending == []
    assert [row.occurrenceId for row in store.notices] == [notice.occurrenceId]
    assert [row.sequence for row in store.notices] == [1]
    assert [row.id for row in _queue(tmp_path)] == [notice.occurrenceId]
    assert notice_store_path(tmp_path).read_text(encoding="utf-8").count(notice.occurrenceId) == 1


def _case_test_confirming_the_same_occurrence_again_adds_nothing(tmp_path) -> None:
    notice = _pending()
    begin_terminal_notice(tmp_path, notice)
    confirm_terminal_notice(tmp_path, notice.occurrenceId)
    first = read_notice_store(tmp_path)
    assert first is not None
    before = notice_store_path(tmp_path).read_bytes()

    confirm_terminal_notice(tmp_path, notice.occurrenceId)
    begin_terminal_notice(tmp_path, notice)
    confirm_terminal_notice(tmp_path, notice.occurrenceId)

    after = read_notice_store(tmp_path)
    assert after is not None
    assert after.notices == first.notices
    assert after.sequence == 1
    assert notice_store_path(tmp_path).read_bytes() == before


def _case_test_begin_without_confirm_announces_nothing(tmp_path) -> None:
    notice = _pending()

    begin_terminal_notice(tmp_path, notice)

    store = read_notice_store(tmp_path)
    assert store is not None
    assert [row.occurrenceId for row in store.pending] == [notice.occurrenceId]
    assert store.notices == []
    assert store.published() == []
    assert _queue(tmp_path) == []


def _case_test_settle_refutes_and_drops_the_intent(tmp_path) -> None:
    notice = _pending()
    begin_terminal_notice(tmp_path, notice)

    store = settle_terminal_notices(tmp_path, lambda _row: False)

    assert store is not None
    assert store.pending == []
    assert store.notices == []
    assert _queue(tmp_path) == []


def _case_test_settle_proves_and_publishes_the_intent_once(tmp_path) -> None:
    notice = _pending()
    begin_terminal_notice(tmp_path, notice)

    settled = settle_terminal_notices(tmp_path, lambda _row: True)
    repeated = settle_terminal_notices(tmp_path, lambda _row: True)

    assert settled is not None and repeated is not None
    assert [row.occurrenceId for row in settled.notices] == [notice.occurrenceId]
    assert settled.pending == []
    assert [row.sequence for row in repeated.notices] == [1]
    assert repeated.pending == []


def _case_test_settle_keeps_an_undecidable_intent_for_a_later_pass(tmp_path) -> None:
    notice = _pending()
    begin_terminal_notice(tmp_path, notice)

    pending = settle_terminal_notices(tmp_path, lambda _row: None)

    assert pending is not None
    assert [row.occurrenceId for row in pending.pending] == [notice.occurrenceId]
    assert pending.notices == []

    proven = settle_terminal_notices(tmp_path, lambda _row: True)
    assert proven is not None
    assert proven.pending == []
    assert [row.occurrenceId for row in proven.notices] == [notice.occurrenceId]


def _case_test_an_empty_store_publishes_nothing_and_notices_survive_a_reread(tmp_path) -> None:
    empty = read_notice_store(tmp_path)
    assert empty is not None
    assert empty.published() == []
    assert not notice_store_path(tmp_path).exists()
    assert _queue(tmp_path) == []

    notice = _pending()
    begin_terminal_notice(tmp_path, notice)
    confirm_terminal_notice(tmp_path, notice.occurrenceId)

    restarted = read_notice_store(tmp_path)
    assert restarted is not None
    assert [row.occurrenceId for row in restarted.published()] == [notice.occurrenceId]
    assert [row.id for row in _queue(tmp_path)] == [notice.occurrenceId]


def _case_test_every_published_kind_can_be_acknowledged() -> None:
    """MIK-R200@v2 rule 7: the three live causes are acknowledged like the notices."""

    for kind in (
        "task-abandoned",
        "task-landed",
        "permission-wait",
        "held-message",
        "task-stalled",
        "provider-down",
        "actionable-drift",
        "failed-setup",
        "blocked-start",
    ):
        record = AttentionDismissalRecord(
            itemId=f"{kind}:" + LEAF_KEY,
            kind=kind,
            dismissedAt="2026-10-22T12:00:00Z",
        )
        assert record.kind == kind

    # A kind this build does not publish is refused, so an older row is never read as current.
    for refused in ("task-stalled-v2", "message-held", "attention"):
        with pytest.raises(ValidationError):
            AttentionDismissalRecord(
                itemId=f"{refused}:whatever",
                kind=refused,
                dismissedAt="2026-10-22T12:00:00Z",
            )


def _case_test_the_store_keeps_at_most_the_newest_bounded_occurrences(tmp_path) -> None:
    total = MAX_TERMINAL_NOTICES + 6
    for index in range(total):
        notice = _pending(f"task-landed:{LEAF_KEY}:commit-{index:03d}")
        begin_terminal_notice(tmp_path, notice)
        confirm_terminal_notice(tmp_path, notice.occurrenceId)

    store = read_notice_store(tmp_path)
    assert store is not None
    assert len(store.notices) == MAX_TERMINAL_NOTICES
    assert len(store.published()) == MAX_TERMINAL_NOTICES
    occurrences = [row.occurrenceId for row in store.notices]
    assert occurrences[-1] == f"task-landed:{LEAF_KEY}:commit-{total - 1:03d}"
    assert occurrences[0] == f"task-landed:{LEAF_KEY}:commit-{total - MAX_TERMINAL_NOTICES:03d}"
    assert store.notices[-1].sequence == total
    assert [row.sequence for row in store.notices] == list(
        range(total - MAX_TERMINAL_NOTICES + 1, total + 1)
    )


# --- rule 6: the one cut taken when the store is created ----------------------------------------


def _case_test_a_store_created_over_existing_terminal_records_publishes_none_of_them(
    tmp_path,
) -> None:
    root = tmp_path / "coordination"
    # A terminal record that predates the store: a landing the enclosure already holds.
    enclosure = root / "enclosures" / "leaf-1.json"
    enclosure.parent.mkdir(parents=True, exist_ok=True)
    enclosure.write_text(
        json.dumps(
            {
                "leafId": "leaf-1",
                "integrationStatus": "completed",
                "completedAt": "2026-10-20T09:00:00+00:00",
                "cleanup": "kept",
            }
        ),
        encoding="utf-8",
    )
    history = _pending(f"task-landed:{LEAF_KEY}:commit-old", at="2026-10-20T09:00:00+00:00")
    fresh = _pending(f"task-landed:{LEAF_KEY}:commit-new", at="2026-10-22T12:30:00+00:00")

    begin_terminal_notice(root, fresh)
    confirm_terminal_notice(root, fresh.occurrenceId)

    store = read_notice_store(root)
    assert store is not None
    assert store.cut is not None
    # The cut is the publication position read inside store creation: not zero, not the intent's
    # own pre-publication stamp, not a projection sequence.
    assert store.cut.sequence == 1
    assert _publication_order(store.cut.at) > _publication_order(history.at)
    # Neither stamp is the cut: they are the intents' own pre-publication reads.
    assert store.cut.at not in {fresh.at, history.at}
    assert store.published()[0].at not in {
        fresh.at,
        history.at,
    }  # confirmation position, not intent
    assert [row.occurrenceId for row in store.published()] == [fresh.occurrenceId]
    assert [row.id for row in _queue(root)] == [fresh.occurrenceId]
    assert history.occurrenceId not in notice_store_path(root).read_text(encoding="utf-8")
    # A position before the cut is history, even at the store's own first sequence number.
    assert store.cut.admits(TerminalNotice(sequence=1, **history.model_dump())) is False
    # The row this store published carries the confirmation position, and its own cut admits it;
    # the intent's pre-publication stamp is not the row (asserted above), so it is not the subject.
    assert store.cut.admits(store.published()[0]) is True


def _case_test_the_cut_excludes_a_retained_notice_that_sits_before_it(tmp_path) -> None:
    early = _pending(f"task-landed:{LEAF_KEY}:commit-early", at="2026-10-22T10:00:00+00:00")
    store = AttentionNoticeStore(
        cut=StoreCut(at="2026-10-22T11:00:00+00:00", sequence=1),
        notices=[TerminalNotice(sequence=1, **early.model_dump())],
    )
    assert [row.occurrenceId for row in store.notices] == [early.occurrenceId]
    assert store.published() == []  # retained, and still not published

    notice_store_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    notice_store_path(tmp_path).write_text(serialize_notice_store(store), encoding="utf-8")
    persisted = read_notice_store(tmp_path)
    assert persisted is not None
    assert persisted.cut == StoreCut(at="2026-10-22T11:00:00+00:00", sequence=1)
    assert persisted.published() == []

    admitted = AttentionNoticeStore(
        cut=StoreCut(at="2026-10-22T09:00:00+00:00", sequence=1),
        notices=[TerminalNotice(sequence=1, **early.model_dump())],
    )
    assert [row.occurrenceId for row in admitted.published()] == [early.occurrenceId]


def _case_test_a_publication_confirmed_after_the_cut_is_admitted_even_when_its_intent_predates_it(
    tmp_path,
) -> None:
    """The ruling's counterexample: A reads t0, B creates the store at t1, A publishes after t1.

    A's intent stamp is older than the cut because it was read before B created the store. The
    cut must not filter A: A's *publication* happened after creation, and only the store's own
    creation order can say so. Filtering on the intent stamp would silently drop a real landing.
    """

    early = _pending(f"task-landed:{LEAF_KEY}:commit-early", at="2026-10-22T11:00:00+00:00")
    creating = _pending(f"task-landed:{LEAF_KEY}:commit-creating", at="2026-10-22T11:30:00+00:00")

    begin_terminal_notice(tmp_path, creating)  # B creates the store
    confirm_terminal_notice(tmp_path, creating.occurrenceId)
    created = read_notice_store(tmp_path)
    assert created is not None and created.cut is not None
    assert created.cut.sequence == 1

    begin_terminal_notice(tmp_path, early)  # A's intent arrives, older than the cut
    confirm_terminal_notice(tmp_path, early.occurrenceId)  # A publishes after creation

    store = read_notice_store(tmp_path)
    assert store is not None
    assert store.cut == created.cut  # never retaken by a later write
    assert [row.occurrenceId for row in store.published()] == [
        creating.occurrenceId,
        early.occurrenceId,
    ]
    assert [row.sequence for row in store.published()] == [1, 2]


def _case_test_a_notice_confirmed_before_the_store_existed_cannot_be_invented_later(
    tmp_path,
) -> None:
    """The other half of the cut: nothing older than creation is backfilled from the record."""

    old = _pending(f"task-landed:{LEAF_KEY}:commit-old", at="2026-10-20T09:00:00+00:00")

    begin_terminal_notice(tmp_path, _pending(f"task-landed:{LEAF_KEY}:commit-new"))
    store = read_notice_store(tmp_path)
    assert store is not None and store.cut is not None

    # The intent for a publication that predates the store is not an intent this store took.
    assert store.cut.admits(TerminalNotice(sequence=1, **old.model_dump())) is False
    assert old.occurrenceId not in notice_store_path(tmp_path).read_text(encoding="utf-8")


def _case_test_a_record_published_while_the_store_is_created_is_assigned_exactly_once(
    tmp_path,
) -> None:
    root = tmp_path / "coordination"
    early = _pending(f"task-landed:{LEAF_KEY}:commit-early", at="2026-10-22T12:00:00+00:00")
    late = _pending(f"task-landed:{LEAF_KEY}:commit-late", at="2026-10-22T12:00:01+00:00")
    start = threading.Barrier(2)

    def publish(row: PendingTerminalNotice) -> None:
        start.wait()
        begin_terminal_notice(root, row)
        confirm_terminal_notice(root, row.occurrenceId)

    threads = [threading.Thread(target=publish, args=(row,)) for row in (early, late)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    store = read_notice_store(root)
    assert store is not None
    assert store.cut is not None
    assert store.cut.sequence == 1
    published = [row.occurrenceId for row in store.published()]
    retained = [row.occurrenceId for row in store.notices]
    # One store, one cut, and one row per occurrence: whoever raced, nothing is doubled or lost.
    assert sorted(retained) == sorted([early.occurrenceId, late.occurrenceId])
    assert len(published) == len(set(published)) == 2
    assert set(published) == set(retained)
    # Both publications were confirmed after the cut that creation took, so both are admitted.
    assert all(store.cut.admits(row) for row in store.published())


def _case_test_a_restart_keeps_the_cut_and_the_pending_intents(tmp_path) -> None:
    published_notice = _pending(f"task-landed:{LEAF_KEY}:commit-aaa")
    interrupted = _pending(f"task-landed:{LEAF_KEY}:commit-bbb", at="2026-10-22T11:59:00+00:00")
    begin_terminal_notice(tmp_path, published_notice)
    confirm_terminal_notice(tmp_path, published_notice.occurrenceId)
    begin_terminal_notice(tmp_path, interrupted)
    before = notice_store_path(tmp_path).read_text(encoding="utf-8")
    created = read_notice_store(tmp_path)
    assert created is not None and created.cut is not None

    restarted = read_notice_store(tmp_path)
    assert restarted is not None
    assert restarted.cut == created.cut  # a restart re-reads the same cut, never a new one
    assert [row.occurrenceId for row in restarted.published()] == [published_notice.occurrenceId]
    assert [row.occurrenceId for row in restarted.pending] == [interrupted.occurrenceId]

    later = _pending(f"task-landed:{LEAF_KEY}:commit-ccc", at="2026-10-22T12:10:00+00:00")
    begin_terminal_notice(tmp_path, later)
    confirm_terminal_notice(tmp_path, later.occurrenceId)

    after = read_notice_store(tmp_path)
    assert after is not None
    assert after.cut == restarted.cut  # never retaken on a restart
    assert [row.occurrenceId for row in after.published()] == [
        published_notice.occurrenceId,
        later.occurrenceId,
    ]
    assert [row.occurrenceId for row in after.pending] == [interrupted.occurrenceId]
    assert (
        json.loads(notice_store_path(tmp_path).read_text(encoding="utf-8"))["cut"]
        == json.loads(before)["cut"]
    )


def _case_test_a_missing_store_gets_a_new_cut_and_replays_nothing(tmp_path) -> None:
    first = _pending(f"task-landed:{LEAF_KEY}:commit-first", at="2026-10-22T11:00:00+00:00")
    begin_terminal_notice(tmp_path, first)
    confirm_terminal_notice(tmp_path, first.occurrenceId)
    gone = read_notice_store(tmp_path)
    assert gone is not None and gone.cut is not None
    notice_store_path(tmp_path).unlink()

    second = _pending(f"task-landed:{LEAF_KEY}:commit-second", at="2026-10-22T12:40:00+00:00")
    begin_terminal_notice(tmp_path, second)
    confirm_terminal_notice(tmp_path, second.occurrenceId)

    store = read_notice_store(tmp_path)
    assert store is not None
    assert store.cut is not None
    assert store.cut.sequence == 1
    assert _publication_order(store.cut.at) > _publication_order(gone.cut.at)  # a new cut
    assert [row.occurrenceId for row in store.published()] == [second.occurrenceId]
    assert first.occurrenceId not in notice_store_path(tmp_path).read_text(encoding="utf-8")


# --- F13: the caps bound the exact document the writer publishes -------------------------------


def _case_test_the_file_is_exactly_the_one_serialization_the_bound_measures(tmp_path) -> None:
    begin_terminal_notice(tmp_path, _pending())
    confirm_terminal_notice(tmp_path, _pending().occurrenceId)

    raw = notice_store_path(tmp_path).read_bytes()
    store = read_notice_store(tmp_path)
    assert store is not None
    assert raw == serialize_notice_store(store).encode("utf-8")
    assert b'"schema": "ar-attention-notice/v1"' in raw


def _case_test_a_fitting_pending_store_is_not_shed(tmp_path) -> None:
    rows = [
        _multibyte_pending(index, title_chars=32, at=f"2026-10-22T11:0{index}:00+00:00")
        for index in range(4)
    ]
    assert _serialized_bytes(rows, cut_at=rows[0].at) < MAX_NOTICE_STORE_BYTES

    for row in rows:
        begin_terminal_notice(tmp_path, row)

    store = read_notice_store(tmp_path)
    assert store is not None
    assert [row.occurrenceId for row in store.pending] == [row.occurrenceId for row in rows]
    assert store.notices == []


def _case_test_a_pending_only_store_of_multibyte_rows_stays_inside_the_byte_cap(tmp_path) -> None:
    rows = [_multibyte_pending(index) for index in range(MAX_PENDING_NOTICES)]
    # The input really is oversized for the declared bound.
    assert _serialized_bytes(rows, cut_at=rows[0].at) > MAX_NOTICE_STORE_BYTES

    for row in rows:
        begin_terminal_notice(tmp_path, row)

    raw = notice_store_path(tmp_path).read_bytes()
    store = read_notice_store(tmp_path)
    assert store is not None
    assert len(raw) <= MAX_NOTICE_STORE_BYTES
    assert raw == serialize_notice_store(store).encode("utf-8")
    assert store.notices == []
    assert 0 < len(store.pending) < MAX_PENDING_NOTICES
    kept = [row.occurrenceId for row in store.pending]
    assert _kept_is_a_suffix([row.occurrenceId for row in rows], kept)
    assert kept[-1] == rows[-1].occurrenceId  # the newest intent survives
    assert rows[0].occurrenceId not in kept  # the oldest is what went


def _case_test_the_byte_cap_sheds_the_oldest_confirmed_rows_before_newer_intents(
    tmp_path, monkeypatch
) -> None:
    confirmed = [
        _pending(f"task-landed:{LEAF_KEY}:old-{index:03d}") for index in range(MAX_TERMINAL_NOTICES)
    ]
    # A confirmed row takes its `at` from the publication position read inside the confirming
    # write, so the fixture pins that clock: without it the rows would carry the wall clock and
    # the premise below ("every confirmed row is older than every intent") would hold only until
    # the machine's own time overtook 12:00Z, which is how this case came to fail at midday.
    ticks = itertools.count()
    monkeypatch.setattr(
        attention_notices,
        "occurrence_at",
        lambda now=None: f"2026-10-22T11:00:{next(ticks):02d}+00:00",
    )
    for row in confirmed:
        begin_terminal_notice(tmp_path, row)
        confirm_terminal_notice(tmp_path, row.occurrenceId)
    monkeypatch.undo()
    intents = [
        _multibyte_pending(index, at=f"2026-10-22T12:{index:02d}:30+00:00")
        for index in range(MAX_PENDING_NOTICES)
    ]
    for row in intents:
        begin_terminal_notice(tmp_path, row)

    raw = notice_store_path(tmp_path).read_bytes()
    store = read_notice_store(tmp_path)
    assert store is not None
    assert len(raw) <= MAX_NOTICE_STORE_BYTES
    assert raw == serialize_notice_store(store).encode("utf-8")
    # Every confirmed row is older than every intent, so this is the order shedding must follow.
    order = [row.occurrenceId for row in confirmed] + [row.occurrenceId for row in intents]
    kept = [row.occurrenceId for row in store.notices] + [row.occurrenceId for row in store.pending]
    assert kept
    assert _kept_is_a_suffix(order, kept)
    assert store.pending[-1].occurrenceId == intents[-1].occurrenceId


def _case_test_a_store_at_exactly_the_byte_cap_is_kept_and_one_more_byte_sheds_the_oldest(
    tmp_path,
) -> None:
    rows = _rows_near_the_cap()

    for row in rows:
        begin_terminal_notice(tmp_path, row)

    raw = notice_store_path(tmp_path).read_bytes()
    assert MAX_NOTICE_STORE_BYTES - 4096 < len(raw) <= MAX_NOTICE_STORE_BYTES - 32

    # The bound measures the document the writer publishes, so fill to the cap by measuring it:
    # one probe carrying the "root" key, then the exact pad.
    begin_terminal_notice(tmp_path, rows[-1].model_copy(update={"root": "x"}))
    probed = len(notice_store_path(tmp_path).read_bytes())
    pad = MAX_NOTICE_STORE_BYTES - probed + 1
    assert 1 <= pad <= 4096
    exact = rows[-1].model_copy(update={"root": "x" * pad})
    begin_terminal_notice(tmp_path, exact)

    raw = notice_store_path(tmp_path).read_bytes()
    store = read_notice_store(tmp_path)
    assert store is not None
    assert len(raw) == MAX_NOTICE_STORE_BYTES
    assert [row.occurrenceId for row in store.pending] == [row.occurrenceId for row in rows]

    assert exact.root is not None
    grown = exact.model_copy(update={"root": exact.root + "x"})  # one byte more
    begin_terminal_notice(tmp_path, grown)

    raw = notice_store_path(tmp_path).read_bytes()
    store = read_notice_store(tmp_path)
    assert store is not None
    assert len(raw) <= MAX_NOTICE_STORE_BYTES
    assert raw == serialize_notice_store(store).encode("utf-8")
    kept = [row.occurrenceId for row in store.pending]
    assert kept == [row.occurrenceId for row in rows[1:]]
    assert rows[0].occurrenceId not in kept


def _case_test_a_shed_intent_never_becomes_a_notice(tmp_path) -> None:
    rows = [_multibyte_pending(index) for index in range(MAX_PENDING_NOTICES)]
    for row in rows:
        begin_terminal_notice(tmp_path, row)
    store = read_notice_store(tmp_path)
    assert store is not None
    gone = rows[0].occurrenceId
    assert gone not in [row.occurrenceId for row in store.pending]

    confirm_terminal_notice(tmp_path, gone)
    settled = settle_terminal_notices(tmp_path, lambda row: row.occurrenceId == gone)

    assert settled is not None
    assert gone not in [row.occurrenceId for row in settled.notices]
    assert settled.published() == []


# --- F07 store half: unreadable is an observation, never emptiness -----------------------------


def _case_test_missing_empty_and_present_are_distinct_observations(tmp_path) -> None:
    missing = read_notice_store_state(tmp_path)
    assert missing.state == "missing"
    assert missing.store is not None
    assert missing.store.published() == []
    assert not notice_store_path(tmp_path).exists()

    notice_store_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    empty_store = AttentionNoticeStore(cut=StoreCut(at="2026-10-22T11:00:00+00:00", sequence=1))
    notice_store_path(tmp_path).write_text(serialize_notice_store(empty_store), encoding="utf-8")
    empty = read_notice_store_state(tmp_path)
    assert empty.state == "empty"
    assert empty.store is not None
    assert empty.store.published() == []

    begin_terminal_notice(tmp_path, _pending())
    present = read_notice_store_state(tmp_path)
    assert present.state == "present"
    assert present.store is not None
    assert [row.occurrenceId for row in present.store.pending] == [_pending().occurrenceId]


def _case_test_an_unreadable_store_is_never_settled_as_empty(tmp_path) -> None:
    notice = _pending()
    begin_terminal_notice(tmp_path, notice)
    confirm_terminal_notice(tmp_path, notice.occurrenceId)
    interrupted = _pending(f"task-landed:{LEAF_KEY}:interrupted", at="2026-10-22T11:59:00+00:00")
    begin_terminal_notice(tmp_path, interrupted)
    good = notice_store_path(tmp_path).read_bytes()
    assert len(good) > 40
    torn = good[: len(good) // 2]
    notice_store_path(tmp_path).write_bytes(torn)

    observed = read_notice_store_state(tmp_path)
    assert observed.state == "unreadable"
    assert observed.store is None
    assert read_notice_store(tmp_path) is None
    assert settle_terminal_notices(tmp_path, lambda _row: True) is None

    # No write has anything to do, so none of them touches the bytes.
    confirm_terminal_notice(tmp_path, f"task-landed:{LEAF_KEY}:absent")
    discard_terminal_notice(tmp_path, interrupted.occurrenceId)
    assert settle_terminal_notices(tmp_path, lambda _row: False) is None

    assert notice_store_path(tmp_path).read_bytes() == torn
    assert read_notice_store_state(tmp_path).state == "unreadable"
    assert list(notice_store_path(tmp_path).parent.glob("attention-notices.torn.*.json")) == []


def _case_test_an_unreadable_store_is_quarantined_and_created_again_without_replay(
    tmp_path,
) -> None:
    first = _pending(f"task-landed:{LEAF_KEY}:commit-aaa", at="2026-10-22T11:00:00+00:00")
    begin_terminal_notice(tmp_path, first)
    confirm_terminal_notice(tmp_path, first.occurrenceId)
    interrupted = _pending(f"task-landed:{LEAF_KEY}:interrupted", at="2026-10-22T11:30:00+00:00")
    begin_terminal_notice(tmp_path, interrupted)
    before = read_notice_store(tmp_path)
    assert before is not None and before.cut is not None
    good = notice_store_path(tmp_path).read_bytes()
    torn = good[: len(good) // 2]
    notice_store_path(tmp_path).write_bytes(torn)

    later = _pending(f"task-landed:{LEAF_KEY}:commit-later", at="2026-10-22T12:50:00+00:00")
    begin_terminal_notice(tmp_path, later)

    quarantined = list(notice_store_path(tmp_path).parent.glob("attention-notices.torn.*.json"))
    assert len(quarantined) == 1
    assert quarantined[0].read_bytes() == torn  # preserved, never overwritten in place

    store = read_notice_store(tmp_path)
    assert store is not None
    assert store.cut is not None
    # A store that had to be created again takes a new cut; the old one is not reused.
    assert _publication_order(store.cut.at) > _publication_order(before.cut.at)
    assert [row.occurrenceId for row in store.pending] == [later.occurrenceId]
    assert store.published() == []  # no replay of what the torn bytes held
    assert first.occurrenceId not in notice_store_path(tmp_path).read_text(encoding="utf-8")


def _case_test_an_acknowledged_notice_survives_a_torn_store_and_a_later_occurrence_arrives(
    tmp_path,
) -> None:
    root = tmp_path / "coordination"
    dismissals = AttentionDismissalStore(tmp_path / "observer")
    first = _pending()
    begin_terminal_notice(root, first)
    confirm_terminal_notice(root, first.occurrenceId)
    dismissals.dismiss(
        AttentionDismissalRecord(
            itemId=first.occurrenceId,
            kind="task-landed",
            dismissedAt=NOW.isoformat(),
        )
    )

    def refresh() -> dict[str, AttentionDismissalRecord]:
        """One projection refresh: settle, then let the acknowledgement fold run only if whole."""

        settled = settle_terminal_notices(root, lambda _row: True)
        read = settled if settled is not None else read_notice_store(root)
        if read is None:  # the notice lane is incomplete: it ends no acknowledgement
            return dismissals.current()
        observations = {
            kind: AttentionObservation(kind, NOW) for kind in ("task-landed", "task-abandoned")
        }
        for kind, observation in observations.items():
            observe_terminal_notices(
                tuple(notice for notice in read.published() if notice.kind == kind),
                {kind: observation},
            )
        return dismissals.reconcile(list(observations.values()))

    kept = refresh()
    assert first.occurrenceId in kept
    assert _queue_with(root, kept) == []  # acknowledged while its occurrence is retained

    good = notice_store_path(root).read_bytes()
    notice_store_path(root).write_bytes(good[:-9])
    kept = refresh()
    assert first.occurrenceId in kept  # unreadable is not complete emptiness
    assert notice_store_path(root).read_bytes() == good[:-9]

    notice_store_path(root).write_bytes(good)  # restored, unchanged
    kept = refresh()
    assert first.occurrenceId in kept
    assert _queue_with(root, kept) == []

    second = _pending(f"task-landed:{LEAF_KEY}:commit-bbb", at="2026-10-22T12:20:00+00:00")
    begin_terminal_notice(root, second)
    confirm_terminal_notice(root, second.occurrenceId)
    kept = refresh()
    assert _queue_with(root, kept) == [second.occurrenceId]


# --- native operation identity and the store written before it existed -------------------------


def _case_test_a_pending_row_persists_the_native_operation_identity(tmp_path) -> None:
    notice = _pending().model_copy(
        update={
            "commit": "abc123",
            "operationKey": "op-7",
            "operationGeneration": 3,
            "repository": "repo",
            "root": "/worktrees/leaf",
        }
    )
    begin_terminal_notice(tmp_path, notice)
    confirm_terminal_notice(tmp_path, notice.occurrenceId)

    store = read_notice_store(tmp_path)
    assert store is not None
    confirmed = store.notices[0]
    assert (confirmed.commit, confirmed.operationKey) == ("abc123", "op-7")
    assert confirmed.operationGeneration == 3
    assert (confirmed.repository, confirmed.root) == ("repo", "/worktrees/leaf")
    raw = json.loads(notice_store_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["notices"][0]["operationGeneration"] == 3


def _case_test_a_store_written_before_the_cut_still_reads_and_adopts_one_cut(tmp_path) -> None:
    legacy = {
        "schema": "ar-attention-notice/v1",
        "sequence": 1,
        "baseline": 0,
        "notices": [
            {
                "occurrenceId": f"task-landed:{LEAF_KEY}:commit-000",
                "kind": "task-landed",
                "taskKey": LEAF_KEY,
                "taskReference": {"repository": "repo", "path": "master/leaf.json"},
                "taskTitle": "Leaf",
                "detail": "landed commit-000",
                "at": "2026-10-22T11:00:00+00:00",
                "sequence": 1,
            }
        ],
        "pending": [
            {
                "occurrenceId": f"task-landed:{LEAF_KEY}:interrupted",
                "kind": "task-landed",
                "taskKey": LEAF_KEY,
                "taskReference": {"repository": "repo", "path": "master/leaf.json"},
                "taskTitle": "Leaf",
                "at": "2026-10-22T11:30:00+00:00",
            }
        ],
    }
    notice_store_path(tmp_path).parent.mkdir(parents=True, exist_ok=True)
    notice_store_path(tmp_path).write_text(json.dumps(legacy, indent=2) + "\n", encoding="utf-8")

    observed = read_notice_store_state(tmp_path)
    assert observed.state == "present"
    assert observed.store is not None
    row = observed.store.pending[0]
    assert (row.commit, row.operationKey, row.operationGeneration) == (None, None, None)
    assert (row.repository, row.root) == (None, None)
    assert [notice.sequence for notice in observed.store.published()] == [1]
    assert not hasattr(observed.store, "baseline")
    # Adoption keeps publishing exactly what the previous build published: it excludes nothing.
    assert observed.store.cut == StoreCut(at="2026-10-22T11:00:00+00:00", sequence=1)

    undecided = settle_terminal_notices(
        tmp_path, lambda notice: None if notice.commit is None else True
    )
    assert undecided is not None
    assert [notice.occurrenceId for notice in undecided.pending] == [row.occurrenceId]
    assert notice_store_path(tmp_path).read_text(encoding="utf-8").count('"baseline"') == 1

    begin_terminal_notice(tmp_path, _pending(f"task-landed:{LEAF_KEY}:new"))
    rewritten = notice_store_path(tmp_path).read_text(encoding="utf-8")
    assert '"baseline"' not in rewritten
    after = read_notice_store(tmp_path)
    assert after is not None
    assert after.cut == observed.store.cut  # adopted once, then preserved
    assert [notice.sequence for notice in after.published()] == [1]


# Consolidated cases: one group test per requirement, each case a subtest that keeps
# its own failure message. The case COUNT is what the suite budget bounds; the protection
# is every assertion below, unmoved.


def test_rule_5_one_notice_per_native_occurrence(subtests, tmp_path, monkeypatch) -> None:
    """Rule 5: a notice is published once per native occurrence and never on a guess."""

    for message, run in (
        (
            "begin then confirm publishes exactly one notice",
            lambda **fixtures: _case_test_begin_then_confirm_publishes_exactly_one_notice(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "confirming the same occurrence again adds nothing",
            lambda **fixtures: _case_test_confirming_the_same_occurrence_again_adds_nothing(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "begin without confirm announces nothing",
            lambda **fixtures: _case_test_begin_without_confirm_announces_nothing(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "settle proves and publishes the intent once",
            lambda **fixtures: _case_test_settle_proves_and_publishes_the_intent_once(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "settle refutes and drops the intent",
            lambda **fixtures: _case_test_settle_refutes_and_drops_the_intent(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "settle keeps an undecidable intent for a later pass",
            lambda **fixtures: _case_test_settle_keeps_an_undecidable_intent_for_a_later_pass(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "an empty store publishes nothing and notices survive a reread",
            lambda **fixtures: (
                _case_test_an_empty_store_publishes_nothing_and_notices_survive_a_reread(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "only the terminal kinds can be acknowledged",
            lambda **fixtures: _case_test_every_published_kind_can_be_acknowledged(
                **_take(
                    fixtures,
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            # Each case gets its own root: pytest hands one tmp_path per group test,
            # and a store written by one case must never be read by the next.
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_rule_6_the_creation_cut_decides_what_this_store_publishes(
    subtests, tmp_path, monkeypatch
) -> None:
    """Rule 6: the store's one creation cut decides which publications it owns."""

    for message, run in (
        (
            "a store created over existing terminal records publishes none of them",
            lambda **fixtures: (
                _case_test_a_store_created_over_existing_terminal_records_publishes_none_of_them(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "the cut excludes a retained notice that sits before it",
            lambda **fixtures: _case_test_the_cut_excludes_a_retained_notice_that_sits_before_it(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a publication confirmed after the cut is admitted even when its intent predates it",
            lambda **fixtures: (
                _case_test_a_publication_confirmed_after_the_cut_is_admitted_even_when_its_intent_predates_it(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a notice confirmed before the store existed cannot be invented later",
            lambda **fixtures: (
                _case_test_a_notice_confirmed_before_the_store_existed_cannot_be_invented_later(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a record published while the store is created is assigned exactly once",
            lambda **fixtures: (
                _case_test_a_record_published_while_the_store_is_created_is_assigned_exactly_once(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a restart keeps the cut and the pending intents",
            lambda **fixtures: _case_test_a_restart_keeps_the_cut_and_the_pending_intents(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a missing store gets a new cut and replays nothing",
            lambda **fixtures: _case_test_a_missing_store_gets_a_new_cut_and_replays_nothing(
                **_take(fixtures, "tmp_path")
            ),
        ),
    ):
        with subtests.test(msg=message):
            # Each case gets its own root: pytest hands one tmp_path per group test,
            # and a store written by one case must never be read by the next.
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_rule_6_whole_store_byte_and_count_bounds(subtests, tmp_path, monkeypatch) -> None:
    """Rule 6: both count bounds and the whole-file byte bound shed the oldest rows only."""

    for message, run in (
        (
            "the file is exactly the one serialization the bound measures",
            lambda **fixtures: (
                _case_test_the_file_is_exactly_the_one_serialization_the_bound_measures(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a fitting pending store is not shed",
            lambda **fixtures: _case_test_a_fitting_pending_store_is_not_shed(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a pending only store of multibyte rows stays inside the byte cap",
            lambda **fixtures: (
                _case_test_a_pending_only_store_of_multibyte_rows_stays_inside_the_byte_cap(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "the byte cap sheds the oldest confirmed rows before newer intents",
            lambda **fixtures: (
                _case_test_the_byte_cap_sheds_the_oldest_confirmed_rows_before_newer_intents(
                    **_take(fixtures, "tmp_path", "monkeypatch")
                )
            ),
        ),
        (
            "a store at exactly the byte cap is kept and one more byte sheds the oldest",
            lambda **fixtures: (
                _case_test_a_store_at_exactly_the_byte_cap_is_kept_and_one_more_byte_sheds_the_oldest(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "a shed intent never becomes a notice",
            lambda **fixtures: _case_test_a_shed_intent_never_becomes_a_notice(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "the store keeps at most the newest bounded occurrences",
            lambda **fixtures: _case_test_the_store_keeps_at_most_the_newest_bounded_occurrences(
                **_take(fixtures, "tmp_path")
            ),
        ),
    ):
        with subtests.test(msg=message):
            # Each case gets its own root: pytest hands one tmp_path per group test,
            # and a store written by one case must never be read by the next.
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_a_torn_store_is_unknown_never_empty(subtests, tmp_path, monkeypatch) -> None:
    """F07: an unreadable store resolves nothing, keeps its acknowledgements and is preserved."""

    for message, run in (
        (
            "missing empty and present are distinct observations",
            lambda **fixtures: _case_test_missing_empty_and_present_are_distinct_observations(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "an unreadable store is never settled as empty",
            lambda **fixtures: _case_test_an_unreadable_store_is_never_settled_as_empty(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "an unreadable store is quarantined and created again without replay",
            lambda **fixtures: (
                _case_test_an_unreadable_store_is_quarantined_and_created_again_without_replay(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
        (
            "an acknowledged notice survives a torn store and a later occurrence arrives",
            lambda **fixtures: (
                _case_test_an_acknowledged_notice_survives_a_torn_store_and_a_later_occurrence_arrives(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            # Each case gets its own root: pytest hands one tmp_path per group test,
            # and a store written by one case must never be read by the next.
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def test_rule_6_native_identity_and_legacy_adoption(subtests, tmp_path, monkeypatch) -> None:
    """Rule 6: an intent carries the native operation identity, and a legacy store adopts one cut."""

    for message, run in (
        (
            "a pending row persists the native operation identity",
            lambda **fixtures: _case_test_a_pending_row_persists_the_native_operation_identity(
                **_take(fixtures, "tmp_path")
            ),
        ),
        (
            "a store written before the cut still reads and adopts one cut",
            lambda **fixtures: (
                _case_test_a_store_written_before_the_cut_still_reads_and_adopts_one_cut(
                    **_take(fixtures, "tmp_path")
                )
            ),
        ),
    ):
        with subtests.test(msg=message):
            # Each case gets its own root: pytest hands one tmp_path per group test,
            # and a store written by one case must never be read by the next.
            root = tmp_path / _slug(message)
            root.mkdir()
            run(tmp_path=root, monkeypatch=monkeypatch)


def _slug(message: str) -> str:
    """A directory name for one case, so no two subtests share a coordination root."""

    return "".join(char if char.isalnum() else "-" for char in message)[:48]


def _take(fixtures: dict, *names: str) -> dict:
    """The fixtures one case declares, so a group can supply its whole set."""

    return {name: fixtures[name] for name in names}
