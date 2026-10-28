"""Supplemental evidence admission preserves whole source identity and re-read authority."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agents_remember.application import review_supplemental_evidence
from agents_remember.application.review_supplemental_evidence import (
    admit_supplemental,
    capture_supplemental,
    reread_supplemental,
)

LEAF = "260928-MIK-L102"


def _task_root(tmp_path: Path) -> Path:
    root = tmp_path / "task"
    (root / "requirements").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "requirements" / "MIK-R102.md").write_text("# R102\n")
    return root


def test_f06_a_supplemental_receipt_binds_the_bytes_it_admits(tmp_path: Path) -> None:
    root = _task_root(tmp_path)
    receipt = capture_supplemental(
        root,
        "requirements/MIK-R102.md",
        receipt_id="sup-1",
        capture_identity="capture-1",
        establishes="the approved packet",
        producer="architect",
        authority="approved",
        source_version="v1",
    )
    assert receipt.read_state == "present"
    assert receipt.canonical_source == "requirements/MIK-R102.md"
    assert receipt.digest is not None and receipt.byte_size == len("# R102\n")

    moved = reread_supplemental(
        receipt,
        task_root=root,
        receipt_id="sup-2",
        capture_identity="capture-2",
        source_version="v2",
    )
    assert moved.read_state == "present"
    assert moved.digest == receipt.digest


def test_f06_a_changed_source_answers_a_successor_and_never_reuses_the_old_identity(
    tmp_path: Path,
) -> None:
    root = _task_root(tmp_path)
    first = capture_supplemental(
        root,
        "requirements/MIK-R102.md",
        receipt_id="sup-1",
        capture_identity="capture-1",
        establishes="the approved packet",
        authority="approved",
        source_version="v1",
    )
    (root / "requirements" / "MIK-R102.md").write_text("# R102, revised\n")
    successor = reread_supplemental(
        first, task_root=root, receipt_id="sup-2", capture_identity="capture-2"
    )
    assert successor.read_state == "changed"
    assert successor.digest != first.digest
    assert successor.canonical_source == first.canonical_source
    assert successor.source_version is None, "a version the new bytes do not carry is not copied"
    assert successor.limitation is not None


def test_f06_a_foreign_source_is_refused_and_an_inside_link_resolves(tmp_path: Path) -> None:
    """Rule 2: a link that stays inside the task's admitted roots is inside and is admitted as the
    file it resolves to; only a link whose target leaves them is refused, naming the source."""

    root = _task_root(tmp_path)
    assert admit_supplemental(root, "../outside.md").refusal_code == "foreign-source"
    assert admit_supplemental(root, "/etc/hostname").refusal_code == "foreign-source"
    assert admit_supplemental(root, "memory/onboarding/x.md").refusal_code == "foreign-source"
    (root / "notes" / "link.md").symlink_to(root / "requirements" / "MIK-R102.md")
    inside = admit_supplemental(root, "notes/link.md")
    assert inside.state == "present" and inside.canonical_source == "requirements/MIK-R102.md"
    (root / "notes" / "linked").symlink_to(root / "requirements", target_is_directory=True)
    through = admit_supplemental(root, "notes/linked/MIK-R102.md")
    assert through.state == "present" and through.digest == inside.digest
    (tmp_path / "outside.md").write_text("# not the task's\n")
    (root / "notes" / "away.md").symlink_to(tmp_path / "outside.md")
    away = admit_supplemental(root, "notes/away.md")
    assert away.refusal_code == "foreign-source" and "notes/away.md" in away.detail
    (root / "task").mkdir(exist_ok=True)
    (root / "task" / "foreign.json").write_text('{"id": "foreign"}\n')
    assert admit_supplemental(root, "task/foreign.json").refusal_code == "foreign-source"
    assert (
        admit_supplemental(
            root, "task/foreign.json", leaf_document="183_the-review-pages-explainer"
        ).refusal_code
        == "foreign-source"
    )
    (root / "DEVELOPER-DIRECTION.md").write_text("# direction\n")
    assert admit_supplemental(root, "DEVELOPER-DIRECTION.md").refusal_code == "foreign-source"


def test_f06_own_leaf_documents_survive_capture_and_reread_while_foreign_stays_refused(
    tmp_path: Path,
) -> None:
    root = _task_root(tmp_path)
    stem = "183_the-review-pages-explainer"
    (root / f"{stem}.json").write_text(json.dumps({"id": LEAF, "slug": stem}))
    (root / f"{stem}.md").write_text("# the leaf\n")
    (root / "184_foreign.json").write_text(
        json.dumps({"id": "260928-MIK-L184", "slug": "184_foreign"})
    )

    for suffix, receipt_id in ((".json", "task-document"), (".md", "task-document-md")):
        name = f"{stem}{suffix}"
        captured = capture_supplemental(
            root,
            name,
            receipt_id=receipt_id,
            capture_identity="cap-1",
            establishes="the leaf's own document",
            producer=f"leaf {LEAF}",
            leaf_document=stem,
        )
        assert captured.read_state == "present"
        assert captured.digest is not None

        unchanged = reread_supplemental(
            captured,
            task_root=root,
            receipt_id=f"{receipt_id}-r2",
            capture_identity="cap-2",
        )
        assert unchanged.read_state == "present"
        assert unchanged.digest == captured.digest

        (root / name).write_text((root / name).read_text() + "\nupdated\n")
        changed = reread_supplemental(
            captured,
            task_root=root,
            receipt_id=f"{receipt_id}-r3",
            capture_identity="cap-3",
        )
        assert changed.read_state == "changed"
        assert changed.digest is not None and changed.digest != captured.digest
        assert captured.digest is not None

        (root / name).unlink()
        missing = reread_supplemental(
            changed,
            task_root=root,
            receipt_id=f"{receipt_id}-r4",
            capture_identity="cap-4",
        )
        assert missing.read_state == "unavailable"
        assert missing.digest is None
        assert changed.digest is not None

    foreign = capture_supplemental(
        root,
        "184_foreign.json",
        receipt_id="foreign-doc",
        capture_identity="cap-1",
        establishes="another leaf",
        producer=f"leaf {LEAF}",
        leaf_document=stem,
    )
    assert foreign.read_state == "unavailable"
    foreign_reread = reread_supplemental(
        foreign,
        task_root=root,
        receipt_id="foreign-doc-r2",
        capture_identity="cap-2",
        leaf_document=stem,
    )
    assert foreign_reread.read_state == "unavailable"
    assert foreign_reread.digest is None


def test_f06_a_lost_source_keeps_the_earlier_receipts_digest(tmp_path: Path) -> None:
    root = _task_root(tmp_path)
    receipt = capture_supplemental(
        root,
        "notes/ruling.md"
        if (root / "notes" / "ruling.md").exists()
        else "requirements/MIK-R102.md",
        receipt_id="sup-1",
        capture_identity="capture-1",
        establishes="a ruling",
    )
    (root / receipt.canonical_source).unlink()
    lost = reread_supplemental(
        receipt, task_root=root, receipt_id="sup-2", capture_identity="capture-2"
    )
    assert lost.read_state == "unavailable"
    assert lost.digest is None
    assert receipt.digest is not None


def test_f06_r2_a_large_source_is_admitted_whole_on_the_bytes_read(tmp_path: Path) -> None:
    """MIK-R211 v4 rule 1: no size refuses a supplemental source; its receipt binds every byte."""

    root = _task_root(tmp_path)
    source = root / "requirements" / "grow.md"
    payload = b"x" * (1024 * 1024 + 1)
    source.write_bytes(payload)
    admitted = admit_supplemental(root, "requirements/grow.md")
    assert admitted.state == "present", admitted.detail
    assert admitted.refusal_code is None
    assert admitted.byte_size == len(payload)
    assert admitted.digest == hashlib.sha256(payload).hexdigest()
    assert not hasattr(review_supplemental_evidence, "MAX_SUPPLEMENTAL_BYTES")
