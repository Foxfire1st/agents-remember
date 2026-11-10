"""Bind capsule detail pointers to immutable per-request files before launch."""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import replace
from pathlib import Path
from typing import Any

from agents_remember.application.skill_resources.provider import shipped_composition_corpus
from agents_remember.cli.role_handover_artifacts import write_detail_artifacts
from agents_remember.cli.role_instruction_links import bind_markdown_links, require_no_unbound_links
from agents_remember.cli.role_instructions import compose_role_instructions
from agents_remember.errors import CapsuleDeliveryError
from agents_remember.models.role_capsules.types import (
    CapsuleDetailReference,
    compute_content_digest,
)
from agents_remember.serving.capsule_delivery import CapsuleDelivery


def compose_launch_instructions(
    delivery: CapsuleDelivery, report_path: str, choice: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    """Preserve user-note composition while binding the baseline's frozen details."""
    references, targets = _bound_details(delivery, Path(report_path))
    baseline = _bound_baseline(delivery, targets)
    paths = write_detail_artifacts(report_path, references)
    for source, path in paths.items():
        baseline = baseline.replace("{{detail:" + source + "}}", path.as_posix())
    instructions, record = compose_role_instructions(baseline, delivery.semantic_digest, choice)
    if paths:
        record["detailReferences"] = [
            {
                "sourcePath": reference.source_path,
                "path": paths[reference.source_path].as_posix(),
                "revision": reference.revision,
                "sizeBytes": len(reference.content),
                "step": reference.step,
            }
            for reference in references
        ]
    return instructions, record


def _detail_path(report: Path, source_path: str) -> Path:
    source_hash = hashlib.sha256(source_path.encode("utf-8")).hexdigest()
    return report.with_name(f"{report.stem}.detail-{source_hash}.md")


def _bound_details(
    delivery: CapsuleDelivery, report: Path
) -> tuple[tuple[CapsuleDetailReference, ...], dict[Path, Path]]:
    """Freeze launch-relative destinations without changing the admitted compiler value."""
    if not delivery.detail_references:
        return (), {}
    with shipped_composition_corpus() as (root, _manifest):
        targets = {
            (root / reference.source_path).resolve(): _detail_path(
                report.absolute(), reference.source_path
            )
            for reference in delivery.detail_references
        }
        references = []
        for reference in delivery.detail_references:
            content = bind_markdown_links(
                reference.content.decode("utf-8"), root / reference.source_path, targets
            ).encode("utf-8")
            references.append(
                replace(reference, content=content, revision=compute_content_digest(content))
            )
    return tuple(references), targets


def _bound_baseline(delivery: CapsuleDelivery, targets: dict[Path, Path]) -> str:
    """Bind inline links to frozen details or their original non-detail sources."""
    baseline = delivery.trusted_instructions
    if not delivery.instruction_origins:
        require_no_unbound_links(baseline)
        return baseline
    offset = 0
    parts: list[str] = []
    with shipped_composition_corpus() as (root, _manifest):
        for origin in delivery.instruction_origins:
            if origin.start != offset or not origin.start <= origin.end <= len(baseline):
                raise CapsuleDeliveryError(
                    "instruction source origins do not cover the trusted stream"
                )
            parts.append(
                bind_markdown_links(
                    baseline[origin.start : origin.end], root / origin.source_path, targets
                )
            )
            offset = origin.end
    if offset != len(baseline):
        raise CapsuleDeliveryError("instruction source origins do not cover the trusted stream")
    return "".join(parts)


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
    artifact = receipt.get("handoverArtifact")
    if not isinstance(artifact, dict) or any(
        not isinstance(artifact.get(key), str) or not artifact[key]
        for key in ("path", "canonicalPath")
    ):
        raise _detail_refusal(reference, "the saved handover has no path and canonical target")
    given_report = Path(report["path"])
    given_artifact = given_report.with_suffix(".handover.txt")
    # The artifact's own file sits beside the report; the path the agent was given reaches it
    # through a link (a launch's own locator, or the report-access link of its enclosure).
    if (
        Path(artifact["path"]).resolve() != given_artifact.resolve()
        or Path(artifact["canonicalPath"]).name != given_artifact.name
    ):
        raise _detail_refusal(reference, "the saved handover is not beside its report")
    detail = _detail_path(given_report, reference["sourcePath"])
    name = detail.name
    expected = Path(artifact["canonicalPath"]).with_name(name)
    if path != detail or path.parent.resolve() / path.name != expected:
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
