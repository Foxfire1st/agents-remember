"""The pinned offline kit the product ships (MIK-R104 rule 12, L104-B13).

A page that wants a kit library names it with a path relative to its own revision — the kit
manifest's contract, e.g. ``kit/shiki/4.5.0/shiki-kit.js``. The revision then has to **carry** those
files as admitted assets under exactly that relative path: the host serves a revision's own assets
and nothing else (rule 3), so a kit file that is not in the revision is not reachable, and a CDN
reference is not part of the kit at all.

This module is the bridge between the two: it reads the shipped files out of the package data and
hands them to a revision under the contract path, checking each file's size and digest against the
manifest so a tampered or mis-versioned copy cannot be carried silently.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "KIT_CONTRACT_PREFIX",
    "KitEntry",
    "KitFile",
    "KitRefused",
    "kit_assets",
    "kit_files",
    "kit_manifest",
]

#: Where a revision carries kit files, and how a page names one (the manifest's own contract).
KIT_CONTRACT_PREFIX = "kit/"

#: The shipped kit, inside the package: ``package_data/canvas/kit/``.
KIT_ROOT = Path(__file__).resolve().parent.parent / "package_data" / "canvas" / "kit"
KIT_MANIFEST_NAME = "manifest.json"


class KitRefused(ValueError):
    """A kit file a revision asked for is not shipped, or its bytes are not the manifest's."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class KitFile:
    """One shipped file: its kit-relative path, its size and its digest."""

    path: str
    bytes: int
    sha256: str

    @property
    def contract_path(self) -> str:
        """The path the revision carries it under, which is the path a page names."""

        return f"{KIT_CONTRACT_PREFIX}{self.path}"


@dataclass(frozen=True)
class KitEntry:
    """One pinned library: identity, licence and the files that ship with it."""

    id: str
    name: str
    version: str
    licence: str
    purpose: str
    entry: str
    files: tuple[KitFile, ...]
    notices: tuple[KitFile, ...]


def _manifest_payload(root: Path | None = None) -> dict[str, object]:
    path = (root or KIT_ROOT) / KIT_MANIFEST_NAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise KitRefused("kit-absent", f"the kit manifest could not be read: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        raise KitRefused("kit-invalid", f"{path} carries no entries")
    return payload


def kit_manifest(root: Path | None = None) -> tuple[KitEntry, ...]:
    """Every pinned entry, as the manifest declares it."""

    entries: list[KitEntry] = []
    for raw in _manifest_payload(root)["entries"]:  # type: ignore[index]
        if not isinstance(raw, dict):
            raise KitRefused("kit-invalid", "a kit entry is not an object")
        # The manifest lists a file's path inside its entry ("shiki-kit.js"); the kit path a page
        # names carries the entry and version in front of it ("shiki/4.5.0/shiki-kit.js").
        prefix = f"{raw['id']}/{raw['version']}/"
        files = tuple(
            KitFile(
                path=f"{prefix}{item['path']}",
                bytes=int(item["bytes"]),
                sha256=str(item["sha256"]),
            )
            for item in raw.get("files", [])
            if isinstance(item, dict)
        )
        entries.append(
            KitEntry(
                id=str(raw["id"]),
                name=str(raw["name"]),
                version=str(raw["version"]),
                licence=str(raw["licence"]),
                purpose=str(raw.get("purpose", "")),
                entry=str(raw.get("entry", "")),
                files=files,
                notices=tuple(
                    KitFile(
                        path=f"{prefix}{item['file']}",
                        bytes=int(item["bytes"]),
                        sha256=str(item["sha256"]),
                    )
                    for item in raw.get("dependencyLicences", [])
                ),
            )
        )
    return tuple(entries)


def kit_files(root: Path | None = None) -> dict[str, KitFile]:
    """Every shipped file by kit-relative path, e.g. ``shiki/4.5.0/shiki-kit.js``."""

    index: dict[str, KitFile] = {}
    for entry in kit_manifest(root):
        for item in (*entry.files, *entry.notices):
            # Two entries can ship a file of the same name in different versions; the path carries
            # the version, so a collision is a manifest error rather than a silent overwrite.
            if item.path in index:
                raise KitRefused("kit-invalid", f"two entries ship {item.path!r}")
            index[item.path] = item
    return index


def _read_verified(path: str, expected: KitFile, root: Path) -> bytes:
    payload = (root / path).read_bytes()
    if len(payload) != expected.bytes:
        raise KitRefused(
            "kit-size-mismatch",
            f"{path} is {len(payload)} bytes; the manifest declares {expected.bytes}",
        )
    digest = hashlib.sha256(payload).hexdigest()
    if digest != expected.sha256:
        raise KitRefused(
            "kit-digest-mismatch",
            f"{path} hashes to {digest}; the manifest declares {expected.sha256}",
        )
    return payload


def kit_assets(paths: list[str] | tuple[str, ...], root: Path | None = None) -> dict[str, bytes]:
    """The named kit files as revision assets, under the contract path a page names them by.

    ``paths`` are kit-relative (``shiki/4.5.0/shiki-kit.js``); each returned key is prefixed with
    :data:`KIT_CONTRACT_PREFIX`, so a page's ``kit/shiki/4.5.0/shiki-kit.js`` reference resolves
    inside its own revision. An unknown path, a wrong size or a wrong digest is refused by name.
    """

    base = root or KIT_ROOT
    index = kit_files(base)
    carried: dict[str, bytes] = {}
    selected = set(paths)
    for entry in kit_manifest(base):
        if selected.intersection(item.path for item in (*entry.files, *entry.notices)):
            # Selecting a library carries its attribution and dependency notices with the
            # revision. The generated page cannot silently strip a bundled dependency's licence.
            selected.update(item.path for item in entry.notices)
            selected.update(
                item.path for item in entry.files if not item.path.endswith((".js", ".css"))
            )
    for path in sorted(selected):
        item = index.get(path)
        if item is None:
            raise KitRefused("kit-unknown-file", f"{path!r} is not a file this kit ships")
        try:
            carried[item.contract_path] = _read_verified(path, item, base)
        except OSError as error:
            raise KitRefused("kit-file-absent", f"{path}: {error}") from error
    return carried
