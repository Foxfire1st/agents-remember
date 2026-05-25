"""Provider runner integrity manifest helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agents_remember.mcp.config import McpRuntimeConfig

MANIFEST_VERSION = 1
IGNORED_NAMES = {"__pycache__"}
IGNORED_SUFFIXES = {".pyc", ".pyo"}
WATCHED_PROVIDER_PATHS = (Path("providers") / "_venvs",)
IGNORED_RECORDED_PROVIDER_PATHS = (Path("providers") / "_bin",)


def manifest_path_for_config(config: McpRuntimeConfig) -> Path:
    stem = config.config_path.name
    for suffix in config.config_path.suffixes:
        stem = stem[: -len(suffix)]
    return config.config_path.with_name(f"{stem}.provider-runners.manifest.json")


def write_provider_runner_manifest(config: McpRuntimeConfig) -> dict[str, Any]:
    manifest = {
        "version": MANIFEST_VERSION,
        "coordinationRoot": config.coordination_root.as_posix(),
        "files": _hash_provider_files(config.coordination_root),
    }
    path = manifest_path_for_config(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return {
        "ok": True,
        "state": "recorded",
        "manifestPath": path.as_posix(),
        "fileCount": len(manifest["files"]),
    }


def check_provider_runner_integrity(config: McpRuntimeConfig) -> dict[str, Any]:
    path = manifest_path_for_config(config)
    current = _hash_provider_files(config.coordination_root)
    if not path.exists():
        if not current:
            return {
                "ok": True,
                "state": "notInstalled",
                "manifestPath": path.as_posix(),
                "fileCount": 0,
                "changed": [],
                "missing": [],
            }
        return {
            "ok": False,
            "state": "manifestMissing",
            "manifestPath": path.as_posix(),
            "fileCount": len(current),
            "changed": [],
            "missing": [],
            "unrecorded": sorted(current),
        }

    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return {
            "ok": False,
            "state": "manifestUnreadable",
            "manifestPath": path.as_posix(),
            "error": str(error),
            "changed": [],
            "missing": [],
        }

    recorded = manifest.get("files", {})
    if not isinstance(recorded, dict):
        return {
            "ok": False,
            "state": "manifestInvalid",
            "manifestPath": path.as_posix(),
            "changed": [],
            "missing": [],
        }
    recorded = {
        relative: expected
        for relative, expected in recorded.items()
        if not _ignored_recorded_provider_path(relative)
    }

    changed = sorted(
        relative
        for relative, expected in recorded.items()
        if relative in current and current[relative] != expected
    )
    missing = sorted(relative for relative in recorded if relative not in current)
    ok = not changed and not missing
    return {
        "ok": ok,
        "state": "checked" if ok else "changed",
        "manifestPath": path.as_posix(),
        "fileCount": len(recorded),
        "changed": changed,
        "missing": missing,
    }


def _hash_provider_files(coordination_root: Path) -> dict[str, str]:
    root = coordination_root.resolve()
    files: dict[str, str] = {}
    for watched in WATCHED_PROVIDER_PATHS:
        watched_root = root / watched
        if not watched_root.exists():
            continue
        for path in sorted(watched_root.rglob("*")):
            if not path.is_file() or _ignored(path):
                continue
            relative = path.resolve().relative_to(root).as_posix()
            files[relative] = _sha256(path)
    return files


def _ignored_recorded_provider_path(relative: str) -> bool:
    path = Path(relative)
    return any(
        path == ignored or ignored in path.parents for ignored in IGNORED_RECORDED_PROVIDER_PATHS
    )


def _ignored(path: Path) -> bool:
    return any(part in IGNORED_NAMES for part in path.parts) or path.suffix in IGNORED_SUFFIXES


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
