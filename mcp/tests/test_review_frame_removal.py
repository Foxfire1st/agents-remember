"""MIK-R103 v2 item 8: the developer's removal of retained revisions.

The "remove old revisions" control of MIK-R106 v2 rule 6 is one store act, so these cases drive it
against a real task directory and read the bytes it left:

1. a chosen revision is deleted, receipted, and its number is never issued again;
2. the pinned agreed revision -- ``intentReview.agreedRevision`` in the leaf's task document -- is
   refused even inside a batch, and stays readable when its neighbours go;
3. a frame that does not exist and a number that is not retained are refused by name;
4. a revision a retained revision's recorded regeneration lineage names is refused;
5. what remains still reads: manifest, binding, parent chain, served asset and the ready pointer;
6. a second removal of the same number refuses, and the first receipt still explains it;
7. retention has no count bound and the 16 MiB per-revision ceiling still refuses one revision;
8. a removal while a publication holds the store's lock answers a typed refusal, not a hang.

Everything is local: no model, no network, no role.
"""

from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from agents_remember.application.review_evidence_packet import (
    COMPARISON_OWNER,
    SideState,
    build_evidence_packet,
)
from agents_remember.application.review_evidence_read import DEFAULT_READ_LIMITS
from agents_remember.application.review_frame_store import (
    TASK_LOCK_NAME,
    FrameRefused,
    begin_draft,
    frame_scope,
    open_asset,
    publish_revision,
    remove_revisions,
    reopen_frame,
    revision_manifest,
    usage,
)
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.models.knowledge.review_evidence import (
    ClaimBasis,
    EvidenceClaim,
    EvidenceMap,
)
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameLimits
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember_test_support.testing.waits import HANG_GUARD_SECONDS

AT = "2026-10-18T00:00:00Z"
LATER = "2026-10-18T06:00:00Z"
TASK = "260928_maintained-invariant-knowledge"
LEAF = "260928-MIK-L183"
FRAME = "explainer"
OWNER = "agents-remember"

ASSETS = {
    "index.html": b"<!doctype html><title>frame</title><p>comparison</p>",
    "app.css": b"body{font:16px monospace}",
}


