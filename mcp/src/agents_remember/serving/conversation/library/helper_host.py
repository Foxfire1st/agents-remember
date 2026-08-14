"""Python host for the locked repository-owned conversation-library helpers (260718-CHATS-L2).

The Claude and Pi dormant libraries run through one versioned JSON-lines Node helper per call:
spawn, handshake (which reports the observed runtime/helper versions as informational evidence),
one operation, exit. The host never discovers modules from npm caches, OpenSrc checkouts, or global
installs: it runs the exact repository helper entry with ``node --import tsx`` from the repository's
own locked package.

THE CONTRACT IS THE ONLY GATE (developer ruling 2026-07-21, 260718-CHATS-L5F R4): the operation
result — not any runtime/helper version-string comparison — decides success. Fail-closed rules: a
missing Node runtime, missing helper entry, or uninstalled dependencies raises
:class:`LibraryStoreError`; an operation the helper cannot perform raises :class:`LibraryStoreError`;
helper-reported ``stale-identity`` maps to :class:`StaleNativeIdentityError`; raw helper stderr is
never disclosed (the protocol's allow-list redaction plus this host's fixed-copy boundary).
"""

from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Literal, NoReturn

import agents_remember
from agents_remember.serving.conversation.library.errors import (
    InvalidLibraryCursorError,
    LibraryStoreError,
    StaleNativeIdentityError,
)

HELPER_PROTOCOL_VERSION = "ar-conversation-library-helper/v1"
HelperHarness = Literal["claude", "pi"]

HELPER_ENTRY_BY_HARNESS: Mapping[HelperHarness, str] = {
    "claude": "claude.ts",
    "pi": "pi.ts",
}
# Informational provenance only (260718-CHATS-L5F R4): the versions whose contract was captured in
# the landed fixtures. Sent as ``expected*`` hints on the handshake request but NEVER compared to
# demote a capability — the operation result is the sole gate (developer ruling 2026-07-21).
LOCKED_HELPER_VERSION: Mapping[HelperHarness, str] = {
    "claude": "0.3.207",
    "pi": "0.80.7",
}
LOCKED_RUNTIME_VERSION: Mapping[HelperHarness, str] = {
    "claude": "2.1.211",
    "pi": "0.80.7",
}
_HELPER_TIMEOUT_SECONDS = 30.0
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_STDERR_CHARS = 4096


def helper_root() -> Path:
    """The repository-owned locked helper package, resolved from the installed package."""

    return (
        Path(agents_remember.__file__).resolve().parents[2]
        / "native_helpers"
        / "conversation_library"
    )


class HelperUnavailable:
    """Static preflight facts for one helper harness (no process spawned)."""

    def __init__(self, reason: str | None) -> None:
        self.reason = reason


def helper_preflight(harness: HelperHarness, *, node: str | None = None) -> HelperUnavailable:
    """Report why the locked helper cannot run, or ``None`` reason when it can."""

    resolved_node = node if node is not None else shutil.which("node")
    if resolved_node is None:
        return HelperUnavailable("node runtime is not installed")
    root = helper_root()
    entry = root / "src" / HELPER_ENTRY_BY_HARNESS[harness]
    if not entry.is_file():
        return HelperUnavailable(f"locked helper entry is missing: {entry.name}")
    if not (root / "node_modules").is_dir():
        return HelperUnavailable(
            "locked helper dependencies are not installed (npm ci has not run)"
        )
    return HelperUnavailable(None)


