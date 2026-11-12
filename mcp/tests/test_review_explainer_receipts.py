"""The explainer's packet receipts: the leaf's packets, its document, its reference folder.

One case per promise of MIK-R185 v2 items 4 and 5: every requirement file the leaf names is
receipted by its bytes, the leaf's own document is admitted while another leaf's is not, the
reference folder is receipted when it exists, a missing source is an ``unavailable`` receipt rather
than a refusal, and an agreed revision's manifest joins the packet.
"""

from __future__ import annotations

import hashlib
import json
from functools import partial
from pathlib import Path

from agents_remember.application import review_frame_store
from agents_remember.application.review_evidence_packet import SideState, build_evidence_packet
from agents_remember.application.review_explainer_receipts import (
    compose_receipts,
    requirement_paths,
)
from agents_remember.application.review_frame_store import (
    begin_draft,
    frame_scope,
    publish_revision,
)
from agents_remember.application.review_supplemental_evidence import (
    admit_supplemental,
    capture_supplemental,
    reread_supplemental,
)
from agents_remember.models.knowledge.review_evidence import EvidenceMap
from agents_remember.models.knowledge.review_frame import FrameAuthoring, FrameLimits
from agents_remember.models.knowledge.review_trees import (
    ReviewTreeComparisonRecord,
    ReviewTreeSide,
)
from agents_remember.models.task_intent import ApprovedRequirementPacketRef
from agents_remember.tasks.document import IntentReview, IntentReviewRevision

LEAF = "260928-MIK-L183"
SLUG = "183_the-review-pages-explainer"
TASK_DOCUMENT_JSON_ID = "task-document"
TASK_DOCUMENT_MD_ID = "task-document-md"


def _task(tmp_path: Path) -> Path:
    root = tmp_path / "tasks" / "agents-remember" / "260928_maintained-invariant-knowledge"
    (root / "requirements").mkdir(parents=True)
    (root / "notes" / "reference" / LEAF).mkdir(parents=True)
    (root / "requirements" / "MIK-R106-v2-the-explainer.md").write_text("# R106\n")
    (root / "requirements" / "MIK-R186-v1-the-environment.md").write_text("# R186\n")
    (root / f"{SLUG}.json").write_text(json.dumps({"id": LEAF, "slug": SLUG}))
    (root / f"{SLUG}.md").write_text("# the leaf\n")
    (root / "notes" / "reference" / LEAF / "picks.md").write_text("a picked reference\n")
    return root


def _document(**overrides) -> dict:
    document = {
        "id": LEAF,
        "slug": SLUG,
        "requirements": [
            "[MIK-R106@v2](requirements/MIK-R106-v2-the-explainer.md) — owned",
            "[MIK-R186@v1](requirements/MIK-R186-v1-the-environment.md) — owned",
        ],
    }
    document.update(overrides)
    return document


def test_the_requirement_paths_are_the_ones_the_document_names() -> None:
    assert requirement_paths(_document()) == (
        "requirements/MIK-R106-v2-the-explainer.md",
        "requirements/MIK-R186-v1-the-environment.md",
    )
    typed_and_multi = _document(
        requirements=[
            ApprovedRequirementPacketRef(
                path="requirements/MIK-R102-v2-evidence.md",
                stableId="MIK-R102",
                version="v2",
            ),
            {
                "kind": "approved-requirement-packet",
                "path": "requirements/MIK-R103-v2-frames.md",
                "stableId": "MIK-R103",
                "version": "v2",
            },
            (
                "[MIK-R104@v3](requirements/MIK-R104-v3-host.md) paired with "
                "[MIK-R185@v2](requirements/MIK-R185-v2-prepare.md)"
            ),
            *[f"[MIK-R{n}@v1](requirements/MIK-R{n}-v1.md)" for n in range(200, 260)],
        ]
    )
    paths = requirement_paths(typed_and_multi)
    assert paths[:4] == (
        "requirements/MIK-R102-v2-evidence.md",
        "requirements/MIK-R103-v2-frames.md",
        "requirements/MIK-R104-v3-host.md",
        "requirements/MIK-R185-v2-prepare.md",
    )
    assert len(paths) == 64


