"""The handover artifact of one launch: the first message as compiled, written once.

Every launch stores the compiled capsule and handover in one file beside the report the agent is
told to write. The file is never changed: it is created read-only, the same request reuses it, and
different content for the same request is refused. The message an agent actually receives is that
content preceded by one line that names the file and its SHA-256, so an agent that no longer has
its assignment in context can read it again.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents_remember.cli.immutable_artifact import (
    MAX_ARTIFACT_BYTES,
)
from agents_remember.cli.immutable_artifact import (
    canonical_path as _canonical,
)
from agents_remember.cli.immutable_artifact import (
    preflight as preflight_target,
)
from agents_remember.cli.immutable_artifact import (
    write_once as _write_once,
)
from agents_remember.cli.role_handover_file import handover_file_line
from agents_remember.errors import AgentsRememberError
from agents_remember.models.role_capsules.types import CapsuleDetailReference
from agents_remember.worktrees.role_handover_locator import ensure_locator, is_locator

# A first message of 300,000 bytes has to launch; this bound only keeps a runaway compilation
# from being written to a task's report folder.
MAX_HANDOVER_ARTIFACT_BYTES = MAX_ARTIFACT_BYTES
_ARTIFACT_SUFFIX = ".handover.txt"


def _artifact_path(report_path: str) -> Path:
    """The artifact's own name beside the report it belongs to."""

    return Path(report_path).with_suffix(_ARTIFACT_SUFFIX)


def _named_path(report_path: str, locator: Path | None) -> Path:
    """The path the agent is given: this request's locator, or the artifact's own place."""

    path = _artifact_path(report_path)
    return path if locator is None else locator / path.name


def _changed_artifact(canonical: Path) -> str:
    return (
        f"This role request already has different handover content in {canonical}. That file is "
        "never changed; start the role again under a new request id."
    )


def handover_artifact_reference(
    report_path: str, content: str, *, locator: Path | None = None
) -> dict[str, Any]:
    """The reference a write of this content produces, without writing any file.

    ``locator`` is the bounded directory alias this request publishes; the reference's ``path``
    is the artifact as the agent reaches it, and ``canonicalPath`` the same file with every link
    of its folder resolved.
    """

    body = content.encode("utf-8")
    if len(body) > MAX_HANDOVER_ARTIFACT_BYTES:
        raise ValueError("The compiled role handover exceeds the task artifact size limit.")
    return {
        "path": _named_path(report_path, locator).as_posix(),
        "canonicalPath": _canonical(_artifact_path(report_path)).as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }


def preflight_handover_artifact(report_path: str, content: str) -> None:
    """Refuse now when the artifact's own target holds other bytes; write nothing."""

    reference = handover_artifact_reference(report_path, content)
    canonical = Path(reference["canonicalPath"])
    preflight_target(canonical, content.encode("utf-8"), _changed_artifact(canonical))


def write_handover_artifact(
    report_path: str, content: str, *, locator: Path | None = None
) -> dict[str, Any]:
    """Write or reuse the immutable artifact beside ``report_path`` and return its reference.

    The file is created at its own place beside the report; ``path`` is the artifact as the agent
    reaches it -- through this request's ``locator`` when one is named -- and ``canonicalPath`` is
    the same file with every link of its folder resolved.
    """

    body = content.encode("utf-8")
    if len(body) > MAX_HANDOVER_ARTIFACT_BYTES:
        raise ValueError("The compiled role handover exceeds the task artifact size limit.")
    canonical = _canonical(_artifact_path(report_path))
    # An artifact with other content and no receipt belongs to an earlier attempt of this request
    # id, which no execution carries: the request id is spent.
    _write_once(canonical, body, _changed_artifact(canonical))
    return {
        "path": _named_path(report_path, locator).as_posix(),
        "canonicalPath": canonical.as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }


