"""What a memory-content commit never contains, declared once.

Four seams create memory-content commits — baseline adoption (``memory/baseline.py``), memory
carryover (``memory/carryover.py``), external closeout
(``worktrees/modules/closeout_external.py``) and direct landing
(``worktrees/integration/direct_landing/direct_landing_execution.py``). Each of them used to
spell ``("memory.md",)`` inline, so the set was four copies of one decision and a fifth
exclusion could not be added to all of them by reading any one of them.

The absorbed onboarding-reform obligation is explicit that ``bootstrap/`` is scaffolding and
is never committed, neither by the first baseline nor by any later memory commit. That makes
the set shared policy, so it lives here — the lowest layer all four seams can read — rather
than in whichever module happened to need it first.

**These are worktree-relative path names, not Git pathspecs.** The staging helpers take names:
:func:`~agents_remember.worktrees.modules.git.stage_worktree_content` runs
``git add -A -- . :(top,exclude)<name>`` *and* ``git update-index --force-remove -- <name>``
for every entry. A pathspec string passed here would be mangled into
``:(top,exclude):(exclude)bootstrap`` and would match nothing.
"""

from __future__ import annotations

#: The computed ledger cache. It is derived from the memory content and committed by its own
#: transaction, never carried along with the content it was computed from.
LEDGER_CACHE_RELATIVE_PATH = "memory.md"

#: Transient bootstrap scaffolding. Never part of a memory commit.
BOOTSTRAP_SCAFFOLDING_RELATIVE_PATH = "bootstrap"

#: Every path a memory-content commit excludes, in one place.
MEMORY_CONTENT_EXCLUDES: tuple[str, ...] = (
    LEDGER_CACHE_RELATIVE_PATH,
    BOOTSTRAP_SCAFFOLDING_RELATIVE_PATH,
)

__all__ = [
    "BOOTSTRAP_SCAFFOLDING_RELATIVE_PATH",
    "LEDGER_CACHE_RELATIVE_PATH",
    "MEMORY_CONTENT_EXCLUDES",
]