def test_every_packet_document_and_reference_file_is_receipted(tmp_path: Path) -> None:
    root = _task(tmp_path)

    receipts = compose_receipts(root, _document(), LEAF)

    sources = {receipt.canonical_source for receipt in receipts}
    assert sources == {
        "requirements/MIK-R106-v2-the-explainer.md",
        "requirements/MIK-R186-v1-the-environment.md",
        f"{SLUG}.json",
        f"{SLUG}.md",
        f"notes/reference/{LEAF}/picks.md",
    }
    ids_by_source = {receipt.canonical_source: receipt.receipt_id for receipt in receipts}
    assert ids_by_source[f"{SLUG}.json"] == TASK_DOCUMENT_JSON_ID
    assert ids_by_source[f"{SLUG}.md"] == TASK_DOCUMENT_MD_ID
    assert len({receipt.receipt_id for receipt in receipts}) == len(receipts)
    assert all(receipt.read_state == "present" for receipt in receipts)
    assert all(receipt.digest for receipt in receipts)


def test_a_missing_packet_file_is_an_unavailable_receipt(tmp_path: Path) -> None:
    root = _task(tmp_path)
    document = _document(requirements=["[MIK-R191@v1](requirements/MIK-R191-not-here.md) — owned"])

    receipts = compose_receipts(root, document, LEAF)

    missing = [receipt for receipt in receipts if receipt.canonical_source.endswith("not-here.md")]
    assert missing and missing[0].read_state == "unavailable"
    assert missing[0].digest is None
    # The build goes on: the other sources are still receipted.
    assert any(receipt.read_state == "present" for receipt in receipts)


def test_a_document_that_is_not_this_leafs_admits_no_document_files(tmp_path: Path) -> None:
    root = _task(tmp_path)

    receipts = compose_receipts(root, _document(id="260928-MIK-L184"), LEAF)

    refused = [
        receipt
        for receipt in receipts
        if receipt.receipt_id in {TASK_DOCUMENT_JSON_ID, TASK_DOCUMENT_MD_ID}
    ]
    assert len(refused) == 2
    assert all(receipt.read_state == "unavailable" for receipt in refused)


def _publish_agreed_revision(root: Path) -> tuple[int, str]:
    comparison = ReviewTreeComparisonRecord(
        task_id="260928_maintained-invariant-knowledge",
        leaf_id=LEAF,
        number=0,
        code_base=ReviewTreeSide(repository="/repo/code", tree="a" * 40),
        code_candidate=ReviewTreeSide(repository="/repo/code", tree="a" * 40),
        memory_base=ReviewTreeSide(repository="/repo/memory", tree="b" * 40),
        memory_candidate=ReviewTreeSide(repository="/repo/memory", tree="b" * 40),
        recorded_at="planned-master-tip",
    )
    evidence = build_evidence_packet(
        comparison,
        repository_id="agents-remember",
        scope="planned",
        question="what changes?",
        code_base=SideState("available"),
        code_candidate=SideState("available"),
        memory_before=SideState("available", index_state="complete"),
        memory_after=SideState("available", index_state="complete"),
        evidence_map=EvidenceMap(coverage="planned"),
        recorded_at="planned-master-tip",
    )
    scope = frame_scope(root, LEAF, "explainer")
    html = b"<!doctype html><title>agreed</title><p>planned</p>"
    begin_draft(
        scope,
        working_directory=root.parent / "authoring-agreed",
        request_id="req-agreed",
        authoring=FrameAuthoring(request_id="req-agreed", started_at="2026-10-19T00:00:00Z"),
        focus="planned",
        assets={"index.html": html},
        limits=FrameLimits(),
        now="2026-10-19T00:00:00Z",
    )
    pub = publish_revision(
        scope,
        request_id="req-agreed",
        entrypoint="index.html",
        evidence=evidence,
        supplemental=(),
        created_at="2026-10-19T00:00:00Z",
        limits=FrameLimits(),
    )
    assert pub.manifest is not None
    # The go records the digest of the manifest bytes (MIK-R189 rule 8): the strict resolver
    # verifies the retained manifest against it, not against an asset's bytes.
    manifest_path = scope.frame_dir / "revisions" / str(pub.manifest.revision) / "manifest.json"
    manifest_sha = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    return pub.manifest.revision, manifest_sha


