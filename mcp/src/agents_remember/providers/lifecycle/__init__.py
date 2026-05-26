#!/usr/bin/env python3
"""Facade for optional Agents Remember context provider lifecycles."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = (
    "agents_remember.providers.context",
    "agents_remember.providers.cgc.lifecycle",
    "agents_remember.providers.grepai.lifecycle",
    "agents_remember.providers.lifecycle.cli",
    "agents_remember.providers.lifecycle.command_runner",
    "agents_remember.providers.lifecycle.compose_runtime",
    "agents_remember.providers.lifecycle.docker_runtime",
    "agents_remember.providers.lifecycle.host_ports",
    "agents_remember.providers.lifecycle.process_status",
    "agents_remember.providers.lifecycle.provider_settings",
    "agents_remember.providers.lifecycle.result_rendering",
    "agents_remember.providers.lifecycle.runtime_environment",
    "agents_remember.providers.lifecycle.state_files",
    "agents_remember.providers.lifecycle.watchers",
)


def __getattr__(name: str) -> Any:
    for module_name in _EXPORT_MODULES:
        module = import_module(module_name)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | {"main"})
