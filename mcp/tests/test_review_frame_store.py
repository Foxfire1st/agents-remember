"""MIK-R103: a durable frame revision, its drafts, its reopening and its reclamation.

One case per round-1 finding, each driving the real store against a real task directory:

1. F01 a revision binds one comparison's full identity, not a tree hash;
2. F09 the admitted evidence and the regeneration inputs live with the revision;
3. F10 the lineage and task ceilings count what the store actually holds, drafts included;
4. F11 a symlinked frame or leaf is held, never followed out of the task;
5. F12 a live draft is kept, a finished or abandoned one is reclaimed, and its outcome is durable;
6. F13 reopening and serving read one verified snapshot and refuse corrupt bytes;
7. F14 a refused publication leaves every prior byte and the pointer where they were;
8. F15 reclamation leaves receipts that reopening and a retry consume;
9. F16 the largest admitted store is measured, restarted and bounded.

Nothing here starts a model, a role or a page: the store is an ordinary module over a temporary task.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import pytest
from agents_remember.application import review_frame_store as store
from agents_remember.application.review_evidence_packet import (
    SideState,
    build_evidence_packet,
    comparison_reference,
)
from agents_remember.application.review_evidence_read import DEFAULT_READ_LIMITS
from agents_remember.application.review_frame_store import (
    RECLAMATION_RECEIPT,
    TASK_LOCK_NAME,
    FrameRefused,
    _record_reclamation,
    begin_draft,
    cleanup_frames,
    finish_draft,
    frame_scope,
    frame_staleness,
    frames_area,
    open_asset,
    publish_revision,
    reclaim_task_frames,
    regeneration_request,
    reopen_frame,
    usage,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceCollection,
    EvidenceMap,
)
from agents_remember.models.knowledge.review_frame import (
    FrameAuthoring,
    FrameBinding,
    FrameLimits,
)
from agents_remember.models.knowledge.review_trees import (
    ReviewConvertedBase,
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

AT = "2026-10-18T00:00:00Z"
LATER = "2026-10-18T06:00:00Z"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L102"
FRAME = "comparison-bound-evidence"
OWNER = "agents-remember"


def _record(  # noqa: PLR0913 - one comparison's own identity fields
    number: int = 3,
    *,
    task: str = TASK,
    leaf: str = LEAF,
    code_repository: str = "/repo/code",
    memory_repository: str = "/repo/memory",
    code_candidate: str = "b" * 40,
    memory_candidate: str = "d" * 40,
    converted: str | None = None,
) -> ReviewTreeComparisonRecord:
    return ReviewTreeComparisonRecord(
        task_id=task,
        leaf_id=leaf,
        number=number,
        code_base=ReviewTreeSide(repository=code_repository, tree="a" * 40),
        code_candidate=ReviewTreeSide(repository=code_repository, tree=code_candidate),
        memory_base=ReviewTreeSide(repository=memory_repository, tree="c" * 40),
        memory_candidate=ReviewTreeSide(repository=memory_repository, tree=memory_candidate),
        converted_base=(
            None
            if converted is None
            else ReviewConvertedBase(
                commit="f" * 40,
                version="ar-review-conversion/v1",
                code_commit="e" * 40,
                tree=converted,
            )
        ),
        recorded_at=AT,
    )


def _evidence(record: ReviewTreeComparisonRecord):
    claim = EvidenceClaim(
        claim_id="c1",
        authoring_revision="rev-1",
        classification="recorded-fact",
        subject="the packet names its comparison",
        applicability="this comparison",
        statement="the packet names one comparison",
        bases=(
            ClaimBasis(
                kind="tree-blob",
                reference="code-candidate:src/app.py",
                state="present",
                side="code-candidate",
                tree=record.code_candidate.tree,
                path="src/app.py",
            ),
        ),
    )
    return build_evidence_packet(
        record,
        repository_id=OWNER,
        scope="the change to src/app.py",
        question="why?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=SideState("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="the selected change"),
        limits=DEFAULT_READ_LIMITS,
        claims=(claim,),
        recorded_at=AT,
    )


ASSETS = {
    "index.html": b"<!doctype html><title>frame</title><p>comparison</p>",
    "app.css": b"body{font:16px monospace}",
}


@pytest.fixture()
def task(tmp_path: Path) -> Path:
    root = tmp_path / "task"
    (root / "notes" / "reports").mkdir(parents=True)
    return root


def _authoring(request: str = "req-1") -> FrameAuthoring:
    return FrameAuthoring(request_id=request, session_id="session-1", started_at=AT)


def _publish(  # noqa: PLR0913 - one test publication states its own comparison and assets
    task_root: Path,
    record: ReviewTreeComparisonRecord,
    *,
    request: str = "req-1",
    assets: dict[str, bytes] | None = None,
    entrypoint: str = "index.html",
    limits: FrameLimits | None = None,
    ancestry: tuple[int, ...] = (),
    now: str = AT,
):
    scope = frame_scope(task_root, record.leaf_id, FRAME)
    begin_draft(
        scope,
        working_directory=task_root.parent
        / "authoring"
        / task_root.name
        / record.leaf_id
        / request,
        request_id=request,
        authoring=_authoring(request),
        focus="the selected comparison",
        assets=ASSETS if assets is None else assets,
        limits=limits or FrameLimits(),
        now=now,
    )
    return publish_revision(
        scope,
        request_id=request,
        entrypoint=entrypoint,
        evidence=_evidence(record),
        supplemental=(),
        created_at=now,
        limits=limits or FrameLimits(),
        asset_ancestry=ancestry,
    )


# --- F01: the binding keeps the comparison's full identity --------------------------------------


def test_f01_a_revision_bound_to_a_foreign_comparison_is_stale(task: Path) -> None:
    published = _publish(task, _record())
    assert published.published
    scope = frame_scope(task, LEAF, FRAME)

    current = frame_staleness(scope, _record(), comparison_owner=OWNER)
    assert current.state == "current"

    for moved in (
        _record(task="other-task"),
        _record(leaf="other-leaf"),
        _record(number=4),
        _record(code_repository="/another/code"),
        _record(code_candidate="9" * 40),
        _record(memory_candidate="8" * 40),
        _record(converted="7" * 40),
    ):
        judged = frame_staleness(scope, moved, comparison_owner=OWNER)
        assert judged.state == "stale", moved
        assert judged.changed
        assert judged.selected is not None and judged.retained is not None
        assert judged.regeneration_offered


def test_f01_a_binding_keeps_repositories_commits_and_the_conversion(task: Path) -> None:
    record = _record().model_copy(
        update={
            "code_candidate": ReviewTreeSide(
                repository="/repo/code", tree="b" * 40, commit="1" * 40, ref="refs/ar/review/x/1"
            )
        }
    )
    binding = FrameBinding.from_record(
        record, comparison_owner=OWNER, comparison_reference=comparison_reference(record)
    )
    assert binding.code_candidate_commit == "1" * 40
    assert binding.code_candidate_ref == "refs/ar/review/x/1"
    assert binding.code_candidate_repository == "/repo/code"

    converted = _record(converted="7" * 40)
    converted_binding = FrameBinding.from_record(
        converted, comparison_owner=OWNER, comparison_reference=comparison_reference(converted)
    )
    assert converted_binding.converted_base_tree == "7" * 40
    assert converted_binding.compared_memory_base_tree == "7" * 40
    assert not binding.same_comparison(converted_binding)


# --- F09: the revision keeps its evidence and its regeneration inputs ----------------------------


def test_f09_the_evidence_survives_the_scratch_and_regeneration_names_it(task: Path) -> None:
    record = _record()
    _publish(task, record)
    scope = frame_scope(task, LEAF, FRAME)

    # The authoring scratch is gone: only the store's own bytes remain.
    for leftover in (scope.frame_dir / "drafts").iterdir():
        for child in sorted(leftover.rglob("*"), reverse=True):
            child.unlink() if child.is_file() else child.rmdir()
        leftover.rmdir()

    opening = reopen_frame(scope)
    assert opening.state == "ready"
    assert opening.manifest is not None
    assert opening.manifest.evidence.claims.claims[0].claim_id == "c1"
    assert opening.manifest.evidence.comparison.comparison_reference == comparison_reference(record)

    moved = _record(code_candidate="9" * 40)
    request = regeneration_request(scope, moved, comparison_owner=OWNER)
    assert request.state == "ready"
    assert request.stale_revision == 1
    assert request.entrypoint == "index.html"
    assert {asset.path for asset in request.assets} == set(ASSETS)
    assert request.evidence_digest is not None
    assert request.asset_ancestry == (1,)


# --- F10: the ceilings count actual store usage -------------------------------------------------


def test_f10_a_lineage_ceiling_counts_revisions_drafts_and_metadata(task: Path) -> None:
    tight = FrameLimits(lineage_bytes=1200, bytes_per_revision=1200, draft_bytes=1200)
    with pytest.raises(FrameRefused, match="lineage"):
        _publish(task, _record(), limits=tight)
    area = frames_area(task, LEAF)
    assert not (area / FRAME / "revisions").exists() or not any(
        (area / FRAME / "revisions").iterdir()
    )

    roomy = FrameLimits(lineage_bytes=100_000, bytes_per_revision=100_000)
    assert _publish(task, _record(), limits=roomy).published
    counted = usage(area, now=AT)
    assert counted["frames"][0]["bytes"] > 0
    assert counted["frames"][0]["revision_bytes"] > 0
    assert counted["frames"][0]["metadata_bytes"] >= 0


def test_f10_the_task_ceiling_counts_every_lineage_and_a_draft(task: Path) -> None:
    first = FrameLimits(task_bytes=12_000, lineage_bytes=100_000, draft_bytes=100_000)
    assert _publish(task, _record(), limits=first).published
    other = frame_scope(task, LEAF, "second-frame")
    begin_draft(
        other,
        working_directory=task.parent / "authoring" / task.name / "req-2",
        request_id="req-2",
        authoring=_authoring("req-2"),
        focus="another page",
        assets={"index.html": b"<p>second</p>" * 40},
        limits=first,
        now=AT,
    )
    tight = FrameLimits(task_bytes=12_000, lineage_bytes=100_000, draft_bytes=100_000)
    with pytest.raises(FrameRefused, match="task"):
        begin_draft(
            other,
            working_directory=task.parent / "authoring" / task.name / "req-3",
            request_id="req-3",
            authoring=_authoring("req-3"),
            focus="a third page",
            assets={"index.html": b"<p>third</p>" * 2000},
            limits=tight,
            now=AT,
        )


# --- F11: physical confinement ---------------------------------------------------------------


def test_f11_a_symlinked_frame_is_held_by_every_store_act(task: Path, tmp_path: Path) -> None:
    external = tmp_path / "elsewhere"
    (external / "revisions" / "1" / "assets").mkdir(parents=True)
    (external / "revisions" / "1" / "assets" / "index.html").write_text("foreign")
    area = frames_area(task, LEAF)
    area.mkdir(parents=True, exist_ok=True)
    (area / FRAME).symlink_to(external, target_is_directory=True)

    with pytest.raises(FrameRefused, match="physically inside"):
        frame_scope(task, LEAF, FRAME)
    with pytest.raises(FrameRefused, match="physically inside"):
        reopen_frame(frame_scope(task, LEAF, FRAME))
    with pytest.raises(FrameRefused, match="physically inside"):
        begin_draft(
            frame_scope(task, LEAF, FRAME),
            working_directory=task.parent / "authoring" / task.name / "req-1",
            request_id="req-1",
            authoring=_authoring(),
            focus="x",
            assets=ASSETS,
            limits=FrameLimits(),
            now=AT,
        )
    report = cleanup_frames(area, dry_run=False, now=LATER)
    assert report["held"], "a symlinked frame is held, not cleaned"
    assert (external / "revisions" / "1" / "assets" / "index.html").read_text() == "foreign"
    archived = reclaim_task_frames(task, dry_run=False, now=LATER)
    assert archived["failed"], "the archive holds a symlinked leaf instead of following it"
    assert (external / "revisions" / "1" / "assets" / "index.html").read_text() == "foreign"


# --- F12: drafts and their outcomes ------------------------------------------------------------


def test_f12_a_live_draft_is_kept_and_an_abandoned_one_is_reclaimed(task: Path) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    begin_draft(
        scope,
        working_directory=task.parent / "authoring" / task.name / "req-1",
        request_id="req-1",
        authoring=_authoring(),
        focus="a draft",
        assets=ASSETS,
        limits=FrameLimits(),
        now=AT,
    )
    held = cleanup_frames(scope.area, dry_run=False, now=AT)
    assert any("draft" in reason for entry in held["held"] for reason in [entry["reason"]])
    assert (scope.frame_dir / "drafts" / "req-1" / "draft.json").is_file()

    abandoned = cleanup_frames(scope.area, dry_run=False, now=LATER)
    assert abandoned["removed"], "a draft past its lease is reclaimed"
    assert not (scope.frame_dir / "drafts" / "req-1").exists()
    receipt = json.loads((scope.frame_dir / "reclaimed.json").read_text())
    assert receipt["state"] == "deleted"
    assert "req-1" in receipt["drafts"]


def test_f12_a_failed_attempt_keeps_its_bounded_outcome(task: Path) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    begin_draft(
        scope,
        working_directory=task.parent / "authoring" / task.name / "req-1",
        request_id="req-1",
        authoring=_authoring(),
        focus="a draft",
        assets=ASSETS,
        limits=FrameLimits(),
        now=AT,
    )
    failed = finish_draft(
        scope,
        "req-1",
        state="failed",
        detail="the authoring turn ended",
        limits=FrameLimits(),
        now=AT,
    )
    assert failed is not None and failed.state == "failed"
    assert failed.detail == "the authoring turn ended"
    with pytest.raises(FrameRefused, match="not active"):
        publish_revision(
            scope,
            request_id="req-1",
            entrypoint="index.html",
            evidence=_evidence(_record()),
            supplemental=(),
            created_at=AT,
            limits=FrameLimits(),
        )


def test_f12_a_published_draft_never_publishes_a_second_revision(task: Path) -> None:
    first = _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    again = publish_revision(
        scope,
        request_id="req-1",
        entrypoint="index.html",
        evidence=_evidence(_record()),
        supplemental=(),
        created_at=LATER,
        limits=FrameLimits(),
    )
    assert not again.published
    assert first.manifest is not None
    assert again.manifest is not None and again.manifest.revision == first.manifest.revision
    assert len([number for number in _revisions(scope)]) == 1


def _revisions(scope) -> list[int]:
    root = scope.frame_dir / "revisions"
    return sorted(int(child.name) for child in root.iterdir() if child.name.isdigit())


# --- F13: one verified snapshot ----------------------------------------------------------------


def test_f13_corrupt_bytes_are_refused_rather_than_served(task: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    asset = scope.frame_dir / "revisions" / "1" / "assets" / "index.html"
    asset.write_text("tampered")

    opening = reopen_frame(scope)
    assert opening.state == "unavailable"
    assert "corrupt" in opening.detail
    with pytest.raises(FrameRefused, match=r"corrupt|missing"):
        open_asset(scope, 1, "index.html")


def test_f13_a_manifest_that_does_not_match_the_pointer_is_unavailable(task: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    manifest = scope.frame_dir / "revisions" / "1" / "manifest.json"
    document = json.loads(manifest.read_text())
    document["focus"] = "rewritten after publication"
    manifest.write_text(json.dumps(document))
    opening = reopen_frame(scope)
    assert opening.state == "unavailable"


# --- F14: a refusal mutates nothing ------------------------------------------------------------


def test_f14_a_refused_publication_preserves_the_ready_revision(task: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    before = {
        path.relative_to(scope.frame_dir).as_posix(): path.read_bytes()
        for path in sorted(scope.frame_dir.rglob("*"))
        if path.is_file() and "drafts" not in path.parts
    }
    tight = FrameLimits(manifest_bytes=64, lineage_bytes=1_000_000)
    begin_draft(
        scope,
        working_directory=task.parent / "authoring" / task.name / "req-2",
        request_id="req-2",
        authoring=_authoring("req-2"),
        focus="a second revision",
        assets=ASSETS,
        limits=tight,
        now=LATER,
    )
    with pytest.raises(FrameRefused, match="manifest"):
        publish_revision(
            scope,
            request_id="req-2",
            entrypoint="index.html",
            evidence=_evidence(_record(number=3)),
            supplemental=(),
            created_at=LATER,
            limits=tight,
        )
    after = {
        path.relative_to(scope.frame_dir).as_posix(): path.read_bytes()
        for path in sorted(scope.frame_dir.rglob("*"))
        if path.is_file() and "drafts" not in path.parts
    }
    assert after == before
    assert _revisions(scope) == [1]
    assert reopen_frame(scope).state == "ready"


# --- F15: reclamation receipts -----------------------------------------------------------------


def test_f15_a_reclaimed_revision_is_named_when_reopened(task: Path) -> None:
    tight = FrameLimits(revisions_per_lineage=1, lineage_bytes=10_000_000)
    _publish(task, _record(), request="req-1", limits=tight)
    _publish(task, _record(), request="req-2", limits=tight, now=LATER)
    scope = frame_scope(task, LEAF, FRAME)
    assert _revisions(scope) == [2]
    receipt = json.loads((scope.frame_dir / "reclaimed.json").read_text())
    assert receipt["state"] == "deleted"
    assert 1 in receipt["revisions"]
    assert receipt["reason"]

    (scope.frame_dir / "ready.json").unlink()
    opening = reopen_frame(scope)
    assert opening.state == "unavailable"
    assert "reclaimed" in opening.detail


def test_f15_an_archive_records_a_partial_failure_and_keeps_what_it_removed(
    task: Path, tmp_path: Path
) -> None:
    _publish(task, _record(), request="req-1")
    area = frames_area(task, LEAF)
    external = tmp_path / "external-frame"
    (external / "revisions" / "1" / "assets").mkdir(parents=True)
    (external / "revisions" / "1" / "assets" / "index.html").write_text("foreign")
    (area / "linked-frame").symlink_to(external, target_is_directory=True)

    report = reclaim_task_frames(task, dry_run=False, now=LATER)
    receipt = json.loads((task / "notes" / "reports" / RECLAMATION_RECEIPT).read_text())
    assert report["state"] == "partial"
    assert receipt["state"] == "partial"
    assert receipt["failed"], "a partial archive records what it could not remove"
    assert any("linked-frame" in entry["path"] for entry in receipt["failed"])
    assert (external / "revisions" / "1" / "assets" / "index.html").read_text() == "foreign"

    retry = reclaim_task_frames(task, dry_run=False, now=LATER)
    assert retry["state"] in {"reclaimed", "partial"}
    kept = json.loads((task / "notes" / "reports" / RECLAMATION_RECEIPT).read_text())
    assert kept["frames"], "a retry keeps the frames an earlier attempt named"
    assert (external / "revisions" / "1" / "assets" / "index.html").read_text() == "foreign"


# --- F16: the largest admitted store, restart and counters --------------------------------------


def test_f16_the_largest_admitted_store_reopens_after_a_restart(task: Path) -> None:
    limits = FrameLimits(revisions_per_lineage=2, lineage_bytes=10_000_000)
    _publish(task, _record(), request="req-1", limits=limits)
    _publish(task, _record(), request="req-2", limits=limits, now=LATER)
    scope = frame_scope(task, LEAF, FRAME)
    assert _revisions(scope) == [1, 2]
    pointer = json.loads((scope.frame_dir / "ready.json").read_text())
    assert pointer["revision"] == 2

    # A second store instance reads the same bytes; nothing in memory carries the page.
    restarted = frame_scope(task, LEAF, FRAME)
    opening = reopen_frame(restarted)
    assert opening.state == "ready" and opening.revision == 2
    assert open_asset(restarted, 2, "index.html").startswith(b"<!doctype html>")

    counted = usage(scope.area, now=LATER)
    assert counted["bytes"] > 0
    assert counted["frames"][0]["revisions"] == [1, 2]
    assert counted["frames"][0]["ready_revision"] == 2
    assert counted["frames"][0]["drafts"] == ["published", "published"]


def test_f16_a_cancelled_attempt_is_a_durable_state_not_an_absence(task: Path) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    begin_draft(
        scope,
        working_directory=task.parent / "authoring" / task.name / "req-1",
        request_id="req-1",
        authoring=_authoring(),
        focus="a cancelled page",
        assets=ASSETS,
        limits=FrameLimits(),
        now=AT,
    )
    finish_draft(
        scope,
        "req-1",
        state="cancelled",
        detail="the turn was cancelled",
        limits=FrameLimits(),
        now=AT,
    )
    counted = usage(scope.area, now=AT)
    assert counted["frames"][0]["drafts"] == ["cancelled"]
    assert reopen_frame(scope).state == "unavailable"
    assert "draft" in reopen_frame(scope).detail


# --- round 2: the remaining fixed-list properties ------------------------------------------------


def test_f01_r2_a_published_draft_never_answers_a_foreign_comparison(task: Path) -> None:
    assert _publish(task, _record()).published
    scope = frame_scope(task, LEAF, FRAME)
    with pytest.raises(FrameRefused, match="different identity"):
        publish_revision(
            scope,
            request_id="req-1",
            entrypoint="index.html",
            evidence=_evidence(_record(task="foreign-task")),
            supplemental=(),
            created_at=LATER,
            limits=FrameLimits(),
        )


def test_f10_r2_draft_admission_counts_its_own_metadata(task: Path) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    tight = FrameLimits(task_bytes=80, lineage_bytes=80, draft_bytes=4096, bytes_per_revision=4096)
    with pytest.raises(FrameRefused):
        begin_draft(
            scope,
            working_directory=task.parent / "authoring" / task.name / "req-1",
            request_id="req-1",
            authoring=_authoring(),
            focus="a draft whose record alone exceeds the ceiling",
            assets={"index.html": b"<p>x</p>"},
            limits=tight,
            now=AT,
        )
    assert not (scope.frame_dir / "drafts" / "req-1").exists()


def test_f11_r2_an_intermediate_asset_link_is_refused(task: Path, tmp_path: Path) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    external = tmp_path / "outside-assets"
    external.mkdir()
    directory = task.parent / "authoring" / task.name / "req-1"
    (directory / "assets").mkdir(parents=True)
    (directory / "assets").rmdir()
    (directory / "assets").symlink_to(external, target_is_directory=True)
    with pytest.raises(FrameRefused, match="physically inside"):
        begin_draft(
            scope,
            working_directory=task.parent / "authoring" / task.name / "req-1",
            request_id="req-1",
            authoring=_authoring(),
            focus="a linked asset directory",
            assets={"index.html": b"<p>x</p>"},
            limits=FrameLimits(),
            now=AT,
        )
    assert not (external / "index.html").exists()


def test_f11_r2_an_intermediate_revision_link_is_not_served(task: Path, tmp_path: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    assets = scope.frame_dir / "revisions" / "1" / "assets"
    external = tmp_path / "outside-revision"
    external.mkdir()
    (external / "index.html").write_text("foreign bytes")
    for child in sorted(assets.iterdir()):
        child.unlink()
    assets.rmdir()
    assets.symlink_to(external, target_is_directory=True)
    with pytest.raises(FrameRefused, match="physically inside"):
        open_asset(scope, 1, "index.html")
    assert (external / "index.html").read_text() == "foreign bytes"


def test_f12_r2_archive_waits_for_the_publication_lock(
    task: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    lock_path = scope.area.parent / TASK_LOCK_NAME
    attempted = threading.Event()

    @contextmanager
    def observe_lock(path: Path, resource: str) -> Iterator[None]:
        assert path == lock_path
        attempted.set()
        with exclusive_file_lock(path, resource):
            yield

    monkeypatch.setattr(store, "exclusive_file_lock", observe_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with exclusive_file_lock(lock_path, "test holds the task lock"):
            archive = pool.submit(reclaim_task_frames, task, dry_run=False, now=LATER)
            assert attempted.wait(timeout=HANG_GUARD_SECONDS), "archive did not enter its task lock"
            assert not archive.done(), "the archive must wait while the publication lock is held"
        assert archive.result(timeout=HANG_GUARD_SECONDS)["state"] == "reclaimed"


def test_f13_r2_a_corrupt_manifest_is_not_served(task: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    manifest = scope.frame_dir / "revisions" / "1" / "manifest.json"
    document = json.loads(manifest.read_text())
    document["focus"] = "rewritten"
    manifest.write_text(json.dumps(document))
    assert reopen_frame(scope).state == "unavailable"
    with pytest.raises(FrameRefused, match="manifest"):
        open_asset(scope, 1, "index.html")


def test_f13_r2_regeneration_reads_one_snapshot(task: Path) -> None:
    record = _record()
    _publish(task, record)
    scope = frame_scope(task, LEAF, FRAME)
    _publish(task, record, request="req-2", now=LATER)
    request = regeneration_request(scope, record, comparison_owner=OWNER)
    assert request.state == "refused", "the ready revision already binds the selection"
    assert request.stale_revision == 2


def test_f15_r2_an_archived_frame_is_explained_by_the_task_receipt(task: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    assert reclaim_task_frames(task, dry_run=False, now=LATER)["state"] == "reclaimed"
    assert not scope.frame_dir.exists()
    opening = reopen_frame(frame_scope(task, LEAF, FRAME))
    assert opening.state == "unavailable"
    assert "archived" in opening.detail


def test_f15_r2_failures_survive_a_later_deleted_record(task: Path) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    begin_draft(
        scope,
        working_directory=task.parent / "authoring" / task.name / "req-1",
        request_id="req-1",
        authoring=_authoring(),
        focus="a draft",
        assets=ASSETS,
        limits=FrameLimits(),
        now=AT,
    )
    _record_reclamation(
        scope,
        state="partial",
        revisions=(9,),
        digests=(),
        reason="a failed removal",
        now=AT,
        failures=("revision 9: permission denied",),
    )
    _record_reclamation(
        scope,
        state="deleted",
        revisions=(9,),
        digests=(),
        reason="a later removal",
        now=LATER,
    )
    receipt = json.loads((scope.frame_dir / "reclaimed.json").read_text())
    assert receipt["failures"] == ["revision 9: permission denied"]
    assert 9 in receipt["revisions"]


def test_f16_r2_the_largest_store_is_measured_at_its_default_ceiling(
    task: Path, tmp_path: Path
) -> None:
    limits = FrameLimits()
    for number in range(1, limits.revisions_per_lineage + 1):
        _publish(task, _record(), request=f"req-{number}", limits=limits, now=AT)
    scope = frame_scope(task, LEAF, FRAME)
    counted = usage(scope.area, now=LATER)
    lineage = counted["frames"][0]
    assert lineage["revisions"] == list(range(1, limits.revisions_per_lineage + 1))
    assert 0 < lineage["bytes"] <= limits.lineage_bytes

    script = tmp_path / "reopen.py"
    script.write_text(
        "import json,sys\n"
        "from pathlib import Path\n"
        "from agents_remember.application.review_frame_store import frame_scope, reopen_frame\n"
        f"scope = frame_scope(Path({str(task)!r}), {LEAF!r}, {FRAME!r})\n"
        "opening = reopen_frame(scope)\n"
        "print(json.dumps({'state': opening.state, 'revision': opening.revision}))\n"
    )
    completed = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")},
        check=True,
    )
    restarted = json.loads(completed.stdout.strip().splitlines()[-1])
    assert restarted == {"state": "ready", "revision": limits.revisions_per_lineage}


def test_f01_r4_each_operand_field_survives_both_binding_constructors(task: Path) -> None:
    record = _record()
    _publish(task, record)
    scope = frame_scope(task, LEAF, FRAME)
    for side in ("code_base", "code_candidate", "memory_base", "memory_candidate"):
        for field, value in (
            ("repository", "/other/repo"),
            ("tree", "9" * 40),
            ("commit", "8" * 40),
            ("ref", "refs/ar/review/other"),
        ):
            moved = record.model_copy(
                update={side: getattr(record, side).model_copy(update={field: value})}
            )
            selected = FrameBinding.from_record(
                moved, comparison_owner=OWNER, comparison_reference=comparison_reference(moved)
            )
            carried = FrameBinding.from_packet(_evidence(moved).comparison)
            assert selected.side_identity() == carried.side_identity()
            assert selected.conversion_identity() == carried.conversion_identity()
            assert frame_staleness(scope, moved, comparison_owner=OWNER).state == "stale", (
                side,
                field,
            )


def test_f10_r4_publication_reserves_every_final_byte(task: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    actual = usage(scope.area, now=AT)["bytes"]
    shutil.rmtree(scope.frame_dir)
    assert _publish(task, _record(), limits=FrameLimits(lineage_bytes=actual)).published
    assert usage(scope.area, now=AT)["bytes"] == actual
    shutil.rmtree(scope.frame_dir)
    with pytest.raises(FrameRefused, match="lineage"):
        _publish(task, _record(), limits=FrameLimits(lineage_bytes=actual - 1))
    assert not (scope.frame_dir / "ready.json").exists()


def test_f10_r4_the_task_ceiling_includes_other_leaf_and_frame_bytes(task: Path) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    actual = usage(scope.area, now=AT)["bytes"]
    for leaf in (LEAF, "260928-MIK-L103"):
        other = frame_scope(task, leaf, "other")
        with pytest.raises(FrameRefused, match="task"):
            begin_draft(
                other,
                working_directory=task.parent / "authoring" / leaf,
                request_id="next",
                authoring=_authoring("next"),
                focus="another",
                assets=ASSETS,
                limits=FrameLimits(task_bytes=actual + 100),
                now=AT,
            )


def test_f11_r4_no_foreign_manifest_is_read_before_the_guard(
    task: Path, tmp_path: Path, monkeypatch
) -> None:
    _publish(task, _record())
    scope = frame_scope(task, LEAF, FRAME)
    revisions = scope.frame_dir / "revisions"
    outside = tmp_path / "foreign-revisions"
    revisions.rename(outside)
    revisions.symlink_to(outside, target_is_directory=True)
    observed = []
    read = store.read_frame_bytes

    def measured(path, *args):
        if outside in path.resolve().parents:
            observed.append(path)
        return read(path, *args)

    monkeypatch.setattr(store, "read_frame_bytes", measured)
    with pytest.raises(FrameRefused, match="physically inside"):
        open_asset(scope, 1, "index.html")
    assert observed == []


def test_f12_r4_draft_bytes_are_imported_from_the_authoring_folder(
    task: Path, tmp_path: Path
) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    working = tmp_path / "authoring" / "request-1"
    draft = begin_draft(
        scope,
        request_id="req-1",
        authoring=_authoring(),
        focus="a draft",
        working_directory=working,
        assets=ASSETS,
        limits=FrameLimits(),
        now=AT,
    )
    assert draft.working_directory == str(working)
    assert (working / "assets" / "index.html").read_bytes() == ASSETS["index.html"]
    assert not list(scope.frame_dir.rglob("*.html"))
    assert cleanup_frames(scope.area, now=AT)["held"]
    result = publish_revision(
        scope,
        request_id="req-1",
        entrypoint="index.html",
        evidence=_evidence(_record()),
        supplemental=(),
        created_at=AT,
        limits=FrameLimits(),
    )
    assert result.published
    shutil.rmtree(working)
    assert open_asset(scope, 1, "index.html") == ASSETS["index.html"]


def test_f13_r4_a_historical_manifest_keeps_its_publication_anchor(task: Path) -> None:
    _publish(task, _record())
    _publish(task, _record(), request="req-2")
    scope = frame_scope(task, LEAF, FRAME)
    assert open_asset(scope, 1, "index.html") == ASSETS["index.html"]
    path = scope.frame_dir / "revisions" / "1" / "manifest.json"
    changed = json.loads(path.read_bytes())
    changed["focus"] = "foreign history"
    path.write_text(json.dumps(changed))
    with pytest.raises(FrameRefused, match="manifest"):
        open_asset(scope, 1, "index.html")
    assert reopen_frame(scope).state == "ready"


def test_f15_r4_archive_enumerates_after_publication_exclusion(task: Path, monkeypatch) -> None:
    _publish(task, _record())
    lock = store.exclusive_file_lock
    armed = True

    @contextmanager
    def interleaved(path, label):
        nonlocal armed
        if armed:
            armed = False
            publication = _publish(task, _record(), request="req-2")
            assert publication.manifest is not None and publication.manifest.revision == 2
        with lock(path, label):
            yield

    monkeypatch.setattr(store, "exclusive_file_lock", interleaved)
    outcome = reclaim_task_frames(task, now=LATER)
    receipt = json.loads(Path(outcome["receipt"]).read_bytes())
    assert outcome["frames"][0]["revisions"] == [1, 2]
    assert receipt["frames"][0]["revisions"] == [1, 2]
    assert receipt["frames"][0]["ready_revision"] == 2


def test_f16_r4_default_byte_maximum_costs_restart_and_reclamation(task: Path, monkeypatch) -> None:  # noqa: PLR0915 - one measured maximum-store lifecycle
    """Real 64 MiB lineages fill the real 512 MiB task; no sparse files or reported constants."""
    limits = FrameLimits()
    large = {"index.html": b"x" * (limits.bytes_per_revision // 64)}
    large.update(
        {f"asset-{index:02}.css": b"x" * (limits.bytes_per_revision // 64) for index in range(63)}
    )
    small = {path: b"x" for path in large}
    calls = {"writes": 0, "reads": 0, "read_bytes": 0}
    write, read = store.atomic_write_bytes, store.read_frame_bytes

    def measured_write(path, payload):
        calls["writes"] += 1
        return write(path, payload)

    def measured_read(path, *args):
        payload = read(path, *args)
        calls["reads"] += 1
        calls["read_bytes"] += len(payload)
        return payload

    monkeypatch.setattr(store, "atomic_write_bytes", measured_write)
    monkeypatch.setattr(store, "read_frame_bytes", measured_read)

    def populate(record, last):
        for number in range(1, limits.revisions_per_lineage + 1):
            assets = large if number <= 3 else last if number == 8 else small
            result = _publish(task, record, request=f"max-{number:02}", assets=assets)
            assert result.published
            working = task.parent / "authoring" / task.name / record.leaf_id / f"max-{number:02}"
            shutil.rmtree(working)

    # Calibrate metadata through actual publication. Only the last asset's decimal byte-size fields
    # (manifest and draft receipt) change width; its SHA-256 stays the same encoded length.
    populate(_record(), small)
    scope = frame_scope(task, LEAF, FRAME)
    calibrated = usage(scope.area, now=AT)["bytes"]
    fill = limits.lineage_bytes - calibrated + 1
    for _ in range(10):
        adjusted = limits.lineage_bytes - calibrated + 1 - 2 * (len(str(fill)) - 1)
        if adjusted == fill:
            break
        fill = adjusted
    last = {**small, "index.html": b"x" * fill}
    shutil.rmtree(scope.frame_dir)
    samples = []
    calls.update(writes=0, reads=0, read_bytes=0)
    for index in range(limits.task_bytes // limits.lineage_bytes):
        leaf = f"260928-MIK-L{102 + index}"
        populate(_record(leaf=leaf), last)
        leaf_scope = frame_scope(task, leaf, FRAME)
        assert usage(leaf_scope.area, now=AT)["bytes"] == limits.lineage_bytes
        if index in (0, 7):
            paths = [path for path in scope.area.parent.rglob("*") if path.is_file()]
            physical = sum(path.stat().st_size for path in paths)
            records = sum(
                path.name in {"manifest.json", "draft.json", "ready.json"} for path in paths
            )
            samples.append(
                {
                    "lineages": index + 1,
                    "bytes": physical,
                    "paths": len(paths),
                    "records": records,
                    **calls,
                }
            )
    assert samples[0]["bytes"] == limits.lineage_bytes
    assert samples[1]["bytes"] == limits.task_bytes
    assert samples[1]["records"] == 8 * samples[0]["records"]
    assert samples[1]["writes"] == 8 * samples[0]["writes"]

    before = (scope.frame_dir / "ready.json").read_bytes()
    with pytest.raises(FrameRefused, match="lineage"):
        begin_draft(
            scope,
            working_directory=task.parent / "over-lineage",
            request_id="extra",
            authoring=_authoring("extra"),
            focus="one byte",
            assets={"index.html": b"x"},
            limits=limits,
            now=AT,
        )
    other = frame_scope(task, LEAF, "extra")
    with pytest.raises(FrameRefused, match="task"):
        begin_draft(
            other,
            working_directory=task.parent / "over-task",
            request_id="extra",
            authoring=_authoring("extra"),
            focus="one byte",
            assets={"index.html": b"x"},
            limits=limits,
            now=AT,
        )
    for assets, reason in (
        ({**small, "extra.css": b"x"}, "file"),
        ({"index.html": b"x" * (limits.bytes_per_revision + 1)}, "draft"),
    ):
        with pytest.raises(FrameRefused, match=reason):
            begin_draft(
                other,
                working_directory=task.parent / "over-asset",
                request_id="over",
                authoring=_authoring("over"),
                focus="over cap",
                assets=assets,
                limits=limits,
                now=AT,
            )
    assert (scope.frame_dir / "ready.json").read_bytes() == before

    restarted = subprocess.run(
        [
            sys.executable,
            "-c",
            "import agents_remember,json,sys; from pathlib import Path; "
            "from agents_remember.application.review_frame_store import frame_scope,reopen_frame; "
            "s=frame_scope(Path(sys.argv[1]),sys.argv[2],sys.argv[3]); o=reopen_frame(s); "
            "print(json.dumps({'source':agents_remember.__file__,'state':o.state,'revision':o.revision}))",
            str(task),
            LEAF,
            FRAME,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    opened = json.loads(restarted.stdout)
    assert opened["state"] == "ready" and opened["revision"] == 8
    assert opened["source"] == str(Path(store.__file__).parents[1] / "__init__.py")
    preview = reclaim_task_frames(task, dry_run=True, now=LATER)
    assert sum(entry["bytes"] for entry in preview["would_remove"]) == limits.task_bytes
    archived = reclaim_task_frames(task, now=LATER)
    assert sum(entry["bytes"] for entry in archived["removed"]) == limits.task_bytes
    assert reopen_frame(scope).state == "unavailable"
    print(
        json.dumps(
            {
                "default_store_samples": samples,
                "restart": opened,
                "reclaimed_bytes": sum(entry["bytes"] for entry in archived["removed"]),
            }
        )
    )


def test_f16_r4_the_default_manifest_ceiling_is_exact(task: Path) -> None:
    scope = frame_scope(task, LEAF, FRAME)
    limits = FrameLimits()

    def publish(padding):
        collection = EvidenceCollection(
            name="source-inventory-and-linked-locations",
            producer="fixture",
            population="opaque metadata for the manifest byte-bound check",
            state="present",
            count=1,
            items=({"detail": "x" * padding},),
        )
        packet = _evidence(_record())
        packet = packet.model_copy(
            update={
                "collections": tuple(
                    collection if entry.name == collection.name else entry
                    for entry in packet.collections
                )
            }
        )
        begin_draft(
            scope,
            request_id="req-1",
            authoring=_authoring(),
            focus="manifest bound",
            working_directory=task.parent / "manifest-authoring",
            assets=ASSETS,
            limits=limits,
            now=AT,
        )
        return publish_revision(
            scope,
            request_id="req-1",
            entrypoint="index.html",
            evidence=packet,
            supplemental=(),
            created_at=AT,
            limits=limits,
        )

    publish(0)
    manifest = scope.frame_dir / "revisions" / "1" / "manifest.json"
    padding = limits.manifest_bytes - manifest.stat().st_size
    shutil.rmtree(scope.frame_dir)
    assert publish(padding).published
    assert manifest.stat().st_size == limits.manifest_bytes
    shutil.rmtree(scope.frame_dir)
    with pytest.raises(FrameRefused, match="manifest"):
        publish(padding + 1)
    assert not (scope.frame_dir / "ready.json").exists()


def test_f10_r4_reclamation_metadata_is_in_the_reservation(task: Path) -> None:
    def populate(limits):
        for number in range(1, 10):
            _publish(task, _record(), request=f"req-{number}", limits=limits)

    populate(FrameLimits())
    scope = frame_scope(task, LEAF, FRAME)
    actual = usage(scope.area, now=AT)["bytes"]
    assert _revisions(scope) == list(range(2, 10))
    assert (scope.frame_dir / "reclaimed.json").is_file()
    shutil.rmtree(scope.frame_dir)
    populate(FrameLimits(lineage_bytes=actual))
    assert usage(scope.area, now=AT)["bytes"] == actual
    shutil.rmtree(scope.frame_dir)
    with pytest.raises(FrameRefused, match="lineage"):
        populate(FrameLimits(lineage_bytes=actual - 1))
    assert reopen_frame(scope).revision == 8