def test_an_agreed_revision_adds_its_manifest(tmp_path: Path) -> None:
    root = _task(tmp_path)
    rev_num, html_sha = _publish_agreed_revision(root)
    review = IntentReview(
        state="go",
        decisionRef="D373",
        agreedRevision=IntentReviewRevision(
            revisionId=f"rev-{rev_num}",
            sha256=html_sha,
            packetVersions=["MIK-R189@v2"],
        ),
    )
    document = _document(intentReview=review.model_dump(by_alias=True))

    resolver = getattr(review_frame_store, "resolve_agreed_revision", None)
    assert resolver is not None
    resolved = resolver(root, document, leaf_id=LEAF)
    assert resolved is not None and resolved.revision == rev_num and resolved.digest_verified

    receipts = compose_receipts(root, document, LEAF)

    agreed = [receipt for receipt in receipts if receipt.receipt_id == "agreed-manifest"]
    assert agreed and agreed[0].read_state == "present"
    assert agreed[0].canonical_source == resolved.manifest_path.relative_to(root).as_posix()

    mismatched = _document(
        intentReview={
            "state": "go",
            "agreedRevision": {
                "revisionId": f"rev-{rev_num}",
                "sha256": "f" * 64,
                "packetVersions": ["MIK-R189@v2"],
            },
        }
    )
    bad_receipts = compose_receipts(root, mismatched, LEAF)
    bad_agreed = [receipt for receipt in bad_receipts if receipt.receipt_id == "agreed-manifest"]
    assert bad_agreed and bad_agreed[0].read_state == "unavailable"
    assert bad_agreed[0].digest is None


def test_every_named_source_is_read(tmp_path: Path) -> None:
    root = _task(tmp_path)
    rev_num, html_sha = _publish_agreed_revision(root)
    for number in range(80):
        (root / "requirements" / f"MIK-R{number}.md").write_text(f"# R{number}\n")
    for number in range(35):
        (root / "notes" / "reference" / LEAF / f"ref-{number:02d}.md").write_text(f"ref {number}\n")
    document = _document(
        requirements=[
            f"[MIK-R{number}](requirements/MIK-R{number}.md) — owned" for number in range(80)
        ],
        intentReview={
            "state": "go",
            "agreedRevision": {
                "revisionId": f"rev-{rev_num}",
                "sha256": html_sha,
                "packetVersions": ["MIK-R189@v2"],
            },
        },
    )

    receipts = compose_receipts(root, document, LEAF)

    # Every named packet (80), both own task documents (2), all reference files (36), and the
    # agreed manifest (1) are accounted for without silent truncation.
    assert len(receipts) == 80 + 2 + 36 + 1
    by_id = {receipt.receipt_id: receipt for receipt in receipts}
    assert by_id[TASK_DOCUMENT_JSON_ID].read_state == "present"
    assert by_id[TASK_DOCUMENT_MD_ID].read_state == "present"
    assert by_id["agreed-manifest"].read_state == "present"
    # Ruling 6085456d, addendum (a): no ceiling leaves a source unread. The old ceiling was 48 of
    # 119; every one of these 119 is read now, and none carries a truncated-read limitation.
    assert all(receipt.read_state == "present" for receipt in receipts)
    assert not any("truncated-read" in (receipt.limitation or "") for receipt in receipts)
    # No source is dropped: every named path is receipted, and what was not read says so itself.
    expected_sources = {
        *(f"requirements/MIK-R{number}.md" for number in range(80)),
        f"{SLUG}.json",
        f"{SLUG}.md",
        *(f"notes/reference/{LEAF}/ref-{number:02d}.md" for number in range(35)),
        *(f"notes/reference/{LEAF}/picks.md" for _ in range(1)),
    }
    assert {receipt.canonical_source for receipt in receipts} >= expected_sources
    assert all(receipt.digest for receipt in receipts), (
        "a source that was read carries the digest of the bytes it read"
    )


def test_typed_and_legacy_refs_are_consumed_and_each_named_source_is_receipted(
    tmp_path: Path,
) -> None:
    """E05: the same enumeration reads typed packet refs and legacy strings; none is dropped."""
    root = _task(tmp_path)
    (root / "requirements" / "MIK-R104-v3-host.md").write_text("# host\n")
    (root / "requirements" / "MIK-R105-v2-confinement.md").write_text("# confinement\n")
    document = _document(
        requirements=[
            ApprovedRequirementPacketRef(
                path="requirements/MIK-R104-v3-host.md",
                stableId="MIK-R104",
                version="v3",
            ),
            "requirements/MIK-R105-v2-confinement.md",
        ]
    )

    assert requirement_paths(document) == (
        "requirements/MIK-R104-v3-host.md",
        "requirements/MIK-R105-v2-confinement.md",
    )
    receipts = compose_receipts(root, document, LEAF)
    packet_sources = {
        receipt.canonical_source: receipt.read_state
        for receipt in receipts
        if receipt.canonical_source.startswith("requirements/")
    }
    assert packet_sources == {
        "requirements/MIK-R104-v3-host.md": "present",
        "requirements/MIK-R105-v2-confinement.md": "present",
    }
    assert len({receipt.receipt_id for receipt in receipts}) == len(receipts)