def _record(number: int = 3) -> ReviewTreeComparisonRecord:
    return ReviewTreeComparisonRecord(
        task_id=TASK,
        leaf_id=LEAF,
        number=number,
        code_base=ReviewTreeSide(repository="/repo/code", tree="a" * 40),
        code_candidate=ReviewTreeSide(repository="/repo/code", tree="b" * 40),
        memory_base=ReviewTreeSide(repository="/repo/memory", tree="c" * 40),
        memory_candidate=ReviewTreeSide(repository="/repo/memory", tree="d" * 40),
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


@pytest.fixture()
def task(tmp_path: Path) -> Path:
    root = tmp_path / "task"
    (root / "notes" / "reports").mkdir(parents=True)
    return root


def _authoring(request: str) -> FrameAuthoring:
    return FrameAuthoring(request_id=request, session_id="session-1", started_at=AT)


def _publish(
    task_root: Path,
    *,
    request: str,
    assets: dict[str, bytes] | None = None,
    limits: FrameLimits | None = None,
    ancestry: tuple[int, ...] = (),
):
    """Publish one revision of the leaf's explainer frame, as the page's builder would."""

    scope = frame_scope(task_root, LEAF, FRAME)
    begin_draft(
        scope,
        working_directory=task_root.parent / "authoring" / task_root.name / request,
        request_id=request,
        authoring=_authoring(request),
        focus="the selected comparison",
        assets=ASSETS if assets is None else assets,
        limits=limits or FrameLimits(),
        now=AT,
    )
    return publish_revision(
        scope,
        request_id=request,
        entrypoint="index.html",
        evidence=_evidence(_record()),
        supplemental=(),
        created_at=AT,
        limits=limits or FrameLimits(),
        asset_ancestry=ancestry,
    )


def _on_disk(scope) -> list[int]:
    """The revision numbers actually present, read from the frame's own directories."""

    root = scope.frame_dir / "revisions"
    return sorted(int(child.name) for child in root.iterdir() if child.name.isdigit())


def _pin(task_root: Path, number: int, *, sha256: str | None = None) -> None:
    """Write the pin the architect writes at the go, using the strict native agreement schema."""

    html_sha = sha256 or hashlib.sha256(ASSETS["index.html"]).hexdigest()
    (task_root / f"{LEAF}.json").write_text(
        json.dumps(
            {
                "id": LEAF,
                "slug": "the-review-pages-explainer",
                "intentReview": {
                    "state": "go",
                    "decisionRef": "D373",
                    "agreedRevision": {
                        "revisionId": f"rev-{number}",
                        "sha256": html_sha,
                        "packetVersions": ["MIK-R103@v2"],
                    },
                },
            }
        )
    )


def _receipt(scope) -> dict:
    return json.loads((scope.frame_dir / "reclaimed.json").read_text())


# --- 1: the developer's removal, its receipt and its number --------------------------------------


def test_a_chosen_revision_is_removed_receipted_and_its_number_is_never_reissued(
    task: Path,
) -> None:
    for request in ("req-1", "req-2", "req-3"):
        assert _publish(task, request=request).published
    scope = frame_scope(task, LEAF, FRAME)
    third = revision_manifest(scope.frame_dir, 3)
    assert third is not None

    # The removal takes the middle revision and the ready one in one call.
    removed = remove_revisions(scope, revisions=(3, 2), now=LATER)

    assert removed.frame_id == FRAME
    assert removed.removed == (2, 3)
    assert removed.retained == (1,)
    assert removed.pinned is None
    assert removed.bytes_freed > 0
    assert _on_disk(scope) == [1]
    assert revision_manifest(scope.frame_dir, 2) is None
    # The receipt is written before and after the bytes go, and names the artifact digest lost.
    receipt = _receipt(scope)
    assert receipt["state"] == "deleted"
    assert receipt["revisions"] == [2, 3]
    assert third.artifact_digest in receipt["manifest_digests"]
    assert "revision selector" in receipt["reason"]
    assert not (scope.frame_dir / "revisions" / "3").exists()
    # The ready pointer no longer names a revision this store does not hold.
    assert json.loads((scope.frame_dir / "ready.json").read_text())["revision"] == 1
    assert reopen_frame(scope).revision == 1

    # The number is not issued again: the next publication lands above every number this lineage
    # ever published, including the removed ready one that a pointer alone would have re-issued.
    published = _publish(task, request="req-4")
    assert published.published and published.manifest is not None
    assert published.manifest.revision == 4
    assert _on_disk(scope) == [1, 4]


# --- 2: the pin -----------------------------------------------------------------------------------


def test_the_pinned_agreed_revision_is_never_removed_and_still_reads(task: Path) -> None:
    for request in ("req-1", "req-2"):
        assert _publish(task, request=request).published
    scope = frame_scope(task, LEAF, FRAME)
    # The go records the digest of the manifest bytes (MIK-R189 rule 8), after the page exists.
    manifest_bytes = (scope.frame_dir / "revisions" / "1" / "manifest.json").read_bytes()
    _pin(task, 1, sha256=hashlib.sha256(manifest_bytes).hexdigest())

    with pytest.raises(FrameRefused) as alone:
        remove_revisions(scope, revisions=(1,))
    assert alone.value.reason == "revision-pinned"
    assert "agreed revision" in alone.value.detail

    # The same number inside a batch refuses the whole request: nothing is deleted first.
    with pytest.raises(FrameRefused) as batch:
        remove_revisions(scope, revisions=(1, 2))
    assert batch.value.reason == "revision-pinned"
    assert _on_disk(scope) == [1, 2]
    assert not (scope.frame_dir / "reclaimed.json").exists()

    # The other revision goes, the pin is answered, and the pinned page is what reopens.
    removed = remove_revisions(scope, revisions=(2,), now=LATER)
    assert removed.removed == (2,)
    assert removed.retained == (1,)
    assert removed.pinned == 1
    advised = revision_manifest(scope.frame_dir, 1)
    assert advised is not None and advised.binding.comparison_reference
    assert reopen_frame(scope).state == "ready"
    assert reopen_frame(scope).revision == 1


# --- 3: a frame and a number that are not there ---------------------------------------------------


def test_a_missing_frame_and_an_unknown_number_are_refused_by_name(task: Path) -> None:
    absent = frame_scope(task, LEAF, FRAME)
    with pytest.raises(FrameRefused) as no_frame:
        remove_revisions(absent, revisions=(1,))
    assert no_frame.value.reason == "frame-not-found"
    assert not absent.frame_dir.exists()

    assert _publish(task, request="req-1").published
    with pytest.raises(FrameRefused) as unknown:
        remove_revisions(absent, revisions=(1, 7))
    assert unknown.value.reason == "revision-not-retained"
    assert "7" in unknown.value.detail
    assert _on_disk(absent) == [1], "a request naming one unknown number deletes nothing"
    assert not (absent.frame_dir / "reclaimed.json").exists()

    with pytest.raises(FrameRefused) as nothing_named:
        remove_revisions(absent, revisions=())
    assert nothing_named.value.reason == "revision-request-invalid"


# --- 4: a revision a retained lineage names -------------------------------------------------------


def test_a_revision_a_retained_lineage_names_is_refused(task: Path) -> None:
    assert _publish(task, request="req-1").published
    assert _publish(task, request="req-2").published
    # Revision 3 was regenerated from revision 1, and says so in its own manifest.
    assert _publish(task, request="req-3", ancestry=(1,)).published
    scope = frame_scope(task, LEAF, FRAME)

    with pytest.raises(FrameRefused) as needed:
        remove_revisions(scope, revisions=(1,))
    assert needed.value.reason == "revision-lineage-needed"
    assert "revision 3" in needed.value.detail
    assert _on_disk(scope) == [1, 2, 3]
    assert not (scope.frame_dir / "reclaimed.json").exists()

    # A revision nothing names in its lineage is removable in the same frame.
    assert remove_revisions(scope, revisions=(2,), now=LATER).removed == (2,)


# --- 5: what remains still reads ------------------------------------------------------------------


def test_the_remaining_lineage_still_reads_after_a_removal(task: Path) -> None:
    for request in ("req-1", "req-2", "req-3"):
        assert _publish(task, request=request).published
    scope = frame_scope(task, LEAF, FRAME)
    assert remove_revisions(scope, revisions=(1,), now=LATER).removed == (1,)

    second = revision_manifest(scope.frame_dir, 2)
    third = revision_manifest(scope.frame_dir, 3)
    assert second is not None and third is not None
    # Every remaining revision keeps its binding, and its parent still names what it followed --
    # a parent that is no longer retained answers as absent, never as an unreadable chain.
    assert second.binding.same_comparison(third.binding)
    assert second.binding.comparison_owner == COMPARISON_OWNER
    assert (second.parent_revision, third.parent_revision) == (1, 2)
    assert revision_manifest(scope.frame_dir, 1) is None

    assert open_asset(scope, 2, "index.html") == ASSETS["index.html"]
    assert open_asset(scope, 3, "index.html") == ASSETS["index.html"]
    opening = reopen_frame(scope)
    assert opening.state == "ready" and opening.revision == 3
    counted = usage(scope.area, now=LATER)
    assert counted["frames"][0]["revisions"] == [2, 3]
    assert counted["frames"][0]["ready_revision"] == 3


# --- 6: the same number is removed once -----------------------------------------------------------


def test_a_second_removal_of_the_same_number_refuses(task: Path) -> None:
    assert _publish(task, request="req-1").published
    assert _publish(task, request="req-2").published
    scope = frame_scope(task, LEAF, FRAME)
    assert remove_revisions(scope, revisions=(2,), now=LATER).removed == (2,)
    first = _receipt(scope)

    with pytest.raises(FrameRefused) as again:
        remove_revisions(scope, revisions=(2,), now=LATER)
    assert again.value.reason == "revision-not-retained"
    assert _on_disk(scope) == [1]
    # The refusal wrote nothing: the first receipt is still the one that explains the loss.
    assert _receipt(scope) == first


# --- 7: retention and the per-revision ceiling still hold ----------------------------------------


def test_retention_and_the_per_revision_ceiling_still_hold_after_a_removal(task: Path) -> None:
    for number in range(1, 11):
        assert _publish(task, request=f"req-{number}").published
    scope = frame_scope(task, LEAF, FRAME)
    removed = remove_revisions(scope, revisions=(2,), now=LATER)

    # Nine revisions remain: the count is still not a ceiling, and removal reclaimed one revision
    # only -- nothing was reclaimed silently beside it.
    assert removed.retained == (1, 3, 4, 5, 6, 7, 8, 9, 10)
    assert len(removed.retained) > 8
    assert _receipt(scope)["revisions"] == [2]

    # The 16 MiB per-revision ceiling still refuses that one revision -- and it is not a refusal of
    # the task total, so the removal control is not offered for it (v2 item 8).
    assert FrameLimits().bytes_per_revision == 16 * 1024 * 1024
    ready_before = json.loads((scope.frame_dir / "ready.json").read_text())
    big = {"index.html": b"x" * (FrameLimits().bytes_per_revision + 1)}
    scope_root = task.parent / "over-revision"
    begin_draft(
        scope,
        working_directory=scope_root,
        request_id="req-big",
        authoring=_authoring("req-big"),
        focus="a revision past its own ceiling",
        assets=big,
        limits=FrameLimits(draft_bytes=len(big["index.html"])),
        now=LATER,
    )
    with pytest.raises(FrameRefused) as too_large:
        publish_revision(
            scope,
            request_id="req-big",
            entrypoint="index.html",
            evidence=_evidence(_record()),
            supplemental=(),
            created_at=LATER,
            limits=FrameLimits(),
        )
    assert too_large.value.reason == "revision-too-large", (
        "the refusal names the per-revision ceiling, not the task total"
    )
    assert "revision" in too_large.value.detail
    assert "revision selector" not in too_large.value.detail, (
        "removing other revisions does not help a revision past its own ceiling, so the control is "
        "not offered for it"
    )
    # Nothing of the prior state went: the ready pointer and every retained revision stay put.
    assert _on_disk(scope) == [1, 3, 4, 5, 6, 7, 8, 9, 10]
    assert json.loads((scope.frame_dir / "ready.json").read_text()) == ready_before
    assert reopen_frame(scope).revision == ready_before["revision"]

    # The draft admission refuses the same bytes by name at the authoring step: a ceiling refusal
    # is typed end to end, at whatever step first measures the attempt.
    with pytest.raises(FrameRefused) as draft_refused:
        begin_draft(
            scope,
            working_directory=task.parent / "over-draft",
            request_id="req-big-2",
            authoring=_authoring("req-big-2"),
            focus="a draft past its own ceiling",
            assets=big,
            limits=FrameLimits(),
            now=LATER,
        )
    assert draft_refused.value.reason == "draft-too-large"


# --- 8: the lock ----------------------------------------------------------------------------------


def test_a_removal_under_a_held_lock_refuses_instead_of_waiting(task: Path) -> None:
    assert _publish(task, request="req-1").published
    assert _publish(task, request="req-2").published
    scope = frame_scope(task, LEAF, FRAME)
    lock_path = scope.area.parent / TASK_LOCK_NAME

    with ThreadPoolExecutor(max_workers=1) as pool:
        with exclusive_file_lock(lock_path, "test holds the store lock"):
            attempt = pool.submit(remove_revisions, scope, revisions=(1,), lock_wait_seconds=0.05)
            with pytest.raises(FrameRefused) as busy:
                attempt.result(timeout=HANG_GUARD_SECONDS)
            assert busy.value.reason == "revision-removal-busy"
            assert "nothing was removed" in busy.value.detail
        assert _on_disk(scope) == [1, 2]
        assert not (scope.frame_dir / "reclaimed.json").exists()

    # The same removal, once the lock is free, goes through: the refusal was the lock alone.
    assert remove_revisions(scope, revisions=(1,), now=LATER).removed == (1,)
