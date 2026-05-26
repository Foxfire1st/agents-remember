"""Docker-owned GrepAI provider lifecycle facade."""

from __future__ import annotations

from importlib import import_module
from typing import Any

_EXPORT_MODULES = (
    "agents_remember.providers.grepai.lifecycle.core",
    "agents_remember.providers.grepai.lifecycle.compose",
    "agents_remember.providers.grepai.lifecycle.backend",
    "agents_remember.providers.grepai.lifecycle.embedder",
    "agents_remember.providers.grepai.lifecycle.runner",
    "agents_remember.providers.grepai.lifecycle.actions",
)


def __getattr__(name: str) -> Any:
    for module_name in _EXPORT_MODULES:
        module = import_module(module_name)
        if hasattr(module, name):
            value = getattr(module, name)
            globals()[name] = value
            return value
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
