"""Dagger-only session setup that keeps the suite hermetic inside its clean graph.

Fixtures commit in throwaway repositories but inherit the process environment. Git
repo-pointer variables from a hook or shell can redirect those commands from their
temporary ``cwd`` into a real repository and clobber it.

Before any of that setup, collection refuses unless the Dagger quality graph minted
the process nonce and wrote the matching container-local attestation file. Stripping
Git variables from ``os.environ`` here, at conftest import (before
any test is collected or run), makes every fixture ``git`` call -- in any test
module, via any helper -- operate on its intended temp repo no matter how or
where the suite is launched. This is the single guard that prevents the
worktree/closeout fixtures from ever committing into a real project repo.

A fallback commit identity is also provided so the committing fixtures never fail
with "Author identity unknown" when no git user is configured (CI runners, fresh
clones, automated evaluation runs).

The autouse fixture rejects changes to the deliberately-enumerated module-level mutable state
owned by ``_global_state.py``. It restores before reporting the leak, so the offending test is
named and later tests are not poisoned.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest

# Pin the checkout under test ahead of any editable-install ``.pth`` entry before importing the
# production attestation validator. Otherwise collection can validate the main checkout and then
# execute a linked-worktree candidate.
MCP_SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(MCP_SRC))

from agents_remember.code_quality.dagger_environment import DaggerEnvironmentError
from agents_remember.code_quality.dagger_environment import (
    require_dagger_test_environment as _require_dagger_test_environment,
)


def require_dagger_test_environment() -> None:
    """Refuse collection outside the pinned Dagger quality graph."""
    try:
        _require_dagger_test_environment()
    except DaggerEnvironmentError as error:
        raise pytest.UsageError(str(error)) from error


require_dagger_test_environment()

from agents_remember.kernel.primitives.checkout_coordination import declare_test_process

declare_test_process()

from agents_remember.application.worktree_services import (
    bind_worktree_services,
    build_default_worktree_services,
)


@pytest.fixture(scope="session", autouse=True)
def _isolate_xdist_worker_cache(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Iterator[None]:
    """Keep process-global application caches private to each xdist worker."""
    del worker_id
    with mock.patch.dict(
        os.environ,
        {"XDG_CACHE_HOME": str(tmp_path_factory.getbasetemp() / "xdg-cache")},
    ):
        yield


@pytest.fixture(scope="session", autouse=True)
def _bind_worktree_services_for_session() -> Iterator[None]:
    """Bind the worktree services for class-level setup that runs outside test scope."""
    bind_worktree_services(build_default_worktree_services())
    yield


@pytest.fixture(autouse=True)
def _bind_worktree_services() -> Iterator[None]:
    """Bind the real worktree service bundle for every test.

    Worktree operations consume providers/memory_quality through the bound
    services port; tests that need a fake bind their own bundle.
    """
    bind_worktree_services(build_default_worktree_services())
    yield
    bind_worktree_services(build_default_worktree_services())


from _global_state import restore_owned_mutable_state, snapshot_owned_mutable_state
from _random_order import shuffle_items
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


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--random-order-seed",
        type=int,
        default=None,
        help="shuffle collected tests with this deterministic seed and report it in the header",
    )


def pytest_report_header(config: pytest.Config) -> str | None:
    seed = config.getoption("random_order_seed")
    return f"random-order seed: {seed}" if seed is not None else None


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    seed = config.getoption("random_order_seed")
    if seed is not None:
        shuffle_items(items, seed)


@pytest.fixture(autouse=True)
def reject_owned_global_state_leaks() -> Iterator[None]:
    """Fail the test that leaks an owned global, after restoring all owned state."""
    previous = snapshot_owned_mutable_state()
    yield
    changed = restore_owned_mutable_state(previous)
    if changed:
        pytest.fail(
            "test leaked owned module-level mutable state; restore it inside the test:\n"
            + "\n".join(changed),
            pytrace=False,
        )
