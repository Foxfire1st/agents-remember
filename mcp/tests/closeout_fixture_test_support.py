"""Waiting-door fixture shared by focused lifecycle boundary suites."""

from pathlib import Path

from test_closeout_queue import MASTER_A, QueueFixture


def selected_fixture(root: Path, *, memory_mode: str) -> QueueFixture:
    fixture = QueueFixture(root, memory_mode=memory_mode)
    fixture.declare(MASTER_A)
    return fixture
