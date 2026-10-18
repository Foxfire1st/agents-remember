"""R67/F07: legal over-capacity histories retain one build, not one build per page."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from agents_remember.application.knowledge_reader import timeline, truth
from agents_remember.kernel.canonical_json import canonical_json_bytes
from agents_remember.memory.knowledge_index import HistoryRow
from agents_remember.models.knowledge.continuation import decode_continuation
from agents_remember.models.knowledge_files.history import FamilyRow


@pytest.fixture(autouse=True)
def clean_timeline_cache() -> Iterator[None]:
    timeline.TIMELINES.clear()
    yield
    timeline.TIMELINES.clear()


@pytest.fixture
def backing_directory() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="reader-timeline-spill-") as directory:
        yield Path(directory)


@pytest.fixture
def selected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    monkeypatch.setattr(timeline, "_record_events", lambda *_: [])
    monkeypatch.setattr(timeline, "_entry_events", lambda *_: [])
    monkeypatch.setattr(timeline, "_last_change", lambda *_: (None, None))
    monkeypatch.setattr(timeline, "_ranks", lambda *_: {})
    return SimpleNamespace(
        memory_repository=tmp_path,
        revision="a" * 40,
        index=SimpleNamespace(state=SimpleNamespace(key="b" * 40)),
    )


def _history(size: int, padding: int, unicode: bool) -> tuple[HistoryRow, ...]:
    rows = []
    for n in range(size):
        reason = "x" * padding
        if unicode:
            reason = hashlib.shake_256(str(n).encode()).hexdigest(padding // 2)
            reason = reason.translate(str.maketrans("0123456789abcdef", "é🧭λЖ中語ñøßΩøçπδאあ"))
        row = FamilyRow(
            id=f"ROW-{n:06d}",
            subject="FAM-000000",
            disposition="no_impact",
            reason=reason,
            items=(),
            examined=(),
            revision=1,
        )
        doc = row.model_dump(mode="json", by_alias=True)
        rows.append(
            HistoryRow(
                doc["id"],
                "fixture",
                "leaf",
                False,
                "knowledge/history/fixture-attempt-2.json",
                doc["subject"],
                "no_impact",
                doc,
            )
        )
    return tuple(rows)


@pytest.mark.parametrize(
    "size,padding,unicode",
    [
        (100, 32, False),
        (1000, 32, False),
        (500, 18000, False),
        (1000, 18000, False),
        (500, 18000, True),
    ],
)
def test_legal_timeline_walk_materializes_and_hashes_once(
    selected: Any,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    padding: int,
    unicode: bool,
) -> None:
    rows = _history(size, padding, unicode)
    record: Any = SimpleNamespace(id="FAM-000000")
    counts = {"builds": 0, "digests": 0, "visits": 0, "freezes": 0}
    build, digest, freeze = timeline._timeline, timeline.sha256_digest, timeline.freeze_timeline

    def counted_build(*args: Any) -> dict[str, Any]:
        counts["builds"] += 1
        return build(*args)

    def counted_digest(value: Any) -> str:
        counts["digests"] += 1
        counts["visits"] += len(value["events"])
        return digest(value)

    def counted_freeze(*args: Any) -> Any:
        counts["freezes"] += 1
        return freeze(*args)

    monkeypatch.setattr(timeline, "_timeline", counted_build)
    monkeypatch.setattr(timeline, "sha256_digest", counted_digest)
    monkeypatch.setattr(timeline, "freeze_timeline", counted_freeze)
    events = []
    tokens = []
    page = timeline.record_timeline(selected, record, (), rows)
    pages = 0
    while True:
        assert page["state"] == "view" and len(page["events"]) == 50
        pages += 1
        events.extend(page["events"])
        if not page["continuation"]:
            break
        tokens.append(page["continuation"])
        page = timeline.record_timeline(selected, record, (), rows, continuation=tokens[-1])
    assert pages == size // 50
    assert [e["row"] for e in events] == [r.id for r in rows]
    assert [e["document"] for e in events] == [r.document for r in rows]
    assert all(e["attempt"] == 2 and e["path"] == rows[0].path for e in events)
    assert counts == {"builds": 1, "digests": 1, "visits": size, "freezes": 1}
    assert 0 < timeline.TIMELINES.weight <= timeline.TIMELINE_CACHE_BYTES
    actual_bytes = sum(len(canonical_json_bytes(e)) for e in events)
    if padding == 18000:
        assert actual_bytes > timeline.TIMELINE_CACHE_BYTES
    expected_digest = digest({"id": record.id, "events": events})
    for token in tokens:
        decoded = decode_continuation(token)
        assert decoded is not None
        assert decoded.continuation_format == "knowledge-continuation/v2"
        assert decoded.manifest_digest == expected_digest
    print(
        json.dumps(
            {
                "size": size,
                "padding": padding,
                "unicode": unicode,
                "pages": pages,
                "counts": counts,
                "event_bytes": actual_bytes,
                "resident_bytes": timeline.TIMELINES.weight,
            }
        )
    )


def _track_backing(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    files: list[Any] = []
    original = tempfile.TemporaryFile

    def opened(*args: Any, **kwargs: Any) -> Any:
        file = original(*args, **kwargs)
        files.append(file)
        return file

    monkeypatch.setattr(tempfile, "TemporaryFile", opened)
    return files


def _assert_removed(files: list[Any], descriptors: list[int]) -> None:
    for file, fd in zip(files, descriptors, strict=True):
        assert file.closed
        with pytest.raises(OSError) as caught:
            os.fstat(fd)
        assert caught.value.errno == errno.EBADF


@pytest.mark.parametrize("action", ["clear", "replacement"])
def test_anonymous_backing_is_actually_removed(
    selected: Any,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    files = _track_backing(monkeypatch)
    record: Any = SimpleNamespace(id="FAM-000000")
    rows = _history(500, 18000, False)
    first = timeline.record_timeline(selected, record, (), rows)
    assert len(files) == 2 and timeline.TIMELINES.spilled_bytes > timeline.TIMELINE_CACHE_BYTES
    descriptors = [file.fileno() for file in files]
    assert all(os.fstat(fd).st_nlink == 0 for fd in descriptors)
    first["events"][0]["document"]["reason"] = "caller mutation"
    again = timeline.record_timeline(selected, record, (), rows)
    assert again["events"][0]["document"] == rows[0].document and len(files) == 2
    if action == "clear":
        timeline.TIMELINES.clear()
        assert timeline.TIMELINES.spilled_bytes == timeline.TIMELINES.weight == 0
    else:
        selected.index.state.key = "c" * 40
        replaced = timeline.record_timeline(selected, record, (), rows)
        assert replaced["events"] == again["events"] and len(files) == 4
        assert all(not file.closed for file in files[2:])
        assert 0 < timeline.TIMELINES.weight <= timeline.TIMELINE_CACHE_BYTES
        assert len(timeline.TIMELINES) == 1
    _assert_removed(files[:2], descriptors)
    latest = files[2:]
    latest_descriptors = [file.fileno() for file in latest]
    timeline.TIMELINES.clear()
    _assert_removed(latest, latest_descriptors)


def test_process_exit_removes_anonymous_backing(tmp_path: Path, backing_directory: Path) -> None:
    script = tmp_path / "spill_process.py"
    script.write_text("""import json, os, sys, tempfile
