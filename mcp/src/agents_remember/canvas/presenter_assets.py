"""Read a prospective revision through held directories, within its publication quota."""

from __future__ import annotations

import os
from pathlib import Path

from agents_remember.canvas.presenter_files import ScanRefused, directory_fd, open_at, scan_files
from agents_remember.canvas.presenter_store import PresenterRefused
from agents_remember.models.knowledge.review_frame import FrameLimits


def _working_quota(descriptor: int, limits: FrameLimits) -> None:
    """Refuse a working folder past the draft ceiling a caller named; by default there is none."""

    ceiling = limits.draft_bytes
    if ceiling is None:
        return
    total = 0
    for _relative, _parent, _name, size in scan_files(descriptor):
        total += size
        if total > ceiling:
            raise PresenterRefused(
                "working-quota-exceeded", f"the working folder exceeds its {ceiling}-byte quota"
            )


def _read_assets(descriptor: int, limits: FrameLimits) -> dict[str, bytes]:
    """Every working asset, whole; a ceiling applies only where a caller named one (rule 8)."""

    assets: dict[str, bytes] = {}
    total = 0
    for relative, parent, name, size in scan_files(descriptor):
        if "\\" in relative:
            raise PresenterRefused("escaped-asset", f"the asset path {relative!r} is not admitted")
        _within(limits.files_per_revision, len(assets) + 1, "too-many-assets", f"{relative!r}")
        _within(limits.bytes_per_revision, total + size, "assets-too-large", f"{relative!r}")
        with open_at(parent, name) as handle:
            payload = handle.read()
        total += len(payload)
        _within(limits.bytes_per_revision, total, "assets-too-large", f"{relative!r}")
        assets[relative] = payload
    if not assets:
        raise PresenterRefused("no-working-assets", "the assets folder is empty")
    return assets


def _within(ceiling: int | None, value: int, reason: str, what: str) -> None:
    """Refuse ``what`` when ``value`` passes a ceiling a caller named; ``None`` is no ceiling."""

    if ceiling is not None and value > ceiling:
        raise PresenterRefused(reason, f"{what} passes the named ceiling of {ceiling}")


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
