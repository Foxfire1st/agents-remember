"""Host port allocation helpers for provider containers."""

from __future__ import annotations

import socket
from typing import Any

from agents_remember.providers.context import ContextProviderError


def host_port_available(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def allocate_host_port(host: str, configured: Any, default: int) -> int:
    text = str(configured) if configured is not None else "auto"
    if text == "auto":
        return allocate_auto_host_port(host, default)

    port = parse_configured_host_port(configured, text)
    require_host_port_available(host, port)
    return port


def allocate_auto_host_port(host: str, default: int) -> int:
    if host_port_available(host, default):
        return default
    return allocate_ephemeral_host_port(host)


def allocate_ephemeral_host_port(host: str) -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def parse_configured_host_port(configured: Any, text: str) -> int:
    try:
        return int(text)
    except ValueError as error:
        raise ContextProviderError(f"invalid host port: {configured}") from error


def require_host_port_available(host: str, port: int) -> None:
    if not host_port_available(host, port):
        raise ContextProviderError(f"configured host port is already in use: {host}:{port}")
