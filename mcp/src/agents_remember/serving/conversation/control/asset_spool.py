"""Attachment spool: the staged-bytes filesystem boundary (260718-CHATS-L3).

Every staged byte lives inside the session's user-private asset spool (the L2E
endpoint convention ``<endpoint-root>/assets/<requestId>/<assetId>``), written
through constructed paths only, with resolve-and-verify containment, safe path
components (non-empty, ≤255 bytes, no separators/dot-segments), and private
permissions (0700 dirs, 0600 files). Bytes are digest-computed at stage time
and re-verified at rebind. This module owns the disk mechanics and the staged
asset data types; the lifecycle policy lives in ``attachments.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Literal

from agents_remember.kernel.atomic_write import atomic_replace
from agents_remember.serving.conversation.control.service import (
    CapabilityRefusedError,
    OperationRejectedError,
)
from agents_remember.serving.conversation.models import (
    AccessibleLabelProvenance,
    AttachmentCapability,
)
from agents_remember.serving.harness_control_models import (
    AssetReference,
    read_asset_bytes,
)


@dataclass(frozen=True)
class StagedUpload:
    """One caller upload, already byte-bounded at the transport edge."""

    kind: Literal["image", "file", "resource"]
    name: str
    mime_type: str
    alt: str | None
    data: bytes


@dataclass
class AssetRecord:
    asset_id: str
    kind: Literal["image", "file", "resource"]
    name: str
    mime_type: str
    size_bytes: int
    sha256: str
    alt: str
    alt_provenance: AccessibleLabelProvenance
    spool_path: Path
    consumed: bool = False

    def reference(self) -> AssetReference:
        return AssetReference(
            asset_id=self.asset_id,
            mime_type=self.mime_type,
            byte_size=self.size_bytes,
            sha256=self.sha256,
        )


def stage_one(
    kind_capabilities: dict[str, AttachmentCapability],
    assets_root: Path,
    request_id: str,
    upload: StagedUpload,
    *,
    position: int,
) -> AssetRecord:
    """Gate, validate, and spool one upload under its exact binding."""

    capability = kind_capabilities.get(upload.kind)
    if capability is None or capability.state != "supported":
        reason = capability.reason if capability is not None else "unknown attachment kind"
        raise CapabilityRefusedError(reason)
    validate_upload(upload, capability)
    return _stage_bytes(assets_root, request_id, upload, position=position)


def validate_upload(upload: StagedUpload, capability: AttachmentCapability) -> None:
    if upload.mime_type not in capability.allowed_mime_types:
        raise OperationRejectedError(
            f"asset MIME type {upload.mime_type!r} is not in the fixture-backed allow-list"
        )
    if not 1 <= len(upload.data) <= capability.max_bytes:
        raise OperationRejectedError(
            f"asset byte size {len(upload.data)} exceeds the {capability.max_bytes}-byte limit"
        )
    if upload.alt is None and capability.description == "required":
        raise OperationRejectedError("this attachment kind requires a supplied description")
    if not upload.name:
        raise OperationRejectedError("asset name must be non-empty")


def _stage_bytes(
    assets_root: Path, request_id: str, upload: StagedUpload, *, position: int
) -> AssetRecord:
    asset_id = f"asset-{position}-{os.urandom(6).hex()}"
    path = confined_path(assets_root, request_id, asset_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    digest = sha256(upload.data).hexdigest()
    path.write_bytes(upload.data)
    path.chmod(0o600)
    alt, provenance = alt_for(upload)
    return AssetRecord(
        asset_id=asset_id,
        kind=upload.kind,
        name=upload.name,
        mime_type=upload.mime_type,
        size_bytes=len(upload.data),
        sha256=digest,
        alt=alt,
        alt_provenance=provenance,
        spool_path=path,
    )


def exchange_bytes(
    assets_root: Path, request_id: str, source: AssetRecord, *, position: int
) -> AssetRecord:
    """Atomically move recoverable bytes into a fresh one-use staged identity."""

    asset_id = f"asset-{position}-{os.urandom(6).hex()}"
    target = confined_path(assets_root, request_id, asset_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.parent.chmod(0o700)
    atomic_replace(source.spool_path, target)
    target.chmod(0o600)
    return AssetRecord(
        asset_id=asset_id,
        kind=source.kind,
        name=source.name,
        mime_type=source.mime_type,
        size_bytes=source.size_bytes,
        sha256=source.sha256,
        alt=source.alt,
        alt_provenance=source.alt_provenance,
        spool_path=target,
    )


def verify_recoverable_bytes(asset: AssetRecord) -> None:
    digest, size, _bytes = read_asset_bytes(asset.spool_path)
    if digest != asset.sha256 or size != asset.size_bytes:
        raise OperationRejectedError("recoverable staged bytes failed digest verification")


def alt_for(upload: StagedUpload) -> tuple[str, AccessibleLabelProvenance]:
    if upload.alt:
        return upload.alt, "supplied-description"
    return f"{upload.name}, {upload.mime_type}", "filename-mime-fallback"


def delete_asset_bytes(assets: list[AssetRecord]) -> None:
    """Delete every staged byte for one operation and its empty request dir."""

    root = assets[0].spool_path.parent if assets else None
    for asset in assets:
        asset.spool_path.unlink(missing_ok=True)
    if root is not None and root.is_dir() and not any(root.iterdir()):
        root.rmdir()


def confined_path(assets_root: Path, request_id: str, asset_id: str) -> Path:
    root = assets_root.resolve()
    candidate = (root / request_id / asset_id).resolve()
    if not candidate.is_relative_to(root):
        raise OperationRejectedError("staged asset path escapes the private spool")
    return candidate


def require_safe_component(value: str, *, label: str) -> None:
    if (
        not value
        or len(value.encode("utf-8")) > 255
        or "/" in value
        or "\x00" in value
        or value in {".", ".."}
    ):
        raise OperationRejectedError(f"{label} is not a safe staging component")


def wire_asset(record: AssetRecord) -> dict[str, object]:
    return {
        "assetId": record.asset_id,
        "mimeType": record.mime_type,
        "byteSize": record.size_bytes,
        "sha256": record.sha256,
    }


def upload_identity(upload: StagedUpload) -> dict[str, object]:
    return {
        "kind": upload.kind,
        "name": upload.name,
        "mimeType": upload.mime_type,
        "sha256": sha256(upload.data).hexdigest(),
        "alt": upload.alt,
    }


def upload_identity_from_record(asset: AssetRecord) -> dict[str, object]:
    return {
        "kind": asset.kind,
        "name": asset.name,
        "mimeType": asset.mime_type,
        "sha256": asset.sha256,
        "alt": asset.alt,
    }