from agents_remember.application.knowledge_reader import timeline
files = []
original = tempfile.TemporaryFile
def opened(*args, **kwargs):
    file = original(*args, **kwargs)
    files.append(file)
    return file
tempfile.TemporaryFile = opened
key = ("private", "revision", "tree", "record")
value = {"sources": {"history": {"state": "read"}},
         "events": [{"reason": "x" * 18000} for _ in range(500)]}
timeline.TIMELINES.put(key, timeline.freeze_timeline(key, value, "a" * 64))
print(json.dumps([file.fileno() for file in files]), flush=True)
sys.stdin.readline()
os._exit(0)
""")
    backing = backing_directory
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(timeline.__file__).parents[3]),
        PYTHONDONTWRITEBYTECODE="1",
        TMPDIR=str(backing),
    )
    with subprocess.Popen(
        [sys.executable, "-B", str(script)],
        env=env,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        assert process.stdout is not None and process.stdin is not None
        try:
            descriptors = json.loads(process.stdout.readline())
            assert len(descriptors) == 2
            for fd in descriptors:
                path = Path(f"/proc/{process.pid}/fd/{fd}")
                if sys.platform == "linux":
                    assert path.exists() and path.stat().st_nlink == 0
            assert list(backing.iterdir()) == []
        finally:
            process.stdin.write("exit\n")
            process.stdin.flush()
            process.wait(timeout=30)
        assert process.returncode == 0
        assert not Path(f"/proc/{process.pid}").exists()
        assert list(backing.iterdir()) == []


def test_spilled_unreadable_source_is_retried(
    selected: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _track_backing(monkeypatch)
    calls = 0

    def record_events(*args: Any) -> list[Any]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise timeline.ReaderReadError("staged source read failure")
        return []

    monkeypatch.setattr(timeline, "_record_events", record_events)
    record: Any = SimpleNamespace(id="FAM-000000")
    rows = _history(500, 18000, False)
    first = timeline.record_timeline(selected, record, (), rows)
    assert first["sources"]["record"]["state"] == "unavailable"
    assert len(first["events"]) == 50 and len(timeline.TIMELINES) == 0
    assert len(files) == 2 and all(file.closed for file in files)
    ready = timeline.record_timeline(selected, record, (), rows, continuation=first["continuation"])
    assert ready["sources"]["record"]["state"] == "read" and ready["page"]["start"] == 50
    timeline.record_timeline(selected, record, (), rows, continuation=ready["continuation"])
    assert calls == 2 and len(files) == 4 and all(not file.closed for file in files[2:])


def test_failed_backing_read_is_not_stuck_in_cache(
    selected: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = _track_backing(monkeypatch)
    rows = _history(500, 18000, False)
    record: Any = SimpleNamespace(id="FAM-000000")
    first = timeline.record_timeline(selected, record, (), rows)
    files[1].truncate(0)
    with pytest.raises(OSError, match="unreadable anonymous timeline offsets"):
        timeline.record_timeline(selected, record, (), rows, continuation=first["continuation"])
    assert len(timeline.TIMELINES) == timeline.TIMELINES.weight == 0
    retry = timeline.record_timeline(selected, record, (), rows, continuation=first["continuation"])
    assert retry["state"] == "view" and retry["page"]["start"] == 50 and len(files) == 4
    assert retry["events"][0]["document"] == rows[50].document


def test_failed_backing_creation_releases_partial_files(
    selected: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    files: list[Any] = []
    original = tempfile.TemporaryFile

    def opened(*args: Any, **kwargs: Any) -> Any:
        if files:
            raise OSError("staged second anonymous file failure")
        file = original(*args, **kwargs)
        files.append(file)
        return file

    record: Any = SimpleNamespace(id="FAM-000000")
    rows = _history(500, 18000, False)
    with monkeypatch.context() as failing:
        failing.setattr(tempfile, "TemporaryFile", opened)
        with pytest.raises(OSError, match="staged second anonymous file failure"):
            timeline.record_timeline(selected, record, (), rows)
    assert len(files) == 1 and files[0].closed and len(timeline.TIMELINES) == 0
    assert len(timeline.record_timeline(selected, record, (), rows)["events"]) == 50


@pytest.mark.parametrize("changed", ["tree", "record"])
def test_spilled_continuation_refuses_another_binding(selected: Any, changed: str) -> None:
    record: Any = SimpleNamespace(id="FAM-000000")
    rows = _history(500, 18000, False)
    first = timeline.record_timeline(selected, record, (), rows)
    if changed == "tree":
        selected.index.state.key = "c" * 40
    else:
        record.id = "FAM-000001"
    refused = timeline.record_timeline(
        selected, record, (), rows, continuation=first["continuation"]
    )
    assert refused["state"] == "refused" and refused["code"] == "continuation_binding_mismatch"
    assert refused["events"] == [] and refused["continuation"] is None


def test_resident_lru_and_spill_share_the_original_memory_budget(selected: Any) -> None:
    record: Any = SimpleNamespace(id="FAM-000000")
    rows = _history(200, 18000, False)
    for revision in ("a" * 40, "b" * 40, "c" * 40):
        selected.revision = revision
        timeline.record_timeline(selected, record, (), rows)
        assert 0 < timeline.TIMELINES.weight <= timeline.TIMELINE_CACHE_BYTES
    assert len(timeline.TIMELINES) == 2
    assert (
        timeline.TIMELINES.get((str(selected.memory_repository), "a" * 40, "b" * 40, record.id))
        is None
    )
    selected.revision = "d" * 40
    timeline.record_timeline(selected, record, (), _history(500, 18000, False))
    assert len(timeline.TIMELINES) == 3
    assert 0 < timeline.TIMELINES.weight <= timeline.TIMELINE_CACHE_BYTES
    assert timeline.TIMELINES.spilled_bytes > timeline.TIMELINE_CACHE_BYTES


@pytest.mark.parametrize("first_read_fails", [False, True])
@pytest.mark.parametrize("size", [500, 1000])
def test_truth_view_acquires_full_history_only_on_manifest_miss(
    selected: Any,
    monkeypatch: pytest.MonkeyPatch,
    first_read_fails: bool,
    size: int,
) -> None:
    rows = _history(size, 18000, False)
    record = SimpleNamespace(
        id="FAM-000000", kind="family", path="knowledge/families/FAM-000000.json", document={}
    )
    calls = 0

    def history(subject: str) -> Any:
        nonlocal calls
        calls += 1
        assert subject == record.id
        if first_read_fails and calls == 1:
            raise OSError("staged index-history failure")
        return SimpleNamespace(value=rows)

    selected.index.record = lambda _: SimpleNamespace(value=record)
    selected.index.incoming_links = lambda _: SimpleNamespace(value=())
    selected.index.history_rows_about = history
    # Hold unrelated truth-view sections, not the defended caller/history acquisition,
    # producer, manifest/cache/digest, spill or continuation.
    monkeypatch.setattr(truth, "summary_of", lambda _: {"id": record.id})
    monkeypatch.setattr(truth, "_outgoing", lambda _: ([], None))
    monkeypatch.setattr(truth, "_kind_part", lambda *_: ({}, ()))
    monkeypatch.setattr(truth, "Decisions", lambda _: None)
    monkeypatch.setattr(
        truth, "read_memory_file", lambda *_: SimpleNamespace(to_document=lambda: {})
    )
    if first_read_fails:
        with pytest.raises(OSError, match="staged index-history failure"):
            truth.record_view(selected, record.id)
        assert len(timeline.TIMELINES) == 0
    result = truth.record_view(selected, record.id)
    assert result is not None
    events = list(result["timeline"]["events"])
    while result["timeline"]["continuation"]:
        result = truth.record_view(
            selected, record.id, continuation=result["timeline"]["continuation"]
        )
        assert result is not None
        events.extend(result["timeline"]["events"])
    assert [event["document"] for event in events] == [row.document for row in rows]
    assert calls == 1 + int(first_read_fails)
