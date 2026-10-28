"""Scratch F8 boundary tests; parent owns application and execution gate."""

from __future__ import annotations

import json
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from agents_remember.serving import changeset as changeset_module
from agents_remember.serving import changeset_ranges
from agents_remember.serving import review_answer_reuse as disk_module
from agents_remember.serving.changeset import master_changeset
from agents_remember.serving.changeset_ranges import MasterRangeAnswers
from agents_remember.serving.master_counter import CounterSource
from agents_remember.serving.master_net_generation import MasterNetPins
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_master_net_generation import KEEP_PATH, MASTER, REPO, _commit, build_master_fixture

pytestmark = pytest.mark.evidence_unit


def _key(number: int) -> tuple[str, str, str]:
    return ("/fixture/code", f"{number:040x}", f"{number + 1:040x}")


def _rows(label: str) -> list[dict[str, Any]]:
    return [{"path": label, "insertions": 2, "deletions": 1, "status": "M"}]


def _files(directory: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in directory.glob("*.review.json")}


@pytest.mark.parametrize("capacity,max_bytes", [(2, 4096), (5, 16384)])
@pytest.mark.parametrize("byte_limited", [False, True])
def test_master_range_batches_respect_capacity_and_bytes_at_two_scales(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capacity: int,
    max_bytes: int,
    byte_limited: bool,
) -> None:
    directory = tmp_path / "ranges"
    writes: list[int] = []
    original = disk_module.atomic_write_bytes

    def write(path: Path, body: bytes) -> None:
        writes.append(len(body))
        original(path, body)

    monkeypatch.setattr(disk_module, "atomic_write_bytes", write)
    width = max_bytes // 2 if byte_limited else 20
    for batch in range(3):
        # A fresh independent owner must reserve against the existing files too.
        owner = MasterRangeAnswers(directory, "bounds", capacity=capacity, max_disk_bytes=max_bytes)
        owner.publish(
            {
                _key(batch * 100 + index): _rows(f"{batch}-{index}-" + "x" * width)
                for index in range(capacity * 3)
            },
            None,
        )
        saved = _files(directory)
        assert saved
        assert len(saved) <= capacity
        assert sum(map(len, saved.values())) <= max_bytes
        if byte_limited:
            assert len(saved) < capacity
        else:
            assert len(saved) == capacity
        assert all(json.loads(body)["answer"]["files"] for body in saved.values())
    before = _files(directory)
    writes.clear()
    owner.publish({_key(9999): _rows("oversized" + "x" * (max_bytes + 1))}, None)
    assert writes == [], "an oversized answer must never be transiently written"
    assert _files(directory) == before


def test_cancel_during_batch_restores_prior_bytes_and_removes_new_answers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "ranges"
    owner = MasterRangeAnswers(directory, "cancellation")
    owner.publish({_key(1): _rows("prior")}, None)
    before = _files(directory)
    cancelled = Event()
    original = disk_module.atomic_write_bytes
    writes = 0

    def write(path: Path, body: bytes) -> None:
        nonlocal writes
        original(path, body)
        writes += 1
        if writes == 2:
            # Both a replacement and a new answer have reached atomic publication.
            cancelled.set()

    monkeypatch.setattr(disk_module, "atomic_write_bytes", write)
    result = owner.publish(
        {_key(1): _rows("replacement"), _key(2): _rows("new"), _key(3): _rows("never published")},
        cancelled,
    )
    assert cancelled.is_set() and writes >= 2
    assert result == ()
    assert _files(directory) == before
    restored = MasterRangeAnswers(directory, "cancellation")
    assert restored.load(_key(1)) == _rows("prior")
    assert restored.load(_key(2)) is None and restored.load(_key(3)) is None


def test_withdrawal_does_not_remove_another_owners_later_replacement(tmp_path: Path) -> None:
    directory = tmp_path / "ranges"
    first = MasterRangeAnswers(directory, "interleaved-owners")
    second = MasterRangeAnswers(directory, "interleaved-owners")
    first.publish({_key(1): _rows("before either request")}, None)
    publications = first.publish({_key(1): _rows("request one")}, None)
    assert publications
    # Deliberate legal concurrency order: B commits while A still holds its receipt,
    # then A notices cancellation. No scheduler timing is needed to exercise it.
    second.publish({_key(1): _rows("request two")}, None)
    before_withdrawal = _files(directory)
    first.withdraw(publications)
    assert _files(directory) == before_withdrawal
    assert MasterRangeAnswers(directory, "interleaved-owners").load(_key(1)) == _rows("request two")


