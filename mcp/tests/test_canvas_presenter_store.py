"""MIK-R105: the presenter's durable attempt store at its own boundary.

The store is one page's records: their guarded paths, the request ledger, chronological listing,
request and session lookup, bounded reads and locked transitions. The orchestration cases (start,
replacement, publication, readiness) stay in :mod:`test_canvas_presenter`.

1. a record survives a new reader, with its conversation and publication state;
2. attempts are listed chronologically, lookups read every retained record, and retention reclaims
   the oldest settled attempt with its working folder while the request ledger still reconciles it;
3. every transition is a locked fresh read-modify-write, so concurrent chat, activity and a
   cancellation lose no state;
4. an oversized record is refused on its size before its bytes are read.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from itertools import count
from pathlib import Path
from typing import Literal

import pytest
from agents_remember.application.review_frame_store import frame_scope
from agents_remember.canvas import presenter_files
from agents_remember.canvas.presenter import (
    MAX_ATTEMPTS,
    FrameBinding,
    Presenter,
    PresenterAttempt,
    PresenterRefused,
)
from agents_remember.canvas.presenter_store import AttemptStore
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS
from canvas_host_test_support import (
    AT,
    FRAME,
    LEAF,
    REPO,
    TASK,
    FakeRunner,
    config,
    packet,
    record,
    swap_on_scandir,
    task,
    watch_opens,
    write_settings,
)

LAUNCH = {"harness": "hermes", "model": "the-presenter-model", "effort": "high", "mode": "auto"}
# The repaired bounds the before/after runs share.
RETAINED_ATTEMPTS = 32
RECORD_BYTES_BOUND = 256 * 1024


def _presenter(settings, root: Path, *, runner=None) -> Presenter:
    return Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=runner or FakeRunner(),
        clock=lambda: AT,
    )


def _start(presenter: Presenter, comparison=None, *, request: str = "req-start"):
    comparison = comparison or record()
    return presenter.start(
        caller="dashboard",
        request_id=request,
        activation="explicit",
        evidence=packet(comparison),
        focus="the change to src/app.py",
    )


# --- chronological listing, independent lookup, effective retention ---------------------


def _clocked_presenter(settings, root: Path, runner=None) -> Presenter:
    """A presenter whose clock advances, so creation order is observable."""

    base = datetime(2026, 10, 18, 9, 0, 0, tzinfo=UTC)
    ticks = count()

    def clock() -> str:
        return (base + timedelta(seconds=next(ticks))).isoformat().replace("+00:00", "Z")

    return Presenter(
        settings,
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        runner=runner or FakeRunner(),
        clock=clock,
    )


def _records_dir(root: Path) -> Path:
    return root / "notes" / "reports" / "review-presenter" / LEAF / FRAME


def _record_files(root: Path) -> list[Path]:
    return [
        path
        for path in _records_dir(root).glob("*.json")
        if not path.name.endswith(".evidence.json") and path.name != "requests.json"
    ]


def _start_many(presenter: Presenter, population: int) -> tuple[dict[str, str], dict[str, Path]]:
    """Create ``population`` settled attempts with distinct, increasing creation times."""

    ids: dict[str, str] = {}
    folders: dict[str, Path] = {}
    for index in range(population):
        request = f"req-{index:02d}"
        attempt = _start(presenter, request=request)
        ids[request] = attempt.attempt_id
        folders[request] = Path(attempt.working_directory)
        presenter.cancel(attempt.attempt_id, reason="settled for the population test")
    return ids, folders


def test_the_attempt_record_is_durable_on_disk(tmp_path: Path) -> None:
    """Rule 7: the record survives a new reader, with its conversation and publication state."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    attempt = _start(_presenter(settings, root), request="req-durable")
    record_path = (
        root
        / "notes"
        / "reports"
        / "review-presenter"
        / LEAF
        / FRAME
        / f"{attempt.attempt_id}.json"
    )
    assert record_path.is_file()
    payload = json.loads(record_path.read_text())
    assert payload["capability"] == "review-presenter"
    assert payload["comparison"]["comparison_reference"] == attempt.comparison.comparison_reference
    assert payload["design_source"] == attempt.design_source
    assert payload["design_sha256"] == attempt.design_sha256


