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
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from agents_remember.errors import AgentsRememberError
from agents_remember.models.role_capsules.types import CapsuleDetailReference

# A first message of 300,000 bytes has to launch; this bound only keeps a runaway compilation
# from being written to a task's report folder.
MAX_HANDOVER_ARTIFACT_BYTES = 1_048_576
_ARTIFACT_SUFFIX = ".handover.txt"
# Nobody writes the file after it was created, the agent it is for included.
_ARTIFACT_MODE = 0o444


def write_handover_artifact(report_path: str, content: str) -> dict[str, Any]:
    """Write or reuse the immutable artifact beside ``report_path`` and return its reference.

    ``path`` is the artifact as the agent reaches it, next to the report path it was given;
    ``canonicalPath`` is the same file with every link of its folder resolved.
    """

    body = content.encode("utf-8")
    if len(body) > MAX_HANDOVER_ARTIFACT_BYTES:
        raise ValueError("The compiled role handover exceeds the task artifact size limit.")
    path = Path(report_path).with_suffix(_ARTIFACT_SUFFIX)
    canonical = _canonical(path)
    # An artifact with other content and no receipt belongs to an earlier attempt of this request
    # id, which no execution carries: the request id is spent.
    _write_once(
        canonical,
        body,
        f"This role request already has different handover content in {canonical}. That file is "
        "never changed; start the role again under a new request id.",
    )
    return {
        "path": path.as_posix(),
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
    if not saved.get("stepUp"):
        return
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
    _write_takeover(_canonical(expected), body)


def _write_takeover(path: Path, body: bytes) -> None:
    remedy = (
        f"The takeover material at {path} must be an unchanged regular file. "
        "Remove the changed or nonregular target and retry the same request; "
        "saved receipts restore only their original bytes."
    )
    try:
        _write_once(path, body, remedy)
    except (OSError, ValueError) as error:
        raise AgentsRememberError(remedy) from error


def artifact_line(reference: dict[str, Any]) -> str:
    """The one line that precedes the compiled content in the message an agent receives."""

    return (
        f"AR handover artifact: {reference['path']} (SHA-256 {reference['sha256']}). It holds "
        "everything below this line; read that file again whenever your assignment is no longer "
        "in your context."
    )


def first_message(reference: dict[str, Any], content: str) -> str:
    """The message sent to the agent: the artifact line, then the content exactly as stored."""

    return f"{artifact_line(reference)}\n{content}"


def restore_handover_artifact(reference: dict[str, Any], message: str, *, receipt: Path) -> None:
    """Before a saved first message is sent again, make sure its artifact still holds it.

    A missing artifact is written again from the saved message; one whose content differs is
    refused. The file is the one the reference's own ``path`` leads to, and no other. Each
    refusal says what has to be put right before the same request is retried: a retry is the
    only way on for a request that has its receipt. ``receipt`` is the file the reference was
    read from; a reference that lacks one of its parts is refused by that name.
    """

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
        or line != artifact_line(reference)
        or hashlib.sha256(body).hexdigest() != reference["sha256"]
    ):
        raise ValueError("The saved first message does not match its handover artifact reference.")
    given = Path(reference["path"])
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
    _write_once(
        canonical,
        body,
        f"The handover artifact {canonical} no longer holds the first message saved for this "
        "request. Delete that file and retry; the retry writes it again from the saved message.",
    )


def _canonical(path: Path) -> Path:
    """The artifact's own name in its resolved folder; a link at that name is never followed."""

    return path.parent.resolve(strict=False) / path.name


def _write_once(path: Path, body: bytes, refusal: str) -> None:
    """Create ``path`` with ``body``, or leave it when it already holds exactly that.

    ``refusal`` is what the caller says when the file holds something else.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if _matches(path, body, refusal):
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fchmod(stream.fileno(), _ARTIFACT_MODE)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            # Another launch of the same request wrote it in the meantime.
            _matches(path, body, refusal)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _matches(path: Path, expected: bytes, refusal: str) -> bool:
    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(mode):
        raise ValueError("The task handover artifact path is not a regular file.")
    # Open without following a swapped link, and never block on a swapped FIFO.
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, "rb") as stream:
        opened = os.fstat(stream.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("The task handover artifact path is not a regular file.")
        if opened.st_size != len(expected) or stream.read(len(expected) + 1) != expected:
            raise ValueError(refusal)
    return True
