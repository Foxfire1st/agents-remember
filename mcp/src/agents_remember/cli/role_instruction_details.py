"""Bind capsule detail pointers to immutable per-request files before launch."""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any

from agents_remember.cli.role_handover_artifacts import write_detail_artifacts
from agents_remember.cli.role_instructions import compose_role_instructions
from agents_remember.serving.capsule_delivery import CapsuleDelivery


def compose_launch_instructions(
    delivery: CapsuleDelivery, report_path: str, choice: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Preserve user-note composition while binding the baseline's frozen details."""
    baseline = delivery.trusted_instructions
    paths = write_detail_artifacts(report_path, delivery.detail_references)
    for source, path in paths.items():
        baseline = baseline.replace("{{detail:" + source + "}}", path.as_posix())
    instructions, record = compose_role_instructions(baseline, delivery.semantic_digest, choice)
    if paths:
        record["detailReferences"] = [
            {
                "sourcePath": reference.source_path,
                "path": paths[reference.source_path].as_posix(),
                "revision": reference.revision,
                "step": reference.step,
            }
            for reference in delivery.detail_references
        ]
    return instructions, record


def validate_instruction_detail_artifacts(receipt: dict[str, Any]) -> None:
    """Check saved detail snapshots, never regenerate them from the current corpus.

    A detail-free receipt remains replayable. A damaged frozen snapshot must be restored
    from its original bytes before retrying the same saved launch.
    """
    composition = receipt.get("instructionComposition")
    if not isinstance(composition, dict):
        return
    references = composition.get("detailReferences", [])
    if not isinstance(references, list):
        raise _detail_refusal({}, "the saved detailReferences must be a list")
    for reference in references:
        _require_detail_reference(reference)
        path = Path(reference["path"])
        try:
            _require_detail_target(receipt, reference, path)
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise _detail_refusal(reference, "the target is not a regular file")
                observed = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
        except OSError as error:
            raise _detail_refusal(reference, str(error)) from error
        if observed != reference["revision"]:
            raise _detail_refusal(reference, "the bytes no longer match the frozen revision")


def _require_detail_reference(reference: Any) -> None:
    if not isinstance(reference, dict):
        raise _detail_refusal({}, "the saved detail reference is not an object")
    if any(
        not isinstance(reference.get(key), str) or not reference[key]
        for key in ("sourcePath", "path", "revision")
    ):
        raise _detail_refusal(reference, "the saved reference lacks sourcePath, path or revision")


def _require_detail_target(receipt: dict[str, Any], reference: dict[str, Any], path: Path) -> None:
    report = receipt.get("report")
    if not isinstance(report, dict) or any(
        not isinstance(report.get(key), str) or not report[key] for key in ("path", "canonicalPath")
    ):
        raise _detail_refusal(reference, "the saved report has no path and canonical target")
    source_hash = hashlib.sha256(reference["sourcePath"].encode("utf-8")).hexdigest()
    given_report = Path(report["path"])
    name = f"{given_report.stem}.detail-{source_hash}.md"
    expected = Path(report["canonicalPath"]).with_name(name)
    if path != given_report.with_name(name) or path.parent.resolve() / path.name != expected:
        raise _detail_refusal(
            reference, f"the path no longer names its canonical target {expected}"
        )


def _detail_refusal(reference: dict[str, Any], reason: str) -> ValueError:
    return ValueError(
        f"The frozen instruction detail {reference.get('sourcePath')!r} at "
        f"{reference.get('path')!r} cannot be used: {reason}. Nothing was sent. "
        "Restore the original frozen file and its report-access path, then retry the same request; "
        "do not regenerate it from the current corpus. Otherwise start under a new request id."
    )