def test_attempt_listing_is_chronological_and_lookups_ignore_the_shown_window(
    tmp_path: Path,
) -> None:
    """Rules 7, 17: 24 records, a 20-record view, hash filenames, and replay outside the view."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    runner = FakeRunner()
    presenter = _clocked_presenter(settings, root, runner)

    ids, _folders = _start_many(presenter, 24)

    displayed = [attempt.attempt_id for attempt in presenter.attempts()]
    assert len(displayed) == MAX_ATTEMPTS
    files = _record_files(root)
    assert len(files) == 24
    hash_order = [
        json.loads(path.read_text())["attempt_id"]
        for path in sorted(files, reverse=True)[:MAX_ATTEMPTS]
    ]
    assert set(hash_order) != set(displayed)
    latest = presenter.current()
    assert latest is not None
    assert latest.attempt_id == ids["req-23"]
    assert ids["req-00"] not in displayed

    calls = len(runner.calls)
    again = _start(presenter, request="req-00")
    assert again.attempt_id == ids["req-00"]
    assert len(runner.calls) == calls

    live = _start(presenter, request="req-live")
    current_live = presenter.live()
    assert current_live is not None
    assert current_live.attempt_id == live.attempt_id
    latest = presenter.current()
    assert latest is not None
    assert latest.attempt_id == live.attempt_id
    assert live.attempt_id in {attempt.attempt_id for attempt in presenter.attempts()}


def test_retention_reclaims_to_its_ceiling_and_keeps_request_reconciliation(
    tmp_path: Path,
) -> None:
    """Rules 7, 17: 48 records are capped, the oldest working folders go, replay still reconciles."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    runner = FakeRunner()
    presenter = _clocked_presenter(settings, root, runner)

    ids, folders = _start_many(presenter, 48)

    assert len(_record_files(root)) == RETAINED_ATTEMPTS
    latest = presenter.current()
    assert latest is not None
    assert latest.attempt_id == ids["req-47"]
    assert presenter.read(ids["req-00"]) is None
    assert not folders["req-00"].exists()
    assert folders["req-20"].is_dir()
    displayed = {attempt.attempt_id for attempt in presenter.attempts()}
    assert ids["req-20"] not in displayed

    calls = len(runner.calls)
    again = _start(presenter, request="req-20")
    assert again.attempt_id == ids["req-20"]
    assert len(runner.calls) == calls

    live = _start(presenter, request="req-live-at-cap")
    current_live = presenter.live()
    assert current_live is not None
    assert current_live.attempt_id == live.attempt_id
    latest = presenter.current()
    assert latest is not None
    assert latest.attempt_id == live.attempt_id

    # A request whose record the retention ceiling reclaimed is reconciled to that attempt by its
    # identity ledger and refused by name, so a late retry starts no duplicate session (B20).
    before = len(_record_files(root))
    callers = len(runner.calls)
    with pytest.raises(PresenterRefused) as reclaimed:
        _start(presenter, request="req-00")
    assert reclaimed.value.reason == "attempt-record-reclaimed"
    assert ids["req-00"] in reclaimed.value.detail or "req-00" in reclaimed.value.detail
    assert len(_record_files(root)) == before
    assert len(runner.calls) == callers


# --- every transition is a locked fresh read-modify-write -------------------------------