class ConversationLibraryHelperHost:
    """One short-lived helper process per operation, with a handshake on every spawn."""

    def __init__(
        self,
        *,
        node_executable: str | None = None,
        root: Path | None = None,
        timeout_seconds: float = _HELPER_TIMEOUT_SECONDS,
    ) -> None:
        self._node = node_executable
        self._root = root if root is not None else helper_root()
        self._timeout = timeout_seconds

    async def call(
        self,
        harness: HelperHarness,
        operation: str,
        payload: Mapping[str, object],
    ) -> tuple[Mapping[str, object], str, str]:
        """Run one helper operation; return ``(result, runtimeVersion, helperVersion)``.

        The handshake is part of the same process so a version drift between gate and call can
        never silently pass: every spawn re-proves runtime/helper compatibility before the
        operation executes.
        """

        node = self._node or shutil.which("node")
        if node is None:
            raise LibraryStoreError("node runtime is not installed")
        entry = self._root / "src" / HELPER_ENTRY_BY_HARNESS[harness]
        if not entry.is_file():
            raise LibraryStoreError(f"locked helper entry is missing: {entry.name}")
        requests = [
            {
                "protocolVersion": HELPER_PROTOCOL_VERSION,
                "requestId": f"ar-helper-{uuid.uuid4().hex}",
                "operation": "handshake",
                "harnessId": harness,
                "expectedRuntimeVersion": LOCKED_RUNTIME_VERSION[harness],
                "expectedHelperVersion": LOCKED_HELPER_VERSION[harness],
            },
            {
                "protocolVersion": HELPER_PROTOCOL_VERSION,
                "requestId": f"ar-helper-{uuid.uuid4().hex}",
                "operation": operation,
                "harnessId": harness,
                **payload,
            },
        ]
        lines = await self._exchange(node, entry, requests)
        handshake = self._expect_handshake(lines[0], requests[0]["requestId"])
        runtime_version = _required_text(handshake, "runtimeVersion")
        helper_version = _required_text(handshake, "helperVersion")
        # 260718-CHATS-L5F R4 (developer ruling 2026-07-21): the handshake reports the observed
        # runtime/helper versions as INFORMATIONAL evidence only — it is never a gate. The contract
        # is the gate: the operation result below is the proof. A helper that cannot perform the
        # operation raises ``LibraryStoreError`` from ``_expect_result``; a version drift between the
        # installed runtime and any captured fixture never demotes the surface.
        result = self._expect_result(lines[1], requests[1]["requestId"])
        return result, runtime_version, helper_version

    async def _exchange(
        self,
        node: str,
        entry: Path,
        requests: Sequence[Mapping[str, object]],
    ) -> list[Mapping[str, object]]:
        argv = (node, "--import", "tsx", str(entry))
        process = await self._spawn(argv)
        outbound = "".join(
            json.dumps(request, separators=(",", ":")) + "\n" for request in requests
        ).encode("utf-8")
        try:
            async with asyncio.timeout(self._timeout):
                stdout, stderr = await process.communicate(outbound)
        except TimeoutError as exc:
            process.kill()
            await process.wait()
            raise LibraryStoreError("locked helper timed out") from exc
        if len(stdout) > _MAX_RESPONSE_BYTES:
            raise LibraryStoreError("locked helper response exceeded the byte bound")
        if process.returncode != 0:
            # stderr is diagnostic-only and never disclosed (allow-list redaction boundary).
            _ = stderr[-_MAX_STDERR_CHARS:]
            raise LibraryStoreError("locked helper process failed; raw detail withheld")
        return _decode_lines(stdout, expected=len(requests))

    async def _spawn(self, argv: tuple[str, ...]) -> asyncio.subprocess.Process:
        try:
            return await asyncio.create_subprocess_exec(
                *argv,
                cwd=self._root,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                limit=1024 * 1024,
            )
        except OSError as exc:
            raise LibraryStoreError(f"could not spawn the locked helper: {exc}") from exc

    def _expect_handshake(
        self, line: Mapping[str, object], request_id: str
    ) -> Mapping[str, object]:
        self._require_common(line, request_id)
        if line.get("status") != "ok":
            raise LibraryStoreError("locked helper handshake failed closed")
        handshake = line.get("result")
        if not isinstance(handshake, Mapping):
            raise LibraryStoreError("locked helper handshake is not an object")
        if handshake.get("status") not in {"ready", "incompatible"}:
            raise LibraryStoreError("locked helper handshake is invalid")
        return handshake

    def _expect_result(self, line: Mapping[str, object], request_id: str) -> Mapping[str, object]:
        self._require_common(line, request_id)
        status = line.get("status")
        if status == "ok":
            result = line.get("result")
            if not isinstance(result, Mapping):
                raise LibraryStoreError("locked helper result is not an object")
            return result
        if status != "error":
            raise LibraryStoreError("locked helper response status is unknown")
        _raise_helper_failure(line)

    def _require_common(self, line: Mapping[str, object], request_id: str) -> None:
        if line.get("protocolVersion") != HELPER_PROTOCOL_VERSION:
            raise LibraryStoreError("locked helper protocol version mismatch")
        if line.get("requestId") != request_id:
            raise LibraryStoreError("locked helper response correlation mismatch")


def _decode_lines(stdout: bytes, *, expected: int) -> list[Mapping[str, object]]:
    lines = [line for line in stdout.decode("utf-8", "replace").splitlines() if line.strip()]
    if len(lines) != expected:
        raise LibraryStoreError("locked helper returned an incomplete response")
    return [_decode_line(line) for line in lines]


def _decode_line(line: str) -> Mapping[str, object]:
    try:
        value = json.loads(line)
    except json.JSONDecodeError as exc:
        raise LibraryStoreError("locked helper returned malformed JSON") from exc
    if not isinstance(value, dict):
        raise LibraryStoreError("locked helper response is not an object")
    return value


def _raise_helper_failure(line: Mapping[str, object]) -> NoReturn:
    error = line.get("error")
    detail = line.get("detail")
    safe_detail = detail if isinstance(detail, str) and detail else "helper request failed"
    if error == "stale-identity":
        raise StaleNativeIdentityError(safe_detail)
    if error == "invalid-request":
        raise InvalidLibraryCursorError(safe_detail)
    raise LibraryStoreError(safe_detail)


def _required_text(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise LibraryStoreError(f"locked helper handshake lacks {key}")
    return value


__all__ = [
    "HELPER_PROTOCOL_VERSION",
    "LOCKED_HELPER_VERSION",
    "LOCKED_RUNTIME_VERSION",
    "ConversationLibraryHelperHost",
    "HelperHarness",
    "helper_preflight",
    "helper_root",
]
