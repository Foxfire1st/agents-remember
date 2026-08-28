"""Certifying-only pytest services layered over the shared hermetic plugin."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

pytest_plugins = (
    "agents_remember_test_support.testing.pytest_bootstrap",
    "agents_remember_test_support.testing.evidence_lanes",
    "agents_remember_test_support.testing.causal_failures",
)


@pytest.fixture(scope="session", autouse=True)
def _bind_worktree_services_for_session() -> Iterator[None]:
    from agents_remember.application.worktree_services import (  # noqa: PLC0415
        bind_worktree_services,
        build_default_worktree_services,
    )
    from agents_remember.worktrees.services import reset_worktree_services  # noqa: PLC0415

    bind_worktree_services(build_default_worktree_services())
    try:
        yield
    finally:
        reset_worktree_services()


@pytest.fixture(autouse=True)
def _bind_worktree_services() -> Iterator[None]:
    from agents_remember.application.worktree_services import (  # noqa: PLC0415
        bind_worktree_services,
        build_default_worktree_services,
    )

    bind_worktree_services(build_default_worktree_services())
    try:
        yield
    finally:
        bind_worktree_services(build_default_worktree_services())
