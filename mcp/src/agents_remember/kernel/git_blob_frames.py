"""Decode exact Git batch blob bodies from a binary stream."""

from __future__ import annotations

from typing import BinaryIO

from agents_remember.kernel.git_preparation import GitPreparationError
from agents_remember.kernel.read_cancellation import raise_if_read_cancelled


def read_blob_frames(output: BinaryIO, requested: list[str]) -> dict[str, bytes]:
    """Read each requested blob and its delimiter without retaining the batch output."""

    blobs: dict[str, bytes] = {}
    for blob_id in requested:
        raise_if_read_cancelled()
        size = _read_blob_size(output, blob_id)
        body = read_blob_body(output, size)
        if isinstance(body, int) or output.read(1) != b"\n":
            raise GitPreparationError(f"Git returned an incomplete blob frame for {blob_id}")
        blobs[blob_id] = body
    raise_if_read_cancelled()
    if output.read(1):
        raise GitPreparationError("Git returned extra bytes after the last requested blob")
    return blobs


def read_blob_body(output: BinaryIO, size: int, *, end: int | None = None) -> bytes | int:
    """Read a frame's body once, returning only the byte count when it is short."""

    if end is not None:
        available = end - output.tell()
        if available < size:
            return available
    body = output.read(size)
    return body if len(body) == size else len(body)


def _read_blob_size(output: BinaryIO, blob_id: str) -> int:
    line = output.readline()
    fields = line.removesuffix(b"\n").decode("ascii", errors="backslashreplace").split(" ")
    if (
        not line.endswith(b"\n")
        or len(fields) != 3
        or fields[0] != blob_id
        or fields[1] != "blob"
        or not fields[2].isdecimal()
    ):
        raise GitPreparationError(f"{blob_id} is not a readable blob: {fields!r}")
    return int(fields[2])
