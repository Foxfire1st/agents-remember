"""Durable last successful Paseo answers, merged independently by catalog part."""

from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents_remember.cli.paseo_catalog_parts import merge_reply, merge_selected_reply
from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.file_lock import exclusive_file_lock
from agents_remember.serving.paseo.paseo_plugin_files import AR_HOME_DIRECTORY


def catalog_store_path(home: Path) -> Path:
    return home / AR_HOME_DIRECTORY / "launcher-catalog.json"


class CatalogParts:
    """Read only successful observations; failure never overwrites an answer."""

    def __init__(self, saved: dict[str, Any], read_at: str) -> None:
        self.saved = saved
        self.read_at = read_at
        self.changed = False

    def observe(
        self, key: tuple[str, ...], value: Any, *, valid: bool, error: str | None = None
    ) -> tuple[Any, dict[str, Any]]:
        encoded = json.dumps(key)
        previous = self.saved.get(encoded)
        if valid:
            current = {"value": deepcopy(value), "readAt": self.read_at}
            if previous is None or str(previous.get("readAt", "")) <= self.read_at:
                self.saved[encoded] = current
                self.changed = True
            return deepcopy(value), {"source": "fresh", "readAt": self.read_at}
        state: dict[str, Any] = {"source": "unknown", "readAt": None}
        if error:
            state["error"] = error
        if isinstance(previous, dict) and "value" in previous:
            state.update(source="saved", readAt=previous.get("readAt"))
            return deepcopy(previous["value"]), state
        return None, state

    def retain_children(
        self, prefix: tuple[str, ...], identifiers: set[str], inventory: tuple[str, ...]
    ) -> None:
        """A successful inventory retires parts of providers/models it no longer names."""
        if self.saved.get(json.dumps(inventory), {}).get("readAt") != self.read_at:
            return
        for encoded in tuple(self.saved):
            key = tuple(json.loads(encoded))
            if (
                key[: len(prefix)] == prefix
                and len(key) > len(prefix)
                and key[len(prefix)] not in identifiers
            ):
                del self.saved[encoded]
                self.changed = True


def merge_catalog_parts(
    path: Path, scope: tuple[str, ...], reply: dict[str, Any] | None, error: str | None
) -> dict[str, Any]:
    return _merge_saved(
        path, scope, _read_at(reply), lambda parts: merge_reply(parts, reply or {}, error)
    )


def merge_selected_parts(
    path: Path,
    scope: tuple[str, ...],
    target: tuple[str, str],
    reply: dict[str, Any] | None,
    error: str | None,
) -> dict[str, Any]:
    return _merge_saved(
        path,
        scope,
        _read_at(reply),
        lambda parts: merge_selected_reply(parts, target, reply or {}, error),
    )


def saved_catalog_parts(path: Path, scope: tuple[str, ...]) -> dict[str, Any]:
    """Read the atomic snapshot without creating a lock, directory, or file."""
    result = merge_reply(CatalogParts(_read_saved(path, scope), _read_at(None)), {}, None)
    # This read makes no Paseo request. Absence of a fresh reply here cannot
    # establish the failure that merge_reply reports for an unanswered request.
    result["catalogDiscovery"].pop("error", None)
    return result


def _merge_saved(
    path: Path,
    scope: tuple[str, ...],
    read_at: str,
    render: Callable[[CatalogParts], dict[str, Any]],
) -> dict[str, Any]:
    with exclusive_file_lock(path, "Paseo launcher answers"):
        stored = _read_saved(path, scope)
        observed = CatalogParts(stored, read_at)
        result = render(observed)
        if observed.changed:
            _write_saved(path, scope, stored)
        return result


def _read_at(reply: dict[str, Any] | None) -> str:
    value = None if reply is None else reply.get("readAt")
    return value if isinstance(value, str) and value else datetime.now(UTC).isoformat()


def _read_saved(path: Path, scope: tuple[str, ...]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(value, dict) or value.get("scope") != list(scope):
        return {}
    return value.get("parts", {}) if isinstance(value.get("parts"), dict) else {}


def _write_saved(path: Path, scope: tuple[str, ...], parts: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(
            {"schema": "paseo-launcher-catalog/v1", "scope": scope, "parts": parts}, sort_keys=True
        ),
    )
