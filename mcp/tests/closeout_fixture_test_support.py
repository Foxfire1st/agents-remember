"""Selected closeout-queue fixture shared by focused L1 boundary suites."""

from pathlib import Path

from test_closeout_queue import LEAF_A, MASTER_A, QueueFixture


def selected_fixture(root: Path, *, memory_mode: str) -> QueueFixture:
    fixture = QueueFixture(root, memory_mode=memory_mode)
    fixture.declare(MASTER_A)
    fixture.mutate("select", candidate=LEAF_A)
    return fixture
