"""One bounded outcome receipt for callers that joined the same owned host-start attempt.

The home lock owns the receipt and each attempt replaces it. It is an observation, never
installation readiness: only a contending caller can reuse a matching result written since
that caller began waiting. Ordinary status and later starts always observe the host itself.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from agents_remember.kernel.atomic_write import atomic_write_text
from agents_remember.kernel.primitives.paseo_runtime_settings import PaseoRuntimeSettings


def _path(settings: PaseoRuntimeSettings) -> Path:
    return settings.home / "agents-remember" / "start-outcome.json"


def _key(settings: PaseoRuntimeSettings) -> str:
    values = [
        str(settings.install_prefix),
        str(settings.home),
        settings.listen,
        settings.version,
        settings.embed_payload(),
        settings.providers,
    ]
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def write_outcome(
    settings: PaseoRuntimeSettings, state: str, line: str, facts: dict[str, Any]
) -> None:
    atomic_write_text(
        _path(settings),
        json.dumps(
            {
                "key": _key(settings),
                "finished": time.monotonic(),
                "state": state,
                "line": line,
                "facts": facts,
            }
        )
        + "\n",
    )


def joined_outcome(settings: PaseoRuntimeSettings, entered: float) -> dict[str, Any] | None:
    try:
        data = json.loads(_path(settings).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("key") != _key(settings):
        return None
    if not isinstance(data.get("finished"), (int, float)) or data["finished"] < entered:
        return None
    if data.get("state") not in {
        "running",
        "not running",
        "not answering",
        "not installed",
        "not configured",
    }:
        return None
    return (
        data if isinstance(data.get("line"), str) and isinstance(data.get("facts"), dict) else None
    )
