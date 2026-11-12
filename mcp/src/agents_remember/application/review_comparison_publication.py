"""Publish one comparison record so that the first writer wins and no later writer replaces it.

A comparison record is immutable once it exists (MIK-R195 rule 4: every earlier numbered record keeps
its number, four trees, timestamp and bytes). The numbering owner used to ask whether the file was
there and then write it, so two processes that both found it absent both published the same number,
and the later write silently replaced the record the earlier reader had already answered with -- the
same number, the same four trees, a different `recorded_at`, and a first reader holding a comparison
the leaf no longer holds (MIK-R195 F05).

The decision therefore belongs to the file system, not to a check beside it: the record's bytes are
written to a private temporary file, and the record is published by *linking* that file into place,
which fails with `FileExistsError` when the name is taken. A call that loses the race returns
``False`` and publishes nothing; the caller then answers with the record that won, or refuses.

Nothing here numbers, pins or re-reads a record: those stay the numbering owner's
(:mod:`agents_remember.application.review_tree_comparison`).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

from agents_remember.models.knowledge.review import ReviewRefusal


def _fsync_directory(directory: Path) -> None:
    """Flush ``directory``'s own entries so the published name survives a host loss.

    Skipped on Windows, which has no directory handle to flush (the kernel writer skips it there
    for the same reason). The file's own fsync and the atomic link already hold.
    """

    if sys.platform == "win32":
        return
    handle = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)


def publish_comparison_record(path: Path, payload: bytes) -> bool:
    """Publish ``payload`` at ``path`` if nothing is there yet; ``True`` when this call created it.

    The temporary file is private to this call (process id and a random suffix) so two writers never
    share one, and it is removed on every path, including a lost race. ``os.link`` is the
    create-only publish: it names the record's bytes the first time only, and its failure is how a
    second writer learns it lost.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), "wb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temp, path)
    except FileExistsError:
        return False
    finally:
        temp.unlink(missing_ok=True)
    _fsync_directory(path.parent)
    return True


def publication_refusal(leaf_id: str, number: int) -> ReviewRefusal:
    """The record this read would publish lost its number to another pair: name it, never guess."""

    return ReviewRefusal(
        code="reviewer_busy",
        detail=(
            f"comparison {number} of leaf {leaf_id} was published by another reader with other "
            "trees while this one was being recorded, so this comparison is not published under a "
            "number that names a different pair"
        ),
        next_action="retry the review; the leaf's recorded comparison numbers are read afresh",
        offending_input=str(number),
    )
