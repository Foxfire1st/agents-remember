"""L201: scalar totals share detailed usage's byte and refusal decisions."""

from pathlib import Path

import pytest
from agents_remember.application import review_frame_store as store
from agents_remember.application.review_planned_comparison import task_frames_total
from agents_remember.models.knowledge.review_frame import FrameLimits
from test_review_frame_store import ASSETS, AT, FRAME, LEAF, _authoring, _publish, _record


def _populated(root: Path) -> tuple[Path, Path]:
    root.mkdir()
    assert _publish(root, _record()).published
    scope = store.frame_scope(root, LEAF, FRAME)
    store.begin_draft(
        scope,
        working_directory=root.parent / "authoring-live",
        request_id="live",
        authoring=_authoring("live"),
        focus="reserved bytes",
        assets=ASSETS,
        limits=FrameLimits(),
        now=AT,
    )
    (scope.frame_dir / store.RECLAMATION_NAME).write_text('{"state":"retained"}')
    return store.frames_area(root, LEAF), scope.frame_dir


def _parity(area: Path) -> dict:
    detailed = store.usage(area, now=AT)
    scalar = store.usage(area, now=AT, include_frames=False)
    assert scalar == {**detailed, "frames": []}
    return detailed


def test_scalar_counts_revisions_metadata_and_live_reservations(tmp_path: Path) -> None:
    area, frame = _populated(tmp_path / "task")
    detailed = _parity(area)
    assert detailed["held"] == []
    assert detailed["frames"][0]["ready_revision"] == 1
    assert detailed["frames"][0]["drafts"] == ["active", "published"]
    physical = sum(path.stat().st_size for path in frame.rglob("*") if path.is_file())
    assert detailed["bytes"] == physical + sum(map(len, ASSETS.values()))


@pytest.mark.parametrize("relative", ["ready.json", "drafts/live/draft.json"])
def test_scalar_keeps_malformed_record_byte_semantics(tmp_path: Path, relative: str) -> None:
    area, frame = _populated(tmp_path / "task")
    (frame / relative).write_bytes(b"not valid json")
    assert _parity(area)["held"] == []


@pytest.mark.parametrize(
    "relative",
    [
        "ready.json",
        "reclaimed.json",
        "revisions",
        "revisions/1/index.html",
        "drafts",
        "drafts/live/draft.json",
        "drafts/live/foreign",
    ],
)
@pytest.mark.parametrize("broken", [False, True])
def test_scalar_keeps_symlink_refusals(tmp_path: Path, relative: str, broken: bool) -> None:
    area, frame = _populated(tmp_path / "task")
    path = frame / relative
    if path.is_dir():
        path.rename(frame / ("saved-" + path.name))
    elif path.exists():
        path.unlink()
    target = tmp_path / "foreign"
    if not broken:
        target.write_bytes(b"outside")
    path.symlink_to(target)
    detailed = _parity(area)
    # Missing pointer/receipt targets were never counted; preserve that existing behavior.
    if not (broken and relative in {"ready.json", "reclaimed.json"}):
        assert detailed["held"]


def test_scalar_keeps_hidden_and_non_directory_rules(tmp_path: Path) -> None:
    area, _ = _populated(tmp_path / "task")
    initial = _parity(area)["bytes"]
    (area / ".hidden").mkdir()
    (area / ".hidden" / "ignored").write_bytes(b"not a frame")
    (area / "not-a-frame").write_bytes(b"also ignored")
    assert _parity(area)["bytes"] == initial
    (area / "foreign-frame").symlink_to(tmp_path / "absent")
    assert _parity(area)["held"]


def test_task_scalar_total_matches_detailed_listing_and_held_result(tmp_path: Path) -> None:
    root = tmp_path / "task"
    area, frame = _populated(root)
    assert _publish(root, _record(leaf="260928-MIK-L103"), request="other").published
    task_area = area.parent
    expected = sum(store.usage(child)["bytes"] for child in task_area.iterdir())
    result = task_frames_total(root, LEAF)
    assert result == {
        "state": "ok",
        "used": expected,
        "bytes": expected,
        "limit": FrameLimits().task_bytes,
    }
    (frame / "revisions/1/foreign").symlink_to(tmp_path / "absent")
    reason = store.usage(area)["held"][0]["reason"]
    assert task_frames_total(root, LEAF) == {
        "state": "unavailable",
        "detail": reason,
        "limit": FrameLimits().task_bytes,
    }


def test_scalar_reads_each_draft_once_and_does_not_decode_ready_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    area, _ = _populated(tmp_path / "task")
    reads: list[Path] = []
    original = store.read_draft_path

    def record_read(path: Path):
        reads.append(path)
        return original(path)

    monkeypatch.setattr(store, "read_draft_path", record_read)
    detailed = store.usage(area)
    assert len(reads) == 4
    reads.clear()

    def unexpected_pointer(_path: Path):
        pytest.fail("scalar usage must not decode display-only pointer metadata")

    monkeypatch.setattr(store, "_pointer", unexpected_pointer)
    scalar = store.usage(area, include_frames=False)
    assert scalar["bytes"] == detailed["bytes"]
    assert scalar["held"] == detailed["held"]
    assert len(reads) == 2
