"""What is left of the ONT host boundary until the Paseo leaves replace its callers.

The Orca bridge script is gone: the launcher catalog, the agent, model and effort validation and
the launch reach the host through ``paseo_bridge.py``, and a launch needs no pairing. The status
and revive call sites that still import :func:`runtime_call` have no Paseo command yet; they are
refused here with a named reason until PNT-R07 moves them onto the bridge.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any
from urllib.parse import urlsplit

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

HOST_CALL_NOT_AVAILABLE = "host_call_not_available"


def runtime_call(
    config: McpRuntimeConfig,
    command: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Refuse an ONT host call that the Paseo bridge does not serve yet."""

    del config, payload
    raise OrcaRuntimeFailure(
        HOST_CALL_NOT_AVAILABLE,
        f"The host call {command!r} belonged to the removed Orca bridge and has no Paseo "
        "bridge command yet: launching arrives with PNT-R03, status and revive with PNT-R07.",
    )


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def configured_frame_url() -> str | None:
    raw = os.environ.get("AR_ORCA_FRAME_URL", "").strip()
    if not raw:
        return None
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"} or (
        parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
    ):
        return None
    return None if parsed.username or parsed.password else raw


def configured_pairing_code() -> str | None:
    value = (
        os.environ.get("ORCA_PAIRING_CODE", "").strip()
        or os.environ.get("ORCA_REMOTE_PAIRING", "").strip()
    )
    return value or None


class OrcaRuntimeFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
