"""Stored-byte reads for frame metadata and assets (MIK-R103 rule 8, MIK-R211 v4 rule 3).

Metadata -- a manifest, a pointer, a draft, a receipt -- is read to its end: no size bound
refuses it. An asset or a digest is read against the size its manifest recorded, one sentinel
byte beyond it, so a file that does not match its record is told apart without reading past it.
The store retains responsibility for containment, manifest identity and digest verification.
"""

from pathlib import Path

from agents_remember.kernel.recorded_reads import ABSENT, bytes_identity, record_read


def read_frame_bytes(path: Path, limit: int | None = None) -> bytes:
    """Read the stored file whole, or at most ``limit + 1`` bytes against a recorded size."""

    try:
        with path.open("rb") as stream:
            payload = stream.read() if limit is None else stream.read(limit + 1)
    except FileNotFoundError:
        record_read(path, ABSENT)
        raise
    except OSError as error:
        record_read(path, f"unreadable ({type(error).__name__})")
        raise
    # The stored bytes are an input of whatever answer reads them (D570).
    over = limit is not None and len(payload) > limit
    record_read(path, None if over else bytes_identity(payload))
    if over:
        raise ValueError(f"{path.name} holds more than the {limit} byte(s) its record names")
    return payload
