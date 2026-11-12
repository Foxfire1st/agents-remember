"""The complete hand-over file of one launch: the whole object, written once beside the message.

The first message carries a projection of the hand-over and names this file for the rest of it
(MIK-R193 rules 1 and 2). It holds exactly the object the launch builds, rendered at one-space
indent, and follows the same write-once contract as the message's own artifact: created read-only
by a hard link of a finished temporary file, reused only when it holds the same bytes, different
content refused, and no link followed at the file's own name.

Nothing here writes any other file. A launch preflights every target it intends to publish with
:func:`preflight_handover_file` before its first write, so a refusal leaves all of them as they
were (rule 4).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents_remember.cli.immutable_artifact import (
    MAX_ARTIFACT_BYTES,
    canonical_path,
    preflight,
    write_once,
)

# The complete hand-over's name beside the report path it belongs to (MIK-R193 rule 2).
HANDOVER_FILE_SUFFIX = ".handover.json"


def handover_file_text(handover: dict[str, Any]) -> str:
    """The complete hand-over object as its file holds it: UTF-8, one-space indent, newline."""

    return json.dumps(handover, ensure_ascii=False, indent=1) + "\n"


def handover_file_reference(report_path: str, content: str) -> dict[str, Any]:
    """The reference a write of ``content`` produces, without writing any file."""

    body = _bounded(content)
    path = Path(report_path).with_suffix(HANDOVER_FILE_SUFFIX)
    return {"path": path.as_posix(), "sha256": hashlib.sha256(body).hexdigest()}


def handover_file_target(report_path: str, content: str) -> tuple[Path, bytes]:
    """The place the complete hand-over would be written and the bytes it would hold."""

    return canonical_path(Path(report_path).with_suffix(HANDOVER_FILE_SUFFIX)), _bounded(content)


def handover_file_refusal(canonical: Path) -> str:
    """What a launch or a restore says when the complete hand-over's target holds other bytes."""

    return _changed_content(canonical)


def planned_handover_file(report_path: str, handover: dict[str, Any]) -> tuple[dict[str, Any], str]:
    """The reference the projection names and the text the file will hold, without writing.

    The reference carries the same path and digest the write of rule 2 produces, so the first
    message can name a file that is written before the start is sent.
    """

    content = handover_file_text(handover)
    return handover_file_reference(report_path, content), content


def write_handover_file(report_path: str, content: str) -> dict[str, Any]:
    """Write or reuse the complete hand-over object beside ``report_path``; return its reference."""

    body = _bounded(content)
    path = Path(report_path).with_suffix(HANDOVER_FILE_SUFFIX)
    canonical = canonical_path(path)
    write_once(canonical, body, _different_content(canonical))
    return {
        "path": path.as_posix(),
        "canonicalPath": canonical.as_posix(),
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
    }


def handover_file_line(saved: dict[str, Any]) -> str:
    """How a message and a restore name the complete hand-over's path and digest."""

    return f"{saved['path']} (SHA-256 {saved['sha256']})"


def preflight_handover_file(saved: dict[str, Any], message: str, *, receipt: Path) -> None:
    """Refuse a saved complete hand-over that cannot be written back, writing nothing."""

    _checked_handover_file(saved, message, receipt)


def validate_handover_file(saved: dict[str, Any], message: str, *, receipt: Path) -> None:
    """Check a saved complete hand-over and its target, before any sibling file is written."""

    canonical, body = _checked_handover_file(saved, message, receipt)
    _preflight_target(canonical, body)


def restore_handover_file(saved: dict[str, Any], message: str, *, receipt: Path) -> None:
    """Make a saved launch's complete hand-over file hold its text again (MIK-R193 rule 4).

    A file that holds the saved text is left untouched; a lost one is written again from the
    receipt's own copy of that text, read-only, and only at the place its reference leads to.
    A changed file, a saved text that does not match its reference, and a path that no longer
    leads to the recorded file are refused with what to put right, leaving receipt and files
    as they are.
    """

    canonical, body = _checked_handover_file(saved, message, receipt)
    _preflight_target(canonical, body)
    write_once(canonical, body, _changed_content(canonical))


def checked_handover_target(
    saved: dict[str, Any], message: str, *, receipt: Path
) -> tuple[Path, bytes]:
    """Where a saved complete hand-over goes and what it holds, or why it cannot be written."""

    return _checked_handover_file(saved, message, receipt)


def _bounded(content: str) -> bytes:
    body = content.encode("utf-8")
    if len(body) > MAX_ARTIFACT_BYTES:
        raise ValueError("The role hand-over file exceeds the task artifact size limit.")
    return body


def _different_content(canonical: Path) -> str:
    return (
        f"This role request already has a different hand-over in {canonical}. That file is "
        "never changed; start the role again under a new request id."
    )


def _changed_content(canonical: Path) -> str:
    return (
        f"The complete hand-over file {canonical} no longer holds the hand-over saved for this "
        "request. Delete that file and retry; the retry writes it again from the saved text."
    )


def _preflight_target(canonical: Path, body: bytes) -> None:
    """Refuse a target that holds other bytes now, before the launch writes any of its files."""

    preflight(canonical, body, _changed_content(canonical))


def _checked_handover_file(
    saved: dict[str, Any], message: str, receipt: Path
) -> tuple[Path, bytes]:
    """The place and bytes of the saved complete hand-over, or why they cannot be written."""

    missing = [
        key
        for key in ("path", "canonicalPath", "sha256")
        if not isinstance(saved.get(key), str) or not saved[key]
    ]
    if missing:
        raise ValueError(
            f"The receipt {receipt} records the complete hand-over of this request without its "
            f"{', '.join(missing)}, so the saved first message cannot be checked against its "
            "file and nothing was sent. Put the reference back as the launch wrote it and retry."
        )
    content = saved.get("content")
    if not isinstance(content, str):
        raise ValueError(
            f"The receipt {receipt} does not hold the text of the complete hand-over at "
            f"{saved['path']}, so a lost file cannot be written again and nothing was sent. "
            "Put the receipt's handoverFile content back as the launch wrote it and retry."
        )
    body = content.encode("utf-8")
    if (
        len(body) > MAX_ARTIFACT_BYTES
        or saved.get("bytes") != len(body)
        or saved.get("sha256") != hashlib.sha256(body).hexdigest()
    ):
        raise ValueError(
            f"The saved text of the complete hand-over at {saved['path']} does not match its "
            f"reference in receipt {receipt}. Nothing was written. Put the recorded text and "
            "digest back as the launch wrote them and retry."
        )
    if handover_file_line(saved) not in message.partition("\n")[0]:
        raise ValueError(
            f"The saved first message of receipt {receipt} does not name the complete hand-over "
            f"at {saved['path']} that it was compiled with. Nothing was written. Restore the "
            "saved message and its reference together and retry."
        )
    given = Path(saved["path"])
    canonical = canonical_path(given)
    if canonical.as_posix() != saved["canonicalPath"]:
        # Where nothing links the given path to another place, it leads to itself.
        leads = (
            "no longer leads to that file" if canonical == given else f"now leads to {canonical}"
        )
        raise ValueError(
            f"The complete hand-over of this request is recorded at {saved['canonicalPath']}, "
            f"but the path the agent was given, {saved['path']}, {leads}. Put back what that "
            "path ran through (for a leaf role, the report-access link of its enclosure) and "
            "retry."
        )
    return canonical, body
