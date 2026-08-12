"""Fail-closed POSIX subprocess boundaries for WSL-hosted automation."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path

_DRIVE_PATH = re.compile(r"^[A-Za-z]:[\\/]")
_WSL_WINDOWS_MOUNT = re.compile(r"^/(?:mnt/[A-Za-z]|run/desktop/mnt/host/[A-Za-z])(?:/|$)")
_WINDOWS_EXECUTABLE_SUFFIXES = {".bat", ".cmd", ".com", ".exe"}


def windows_interop_reason(value: str | Path, *, platform: str | None = None) -> str | None:
    """Explain why a path would cross into Windows from a POSIX runner."""
    if (platform or os.name) == "nt":
        return None
    text = os.fspath(value).strip()
    normalized = text.replace("\\", "/")
    reason: str | None = None
    if normalized.startswith("//"):
        reason = "UNC path"
    elif _DRIVE_PATH.match(text):
        reason = "Windows drive path"
    elif _WSL_WINDOWS_MOUNT.match(normalized):
        reason = "Windows-mounted WSL path"
    elif Path(normalized).suffix.lower() in _WINDOWS_EXECUTABLE_SUFFIXES:
        reason = "Windows executable or command shim"
    candidate = Path(text)
    if reason is None and candidate.exists():
        resolved = candidate.resolve().as_posix()
        if _WSL_WINDOWS_MOUNT.match(resolved):
            reason = "path resolves into a Windows-mounted filesystem"
    return reason


def native_subprocess_environment(
    base: Mapping[str, str],
    *,
    temp_root: Path,
    platform: str | None = None,
) -> dict[str, str]:
    """Build a runner environment without inherited Windows tools or scratch paths."""
    current_platform = platform or os.name
    env = native_path_environment(base, platform=current_platform)
    if current_platform == "nt":
        return env
    reason = windows_interop_reason(temp_root, platform=current_platform)
    if reason is not None:
        raise RuntimeError(
            f"quality temp root must be native POSIX storage: {temp_root} ({reason})"
        )
    temp_root.mkdir(parents=True, exist_ok=True)
    env.update({name: temp_root.as_posix() for name in ("TMPDIR", "TMP", "TEMP")})
    return env


def native_path_environment(
    base: Mapping[str, str], *, platform: str | None = None
) -> dict[str, str]:
    """Remove Windows interop entries from PATH without choosing a scratch directory."""
    current_platform = platform or os.name
    env = dict(base)
    if current_platform == "nt":
        return env
    path_entries = [
        entry
        for entry in env.get("PATH", "").split(os.pathsep)
        if entry and windows_interop_reason(entry, platform=current_platform) is None
    ]
    if not path_entries:
        raise RuntimeError(
            "quality runner has no native POSIX PATH entries after rejecting Windows interop paths"
        )
    env["PATH"] = os.pathsep.join(path_entries)
    return env


def resolve_native_executable(
    executable: str,
    env: Mapping[str, str],
    *,
    platform: str | None = None,
) -> str:
    """Resolve one executable and refuse mounted Windows binaries and command shims."""
    current_platform = platform or os.name
    direct = os.sep in executable or (os.altsep is not None and os.altsep in executable)
    resolved = executable if direct else shutil.which(executable, path=env.get("PATH"))
    if resolved is None:
        raise RuntimeError(f"native executable is unavailable on PATH: {executable}")
    reason = windows_interop_reason(resolved, platform=current_platform)
    if reason is not None:
        raise RuntimeError(
            f"refusing incompatible subprocess executable {resolved!r}: {reason}; "
            "install the Linux tool inside WSL or use the clean-container quality executor"
        )
    return resolved


def native_command(
    command: Sequence[str], env: Mapping[str, str], *, platform: str | None = None
) -> list[str]:
    """Return a command whose program is an explicitly resolved native executable."""
    if not command:
        raise ValueError("subprocess command must not be empty")
    return [
        resolve_native_executable(command[0], env, platform=platform),
        *command[1:],
    ]
