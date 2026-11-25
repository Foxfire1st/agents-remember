"""Bind capsule detail pointers to immutable per-request files before launch."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote

from agents_remember.application.skill_resources.provider import (
    durable_composition_corpus_root,
    shipped_composition_corpus,
)
from agents_remember.cli import immutable_artifact
from agents_remember.cli.role_handover_artifacts import write_detail_artifacts
from agents_remember.cli.role_instruction_links import (
    bind_markdown_links,
    compact_skill_uri_links,
    require_no_unbound_links,
)
from agents_remember.cli.role_instructions import compose_role_instructions
from agents_remember.errors import CapsuleDeliveryError
from agents_remember.models.role_capsules.types import (
    CapsuleDetailReference,
    compute_content_digest,
)
from agents_remember.serving.capsule_delivery import CapsuleDelivery


def nested_detail_root(report: Path) -> Path:
    """Directory beside ``report`` that holds nested frozen detail snapshots."""
    return report.with_name(f"{report.stem}.details")


def _validated_detail_source_parts(source_path: str) -> tuple[str, ...]:
    if not source_path or "\\" in source_path or "\x00" in source_path:
        raise ValueError(
            f"Detail source path must be a confined POSIX relative path: {source_path!r}"
        )
    candidate = PurePosixPath(source_path)
    windows_candidate = PureWindowsPath(source_path)
    raw_parts = tuple(source_path.split("/"))
    if (
        candidate.is_absolute()
        or windows_candidate.is_absolute()
        or windows_candidate.drive
        or any(part in {"", ".", ".."} for part in raw_parts)
    ):
        raise ValueError(
            f"Detail source path must be a confined POSIX relative path: {source_path!r}"
        )
    return raw_parts


def nested_detail_path(report: Path, source_path: str) -> Path:
    """Confined path for one frozen detail file under ``<report_stem>.details/``."""
    _validated_detail_source_parts(source_path)
    return nested_detail_root(report) / source_path


@contextmanager
def _corpus_root_context(*, compact_links: bool, corpus_root: Path | None) -> Iterator[Path]:
    if corpus_root is not None:
        yield corpus_root.resolve()
        return
    if compact_links:
        yield durable_composition_corpus_root()
        return
    with shipped_composition_corpus() as (root, _manifest):
        yield root


def compose_launch_instructions(
    delivery: CapsuleDelivery,
    report_path: str,
    choice: dict[str, Any],
    *,
    compact_links: bool = False,
    corpus_root: Path | None = None,
) -> tuple[str, dict[str, Any]]:
    """Preserve user-note composition while binding the baseline's frozen details."""
    report = Path(report_path)
    with _corpus_root_context(compact_links=compact_links, corpus_root=corpus_root) as root:
        references, targets = _bound_details(
            delivery, report, root=root, compact_links=compact_links
        )
        detail_targets = (
            {
                (root / ref.source_path).resolve(): ref.source_path
                for ref in delivery.detail_references
            }
            if compact_links
            else None
        )
        baseline = _bound_baseline(
            delivery,
            targets,
            root=root,
            compact_links=compact_links,
            detail_targets=detail_targets,
        )
    paths = (
        _write_nested_detail_artifacts(report, references)
        if compact_links
        else write_detail_artifacts(report_path, references)
    )
    for source, path in paths.items():
        replacement = "detail/" + quote(source, safe="/:@") if compact_links else path.as_posix()
        baseline = baseline.replace("{{detail:" + source + "}}", replacement)
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
    delivery: CapsuleDelivery,
    report: Path,
    *,
    root: Path,
    compact_links: bool,
) -> tuple[tuple[CapsuleDetailReference, ...], dict[Path, Path]]:
    """Freeze launch-relative destinations without changing the admitted compiler value."""
    if not delivery.detail_references:
        return (), {}
    absolute_report = report.absolute()
    targets = {
        (root / reference.source_path).resolve(): (
            nested_detail_path(absolute_report, reference.source_path)
            if compact_links
            else _detail_path(absolute_report, reference.source_path)
        )
        for reference in delivery.detail_references
    }
    references = []
    for reference in delivery.detail_references:
        # Links inside frozen detail files always bind to absolute durable targets so a detail
        # file read standalone still resolves after the provider exits.
        content = bind_markdown_links(
            reference.content.decode("utf-8"),
            root / reference.source_path,
            targets,
            compact_links=False,
        ).encode("utf-8")
        references.append(
            replace(reference, content=content, revision=compute_content_digest(content))
        )
    return tuple(references), targets


def _bound_baseline(
    delivery: CapsuleDelivery,
    targets: dict[Path, Path],
    *,
    root: Path,
    compact_links: bool,
    detail_targets: dict[Path, str] | None,
) -> str:
    """Bind inline links to frozen details or their original non-detail sources."""
    baseline = delivery.trusted_instructions
    if not delivery.instruction_origins:
        require_no_unbound_links(baseline)
        return compact_skill_uri_links(baseline) if compact_links else baseline
    offset = 0
    parts: list[str] = []
    for origin in delivery.instruction_origins:
        if origin.start != offset or not origin.start <= origin.end <= len(baseline):
            raise CapsuleDeliveryError("instruction source origins do not cover the trusted stream")
        parts.append(
            bind_markdown_links(
                baseline[origin.start : origin.end],
                root / origin.source_path,
                targets,
                compact_links=compact_links,
                corpus_root=root if compact_links else None,
                detail_targets=detail_targets,
            )
        )
        offset = origin.end
    if offset != len(baseline):
        raise CapsuleDeliveryError("instruction source origins do not cover the trusted stream")
    return "".join(parts)


