"""Process status and namespace checks for provider lifecycle commands."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import Any

from agents_remember.providers.context import ContextProviderError


def process_namespace_warning() -> str | None:
    if os.name == "nt":
        return None

    try:
        init_cmdline = (
            Path("/proc/1/cmdline")
            .read_bytes()
            .replace(b"\x00", b" ")
            .decode("utf-8", errors="replace")
            .strip()
        )
    except OSError:
        return None

    if "--die-with-parent" not in init_cmdline:
        return None

    supervisor = init_cmdline.split(maxsplit=1)[0] if init_cmdline else "PID 1"
    return (
        f"current PID namespace is supervised by {supervisor} with --die-with-parent; "
        "background provider processes started here may be killed when this command exits, "
        "and host-side provider PIDs may not be visible from this namespace"
    )


def process_namespace_status() -> dict[str, Any]:
    warning = process_namespace_warning()
    return {
        "durableForDaemons": warning is None,
        "warning": warning,
    }


def require_durable_process_namespace(action: str) -> None:
    warning = process_namespace_warning()
    if warning is None:
        return
    raise ContextProviderError(
        f"{action} manages long-running provider processes and must run outside this ephemeral process namespace: "
        f"{warning}. Run the lifecycle command from a normal host terminal or another durable host execution context."
    )


def process_alive(pid: int) -> bool:
    if os.name == "nt":
        return windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def windows_process_alive(pid: int) -> bool:
    kernel32 = ctypes.windll.kernel32
    process_query_limited_information = 0x1000
    still_active = 259
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        # Access denied means the PID exists but cannot be queried by this
        # process token. Other OpenProcess failures usually mean no process.
        return ctypes.get_last_error() == 5
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def process_cmdline(pid: int) -> str:
    proc_cmdline = Path("/proc") / str(pid) / "cmdline"
    if not proc_cmdline.exists():
        return ""
    try:
        return (
            proc_cmdline.read_bytes()
            .replace(b"\x00", b" ")
            .decode("utf-8", errors="replace")
            .strip()
        )
    except OSError:
        return ""
