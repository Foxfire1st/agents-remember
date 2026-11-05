"""Install and revalidate a refreshless native Codex OAuth credential pool."""

from __future__ import annotations

import base64
import binascii
import json
import math
import os
import stat
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from agents_remember.errors import AuthorityError


class AccessOnlyError(AuthorityError):
    """The explicitly supplied access-only credential cannot safely serve a trial."""


_MAX_BYTES = 128 * 1024
_MAX_NODES = 512
_CREDENTIAL_KEYS = {"access_token", "id_token", "account_id"}
_ROW_KEYS = _CREDENTIAL_KEYS | {
    "id",
    "source",
    "auth_type",
    "label",
    "priority",
    "base_url",
    "request_count",
    "last_status",
    "last_status_at",
    "last_error_code",
    "last_error_reason",
    "last_error_message",
    "last_error_reset_at",
    "status_cleared_at",
    "model_cooldowns",
    "expires_at",
    "expires_at_ms",
    "last_refresh",
    "failure_reason",
}


def _directory(path: Path, stack: ExitStack) -> int:
    """Open the named directory without following an ancestor alias."""
    path = path.absolute()
    if ".." in path.parts:
        raise AccessOnlyError("codex-access-path-invalid")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    stack.callback(os.close, descriptor)
    for part in path.parts[1:]:
        descriptor = os.open(part, flags, dir_fd=descriptor)
        stack.callback(os.close, descriptor)
    return descriptor


def _private_home(home: Path, stack: ExitStack) -> int:
    descriptor = _directory(home, stack)
    metadata = os.fstat(descriptor)
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o700:
        raise AccessOnlyError("codex-access-home-not-private")
    return descriptor


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise AccessOnlyError("codex-access-json-duplicate-key")
        result[key] = value
    return result


def _document(raw: bytes) -> dict[str, Any]:
    try:
        document = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs)
    except (UnicodeError, ValueError, RecursionError):
        raise AccessOnlyError("codex-access-json-invalid") from None
    if not isinstance(document, dict):
        raise AccessOnlyError("codex-access-document-invalid")
    _no_refresh(document)
    return document


def _no_refresh(document: dict[str, Any]) -> None:
    pending: list[Any] = [document]
    visited = 0
    while pending:
        current = pending.pop()
        visited += 1
        if visited > _MAX_NODES:
            raise AccessOnlyError("codex-access-document-limit")
        if isinstance(current, dict):
            if "refresh_token" in current:
                raise AccessOnlyError("codex-access-refresh-token-present")
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)


def _file_identity(metadata: os.stat_result) -> tuple[int, ...]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
        metadata.st_mode,
        metadata.st_uid,
        metadata.st_nlink,
    )


def _read_at(parent: int, name: str, stack: ExitStack) -> tuple[dict[str, Any], os.stat_result]:
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    stream = stack.enter_context(os.fdopen(descriptor, "rb"))
    metadata = os.fstat(descriptor)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise AccessOnlyError("codex-access-file-not-private-regular")
    if metadata.st_size > _MAX_BYTES:
        raise AccessOnlyError("codex-access-file-limit")
    raw = stream.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise AccessOnlyError("codex-access-file-limit")
    after = os.fstat(descriptor)
    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if _file_identity(metadata) != _file_identity(after) or _file_identity(after) != _file_identity(
        current
    ):
        raise AccessOnlyError("codex-access-file-changed-during-read")
    return _document(raw), metadata


def _deadline(expected_end: float) -> None:
    if isinstance(expected_end, bool):
        raise AccessOnlyError("codex-access-deadline-invalid")
    try:
        valid = math.isfinite(expected_end) and expected_end > 0
    except (TypeError, OverflowError):
        valid = False
    if not valid:
        raise AccessOnlyError("codex-access-deadline-invalid")


