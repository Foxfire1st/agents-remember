"""The handover artifact of one launch: the first message as compiled, written once.

Every launch stores the compiled capsule and handover in one file beside the report the agent is
told to write. The file is never changed: it is created read-only, the same request reuses it, and
different content for the same request is refused. The message an agent actually receives is that
content preceded by one line that names the file and its SHA-256, so an agent that no longer has
its assignment in context can read it again.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

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


def restore_handover_artifact(reference: dict[str, Any], message: str) -> None:
    """Before a saved first message is sent again, make sure its artifact still holds it.

    A missing artifact is written again from the saved message; one whose content differs is
    refused. The file is the one the reference's own ``path`` leads to, and no other. Each
    refusal says what has to be put right before the same request is retried: a retry is the
    only way on for a request that has its receipt.
    """

    line, separator, content = message.partition("\n")
    body = content.encode("utf-8")
    if (
        not separator
        or line != artifact_line(reference)
        or hashlib.sha256(body).hexdigest() != reference["sha256"]
    ):
        raise ValueError("The saved first message does not match its handover artifact reference.")
    canonical = _canonical(Path(reference["path"]))
    if canonical.as_posix() != reference["canonicalPath"]:
        raise ValueError(
            f"The handover artifact of this request is recorded at {reference['canonicalPath']}, "
            f"but the path the agent was given, {reference['path']}, now leads to {canonical}. "
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
    if path.read_bytes() != expected:
        raise ValueError(refusal)
    return True
