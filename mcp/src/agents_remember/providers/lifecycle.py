#!/usr/bin/env python3
"""Facade for optional Agents Remember context provider lifecycles."""

from __future__ import annotations

import sys

from agents_remember.providers.context import (  # noqa: F401
    ContextProviderError,
    stable_provider_id,
)
from agents_remember.providers.lifecycle_modules.cgc import *  # noqa: F403
from agents_remember.providers.lifecycle_modules.cli import *  # noqa: F403
from agents_remember.providers.lifecycle_modules.cli import main
from agents_remember.providers.lifecycle_modules.common import *  # noqa: F403
from agents_remember.providers.lifecycle_modules.grepai import *  # noqa: F403
from agents_remember.providers.lifecycle_modules.watchers import *  # noqa: F403

__all__ = [name for name in globals() if not name.startswith("_")]


if __name__ == "__main__":
    sys.exit(main())
