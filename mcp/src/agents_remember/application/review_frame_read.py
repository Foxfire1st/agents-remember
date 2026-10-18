"""Finite stored-byte reads for frame metadata and assets (MIK-R103 rule 8).

Read one sentinel byte beyond the admitted size, never the whole file followed by a size check.
The store retains responsibility for containment, manifest identity and digest verification.
"""

from pathlib import Path

from agents_remember.models.knowledge.review_frame import (
    MAX_FILES,
    MAX_MANIFEST_BYTES,
    MAX_REVISION_BYTES,
    FrameAsset,
)


def read_frame_bytes(path: Path, limit: int = MAX_MANIFEST_BYTES) -> bytes:
    """Read at most ``limit + 1`` bytes; refuse an oversized or growing stored file."""

    with path.open("rb") as stream:
        payload = stream.read(limit + 1)
    if len(payload) > limit:
        raise ValueError(f"{path.name} exceeds its admitted read bound of {limit} byte(s)")
    return payload


def validate_asset_read_bounds(assets: tuple[FrameAsset, ...]) -> None:
    """A modified manifest cannot authorize more than the admitted revision's population."""

    if len(assets) > MAX_FILES or sum(asset.byte_size for asset in assets) > MAX_REVISION_BYTES:
        raise ValueError("the stored asset descriptors exceed the admitted revision read bounds")
