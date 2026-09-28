"""Pinned public Orca RuntimeClient boundary."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import HTTPException

from agents_remember.kernel.primitives.runtime_config import McpRuntimeConfig

ORCA_RUNTIME_TIMEOUT_SECONDS = 180


def runtime_call(
    config: McpRuntimeConfig,
    command: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    settings = config.orca_runtime
    if settings is None:
        raise OrcaRuntimeFailure(
            "native_runtime_configuration_missing",
            "Native Orca launch requires orcaRuntime.runtimeRoot and orcaRuntime.userDataPath "
            "in the shared Agents Remember MCP settings.",
        )
    node = shutil.which("node")
    script = Path(__file__).with_name("orca_runtime_capabilities.mjs")
    if not node or not script.is_file():
        raise OrcaRuntimeFailure(
            "runtime_boundary_unavailable", "The pinned Orca RuntimeClient boundary is unavailable."
        )
    env = dict(os.environ)
    env.pop("AR_ORCA_CLI", None)
    env["AR_ORCA_RUNTIME_ROOT"] = settings.runtime_root.as_posix()
    env["ORCA_USER_DATA_PATH"] = settings.user_data_path.as_posix()
    try:
        response = subprocess.run(
            [node, script.as_posix(), command],
            input=json.dumps(payload, ensure_ascii=False),
            text=True,
            capture_output=True,
            check=False,
            timeout=ORCA_RUNTIME_TIMEOUT_SECONDS,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise OrcaRuntimeFailure(
            "runtime_unavailable", "The paired Orca runtime did not answer the requested operation."
        ) from error
    try:
        result = json.loads(response.stdout)
    except json.JSONDecodeError as error:
        raise OrcaRuntimeFailure(
            "runtime_invalid_response", "The Orca runtime boundary returned an invalid response."
        ) from error
    if response.returncode != 0 or not isinstance(result, dict):
        error = result.get("error") if isinstance(result, dict) else None
        if isinstance(error, dict):
            code = str(error.get("code") or "orca_runtime_refused")[:100]
            message = str(error.get("message") or "Orca refused the operation.")[:800]
            raise OrcaRuntimeFailure(code, message)
        raise OrcaRuntimeFailure("orca_runtime_refused", "Orca refused the operation.")
    return result


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


def orca_catalog_scope(config: McpRuntimeConfig) -> tuple[str, str]:
    pairing = configured_pairing_code() or ""
    runtime = config.orca_runtime
    runtime_key = digest(
        {
            "pairingFingerprint": hashlib.sha256(pairing.encode("utf-8")).hexdigest(),
            "frameUrl": configured_frame_url(),
            "environment": os.environ.get("ORCA_ENVIRONMENT", "").strip() or None,
            "runtimeRoot": runtime.runtime_root.as_posix() if runtime else None,
            "userDataPath": runtime.user_data_path.as_posix() if runtime else None,
        }
    )
    return runtime_key, config.workspace_root.resolve().as_posix()


def require_pairing() -> None:
    if not configured_pairing_code():
        raise HTTPException(
            status_code=503,
            detail="The dashboard process has no explicit Orca pairing; no session was started.",
        )


class OrcaRuntimeFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
