"""Reusable pytest bootstrap with no certifying or external-service capability."""

from __future__ import annotations

import os
from collections.abc import Iterator
from unittest import mock

import pytest

from agents_remember_test_support.testing.global_state import (
    begin_pytest_process,
    end_pytest_process,
    restore_owned_mutable_state,
    snapshot_owned_mutable_state,
)
from agents_remember_test_support.testing.random_order import shuffle_items

begin_pytest_process()


def pytest_unconfigure(config: pytest.Config) -> None:
    del config
    end_pytest_process()


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


@pytest.fixture(scope="session", autouse=True)
def _isolate_pytest_process_cache(
    tmp_path_factory: pytest.TempPathFactory,
    worker_id: str,
) -> Iterator[None]:
    del worker_id
    with mock.patch.dict(
        os.environ,
        {"XDG_CACHE_HOME": str(tmp_path_factory.getbasetemp() / "xdg-cache")},
    ):
        yield


@pytest.fixture(autouse=True)
def reject_owned_global_state_leaks() -> Iterator[None]:
    previous = snapshot_owned_mutable_state()
    yield
    changed = restore_owned_mutable_state(previous)
    if changed:
        pytest.fail(
            "test leaked owned module-level mutable state; restore it inside the test:\n"
            + "\n".join(changed),
            pytrace=False,
        )