def _require_non_symlink_directory(directory: Path, *, allow_missing: bool) -> None:
    try:
        st = os.lstat(directory)
    except FileNotFoundError:
        if allow_missing:
            return
        raise
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ValueError(
            f"Detail artifact directory {directory} must be a real directory, not a symlink or file."
        )


def _ensure_real_directory(directory: Path) -> None:
    try:
        st = os.lstat(directory)
    except FileNotFoundError:
        try:
            directory.mkdir(exist_ok=False)
            return
        except FileExistsError:
            st = os.lstat(directory)
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
        raise ValueError(
            f"Detail artifact directory {directory} must be a real directory, not a symlink or file."
        )


def _write_nested_detail_artifacts(
    report: Path, references: tuple[CapsuleDetailReference, ...]
) -> dict[str, Path]:
    """Freeze each admitted detail under ``<report_stem>.details/<source_path>``, once."""
    if not references:
        return {}
    # Resolve only the outer report parent so admitted outer report-access or locator symlinks
    # stay intact while every interior directory beneath <stem>.details is confined to real dirs.
    canonical_report_parent = report.parent.resolve(strict=False)
    details_dirname = f"{report.stem}.details"
    canonical_details_root = canonical_report_parent / details_dirname
    paths: dict[str, Path] = {}
    for reference in references:
        parts = _validated_detail_source_parts(reference.source_path)
        given_target = nested_detail_path(report, reference.source_path)
        given_cursor = report.parent
        for segment in (details_dirname, *parts[:-1]):
            given_cursor = given_cursor / segment
            _require_non_symlink_directory(given_cursor, allow_missing=True)
        canonical_report_parent.mkdir(parents=True, exist_ok=True)
        canonical_cursor = canonical_report_parent
        for segment in (details_dirname, *parts[:-1]):
            canonical_cursor = canonical_cursor / segment
            _ensure_real_directory(canonical_cursor)
        given_cursor = report.parent
        for segment in (details_dirname, *parts[:-1]):
            given_cursor = given_cursor / segment
            _require_non_symlink_directory(given_cursor, allow_missing=False)
        canonical_target = canonical_details_root / reference.source_path
        if (
            not canonical_target.is_relative_to(canonical_details_root)
            or canonical_target == canonical_details_root
        ):
            raise ValueError(
                f"Detail artifact target {canonical_target} escapes {canonical_details_root}."
            )
        refusal = (
            f"This role request already has different detail content in {given_target}; "
            "start under a new request id."
        )
        immutable_artifact.write_once(canonical_target, reference.content, refusal)
        paths[reference.source_path] = given_target
    return paths


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
    source_path = reference["sourcePath"]
    legacy_detail = _detail_path(given_report, source_path)
    if path == legacy_detail:
        expected = Path(artifact["canonicalPath"]).with_name(legacy_detail.name)
        if path.parent.resolve() / path.name != expected:
            raise _detail_refusal(
                reference, f"the path no longer names its canonical target {expected}"
            )
        return
    _require_nested_detail_target(given_report, artifact, reference, path)


def _require_nested_detail_target(
    given_report: Path,
    artifact: dict[str, Any],
    reference: dict[str, Any],
    path: Path,
) -> None:
    source_path = reference["sourcePath"]
    try:
        parts = _validated_detail_source_parts(source_path)
    except ValueError as error:
        raise _detail_refusal(reference, str(error)) from error
    expected_nested = nested_detail_path(given_report, source_path)
    details_dirname = f"{given_report.stem}.details"
    canonical_details_root = Path(artifact["canonicalPath"]).parent / details_dirname
    expected_canonical = canonical_details_root / source_path
    if (
        path != expected_nested
        or not expected_canonical.is_relative_to(canonical_details_root)
        or expected_canonical == canonical_details_root
    ):
        raise _detail_refusal(
            reference, f"the path no longer names its canonical target {expected_canonical}"
        )
    cursor = given_report.parent
    for segment in (details_dirname, *parts[:-1]):
        cursor = cursor / segment
        st = os.lstat(cursor)
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise _detail_refusal(
                reference, f"interior detail directory {cursor} is not a real directory"
            )
    if path.parent.resolve() / path.name != expected_canonical:
        raise _detail_refusal(
            reference, f"the path no longer names its canonical target {expected_canonical}"
        )


def _detail_refusal(reference: dict[str, Any], reason: str) -> ValueError:
    return ValueError(
        f"The frozen instruction detail {reference.get('sourcePath')!r} at "
        f"{reference.get('path')!r} cannot be used: {reason}. Nothing was sent. "
        "Restore the original frozen file and its report-access path, then retry the same request; "
        "do not regenerate it from the current corpus. Otherwise start under a new request id."
    )