def write_detail_artifacts(
    report_path: str, references: tuple[CapsuleDetailReference, ...]
) -> dict[str, Path]:
    """Freeze each admitted detail file beside this request's handover, once."""
    report = Path(report_path)
    paths: dict[str, Path] = {}
    for reference in references:
        name = hashlib.sha256(reference.source_path.encode("utf-8")).hexdigest()
        path = report.with_name(f"{report.stem}.detail-{name}.md")
        _write_once(
            _canonical(path),
            reference.content,
            f"This role request already has different detail content in {path}; start under a new request id.",
        )
        paths[reference.source_path] = path
    return paths


def write_step_up_artifact(report_path: str, material: dict[str, Any]) -> dict[str, Any]:
    """Freeze takeover material beside the request's handover; the receipt keeps its bytes."""
    content = json.dumps(material, indent=2, ensure_ascii=False) + "\n"
    body = content.encode("utf-8")
    path = Path(report_path).with_suffix(".step-up.json")
    if len(body) > MAX_HANDOVER_ARTIFACT_BYTES:
        raise AgentsRememberError(
            f"The takeover material at {path} exceeds the artifact size limit."
        )
    canonical = _canonical(path)
    _write_takeover(canonical, body)
    return {
        "path": path.as_posix(),
        "canonicalPath": canonical.as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "content": content,
    }


def takeover_artifact_line(reference: dict[str, Any]) -> str:
    """Bind the named sidecar bytes to the immutable primary handover."""
    return f"Read takeover material at {reference['path']} (SHA-256 {reference['sha256']})"


def restore_step_up_artifact(saved: dict[str, Any], message: str, *, receipt: Path) -> None:
    """Restore only frozen takeover bytes, refusing changed, nonregular or rebound targets."""

    target = checked_step_up_target(saved, message, receipt=receipt)
    if target is None:
        return
    canonical, body = target
    _write_takeover(canonical, body)


def checked_step_up_target(
    saved: dict[str, Any], message: str, *, receipt: Path
) -> tuple[Path, bytes] | None:
    """Where a saved receipt's takeover material goes and what it holds, or nothing to write."""

    if not saved.get("stepUp"):
        return None
    reference = saved.get("stepUpArtifact")
    report = saved.get("report") or {}
    remedy = f"The takeover material recorded by receipt {receipt} cannot be verified; restore its original reference and retry the same request."
    if not isinstance(reference, dict) or not isinstance(report.get("path"), str):
        raise AgentsRememberError(remedy)
    content = reference.get("content")
    expected = Path(report["path"]).with_suffix(".step-up.json")
    if (
        not isinstance(content, str)
        or reference.get("path") != expected.as_posix()
        or reference.get("canonicalPath") != _canonical(expected).as_posix()
    ):
        raise AgentsRememberError(
            f"Takeover material at {expected} is missing or rebound. {remedy}"
        )
    body = content.encode("utf-8")
    if (
        len(body) > MAX_HANDOVER_ARTIFACT_BYTES
        or reference.get("bytes") != len(body)
        or reference.get("sha256") != hashlib.sha256(body).hexdigest()
        or takeover_artifact_line(reference) not in message
    ):
        raise AgentsRememberError(
            f"Takeover material at {expected} has no matching saved hash. {remedy}"
        )
    return _canonical(expected), body


def takeover_refusal(path: Path) -> str:
    """What a restore says when the takeover material's target holds other bytes."""

    return (
        f"The takeover material at {path} must be an unchanged regular file. "
        "Remove the changed or nonregular target and retry the same request; "
        "saved receipts restore only their original bytes."
    )


def _write_takeover(path: Path, body: bytes) -> None:
    remedy = takeover_refusal(path)
    try:
        _write_once(path, body, remedy)
    except (OSError, ValueError) as error:
        raise AgentsRememberError(remedy) from error


