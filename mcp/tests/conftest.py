"""Session-wide test setup that keeps the suite hermetic and safe to run anywhere.

Several fixtures create throwaway git repositories and ``git commit`` into them.
They run ``git`` with ``cwd=<temp repo>`` but inherit the process environment, so
if the suite is launched with git's repo-pointer variables set -- most commonly
inside a ``git`` hook, which exports ``GIT_DIR``, or with a stray ``GIT_DIR`` /
``GIT_WORK_TREE`` in the environment -- those fixture ``git`` commands act on
whatever real repository those variables point at instead of their temp dir, and
clobber it.

Stripping those variables from ``os.environ`` here, at conftest import (before
any test is collected or run), makes every fixture ``git`` call -- in any test
module, via any helper -- operate on its intended temp repo no matter how or
where the suite is launched. This is the single guard that prevents the
worktree/closeout fixtures from ever committing into a real project repo.

A fallback commit identity is also provided so the committing fixtures never fail
with "Author identity unknown" when no git user is configured (CI runners, fresh
clones, automated evaluation runs).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Pin the checkout under test ahead of any editable-install ``.pth`` entry. Without this, invoking
# the canonical ``pytest mcp/tests`` command from a worktree can import the main checkout instead of
# the worktree candidate and report green against the wrong source tree.
MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

# git's repo-pointer / object-store environment. Any of these, if inherited,
# redirects a `git` subprocess away from its `cwd` and onto another repository.
_GIT_LOCATION_VARS = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_PREFIX",
)

for _var in _GIT_LOCATION_VARS:
    os.environ.pop(_var, None)

# Self-contained identity for the throwaway fixture commits; defers to a real
# identity if one is already exported.
os.environ.setdefault("GIT_AUTHOR_NAME", "Agents Remember Tests")
os.environ.setdefault("GIT_AUTHOR_EMAIL", "agents-remember-tests@example.invalid")
os.environ.setdefault("GIT_COMMITTER_NAME", "Agents Remember Tests")
os.environ.setdefault("GIT_COMMITTER_EMAIL", "agents-remember-tests@example.invalid")

# Reopen drill marker (L13): first cycle, then reopened and completed a second time under the same leaf id.
