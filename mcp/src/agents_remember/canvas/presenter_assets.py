"""Read a prospective revision through held directories, within its publication quota."""

from __future__ import annotations

import os
from pathlib import Path

from agents_remember.canvas.presenter_files import ScanRefused, directory_fd, open_at, scan_files
from agents_remember.canvas.presenter_store import PresenterRefused
from agents_remember.models.knowledge.review_frame import (
    MAX_DRAFT_BYTES,
    MAX_FILES,
    MAX_REVISION_BYTES,
    FrameLimits,
)

MAX_ASSET_DEPTH = 8
MAX_WORKING_ENTRIES = 1024


def _scan(descriptor: int):
    return scan_files(descriptor, depth_limit=MAX_ASSET_DEPTH, entry_limit=MAX_WORKING_ENTRIES)


def _working_quota(descriptor: int, limits: FrameLimits) -> None:
    ceiling = min(limits.draft_bytes, MAX_DRAFT_BYTES)
    total = 0
    for _relative, _parent, _name, size in _scan(descriptor):
        total += size
        if total > ceiling:
            raise PresenterRefused(
                "working-quota-exceeded", f"the working folder exceeds its {ceiling}-byte quota"
            )


def _read_assets(descriptor: int, limits: FrameLimits) -> dict[str, bytes]:
    file_cap = min(limits.files_per_revision, MAX_FILES)
    byte_cap = min(limits.bytes_per_revision, MAX_REVISION_BYTES)
    assets: dict[str, bytes] = {}
    total = 0
    for relative, parent, name, size in _scan(descriptor):
        if len(relative) > 300 or "\\" in relative:
            raise PresenterRefused("escaped-asset", f"the asset path {relative!r} is not admitted")
        if len(assets) >= file_cap:
            raise PresenterRefused("too-many-assets", f"more than {file_cap} assets")
        if total + size > byte_cap:
            raise PresenterRefused("assets-too-large", f"{relative!r} exceeds {byte_cap} bytes")
        with open_at(parent, name) as handle:
            payload = handle.read(byte_cap - total + 1)
        total += len(payload)
        if total > byte_cap:
            raise PresenterRefused("assets-too-large", f"{relative!r} grew past {byte_cap} bytes")
        assets[relative] = payload
    if not assets:
        raise PresenterRefused("no-working-assets", "the assets folder is empty")
    return assets


def collect_assets(working: Path, limits: FrameLimits) -> dict[str, bytes]:
    try:
        with directory_fd(working) as held:
            _working_quota(held, limits)
            assets = os.open("assets", os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=held)
            try:
                return _read_assets(assets, limits)
            finally:
                os.close(assets)
    except ScanRefused as error:
        raise PresenterRefused(error.reason, str(error)) from error
    except OSError as error:
        raise PresenterRefused(
            "unsafe-asset-path", f"the asset directory cannot be opened: {error}"
        ) from error
