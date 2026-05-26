"""CodeGraphContext provider lifecycle facade."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = (
    "agents_remember.providers.cgc.lifecycle.core",
    "agents_remember.providers.cgc.lifecycle.compose",
    "agents_remember.providers.cgc.lifecycle.backend",
    "agents_remember.providers.cgc.lifecycle.runner",
    "agents_remember.providers.cgc.lifecycle.installation",
    "agents_remember.providers.cgc.lifecycle.process_control",
    "agents_remember.providers.cgc.lifecycle.query",
    "agents_remember.providers.cgc.lifecycle.refresh",
)


def __getattr__(name: str) -> Any:
    for module_name in _EXPORT_MODULES:
        module = import_module(module_name)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
