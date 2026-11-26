"""Turn-1 dynamic appendix offload capping and deterministic sidecar artifacts.

Direct opening messages and held-seat opening batches are bounded by ``role_message_limit``.
Messages within the limit stay inline; longer messages are truncated to a Unicode-character
preview within the limit followed by a deterministic pointer line to an immutable read-only
sidecar beside the launch report.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from agents_remember.cli.immutable_artifact import canonical_path, preflight, write_once
from agents_remember.kernel.agentic_settings import load_agentic_settings
from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

OPENING_MESSAGE_SUFFIX = ".opening-message.md"
_SELECTION_REF_KEYS = ("taskDocumentRef", "masterDocumentRef", "sprintDocumentRef")


def resolve_role_message_limit(config: McpRuntimeConfig, *, repo_id: str | None) -> int:
    """Resolve the active ``role_message_limit`` for a launch's repository scope."""

    repo_root = (
        config.repositories[repo_id].path if repo_id and repo_id in config.repositories else None
    )
    return load_agentic_settings(config.coordination_root, repo_root=repo_root).role_message_limit


def selection_repo_id(selection: Mapping[str, Any] | None) -> str | None:
    """Return the repository id from the first populated document ref in ``selection``."""

    if selection is None:
        return None
    for key in _SELECTION_REF_KEYS:
        ref = selection.get(key)
        if isinstance(ref, Mapping):
            repository = ref.get("repository")
            if isinstance(repository, str) and repository:
                return repository
    return None


def opening_message_pointer_line(path: Path | str, sha256: str) -> str:
    """Format the trailing pointer line for an offloaded direct opening message."""

    return f"Opening message file: {Path(path).as_posix()} (SHA-256 {sha256})"


def held_batch_pointer_line(path: Path | str, sha256: str) -> str:
    """Format the trailing pointer line for an offloaded held-seat opening batch."""

    return f"Held opening messages file: {Path(path).as_posix()} (SHA-256 {sha256})"


def format_bounded_appendix(full_text: str, *, limit: int, pointer_line: str) -> tuple[str, bool]:
    """Return ``(emitted_text, offloaded)`` enforcing a Unicode-character cap on the inline body."""

    if isinstance(limit, bool) or limit < 1:
        raise ValueError("The role message limit must be a positive integer.")
    if len(full_text) <= limit:
        return full_text, False
    preview = f"{full_text[: limit - 3]}..." if limit >= 4 else ""
    emitted = f"{preview}\n{pointer_line}" if preview else pointer_line
    return emitted, True


def opening_message_refusal(path: Path) -> str:
    """Build an operator-legible refusal when an opening message target is changed or nonregular."""

    return (
        f"The opening message file {path} no longer holds the saved opening message or is not a "
        "regular file. Remove the changed or nonregular target and retry the same request, or "
        "start under a new request id."
    )


def prepare_direct_opening_message(
    report_path: str, opening_message: str, *, limit: int
) -> tuple[str, dict[str, Any] | None]:
    """Return the inline opening message or a bounded preview plus planned sidecar reference."""

    if isinstance(limit, bool) or limit < 1:
        raise ValueError("The role message limit must be a positive integer.")
    if len(opening_message) <= limit:
        return opening_message, None
    path = Path(report_path).with_suffix(OPENING_MESSAGE_SUFFIX)
    canonical = canonical_path(path)
    body = opening_message.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    pointer = opening_message_pointer_line(path, digest)
    bounded, _ = format_bounded_appendix(opening_message, limit=limit, pointer_line=pointer)
    reference: dict[str, Any] = {
        "path": path.as_posix(),
        "canonicalPath": canonical.as_posix(),
        "sha256": digest,
        "bytes": len(body),
        "content": opening_message,
    }
    return bounded, reference


