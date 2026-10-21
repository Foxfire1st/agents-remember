"""One immutable artifact file of a launch: created once, read-only, never followed at its name.

A launch writes several files beside the report its agent is given — the compiled first message,
the complete hand-over, the takeover material, the detail snapshots — under one contract: the
file is created by a hard link of a finished temporary file, an existing file is reused only when
it holds exactly the same bytes, and different content is refused with the caller's own words.
Nothing here ever follows a link at the artifact's own name.
"""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

# A first message of 300,000 bytes has to launch; this bound only keeps a runaway compilation
# from being written to a task's report folder.
MAX_ARTIFACT_BYTES = 1_048_576
# Nobody writes the file after it was created, the agent it is for included.
ARTIFACT_MODE = 0o444


def canonical_path(path: Path) -> Path:
    """The artifact's own name in its resolved folder; a link at that name is never followed."""

    return path.parent.resolve(strict=False) / path.name


def write_once(path: Path, body: bytes, refusal: str) -> None:
    """Create ``path`` with ``body``, or leave it when it already holds exactly that.

    ``refusal`` is what the caller says when the file holds something else.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    if holds_exactly(path, body, refusal):
        return
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fchmod(stream.fileno(), ARTIFACT_MODE)
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path, follow_symlinks=False)
        except FileExistsError:
            # Another launch of the same request wrote it in the meantime.
            holds_exactly(path, body, refusal)
    finally:
        Path(temporary).unlink(missing_ok=True)


def preflight(path: Path, body: bytes, refusal: str) -> None:
    """Refuse now, before any sibling is written, when ``path`` holds other bytes.

    A target that is absent or already holds these bytes is left for its own write. This is the
    check that keeps a refused launch from having created one of its files before the refusal.
    """

    try:
        mode = path.lstat().st_mode
    except FileNotFoundError:
        return
    except OSError as error:
        raise ValueError(refusal) from error
    if not stat.S_ISREG(mode):
        # A link, a directory or a device at the artifact's own name is never written through.
        raise ValueError("The task handover artifact path is not a regular file.")
    holds_exactly(path, body, refusal)


def holds_exactly(path: Path, expected: bytes, refusal: str) -> bool:
    """Whether ``path`` already holds exactly ``expected``; a different file raises ``refusal``."""

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
