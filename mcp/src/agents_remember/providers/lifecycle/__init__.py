#!/usr/bin/env python3
"""Facade for optional Agents Remember context provider lifecycles."""

from __future__ import annotations

from agents_remember.providers.cgc.lifecycle import *  # noqa: F403
from agents_remember.providers.context import (  # noqa: F401
    ContextProviderError,
    stable_provider_id,
)
from agents_remember.providers.grepai.lifecycle import *  # noqa: F403
from agents_remember.providers.lifecycle.cli import *  # noqa: F403
from agents_remember.providers.lifecycle.cli import main as main
from agents_remember.providers.lifecycle.command_runner import *  # noqa: F403
from agents_remember.providers.lifecycle.docker_runtime import *  # noqa: F403
from agents_remember.providers.lifecycle.host_ports import *  # noqa: F403
from agents_remember.providers.lifecycle.process_status import *  # noqa: F403
from agents_remember.providers.lifecycle.provider_settings import *  # noqa: F403
from agents_remember.providers.lifecycle.result_rendering import *  # noqa: F403
from agents_remember.providers.lifecycle.runtime_environment import *  # noqa: F403
from agents_remember.providers.lifecycle.state_files import *  # noqa: F403
from agents_remember.providers.lifecycle.watchers import *  # noqa: F403

__all__ = [name for name in globals() if not name.startswith("_")]
