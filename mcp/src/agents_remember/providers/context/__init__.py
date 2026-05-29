"""Facade for context provider runtime layout and patch helpers."""

from __future__ import annotations

from agents_remember.providers.cgc.context import *  # noqa: F403
from agents_remember.providers.context.common import *  # noqa: F403
from agents_remember.providers.grepai.context import *  # noqa: F403

# No explicit ``__all__``: this facade re-exports every public name from the
# starred submodules (matching the sibling ``cgc/context`` facade). A computed
# ``__all__`` over ``globals()`` is not statically analyzable, and the default
# ``import *`` semantics already export exactly these names.