def test_concurrent_chat_activity_and_cancellation_lose_no_state(tmp_path: Path) -> None:
    """Rules 7, 9: concurrent appends and a cancellation each survive the other."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-concurrent")
    barrier = threading.Barrier(3)

    def talk() -> None:
        barrier.wait()
        for index in range(20):
            presenter.say(attempt.attempt_id, role="developer", text=f"question {index}")

    def note() -> None:
        barrier.wait()
        for index in range(20):
            presenter.note_activity(
                attempt.attempt_id, kind="inspection", text=f"read object {index}"
            )

    def stop() -> None:
        barrier.wait()
        presenter.cancel(attempt.attempt_id, reason="stopped concurrently")

    threads = [threading.Thread(target=target) for target in (talk, note, stop)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(HANG_GUARD_SECONDS)
    assert all(not thread.is_alive() for thread in threads)

    final = presenter.read(attempt.attempt_id)
    assert final is not None
    assert final.state == "cancelled"
    assert len(final.conversation) == 20
    assert len(final.activity) == 20


# --- a record beyond its metadata bound is refused before it is read --------------------


def test_an_oversized_attempt_record_is_refused_before_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Rule 5: metadata is sized before it is parsed, so a huge record costs nothing."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-huge-record")
    record_path = presenter._record_path(attempt.attempt_id)
    record_path.write_bytes(b"{" + b" " * (RECORD_BYTES_BOUND + 1))

    opened = watch_opens(monkeypatch)
    assert presenter.read(attempt.attempt_id) is None
    assert record_path not in opened


# --- a live record cannot outgrow its own ceiling, and a swapped folder is refused --------


def test_a_live_record_grows_no_further_than_its_own_byte_ceiling(tmp_path: Path) -> None:
    """L104-B20: legal activity settles its oldest turns instead of outgrowing the record.

    The pre-repair behaviour was the finding: a still-running record grew past
    :data:`RECORD_BYTES_BOUND`, every reader then refused it as ``record-too-large``, and it vanished
    from ``read``, ``current`` and ``by_request`` while it was still the live attempt.
    """

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-growth")
    filler = "x" * 4000
    for index in range(80):
        presenter.say(attempt.attempt_id, role="developer", text=f"{index:04d} {filler}")

    path = presenter._record_path(attempt.attempt_id)
    assert path.stat().st_size <= RECORD_BYTES_BOUND
    kept = presenter.read(attempt.attempt_id)
    assert kept is not None
    assert kept.state in {"started", "running"}
    # Reconciliation is independent of what had to be settled out of the record.
    shown = presenter.current()
    assert shown is not None
    assert shown.attempt_id == attempt.attempt_id
    assert presenter.by_request("req-growth") is not None
    # The newest turn survives; the oldest ones are the ones settled away.
    assert len(kept.conversation) < 80
    assert "0079" in kept.conversation[-1].text


def test_a_swapped_record_directory_is_refused_before_it_is_enumerated(tmp_path: Path) -> None:
    """L104-B22: a store attached before the swap must not list the foreign folder even once."""

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="req-swap")

    directory = _records_dir(root)
    moved = tmp_path / "moved-away"
    directory.rename(moved)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    # A record that *would* validate if the foreign folder were enumerated: the swap must be
    # refused before the scan, not by the decoy failing to parse.
    original = json.loads((moved / f"{attempt.attempt_id}.json").read_text())
    original["attempt_id"] = "decoy-attempt"
    (decoy / "decoy-attempt.json").write_text(json.dumps(original))
    directory.symlink_to(decoy, target_is_directory=True)

    assert presenter.attempts() == ()
    assert presenter.read(attempt.attempt_id) is None
    assert presenter.by_request("req-swap") is None


# --- the store at its own boundary: constructed directly, driven without the facade ---------


def _direct_store(settings, root: Path) -> AttemptStore:
    """One store built from its own inputs, with no Presenter in the path."""

    scope = frame_scope(root, LEAF, FRAME)
    directory = scope.guard(_records_dir(root), label="presenter directory")
    return AttemptStore(settings, scope=scope, directory=directory, clock=lambda: AT)


def _attempt_model(
    number: int,
    *,
    request: str,
    state: Literal["started", "running", "published", "failed", "cancelled"] = "started",
    created: str = "2026-10-18T09:00:00Z",
    session: str | None = None,
) -> PresenterAttempt:
    """One valid attempt record, built without a facade start."""

    binding = FrameBinding.from_packet(packet(record()).comparison)
    return PresenterAttempt(
        attempt_id=f"attempt-{number:03d}",
        request_id=request,
        caller="dashboard",
        repository_id=REPO,
        task=TASK,
        leaf_id=LEAF,
        frame_id=FRAME,
        comparison=binding,
        evidence_digest="0" * 64,
        focus="the change to src/app.py",
        state=state,
        created_at=created,
        updated_at=created,
        working_directory=f"/nonexistent/work/attempt-{number:03d}",
        session_id=session,
        design_source="default",
        design_sha256="1" * 64,
        design_words=3,
        instructions_sha256="2" * 64,
    )


def test_the_store_writes_reads_and_lists_at_its_own_boundary(tmp_path: Path) -> None:
    """L104-A3 extraction: AttemptStore is usable and tested without Presenter.start."""

    settings = config(tmp_path)
    store = _direct_store(settings, task(settings))
    first = _attempt_model(1, request="req-one", created="2026-10-18T09:00:00Z")
    second = _attempt_model(2, request="req-two", created="2026-10-18T09:30:00Z", session="sess-2")

    store.write(first)
    store.write(second)

    assert store.read("attempt-001") == first
    assert [item.attempt_id for item in store.records()] == ["attempt-002", "attempt-001"]
    assert store.by_request("req-one") == first
    assert store.by_session("sess-2") == second
    assert store.current() == second


def test_the_store_refuses_one_identity_that_is_not_one_path_segment(tmp_path: Path) -> None:
    """The store's own admission: an attempt id is one segment or it is refused by name."""

    settings = config(tmp_path)
    store = _direct_store(settings, task(settings))
    with pytest.raises(PresenterRefused) as refused:
        store.record_path("../escape")
    assert refused.value.reason == "unknown-attempt"


