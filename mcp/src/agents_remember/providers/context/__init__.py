"""Facade for context provider runtime layout and patch helpers."""

from __future__ import annotations

# Shared helpers live in ``providers.context_common`` — deliberately OUTSIDE
# this package, so modules loaded during ``cgc.context``/``grepai.context``
# package init can import them without re-entering this facade's own
# initialization. When the helpers lived at ``providers.context.common``, any
# import of ``cgc.context`` before ``providers.context`` re-entered this init
# mid-flight, star-collected an empty ``cgc.context``, and left the facade
# permanently missing every CGC name (import-order-dependent ImportError;
# GitHub #58).
from agents_remember.providers.cgc.context import *  # noqa: F403
from agents_remember.providers.context_common import *  # noqa: F403
from agents_remember.providers.grepai.context import *  # noqa: F403

# No explicit ``__all__``: this facade re-exports every public name from the
# starred submodules (matching the sibling ``cgc/context`` facade). A computed
# ``__all__`` over ``globals()`` is not statically analyzable, and the default
# ``import *`` semantics already export exactly these names.
