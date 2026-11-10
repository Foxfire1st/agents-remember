"""Platform-native socket and argument ownership for dashboard supervision."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import ipaddress
import socket
import struct
import subprocess
import sys
from pathlib import Path

PROC_ROOT = Path("/proc")
PROCESS_PLATFORM = sys.platform
_CMDLINE_MARKERS = (b"agents_remember", b"agents-remember")
_NATIVE_PROBE_SECONDS = 2.0


def listening_dashboard_pid(host: str, port: int) -> int | None:
    if PROCESS_PLATFORM == "linux":
        return _linux_listener_pid(host, port)
    if PROCESS_PLATFORM == "darwin":
        for pid in _darwin_listener_pids(host, port):
            command = b"\0".join(process_arguments(pid))
            if b"dashboard" in command and any(marker in command for marker in _CMDLINE_MARKERS):
                return pid
        return None
    raise OSError(errno.ENOSYS, f"dashboard socket ownership is unavailable on {PROCESS_PLATFORM}")


def process_arguments(pid: int) -> list[bytes]:
    if PROCESS_PLATFORM == "linux":
        return (PROC_ROOT / str(pid) / "cmdline").read_bytes().split(b"\0")
    if PROCESS_PLATFORM == "darwin":
        return _darwin_process_arguments(pid)
    raise OSError(errno.ENOSYS, f"dashboard arguments are unavailable on {PROCESS_PLATFORM}")


def _darwin_listener_pids(host: str, port: int) -> list[int]:
    address = f"[{host}]" if ":" in host else host
    result = subprocess.run(
        ["/usr/sbin/lsof", "-nP", "-a", f"-iTCP@{address}:{port}", "-sTCP:LISTEN", "-F0p"],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=_NATIVE_PROBE_SECONDS,
        check=False,
    )
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return []
    if result.returncode != 0:
        raise OSError(
            f"lsof could not identify the listening socket: {result.stderr.decode(errors='replace').strip()}"
        )
    fields = [field.strip(b"\n") for field in result.stdout.split(b"\0")]
    pids = list(
        dict.fromkeys(
            int(field[1:]) for field in fields if field.startswith(b"p") and field[1:].isdigit()
        )
    )
    if not pids:
        raise OSError("lsof returned no parseable listening process identity")
    return pids


def _darwin_process_arguments(pid: int) -> list[bytes]:
    # XNU exposes exact argv as argc, executable path, NUL padding, then argc
    # NUL-terminated arguments. Unlike ps output, spaces stay inside their argument.
    sysctl = ctypes.CDLL(None, use_errno=True).sysctl
    sysctl.argtypes = [
        ctypes.POINTER(ctypes.c_int),
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    sysctl.restype = ctypes.c_int
    mib = (ctypes.c_int * 3)(1, 49, pid)  # CTL_KERN, KERN_PROCARGS2, process id
    size = ctypes.c_size_t()
    if sysctl(mib, 3, None, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), f"cannot inspect dashboard process {pid} argv")
    buffer = ctypes.create_string_buffer(size.value)
    if sysctl(mib, 3, buffer, ctypes.byref(size), None, 0) != 0:
        raise OSError(ctypes.get_errno(), f"cannot read dashboard process {pid} argv")
    return _decode_darwin_arguments(buffer.raw[: size.value])


def _decode_darwin_arguments(data: bytes) -> list[bytes]:
    if len(data) < struct.calcsize("i"):
        raise OSError("the dashboard process argument header is incomplete")
    count = struct.unpack_from("i", data)[0]
    start = data.find(b"\0", struct.calcsize("i"))
    if count <= 0 or start < 0:
        raise OSError("the dashboard process argument header is invalid")
    start += 1
    while start < len(data) and data[start] == 0:
        start += 1
    arguments = data[start:].split(b"\0")
    if len(arguments) <= count:
        raise OSError("the dashboard process argument list is incomplete")
    return arguments[:count]


def _linux_listener_pid(host: str, port: int) -> int | None:
    """Find a dashboard process holding the socket for this exact address and port."""
    inodes = _listening_inodes(host, port)
    if not inodes:
        return None
    sockets = {f"socket:[{inode}]" for inode in inodes}
    for process in PROC_ROOT.iterdir():
        if not process.name.isdigit():
            continue
        with contextlib.suppress(OSError):
            if not pid_is_dashboard(int(process.name)):
                continue
            for descriptor in (process / "fd").iterdir():
                with contextlib.suppress(OSError):
                    if descriptor.readlink().as_posix() in sockets:
                        return int(process.name)
    return None


def pid_is_dashboard(pid: int) -> bool:
    """The process command must name the product's dashboard, not a reused PID."""
    try:
        command = b"\0".join(process_arguments(pid))
    except OSError:
        return False
    return b"dashboard" in command and any(marker in command for marker in _CMDLINE_MARKERS)


def _listening_inodes(host: str, port: int) -> set[str]:
    addresses = {row[4][0] for row in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    inodes: set[str] = set()
    for table in (PROC_ROOT / "net/tcp", PROC_ROOT / "net/tcp6"):
        with contextlib.suppress(OSError, ValueError):
            for line in table.read_text().splitlines()[1:]:
                fields = line.split()
                encoded_address, encoded_port = fields[1].split(":")
                if fields[3] != "0A" or int(encoded_port, 16) != port:
                    continue
                raw = bytes.fromhex(encoded_address)
                # procfs writes each address word in native little-endian order.
                raw = b"".join(raw[index : index + 4][::-1] for index in range(0, len(raw), 4))
                address = str(ipaddress.ip_address(raw))
                if address in addresses or address in ("0.0.0.0", "::"):
                    inodes.add(fields[9])
    return inodes