@pytest.mark.parametrize("include_leaves", [False, True])
@pytest.mark.parametrize("pinned_after_move", [False, True])
def test_lean_counter_matches_full_same_generation_without_any_sidecar_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    include_leaves: bool,
    pinned_after_move: bool,
) -> None:
    world = build_master_fixture(tmp_path / "world")
    with (world.code_repo / KEEP_PATH).open("a", encoding="utf-8") as stream:
        stream.write("\n# first measured net\n")
    _commit(world.code_repo, "a nonempty master net")
    onboarding = world.memory_repo / "onboarding"
    onboarding.mkdir()
    (onboarding / "overview.index.json").write_text(
        json.dumps({"sourceScope": ["src/"], "coveredFiles": [KEEP_PATH]}), encoding="utf-8"
    )
    initial = master_changeset(world.config, REPO, MASTER, include_leaves=include_leaves)
    assert initial["code"][0]["hasSidecar"] is True
    pins = None
    if pinned_after_move:
        generation = initial["generation"]
        pins = MasterNetPins(
            generation["codeBase"],
            generation["codeTip"],
            generation["memoryBase"],
            generation["memoryTip"],
        )
        with (world.code_repo / KEEP_PATH).open("a", encoding="utf-8") as stream:
            stream.write("\n# later branch tip\n")
        _commit(world.code_repo, "move beyond pinned generation")
    full = master_changeset(world.config, REPO, MASTER, include_leaves=include_leaves, pins=pins)
    assert full["currentness"] == ("superseded" if pinned_after_move else "current")

    def unexpected(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the lean counter consulted sidecar presentation data")

    monkeypatch.setattr(changeset_module, "route_sidecar_status", unexpected)
    monkeypatch.setattr(changeset_module, "_master_onboarding_root", unexpected)
    lean = CounterSource(changeset_module._net_diff, changeset_module._master_leaf_summaries).read(
        world.config, REPO, MASTER, include_leaves=include_leaves, pins=pins
    )
    assert lean == {key: value for key, value in full.items() if key not in ("code", "memory")}


def test_fixed_pair_survives_time_without_age_reclamation(tmp_path, monkeypatch):
    monkeypatch.setattr(changeset_ranges, "time", lambda: 1_000_000.0)
    MasterRangeAnswers(tmp_path, "age-free").publish({_key(1): _rows("fixed")}, None)
    monkeypatch.setattr(changeset_ranges, "time", lambda: 1_000_000.0 + 100 * 365 * 86400)
    assert MasterRangeAnswers(tmp_path, "age-free").load(_key(1)) == _rows("fixed")


@pytest.mark.parametrize("include_leaves", [False, True])
def test_counter_route_matches_listing_and_keeps_conditional_reads(tmp_path, include_leaves):
    world = build_master_fixture(tmp_path / "world")
    app = FastAPI()
    changeset_module.register_changeset_routes(app, world.config)
    with TestClient(app) as client:
        params = {"repo": REPO, "master": MASTER, "includeLeaves": str(include_leaves).lower()}
        lean = client.get("/api/changeset/master/counters", params=params)
        assert lean.status_code == 200, lean.text
        full = client.get("/api/changeset/master", params=params)
        assert full.status_code == 200, full.text
        assert lean.json() == {
            key: value for key, value in full.json().items() if key not in {"code", "memory"}
        }
        assert "code" not in lean.json() and "memory" not in lean.json()
        unchanged = client.get(
            "/api/changeset/master/counters",
            params=params,
            headers={"If-None-Match": lean.headers["etag"]},
        )
        assert unchanged.status_code == 304
        absent = client.get(
            "/api/changeset/master/counters", params={**params, "codeTip": "0" * 40}
        )
        assert absent.status_code == 404
        assert absent.json()["status"] == "not-found"
