"""The bytes an anchor names, and their ``content`` identity (MIK-R21 rule 3, MIK-R08 rule 3).

``content`` is ``sha256:`` of the bytes of the resolved range in the blob the anchor was recorded
against. This module fixes which bytes those are, once, so the writer (MIK-R12) that records an
anchor and every later reader that compares one (MIK-R03, MIK-R08) hash the same bytes:

* a **line range** ``start``..``end`` (one-based, inclusive) is those lines of the blob, each with its
  own line terminator exactly as the blob holds it (a final line without a newline has none);
* a **whole file** is every byte of the blob;
* a **symbol** is the line range the shipped extractor binds uniquely for the name. Finding that range
  needs the extractor, so it is the caller's (the writer resolves it); this module hashes the range.

Lines are split on ``\\n`` only. Bytes are never decoded, so a blob that is not UTF-8 still has a
content identity.
"""

from __future__ import annotations

import hashlib

CONTENT_PREFIX = "sha256:"


class RangeOutsideBlobError(ValueError):
    """A line range names lines the blob does not hold."""


def blob_lines(data: bytes) -> list[bytes]:
    """Return the blob's lines, each keeping its own ``\\n`` (the last one may have none)."""

    lines = data.splitlines(keepends=True)
    # ``splitlines`` also splits on \r, \v, \f and others; rejoin so only \n ends a line.
    joined: list[bytes] = []
    for piece in lines:
        if joined and not joined[-1].endswith(b"\n"):
            joined[-1] += piece
        else:
            joined.append(piece)
    return joined


def line_count(data: bytes) -> int:
    """How many lines the blob holds: a final newline ends the last line rather than opening one."""

    return len(blob_lines(data))


def range_bytes(data: bytes, start: int, end: int) -> bytes:
    """Return lines ``start``..``end`` (one-based, inclusive) of ``data`` with their terminators."""

    if start < 1 or end < start:
        raise RangeOutsideBlobError(f"lines {start}-{end} are not a one-based inclusive range")
    lines = blob_lines(data)
    if end > len(lines):
        raise RangeOutsideBlobError(
            f"lines {start}-{end} are not lines the blob holds (it holds {len(lines)})"
        )
    return b"".join(lines[start - 1 : end])


def content_identity(data: bytes) -> str:
    """Return ``sha256:<hex>`` of ``data``, the ``content`` field of an anchor over those bytes."""

    return f"{CONTENT_PREFIX}{hashlib.sha256(data).hexdigest()}"