def test_the_leaf_document_admission_is_by_exact_names_never_the_task_root(tmp_path: Path) -> None:
    """E07: only requirements/, notes/ and the exact own two filenames are admitted."""
    root = _task(tmp_path)
    (root / "task").mkdir()
    (root / "task" / "foreign.json").write_text(json.dumps({"id": "another-leaf"}))

    # A synthetic ``task/`` root admits nothing, with or without the leaf context.
    for leaf_document in (None, SLUG):
        refused = admit_supplemental(root, "task/foreign.json", leaf_document=leaf_document)
        assert refused.state == "refused"
        assert refused.refusal_code == "foreign-source"
    # The own two filenames are admitted only when the leaf context names them.
    assert admit_supplemental(root, f"{SLUG}.json").state == "refused"
    assert admit_supplemental(root, f"{SLUG}.json", leaf_document=SLUG).state == "present"
    assert admit_supplemental(root, f"{SLUG}.md", leaf_document=SLUG).state == "present"
    # Another leaf's document and the developer direction stay out.
    (root / "260928-MIK-L184.json").write_text(json.dumps({"id": "260928-MIK-L184"}))
    assert admit_supplemental(root, "260928-MIK-L184.json", leaf_document=SLUG).state == "refused"
    assert admit_supplemental(root, "DEVELOPER-DIRECTION.md", leaf_document=SLUG).state == "refused"


def test_an_own_task_document_receipt_rereads_through_the_admission(tmp_path: Path) -> None:
    """E08: unchanged stays present, a change is a successor digest, loss and foreign stay refused."""
    root = _task(tmp_path)
    capture = partial(
        capture_supplemental,
        root,
        receipt_id=TASK_DOCUMENT_JSON_ID,
        capture_identity="the explainer's build",
        establishes="the leaf's own task document",
        producer=f"leaf {LEAF}",
        leaf_document=SLUG,
    )
    original = capture(f"{SLUG}.json")
    assert original.read_state == "present" and original.digest

    unchanged = reread_supplemental(
        original,
        task_root=root,
        receipt_id=TASK_DOCUMENT_JSON_ID,
        capture_identity="the explainer's Update",
        leaf_document=SLUG,
    )
    assert unchanged.read_state == "present"
    assert unchanged.digest == original.digest

    (root / f"{SLUG}.json").write_text(json.dumps({"id": LEAF, "slug": SLUG, "status": "go"}))
    changed = reread_supplemental(
        original,
        task_root=root,
        receipt_id=TASK_DOCUMENT_JSON_ID,
        capture_identity="the explainer's Update",
        leaf_document=SLUG,
    )
    assert changed.read_state == "changed"
    assert changed.digest and changed.digest != original.digest
    assert (
        original.digest
        == hashlib.sha256(json.dumps({"id": LEAF, "slug": SLUG}).encode()).hexdigest()
    )

    (root / f"{SLUG}.json").unlink()
    missing = reread_supplemental(
        original,
        task_root=root,
        receipt_id=TASK_DOCUMENT_JSON_ID,
        capture_identity="the explainer's Update",
        leaf_document=SLUG,
    )
    assert missing.read_state == "unavailable"
    assert missing.digest is None

    # A foreign leaf's document at the same stem is not this leaf's bytes: the reread stays refused.
    (root / f"{SLUG}.json").write_text(json.dumps({"id": "260928-MIK-L184"}))
    foreign = reread_supplemental(
        original,
        task_root=root,
        receipt_id=TASK_DOCUMENT_JSON_ID,
        capture_identity="the explainer's Update",
    )
    assert foreign.read_state == "unavailable"
    assert foreign.digest is None

    # The rendered Markdown format goes through the same admission and reread identity.
    markdown = capture_supplemental(
        root,
        f"{SLUG}.md",
        receipt_id=TASK_DOCUMENT_MD_ID,
        capture_identity="the explainer's build",
        establishes="the leaf's own rendered document",
        producer=f"leaf {LEAF}",
        leaf_document=SLUG,
    )
    assert markdown.read_state == "present" and markdown.digest
    (root / f"{SLUG}.md").write_text("# the leaf, edited\n")
    md_changed = reread_supplemental(
        markdown,
        task_root=root,
        receipt_id=TASK_DOCUMENT_MD_ID,
        capture_identity="the explainer's Update",
        leaf_document=SLUG,
    )
    assert md_changed.read_state == "changed"
    assert md_changed.digest and md_changed.digest != markdown.digest
