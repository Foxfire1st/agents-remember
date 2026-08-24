"""Strict reader for the atomic current-quality-generation manifest."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Literal

QUALITY_MANIFEST_SCHEMA_VERSION: Final[Literal["1.0"]] = "1.0"
REPORT_SET_MANIFEST = "quality-report-set.json"
_MANIFEST_ERROR = "no complete Dagger report generation is published"
_ALLOWED_ROOT_FIELDS = frozenset({"schemaVersion", "generation", "files", "attestation"})


class PublishedQualityManifestError(RuntimeError):
    """The atomic quality pointer is unreadable or structurally invalid."""


@dataclass(frozen=True)
class PublishedQualityFile:
    sha256: str
    size: int


@dataclass(frozen=True)
class PublishedQualityManifest:
    schema_version: Literal["1.0"]
    generation: str
    files: Mapping[str, PublishedQualityFile]
    attestation: Mapping[str, str] | None

    def require_file(self, name: str) -> PublishedQualityFile:
        """Return one declared file without constructing an unverified path."""

        try:
            return self.files[name]
        except KeyError as error:
            raise PublishedQualityManifestError(_MANIFEST_ERROR) from error


def load_published_quality_manifest(destination: Path) -> PublishedQualityManifest:
    """Read and validate the sole pointer to the current immutable generation."""

    try:
        raw: object = json.loads((destination / REPORT_SET_MANIFEST).read_text(encoding="utf-8"))
        return _parse_manifest(raw)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise PublishedQualityManifestError(_MANIFEST_ERROR) from error


def _parse_manifest(raw: object) -> PublishedQualityManifest:
    if not isinstance(raw, dict):
        raise ValueError("quality manifest root must be an object")
    if set(raw) - _ALLOWED_ROOT_FIELDS:
        raise ValueError("quality manifest contains unsupported fields")
    if raw.get("schemaVersion") != QUALITY_MANIFEST_SCHEMA_VERSION:
        raise ValueError("quality manifest schema version is unsupported")

    generation = raw.get("generation")
    if not isinstance(generation, str) or not re.fullmatch(r"[0-9a-f]{64}", generation):
        raise ValueError("quality manifest generation id is invalid")

    raw_files = raw.get("files")
    if not isinstance(raw_files, dict):
        raise ValueError("quality manifest files must be an object")
    files = {
        name: _parse_file(name, record)
        for name, record in raw_files.items()
        if isinstance(name, str)
    }
    if len(files) != len(raw_files):
        raise ValueError("quality manifest file names must be strings")

    raw_attestation = raw.get("attestation")
    attestation: Mapping[str, str] | None = None
    if raw_attestation is not None:
        if not isinstance(raw_attestation, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in raw_attestation.items()
        ):
            raise ValueError("quality manifest attestation must contain string pairs")
        attestation = MappingProxyType(dict(raw_attestation))

    return PublishedQualityManifest(
        schema_version=QUALITY_MANIFEST_SCHEMA_VERSION,
        generation=generation,
        files=MappingProxyType(files),
        attestation=attestation,
    )


def _parse_file(name: str, raw: object) -> PublishedQualityFile:
    if not name or not isinstance(raw, dict) or set(raw) != {"sha256", "size"}:
        raise ValueError("quality manifest file record is invalid")
    sha256 = raw.get("sha256")
    size = raw.get("size")
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("quality manifest file digest is invalid")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError("quality manifest file size is invalid")
    return PublishedQualityFile(sha256=sha256, size=size)