def _credentials(row: dict[str, Any], expected_end: float) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in _CREDENTIAL_KEYS:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            raise AccessOnlyError("codex-access-credential-missing")
        result[key] = value
    parts = result["access_token"].split(".")
    if len(parts) != 3:
        raise AccessOnlyError("codex-access-jwt-invalid")
    try:
        encoded = parts[1].encode("ascii")
        payload = base64.b64decode(
            encoded + b"=" * (-len(encoded) % 4), altchars=b"-_", validate=True
        )
        claims = _document(payload)
    except (ValueError, UnicodeError, binascii.Error):
        raise AccessOnlyError("codex-access-jwt-invalid") from None
    expiry = claims.get("exp")
    if type(expiry) is not int:
        raise AccessOnlyError("codex-access-expiry-invalid")
    if expiry < expected_end:
        raise AccessOnlyError("codex-access-expiry-insufficient")
    return result


def _pool_row(document: dict[str, Any]) -> dict[str, Any]:
    if set(document) - {"credential_pool", "providers", "version", "updated_at"}:
        raise AccessOnlyError("codex-access-store-invalid")
    if document.get("providers", {}) != {}:
        raise AccessOnlyError("codex-access-provider-not-admitted")
    pool = document.get("credential_pool")
    if not isinstance(pool, dict) or set(pool) != {"openai-codex"}:
        raise AccessOnlyError("codex-access-pool-invalid")
    rows = pool["openai-codex"]
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise AccessOnlyError("codex-access-pool-identity-invalid")
    row = rows[0]
    if set(row) - _ROW_KEYS:
        raise AccessOnlyError("codex-access-row-invalid")
    if (row.get("id"), row.get("source"), row.get("auth_type")) != (
        "trial-access",
        "manual",
        "oauth",
    ):
        raise AccessOnlyError("codex-access-pool-identity-invalid")
    return row


def _receipt(home: Path, row: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": "ar-codex-access-only/v1",
        "status": "valid",
        "authPath": str(home.absolute() / "auth.json"),
        "provider": "openai-codex",
        "source": "manual",
        "authType": "oauth",
        "poolRows": 1,
        "refreshTokenPresent": False,
        "validThroughExpectedEnd": True,
        "inventory": [{"path": "auth.json", "poolRowFieldNames": sorted(row), "mode": "0600"}],
    }


def install_access_pool(source: Path, home: Path, expected_end: float) -> dict[str, Any]:
    """Install only supplied access material into a fresh private native pool."""
    _deadline(expected_end)
    try:
        with ExitStack() as stack:
            parent = _directory(source.parent, stack)
            supplied, _ = _read_at(parent, source.name, stack)
            if set(supplied) != _CREDENTIAL_KEYS:
                raise AccessOnlyError("codex-access-source-schema-invalid")
            credentials = _credentials(supplied, expected_end)
            # Manual OAuth rows survive native source pruning without external adoption.
            row = {"id": "trial-access", "source": "manual", "auth_type": "oauth", **credentials}
            encoded = json.dumps({"credential_pool": {"openai-codex": [row]}}).encode("utf-8")
            if len(encoded) > _MAX_BYTES:
                raise AccessOnlyError("codex-access-file-limit")
            descriptor = _private_home(home, stack)
            file = os.open(
                "auth.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=descriptor,
            )
            try:
                with os.fdopen(file, "wb") as stream:
                    os.fchmod(stream.fileno(), 0o600)
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                os.unlink("auth.json", dir_fd=descriptor)
                raise
    except OSError:
        raise AccessOnlyError("codex-access-install-file-refused") from None
    return _receipt(home, row)


def validate_access_pool(home: Path, expected_end: float) -> dict[str, Any]:
    """Recheck refresh absence and lifetime after native pool normalization."""
    _deadline(expected_end)
    try:
        with ExitStack() as stack:
            descriptor = _private_home(home, stack)
            document, metadata = _read_at(descriptor, "auth.json", stack)
            if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600:
                raise AccessOnlyError("codex-access-auth-not-private")
            row = _pool_row(document)
            _credentials(row, expected_end)
    except OSError:
        raise AccessOnlyError("codex-access-validation-file-refused") from None
    return _receipt(home, row)
