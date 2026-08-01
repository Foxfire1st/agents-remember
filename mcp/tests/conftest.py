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

The one autouse fixture here restores the durable store's process-role declaration
around every test; see :func:`restore_declared_process_role` for why that is a
session-wide concern and not one file's business.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

# Pin the checkout under test ahead of any editable-install ``.pth`` entry. Without this, invoking
# the canonical ``pytest mcp/tests`` command from a worktree can import the main checkout instead of
# the worktree candidate and report green against the wrong source tree.
MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.controlplane import durable_store
from agents_remember.kernel.git_command import GIT_REPOSITORY_SELECTOR_ENV

# git's repo-pointer / object-store environment. Any of these, if inherited,
# redirects a `git` subprocess away from its `cwd` and onto another repository.
for _var in GIT_REPOSITORY_SELECTOR_ENV:
    os.environ.pop(_var, None)

# Self-contained identity for the throwaway fixture commits; defers to a real
# identity if one is already exported.
os.environ.setdefault("GIT_AUTHOR_NAME", "Agents Remember Tests")
os.environ.setdefault("GIT_AUTHOR_EMAIL", "agents-remember-tests@example.invalid")
os.environ.setdefault("GIT_COMMITTER_NAME", "Agents Remember Tests")
os.environ.setdefault("GIT_COMMITTER_EMAIL", "agents-remember-tests@example.invalid")


@pytest.fixture(autouse=True)
def restore_declared_process_role() -> Iterator[None]:
    """Undo any ``declare_process_role`` a test performs, for every test in this tree.

    ``durable_store._declared`` is process-global and has no reset -- it is written once at a
    real process's entry point and never again. The test suite is the one interpreter that hosts
    both roles, and it reaches those entry points directly: ``cli/dashboard.py::run`` declares
    ``"dashboard"`` as its first statement, and ``test_serving.py`` calls it seven times -- FOUR
    in ``CliRunTests`` and three in ``CliSimTests``. Both classes are named because the leak is
    not one class's to fix, which is half the argument for this being a fixture rather than a
    ``tearDown``. From any of those calls on, every later test in the same interpreter claims to
    be the dashboard. Measured on this tree before this fixture existed: ``declared_process_role()``
    is ``'dashboard'`` at the end of a run of ``CliRunTests`` alone.

    The value is not cosmetic. It is what ``StoreOwnership.is_compaction_owner`` and
    ``check_declared_writer`` answer from, so a leak silently flips whether a process is treated
    as a log's compaction owner and whether a write to a dashboard-only store is refused. The
    suite is green today only because of the order the files happen to run in -- a new test, a
    renamed file or a ``-k`` subset is enough to move it, and the failure would land on whichever
    test ran next rather than on the one that leaked.

    RESTORE, not clear. A test that legitimately declares a role must still observe its own
    declaration for the rest of its body, and an enclosing fixture that declared one must get it
    back; only the escape into the NEXT test is closed. Autouse fixtures apply to
    ``unittest.TestCase`` tests too, which is what the great majority of this suite is written as
    and why this cannot be a per-file helper.
    """
    previous = dict(durable_store._declared)
    yield
    durable_store._declared.clear()
    durable_store._declared.update(previous)