def artifact_line(reference: dict[str, Any], handover_file: dict[str, Any] | None = None) -> str:
    """The one line that precedes the compiled content in the message an agent receives.

    A launch of this leaf names both files: the text that holds the message below the line, and
    the JSON that holds the complete hand-over object (MIK-R193 rule 3). A receipt without that
    reference — a seat started before this leaf — keeps today's one-file text, so that the
    restore's comparison of an unchanged message still holds.
    """

    if handover_file is None:
        return (
            f"AR handover artifact: {reference['path']} (SHA-256 {reference['sha256']}). It holds "
            "everything below this line; read that file again whenever your assignment is no "
            "longer in your context."
        )
    return (
        f"AR handover artifacts: {reference['path']} (SHA-256 {reference['sha256']}) holds "
        f"everything below this line; {handover_file_line(handover_file)} holds the complete "
        "hand-over. Read them again whenever your assignment is no longer in context."
    )


def first_message(
    reference: dict[str, Any],
    content: str,
    handover_file: dict[str, Any] | None = None,
) -> str:
    """The message sent to the agent: the artifact line, then the content exactly as stored."""

    return f"{artifact_line(reference, handover_file)}\n{content}"


def restore_handover_artifact(
    reference: dict[str, Any],
    message: str,
    *,
    receipt: Path,
    handover_file: dict[str, Any] | None = None,
) -> None:
    """Before a saved first message is sent again, make sure its artifact still holds it.

    A missing artifact is written again from the saved message; one whose content differs is
    refused. The file is the one the reference's own ``path`` leads to, and no other. Each
    refusal says what has to be put right before the same request is retried: a retry is the
    only way on for a request that has its receipt. ``receipt`` is the file the reference was
    read from; a reference that lacks one of its parts is refused by that name. ``handover_file``
    is the complete hand-over's reference of the same receipt, which the artifact line names.
    """

    canonical, body = checked_handover_artifact_target(
        reference, message, receipt=receipt, handover_file=handover_file
    )
    _write_once(canonical, body, artifact_refusal(canonical))


def checked_handover_artifact_target(
    reference: dict[str, Any],
    message: str,
    *,
    receipt: Path,
    handover_file: dict[str, Any] | None = None,
) -> tuple[Path, bytes]:
    """Where the saved message's artifact goes and what it holds, or why it cannot be written."""

    missing = [
        key
        for key in ("path", "canonicalPath", "sha256")
        if not isinstance(reference.get(key), str) or not reference[key]
    ]
    if missing:
        raise ValueError(
            f"The receipt {receipt} records the handover artifact of this request without its "
            f"{', '.join(missing)}, so the saved first message cannot be checked against its "
            "file and nothing was sent. Put the reference back as the launch wrote it and retry."
        )
    line, separator, content = message.partition("\n")
    body = content.encode("utf-8")
    if (
        not separator
        or line != artifact_line(reference, handover_file)
        or hashlib.sha256(body).hexdigest() != reference["sha256"]
    ):
        raise ValueError("The saved first message does not match its handover artifact reference.")
    given = Path(reference["path"])
    canonical = _canonical(given)
    if canonical.as_posix() != reference["canonicalPath"] and is_locator(given.parent):
        # The request's own locator: a lost one is rebuilt where the receipt recorded its file,
        # and one that leads to another folder is refused with what has to be put right. No
        # artifact is written here.
        ensure_locator(given.parent, Path(reference["canonicalPath"]).parent)
        canonical = _canonical(given)
    if canonical.as_posix() != reference["canonicalPath"]:
        # Where nothing links the given path to another place, it leads to itself.
        leads = (
            "no longer leads to that file" if canonical == given else f"now leads to {canonical}"
        )
        raise ValueError(
            f"The handover artifact of this request is recorded at {reference['canonicalPath']}, "
            f"but the path the agent was given, {reference['path']}, {leads}. "
            "Put back what that path ran through (for a leaf role, the report-access link of its "
            "enclosure) and retry."
        )
    return canonical, body


def artifact_refusal(canonical: Path) -> str:
    """What a start or restore says when the artifact's target holds other bytes."""

    return (
        f"The handover artifact {canonical} no longer holds the first message saved for this "
        "request. Delete that file and retry; the retry writes it again from the saved message."
    )