def test_the_store_prunes_to_its_ceiling_and_keeps_a_live_record(tmp_path: Path) -> None:
    """Bounded retention at the boundary: the settled tail goes, the live attempt stays."""

    settings = config(tmp_path)
    store = _direct_store(settings, task(settings))
    for number in range(RETAINED_ATTEMPTS + 8):
        store.write(
            _attempt_model(
                number,
                request=f"req-{number:02d}",
                state="cancelled",
                created=f"2026-10-18T09:{number:02d}:00Z",
            )
        )
    live = _attempt_model(
        RETAINED_ATTEMPTS + 8,
        request="req-live",
        created="2026-10-18T10:00:00Z",
    )
    store.write(live)

    kept = store.records()
    assert len(kept) == RETAINED_ATTEMPTS
    assert live in kept


def test_a_first_read_of_an_unsafe_directory_looks_empty_not_fatal(tmp_path: Path) -> None:
    """L104-A3 extraction: A2's read semantics survive the split.

    A page whose own directory is not admissible reads as empty -- ``None`` and ``()`` -- instead of
    raising on the first read. A *write* still refuses by name; only reading is tolerant.
    """

    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)  # nothing started: the store is not attached yet

    directory = _records_dir(root)
    directory.parent.mkdir(parents=True, exist_ok=True)
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    directory.symlink_to(foreign, target_is_directory=True)

    assert presenter.read("attempt-001") is None
    assert presenter.attempts() == ()
    assert presenter.current() is None


@pytest.mark.parametrize("population", [2, 4])
def test_listing_holds_its_directory_across_the_guard_scan_race(tmp_path, monkeypatch, population):
    settings = config(tmp_path)
    root = task(settings)
    write_settings(settings, LAUNCH)
    presenter = _presenter(settings, root)
    attempt = _start(presenter, request="guard-race")
    directory = _records_dir(root)
    decoy = tmp_path / "foreign-records"
    decoy.mkdir()
    for number in range(population):
        payload = attempt.model_copy(update={"attempt_id": f"foreign-{number}"})
        (decoy / f"foreign-{number}.json").write_text(payload.model_dump_json())
    foreign = swap_on_scandir(monkeypatch, directory, decoy)
    records = presenter.attempts()
    assert foreign == [], f"enumerated {len(foreign)} foreign entries"
    assert all(not item.attempt_id.startswith("foreign-") for item in records)


def test_output_keeps_its_parent_during_an_ancestor_swap(tmp_path, monkeypatch):
    original = presenter_files.atomic_write_bytes
    for population in (2, 4):
        root = tmp_path / str(population)
        root.mkdir()
        assets = root / "assets"
        assets.mkdir()
        decoy = root / "foreign"
        decoy.mkdir()
        for number in range(population):
            (decoy / str(number)).write_bytes(b"foreign sentinel")
        retained = root / "retained-assets"

        def swapped(path, payload, *, assets=assets, retained=retained, decoy=decoy):
            assets.rename(retained)
            assets.symlink_to(decoy, target_is_directory=True)
            original(path, payload)

        monkeypatch.setattr(presenter_files, "atomic_write_bytes", swapped)
        presenter_files.write_output(assets, "result.txt", b"admitted output")
        assert (retained / "result.txt").read_bytes() == b"admitted output"
        assert sorted(path.name for path in decoy.iterdir()) == [str(n) for n in range(population)]
        assert all(path.read_bytes() == b"foreign sentinel" for path in decoy.iterdir())