def preflight_opening_message_artifact(reference: Mapping[str, Any]) -> None:
    """Refuse now when the opening message sidecar target holds other bytes; write nothing."""

    canonical = Path(reference["canonicalPath"])
    body = str(reference["content"]).encode("utf-8")
    preflight(canonical, body, opening_message_refusal(canonical))


def write_opening_message_artifact(reference: Mapping[str, Any]) -> None:
    """Preflight and write or reuse the immutable opening message sidecar."""

    canonical = Path(reference["canonicalPath"])
    body = str(reference["content"]).encode("utf-8")
    refusal = opening_message_refusal(canonical)
    preflight(canonical, body, refusal)
    write_once(canonical, body, refusal)


def checked_opening_message_target(
    saved: Mapping[str, Any], message: str, *, receipt: Path
) -> tuple[Path, bytes] | None:
    """Validate a saved opening message sidecar against its receipt and message."""

    reference = saved.get("openingMessageArtifact")
    if reference is None:
        return None
    report = saved.get("report")
    report_path = report.get("path") if isinstance(report, Mapping) else None
    if not isinstance(reference, Mapping) or not isinstance(report_path, str) or not report_path:
        raise ValueError(
            f"The opening message reference in receipt {receipt} is missing or malformed. "
            "Restore the receipt's report path and openingMessageArtifact reference and retry."
        )
    expected = Path(report_path).with_suffix(OPENING_MESSAGE_SUFFIX)
    expected_canonical = canonical_path(expected)
    content = reference.get("content")
    if (
        not isinstance(content, str)
        or reference.get("path") != expected.as_posix()
        or reference.get("canonicalPath") != expected_canonical.as_posix()
    ):
        raise ValueError(
            f"The opening message artifact at {expected} recorded by receipt {receipt} is missing "
            "its content or rebound to another path. Restore the original reference and retry."
        )
    body = content.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    recorded_bytes = reference.get("bytes")
    if (
        isinstance(recorded_bytes, bool)
        or recorded_bytes != len(body)
        or reference.get("sha256") != digest
        or opening_message_pointer_line(expected, digest) not in message
    ):
        raise ValueError(
            f"The opening message artifact at {expected} does not match the digest or pointer "
            f"line recorded by receipt {receipt}. Restore the saved message and reference together "
            "and retry."
        )
    return expected_canonical, body


def restore_opening_message_artifact(
    saved: Mapping[str, Any], message: str, *, receipt: Path
) -> None:
    """Restore a saved opening message sidecar from its receipt when present."""

    target = checked_opening_message_target(saved, message, receipt=receipt)
    if target is not None:
        write_once(target[0], target[1], opening_message_refusal(target[0]))


def prepare_held_opening_batch(
    config: McpRuntimeConfig, receipt: Mapping[str, Any], batch_text: str
) -> str:
    """Bound a held-seat opening batch to the role message limit, offloading when exceeded."""

    selection = receipt.get("selection")
    repo_id = selection_repo_id(selection if isinstance(selection, Mapping) else None)
    limit = resolve_role_message_limit(config, repo_id=repo_id)
    if len(batch_text) <= limit:
        return batch_text
    report = receipt.get("report")
    report_path = report.get("path") if isinstance(report, Mapping) else None
    if not isinstance(report_path, str) or not report_path:
        raise ValueError("The held seat receipt has no report path for its held opening messages.")
    body = batch_text.encode("utf-8")
    digest = hashlib.sha256(body).hexdigest()
    report_obj = Path(report_path)
    path = report_obj.with_name(f"{report_obj.stem}.held-batch-{digest}.md")
    canonical = canonical_path(path)
    refusal = (
        f"The held opening messages file {canonical} already exists with different bytes or is "
        "not a regular file. Remove the conflicting target and retry."
    )
    preflight(canonical, body, refusal)
    write_once(canonical, body, refusal)
    pointer = held_batch_pointer_line(path, digest)
    bounded, _ = format_bounded_appendix(batch_text, limit=limit, pointer_line=pointer)
    return bounded
